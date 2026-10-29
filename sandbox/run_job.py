"""Run the real Culprit job (job/main.py run()) against the local sandbox.

What's real: detection, thresholds, dedupe, kill policy and daily cap, breakdown, Slack text, audit rows.
What's swapped: the BigQuery SQL and Sentry queries (sandbox /counts and /segments return the same
rows), the Remote Config / Statsig write (sandbox /kill), and the audit table (sandbox /audit).
Test those for real in a staging project (see the README).

Usage: python3 run_job.py [--auto-kill] [--every SECONDS] [--url http://localhost:8787]
Thresholds can be overridden as in production: MIN_USERS=50 python3 run_job.py
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time
import types
import urllib.parse
import urllib.request

JOB = pathlib.Path(__file__).resolve().parent.parent / "job"
sys.path.insert(0, str(JOB))
sys.dont_write_bytecode = True


class Response:
    def __init__(self, r):
        self.status, self.body = r.status, r.read()

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return json.loads(self.body)


def http(url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return Response(r)


# The job imports these at the top; none are needed here. requests.post is the only call left (Slack).
for name in ["google", "google.auth", "google.auth.transport", "google.auth.transport.requests",
             "google.cloud", "google.cloud.bigquery"]:
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["google.auth.transport.requests"].AuthorizedSession = None
sys.modules["google.cloud"].bigquery = sys.modules["google.cloud.bigquery"]
sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda url, json=None, **kw: http(url, json)))

import main  # noqa: E402


def wire(url):
    """Point the job's data, kill and audit calls at the sandbox."""
    get = lambda path, **q: http(f"{url}{path}?{urllib.parse.urlencode(q)}").json()
    main.counts = lambda bq, flags, end: get("/counts", end=end.isoformat())
    main.segments_bigquery = lambda bq, f, control, end: get(
        "/segments", flag=f.flag, variant=f.variant, control=control,
        version=f.version, signal=f.signal, end=end.isoformat())
    main.kill = lambda flag: http(f"{url}/kill", {"flag": flag}).json()["result"]
    main.query = lambda bq, sql, since: get("/audit", since=since.isoformat())  # the dedupe read in run()
    return types.SimpleNamespace(insert_rows_json=lambda table, rows: http(f"{url}/audit", rows) and [])


def main_():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8787")
    ap.add_argument("--auto-kill", action="store_true", help="AUTO_KILL=1 and auto_kill on every flag")
    ap.add_argument("--every", type=int, metavar="SECONDS", help="repeat, like the 15-minute scheduler")
    a = ap.parse_args()

    flags = json.loads((JOB / "flags.json").read_text())
    if a.auto_kill:
        os.environ["AUTO_KILL"] = "1"
        flags = {k: {**v, "auto_kill": True} for k, v in flags.items()}
    os.environ.update(SLACK_WEBHOOK=f"{a.url}/slack", AUDIT_TABLE="sandbox.culprit.actions")
    os.environ.setdefault("FLAG_TOOL", "firebase")
    bq = wire(a.url)

    while True:
        before = len(main.query(bq, "", since=dt.datetime.now(dt.timezone.utc) - main.WINDOW))
        main.run(bq, flags, dt.datetime.now(dt.timezone.utc))
        after = main.query(bq, "", since=dt.datetime.now(dt.timezone.utc) - main.WINDOW)
        new = after[before:]
        print(f"{dt.datetime.now():%H:%M:%S} job ran: {len(new)} new finding(s)"
              + "".join(f"\n  {r['flag']}={r['variant']} v{r['version']} {r['signal']}: {r['action']}" for r in new))
        if not a.every:
            return
        time.sleep(a.every)


if __name__ == "__main__":
    main_()
