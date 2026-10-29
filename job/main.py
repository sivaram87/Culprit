"""Flag Guard job: crash rate per flag variant -> Slack alert -> optional kill switch.

Env:
  GCP_PROJECT        Firebase / GCP project id
  CRASHLYTICS_TABLE  project.firebase_crashlytics.<app>_REALTIME (batch table for long backtests)
  ANALYTICS_DATASET  project.analytics_<id>
  AUDIT_TABLE        project.flagguard.actions
  SLACK_WEBHOOK      mounted from Secret Manager
  AUTO_KILL=1        allow kills for flags with "auto_kill": true (default: alert only)
  MIN_USERS, MIN_CRASHES, MIN_RATIO, ALPHA   optional threshold overrides (staging)

Usage: python main.py [--dry-run] [--backtest DAYS]
"""
import argparse
import dataclasses
import datetime as dt
import json
import os
import pathlib

import google.auth
import requests
from google.auth.transport.requests import AuthorizedSession
from google.cloud import bigquery

import detect

RC_URL = "https://firebaseremoteconfig.googleapis.com/v1/projects/{}/remoteConfig"
MAX_KILLS_PER_DAY = 1
WINDOW = dt.timedelta(hours=24)

# Crashing users and active users per (flag, variant, version). Both sides tag
# the same key: Crashlytics custom key / Analytics user property "ff_<flag>".
SQL = """
WITH crashes AS (
  SELECT SUBSTR(k.key, 4) AS flag, k.value AS variant,
         application.display_version AS version,
         COUNT(DISTINCT installation_uuid) AS crashed_users
  FROM `{crash_table}`, UNNEST(custom_keys) AS k
  WHERE is_fatal AND STARTS_WITH(k.key, 'ff_')
    AND event_timestamp BETWEEN @start AND @end
  GROUP BY 1, 2, 3
), users AS (
  SELECT SUBSTR(p.key, 4) AS flag, p.value.string_value AS variant,
         app_info.version AS version,
         COUNT(DISTINCT user_pseudo_id) AS users
  FROM `{analytics}.events_*`, UNNEST(user_properties) AS p
  WHERE _TABLE_SUFFIX >= FORMAT_DATE('%Y%m%d', DATE(@start))  -- also matches intraday_*
    AND STARTS_WITH(p.key, 'ff_')
    AND event_timestamp BETWEEN UNIX_MICROS(@start) AND UNIX_MICROS(@end)
  GROUP BY 1, 2, 3
)
SELECT flag, variant, version, users, IFNULL(crashed_users, 0) AS crashed_users
FROM users LEFT JOIN crashes USING (flag, variant, version)
"""


def query(bq, sql, **params):
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter(k, "TIMESTAMP", v) for k, v in params.items()]
    )
    return [dict(r) for r in bq.query(sql, job_config=cfg).result()]


def counts(bq, end):
    sql = SQL.format(crash_table=os.environ["CRASHLYTICS_TABLE"], analytics=os.environ["ANALYTICS_DATASET"])
    return query(bq, sql, start=end - WINDOW, end=end)


def thresholds():
    return {k: type(v)(os.environ[k.upper()]) for k, v in detect.DEFAULTS.items() if k.upper() in os.environ}


def kill(flag):
    """Set killswitch_<flag>=true in Remote Config. ETag guards against clobbering a concurrent edit."""
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/firebase.remoteconfig"])
    s = AuthorizedSession(creds)
    url = RC_URL.format(os.environ["GCP_PROJECT"])
    r = s.get(url, timeout=30)
    r.raise_for_status()
    if detect.is_killed(r.json(), flag):
        return "already_killed"
    new = detect.kill_template(r.json(), flag)  # raises if anything else would change
    s.put(
        url, json=new, timeout=30,
        headers={"If-Match": r.headers["ETag"], "Content-Type": "application/json; UTF8"},
    ).raise_for_status()
    return "killed"


def slack(f, action):
    next_step = {
        "killed": f"Undo: set `killswitch_{f.flag}` to false in Remote Config.",
        "already_killed": "Kill switch was already on.",
        "kill_capped": f"Daily auto-kill cap reached. Set `killswitch_{f.flag}` to true by hand if needed.",
        "alerted": f"Alert only. To stop it, set `killswitch_{f.flag}` to true in Remote Config.",
    }[action]
    text = (
        f":rotating_light: *{f.flag}={f.variant}* on v{f.version}: crash rate {f.crash_rate:.2%} "
        f"vs control {f.control_rate:.2%} ({f.crashed_users}/{f.users} users, p={f.p:.1e}). "
        f"Action: *{action}*. {next_step}"
    )
    requests.post(os.environ["SLACK_WEBHOOK"], json={"text": text}, timeout=10).raise_for_status()


def audit(bq, f, action, now):
    errors = bq.insert_rows_json(
        os.environ["AUDIT_TABLE"], [{"ts": now.isoformat(), **dataclasses.asdict(f), "action": action}]
    )
    if errors:
        raise RuntimeError(errors)


def run(bq, flags, now):
    findings = detect.detect(counts(bq, now), flags, **thresholds())
    if not findings:
        return
    recent = query(
        bq, f"SELECT flag, variant, version, action FROM `{os.environ['AUDIT_TABLE']}` WHERE ts > @since",
        since=now - WINDOW,
    )
    seen = {(a["flag"], a["variant"], a["version"]) for a in recent}
    kills = sum(a["action"] == "killed" for a in recent)
    for f in findings:
        if (f.flag, f.variant, f.version) in seen:
            continue  # already alerted in the last 24h
        action = "alerted"
        if os.environ.get("AUTO_KILL") == "1" and flags[f.flag].get("auto_kill"):
            action = kill(f.flag) if kills < MAX_KILLS_PER_DAY else "kill_capped"
            kills += action == "killed"
        slack(f, action)  # Slack before audit: a lost audit row means a repeat alert, not a missed one
        audit(bq, f, action, now)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print findings, no Slack/audit/kill")
    ap.add_argument("--backtest", type=int, metavar="DAYS", help="dry-run one 24h window per past day")
    args = ap.parse_args()

    flags = json.loads((pathlib.Path(__file__).parent / "flags.json").read_text())
    bq = bigquery.Client(project=os.environ["GCP_PROJECT"])
    now = dt.datetime.now(dt.timezone.utc)

    if args.dry_run or args.backtest:
        for i in range(args.backtest or 1):
            end = now - i * WINDOW
            for f in detect.detect(counts(bq, end), flags, **thresholds()):
                print(end.date(), f)
        return
    run(bq, flags, now)


if __name__ == "__main__":
    main()
