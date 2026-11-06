#!/usr/bin/env python3
"""Generate an authenticated Foundry model endpoint without embedding API keys."""

import argparse
import hashlib
import json
from pathlib import Path
import re


def configuration(resource, model, gateway_key, *, alias="foundry-team-model",
                  resource_type="openAI", project=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", resource):
        raise ValueError("Azure resource must be a resource name, not a URL.")
    for name, value in (("model", model), ("alias", alias)):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,159}", value):
            raise ValueError(f"Invalid {name} identifier.")
    if resource_type not in {"openAI", "foundry"}:
        raise ValueError("resource_type must be openAI or foundry.")
    if resource_type == "foundry" and not project:
        raise ValueError("A Foundry project name is required.")
    if project and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", project):
        raise ValueError("Invalid Foundry project name.")
    if len(gateway_key) < 32:
        raise ValueError("Use a random gateway key of at least 32 characters.")
    provider = {
        "resourceName": resource, "resourceType": resource_type,
        "model": model, "apiVersion": "v1",
    }
    if project:
        provider["projectName"] = project
    catalog = json.dumps({
        "object": "list",
        "data": [{"id": alias, "object": "model", "owned_by": "enterprise-gateway"}],
    })
    return {
        "config": {
            "adminAddr": "off", "statsAddr": "off", "readinessAddr": "off",
            "logging": {"format": "json", "level": "off,request=info"},
        },
        "frontendPolicies": {
            "http": {"maxBufferSize": 2 * 1024 * 1024},
            "accessLog": {
                "remove": ["http.path", "error", "reason"],
                "add": {
                    "upstream_requested_model": "default(llm.requestModel, null)",
                    "response_model_raw": "default(llm.responseModel, null)",
                    "method": "request.method",
                    "status": "default(response.code, 0)",
                    "upstream_deployment": json.dumps(model),
                    "advertised_model_alias": json.dumps(alias),
                },
            },
        },
        "gateways": {
            "byok-api": {
                "port": 8080,
                "apiKey": {
                    "mode": "strict",
                    "keys": [{
                        "keyHash": "sha256:" + hashlib.sha256(gateway_key.encode()).hexdigest(),
                    }],
                },
            },
        },
        "routes": [
            {
                "name": "model-catalog", "gateways": ["byok-api"],
                "matches": [
                    {"path": {"exact": "/v1/models"}},
                    {"path": {"exact": "/models"}},
                ],
                "policies": {
                    "directResponse": {"status": 200, "body": catalog},
                    "responseHeaderModifier": {"set": {"content-type": "application/json"}},
                },
            },
            {
                "name": "foundry-inference", "gateways": ["byok-api"],
                "policies": {
                    "ai": {"routes": {
                        "/v1/chat/completions": "completions",
                        "/chat/completions": "completions",
                        "/v1/responses": "responses",
                        "/responses": "responses",
                    }},
                    "requestHeaderModifier": {"remove": ["authorization", "api-key"]},
                },
                "backends": [{
                    "ai": {
                        "name": "foundry",
                        "provider": {"azure": provider},
                        "policies": {
                            "backendTLS": {},
                            "backendAuth": {"key": {
                                "value": {"file": "/secrets/foundry-api-key"},
                                "location": {"header": {"name": "api-key"}},
                            }},
                        },
                    },
                }],
            },
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource", required=True)
    parser.add_argument("--model", required=True, help="Azure deployment/model identifier")
    parser.add_argument("--alias", default="foundry-team-model")
    parser.add_argument("--resource-type", choices=("openAI", "foundry"), default="openAI")
    parser.add_argument("--project")
    parser.add_argument("--gateway-key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = configuration(
            args.resource, args.model, args.gateway_key_file.read_text().strip(),
            alias=args.alias, resource_type=args.resource_type, project=args.project,
        )
    except ValueError as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(config, indent=2) + "\n")
