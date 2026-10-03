"""Minimal stand-in for the Metal ERP backend: checkin + zip download (update e2e).
Write a version into work/target.txt to make it offer that version.
Offers `target.txt`'s version to any agent not on it, unless that agent already
reported update_failed_version == target (mirrors the real backend's rule)."""
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
REL = json.load(open(os.path.join(WORK, "out", "releases.json")))
EVENTS = os.path.join(WORK, "events.log")
TARGET = os.path.join(WORK, "target.txt")
PORT = 8099
failed = set()


def log(msg):
    with open(EVENTS, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            raw = b""
            while True:
                size = int(self.rfile.readline().strip() or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    break
                raw += self.rfile.read(size)
                self.rfile.readline()
        else:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.loads(raw or b"{}")
        if self.path.endswith("/checkin"):
            ver = body.get("agent_version")
            fv = body.get("update_failed_version")
            log(f"checkin agent_version={ver} failed={fv} err={body.get('update_error')}")
            if fv:
                failed.add(fv)
            target = open(TARGET).read().strip() if os.path.exists(TARGET) else ""
            update = None
            if target and ver and ver != target and target not in failed and target in REL:
                r = REL[target]
                update = {"version": target, "url": f"http://127.0.0.1:{PORT}/zip/{target}",
                          "sha256": r["sha256"], "size_bytes": r["size_bytes"], "signature": r["signature"]}
                log(f"  -> offering {target}")
            return self._json({"shop_id": "e2e", "checked_in_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "outbox": [], "update": update})
        self._json({"detail": "n/a"}, 404)

    def do_GET(self):
        if self.path.startswith("/zip/"):
            v = self.path.split("/")[-1]
            data = open(REL[v]["zip"], "rb").read()
            log(f"serving zip {v} ({len(data)} bytes)")
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._json({"ok": True})


if __name__ == "__main__":
    open(EVENTS, "w").close()
    log("backend up")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
