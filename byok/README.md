# Foundry models behind Agentgateway

This runbook creates an authenticated OpenAI-compatible model API with Azure-managed HTTPS ingress. It routes a client-facing model alias to an Azure OpenAI deployment or a Foundry project endpoint.

> [!IMPORTANT]
> Enterprise- and organization-managed GitHub Copilot BYOK is **public preview and subject to change**. See [GitHub's custom-model setup](https://docs.github.com/en/copilot/how-tos/administer-copilot/manage-for-enterprise/enable-custom-models).

## Create the provider deployment

In the Foundry portal, create/select the resource and deploy a model that supports your intended client APIs, tool calling, and streaming. Use [Microsoft's deployment guidance](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/deploy-foundry-models) and [endpoint reference](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/endpoints).

Get the deployed model identifier and endpoint. This gateway sample supports:

| Endpoint family | `RESOURCE_TYPE` | Required inputs |
| --- | --- | --- |
| `{resource}.openai.azure.com` | `openAI` | Resource name and deployment/model ID |
| `{resource}.services.ai.azure.com/api/projects/{project}/openai/v1` | `foundry` | Resource name, project name, deployment/model ID |

For project endpoints, use the hostname's exact resource component. Do not add or remove a `-resource` suffix by assumption.

This implementation uses the Azure v1 API. A deployment exposed through another endpoint/protocol needs an appropriate provider configuration; do not point it at this sample without adapting that configuration.

## Deploy the gateway API

Load your subscription and region settings, then set your non-secret inputs:

```sh
export AZURE_RESOURCE=YOUR_RESOURCE_NAME
export AZURE_MODEL=YOUR_DEPLOYMENT_NAME
export MODEL_ALIAS=foundry-team-model
export RESOURCE_TYPE=openAI
# For a project endpoint instead:
# export RESOURCE_TYPE=foundry
# export AZURE_PROJECT=YOUR_PROJECT_NAME
```

Supply the provider API key from an approved secret source. For an interactive Bash terminal, read it without echoing or writing it into command history:

```sh
read -r -s -p "Upstream provider API key: " FOUNDRY_API_KEY
printf '\n'
export FOUNDRY_API_KEY
bash byok/deploy.sh
unset FOUNDRY_API_KEY
```

The default group is `rg-agentgateway-byok`. Override `RG`, `NAME`, and `LOCATION` if needed. The script does not create or modify GitHub enterprise policy.

Outputs and the inbound gateway key are under `.secrets/byok/`. The generated config stores only a hash of that gateway key. The upstream provider key is passed through protected deployment parameters into a Container Apps secret volume. Neither key is published in this repository.

For production, replace this lab key lifecycle with approved secret storage/rotation and least-privilege Azure authentication.

## Add the endpoint to GitHub enterprise settings

1. Open the enterprise's **AI controls → Copilot → Configure custom models**.
2. Add a provider entry for an **OpenAI-compatible provider**.
3. Use the gateway `baseUrl` from `.secrets/byok/outputs.json`.
4. Supply the **gateway** key from `.secrets/byok/gateway-api-key`, not the Foundry provider key.
5. Add the model ID exposed by `MODEL_ALIAS` and enable its availability for the intended organizations.

The provider-specific form can change during preview. Use the current [official instructions](https://docs.github.com/en/copilot/how-tos/administer-copilot/manage-for-enterprise/enable-custom-models) for the fields shown in your enterprise.

Selecting GitHub's Microsoft Foundry provider with a direct Foundry URL bypasses this gateway. Use the gateway's compatible endpoint when the goal is gateway mediation.

Users keep their GitHub sign-in and Copilot license. GitHub calls the gateway over HTTPS, and the gateway uses its own separate upstream provider credential.

The enterprise-provider endpoint must be reachable by GitHub's service. A workstation-only tunnel or private endpoint is not enough for this path.

## Alternative: user-configured BYOK

For the desktop app, add an OpenAI-compatible provider under **Settings → Model providers** with the gateway URL and gateway key. See [desktop provider setup](https://docs.github.com/en/copilot/how-tos/github-copilot-app/add-llm-models).

For CLI provider configuration, use [the CLI model-provider guide](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-llm-models). Configure the gateway base URL, model alias, and key using that client's current supported format.

User-configured BYOK sends directly from the client to the gateway. Enterprise-managed BYOK sends through GitHub. The two are different identity and credential-distribution designs.

If the client also has a CONNECT proxy configured, its request to the BYOK hostname may be refused by that proxy's Copilot allowlist. Add the approved gateway hostname as a passthrough route, or use a deliberate `NO_PROXY` exception for this gateway API. The latter bypasses the CONNECT listener, but still sends inference to the authenticated model gateway.

## Hybrid

Keep GitHub-managed models enabled and add the custom provider alongside them. The model picker selection determines the path:

```text
GitHub-managed selection → configured Copilot proxy → GitHub-managed model
Enterprise BYOK selection → GitHub → gateway model API → Foundry
User BYOK selection       → gateway model API → Foundry
```

The managed and BYOK listeners have separate credentials and responsibilities. These scripts deploy them separately; they do not establish a shared control plane or automatic provider failover.

Foundry models do not automatically enter Fusion/Auto's pool or replace models used by hosted Copilot features. Keep the managed path available for workflows that depend on it.

## API behavior and logs

[configure.py](configure.py) exposes the model catalog, validates an inbound key, and routes Chat Completions/Responses to Azure. It removes inbound auth headers and injects the separate provider key in `api-key`.

The native Azure provider pins every inference on this route to `AZURE_MODEL`, regardless of the incoming model string. The catalog advertises `MODEL_ALIAS`; this sample does not reject other aliases, but they cannot select another upstream deployment.

Access logs retain `upstream_requested_model` after provider adaptation, the reported response model, configured upstream deployment, advertised alias, method, and status. The advertised alias is a configuration value, not evidence of the incoming model string. Ordinary diagnostic logging is disabled in this sample to avoid persisting upstream error content; use HTTP status and readiness monitoring for failures.

Query the deployment's workspace:

```kusto
ContainerAppConsoleLogs_CL
| extend Entry = parse_json(Log_s)
| where isnotempty(Entry.method)
| project TimeGenerated,
          UpstreamRequestedModel = tostring(Entry.upstream_requested_model),
          ResponseModel = tostring(Entry.response_model_raw),
          UpstreamDeployment = tostring(Entry.upstream_deployment),
          Status = toint(Entry.status)
| order by TimeGenerated desc
```

This is not the inspection pilot's `CopilotModel_CL` pipeline. In enterprise BYOK, the inbound gateway key identifies the provider entry, not necessarily the human user.

## Source checks and operating limits

```sh
python3 -m unittest discover -s byok -p 'test_*.py'
bash -n byok/deploy.sh
```

The real-binary fixture suite can be run with the pinned gateway downloaded in the [inspection runbook](../inspection-poc/README.md):

```sh
python3 byok/test_gateway.py --gateway .secrets/fixture/agentgateway-darwin-arm64
```

The deployment has one replica and no automatic model fallback. Review [operations](../docs/operations.md) for costs, secrets, availability, and cleanup.
