"""Send fake app traffic to the sandbox, the way real installs would.

Each user fetches /config, gets the variant the kill switch allows, tags it as ff_<flag>,
reports a session, and sometimes an error. The scenario picks what's broken.

Usage: python3 simulate.py [scenario] [--users 3000] [--url http://localhost:8787]
Scenarios:
  healthy   both variants behave the same (expect no alert)
  crash     "on" crashes 10x more than control (expect a FATAL alert)
  anr       "on" freezes 10x more (expect an ANR alert)
  nonfatal  "on" logs 10x more handled errors (expect an alert, never a kill)
  android14 like crash, but only on one OS version (expect the breakdown to name it)
"""
import argparse
import json
import random
import urllib.request

BASE = {"FATAL": 0.002, "ANR": 0.002, "NON_FATAL": 0.01}
OSES = ["iOS 18.5", "iOS 17.6", "Android 14", "Android 13"]
DEVICES = {"iOS": ["iPhone 15", "iPhone 13"], "Android": ["Pixel 8", "Galaxy S23"]}


def rates(scenario, variant, os):
    r = dict(BASE)
    if variant == "on":
        if scenario in ("crash", "anr", "nonfatal"):
            signal = {"crash": "FATAL", "anr": "ANR", "nonfatal": "NON_FATAL"}[scenario]
            r[signal] *= 10
        if scenario == "android14" and os == "Android 14":
            r["FATAL"] = 0.06
    return r


def call(url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", default="crash",
                    choices=["healthy", "crash", "anr", "nonfatal", "android14"])
    ap.add_argument("--users", type=int, default=3000)
    ap.add_argument("--version", default="5.2")
    ap.add_argument("--url", default="http://localhost:8787")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    rnd = random.Random(a.seed)

    events, counts = [], {}
    for i in range(a.users):
        user = f"sim-{a.scenario}-{rnd.getrandbits(48):x}"
        cfg = call(f"{a.url}/config?user={user}")
        killed = cfg["killswitch_new_checkout"]
        variant = "off" if killed else cfg["new_checkout"]  # what Flags.variant() would return
        os = rnd.choice(OSES)
        device = rnd.choice(DEVICES[os.split()[0]])
        base = dict(user=user, version=a.version, os=os, device=device, tags={"ff_new_checkout": variant})
        events.append({**base, "type": "session"})
        for signal, p in rates(a.scenario, variant, os).items():
            if rnd.random() < p:
                events.append({**base, "type": signal})
                counts[(variant, signal)] = counts.get((variant, signal), 0) + 1
        if len(events) >= 500:
            call(f"{a.url}/events", events)
            events = []
    if events:
        call(f"{a.url}/events", events)
    print(f"{a.users} users sent ({a.scenario}). Errors by (variant, signal): {dict(sorted(counts.items()))}")


if __name__ == "__main__":
    main()
