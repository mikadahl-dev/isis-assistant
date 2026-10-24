#!/bin/sh
# Installs/updates the code from this checkout into /opt/isis, pinned deps, the `isis` command and the units.
# Run as root after scripts/setup.sh; safe to run again after every change. (On Proxmox ./deploy.sh calls it in the CT.)
set -e
SRC=$(cd "$(dirname "$0")/.." && pwd)
[ -d /opt/isis/venv ] || { echo "run scripts/setup.sh first" >&2; exit 1; }

python3 -m py_compile "$SRC"/app/*/*.py "$SRC"/app/*/*/*.py
rm -rf /opt/isis/app.new && mkdir -p /opt/isis/app.new
tar -C "$SRC" --exclude=__pycache__ -cf - app systemd requirements.txt | tar -C /opt/isis/app.new -xf -
cd /opt/isis && rm -rf app systemd && mv app.new/app app.new/systemd app.new/requirements.txt . && rmdir app.new
chown -R root:root app systemd requirements.txt && chmod -R go-w app systemd
/opt/isis/venv/bin/pip install -q -r /opt/isis/requirements.txt

install -m755 /opt/isis/systemd/isis-wrapper.sh /usr/local/bin/isis
install -m644 /opt/isis/systemd/*.service /opt/isis/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
units="isis-sync.timer isis-api.service"
[ -x /opt/jeff/llama.cpp/build/bin/llama-server ] && units="$units jeff.service"
systemctl enable -q --now $units
systemctl restart isis-api.service
# isis-wissen.timer (nightly knowledge net) stays off until you like the result: systemctl enable --now isis-wissen.timer
/usr/local/bin/isis --help >/dev/null && echo "ok: isis installed ($units)"
