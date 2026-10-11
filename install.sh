#!/usr/bin/env bash
# Quick install for Proxmox VE (run as root on the PVE host), in the style of the community helper scripts:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/nxtsuj2005/clothing-haul/main/install.sh)"
# Creates a Debian 12 LXC with nginx + the clothing haul page and API.
# Update an existing install:  bash install.sh update
set -Eeuo pipefail

REPO="${HAUL_REPO:-nxtsuj2005/clothing-haul}"
BRANCH="${HAUL_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/$REPO/$BRANCH"

msg()  { echo -e "\e[1;34m[*]\e[0m $*"; }
ok()   { echo -e "\e[1;32m[ok]\e[0m $*"; }
die()  { echo -e "\e[1;31m[x]\e[0m $*" >&2; exit 1; }
trap 'die "failed at line $LINENO"' ERR

# ---------- script that runs INSIDE the container ----------
read -r -d '' INNER <<'EOF' || true
#!/usr/bin/env bash
set -Eeuo pipefail
RAW="$1"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx python3 curl ca-certificates >/dev/null
install -d -o www-data -g www-data /opt/clothing
fetch() { curl -fsSL "$RAW/$1" -o "$2"; }
fetch index.html /var/www/html/index.html
fetch server/server.py /opt/clothing/server.py
# live data is never overwritten
[ -f /opt/clothing/items.json ] || fetch server/items.json /opt/clothing/items.json
chown -R www-data:www-data /opt/clothing

cat > /etc/systemd/system/clothing-api.service <<UNIT
[Unit]
Description=Clothing haul API
After=network.target
[Service]
User=www-data
ExecStart=/usr/bin/python3 /opt/clothing/server.py
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/clothing
[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/nginx/sites-available/default <<NGINX
server {
	listen 80 default_server;
	listen [::]:80 default_server;
	root /var/www/html;
	index index.html;
	server_name _;
	location /api/ {
		proxy_pass http://127.0.0.1:8080;
		proxy_set_header X-Real-IP \$remote_addr;
		client_max_body_size 100k;
	}
	location / {
		try_files \$uri \$uri/ =404;
	}
}
NGINX

cat > /usr/local/bin/update-clothing-haul <<UPD
#!/usr/bin/env bash
set -e
curl -fsSL "$RAW/index.html" -o /var/www/html/index.html
curl -fsSL "$RAW/server/server.py" -o /opt/clothing/server.py
chown -R www-data:www-data /opt/clothing
systemctl restart clothing-api
echo "updated (data in /opt/clothing untouched)"
UPD
chmod +x /usr/local/bin/update-clothing-haul

systemctl daemon-reload
systemctl enable --now clothing-api
nginx -t
systemctl enable nginx
systemctl restart nginx
EOF

# ---------- host side ----------
[ "$(id -u)" -eq 0 ] || die "run as root"
command -v pct >/dev/null || die "pct not found: run this on a Proxmox VE host"

if [ "${1:-}" = "update" ]; then
  CTID="${2:-}"
  [ -n "$CTID" ] || read -r -p "Container ID to update: " CTID
  pct exec "$CTID" -- update-clothing-haul
  exit 0
fi

# defaults (override via env, or pick "Advanced" below)
CTID="${CTID:-$(pvesh get /cluster/nextid)}"
HN="${HN:-clothing-haul}"
CORES="${CORES:-1}"
RAM="${RAM:-256}"
DISK="${DISK:-2}"
BRG="${BRG:-vmbr0}"
NET="${NET:-dhcp}"        # dhcp or CIDR like 192.168.1.50/24
GW="${GW:-}"              # only needed with a static CIDR
STORAGE="${STORAGE:-$(pvesm status -content rootdir | awk 'NR>1{print $1; exit}')}"
[ -n "$STORAGE" ] || die "no storage with rootdir support found"

if command -v whiptail >/dev/null && [ -t 0 ]; then
  if whiptail --title "Clothing Haul" --yesno "Use default settings?\n\nCTID $CTID, ${CORES} core, ${RAM} MB RAM, ${DISK} GB on $STORAGE, bridge $BRG, DHCP" --yes-button "Default" --no-button "Advanced" 14 60; then
    :
  else
    ask() { whiptail --title "Clothing Haul" --inputbox "$1" 9 60 "$2" 3>&1 1>&2 2>&3; }
    CTID=$(ask "Container ID" "$CTID")
    HN=$(ask "Hostname" "$HN")
    RAM=$(ask "RAM (MB)" "$RAM")
    DISK=$(ask "Disk (GB)" "$DISK")
    STORAGE=$(ask "Storage" "$STORAGE")
    BRG=$(ask "Bridge" "$BRG")
    NET=$(ask "IP (dhcp or CIDR, e.g. 192.168.1.50/24)" "$NET")
    if [ "$NET" != "dhcp" ]; then GW=$(ask "Gateway" "$GW"); fi
  fi
fi

pct status "$CTID" >/dev/null 2>&1 && die "CTID $CTID already exists"

msg "Looking for a Debian 12 template"
TPL_STORAGE="${TPL_STORAGE:-local}"
pveam update >/dev/null
TPL="$(pveam list "$TPL_STORAGE" | awk '/debian-12-standard/{print $1; exit}' | sed 's|.*/||')"
if [ -z "$TPL" ]; then
  TPL="$(pveam available --section system | awk '/debian-12-standard/{print $2}' | sort -V | tail -1)"
  [ -n "$TPL" ] || die "no debian-12 template available"
  msg "Downloading $TPL"
  pveam download "$TPL_STORAGE" "$TPL" >/dev/null
fi

NETCFG="name=eth0,bridge=$BRG,ip=$NET"
[ "$NET" = "dhcp" ] || NETCFG="$NETCFG,gw=$GW"

msg "Creating LXC $CTID ($HN)"
pct create "$CTID" "$TPL_STORAGE:vztmpl/$TPL" \
  --hostname "$HN" --cores "$CORES" --memory "$RAM" --swap 0 \
  --rootfs "$STORAGE:$DISK" --net0 "$NETCFG" \
  --unprivileged 1 --onboot 1 --tags clothing-haul >/dev/null
pct start "$CTID"

msg "Waiting for network"
for _ in $(seq 1 30); do
  pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
  sleep 2
done
pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 || die "container has no network/DNS"

msg "Installing"
TMP="$(mktemp)"; printf '%s\n' "$INNER" > "$TMP"
pct push "$CTID" "$TMP" /root/install-inner.sh --perms 755
rm -f "$TMP"
pct exec "$CTID" -- bash /root/install-inner.sh "$RAW"
pct exec "$CTID" -- rm -f /root/install-inner.sh

IP="$(pct exec "$CTID" -- hostname -I | awk '{print $1}')"
ok "Done: http://$IP"
echo "Open it, click \"Ersten Account anlegen\" and create the admin account right away."
echo "Update later: bash install.sh update $CTID   (or inside the CT: update-clothing-haul)"
