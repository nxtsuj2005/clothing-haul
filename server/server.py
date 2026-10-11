#!/usr/bin/env python3
"""Tiny JSON API for the clothing haul page (stdlib only)."""
import hashlib, hmac, ipaddress, json, os, re, secrets, socket, threading, time
import urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ.get("HAUL_DATA", "/opt/clothing/items.json")
CONF = os.environ.get("HAUL_CONF", "/opt/clothing/config.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
LOCK = threading.Lock()
FAILS = {}  # ip -> (count, first_ts)

MIN_PW = 4
USERS = os.environ.get("HAUL_USERS", "/opt/clothing/users.json")
HERE = os.path.dirname(os.path.abspath(__file__))


def write_json(path, obj, mode=0o600):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# config.json holds only the signing secret; accounts live in users.json
try:
    with open(CONF) as f:
        CFG = json.load(f)
except FileNotFoundError:
    CFG = {"secret": secrets.token_hex(32)}
    write_json(CONF, CFG)
if "hash" in CFG:  # legacy single-password config: drop it, accounts replace it
    CFG = {"secret": CFG["secret"]}
    write_json(CONF, CFG)


def load_users():
    try:
        with open(USERS) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_users(users):
    write_json(USERS, users)


def hash_pw(pw, salt):
    return hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def set_pw(user, pw):
    salt = os.urandom(16)
    user["salt"], user["hash"] = salt.hex(), hash_pw(pw, salt)


def check_pw(user, pw):
    return hmac.compare_digest(hash_pw(pw, bytes.fromhex(user["salt"])), user["hash"])


def sign(msg):
    return hmac.new(bytes.fromhex(CFG["secret"]), msg.encode(), "sha256").hexdigest()


def cookie(uid):
    msg = "%d.%d" % (uid, time.time() + 30 * 86400)
    return "haul_auth=%s.%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (msg, sign(msg), 30 * 86400)


def parse_token(tok):
    try:
        uid, exp, sig = tok.split(".")
        if hmac.compare_digest(sig, sign(uid + "." + exp)) and float(exp) > time.time():
            return int(uid)
    except Exception:
        pass
    return None


def clean_sizes(raw):
    out = []
    for r in (raw if isinstance(raw, list) else [])[:12]:
        if isinstance(r, dict) and str(r.get("label", "")).strip():
            out.append({k: str(r.get(k, "")).strip()[:60] for k in ("label", "value", "note")})
    return out


def public_user(u):
    return {"id": u["id"], "name": u["name"], "admin": u.get("admin", False), "sizes": u.get("sizes", [])}


def load():
    with open(DATA) as f:
        return json.load(f)


def save(items):
    write_json(DATA, items, 0o644)


def safe_url(u):
    p = urllib.parse.urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        for ai in socket.getaddrinfo(p.hostname, None):
            if not ipaddress.ip_address(ai[4][0]).is_global:
                return False
    except Exception:
        return False
    return True


def meta(page, prop):
    for pat in (r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)' % re.escape(prop),
                r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(prop)):
        m = re.search(pat, page, re.I)
        if m:
            import html
            return html.unescape(m.group(1)).strip()
    return ""


def fmt_price(v):
    try:
        return ("%.2f" % float(str(v).replace(",", "."))).replace(".", ",")
    except ValueError:
        return ""


def scrape(url):
    """Best effort: title, image, price. Returns {} fields empty on failure."""
    out = {"name": "", "img": "", "price": "", "brand": ""}
    if not safe_url(url):
        return out
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
        with urllib.request.urlopen(req, timeout=15) as r:
            page = r.read(2_000_000).decode("utf-8", "replace")
    except Exception as e:
        print("scrape failed", url, e, flush=True)
        return out
    title = meta(page, "og:title")
    title = re.sub(r"\s*[|\-–]\s*(Zalando|bonprix).*$", "", title, flags=re.I)
    out["name"] = title
    out["img"] = meta(page, "og:image")
    price = meta(page, "product:price:amount") or meta(page, "og:price:amount")
    if not price:
        m = re.search(r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)', page)
        price = m.group(1) if m else ""
    out["price"] = fmt_price(price) if price else ""
    out["brand"] = meta(page, "product:brand") or meta(page, "og:site_name")
    return out


