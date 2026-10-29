"""Signal counts from Sentry, in the same row shape as the BigQuery query:
affected users per (flag, variant, release, signal) from error events, and
active users from app-start spans.

Env: SENTRY_URL (default https://sentry.io), SENTRY_ORG, SENTRY_PROJECT_ID,
     SENTRY_AUTH_TOKEN (read-only scopes: org:read, event:read).
App: tag ff_<flag>=<variant> on the Sentry scope, and sample app-start traces at 100%.
"""
import os

import requests

import detect

# Check these against your Sentry data in staging: SDK versions name ANRs/hangs differently.
ANR = "error.type:[ApplicationNotResponding,AppHang]"
SIGNAL_QUERIES = {
    "FATAL": f"error.unhandled:true !{ANR}",
    "ANR": ANR,
    "NON_FATAL": f"error.handled:true !{ANR}",
}
USERS_QUERY = "span.op:[app.start.cold,app.start.warm]"
COUNT = "count_unique(user)"  # mobile SDKs default user.id to an install id


def fetch(dataset, query, tag, start, end):
    """Every row of one events query, following Sentry's Link-header pagination."""
    url = f"{os.environ.get('SENTRY_URL', 'https://sentry.io')}/api/0/organizations/{os.environ['SENTRY_ORG']}/events/"
    params = {
        "dataset": dataset,
        "query": f"has:{tag} {query}",
        "field": [tag, "release", COUNT],
        "project": os.environ["SENTRY_PROJECT_ID"],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "per_page": 100,
    }
    headers = {"Authorization": f"Bearer {os.environ['SENTRY_AUTH_TOKEN']}"}
    rows = []
    while url:
        r = requests.get(url, params=params, headers=headers, timeout=60)
        r.raise_for_status()
        rows += r.json()["data"]
        nxt = r.links.get("next", {})
        url, params = (nxt["url"], None) if nxt.get("results") == "true" else (None, None)
    return rows


def parse(rows, flag):
    """Sentry rows -> {(flag, variant, version): users}."""
    tag = "ff_" + flag
    return {(flag, r[tag], r["release"]): int(r[COUNT]) for r in rows if r.get(tag)}


def merge(users, crashes):
    """One row per user group per signal, 0 where a signal had no events.
    users: {(flag, variant, version): n}; crashes: {signal: {(flag, variant, version): n}}."""
    return [
        dict(flag=f, variant=v, version=ver, signal=s, users=n, crashed_users=crashes.get(s, {}).get((f, v, ver), 0))
        for (f, v, ver), n in users.items()
        for s in detect.SIGNALS
    ]


def counts(flags, start, end):
    users, crashes = {}, {s: {} for s in SIGNAL_QUERIES}
    for flag in flags:
        tag = "ff_" + flag
        users.update(parse(fetch("spans", USERS_QUERY, tag, start, end), flag))
        for signal, query in SIGNAL_QUERIES.items():
            crashes[signal].update(parse(fetch("errors", query, tag, start, end), flag))
    return merge(users, crashes)
