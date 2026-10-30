"""Run every sandbox scenario many times through the real job and write the key metrics.

Usage: python3 benchmark.py [--seeds 30] [--out ../docs/results.json] [--only crash tiny_crash]
Starts its own sandbox on a free port; takes a few minutes.
"""
import argparse
import datetime as dt
import json
import pathlib
import statistics
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import server

HERE = pathlib.Path(__file__).resolve().parent
# name -> (simulate.py scenario, signal that should be found, should it be killed, OS the breakdown should name)
EXPECT = {
    "healthy": ("healthy", None, False, None),
    "crash": ("crash", "FATAL", True, None),
    "anr": ("anr", "ANR", True, None),
    "nonfatal": ("nonfatal", "NON_FATAL", False, None),
    "android14": ("android14", "FATAL", True, "Android 14"),
    # Small rollout seen over several days: a batch of users per "day", the job after each one.
    "tiny_healthy": ("healthy", None, False, None),
    "tiny_crash": ("crash", "FATAL", True, None),
}
# Each "day" sends a batch of users, then runs the job: evidence builds up as it does in production.
# Regular scenarios: up to 2 days of --users. tiny_*: up to 5 days of 600 users (300 per variant,
# under min_users for any single day).
DAYS = {"tiny": (5, 600)}


def sh(*args):
    subprocess.run([sys.executable, *args], cwd=HERE, check=True, capture_output=True)


def unit_tests():
    job = HERE.parent / "job"
    out = "".join(subprocess.run([sys.executable, t], cwd=job, capture_output=True, text=True, check=True).stdout
                  for t in ("test_detect.py", "test_adapters.py"))
    return sum(line.startswith("ok ") for line in out.splitlines())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--users", type=int, default=3000)
    ap.add_argument("--out", default=str(HERE.parent / "docs" / "results.json"))
    ap.add_argument("--only", nargs="*", help="run just these scenarios")
    a = ap.parse_args()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_port}"

    scenarios = {}
    for name, (scenario, signal, should_kill, hot_os) in EXPECT.items():
        if a.only and name not in a.only:
            continue
        s = dict(runs=0, detected=0, detected_day1=0, false_alerts=0, correct_action=0, breakdown_right=0,
                 breakdown_noise=0, ratios=[], job_ms=[])
        days = []
        looks, users = DAYS.get(name.split("_")[0], (2, a.users))
        for seed in range(1, a.seeds + 1):
            server.reset()
            for day in range(1, looks + 1):
                sh("simulate.py", scenario, "--url", url, "--seed", str(seed if day == 1 else seed * 100 + day), "--users", str(users))
                t = time.perf_counter()
                sh("run_job.py", "--url", url, "--auto-kill")
                s["job_ms"].append((time.perf_counter() - t) * 1000)
                if any(r["signal"] == signal for r in server.state["audit"]):
                    days.append(day)
                    break
            rows = server.state["audit"]
            s["runs"] += 1
            hit = [r for r in rows if r["signal"] == signal]
            s["false_alerts"] += len(rows) > len(hit)
            if hit:
                r = hit[0]
                s["detected"] += 1
                s["ratios"].append(r["crash_rate"] / r["control_rate"] if r["control_rate"] else None)
                s["correct_action"] += (r["action"] == "killed") == should_kill
                segs = [g["segment"] for g in json.loads(r["segments"] or "[]")]
                if hot_os:
                    s["breakdown_right"] += bool(segs) and segs[0] == hot_os
                    s["breakdown_noise"] += any(g.startswith("iOS") or g.startswith("iPhone") for g in segs)
                else:
                    s["breakdown_noise"] += bool(segs)
        ratios = [x for x in s.pop("ratios") if x]
        s["median_ratio"] = round(statistics.median(ratios), 1) if ratios else None
        s["median_job_ms"] = round(statistics.median(s.pop("job_ms")))
        s["detected_day1"] = days.count(1)
        s["median_day"] = statistics.median(days) if days else None
        scenarios[name] = s
        print(name, s, flush=True)

    out = dict(
        generated=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        seeds=a.seeds, users_per_run=a.users, unit_tests=unit_tests(), scenarios=scenarios,
    )
    pathlib.Path(a.out).write_text(json.dumps(out, indent=2) + "\n")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
