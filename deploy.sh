#!/bin/sh
# Proxmox host: copies this checkout into the CT from config.env and runs scripts/install-app.sh there.
set -e
DIR=$(dirname "$(readlink -f "$0")")
CT=$(sed -n 's/^CT_ID=\([0-9]*\).*/\1/p' "$DIR/config.env" 2>/dev/null)
[ -n "$CT" ] || { echo "config.env with CT_ID missing (cp config.example.env config.env)" >&2; exit 1; }
python3 -m py_compile "$DIR"/app/*/*.py "$DIR"/app/*/*/*.py
pct exec "$CT" -- sh -c 'rm -rf /opt/isis/src && mkdir -p /opt/isis/src'
tar -C "$DIR" --exclude=__pycache__ --exclude=.git -cf - app systemd scripts requirements.txt | pct exec "$CT" -- tar -C /opt/isis/src -xf -
pct exec "$CT" -- sh /opt/isis/src/scripts/install-app.sh
