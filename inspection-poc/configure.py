#!/usr/bin/env python3
"""Generate version-pinned, metadata-only Agentgateway inspection configuration."""

import argparse
import json
from pathlib import Path

VERSION = "1.7.0"
BUFFER_LIMIT = 2 * 1024 * 1024
INSPECT_HOSTS = (
    "api.enterprise.githubcopilot.com",
    "api.business.githubcopilot.com",
)
# Path suffix -> Agentgateway LLM route type. Only typed routes decode the request
# body; everything else is forwarded unchanged.
AI_ROUTES = {
    "/responses": "responses",
    "/chat/completions": "completions",
    "/v1/messages": "messages",
    "*": "passthrough",
}
COPILOT_FORMATS = [
    {"type": "responses", "path": "/responses"},
    {"type": "completions", "path": "/chat/completions"},
    {"type": "messages", "path": "/v1/messages"},
]
PASSTHROUGH_HOSTS = (
    "github.com",
    "*.github.com",
    "*.githubusercontent.com",
    "*.githubassets.com",
    "*.enterprise.githubcopilot.com",
    "*.business.githubcopilot.com",
    "default.exp-tas.com",
)


def configuration(ca_dir="/etc/inspection-poc", fixture=False):
    body = "coalesce(request.bodyPrefix, request.body, '')"
    parsed = f"coalesce(json({body}), null)"
    model = f"default(({parsed}).model, null)"
    content_type = "default(request.headers['content-type'], '')"
    encoding = "default(request.headers['content-encoding'], '')"
    response_code = "default(response.code, 0)"
    status = (
        f"size({body}) >= {BUFFER_LIMIT} ? 'capture_limit_unknown' : "
        f"size({body}) == 0 && {response_code} == 413 ? 'http_413_no_body_captured' : "
        f"size({body}) == 0 ? 'no_body' : "
        f"!({encoding} in ['', 'identity']) ? 'compressed_body' : "
        f"!{content_type}.startsWith('application/json') ? 'unsupported_content_type' : "
        f"{parsed} == null ? (coalesce(string({body}), '').trim() == 'null' ? 'invalid_json_shape' : 'parse_error') : "
        f"type({parsed}) != map ? 'invalid_json_shape' : "
        f"{model} == null ? 'not_present' : "
        f"type({model}) != string ? 'invalid_model_type' : "
        f"size({model}) == 0 ? 'not_present' : 'captured'"
    )
    llm_model = "default(llm.requestModel, '')"
    has_llm_model = f"(type({llm_model}) == string && size({llm_model}) > 0)"
    final_status = f"{has_llm_model} ? 'captured' : ({status})"
    fields = {
        "poc_schema": "'copilot-model-v1'",
        "gateway_version": f"'{VERSION}'",
        "exchange_id": "uuid()",
        "host": "request.host",
        "method": "request.method",
        "path": "request.path",
        "status": response_code,
        "content_type": content_type,
        "content_encoding": encoding,
        "body_bytes": f"size({body})",
        "model_capture_status": final_status,
        # llm.requestModel is read by the LLM pipeline after Content-Encoding is
        # decoded, so it covers zstd/gzip request bodies that CEL json() cannot parse.
        "requested_model_raw": (
            f"{has_llm_model} ? {llm_model} : (({status}) == 'captured' ? {model} : null)"
        ),
        "model_source": (
            f"{has_llm_model} ? 'llm.requestModel' : "
            f"(({status}) == 'captured' ? 'request.body.model' : null)"
        ),
        "response_model_raw": "default(llm.responseModel, null)",
    }
    listeners = []
    for index, host in enumerate(INSPECT_HOSTS):
        route = {
            "name": f"inspect-{index}",
            "hostnames": [host],
            "policies": {
                "authorization": {"rules": [{"allow": f"request.host == '{host}'"}]},
            },
            # Fix the upstream to the approved listener host and port. Never dial an
            # arbitrary authority supplied inside a permitted CONNECT tunnel. The custom
            # provider declares Copilot's native formats, so bodies are never translated and
            # no credentials are added or moved: the user's own GitHub token is forwarded.
            "backends": [{
                "ai": {
                    "name": f"github-copilot-{index}",
                    "provider": {"custom": {"formats": COPILOT_FORMATS}},
                    "hostOverride": f"{host}:443",
                    "policies": {"backendTLS": {}},
                },
            }],
        }
        route["policies"]["ai"] = {"routes": AI_ROUTES}
        if fixture:
            route["policies"]["directResponse"] = {"status": 200, "body": "FIXTURE_OK"}
            route["backends"] = []
        listeners.append(
            {
                "name": f"inspect-{index}",
                "hostname": host,
                "protocol": "HTTPS",
                "tls": {
                    "mode": "dynamicCa",
                    "cert": f"{ca_dir}/ca.crt",
                    "key": f"{ca_dir}/ca.key",
                },
                "routes": [route],
            }
        )
    listeners.append(
        {
            "name": "passthrough",
            "hostname": "*",
            "protocol": "TLS",
            "tcpRoutes": [
                {"hostnames": list(PASSTHROUGH_HOSTS), "backends": [{"dynamic": {}}]}
            ],
        }
    )
    return {
        "config": {
            "adminAddr": "off",
            "statsAddr": "127.0.0.1:15020",
            "readinessAddr": "127.0.0.1:15021",
            "logging": {"format": "json", "level": "warn,request=info"},
        },
        "frontendPolicies": {
            "http": {"maxBufferSize": BUFFER_LIMIT},
            "accessLog": {
                # Only inspected HTTP requests have request.host. Tunnel-only
                # traffic is not part of the inference-coverage denominator.
                "filter": "default(request.host, '') in " + json.dumps(list(INSPECT_HOSTS)),
                "remove": ["http.path", "error", "reason"],
                "add": fields,
            },
        },
        "binds": [
            {"port": 3128, "tunnelProtocol": "connect", "listeners": []},
            {"port": 443, "mode": "internal", "listeners": listeners},
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ca-dir", default="/etc/inspection-poc")
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(configuration(args.ca_dir, args.fixture), indent=2) + "\n")
