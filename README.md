<img src="docs/icon.svg" width="80" alt="Culprit icon">

# Culprit

**Find the feature-flag variant behind a mobile crash, ANR or error spike, and switch it off automatically.**

Your crash tool shows that crashes, ANRs or non-fatal errors went up. Culprit shows *why*: "variant `on` of `new_checkout` crashes 6× more than control on v5.2". It posts that to Slack and, if you've allowed it, turns the variant off with a kill switch, all within about 15 minutes and without anyone being woken up to compare dashboards.

**Website:** https://sivaram87.github.io/Culprit/ has an interactive detector demo and a setup guide for your stack. The [story behind Culprit](https://sivaram87.github.io/Culprit/story.html) explains why it exists, its pros and cons, and the roadmap.

Works with **Crashlytics or Sentry** for crash data, and **Firebase Remote Config or Statsig** for flags, in any combination.

```
:rotating_light: new_checkout=on on v5.2: crash rate 1.20% vs control 0.20%
(120/10000 users, p=1.1e-17). Action: killed.
Undo: turn `killswitch_new_checkout` off in Statsig.
Concentrated in: Android 14 (62% vs 20% control) · Samsung SM-S918B (41% vs 9% control).
```

Every finding is also logged to BigQuery, and a [Looker Studio or Grafana dashboard](#dashboard) shows them over time.

---

## Contents

- [Why](#why)
- [How it works](#how-it-works)
- [Supported tools](#supported-tools)
- [Repository layout](#repository-layout)
- [Try it locally](#try-it-locally)
- [Requirements](#requirements)
- [Setup](#setup)
  - [1. Prepare your crash data source](#1-prepare-your-crash-data-source)
  - [2. Add the app wrapper](#2-add-the-app-wrapper)
  - [3. Create kill switches](#3-create-kill-switches)
  - [4. Configure watched flags](#4-configure-watched-flags)
  - [5. Backtest](#5-backtest)
  - [6. Deploy](#6-deploy)
  - [7. Turn on auto-kill](#7-turn-on-auto-kill)
- [Configuration reference](#configuration-reference)
- [Detection rules](#detection-rules)
- [Slack actions](#slack-actions)
- [Undoing a kill](#undoing-a-kill)
- [Dashboard](#dashboard)
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

Crash tools handle step 1. Culprit handles steps 2 to 4.

## How it works

```
 ┌──────────────── App (iOS / Android) ────────────────┐
 │ Flags.variant("new_checkout")                        │
 │   ├─ killswitch_new_checkout on → control value      │
 │   └─ else → value from Remote Config / Statsig       │
 │ Flags.tag() → ff_new_checkout=on on every crash      │
 │               and session (Crashlytics or Sentry)    │
 └──────────────────────────┬──────────────────────────┘
                            │ BigQuery export or Sentry API
                            ▼
 ┌────────── Culprit job (Cloud Run, every 15 min) ─────────┐
 │ 1. Count the last 24h: affected users and active users   │
 │    per (flag, variant, app version, signal)              │
 │    signal = FATAL | ANR | NON_FATAL                      │
 │ 2. detect(): compare each variant with control           │
 │ 3. Skip anything already reported in the last 24h        │
 │ 4. Optionally turn on killswitch_<flag>                  │
 │    (Remote Config parameter or Statsig gate)             │
 │ 5. Find where the errors concentrate (OS, device)        │
 │ 6. Post to Slack                                         │
 │ 7. Write an audit row to BigQuery                        │
 └──────────────────────────────────────────────────────────┘
```

**Why a separate kill switch?** Editing an experiment's targeting automatically is risky, and Firebase A/B Testing has no public API for stopping an experiment. A separate boolean, `killswitch_<flag>`, that the app checks first works with any flag tool, can only ever send users to the control (known-good) path, and is easy to undo.

## Supported tools

Two settings choose the tools. Any combination works.

| Setting | Value | Crash data / kill target |
|---|---|---|
| `SIGNAL_SOURCE` | `crashlytics` (default) | Crashlytics + Google Analytics, through the BigQuery export |
| | `sentry` | Sentry error events + app-start traces, through the Sentry API |
| `FLAG_TOOL` | `firebase` (default) | Firebase Remote Config parameter `killswitch_<flag>` |
| | `statsig` | Statsig feature gate `killswitch_<flag>` |

Whatever the tools, the job runs on Google Cloud (Cloud Run, Cloud Scheduler) and keeps its audit log in BigQuery.

## Repository layout

```
client/
  ios/Flags.swift          Vendor-neutral flag wrapper: kill-switch check + tagging
  ios/FlagsTests.swift     XCTest: kill switch serves control
  android/Flags.kt         Same wrapper for Android
  android/FlagsTest.kt     JUnit: kill switch serves control
grafana/
  culprit.json           Importable Grafana dashboard (BigQuery plugin)
sandbox/
  server.py                Local stand-in for flag tool, crash data, Slack and audit table
  simulate.py              Fake app traffic with a chosen bug
  run_job.py               Runs the real job against the sandbox
sample/ios/                SwiftUI demo app using Flags.swift (sandbox or Firebase)
job/
  detect.py                Detection, kill policy and safety checks, no side effects
  main.py                  Crashlytics/BigQuery counts, Remote Config kill, Slack, audit, CLI
  source_sentry.py         Sentry counts
  kill_statsig.py          Statsig kill
  dashboard.sql            BigQuery views behind the dashboards
  flags.json               Watched flags: control variant + auto_kill opt-in
  test_detect.py           Detector tests (plain asserts, no framework)
  test_adapters.py         Sentry, Statsig and dispatch tests (no network)
  deploy.sh                One-shot GCP setup and deploy
  Dockerfile, requirements.txt
```

## Try it locally

The sandbox runs the whole loop on your Mac with no accounts: a fake app population, the real detection and kill logic, a fake Slack and a live page to watch. It needs only Python 3.

```bash
python3 sandbox/server.py
```

Open http://localhost:8787/, then in a second terminal:

```bash
cd sandbox && python3 simulate.py crash && python3 run_job.py --auto-kill
```

Within a few seconds the page shows the Slack alert, the audit row, and `killswitch_new_checkout` turned on. New simulated users now get control.

| Scenario | Expect |
|---|---|
| `healthy` | No alert |
| `crash` | FATAL alert (killed with `--auto-kill`) |
| `anr` | ANR alert (killed with `--auto-kill`) |
| `nonfatal` | NON_FATAL alert, never a kill |
| `android14` | FATAL alert, breakdown names Android 14 |

Use **Turn off (undo kill)** or **Reset everything** on the page between runs. `run_job.py --every 30` repeats the job like the scheduler does, and running it twice shows that an alert isn't repeated within 24 hours.

**What the sandbox covers:** `job/main.py` `run()` and everything in `detect.py`: thresholds, the z-test, dedupe, kill policy, breakdown, Slack text and audit rows. **What it doesn't:** the BigQuery SQL, the Sentry queries, and the real Remote Config and Statsig writes. The sandbox's `/counts` and `/segments` return rows in the same shape those queries do. Test the real ones in a [staging project](#testing).

### Sample iOS app

`sample/ios` is a small SwiftUI shop ("Bean There") that wires `client/ios/Flags.swift` the way a real app should. The `on` variant of `new_checkout` is deliberately broken:

| Button | Signal |
|---|---|
| Pay $24 with one tap | Crash (FATAL) |
| Apply coupon | Main thread blocked 6 s (ANR / app hang) |
| Save card | Handled error (NON_FATAL) |

With the sandbox running, open `sample/ios/CulpritDemo.xcodeproj` in Xcode and run it on any iPhone simulator. The simulator reaches the sandbox at `localhost`. To use another address, set the `SANDBOX_URL` environment variable in the scheme. Like a crash SDK, the app saves a crash to disk and uploads it on the next launch.

To see the full loop: tap **Become a new user** until `new_checkout` shows `on`, then crash it with **Pay**, and reopen the app. Then run `python3 simulate.py crash && python3 run_job.py --auto-kill`, and tap **Refresh flags**: the kill switch reads `on`, the app serves `off`, and checkout is the safe classic one.

Where to look in the code:
- `CulpritDemoApp.swift`: `Backend.start()` runs before any UI reads a flag. Checkout reads `Flags.variant("new_checkout")` and nothing else.
- `Backend.swift`: the three hooks (`value`, `isKilled`, `tagSinks`) and `Flags.tag()` after every refresh. It has two versions. The Firebase one (Remote Config, Crashlytics, Analytics) compiles automatically once you add the Firebase packages; otherwise the sandbox one is used.

## Requirements

Always:
- A Google Cloud project with billing enabled (Cloud Run, Cloud Scheduler, Secret Manager, BigQuery for the audit log)
- `gcloud` and `bq` CLIs, logged in as a project owner (needed once, for deploy)
- Python 3.12 to run locally
- A Slack [incoming webhook](https://api.slack.com/messaging/webhooks)

Per tool:

| Tool | You need |
|---|---|
| Crashlytics | Crashlytics and Google Analytics for Firebase in the app, with BigQuery export on |
| Sentry | Sentry SDK with performance tracing, app-start traces sampled at 100%, and an auth token with `org:read` and `event:read` only |
| Firebase Remote Config | Remote Config in the app, in the same project the job deploys to |
| Statsig | Statsig SDK in the app, and a Statsig Console API key |

## Setup

### 1. Prepare your crash data source

**Crashlytics:** in the Firebase console, open **Project settings → Integrations → BigQuery** and enable:
- **Crashlytics** with streaming export turned on. This creates `firebase_crashlytics.<app>_REALTIME`.
- **Google Analytics** with streaming (intraday) export turned on. This creates `analytics_<id>.events_intraday_*`.

Wait until data appears (up to 24h for the first daily tables).

**Sentry:** Sentry can't break its session counts down by custom tags, so Culprit counts active users from **app-start traces**, which carry the same `ff_*` tags as errors. Sample them at 100%: set `tracesSampleRate = 1.0`, or use a `tracesSampler` that returns `1.0` for the first screen-load transaction (op `ui.load`), which holds the app-start spans. Sentry's mobile SDKs use an install ID as `user.id` by default, so counts are distinct installs, the same unit as Crashlytics.

Create an auth token (**Settings → Auth Tokens**) with **only** `org:read` and `event:read`, and note your organization slug and numeric project ID.

### 2. Add the app wrapper

Copy `client/ios/Flags.swift` or `client/android/Flags.kt` into your app. The wrapper has no vendor imports. You connect it to your tools once at startup through three hooks:

| Hook | What it does |
|---|---|
| `value(name)` | Returns the flag's current variant as a string |
| `isKilled(name)` | Returns whether `killswitch_<name>` is on |
| `tagSinks` | Receive `("ff_<name>", variant)` and attach it to crashes and sessions |

**Flag tool: Firebase Remote Config**
```swift
Flags.value = { RemoteConfig.remoteConfig()[$0].stringValue }
Flags.isKilled = { RemoteConfig.remoteConfig()["killswitch_\($0)"].boolValue }
```
```kotlin
Flags.value = { Firebase.remoteConfig.getString(it) }
Flags.isKilled = { Firebase.remoteConfig.getBoolean("killswitch_$it") }
```

**Flag tool: Statsig** (feature gates; for experiments, return the group name your Statsig SDK exposes)
```swift
Flags.value = { Statsig.checkGate($0) ? "on" : "off" }
Flags.isKilled = { Statsig.checkGate("killswitch_\($0)") }
```
```kotlin
Flags.value = { if (Statsig.checkGate(it)) "on" else "off" }
Flags.isKilled = { Statsig.checkGate("killswitch_$it") }
```

**Crash data: Crashlytics** (tags crashes and Analytics users)
```swift
Flags.tagSinks = [{ key, value in
    Crashlytics.crashlytics().setCustomValue(value, forKey: key)
    Analytics.setUserProperty(value, forName: key)
}]
```
```kotlin
Flags.tagSinks = listOf { key, value ->
    Firebase.crashlytics.setCustomKey(key, value)
    Firebase.analytics.setUserProperty(key, value)
}
```

**Crash data: Sentry** (tags errors and traces)
```swift
Flags.tagSinks = [{ key, value in SentrySDK.configureScope { $0.setTag(value: value, key: key) } }]
```
```kotlin
Flags.tagSinks = listOf { key, value -> Sentry.setTag(key, value) }
```

Then:

1. Put each flag you want watched into `Flags.watched`, with its **control** value:
   ```swift
   static let watched: [String: String] = ["new_checkout": "off"]
   ```
2. Read watched flags **only** through the wrapper:
   ```swift
   if Flags.variant("new_checkout") == "on" { showNewCheckout() }
   ```
3. Call `Flags.tag()` **as early as possible at launch** (Remote Config and Statsig both serve cached values right away), and again after every flag refresh:
   ```swift
   Flags.tag()
   RemoteConfig.remoteConfig().fetchAndActivate { status, _ in
       if status != .error { Flags.tag() }
   }
   ```
   ```kotlin
   Flags.tag()
   Firebase.remoteConfig.fetchAndActivate().addOnSuccessListener { Flags.tag() }
   ```
   With Sentry this matters even more: the app-start trace only counts a user for a variant if the tag was set before the trace finished.

Rules:
- Flag names use only **lowercase letters, digits and `_`**, and are **21 characters or fewer**. Analytics limits user property names to 24 characters including the `ff_` prefix. The job refuses any other name before it touches a kill switch.
- With Crashlytics, watch about **10 flags at most**. Analytics allows 25 user properties per project, and you probably use some already.
- The tag records the variant the user *actually gets*, so after a kill those users count as control and the variant's crash rate stops rising.

### 3. Create kill switches

**Firebase Remote Config:** for each watched flag, add a parameter:

| Key | Type | Default |
|---|---|---|
| `killswitch_new_checkout` | Boolean | `false` |

Keep these parameters **outside parameter groups** (see [Limitations](#limitations)). If one is missing, the job adds it when it first kills that flag.

**Statsig:** for each watched flag, create a feature gate `killswitch_new_checkout` with one rule, **Everyone, 100% pass**, then **disable** the gate. A disabled gate returns `false` for everyone. When the job enables it, everyone gets `true` and the wrapper serves control. If the gate is missing, the job fails loudly and doesn't post the alert, so create the gates before deploying.

If a fetch fails, the app keeps its last values. If it has never fetched, the kill switch reads as off.

### 4. Configure watched flags

`job/flags.json` must list the same flags and control values as `Flags.watched` in the app:

```json
{
  "new_checkout": {"control": "off", "auto_kill": false}
}
```

Changes to this file go through code review. That review is the approval step for letting a flag be killed automatically.

### 5. Backtest

Before deploying, check what Culprit would have flagged over the last 30 days. Neither `--backtest` nor `--dry-run` (last 24h only) posts to Slack, writes the audit table or touches a kill switch.

```bash
cd job
pip install -r requirements.txt
gcloud auth application-default login
export GCP_PROJECT=my-proj

# Crashlytics: use the batch table for long backtests (the realtime one keeps less history)
export CRASHLYTICS_TABLE=my-proj.firebase_crashlytics.com_example_app_ANDROID
export ANALYTICS_DATASET=my-proj.analytics_123456

# or Sentry:
# export SIGNAL_SOURCE=sentry SENTRY_ORG=acme SENTRY_PROJECT_ID=42 SENTRY_AUTH_TOKEN=...

python main.py --backtest 30
```

Each output line is a variant that would have triggered an alert. Every one should match an incident you remember. If not, raise the thresholds (see [Configuration reference](#configuration-reference)). Backtesting with Sentry is limited by your plan's event retention.

### 6. Deploy

```bash
cd job

# Crashlytics + Remote Config
PROJECT=my-proj \
CRASHLYTICS_TABLE=my-proj.firebase_crashlytics.com_example_app_ANDROID_REALTIME \
ANALYTICS_DATASET=analytics_123456 \
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/... \
./deploy.sh

# Sentry + Statsig
PROJECT=my-proj SIGNAL_SOURCE=sentry FLAG_TOOL=statsig \
SENTRY_ORG=acme SENTRY_PROJECT_ID=42 SENTRY_AUTH_TOKEN=... \
STATSIG_CONSOLE_KEY=... \
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/... \
./deploy.sh
```

Secrets (`SLACK_WEBHOOK_URL`, `SENTRY_AUTH_TOKEN`, `STATSIG_CONSOLE_KEY`) are only needed on the first deploy or to rotate them. They go straight into Secret Manager.

`deploy.sh` is safe to re-run. Re-running it is also how you upgrade: it adds the `signal` and `segments` columns to an audit table created by an older version. It sets up:

- the service account `culprit@<project>.iam.gserviceaccount.com`
- the dataset and table `culprit.actions`, which is the audit log
- the dashboard views `culprit.findings`, `culprit.daily` and `culprit.flags_30d`, plus a printed link that creates the [dashboard](#dashboard)
- IAM roles for the chosen tools only (see [Security](#security))
- secrets `culprit-slack`, plus `culprit-sentry` and/or `culprit-statsig` when used
- the Cloud Run job `culprit`, built from `Dockerfile`, in **alert-only** mode
- the Cloud Scheduler job `culprit-every-15m`

Optional: set `REGION`, which defaults to `us-central1`.

To deploy a second app (iOS and Android are separate Crashlytics tables or Sentry projects), deploy again with different values.

> ⚠️ A second deploy into the same Google Cloud project currently reuses the job name `culprit`. Rename the job in `deploy.sh` to run both side by side.

### 7. Turn on auto-kill

After roughly two weeks of alerts with no false alarms:

1. Set `"auto_kill": true` for **one** flag in `flags.json` and merge it.
2. Redeploy with `AUTO_KILL=1 ./deploy.sh ...`.

Both are required. `AUTO_KILL=1` alone does nothing for flags without `auto_kill: true`.

Only crash (`FATAL`) and `ANR` findings can kill a flag. `NON_FATAL` findings always alert only, because non-fatals are whatever the app chooses to log, which makes them too easy to trigger by accident or with spoofed reports.

## Configuration reference

Environment variables read by the job:

| Variable | Needed for | Description |
|---|---|---|
| `SIGNAL_SOURCE` | — | `crashlytics` (default) or `sentry` |
| `FLAG_TOOL` | — | `firebase` (default) or `statsig` |
| `GCP_PROJECT` | always | GCP project id. Also the Firebase project for Crashlytics and Remote Config. |
| `AUDIT_TABLE` | job | `project.culprit.actions` |
| `SLACK_WEBHOOK` | job | Mounted from Secret Manager by `deploy.sh` |
| `CRASHLYTICS_TABLE` | Crashlytics | `project.firebase_crashlytics.<app>_REALTIME` |
| `ANALYTICS_DATASET` | Crashlytics | `project.analytics_<id>` |
| `SENTRY_URL` | Sentry | Default `https://sentry.io`. Set it for the EU region or self-hosted Sentry. |
| `SENTRY_ORG` | Sentry | Organization slug |
| `SENTRY_PROJECT_ID` | Sentry | Numeric project ID |
| `SENTRY_AUTH_TOKEN` | Sentry | Read-only token, mounted from Secret Manager |
| `STATSIG_CONSOLE_KEY` | Statsig | Console API key, mounted from Secret Manager |
| `AUTO_KILL` | — | `1` allows kills. Anything else means alert only (default). |
| `MIN_USERS` | — | Minimum users in both variant and control (default `500`) |
| `MIN_CRASHES` | — | Minimum affected users in the variant (default `10`) |
| `MIN_RATIO` | — | Variant rate must be at least this × control (default `2.0`) |
| `ALPHA` | — | Significance level before Bonferroni correction (default `0.001`) |

Use the `MIN_*` overrides in a staging project, where traffic is small.

Constants in `main.py`:

| Constant | Value | Meaning |
|---|---|---|
| `WINDOW` | 24h | Rolling window for counts and alert de-duplication |
| `MAX_KILLS_PER_DAY` | 1 | Automatic kills allowed per 24h; further findings only alert |

## Detection rules

For each watched flag, **app version** and **signal**, every non-control variant is compared with control.

| Signal | Meaning | Crashlytics | Sentry | Can auto-kill |
|---|---|---|---|---|
| `FATAL` | App crashed | `error_type = FATAL` | `error.unhandled:true`, excluding ANRs and hangs | yes |
| `ANR` | App froze | `error_type = ANR` (Android only) | `error.type:[ApplicationNotResponding,AppHang]` | yes |
| `NON_FATAL` | Error the app recorded itself | `error_type = NON_FATAL` | `error.handled:true`, excluding ANRs and hangs | no, alert only |

The Sentry filters live in `SIGNAL_QUERIES` in `job/source_sentry.py`. Sentry SDK versions name ANRs and hangs differently, so check them against your own data in staging.

A variant is flagged only when **all** of these hold:

| Check | Default | Why |
|---|---|---|
| Users in variant and control | ≥ 500 each | Small samples are noise |
| Affected users in variant | ≥ 10 | One bad device shouldn't trigger an alert |
| Variant rate ÷ control rate | ≥ 2× | Statistically significant but trivial differences aren't worth a page |
| One-sided two-proportion z-test | p < `ALPHA` ÷ number of comparisons | Bonferroni correction: 20 flags checked every run shouldn't mean 20 chances of a false alarm |

Details:
- Each signal is compared on its own: an ANR regression is flagged even when the crash rate is flat.
- Every (flag, variant, version) gets a row for each signal even when the count is zero, so a control group with no ANRs still counts as a control.
- Units are **distinct affected installs** over **distinct active installs**. With Crashlytics those are `installation_uuid` and `user_pseudo_id`. With Sentry both are `count_unique(user)`, with active users taken from app-start traces. The two numbers come from different data, so the detector caps affected users at the user count.
- Breaking results down by version (Sentry: by release) means a crash that exists only in v5.2 isn't hidden by healthy older versions.
- The control variant is never flagged.
- A (flag, variant, version, signal) already reported in the last 24h isn't reported again.

### Where it happens: OS and device breakdown

Each new alert also says where the variant's errors concentrate. Detection itself doesn't change, so this adds no false alarms.

- For the finding's flag, version and signal, the job counts affected users by **OS major version** (`Android 14.0.1` and `14.1` both count as `Android 14`) and by **device model**, for the variant and for control.
- It compares **shares**. "Android 14 is 62% of the variant's affected users but 20% of control's" means the problem is concentrated there. Shares need no active-user counts, so this works the same for Crashlytics and Sentry.
- The top 3 segments whose share is at least 10 points higher than control's are shown, and only when the gap is statistically clear (one-sided z-test, p < 0.05 split across the segments compared). When control has only a few errors, "35% vs 0%" on one device is often chance, so the line says "No OS or device stands out" rather than naming the wrong one. If control has no errors at all (common for ANRs), there is nothing to test against, and the variant's biggest segments are shown.
- The breakdown runs after any kill, so it never delays one. If its query fails, the alert still goes out with "Breakdown unavailable."
- The result is stored as JSON in the audit table's `segments` column.

## Slack actions

Every alert carries one action:

| Action | Meaning |
|---|---|
| `alerted` | Alert only: auto-kill is off, the flag isn't opted in, or the signal is `NON_FATAL` |
| `killed` | `killswitch_<flag>` was turned on |
| `already_killed` | The kill switch was already on |
| `kill_capped` | The daily auto-kill cap was reached, so it's alert only. Kill by hand if needed. |

## Undoing a kill

- **Remote Config:** set `killswitch_<flag>` back to `false` and publish, or use **Remote Config → Change history** to roll back to the version before the kill.
- **Statsig:** disable the gate `killswitch_<flag>` again. Statsig's audit log shows when the job enabled it.

Find the cause before you undo. The detector will flag the variant again once its rate climbs back.

## Dashboard

Culprit's history (every finding and kill) can be shown in **Looker Studio** or **Grafana**. Both read the same BigQuery views directly, so there's nothing extra to host.

**Views** (created by `deploy.sh` from `job/dashboard.sql`):

| View | One row per | Use it for |
|---|---|---|
| `culprit.findings` | finding | The full list, with `rate_ratio` (variant ÷ control) and `top_segment` |
| `culprit.daily` | day × signal × action | Trends: findings and kills over time |
| `culprit.flags_30d` | flag | Which flags misbehave most: alerts, kills, last alert, last action |

The audit table doesn't record when someone undoes a kill, so "last action" shows what Culprit did, not the switch's current state.

### Looker Studio

1. Open the link `deploy.sh` prints at the end. It creates a report named "Culprit" with all three views connected.
2. For each data source, choose **Viewer's credentials** (Resource → Manage added data sources → Edit). Each viewer then needs their own BigQuery access, so the dashboard never shows data to someone who couldn't query it.
3. Add four charts:
   - **Findings per day:** stacked bar from `daily`, dimension `day`, breakdown `signal`, metric `findings`
   - **Kills over time:** line from `daily`, filtered to `action = killed`
   - **Recent findings:** table from `findings`, sorted by `ts` descending, showing flag, variant, version, signal, crash_rate, control_rate, rate_ratio, top_segment and action
   - **Flags, last 30 days:** table from `flags_30d`, sorted by `alerts`
4. **Give your team access:** grant `roles/bigquery.dataViewer` on the `culprit` dataset and `roles/bigquery.jobUser` on the project, then share the report.

### Grafana

`grafana/culprit.json` is a ready-made dashboard: three totals (findings, kills, flags affected), findings per day stacked by signal, kills per day, a table of recent findings with kills highlighted, and a 30-day per-flag table. All panels except the 30-day table follow Grafana's time picker. It needs **Grafana 11.6 or newer** and the official BigQuery plugin.

**1. Install the plugin**

```bash
grafana cli plugins install grafana-bigquery-datasource
```

Or install "Google BigQuery" from **Administration → Plugins**. On Grafana Cloud it's already available.

**2. Give Grafana read-only access.** Grafana only needs to read the `culprit` dataset. Pick the first option that fits where Grafana runs:

| Grafana runs on | Auth method in the data source | What to grant |
|---|---|---|
| A Google Compute Engine VM | GCE Default Service Account | The two roles below, to the VM's service account. No key file. |
| Grafana Cloud | Workload Identity Federation or Forward OAuth Identity | The two roles below, to each user (or their group). Queries run as the signed-in user. |
| Anywhere else | Google JWT File | The two roles below, to a dedicated service account with a key |

The two roles: `roles/bigquery.dataViewer` on the `culprit` dataset, and `roles/bigquery.jobUser` on the project. For a dedicated service account:

```bash
gcloud iam service-accounts create culprit-grafana --project my-proj
bq add-iam-policy-binding --member serviceAccount:culprit-grafana@my-proj.iam.gserviceaccount.com \
  --role roles/bigquery.dataViewer my-proj:culprit
gcloud projects add-iam-policy-binding my-proj --condition None \
  --member serviceAccount:culprit-grafana@my-proj.iam.gserviceaccount.com --role roles/bigquery.jobUser
```

Only if you need the JWT option, create a key and keep it out of this repo. Grafana stores it encrypted.

```bash
gcloud iam service-accounts keys create culprit-grafana-key.json \
  --iam-account culprit-grafana@my-proj.iam.gserviceaccount.com
```

The account can read only aggregate counts in one dataset. It can't touch kill switches, crash data or the job.

**3. Add the data source.** In **Connections → Data sources → Add → Google BigQuery**, pick the auth method and set **Default project** to your project. Because access is granted on the dataset only, the plugin's dataset drop-down may look empty. The dashboard's queries use full table names, so that's fine. To provision it as code instead:

```yaml
apiVersion: 1
datasources:
  - name: BigQuery (Culprit)
    type: grafana-bigquery-datasource
    jsonData:
      authenticationType: jwt          # or gce
      clientEmail: culprit-grafana@my-proj.iam.gserviceaccount.com
      defaultProject: my-proj
      tokenUri: https://oauth2.googleapis.com/token
      privateKeyPath: /etc/grafana/secrets/culprit-grafana-key.json
```

**4. Import the dashboard.** In **Dashboards → New → Import**, upload `grafana/culprit.json`. Then pick the BigQuery data source and enter your GCP project id when asked. Or import it through the API:

```bash
jq -n --slurpfile d grafana/culprit.json --arg ds "$DATASOURCE_UID" --arg project my-proj '{
  dashboard: $d[0], overwrite: true,
  inputs: [
    {name: "DS_BIGQUERY", type: "datasource", pluginId: "grafana-bigquery-datasource", value: $ds},
    {name: "VAR_PROJECT", type: "constant", value: $project}
  ]}' |
curl -sS -X POST "$GRAFANA_URL/api/dashboards/import" \
  -H "Authorization: Bearer $GRAFANA_TOKEN" -H "Content-Type: application/json" --data @-
```

The dashboard file holds no project id or credentials, so it's safe to commit as is. If you change the views in `job/dashboard.sql`, run `python3 job/test_adapters.py`: it fails if the dashboard reads a view that no longer exists.

## Testing

All tests run without network access or cloud libraries:

```bash
cd job && python3 test_detect.py && python3 test_adapters.py
```

`test_detect.py` covers:
- a clear regression is flagged
- small samples and equal rates are ignored
- only the regressing version is flagged
- a missing control or an unwatched flag is skipped
- the Bonferroni correction suppresses a marginal result among 20 flags
- the control variant is never returned
- an ANR regression is flagged even when crashes are flat, including against a zero-ANR control
- each signal produces its own finding
- kill policy: non-fatals never kill, the daily cap applies, and both opt-ins are required
- OS versions are reduced to their major version
- the breakdown finds a concentrated OS, stays empty when the mix matches control, and handles a control with no errors
- bad data (crashes greater than users) doesn't crash the detector
- `kill_template` changes only `killswitch_<flag>`, and the safety check rejects any other edit

`test_adapters.py` covers:
- Sentry results are merged with a zero for every signal that had no events
- Sentry rows are parsed into the detector's format and produce findings
- Sentry paging follows the `Link` header to the end
- flag names that could inject into a URL or key are rejected
- the Statsig kill only ever writes `.../gates/killswitch_<flag>/enable`, and reports `already_killed`
- all four combinations of crash source and flag tool reach the right code, and unknown values are refused
- the Sentry breakdown queries the finding's release and signal, grouped by `os` and then `device`
- the breakdown line reaches Slack and the audit row, and a failed breakdown still sends the alert
- the Grafana dashboard only reads views that `dashboard.sql` creates, uses the import-time data source and project, and applies the time picker to findings queries

**App wrappers:** `FlagsTests.swift` / `FlagsTest.kt` check that the kill switch serves control. Add them to your app's test target. Set the `@testable import` module name to match your app.

**End to end (staging project):** this tests what the sandbox can't: the BigQuery SQL, the exports, and the real kill switch.

1. **Firebase project.** Create a separate staging project in the Firebase console and add the iOS app with bundle id `dev.culprit.demo` (or your own app).
2. **Exports.** In **Project settings → Integrations → BigQuery**, link the project and turn on export for **Crashlytics** (with streaming) and **Analytics** (daily plus streaming). Tables appear after the first events, which can take up to a day for Analytics.
3. **Remote Config.** Add a condition `new_checkout_50` (user in random percentile ≤ 50) and these parameters:

   | Key | Type | Default | `new_checkout_50` |
   |---|---|---|---|
   | `new_checkout` | String | `off` | `on` |
   | `killswitch_new_checkout` | Boolean | `false` | |

4. **Sample app on Firebase.** In Xcode, add the package `https://github.com/firebase/firebase-ios-sdk` with the products FirebaseAnalytics, FirebaseCrashlytics and FirebaseRemoteConfig, then add your `GoogleService-Info.plist` to the CulpritDemo target. The app switches to its Firebase backend on its own; the debug panel shows `Backend: Firebase`. Run it **without the debugger attached** when you crash it, or Crashlytics won't see the crash.
5. **Job.** In `job/flags.json`, set `"auto_kill": true` on `new_checkout`. Deploy to the staging project with lower thresholds:
   ```bash
   cd job && MIN_USERS=20 MIN_CRASHES=3 AUTO_KILL=1 ./deploy.sh
   ```
6. **Traffic.** Run the app on several simulators or test devices. Erase a simulator between runs to get a new install and a new random percentile. Get at least 20 users on each variant and crash at least 3 `on` users.
7. **Check.** Within about 30 minutes, expect a Slack alert and `killswitch_new_checkout` turned on in Remote Config. After the next fetch, the app shows `off` and the crashes stop. `python3 main.py --dry-run` against the staging project prints the same finding.

Crashlytics doesn't report iOS app hangs (its ANR type is Android-only), so test the ANR path with Sentry's app-hang tracking or an Android build. With Sentry, also check that `python main.py --dry-run` counts match Sentry's own UI. That confirms the `SIGNAL_QUERIES` filters and that app-start traces carry the `ff_*` tags.

## Security

| Risk | Mitigation |
|---|---|
| The flag-tool credential can change any flag: the Remote Config role and the Statsig Console API key can't be narrowed further | The job only ever turns **on** `killswitch_<flag>`. Flag names are checked against `[a-z0-9_]+` before use. Remote Config writes go through `check_only_kill`, which refuses any other change, and use the ETag (`If-Match`) so a concurrent human edit isn't overwritten. The only Statsig write is `PUT .../gates/killswitch_<flag>/enable`. Both are covered by tests, and both tools keep a change history. |
| Fake crash reports: crash SDK keys ship in the app, so crash data can be forged to get a feature killed | A kill only sends users to the control path. Kills need ≥ 10 distinct affected installs plus a significant result, are capped at 1 per day, and every one is posted to Slack. Non-fatal errors, the easiest signal to fake, can never trigger a kill. |
| A bad detection kills a winning experiment | Alert-only is the default. Auto-kill is opt-in per flag through a reviewed PR, and must also be enabled globally with `AUTO_KILL=1`. |
| Secrets leak | The Slack webhook, Sentry token and Statsig key live only in Secret Manager, and each is mounted only when its tool is chosen. There are no service-account key files, because Cloud Run attaches the account. |
| Sentry token misuse | The token needs only `org:read` and `event:read`. It can read error data but change nothing. |
| PII | Tags contain only flag name and variant. Slack messages, audit rows and dashboard views contain only aggregate counts and OS/device names, never user IDs. |
| Dashboard over-sharing | Looker Studio data sources use viewer's credentials, so viewers need their own BigQuery read access on the `culprit` dataset. Sharing the report doesn't share the data. |
| Grafana's BigQuery access | Read-only: `bigquery.dataViewer` on the `culprit` dataset plus `bigquery.jobUser`. Auth methods without keys (GCE, workload identity, forwarded OAuth) are preferred. A key, if needed, lives only in Grafana's encrypted storage. The dashboard JSON contains no project id or credentials. |
| Over-broad access | Always: `bigquery.jobUser`, `bigquery.dataEditor` on the audit table only, `secretAccessor` on its own secrets, and `run.invoker` on this job only. Crashlytics adds `bigquery.dataViewer` on the two export datasets. Remote Config adds `firebaseremoteconfig.admin`. |
| Supply chain | Three pinned dependencies: `google-cloud-bigquery`, `google-auth`, `requests`. |

## Limitations

- **Sentry needs app-start traces at 100%.** Without them, active users per variant are undercounted and rates are inflated. This can cost Sentry quota.
- **The Sentry filters for ANRs and hangs need checking against your SDK version.** They live in one dict in `source_sentry.py`.
- **ANRs from Crashlytics are Android-only.** Crashlytics doesn't report iOS hangs as ANRs. Sentry reports iOS app hangs, which count as `ANR`.
- **Statsig support covers feature gates.** For experiments, `Flags.value` must return the group name, and you need a kill-switch gate per experiment.
- **One set of thresholds for all signals.** Non-fatal rates are usually much higher than crash rates. That's fine for a ratio test, but noisy apps may want separate thresholds later.
- **Remote Config kill-switch parameters must not be inside a parameter group.** If one is, the API rejects the update and nothing changes; the job fails loudly.
- **The flag list is kept in two places** (`Flags.watched` and `flags.json`) and must match.
- **Fixed thresholds plus a z-test.** Simple and predictable, but slower to react than sequential testing on low-traffic flags.
- **The breakdown compares shares, not rates.** It shows where the variant's errors concentrate relative to control, not the crash rate on each device, which would need active users per device.
- **Sentry's `os` and `device` tags** are what the breakdown groups by. Check in staging that they hold what you expect for your SDK version.
- **One app per job.** iOS and Android need separate deploys.
- **Switching tools doesn't remove old access.** Redeploying with a different tool doesn't revoke the previous tool's IAM role or delete its secret. Remove those by hand.

## Roadmap

These will be added when there's a need:

- More tools: LaunchDarkly or Unleash for flags, Bugsnag or Embrace for crashes. Each is one `counts` or `kill` function plus a branch in `main.py`.
- Sequential testing (mSPRT) for faster, safer detection
- Detection per OS version, for regressions too small to move the overall rate
- Recording undos and false alarms in the audit table, so the dashboard can show current state and precision

## Author

Created by [Sivaram Jebamani](https://github.com/sivaram87).
