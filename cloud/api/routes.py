"""
cloud/api/routes.py
--------------------
REST API — always reads from DynamoDB when AWS env vars are set.
Falls back to local in-memory state_store otherwise.
"""
import json
import logging
import os
import sys
sys.path.insert(0, ".")

try:
    import boto3
    from botocore.exceptions import ClientError
    _BOTO_OK = True
except ImportError:
    _BOTO_OK = False

from flask import Blueprint, jsonify, request
from fog.local_state import state_store

logger = logging.getLogger("api.routes")
api_bp = Blueprint("api", __name__, url_prefix="/api")

# Detect AWS mode — requires DYNAMO_LATEST_TABLE + AWS credentials
_REGION      = os.environ.get("AWS_REGION", "us-east-1")
_LATEST_TBL  = os.environ.get("DYNAMO_LATEST_TABLE", "MachineLatestStatus")
_HISTORY_TBL = os.environ.get("DYNAMO_HISTORY_TABLE", "MachineHistory")
_USE_AWS     = _BOTO_OK and bool(os.environ.get("DYNAMO_LATEST_TABLE"))

if _USE_AWS:
    try:
        _dyn  = boto3.resource("dynamodb", region_name=_REGION)
        _ltbl = _dyn.Table(_LATEST_TBL)
        _htbl = _dyn.Table(_HISTORY_TBL)
        logger.info("API: DynamoDB mode  table=%s  region=%s", _LATEST_TBL, _REGION)
    except Exception as e:
        logger.error("DynamoDB init failed: %s", e)
        _USE_AWS = False
else:
    logger.info("API: local in-memory mode (set DYNAMO_LATEST_TABLE to use DynamoDB)")


def _parse(item: dict) -> dict:
    """Parse JSON-string fields back to dicts."""
    for f in ("metrics", "anomalies"):
        if f in item and isinstance(item[f], str):
            try:
                item[f] = json.loads(item[f])
            except Exception:
                pass
    # Normalise timestamp field name
    if "latest_timestamp" in item and "timestamp" not in item:
        item["timestamp"] = item["latest_timestamp"]
    return item


# ── /api/machines ─────────────────────────────────────────────────────────────
@api_bp.route("/machines")
def get_machines():
    if _USE_AWS:
        try:
            resp = _ltbl.scan(
                ProjectionExpression="machine_id, latest_timestamp, health_score, machine_state"
            )
            items = [_parse(i) for i in resp.get("Items", [])]
            return jsonify(items)
        except ClientError as e:
            logger.error("DynamoDB scan failed: %s", e)
            return jsonify([])
    return jsonify(state_store.get_all_machines())


# ── /api/machines/<id>/latest ─────────────────────────────────────────────────
@api_bp.route("/machines/<machine_id>/latest")
def get_latest(machine_id):
    if _USE_AWS:
        try:
            resp = _ltbl.get_item(Key={"machine_id": machine_id})
            item = resp.get("Item")
            if not item:
                return jsonify({"error": f"Machine '{machine_id}' not found"}), 404
            return jsonify(_parse(item))
        except ClientError as e:
            logger.error("DynamoDB get_item failed: %s", e)
            return jsonify({"error": str(e)}), 500

    data = state_store.get_latest(machine_id)
    if not data:
        return jsonify({"error": f"Machine '{machine_id}' not found"}), 404
    return jsonify(data)


# ── /api/machines/<id>/history ────────────────────────────────────────────────
@api_bp.route("/machines/<machine_id>/history")
def get_history(machine_id):
    limit = max(1, min(int(request.args.get("limit", 60)), 300))
    if _USE_AWS:
        try:
            from boto3.dynamodb.conditions import Key as K
            resp = _htbl.query(
                KeyConditionExpression=K("machine_id").eq(machine_id),
                ScanIndexForward=True,
                Limit=limit,
            )
            return jsonify([_parse(i) for i in resp.get("Items", [])])
        except ClientError as e:
            logger.error("DynamoDB query failed: %s", e)
            return jsonify([])
    return jsonify(state_store.get_history(machine_id, limit))


# ── /api/machines/<id>/waveform/<sensor> ──────────────────────────────────────
@api_bp.route("/machines/<machine_id>/waveform/<sensor>")
def get_waveform(machine_id, sensor):
    """Raw per-sensor waveform data from local in-memory fog store."""
    return jsonify(state_store.get_waveform(machine_id, sensor))


# ── /api/alerts ───────────────────────────────────────────────────────────────
@api_bp.route("/alerts")
def get_alerts():
    limit = max(1, min(int(request.args.get("limit", 30)), 100))
    return jsonify(state_store.get_alerts(limit))


# ── /api/status ───────────────────────────────────────────────────────────────
@api_bp.route("/status")
def status():
    """Health check endpoint used by Docker and EBS."""
    return jsonify({
        "ok": True,
        "mode": "dynamodb" if _USE_AWS else "local",
        "region": _REGION,
        "latest_table": _LATEST_TBL,
    })
