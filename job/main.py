"""Flag Guard job: crash rate per flag variant -> Slack alert -> optional kill switch.

Env:
  SIGNAL_SOURCE      crashlytics (default) | sentry   -- see source_sentry.py for Sentry env
  FLAG_TOOL          firebase (default) | statsig     -- see kill_statsig.py for Statsig env
  GCP_PROJECT        GCP project id (audit table; also Firebase project for crashlytics/firebase)
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
import kill_statsig
import source_sentry

RC_URL = "https://firebaseremoteconfig.googleapis.com/v1/projects/{}/remoteConfig"
MAX_KILLS_PER_DAY = 1
WINDOW = dt.timedelta(hours=24)

# Affected users and active users per (flag, variant, version, signal). Both sides tag
# the same key: Crashlytics custom key / Analytics user property "ff_<flag>".
SQL = """
WITH crashes AS (
  SELECT SUBSTR(k.key, 4) AS flag, k.value AS variant,
         application.display_version AS version, error_type AS signal,
         COUNT(DISTINCT installation_uuid) AS crashed_users
  FROM `{crash_table}`, UNNEST(custom_keys) AS k
  WHERE STARTS_WITH(k.key, 'ff_')
    AND event_timestamp BETWEEN @start AND @end
  GROUP BY 1, 2, 3, 4
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
-- Cross join so a group with zero ANRs still gets a row (else its control looks "missing").
SELECT flag, variant, version, signal, users, IFNULL(crashed_users, 0) AS crashed_users
FROM users CROSS JOIN UNNEST({signals}) AS signal
LEFT JOIN crashes USING (flag, variant, version, signal)
"""


# Affected installs for one finding, split by OS and by device, for the variant and control.
SEGMENTS_SQL = """
WITH e AS (
  SELECT k.value AS variant, installation_uuid,
         CONCAT(IFNULL(operating_system.name, ''), ' ', IFNULL(operating_system.display_version, '')) AS os,
         TRIM(CONCAT(IFNULL(device.manufacturer, ''), ' ', IFNULL(device.model, ''))) AS device
  FROM `{crash_table}`, UNNEST(custom_keys) AS k
  WHERE k.key = @tag AND k.value IN (@variant, @control)
    AND application.display_version = @version AND error_type = @signal
    AND event_timestamp BETWEEN @start AND @end
)
SELECT variant, 'os' AS dim, os AS segment, COUNT(DISTINCT installation_uuid) AS crashed_users FROM e GROUP BY 1, 3
UNION ALL
SELECT variant, 'device', device, COUNT(DISTINCT installation_uuid) FROM e GROUP BY 1, 3
"""


def query(bq, sql, **params):
    kind = lambda v: "TIMESTAMP" if isinstance(v, dt.datetime) else "STRING"
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter(k, kind(v), v) for k, v in params.items()]
    )
    return [dict(r) for r in bq.query(sql, job_config=cfg).result()]


def counts_bigquery(bq, end):
    sql = SQL.format(
        crash_table=os.environ["CRASHLYTICS_TABLE"],
        analytics=os.environ["ANALYTICS_DATASET"],
        signals=list(detect.SIGNALS),  # Python list repr is a valid BigQuery string array
    )
    return query(bq, sql, start=end - WINDOW, end=end)


def counts(bq, flags, end):
    source = os.environ.get("SIGNAL_SOURCE", "crashlytics")
    if source == "crashlytics":
        return counts_bigquery(bq, end)
    if source == "sentry":
        return source_sentry.counts(flags, end - WINDOW, end)
    raise ValueError(f"unknown SIGNAL_SOURCE {source!r}")


def segments_bigquery(bq, f, control, end):
    sql = SEGMENTS_SQL.format(crash_table=os.environ["CRASHLYTICS_TABLE"])
    return query(
        bq, sql, tag="ff_" + f.flag, variant=f.variant, control=control,
        version=f.version, signal=f.signal, start=end - WINDOW, end=end,
    )


def breakdown(bq, f, flags, end):
    """Top OS/device segments for a finding, or None if the lookup fails.
    Optional context: it must never block an alert or a kill."""
    control = flags[f.flag]["control"]
    try:
        source = os.environ.get("SIGNAL_SOURCE", "crashlytics")
        if source == "sentry":
            rows = source_sentry.segments(f, control, end - WINDOW, end)
        else:
            rows = segments_bigquery(bq, f, control, end)
        return detect.concentration(rows, f.variant, control)
    except Exception as e:  # noqa: BLE001
        print(f"breakdown failed for {f.flag}={f.variant}: {e!r}")
        return None


def kill(flag):
    tool = os.environ.get("FLAG_TOOL", "firebase")
    if tool == "firebase":
        return kill_remote_config(flag)
    if tool == "statsig":
        return kill_statsig.kill(flag)
    raise ValueError(f"unknown FLAG_TOOL {tool!r}")


def thresholds():
    return {k: type(v)(os.environ[k.upper()]) for k, v in detect.DEFAULTS.items() if k.upper() in os.environ}


def kill_remote_config(flag):
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


def slack(f, action, segments):
    where = {"firebase": "Remote Config", "statsig": "Statsig"}.get(os.environ.get("FLAG_TOOL", "firebase"))
    switch = f"`killswitch_{f.flag}`"
    next_step = {
        "killed": f"Undo: turn {switch} off in {where}.",
        "already_killed": "Kill switch was already on.",
        "kill_capped": f"Daily auto-kill cap reached. Turn {switch} on in {where} by hand if needed.",
        "alerted": f"Alert only. To stop it, turn {switch} on in {where}.",
    }[action]
    what = {"FATAL": "crash", "ANR": "ANR", "NON_FATAL": "non-fatal error"}[f.signal]
    text = (
        f":rotating_light: *{f.flag}={f.variant}* on v{f.version}: {what} rate {f.crash_rate:.2%} "
        f"vs control {f.control_rate:.2%} ({f.crashed_users}/{f.users} users, p={f.p:.1e}). "
        f"Action: *{action}*. {next_step}\n{detect.describe(segments)}"
    )
    requests.post(os.environ["SLACK_WEBHOOK"], json={"text": text}, timeout=10).raise_for_status()


def audit(bq, f, action, now, segments):
    row = {
        "ts": now.isoformat(), **dataclasses.asdict(f), "action": action,
        "segments": None if segments is None else json.dumps(segments),
    }
    errors = bq.insert_rows_json(os.environ["AUDIT_TABLE"], [row])
    if errors:
        raise RuntimeError(errors)


def run(bq, flags, now):
    findings = detect.detect(counts(bq, flags, now), flags, **thresholds())
    if not findings:
        return
    recent = query(
        bq,
        f"SELECT flag, variant, version, signal, action FROM `{os.environ['AUDIT_TABLE']}` WHERE ts > @since",
        since=now - WINDOW,
    )
    seen = {(a["flag"], a["variant"], a["version"], a["signal"]) for a in recent}
    kills = sum(a["action"] == "killed" for a in recent)
    auto_kill = os.environ.get("AUTO_KILL") == "1"
    for f in findings:
        if (f.flag, f.variant, f.version, f.signal) in seen:
            continue  # already alerted in the last 24h
        action = detect.action_for(f, flags, auto_kill, kills, MAX_KILLS_PER_DAY)
        if action == "kill":
            action = kill(f.flag)
            kills += action == "killed"
        segments = breakdown(bq, f, flags, now)  # after the kill, so it never delays one
        slack(f, action, segments)  # Slack before audit: a lost audit row means a repeat alert, not a missed one
        audit(bq, f, action, now, segments)


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
            for f in detect.detect(counts(bq, flags, end), flags, **thresholds()):
                print(end.date(), f)
                print("   ", detect.describe(breakdown(bq, f, flags, end)))
        return
    run(bq, flags, now)


if __name__ == "__main__":
    main()
