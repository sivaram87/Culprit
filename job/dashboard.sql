-- Views behind the Looker Studio dashboard. deploy.sh replaces {project} and runs this.
-- Safe to re-run: every view is CREATE OR REPLACE.

-- Every finding, with the numbers people actually read.
CREATE OR REPLACE VIEW `{project}.flagguard.findings` AS
SELECT
  ts,
  DATE(ts) AS day,
  flag, variant, version, signal, action,
  users, crashed_users, crash_rate, control_rate, p,
  SAFE_DIVIDE(crash_rate, NULLIF(control_rate, 0)) AS rate_ratio,
  JSON_VALUE(segments, '$[0].segment') AS top_segment,
  segments
FROM `{project}.flagguard.actions`;

-- Findings per day, for trend charts.
CREATE OR REPLACE VIEW `{project}.flagguard.daily` AS
SELECT DATE(ts) AS day, signal, action, COUNT(*) AS findings
FROM `{project}.flagguard.actions`
GROUP BY day, signal, action;

-- One row per flag over the last 30 days.
CREATE OR REPLACE VIEW `{project}.flagguard.flags_30d` AS
SELECT
  flag,
  COUNT(*) AS alerts,
  COUNTIF(action = 'killed') AS kills,
  MAX(ts) AS last_alert,
  ARRAY_AGG(action ORDER BY ts DESC LIMIT 1)[OFFSET(0)] AS last_action
FROM `{project}.flagguard.actions`
WHERE ts > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY flag;
