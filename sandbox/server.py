"""Local stand-in for the services Culprit talks to, so the whole loop runs on one machine.

Plays four roles, in memory:
  flag tool        GET  /config?user=ID      variants + killswitch_<flag> (like Remote Config / Statsig)
  crash + sessions POST /events              sessions and FATAL / ANR / NON_FATAL events, tagged ff_<flag>
  Slack            POST /slack               alert text
  audit table      GET|POST /audit           rows the job writes
Job helpers: GET /counts and /segments return what the BigQuery SQL would; POST /kill and /killswitch.
Dashboard: http://localhost:8787/

Usage: python3 server.py [--port 8787]
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SIGNALS = ("FATAL", "ANR", "NON_FATAL")
WINDOW = dt.timedelta(hours=24)

# Flags the sandbox serves: % of users who get "on". Control is "off".
FLAGS = {"new_checkout": 50}

lock = threading.Lock()
state = {}


def reset():
    with lock:
        state.update(events=[], slack=[], audit=[], killed={f: False for f in FLAGS})


reset()


def now():
    return dt.datetime.now(dt.timezone.utc)


def variant(flag, user):
    bucket = int(hashlib.sha256(f"{flag}:{user}".encode()).hexdigest(), 16) % 100
    return "on" if bucket < FLAGS[flag] else "off"


def config(user):
    out = {}
    for flag in FLAGS:
        out[flag] = variant(flag, user)
        out["killswitch_" + flag] = state["killed"][flag]
    return out


def recent(start, end):
    return [e for e in state["events"] if start <= e["ts"] <= end]


def counts(end):
    """Same shape as main.SQL: one row per (flag, variant, version, signal), zero-filled."""
    users, crashed = {}, {}
    for e in recent(end - WINDOW, end):
        for key, value in e.get("tags", {}).items():
            if not key.startswith("ff_"):
                continue
            k = (key[3:], value, e["version"])
            if e["type"] == "session":
                users.setdefault(k, set()).add(e["user"])
            elif e["type"] in SIGNALS:
                crashed.setdefault(k + (e["type"],), set()).add(e["user"])
    return [
        dict(flag=f, variant=v, version=ver, signal=s, users=len(u),
             crashed_users=len(crashed.get((f, v, ver, s), ())))
        for (f, v, ver), u in users.items() for s in SIGNALS
    ]


def segments(q, end):
    """Same shape as main.SEGMENTS_SQL: affected users per OS and device, variant and control."""
    tag, keep = "ff_" + q["flag"], (q["variant"], q["control"])
    seen = {}
    for e in recent(end - WINDOW, end):
        v = e.get("tags", {}).get(tag)
        if v in keep and e["type"] == q["signal"] and e["version"] == q["version"]:
            for dim in ("os", "device"):
                seen.setdefault((v, dim, e.get(dim, "")), set()).add(e["user"])
    return [dict(variant=v, dim=d, segment=s, crashed_users=len(u)) for (v, d, s), u in seen.items()]


def kill(flag):
    if flag not in FLAGS:
        raise KeyError(flag)
    if state["killed"][flag]:
        return "already_killed"
    state["killed"][flag] = True
    return "killed"


def page():
    esc = html.escape
    rows = sorted(counts(now()), key=lambda r: (r["flag"], r["signal"], r["variant"]))
    table = "".join(
        f"<tr><td>{esc(r['flag'])}</td><td>{esc(r['variant'])}</td><td>{esc(r['version'])}</td>"
        f"<td>{r['signal']}</td><td>{r['users']}</td><td>{r['crashed_users']}</td>"
        f"<td>{r['crashed_users'] / r['users']:.2%}</td></tr>"
        for r in rows if r["users"]
    ) or "<tr><td colspan=7>No traffic yet. Run simulate.py or open the sample app.</td></tr>"
    switches = "".join(
        f"<form method=post action=/killswitch><input type=hidden name=flag value='{esc(f)}'>"
        f"<input type=hidden name=on value='{'0' if on else '1'}'>"
        f"<code>killswitch_{esc(f)}</code> is <b class={'on' if on else 'off'}>{'ON' if on else 'off'}</b> "
        f"<button>{'Turn off (undo kill)' if on else 'Turn on'}</button></form>"
        for f, on in state["killed"].items()
    )
    slack = "".join(f"<li>{esc(m)}</li>" for m in reversed(state["slack"])) or "<li>None yet.</li>"
    audit = "".join(
        f"<tr><td>{esc(a['ts'][:19])}</td><td>{esc(a['flag'])}={esc(a['variant'])}</td><td>{esc(a['version'])}</td>"
        f"<td>{esc(a['signal'])}</td><td>{esc(a['action'])}</td></tr>"
        for a in reversed(state["audit"])
    ) or "<tr><td colspan=5>None yet.</td></tr>"
    return f"""<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=3>
