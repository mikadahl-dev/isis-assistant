#!/bin/bash
# One-time system setup on Debian 13 (inside the Proxmox CT, a VM or bare metal), run as root.
# Creates the users isis (+ jeff), /etc/isis/isis.env with a random API token, the venv and, with WITH_JEFF=1,
# a CPU build of llama.cpp with the jeff models. Settings come from the environment (see config.example.env).
# Afterwards: scripts/install-app.sh (or ./deploy.sh on Proxmox), then `isis login`.
set -euo pipefail
: "${ISIS_URL:=https://isis.tu-berlin.de}" "${ISIS_OLLAMA:=http://127.0.0.1:11434}" "${ISIS_MODEL:=qwen3.5:9b}"
: "${ISIS_API_ALLOW:=127.0.0.1}" "${ISIS_UID:=999}" "${WITH_JEFF:=1}"
export DEBIAN_FRONTEND=noninteractive

apt-get update -q
apt-get -yq full-upgrade
apt-get -yq install --no-install-recommends python3 python3-venv ca-certificates curl locales unattended-upgrades acl
sed -i "s/^# *de_DE.UTF-8/de_DE.UTF-8/; s/^# *en_US.UTF-8/en_US.UTF-8/" /etc/locale.gen && locale-gen >/dev/null
update-locale LANG=en_US.UTF-8
echo "unattended-upgrades unattended-upgrades/enable_auto_updates boolean true" | debconf-set-selections
dpkg-reconfigure -f noninteractive unattended-upgrades

# --- isis: own system user, code owned by root; token in /var/lib/isis/token (0600 isis) ---
id isis >/dev/null 2>&1 || useradd --system --uid "$ISIS_UID" --user-group --home-dir /var/lib/isis --shell /usr/sbin/nologin isis
install -d -m 755 /opt/isis
install -d -m 700 -o isis -g isis /var/lib/isis
install -d -m 750 -o root -g isis /etc/isis
[ -d /mnt/isis ] || install -d -m 750 -o isis -g isis /mnt/isis
if [ ! -f /etc/isis/isis.env ]; then
  jeff_url=http://127.0.0.1:8766; [ "$WITH_JEFF" = 1 ] || jeff_url=off
  (umask 027; cat > /etc/isis/isis.env <<EOF2
# Settings for isis-* (root:isis 0640). Reference: docs/CONFIG.md
ISIS_URL=$ISIS_URL
ISIS_DATA=/mnt/isis
ISIS_OLLAMA=$ISIS_OLLAMA
ISIS_MODEL=$ISIS_MODEL
ISIS_JEFF=$jeff_url
ISIS_API_ALLOW=$ISIS_API_ALLOW
ISIS_API_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
EOF2
  ); chgrp isis /etc/isis/isis.env
fi
[ -d /opt/isis/venv ] || python3 -m venv /opt/isis/venv
runuser -u isis -- touch /mnt/isis/.isis-data   # marker: sync refuses to write files if the data disk is missing

# --- jeff (github.com/firelex/jeff): llama.cpp (CPU) + jeff-base Q8_0 + ground adapter, root-owned ---
if [ "$WITH_JEFF" = 1 ]; then
  apt-get -yq install --no-install-recommends build-essential cmake git >/dev/null
  id jeff >/dev/null 2>&1 || useradd --system --user-group --home-dir /opt/jeff --shell /usr/sbin/nologin jeff
  install -d -m 755 /opt/jeff /opt/jeff/models
  cd /opt/jeff && { [ -d llama.cpp ] || git clone -q https://github.com/ggml-org/llama.cpp; }
  cd llama.cpp && git fetch -q && git checkout -q 620d2225cc436ab0b001e3200e590e3b286ef9ab   # v0.7.0, needs >= cb7934c (qwen35 + output LoRA)
  cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON >/dev/null && cmake --build build --target llama-server -j"$(nproc)" >/dev/null
  cd /opt/jeff/models
  get() { [ -s "$(basename "$2")" ] || curl -sfL -o "$(basename "$2")" "https://huggingface.co/mstrasser/$1/resolve/main/$2"; }
  get jeff-base-gguf models/base-q8_0.gguf; get jeff-base-gguf models/base.jeff.json
  get jeff-adapter-ground-gguf loras/ground.gguf; get jeff-adapter-ground-gguf models/ground.jeff.json
  sha256sum -c <<EOF2
c4f7c3c1bef53b4441b45c8068fc97ae2731ada2fbb014484f725abb1ac514ba  base-q8_0.gguf
ced9506c3ad5de4096ea244e3de0fde6c2c064d50432e180b081dddf22aea95b  ground.gguf
EOF2
  chown -R root:root /opt/jeff
fi
echo "setup ok. Next: scripts/install-app.sh (Proxmox: ./deploy.sh)"
