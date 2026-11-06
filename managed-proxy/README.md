# GitHub-managed models through CONNECT

This runbook deploys an authenticated HTTP CONNECT proxy on Azure Container Instances, plus a separate HTTPS admin UI. GitHub retains the user's Copilot authentication, seat, model selection, and serving path.

CONNECT access logs do not contain the decrypted model or prompt. Use the [inspection runbook](../inspection-poc/README.md) when you need model metadata.

## Prerequisites

- Azure CLI, Python 3, OpenSSL, `curl`, and `htpasswd`.
- The subscription/region variables from the root [.env.example](../.env.example).
- Azure permission to create the resources and register providers.

On macOS, `htpasswd` is usually available with the Apache tools. Check `command -v htpasswd` before deployment. On Ubuntu, it is supplied by `apache2-utils`.

## Deploy

From the repository root:

```sh
bash managed-proxy/deploy.sh
```

The defaults are a separate `rg-agentgateway-copilot` group and `agw-copilot` resource prefix. Override them without changing source:

```sh
RG=rg-my-copilot-proxy NAME=my-copilot bash managed-proxy/deploy.sh
```

Local credentials, certificates, deployment parameters, and backups go under `managed-proxy/.secrets/`. They are ignored by Git.

Read `proxyFqdn` and `uiUrl` from `managed-proxy/.secrets/deployment-outputs.json`. No live endpoint from the original PoC is included here.

## Client setup

Scope environment settings to the Copilot process rather than the whole terminal:

```sh
source managed-proxy/.secrets/proxy-credentials.env
FQDN="$(python3 -c 'import json; print(json.load(open("managed-proxy/.secrets/deployment-outputs.json"))["proxyFqdn"]["value"])')"
PROXY_URL="http://$PROXY_USER:$PROXY_PASS@$FQDN:3128"

HTTPS_PROXY="$PROXY_URL" HTTP_PROXY="$PROXY_URL" \
  copilot -p 'Reply with exactly: PROXY_OK'
```

Use a model permitted by your enterprise. A model-selection notice or CONNECT `200` alone is not a completed inference response.

For macOS desktop local sessions, fully quit the app first, then launch it from a terminal outside the app:

```sh
open -a "GitHub Copilot" \
  --env "HTTPS_PROXY=$PROXY_URL" \
  --env "HTTP_PROXY=$PROXY_URL"
```

Reopen normally to remove these process settings. Remote/cloud sessions use their execution host's network settings.

GitHub's [network documentation](https://docs.github.com/en/copilot/concepts/security-governance-and-network-settings/network-settings) explains IDE settings and proxy support. Keep certificate verification enabled.

> [!WARNING]
> The lab's HTTP proxy credential is not encrypted on its client-to-proxy hop. Use private connectivity or another approved encrypted transport for enterprise use. The inspection runbook uses a key-authenticated SSH tunnel instead.

## UI and rules

Open your deployment's `uiUrl`. Use the separate account in `managed-proxy/.secrets/ui-credentials.env`, not the proxy credential.

**Traffic → Routes** controls hostname routes; **Traffic → Gateways** controls the listener and authentication. Save a rule, allow propagation, then make a new request. Existing tunnels can outlive a rule change.

The UI uses Container Apps' managed public HTTPS certificate. Its origin hop to ACI is HTTPS with a private CA, and Caddy authenticates UI/API requests. The gateway UI listener is not publicly exposed.

The LLM analytics pages are not a decoder for CONNECT tunnels. This deployment has no LLM log database.

## Access logs

Open the deployment's Log Analytics workspace:

```kusto
ContainerInstanceLog_CL
| where ContainerName_s == "agentgateway" and Message has "http.method=CONNECT"
| extend Host = extract(@"http.host=(\S+)", 1, Message),
         Status = toint(extract(@"http.status=(\d+)", 1, Message)),
         Duration = extract(@"duration=(\S+)", 1, Message)
| project TimeGenerated, Host, Status, Duration
| order by TimeGenerated desc
```

The shared proxy account and ACI source address do not identify an individual developer.

## Update and recover

```sh
bash managed-proxy/export-config.sh
bash managed-proxy/deploy.sh
# Intentionally overwrite runtime rules with the local configuration:
bash managed-proxy/deploy.sh --apply-config
bash managed-proxy/smoke-test.sh
```

The deployment exports live UI configuration before a normal update. Manual stop/restart/replacement requires your own export first.

ACI's writable `emptyDir` configuration is not durable cloud storage. Keep exported backups private. See [operations](../docs/operations.md).

## Common failure

Exporting this proxy for an entire terminal can send Azure CLI management requests through a destination allowlist intended for Copilot. Those calls can fail with `Tunnel connection failed: 404 Not Found`.

The deployment script removes the inherited proxy variables from its process. For other Azure commands, use a separate clean terminal or:

```sh
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
  az resource list --subscription "$SUBSCRIPTION" --resource-group rg-agentgateway-copilot -o table
```
