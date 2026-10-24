#!/bin/bash
# Proxmox host: creates the unprivileged Debian 13 container from config.env and runs scripts/setup.sh in it (once).
# Then ./deploy.sh, then inside the CT `isis login`. Undo: pct stop $CT_ID && pct destroy $CT_ID
# (DATA_HOST_DIR and its ACL stay, remove by hand).
set -euo pipefail
DIR=$(dirname "$(readlink -f "$0")")
[ -f "$DIR/config.env" ] || { echo "cp config.example.env config.env and edit it first" >&2; exit 1; }
set -a; . "$DIR/config.env"; set +a
CT=$CT_ID

if ! pct status "$CT" >/dev/null 2>&1; then
  ip=$CT_IP; [ "$ip" = dhcp ] || ip="$CT_IP,gw=$CT_GW"
  pct create "$CT" "$CT_TEMPLATE" --hostname "$CT_HOSTNAME" --ostype debian --unprivileged 1 --features nesting=1 \
    --cores "$CT_CORES" --memory "$CT_MEMORY_MB" --swap 512 --rootfs "$CT_STORAGE:$CT_DISK_GB" \
    --net0 "name=eth0,bridge=$CT_BRIDGE,firewall=1,ip=$ip,type=veth" ${CT_DNS:+--nameserver "$CT_DNS"} \
    --timezone host --onboot 1 --description "isis: Moodle (ISIS) -> files, calendar, knowledge net"
fi
if [ -n "${DATA_HOST_DIR:-}" ] && ! pct config "$CT" | grep -q "^mp0:"; then
  # unprivileged CT: uid N inside = 100000+N on the host. ACL so only the isis user gets in (works on ext4/zfs/ntfs3)
  mkdir -p "$DATA_HOST_DIR" && chmod 0770 "$DATA_HOST_DIR"
  setfacl -m "u:$((100000 + ISIS_UID)):rwx,m::rwx" "$DATA_HOST_DIR" 2>/dev/null || chown "$((100000 + ISIS_UID))" "$DATA_HOST_DIR"
  pct set "$CT" -mp0 "$DATA_HOST_DIR,mp=/mnt/isis"
fi
pct status "$CT" | grep -q running || { pct start "$CT"; sleep 5; }

pct push "$CT" "$DIR/scripts/setup.sh" /root/isis-setup.sh --perms 0755
pct exec "$CT" -- env ISIS_URL="$ISIS_URL" ISIS_OLLAMA="$ISIS_OLLAMA" ISIS_MODEL="$ISIS_MODEL" ISIS_API_ALLOW="$ISIS_API_ALLOW" \
  ISIS_UID="$ISIS_UID" WITH_JEFF="$WITH_JEFF" /root/isis-setup.sh
pct exec "$CT" -- rm -f /root/isis-setup.sh
echo "CT $CT ready. Next: ./deploy.sh"
