#!/bin/sh
# `isis <command>` as root: runs the CLI as the service user (token lives in /var/lib/isis, 0600 isis).
[ -f /etc/isis/isis.env ] && { set -a; . /etc/isis/isis.env; set +a; }
cd /opt/isis
exec runuser -u isis -- /opt/isis/venv/bin/python -m app.isis.cli "$@"
