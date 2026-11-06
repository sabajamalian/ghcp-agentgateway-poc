#!/usr/bin/env bash
# Save the live configuration locally before stopping or replacing the ACI group.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")" && pwd)"
SECRETS="$ROOT/.secrets"
source "$SECRETS/ui-credentials.env"
if [[ -z "${UI_URL:-}" ]]; then
  UI_URL="$(python3 -c 'import json,sys; from urllib.parse import urlsplit; u=urlsplit(json.load(open(sys.argv[1]))["uiUrl"]["value"]); print(f"{u.scheme}://{u.netloc}")' "$SECRETS/deployment-outputs.json")"
fi
BACKUP="$(mktemp "$SECRETS/runtime-config-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
trap 'rm -f "$BACKUP"' EXIT
curl --fail --silent --show-error --noproxy '*' --connect-timeout 10 --max-time 60 \
  --user "$UI_USER:$UI_PASS" "$UI_URL/api/config" > "$BACKUP"
python3 - "$BACKUP" <<'PY'
import json
import sys

with open(sys.argv[1]) as stream:
    config = json.load(stream)
if not isinstance(config, dict) or not isinstance(config.get("gateways"), dict):
    raise SystemExit("The UI API did not return a valid gateway configuration.")
PY
mv "$BACKUP" "$BACKUP.json"
trap - EXIT
printf '%s.json\n' "$BACKUP"
