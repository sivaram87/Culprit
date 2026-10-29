# Flag Guard

**Find the feature-flag variant behind a mobile crash, ANR or error spike, and switch it off automatically.**

Crashlytics shows that crashes, ANRs or non-fatal errors went up. Flag Guard shows *why*: "variant `on` of `new_checkout` crashes 6× more than control on v5.2". It posts that to Slack and, if you've allowed it, turns the variant off with a kill switch, all within about 15 minutes and without anyone being woken up to compare dashboards.

```
:rotating_light: new_checkout=on on v5.2: crash rate 1.20% vs control 0.20%
(120/10000 users, p=1.1e-17). Action: killed.
Undo: set `killswitch_new_checkout` to false in Remote Config.
```

---

## Contents

- [Why](#why)
- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Setup](#setup)
  - [1. Turn on BigQuery export](#1-turn-on-bigquery-export)
  - [2. Add the app wrapper](#2-add-the-app-wrapper)
  - [3. Create kill-switch parameters](#3-create-kill-switch-parameters)
  - [4. Configure watched flags](#4-configure-watched-flags)
  - [5. Backtest](#5-backtest)
  - [6. Deploy](#6-deploy)
  - [7. Turn on auto-kill](#7-turn-on-auto-kill)
- [Configuration reference](#configuration-reference)
- [Detection rules](#detection-rules)
- [Slack actions](#slack-actions)
- [Undoing a kill](#undoing-a-kill)
- [Testing](#testing)
- [Security](#security)
- [Limitations](#limitations)
- [Roadmap](#roadmap)

---

## Why

Mobile releases can't be rolled back. Once a build is in the store, the only fast lever you have is a feature flag. When crashes spike, someone has to:

1. notice the spike,
2. work out which of a dozen running flags and experiments caused it,
3. check whether it affects every app version or only one,
4. flip the right flag.

Crash tools handle step 1. Flag Guard handles steps 2 to 4.

## How it works

```
 ┌──────────── App (iOS / Android) ────────────┐
 │ Flags.variant("new_checkout")                │
 │   ├─ killswitch_new_checkout == true → control
 │   └─ else → Remote Config value              │
 │ Flags.tag() → Crashlytics key   ff_new_checkout=on
 │             → Analytics property ff_new_checkout=on
 └──────────────────────┬──────────────────────┘
                        │ BigQuery export (streaming)
                        ▼
 ┌──────── Flag Guard job (Cloud Run, every 15 min) ────────┐
 │ 1. Query the last 24h: affected users and active users   │
 │    per (flag, variant, app version, signal)              │
 │    signal = FATAL | ANR | NON_FATAL                      │
 │ 2. detect(): compare each variant with control           │
 │ 3. Skip anything already reported in the last 24h        │
 │ 4. Optionally set killswitch_<flag>=true in Remote Config│
 │ 5. Post to Slack                                         │
 │ 6. Write an audit row to BigQuery                        │
 └──────────────────────────────────────────────────────────┘
```

**Why a separate kill-switch parameter?** Firebase A/B Testing has no public API for stopping an experiment, and editing an experiment's targeting automatically is risky. A separate boolean, `killswitch_<flag>`, that the app checks first works with any flag provider, can only ever send users to the control (known-good) path, and is easy to undo.

## Repository layout

```
client/
  ios/Flags.swift          Flag wrapper: kill-switch check + crash/session tagging
  ios/FlagsTests.swift     XCTest: kill switch serves control
  android/Flags.kt         Same wrapper for Android
  android/FlagsTest.kt     JUnit: kill switch serves control
job/
  detect.py                Detection + Remote Config safety logic, no side effects
  main.py                  BigQuery queries, Slack, Remote Config, audit, CLI
  flags.json               Watched flags: control variant + auto_kill opt-in
  test_detect.py           Unit tests (plain asserts, no framework)
  deploy.sh                One-shot GCP setup and deploy
  Dockerfile, requirements.txt
```

## Requirements

- A Firebase project using **Crashlytics**, **Google Analytics for Firebase** and **Remote Config**
- A Google Cloud project with billing enabled (for BigQuery, Cloud Run and Cloud Scheduler)
- `gcloud` and `bq` CLIs, logged in as a project owner (needed once, for deploy)
- Python 3.12 to run locally
- A Slack [incoming webhook](https://api.slack.com/messaging/webhooks)
- Firebase SDKs: iOS 10+ / Android BoM 32.5+ (they provide the `Firebase.*` Kotlin accessors used here)

## Setup

### 1. Turn on BigQuery export

In the Firebase console, open **Project settings → Integrations → BigQuery** and enable:

- **Crashlytics** with streaming export turned on. This creates `firebase_crashlytics.<app>_REALTIME`.
- **Google Analytics** with streaming (intraday) export turned on. This creates `analytics_<id>.events_intraday_*`.

Wait until data appears (up to 24h for the first daily tables).

### 2. Add the app wrapper

Copy `client/ios/Flags.swift` or `client/android/Flags.kt` into your app. Then:

1. Put each flag you want watched into `Flags.watched`, with its **control** value:
   ```swift
   static let watched: [String: String] = ["new_checkout": "off"]
   ```
2. Read watched flags **only** through the wrapper:
   ```swift
   if Flags.variant("new_checkout") == "on" { showNewCheckout() }
   ```
   ```kotlin
   if (Flags.variant("new_checkout") == "on") showNewCheckout()
   ```
3. Call `Flags.tag()` after every successful Remote Config `activate()`:
   ```swift
   RemoteConfig.remoteConfig().fetchAndActivate { status, _ in
       if status != .error { Flags.tag() }
   }
   ```
   ```kotlin
   Firebase.remoteConfig.fetchAndActivate().addOnSuccessListener { Flags.tag() }
   ```

Rules:
- Flag names must be **21 characters or fewer**. The `ff_` prefix plus the name must fit Analytics' 24-character limit for user property names.
- Watch about **10 flags at most**. Analytics allows 25 user properties per project, and you probably use some already.
- The tag records the variant the user *actually gets*, so after a kill those users count as control and the variant's crash rate stops rising.

### 3. Create kill-switch parameters

For each watched flag, add a Remote Config parameter:

| Key | Type | Default |
|---|---|---|
| `killswitch_new_checkout` | Boolean | `false` |

Keep these parameters **outside parameter groups** (see [Limitations](#limitations)). If one is missing, the job adds it when it first kills that flag.

If a fetch fails, the app keeps its last activated values. If it has never fetched, the kill switch defaults to `false`.

### 4. Configure watched flags

`job/flags.json` must list the same flags and control values as `Flags.watched` in the app:

```json
{
  "new_checkout": {"control": "off", "auto_kill": false}
}
```

Changes to this file go through code review. That review is the approval step for letting a flag be killed automatically.

### 5. Backtest

Before deploying, check what Flag Guard would have flagged over the last 30 days. Use the **batch** Crashlytics table for long backtests, because the realtime table keeps a shorter history.

```bash
cd job
pip install -r requirements.txt
gcloud auth application-default login
export GCP_PROJECT=my-proj
export CRASHLYTICS_TABLE=my-proj.firebase_crashlytics.com_example_app_ANDROID
export ANALYTICS_DATASET=my-proj.analytics_123456
python main.py --backtest 30
```

Each output line is a variant that would have triggered an alert. Every one should match an incident you remember. If not, raise the thresholds (see [Configuration reference](#configuration-reference)).

`python main.py --dry-run` checks only the last 24 hours. Neither mode posts to Slack, writes to the audit table or touches Remote Config.

### 6. Deploy

```bash
cd job
PROJECT=my-proj \
CRASHLYTICS_TABLE=my-proj.firebase_crashlytics.com_example_app_ANDROID_REALTIME \
ANALYTICS_DATASET=analytics_123456 \
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/... \
./deploy.sh
```

`deploy.sh` is safe to re-run. Re-running it is also how you upgrade: it adds the `signal` column to an audit table created by an older version. It sets up:

- the service account `flagguard@<project>.iam.gserviceaccount.com`
- the dataset and table `flagguard.actions`, which is the audit log
- IAM roles (see [Security](#security))
- the secret `flagguard-slack` in Secret Manager
- the Cloud Run job `flagguard`, built from `Dockerfile`, in **alert-only** mode
- the Cloud Scheduler job `flagguard-every-15m`

Optional: set `REGION`, which defaults to `us-central1`.

To deploy a second app (iOS and Android are separate Crashlytics tables), deploy again with different values.

> ⚠️ Note: a second deploy into the same project currently reuses the job name `flagguard`. Rename the job in `deploy.sh` to run both side by side.

### 7. Turn on auto-kill

After roughly two weeks of alerts with no false alarms:

1. Set `"auto_kill": true` for **one** flag in `flags.json` and merge it.
2. Redeploy with `AUTO_KILL=1 ./deploy.sh ...`.

Both are required. `AUTO_KILL=1` alone does nothing for flags without `auto_kill: true`.

Only crash (`FATAL`) and `ANR` findings can kill a flag. `NON_FATAL` findings always alert only, because non-fatals are whatever the app chooses to log, which makes them too easy to trigger by accident or with spoofed reports.

## Configuration reference

Environment variables read by `main.py`:

| Variable | Required | Description |
|---|---|---|
| `GCP_PROJECT` | yes | Firebase / GCP project id |
| `CRASHLYTICS_TABLE` | yes | `project.firebase_crashlytics.<app>_REALTIME` |
| `ANALYTICS_DATASET` | yes | `project.analytics_<id>` |
| `AUDIT_TABLE` | job only | `project.flagguard.actions` |
| `SLACK_WEBHOOK` | job only | Mounted from Secret Manager by `deploy.sh` |
| `AUTO_KILL` | no | `1` allows kills. Anything else means alert only (default). |
| `MIN_USERS` | no | Minimum users in both variant and control (default `500`) |
| `MIN_CRASHES` | no | Minimum crashing users in the variant (default `10`) |
| `MIN_RATIO` | no | Variant crash rate must be at least this × control (default `2.0`) |
| `ALPHA` | no | Significance level before Bonferroni correction (default `0.001`) |

Use the `MIN_*` overrides in a staging project, where traffic is small.

Constants in `main.py`:

| Constant | Value | Meaning |
|---|---|---|
| `WINDOW` | 24h | Rolling window for counts and alert de-duplication |
| `MAX_KILLS_PER_DAY` | 1 | Automatic kills allowed per 24h; further findings only alert |

## Detection rules

For each watched flag, **app version** and **signal**, every non-control variant is compared with control. The signal comes from Crashlytics' `error_type`:

| Signal | Meaning | Can auto-kill |
|---|---|---|
| `FATAL` | App crashed | yes |
| `ANR` | App froze for 5s or more (Android only) | yes |
| `NON_FATAL` | Error recorded with `recordError` / `recordException` | no, alert only | A variant is flagged only when **all** of these hold:

| Check | Default | Why |
|---|---|---|
| Users in variant and control | ≥ 500 each | Small samples are noise |
| Affected users in variant | ≥ 10 | One bad device shouldn't trigger an alert |
| Variant rate ÷ control rate | ≥ 2× | Statistically significant but trivial differences aren't worth a page |
| One-sided two-proportion z-test | p < `ALPHA` ÷ number of comparisons | Bonferroni correction: 20 flags checked every run shouldn't mean 20 chances of a false alarm |

Details:
- Each signal is compared on its own: an ANR regression is flagged even when the crash rate is flat.
- Every (flag, version) gets a row for each signal even when the count is zero, so a control group with no ANRs still counts as a control.
- Units are **distinct affected installs** (`installation_uuid`) over **distinct active installs** (`user_pseudo_id`). The two come from separate exports, so the detector caps crashes at the user count.
- Breaking results down by version means a crash that exists only in v5.2 isn't hidden by healthy older versions.
- The control variant is never flagged.
- A (flag, variant, version, signal) already reported in the last 24h isn't reported again.

## Slack actions

Every alert carries one action:

| Action | Meaning |
|---|---|
| `alerted` | Alert only: auto-kill is off, the flag isn't opted in, or the signal is `NON_FATAL` |
| `killed` | `killswitch_<flag>` was set to `true` |
| `already_killed` | The kill switch was already on |
| `kill_capped` | The daily auto-kill cap was reached, so it's alert only. Kill by hand if needed. |

## Undoing a kill

In the Firebase console, open Remote Config and set `killswitch_<flag>` back to `false`, then publish. Alternatively, use **Remote Config → Change history** to roll back to the version before the kill.

Find the cause before you undo. The detector will flag the variant again once its crash rate climbs back.

## Testing

**Detector and safety logic** (no cloud access needed):

```bash
cd job && python3 test_detect.py
```

Covers:
- a clear regression is flagged
- small samples and equal rates are ignored
- only the regressing version is flagged
- a missing control or an unwatched flag is skipped
- the Bonferroni correction suppresses a marginal result among 20 flags
- the control variant is never returned
- an ANR regression is flagged even when crashes are flat, including against a zero-ANR control
- each signal produces its own finding
- kill policy: non-fatals never kill, the daily cap applies, and both opt-ins are required
- bad data (crashes greater than users) doesn't crash the detector
- `kill_template` changes only `killswitch_<flag>`
- the safety check rejects any other edit

**App wrappers:** `FlagsTests.swift` / `FlagsTest.kt` check that the kill switch serves control. Add them to your app's test target. Set the `@testable import` module name to match your app.

**End to end (staging Firebase project):**
1. Add a watched flag `crash_demo` whose variant `b` crashes on purpose.
2. Deploy with `MIN_USERS=20 MIN_CRASHES=3` and `AUTO_KILL=1`, with `"auto_kill": true` on `crash_demo`.
3. Roll `b` out to test devices and trigger the crash.
4. Expect a Slack alert and `killswitch_crash_demo=true` within about 30 minutes. After the next fetch, devices serve control and the crashes stop.

## Security

| Risk | Mitigation |
|---|---|
| The service account can edit any Remote Config parameter (the role can't be scoped further) | `check_only_kill` refuses to write anything except `killswitch_<flag>=true`. The check runs on every write and is covered by tests. Writes use the ETag (`If-Match`), so a concurrent human edit isn't overwritten. Remote Config history allows one-click rollback. |
| Fake crash reports: the Firebase API key ships in the app, so crash data can be forged to get a feature killed | A kill only sends users to the control path. Kills need ≥ 10 distinct affected installs plus a significant result, are capped at 1 per day, and every one is posted to Slack. Non-fatal errors, the easiest signal to fake, can never trigger a kill. |
| A bad detection kills a winning experiment | Alert-only is the default. Auto-kill is opt-in per flag through a reviewed PR, and must also be enabled globally with `AUTO_KILL=1`. |
| Secrets leak | The Slack webhook lives only in Secret Manager. There are no service-account key files, because Cloud Run attaches the account. |
| PII | Crash keys and user properties contain only flag name and variant. Slack messages and audit rows contain only aggregate counts. |
| Over-broad access | `bigquery.dataViewer` on the two export datasets, `bigquery.jobUser`, `bigquery.dataEditor` on the audit table only, `firebaseremoteconfig.admin`, `secretAccessor` on one secret, and `run.invoker` on this job only. |
| Supply chain | Three pinned dependencies: `google-cloud-bigquery`, `google-auth`, `requests`. |

## Limitations

- **ANRs are Android-only.** Crashlytics doesn't report iOS hangs as ANRs, so iOS ANR counts are always zero and never trigger.
- **One set of thresholds for all signals.** Non-fatal rates are usually much higher than crash rates. That's fine for a ratio test, but noisy apps may want separate thresholds later.
- **Kill-switch parameters must not be inside a Remote Config parameter group.** If one is, the API rejects the update and nothing changes; the job fails loudly.
- **The flag list is kept in two places** (`Flags.watched` and `flags.json`) and must match.
- **Fixed thresholds plus a z-test.** Simple and predictable, but slower to react than sequential testing on low-traffic flags.
- **Firebase only.** Using Sentry or Statsig means replacing the SQL query and the `kill()` call in `main.py`.
- **One app per job.** iOS and Android need separate deploys.

## Roadmap

These will be added when there's a need:

- Sentry and Statsig adapters
- Sequential testing (mSPRT) for faster, safer detection
- Breakdown by device and OS
- A dashboard on top of the audit table
