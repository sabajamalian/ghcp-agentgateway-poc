# Request paths and trust boundaries

## GitHub-managed models through CONNECT

The client authenticates to the proxy, then establishes TLS with GitHub inside the tunnel. GitHub continues to authenticate the developer, enforce the Copilot seat and enterprise policy, and supply the model.

The [initial configuration](../managed-proxy/config.yaml) allows GitHub support hosts and the business/enterprise Copilot hostnames. Other destinations have no route. A CONNECT `200` means a tunnel was established; it does not prove a model answered a prompt.

The proxy's basic-auth account is separate from the user's GitHub account. The lab uses a shared proxy account. Its logs cannot attribute requests to individual developers.

The admin UI has another, separate credential. Container Apps supplies the public HTTPS certificate; Caddy on ACI verifies and authenticates the origin hop. The UI listener is loopback-only.

## Selective inspection of managed-model traffic

The inspection pilot creates a separate VM. A key-authenticated SSH tunnel exposes its loopback proxy to the local client:

```text
Copilot client → localhost proxy → SSH tunnel → VM loopback proxy
              → Agentgateway selective TLS inspection → verified GitHub HTTPS
```

Only the configured Copilot inference hosts are decrypted. GitHub authentication and support hosts use passthrough routes. The client trusts a dedicated pilot CA through `NODE_EXTRA_CA_CERTS`; there is no OS-wide certificate installation.

The gateway presents an inspection certificate to the client and independently validates GitHub's upstream certificate. It holds the signing key, so this is a materially different trust boundary from CONNECT.

### Preserve Copilot's native protocols

Copilot can send Responses, Chat Completions, and Anthropic Messages requests. Its CLI can zstd-compress inference bodies.

[configure.py](../inspection-poc/configure.py) declares a custom AI provider with native format mappings:

| Path | Gateway format |
| --- | --- |
| `/responses` | `responses` |
| `/chat/completions` | `completions` |
| `/v1/messages` | `messages` |

The typed AI pipeline decompresses the body before reading `llm.requestModel`. Generic `json(request.body).model` cannot read zstd bytes.

Using a generic OpenAI provider for the managed-model tunnel can translate an Anthropic request into another protocol. Using an Anthropic provider can relocate bearer credentials. This PoC preserves the native format and the client's `Authorization` header. It does not configure the gateway's own Copilot credentials or share a Copilot seat.

The inspected upstream is pinned to the approved hostname and port. A different inner Host header must not select arbitrary upstream egress.

## BYOK changes the provider leg

**User-configured BYOK:** the client sends model requests directly to the provider endpoint configured in that client. For this PoC, that endpoint is Agentgateway, which forwards to Foundry.

**Enterprise-managed BYOK:** users continue to call GitHub's Copilot API. GitHub holds the configured gateway key and calls the model endpoint. Agentgateway then authenticates to Foundry using a different upstream credential.

```text
Client → GitHub Copilot → public HTTPS Agentgateway API → Foundry
         user/seat auth   gateway bearer-key auth          provider auth
```

The public BYOK endpoint must be reachable by GitHub's service, not just by the developer's workstation. A workstation-only SSH tunnel or private listener is insufficient for this enterprise-provider path.

The [BYOK implementation](../byok/configure.py) validates an inbound gateway key, pins its upstream model deployment, removes inbound provider/auth headers, and supplies the upstream Foundry credential. The alias exposed to Copilot can differ from the Azure deployment name.

This is intentional model-provider adaptation. It differs from the inspection path, which preserves GitHub's requested model and user credential.

## Hybrid means selectable paths

A user can keep GitHub-managed models and add custom models alongside them. The selected model determines which provider path is used.

Hybrid here does not mean automatic failover from GitHub to Foundry, a shared inference pool, or that Foundry models become eligible for Fusion. It means both choices remain available behind gateway controls.

These deployments have separate configuration, authentication, and log collection. A single Agentgateway process can expose multiple listeners, but a production design must still preserve those security boundaries.

## Workstation routing has a limit

Desktop **local** sessions use the workstation's environment. Remote sessions use the remote execution host's environment. GitHub-hosted agent and review work does not traverse a laptop proxy simply because that proxy is configured locally.

An enterprise that needs mandatory routing must combine endpoint policy with network enforcement. See [operations](operations.md).
