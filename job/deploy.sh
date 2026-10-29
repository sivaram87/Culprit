#!/usr/bin/env bash
# Deploy Flag Guard as a Cloud Run job, triggered every 15 min by Cloud Scheduler.
# Usage: PROJECT=my-proj CRASHLYTICS_TABLE=... ANALYTICS_DATASET=analytics_123 SLACK_WEBHOOK_URL=... ./deploy.sh
# Prereqs: Crashlytics + Analytics BigQuery export (streaming) enabled in Firebase.
set -euo pipefail

: "${PROJECT:?}" "${CRASHLYTICS_TABLE:?}" "${ANALYTICS_DATASET:?}"
REGION=${REGION:-us-central1}
SA=flagguard@$PROJECT.iam.gserviceaccount.com
CRASH_DATASET=${CRASHLYTICS_TABLE%.*}  # project.firebase_crashlytics
AUDIT_TABLE=$PROJECT.flagguard.actions
cd "$(dirname "$0")"

# Service account. No keys: Cloud Run attaches it directly.
gcloud iam service-accounts create flagguard --project "$PROJECT" --display-name "Flag Guard" || true

# Least privilege: read the two exports, run queries, write only the audit table, edit Remote Config.
bq --project_id "$PROJECT" mk --dataset flagguard || true
bq --project_id "$PROJECT" mk --table "$PROJECT:flagguard.actions" \
  ts:TIMESTAMP,flag:STRING,variant:STRING,version:STRING,signal:STRING,users:INTEGER,crashed_users:INTEGER,crash_rate:FLOAT,control_rate:FLOAT,p:FLOAT,action:STRING || true
# Upgrade path for audit tables created before ANR/non-fatal support.
bq --project_id "$PROJECT" query --use_legacy_sql=false \
  "ALTER TABLE \`$AUDIT_TABLE\` ADD COLUMN IF NOT EXISTS signal STRING"
for ds in "${CRASH_DATASET/./:}" "$PROJECT:$ANALYTICS_DATASET"; do
  bq add-iam-policy-binding --member "serviceAccount:$SA" --role roles/bigquery.dataViewer "$ds"
done
bq add-iam-policy-binding --member "serviceAccount:$SA" --role roles/bigquery.dataEditor "$PROJECT:flagguard.actions"
for role in roles/bigquery.jobUser roles/firebaseremoteconfig.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role "$role" --condition None
done

# Slack webhook lives in Secret Manager only.
if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
  printf %s "$SLACK_WEBHOOK_URL" | gcloud secrets create flagguard-slack --project "$PROJECT" --data-file=- \
    || printf %s "$SLACK_WEBHOOK_URL" | gcloud secrets versions add flagguard-slack --project "$PROJECT" --data-file=-
fi
gcloud secrets add-iam-policy-binding flagguard-slack --project "$PROJECT" \
  --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor

# Job. AUTO_KILL stays off (alert only) until the backtest is clean; redeploy with AUTO_KILL=1 to enable.
gcloud run jobs deploy flagguard --project "$PROJECT" --region "$REGION" --source . \
  --service-account "$SA" --max-retries 0 \
  --set-env-vars "GCP_PROJECT=$PROJECT,CRASHLYTICS_TABLE=$CRASHLYTICS_TABLE,ANALYTICS_DATASET=$PROJECT.$ANALYTICS_DATASET,AUDIT_TABLE=$AUDIT_TABLE,AUTO_KILL=${AUTO_KILL:-0}" \
  --set-secrets "SLACK_WEBHOOK=flagguard-slack:latest"

gcloud run jobs add-iam-policy-binding flagguard --project "$PROJECT" --region "$REGION" \
  --member "serviceAccount:$SA" --role roles/run.invoker

gcloud scheduler jobs create http flagguard-every-15m --project "$PROJECT" --location "$REGION" \
  --schedule "*/15 * * * *" --http-method POST \
  --uri "https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/flagguard:run" \
  --oauth-service-account-email "$SA" || echo "scheduler job already exists"
