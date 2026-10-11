#!/usr/bin/env bash
# Push page + API code to the LXC. items.json is NOT copied: the live list is edited via the admin UI.
set -euo pipefail
cd "$(dirname "$0")"
SSH_OPTS=(-i "$HOME/.ssh/clothing_lxc" -o IdentitiesOnly=yes -o BatchMode=yes)
HOST=root@192.168.20.244
scp -q "${SSH_OPTS[@]}" index.html "$HOST:/var/www/html/index.html"
scp -q "${SSH_OPTS[@]}" server/server.py "$HOST:/opt/clothing/server.py"
ssh "${SSH_OPTS[@]}" "$HOST" 'systemctl restart clothing-api && systemctl is-active clothing-api'
