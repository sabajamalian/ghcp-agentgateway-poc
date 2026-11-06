#!/bin/sh
set -eu
TOOLS="${CADDY_TOOLS_DIR:-/config/bin}"
if [ ! -x "$TOOLS/caddy" ]; then
    mkdir -p "$TOOLS"
    curl --fail --silent --show-error --location --retry 3 --max-time 180 \
        https://github.com/caddyserver/caddy/releases/download/v2.11.7/caddy_2.11.7_linux_amd64.tar.gz \
        --output "$TOOLS/caddy.tar.gz"
    printf '%s  %s\n' \
        a7a433a1b133efc3c8d10eb0b99d52a24b5ef5c322dc77f5282182b1c0402139ab83f3a99f0c52409df77d20123fb0b523edad8a66d8f5e49136197bf61ef0e7 \
        "$TOOLS/caddy.tar.gz" | sha512sum --check
    python3 - "$TOOLS" <<'PY'
import pathlib
import sys
import tarfile

tools = pathlib.Path(sys.argv[1])
with tarfile.open(tools / "caddy.tar.gz") as archive:
    archive.extract("caddy", path=tools, filter="data")
PY
    chmod 755 "$TOOLS/caddy"
    rm "$TOOLS/caddy.tar.gz"
fi