<title>Culprit sandbox</title>
<style>
:root {{ color-scheme: light dark; --b: #8884; }}
body {{ font: 15px system-ui, sans-serif; max-width: 900px; margin: 24px auto; padding: 0 16px; }}
table {{ border-collapse: collapse; width: 100%; display: block; overflow-x: auto; }} td, th {{ border-bottom: 1px solid var(--b); padding: 4px 8px; text-align: left; }}
.on {{ color: #c93434; }} li {{ margin: 6px 0; white-space: pre-wrap; }} form {{ display: inline; margin-right: 16px; }}
</style>
<h1>Culprit sandbox</h1>
<p>{len(state['events'])} events. Refreshes every 3s.
<form method=post action=/reset><button>Reset everything</button></form></p>
<h2>Kill switches</h2><p>{switches}</p>
<h2>Slack</h2><ul>{slack}</ul>
<h2>Last 24h by variant</h2>
<table><tr><th>Flag</th><th>Variant</th><th>Version</th><th>Signal</th><th>Users</th><th>Affected</th><th>Rate</th></tr>{table}</table>
<h2>Audit table</h2>
<table><tr><th>Time (UTC)</th><th>Variant</th><th>Version</th><th>Signal</th><th>Action</th></tr>{audit}</table>"""


class Handler(BaseHTTPRequestHandler):
    def send(self, body, status=200, kind="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            return {k: v[0] for k, v in parse_qs(raw).items()}
        return json.loads(raw or "null")

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        end = dt.datetime.fromisoformat(q["end"]) if "end" in q else now()
        with lock:
            if url.path == "/":
                return self.send(page(), kind="text/html; charset=utf-8")
            if url.path == "/config":
                return self.send(config(q.get("user", "")))
            if url.path == "/counts":
                return self.send(counts(end))
            if url.path == "/segments":
                return self.send(segments(q, end))
            if url.path == "/audit":
                since = dt.datetime.fromisoformat(q["since"]) if "since" in q else end - WINDOW
                return self.send([a for a in state["audit"] if dt.datetime.fromisoformat(a["ts"]) > since])
        self.send({"error": "not found"}, 404)

    def do_POST(self):
        path, data = urlparse(self.path).path, self.body()
        with lock:
            if path == "/events":
                for e in data if isinstance(data, list) else [data]:
                    e["ts"] = now()
                    state["events"].append(e)
                return self.send({"ok": True})
            if path == "/slack":
                state["slack"].append(data["text"])
                print("SLACK:", data["text"], flush=True)
                return self.send("ok", kind="text/plain")
            if path == "/audit":
                state["audit"].extend(data)
                return self.send({"ok": True})
            if path == "/kill":
                return self.send({"result": kill(data["flag"])})
            if path == "/killswitch":  # dashboard button: turn a kill switch on or off by hand
                state["killed"][data["flag"]] = data["on"] in ("1", True)
                return self.redirect()
        if path == "/reset":
            reset()
            return self.redirect()
        self.send({"error": "not found"}, 404)

    def redirect(self):
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, *a):  # keep the console for Slack messages
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    port = ap.parse_args().port
    print(f"Culprit sandbox on http://localhost:{port}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