class H(BaseHTTPRequestHandler):
    server_version = "haul"

    def log_message(self, *a):
        pass

    def send(self, code, obj=None, headers=()):
        body = json.dumps(obj if obj is not None else {}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def user(self):
        for c in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = c.strip().partition("=")
            if k == "haul_auth":
                uid = parse_token(v)
                for u in load_users():
                    if u["id"] == uid:
                        return u
        return None

    def ip(self):
        return self.headers.get("X-Real-IP") or self.client_address[0]

    def do_GET(self):
        if self.path == "/api/items":
            return self.send(200, load())
        if self.path == "/api/users":
            return self.send(200, [public_user(u) for u in load_users()])
        if self.path == "/api/me":
            u = self.user()
            return self.send(200, {"user": public_user(u) if u else None, "setup": not load_users()})
        self.send(404, {"error": "not found"})

    def check_new_name_pw(self, d, users):
        name, pw = str(d.get("name", "")).strip()[:30], str(d.get("password", ""))
        if not name:
            return None, None, "Name fehlt"
        if len(pw) < MIN_PW:
            return None, None, "Passwort: mindestens %d Zeichen" % MIN_PW
        if any(u["name"].lower() == name.lower() for u in users):
            return None, None, "Name schon vergeben"
        return name, pw, None

    def do_POST(self):
        d = self.body()
        if self.path == "/api/login":
            ip = self.ip()
            cnt, ts = FAILS.get(ip, (0, 0))
            if cnt >= 5 and time.time() - ts < 300:
                return self.send(429, {"error": "zu viele Versuche, kurz warten"})
            name = str(d.get("name", "")).strip().lower()
            for u in load_users():
                if u["name"].lower() == name and check_pw(u, str(d.get("password", ""))):
                    FAILS.pop(ip, None)
                    return self.send(200, {"user": public_user(u)}, [("Set-Cookie", cookie(u["id"]))])
            FAILS[ip] = (cnt + 1 if time.time() - ts < 300 else 1, time.time())
            time.sleep(1)
            return self.send(401, {"error": "Name oder Passwort falsch"})
        if self.path == "/api/logout":
            return self.send(200, {}, [("Set-Cookie", "haul_auth=; Path=/; Max-Age=0")])
        if self.path == "/api/setup":  # first account, becomes admin and adopts legacy items
            with LOCK:
                users = load_users()
                if users:
                    return self.send(409, {"error": "Es gibt schon Accounts"})
                name, pw, err = self.check_new_name_pw(d, users)
                if err:
                    return self.send(400, {"error": err})
                u = {"id": 1, "name": name, "admin": True, "sizes": []}
                try:
                    with open(os.path.join(HERE, "default_sizes.json")) as f:
                        u["sizes"] = clean_sizes(json.load(f))
                except FileNotFoundError:
                    pass
                set_pw(u, pw)
                save_users([u])
                items = load()
                for i in items:
                    i.setdefault("owner", 1)
                save(items)
            return self.send(200, {"user": public_user(u)}, [("Set-Cookie", cookie(1))])
        me = self.user()
        if not me:
            return self.send(401, {"error": "nicht eingeloggt"})
        if self.path == "/api/users":
            if not me.get("admin"):
                return self.send(403, {"error": "nur Admin"})
            with LOCK:
                users = load_users()
                name, pw, err = self.check_new_name_pw(d, users)
                if err:
                    return self.send(400, {"error": err})
                u = {"id": max(x["id"] for x in users) + 1, "name": name, "admin": False, "sizes": []}
                set_pw(u, pw)
                users.append(u)
                save_users(users)
            return self.send(201, public_user(u))
        if self.path == "/api/items":
            url = str(d.get("url", "")).strip()
            if not re.match(r"https?://", url):
                return self.send(400, {"error": "Link fehlt oder ungültig"})
            sc = scrape(url)
            host = urllib.parse.urlparse(url).hostname.removeprefix("www.")
            item = {
                "brand": str(d.get("brand") or sc["brand"] or host)[:80],
                "name": str(d.get("name") or sc["name"] or host)[:200],
                "url": url,
                "img": str(d.get("img") or sc["img"])[:600],
                "price": str(d.get("price") or sc["price"])[:20],
                "size": str(d.get("size", ""))[:20],
                "tags": [str(t)[:30] for t in d.get("tags", [])][:6],
                "owner": me["id"],
            }
            with LOCK:
                items = load()
                item["id"] = max([i["id"] for i in items] + [0]) + 1
                items.append(item)
                save(items)
            return self.send(201, item)
        self.send(404, {"error": "not found"})

    def item_id(self):
        m = re.fullmatch(r"/api/items/(\d+)", self.path)
        return int(m.group(1)) if m else None

    def user_id(self):
        m = re.fullmatch(r"/api/users/(\d+)", self.path)
        return int(m.group(1)) if m else None

    def do_DELETE(self):
        me = self.user()
        iid, uid = self.item_id(), self.user_id()
        if iid is None and uid is None:
            return self.send(404)
        if not me:
            return self.send(401, {"error": "nicht eingeloggt"})
        with LOCK:
            if iid is not None:
                items = load()
                it = next((i for i in items if i["id"] == iid), None)
                if not it:
                    return self.send(404, {"error": "nicht gefunden"})
                if it.get("owner") != me["id"] and not me.get("admin"):
                    return self.send(403, {"error": "gehört einem anderen Account"})
                save([i for i in items if i["id"] != iid])
                return self.send(200, {})
            if not me.get("admin"):
                return self.send(403, {"error": "nur Admin"})
            if uid == me["id"]:
                return self.send(400, {"error": "du kannst dich nicht selbst löschen"})
            users = load_users()
            if not any(u["id"] == uid for u in users):
                return self.send(404, {"error": "nicht gefunden"})
            save_users([u for u in users if u["id"] != uid])
            items = load()  # hand the deleted account's items to the deleting admin
            for i in items:
                if i.get("owner") == uid:
                    i["owner"] = me["id"]
            save(items)
        self.send(200, {})

    def do_PATCH(self):
        me = self.user()
        iid, uid = self.item_id(), self.user_id()
        if self.path == "/api/me":
            uid = me["id"] if me else None
        elif iid is None and uid is None:
            return self.send(404)
        if not me:
            return self.send(401, {"error": "nicht eingeloggt"})
        d = self.body()
        with LOCK:
            if iid is not None:
                items = load()
                for i in items:
                    if i["id"] == iid:
                        if i.get("owner") != me["id"] and not me.get("admin"):
                            return self.send(403, {"error": "gehört einem anderen Account"})
                        for k, mx in (("brand", 80), ("name", 200), ("url", 600), ("img", 600), ("price", 20), ("size", 20)):
                            if k in d:
                                i[k] = str(d[k])[:mx]
                        if "tags" in d:
                            i["tags"] = [str(t)[:30] for t in d["tags"]][:6]
                        save(items)
                        return self.send(200, i)
                return self.send(404, {"error": "nicht gefunden"})
            if uid != me["id"] and not me.get("admin"):
                return self.send(403, {"error": "nur Admin"})
            users = load_users()
            for u in users:
                if u["id"] == uid:
                    if "sizes" in d and uid == me["id"]:
                        u["sizes"] = clean_sizes(d["sizes"])
                    if d.get("password"):
                        if len(str(d["password"])) < MIN_PW:
                            return self.send(400, {"error": "Passwort: mindestens %d Zeichen" % MIN_PW})
                        set_pw(u, str(d["password"]))
                    save_users(users)
                    return self.send(200, public_user(u))
        self.send(404, {"error": "nicht gefunden"})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8080), H).serve_forever()
