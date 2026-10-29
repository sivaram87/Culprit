#!/usr/bin/env bash
# Deploy Flag Guard as a Cloud Run job, triggered every 15 min by Cloud Scheduler.
#
# Pick a signal source and a flag tool (any combination works):
#   SIGNAL_SOURCE=crashlytics  needs CRASHLYTICS_TABLE, ANALYTICS_DATASET (BigQuery export on)
#   SIGNAL_SOURCE=sentry       needs SENTRY_ORG, SENTRY_PROJECT_ID, SENTRY_AUTH_TOKEN (first deploy)
#                              optional SENTRY_URL (self-hosted / EU)
#   FLAG_TOOL=firebase         Remote Config in $PROJECT
#   FLAG_TOOL=statsig          needs STATSIG_CONSOLE_KEY (first deploy)
# Always: PROJECT, SLACK_WEBHOOK_URL (first deploy). Optional: REGION, AUTO_KILL=1.
#
# Example: PROJECT=my-proj SIGNAL_SOURCE=sentry FLAG_TOOL=statsig SENTRY_ORG=acme \
#          SENTRY_PROJECT_ID=42 SENTRY_AUTH_TOKEN=... STATSIG_CONSOLE_KEY=... SLACK_WEBHOOK_URL=... ./deploy.sh
set -euo pipefail

: "${PROJECT:?}"
SIGNAL_SOURCE=${SIGNAL_SOURCE:-crashlytics}
FLAG_TOOL=${FLAG_TOOL:-firebase}
REGION=${REGION:-us-central1}
SA=flagguard@$PROJECT.iam.gserviceaccount.com
AUDIT_TABLE=$PROJECT.flagguard.actions
cd "$(dirname "$0")"

# Store a secret in Secret Manager (create or add a version) when a value is given,
# grant the job read access, and add it to the job's mounted secrets.
SECRETS=()
secret() {  # secret <env name in job> <secret id> <value or empty>
  if [[ -n "$3" ]]; then
    printf %s "$3" | gcloud secrets create "$2" --project "$PROJECT" --data-file=- \
      || printf %s "$3" | gcloud secrets versions add "$2" --project "$PROJECT" --data-file=-
  fi
  gcloud secrets add-iam-policy-binding "$2" --project "$PROJECT" \
    --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor
  SECRETS+=("$1=$2:latest")
}

# Service account. No keys: Cloud Run attaches it directly.
gcloud iam service-accounts create flagguard --project "$PROJECT" --display-name "Flag Guard" || true

# Audit table (all combinations): run queries, write only this table.
bq --project_id "$PROJECT" mk --dataset flagguard || true
bq --project_id "$PROJECT" mk --table "$PROJECT:flagguard.actions" \
  ts:TIMESTAMP,flag:STRING,variant:STRING,version:STRING,signal:STRING,users:INTEGER,crashed_users:INTEGER,crash_rate:FLOAT,control_rate:FLOAT,p:FLOAT,action:STRING,segments:STRING || true
# Upgrade path for audit tables created by older versions.
bq --project_id "$PROJECT" query --use_legacy_sql=false \
  "ALTER TABLE \`$AUDIT_TABLE\` ADD COLUMN IF NOT EXISTS signal STRING, ADD COLUMN IF NOT EXISTS segments STRING"
# Dashboard views (read by Looker Studio).
sed "s/{project}/$PROJECT/g" dashboard.sql | bq --project_id "$PROJECT" query --use_legacy_sql=false
bq add-iam-policy-binding --member "serviceAccount:$SA" --role roles/bigquery.dataEditor "$PROJECT:flagguard.actions"
gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role roles/bigquery.jobUser --condition None

ENV="GCP_PROJECT=$PROJECT,AUDIT_TABLE=$AUDIT_TABLE,AUTO_KILL=${AUTO_KILL:-0},SIGNAL_SOURCE=$SIGNAL_SOURCE,FLAG_TOOL=$FLAG_TOOL"
secret SLACK_WEBHOOK flagguard-slack "${SLACK_WEBHOOK_URL:-}"

# Signal source: read-only access only.
case "$SIGNAL_SOURCE" in
  crashlytics)
    : "${CRASHLYTICS_TABLE:?}" "${ANALYTICS_DATASET:?}"
    CRASH_DATASET=${CRASHLYTICS_TABLE%.*}  # project.firebase_crashlytics
    for ds in "${CRASH_DATASET/./:}" "$PROJECT:$ANALYTICS_DATASET"; do
      bq add-iam-policy-binding --member "serviceAccount:$SA" --role roles/bigquery.dataViewer "$ds"
    done
    ENV+=",CRASHLYTICS_TABLE=$CRASHLYTICS_TABLE,ANALYTICS_DATASET=$PROJECT.$ANALYTICS_DATASET"
    ;;
  sentry)
    : "${SENTRY_ORG:?}" "${SENTRY_PROJECT_ID:?}"
    secret SENTRY_AUTH_TOKEN flagguard-sentry "${SENTRY_AUTH_TOKEN:-}"  # scopes: org:read, event:read
    ENV+=",SENTRY_ORG=$SENTRY_ORG,SENTRY_PROJECT_ID=$SENTRY_PROJECT_ID,SENTRY_URL=${SENTRY_URL:-https://sentry.io}"
    ;;
  *) echo "unknown SIGNAL_SOURCE=$SIGNAL_SOURCE" >&2; exit 1 ;;
esac

# Flag tool: the only write access the job has.
case "$FLAG_TOOL" in
  firebase)
    gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" \
      --role roles/firebaseremoteconfig.admin --condition None
    ;;
  statsig)
    secret STATSIG_CONSOLE_KEY flagguard-statsig "${STATSIG_CONSOLE_KEY:-}"
    ;;
  *) echo "unknown FLAG_TOOL=$FLAG_TOOL" >&2; exit 1 ;;
esac

# Job. AUTO_KILL stays off (alert only) until the backtest is clean; redeploy with AUTO_KILL=1 to enable.
gcloud run jobs deploy flagguard --project "$PROJECT" --region "$REGION" --source . \
  --service-account "$SA" --max-retries 0 \
  --set-env-vars "$ENV" \
  --set-secrets "$(IFS=,; echo "${SECRETS[*]}")"

gcloud run jobs add-iam-policy-binding flagguard --project "$PROJECT" --region "$REGION" \
  --member "serviceAccount:$SA" --role roles/run.invoker

gcloud scheduler jobs create http flagguard-every-15m --project "$PROJECT" --location "$REGION" \
  --schedule "*/15 * * * *" --http-method POST \
  --uri "https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/flagguard:run" \
  --oauth-service-account-email "$SA" || echo "scheduler job already exists"

# Looker Studio report with the three dashboard views connected. Opening it creates the report.
LS="https://lookerstudio.google.com/reporting/create?r.reportName=Flag%20Guard"
i=0
for view in findings daily flags_30d; do
  LS+="&ds.ds$i.connector=bigQuery&ds.ds$i.type=TABLE&ds.ds$i.projectId=$PROJECT&ds.ds$i.datasetId=flagguard&ds.ds$i.tableId=$view"
  i=$((i + 1))
done
echo
echo "Dashboard: open this link to create the Looker Studio report, then follow README 'Dashboard':"
echo "$LS"
