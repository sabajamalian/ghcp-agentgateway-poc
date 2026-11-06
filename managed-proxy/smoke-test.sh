#!/usr/bin/env bash
# Check proxy rules and the authenticated HTTPS UI without changing configuration.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
source "$ROOT/.secrets/proxy-credentials.env"
FQDN="$(python3 -c "import json;print(json.load(open('$ROOT/.secrets/deployment-outputs.json'))['proxyFqdn']['value'])")"
PROXY="http://$FQDN:3128"

connect_status() { # prints the proxy's CONNECT response code
  curl -sv --max-time 15 --noproxy '' --proxy "$PROXY" "${@:2}" -o /dev/null "$1" 2>&1 \
    | grep -m1 -oE '< HTTP/1.1 [0-9]+' | awk '{print $3}'
}
check() { # url expected [curl args]
  got="$(connect_status "$1" "${@:3}")"
  [[ "$got" == "$2" ]] && r=PASS || { r=FAIL; FAILED=1; }
  printf '%-4s %-48s expected %s got %s\n' "$r" "$1" "$2" "${got:-none}"
}
AUTH=(--proxy-user "$PROXY_USER:$PROXY_PASS")
FAILED=0
check https://api.github.com/              407
check https://api.github.com/              200 "${AUTH[@]}"
check https://github.com/login            200 "${AUTH[@]}"
check https://api.enterprise.githubcopilot.com/ 200 "${AUTH[@]}"
check https://api.business.githubcopilot.com/   200 "${AUTH[@]}"
check https://copilot-proxy.githubusercontent.com/ 200 "${AUTH[@]}"
check https://api.individual.githubcopilot.com/ 404 "${AUTH[@]}"
check https://example.com/                 404 "${AUTH[@]}"

source "$ROOT/.secrets/ui-credentials.env"
UI="$(python3 - "$ROOT/.secrets/deployment-outputs.json" <<'PY'
import json
import sys
from urllib.parse import urlsplit

url = urlsplit(json.load(open(sys.argv[1]))["uiUrl"]["value"])
print(f"{url.scheme}://{url.netloc}")
PY
)"
UI_AUTH=(--user "$UI_USER:$UI_PASS")
check_http() {
  got="$(curl --silent --show-error --noproxy '*' --connect-timeout 10 --max-time 20 \
    --output /dev/null --write-out '%{http_code}' "${@:3}" "$1")"
  [[ "$got" == "$2" ]] && r=PASS || { r=FAIL; FAILED=1; }
  printf '%-4s %-48s expected %s got %s\n' "$r" "$1" "$2" "${got:-none}"
}
REDIRECT="$(curl --silent --show-error --noproxy '*' --connect-timeout 10 --max-time 60 \
  --output /dev/null --write-out '%{http_code} %{redirect_url}' "${UI/https:/http:}/ui/")"
case "$REDIRECT" in
  "301 $UI/ui/"|"302 $UI/ui/"|"307 $UI/ui/"|"308 $UI/ui/") r=PASS ;;
  *) r=FAIL; FAILED=1 ;;
esac
printf '%-4s HTTP redirects to the managed HTTPS UI: %s\n' "$r" "$REDIRECT"
check_http "$UI/ui/"                    401
check_http "$UI/ui/"                    401 --user "$UI_USER:wrong-password"
check_http "$UI/ui/"                    401 --user "$PROXY_USER:$PROXY_PASS"
check_http "$UI/api/config"             401
check_http "$UI/ui/"                    200 "${UI_AUTH[@]}"
check_http "$UI/api/runtime"            200 "${UI_AUTH[@]}"
check_http "$UI/api/config/resources/traffic.route" 200 "${UI_AUTH[@]}"
exit $FAILED
