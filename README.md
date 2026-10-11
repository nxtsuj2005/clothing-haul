<img src="assets/icon.svg" width="64" height="64" alt="">

# Clothing haul

Static page plus a tiny stdlib Python API (admin login, add/edit/delete items). Runs in a Proxmox LXC behind nginx.

- `index.html` page, renders the list from `/api/items`
- `server/server.py` JSON API on 127.0.0.1:8080 (nginx proxies `/api/`)
- `server/items.json` seed data; the live list is `/opt/clothing/items.json` on the LXC
- `deploy.sh` copies page and API to the LXC (not the data)

## Admin setup

No default password. On a fresh install the first visitor clicks "Admin einrichten" at the bottom of the page and picks a password (min. 4 chars). Only the salted scrypt hash and a signing secret are stored, in `/opt/clothing/config.json` on the server (never committed).

To reset the password: remove the `salt` and `hash` keys from that file and restart `clothing-api`; the setup prompt appears again. Until the password is set, anyone who can reach the page can claim admin, so set it right after deploying.
