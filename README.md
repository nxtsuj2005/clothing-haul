<img src="assets/icon.svg" width="64" height="64" alt="">

# Clothing haul

Static page plus a tiny stdlib Python API (admin login, add/edit/delete items). Runs in a Proxmox LXC behind nginx.

- `index.html` page, renders the list from `/api/items`
- `server/server.py` JSON API on 127.0.0.1:8080 (nginx proxies `/api/`)
- `server/items.json` seed data; the live list is `/opt/clothing/items.json` on the LXC
- `deploy.sh` copies page and API to the LXC (not the data)

Admin password hash and signing secret live in `/opt/clothing/config.json` on the LXC (never committed).
