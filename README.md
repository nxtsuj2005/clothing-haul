<img src="assets/icon.svg" width="64" height="64" alt="">

# Clothing haul

Static page plus a tiny stdlib Python API (admin login, add/edit/delete items). Runs in a Proxmox LXC behind nginx.

- `index.html` page, renders the list from `/api/items`
- `server/server.py` JSON API on 127.0.0.1:8080 (nginx proxies `/api/`)
- `server/items.json` seed data; the live list is `/opt/clothing/items.json` on the LXC
- `deploy.sh` copies page and API to the LXC (not the data)

## Quick install (Proxmox VE)

Run on the PVE host as root. Creates a Debian 12 LXC (1 core, 256 MB, 2 GB), installs nginx and the app, prints the URL:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/nxtsuj2005/clothing-haul/main/install.sh)"
```

Settings can be overridden via env (`CTID`, `HN`, `RAM`, `DISK`, `STORAGE`, `BRG`, `NET`, `GW`) or via the "Advanced" dialog. Update later (keeps all data):

```bash
bash install.sh update <CTID>    # or inside the container: update-clothing-haul
```

The one-liner only works once the repo is public (it downloads from raw.githubusercontent.com). A fork can be installed with `HAUL_REPO=<user>/<repo>`.

## Accounts

No default password. On a fresh install the first visitor clicks "Ersten Account anlegen" and picks a name and password (min. 4 chars). That account is the admin, adopts the seed items and gets the sizes from `server/default_sizes.json`. The admin creates further accounts under "Konto".

- Everyone (also logged out) sees all items and everyone's size cards; the tabs filter by person.
- Logged-in users add items and edit/delete their own; the admin can edit/delete all.
- Each user maintains their own size cards under "Konto" (also password change).
- Accounts live in `/opt/clothing/users.json` (salted scrypt hashes), the cookie signing secret in `/opt/clothing/config.json`. Neither is committed.

Password reset: edit/remove the user in `users.json` (delete the file to start over with the setup prompt) and restart `clothing-api`. Until the first account exists, anyone who can reach the page can claim admin, so register right after deploying.
