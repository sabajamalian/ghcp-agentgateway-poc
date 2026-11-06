#!/usr/bin/env bash
# Tag the pilot's run context as a desktop test, then launch the GitHub Copilot
# desktop app with process-scoped proxy and CA settings. Quit the app fully
# (Cmd+Q) before running this, and keep `bash inspection-poc/tunnel.sh` running
# in another Terminal window. No OS-wide trust or proxy settings are changed.
set -euo pipefail
cd "$(dirname "$0")/.."
STATE=.secrets/inspection-poc
PORT=23128

if ps -axo comm= | grep -x "/Applications/GitHub Copilot.app/Contents/MacOS/github" >/dev/null; then
  echo "GitHub Copilot is still running. Quit it fully (Cmd+Q), then rerun." >&2
  exit 1
fi
if ! nc -z 127.0.0.1 "$PORT" 2>/dev/null; then
  echo "No tunnel on 127.0.0.1:$PORT. Start: bash inspection-poc/tunnel.sh" >&2
  exit 1
fi

APP_VERSION="$(defaults read "/Applications/GitHub Copilot.app/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo unknown)"
RUN_ID="$(uuidgen | tr '[:upper:]' '[:lower:]')"
CONTEXT="{\"run_id\":\"$RUN_ID\",\"client\":\"desktop\",\"client_version\":\"GitHub Copilot app $APP_VERSION\"}"
SSH=(ssh -p "$(cat "$STATE/ssh-port")" -i "$STATE/id_ed25519" -o BatchMode=yes
     -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$STATE/known_hosts"
     -o ConnectTimeout=10 "pilot@$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["sshIp"]["value"])' "$STATE/outputs.json")")

# Global Secure Access resets some SSH connections; retry.
PUSHED=0
for _ in 1 2 3 4 5 6 7 8; do
  if printf '%s' "$CONTEXT" | "${SSH[@]}" 'sudo tee /etc/inspection-poc/run.json >/dev/null' 2>/dev/null; then
    PUSHED=1
    break
  fi
  sleep 2
done
if [ "$PUSHED" != 1 ]; then
  echo "Could not set run context on the pilot VM." >&2
  exit 1
fi

/usr/bin/open -a "GitHub Copilot" \
  --env "HTTPS_PROXY=http://127.0.0.1:$PORT" \
  --env "HTTP_PROXY=http://127.0.0.1:$PORT" \
  --env "NO_PROXY=localhost,127.0.0.1,::1" \
  --env "NODE_EXTRA_CA_CERTS=$PWD/$STATE/pilot-ca.crt"

echo "Desktop run_id: $RUN_ID"
echo "In a local session, send a few reply-only prompts with different models, then query"
echo "CopilotModel_CL for this run_id (see inspection-poc/queries.kql)."
