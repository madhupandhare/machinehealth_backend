"""
cloud/lambda_processor/lambda_function.py
------------------------------------------
AWS Lambda handler — triggered by SQS.

On each processed fog payload:
  1. Validate schema
  2. Upsert DynamoDB MachineLatestStatus  (PK: machine_id)
  3. Append DynamoDB MachineHistory       (PK: machine_id, SK: timestamp)
  4. Archive raw JSON to S3               (date-partitioned)
  5. If machine_state == "critical"  →  publish SNS alert email/SMS

Environment variables (set in Lambda console or Terraform):
  DYNAMO_LATEST_TABLE   MachineLatestStatus
  DYNAMO_HISTORY_TABLE  MachineHistory
  S3_BUCKET             your-archive-bucket
  ENABLE_S3_ARCHIVE     true | false
  SNS_TOPIC_ARN         arn:aws:sns:region:account:MachineCriticalAlerts
  AWS_REGION            eu-west-1
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_region        = os.environ.get("AWS_REGION", "eu-west-1")
_dyn           = boto3.resource("dynamodb", region_name=_region)
_s3            = boto3.client("s3", region_name=_region)
_sns           = boto3.client("sns", region_name=_region)

LATEST_TABLE   = os.environ.get("DYNAMO_LATEST_TABLE", "MachineLatestStatus")
HISTORY_TABLE  = os.environ.get("DYNAMO_HISTORY_TABLE", "MachineHistory")
S3_BUCKET      = os.environ.get("S3_BUCKET", "")
ENABLE_S3      = os.environ.get("ENABLE_S3_ARCHIVE", "true").lower() == "true"
SNS_TOPIC_ARN  = os.environ.get("SNS_TOPIC_ARN", "")

_latest_tbl    = _dyn.Table(LATEST_TABLE)
_history_tbl   = _dyn.Table(HISTORY_TABLE)

REQUIRED_FIELDS = {"machine_id", "timestamp", "window_seconds",
                   "metrics", "anomalies", "health_score", "machine_state"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def validate(p: dict) -> bool:
    missing = REQUIRED_FIELDS - p.keys()
    if missing:
        logger.warning("Missing fields: %s", missing)
        return False
    return True


def upsert_latest(p: dict):
    _latest_tbl.put_item(Item={
        "machine_id":       p["machine_id"],
        "latest_timestamp": p["timestamp"],
        "health_score":     p["health_score"],
        "machine_state":    p["machine_state"],
        "metrics":          json.dumps(p["metrics"]),
        "anomalies":        json.dumps(p["anomalies"]),
        "window_seconds":   p["window_seconds"],
    })


def append_history(p: dict):
    _history_tbl.put_item(Item={
        "machine_id":    p["machine_id"],
        "timestamp":     p["timestamp"],
        "health_score":  p["health_score"],
        "machine_state": p["machine_state"],
        "metrics":       json.dumps(p["metrics"]),
        "anomalies":     json.dumps(p["anomalies"]),
        "window_seconds": p["window_seconds"],
    })


def archive_s3(p: dict, raw: str):
    if not S3_BUCKET:
        return
    try:
        dt  = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
        key = (f"machine-health-archive/{dt.year:04d}/{dt.month:02d}/{dt.day:02d}"
               f"/{p['machine_id']}/{p['timestamp'].replace(':','-')}.json")
        _s3.put_object(Bucket=S3_BUCKET, Key=key, Body=raw, ContentType="application/json")
    except ClientError as e:
        logger.error("S3 archive failed: %s", e)


def send_sns_alert(p: dict):
    """
    Publish a critical machine health report to the SNS topic.
    Subscribers (email / SMS / Lambda) receive the full health report.
    """
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not configured — skipping alert.")
        return

    m = p.get("metrics", {})
    active_anomalies = [k for k, v in p.get("anomalies", {}).items() if v]

    subject = f"🚨 CRITICAL ALERT — Machine {p['machine_id']} Health Score: {p['health_score']}/100"

    message = f"""
INDUSTRIAL MACHINE CRITICAL HEALTH ALERT
==========================================
Machine ID    : {p['machine_id']}
Timestamp     : {p['timestamp']}
Health Score  : {p['health_score']} / 100
Machine State : {p['machine_state'].upper()}

--- Aggregated Metrics (last {p['window_seconds']}s) ---
Vibration RMS   : {m.get('vibration_rms', 'N/A')} mm/s
Avg Temperature : {m.get('avg_temperature', 'N/A')} °C
Avg Current     : {m.get('avg_current', 'N/A')} A
Avg Acoustic    : {m.get('avg_acoustic', 'N/A')} dB

--- Active Anomalies ---
{chr(10).join('  ⚠ ' + a.replace('_', ' ').upper() for a in active_anomalies) or '  None'}

Immediate inspection is recommended.
This alert was generated automatically by the Fog-Based Machine Health Monitor.
""".strip()

    try:
        _sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message,
        )
        logger.info("SNS critical alert sent for %s", p["machine_id"])
    except ClientError as e:
        logger.error("SNS publish failed: %s", e)


# ── Lambda handler ────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    records  = event.get("Records", [])
    failures = []

    logger.info("Processing SQS batch: %d message(s)", len(records))

    for record in records:
        msg_id = record.get("messageId", "?")
        body   = record.get("body", "")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            logger.error("JSON decode error msg=%s: %s", msg_id, e)
            failures.append({"itemIdentifier": msg_id})
            continue

        if not validate(payload):
            # Invalid schema — discard (do NOT retry bad messages)
            logger.error("Schema invalid msg=%s — discarded", msg_id)
            continue

        try:
            upsert_latest(payload)
            append_history(payload)
            if ENABLE_S3:
                archive_s3(payload, body)
            # ── SNS alert on critical state ───────────────────────────────────
            if payload.get("machine_state") == "critical":
                send_sns_alert(payload)
        except ClientError as e:
            logger.error("AWS error msg=%s: %s", msg_id, e)
            failures.append({"itemIdentifier": msg_id})

    return {"batchItemFailures": failures}
