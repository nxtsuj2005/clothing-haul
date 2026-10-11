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


def save_conf():
    tmp = CONF + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(CFG, f)
    os.replace(tmp, CONF)


# {"secret": hex} until an admin password is chosen, then also {"salt": hex, "hash": hex}
try:
    with open(CONF) as f:
        CFG = json.load(f)
except FileNotFoundError:
    CFG = {"secret": secrets.token_hex(32)}
    save_conf()


def hash_pw(pw, salt):
    return hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def check_pw(pw):
    if "hash" not in CFG:
        return False
    return hmac.compare_digest(hash_pw(pw, bytes.fromhex(CFG["salt"])), CFG["hash"])


def cookie():
    return "haul_admin=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (make_token(), 30 * 86400)


def make_token():
    exp = str(int(time.time()) + 30 * 86400)
    sig = hmac.new(bytes.fromhex(CFG["secret"]), exp.encode(), "sha256").hexdigest()
    return exp + "." + sig


def valid_token(tok):
    try:
        exp, sig = tok.split(".")
        good = hmac.new(bytes.fromhex(CFG["secret"]), exp.encode(), "sha256").hexdigest()
        return hmac.compare_digest(sig, good) and int(exp) > time.time()
    except Exception:
        return False


def load():
    with open(DATA) as f:
        return json.load(f)


def save(items):
    tmp = DATA + ".tmp"
    with open(tmp, "w") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA)


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

    def admin(self):
        for c in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = c.strip().partition("=")
            if k == "haul_admin" and valid_token(v):
                return True
        return False

    def ip(self):
        return self.headers.get("X-Real-IP") or self.client_address[0]

    def do_GET(self):
        if self.path == "/api/items":
            return self.send(200, load())
        if self.path == "/api/me":
            return self.send(200, {"admin": self.admin(), "setup": "hash" not in CFG})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        d = self.body()
        if self.path == "/api/setup":
            pw = str(d.get("password", ""))
            if len(pw) < MIN_PW:
                return self.send(400, {"error": "mindestens %d Zeichen" % MIN_PW})
            with LOCK:
                if "hash" in CFG:
                    return self.send(409, {"error": "Admin ist schon eingerichtet"})
                salt = os.urandom(16)
                CFG["salt"], CFG["hash"] = salt.hex(), hash_pw(pw, salt)
                save_conf()
            return self.send(200, {"admin": True}, [("Set-Cookie", cookie())])
        if self.path == "/api/login":
            ip = self.ip()
            cnt, ts = FAILS.get(ip, (0, 0))
            if cnt >= 5 and time.time() - ts < 300:
                return self.send(429, {"error": "zu viele Versuche, kurz warten"})
            if check_pw(str(d.get("password", ""))):
                FAILS.pop(ip, None)
                return self.send(200, {"admin": True}, [("Set-Cookie", cookie())])
            FAILS[ip] = (cnt + 1 if time.time() - ts < 300 else 1, time.time())
            time.sleep(1)
            return self.send(401, {"error": "falsches Passwort"})
        if self.path == "/api/logout":
            return self.send(200, {}, [("Set-Cookie", "haul_admin=; Path=/; Max-Age=0")])
        if not self.admin():
            return self.send(401, {"error": "nicht eingeloggt"})
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

    def do_DELETE(self):
        iid = self.item_id()
        if iid is None:
            return self.send(404)
        if not self.admin():
            return self.send(401, {"error": "nicht eingeloggt"})
        with LOCK:
            items = load()
            new = [i for i in items if i["id"] != iid]
            if len(new) == len(items):
                return self.send(404, {"error": "nicht gefunden"})
            save(new)
        self.send(200, {})

    def do_PATCH(self):
        iid = self.item_id()
        if iid is None:
            return self.send(404)
        if not self.admin():
            return self.send(401, {"error": "nicht eingeloggt"})
        d = self.body()
        with LOCK:
            items = load()
            for i in items:
                if i["id"] == iid:
                    for k, mx in (("brand", 80), ("name", 200), ("url", 600), ("img", 600), ("price", 20), ("size", 20)):
                        if k in d:
                            i[k] = str(d[k])[:mx]
                    if "tags" in d:
                        i["tags"] = [str(t)[:30] for t in d["tags"]][:6]
                    save(items)
                    return self.send(200, i)
        self.send(404, {"error": "nicht gefunden"})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8080), H).serve_forever()
