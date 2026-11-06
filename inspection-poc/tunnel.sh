#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
STATE="$ROOT/../.secrets/inspection-poc"
IP="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sshIp"]["value"])' "$STATE/outputs.json")"
exec ssh -i "$STATE/id_ed25519" -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -p "$(cat "$STATE/ssh-port")" \
  -o UserKnownHostsFile="$STATE/known_hosts" -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -N -L 127.0.0.1:23128:127.0.0.1:3128 "pilot@$IP"
