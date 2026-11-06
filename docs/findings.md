# Findings and tradeoffs

## What the PoC makes observable

| Capability | Practical result |
| --- | --- |
| Copilot CLI and desktop local-session routing | The client can reach GitHub-managed model APIs through an Azure gateway |
| Model extraction | Selective inspection exposes the model field actually sent in the inference request |
| Requested versus response model | Both are retained; identifier spelling can differ |
| Multiple native APIs | Responses, Chat Completions, and Anthropic Messages have explicit mappings |
| Compressed requests | The AI pipeline decodes bodies before extracting model metadata |
| Developer GitHub credentials | Preserved on the managed-model inspection path |
| Administrative rules | CONNECT routes can allow or refuse destinations; the authenticated UI manages runtime configuration |
| Metadata monitoring | A projection writes selected fields to Azure Log Analytics |
| Custom models | A separately authenticated API routes a model alias to a Foundry deployment |
| Hybrid selection | GitHub-managed and custom models can remain available together |

The [desktop screenshot](images/copilot-model-logs.png) illustrates the model metadata. It does not establish authenticated developer identity or a universal capture rate.

## Compare the approaches

| Consideration | GitHub-managed CONNECT | GitHub-managed inspection | Foundry through BYOK | Hybrid |
| --- | --- | --- | --- | --- |
| Who supplies inference | GitHub | GitHub | Your configured provider | Depends on selected model |
| Model visibility at gateway | Encrypted | Requested and reported model | Forwarded deployment and reported model | Separate records by path |
| Client trust change | No inspection CA | Dedicated inspection CA | Normal HTTPS trust for model API | CA only for inspected path |
| GitHub user/seat auth | Retained | Retained | Retained for enterprise BYOK; different rules for user BYOK | Depends on configuration |
| Credentials to manage | Proxy/admin accounts | SSH and inspection CA | Gateway key plus upstream provider credential | All selected boundaries |
| Provider capacity | GitHub-managed | GitHub-managed | Your quotas, deployment capacity, and provider rates | Split responsibility |
| Operating burden | Proxy availability and config | Trust lifecycle plus protocol/logging compatibility | Model API availability, keys, versions, quota | Both paths |
| Hosted GitHub work | Not controlled by laptop proxy | Not controlled by laptop proxy | Surface-specific model support | Keep managed path for hosted features |

### Reasons to keep GitHub-managed models

Developers retain the GitHub model catalog and the GitHub-managed serving path. A transparent proxy does not itself replace the model or alter Copilot feature selection.

CONNECT has a smaller trust change and does not decrypt conversation content. When exact model visibility is required, selective inspection can retain the same model-serving arrangement, with added security and protocol responsibilities.

### Reasons to add Foundry

The enterprise can select its own deployments, align provider billing with its Azure arrangements, and apply policies to a decoded model API.

Enterprise-managed BYOK centralizes keys and model availability. User-configured BYOK can send directly to the gateway and supports a different credential-distribution model. [GitHub documents both configuration methods](https://docs.github.com/en/copilot/concepts/models/bring-your-own-key).

Provider terms, regional deployment choices, and network controls require their own review. Routing to Foundry does not remove GitHub from enterprise BYOK or automatically establish end-to-end data residency.

### Reasons to use a hybrid

Keep GitHub-managed models for the workflows that use them and offer Foundry for selected models or teams. This avoids making every workflow depend on one custom provider's capability.

The cost is additional configuration and operating boundaries. A unified reporting view needs field normalization across the two log pipelines. There is no shared per-user identity in the default implementation.

## Copilot feature coverage

Custom models need to support the tools, streaming, and APIs required by the chosen client. A model name in a picker is not a guarantee of equivalent agent behavior.

Custom agents, MCP, and local agent workflows are distinct from the model provider. Do not describe all agentic capabilities as lost under BYOK. Their behavior still depends on the client, model, tools, and policies.

Fusion is a runtime model router and orchestrator. Adding a Foundry model to Copilot's custom-model list does not automatically enroll it in Fusion or Auto. Check the current [Fusion](https://docs.github.com/en/copilot/concepts/models/copilot-fusion), [Auto](https://docs.github.com/en/copilot/concepts/models/auto-model-selection), and [policy-surface](https://docs.github.com/en/copilot/reference/supported-surfaces-for-policies) documentation.

Do not infer availability on a hosted surface from a local-session screenshot. Copilot cloud-agent model choices and code-review behavior have their own documented support boundaries.

## Model names and identity are different problems

The gateway can read a requested model field and record the response's reported model. These are API identifiers, not independent attestation of a provider's internal model deployment.

The inspection harness supplies `client`, `client_version`, and `run_id`. Those labels are useful for correlating a controlled experiment; they are not authenticated identity.

On the enterprise BYOK provider leg, the gateway authenticates GitHub's configured key. Do not assume GitHub forwards the human user's identity in a documented, trustworthy header.

Per-user gateway keys can support attribution on a direct user-BYOK path if their distribution, identity mapping, and rotation are controlled. Source IP alone is insufficient.

## The limits that matter

- A local proxy setting is bypassable without endpoint and network enforcement.
- Full prompts pass through memory on the inspected path even though they are not persisted.
- One prompt can cause multiple requests, model calls, or retries.
- New client API paths can require updated format mappings.
- The inspector's decoded-body limit comes from [BUFFER_LIMIT](../inspection-poc/configure.py); oversized requests can be rejected.
- The single-instance deployments are lab infrastructure, with no availability or performance claim.
- Enterprise-managed BYOK is public preview and can change independently of this repository.
