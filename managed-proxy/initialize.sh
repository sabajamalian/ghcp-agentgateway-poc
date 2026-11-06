#!/bin/sh
set -eu

if [ "$REPLACE_CONFIG" = true ] || [ ! -e /config/config.yaml ]; then
    cp /bootstrap/config.yaml /config/config.yaml
    echo "Initialized writable configuration"
else
    echo "Preserving existing UI configuration"
fi

# agentgateway v1.7.0 runs as the non-root user 65532.
chown 65532:65532 /config /config/config.yaml
chmod 700 /config
chmod 600 /config/config.yaml

/bin/sh /bootstrap/install-caddy.sh
