#!/bin/sh
set -eu

if [ ! -f "$D3_CONFIG" ]; then
    cp /app/site/config.json "$D3_CONFIG"
fi

exec "$@"
