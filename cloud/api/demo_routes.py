"""
cloud/api/demo_routes.py
-------------------------
Demo/presentation endpoints.
POST /api/demo/inject  — injects fault sensor readings to IoT Core
                          so the fog node detects anomalies and triggers SNS.

Used by the DemoPanel React component for live presentations.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

logger  = logging.getLogger("api.demo")
demo_bp = Blueprint("demo", __name__, url_prefix="/api/demo")

# How many fault messages to publish per sensor (fog window = 10s)
BURST = 12

# Scenario definitions — value to inject per sensor
SCENARIOS = {
    "vibration_spike": {
        "vibration":   28.5,   # way above crit=16
        "temperature": 74.0,   # normal
        "current":     17.0,   # normal
        "acoustic":    66.0,   # normal
        "desc": "High vibration injected (28.5 mm/s) — bearing fault simulation",
    },
    "overtemperature": {
        "vibration":   7.0,    # normal
        "temperature": 118.0,  # way above crit=100
        "current":     17.0,
        "acoustic":    66.0,
        "desc": "Overtemperature injected (118 °C) — cooling failure simulation",
    },
    "multi_fault": {
        "vibration":   25.0,   # above crit=16
        "temperature": 110.0,  # above crit=100
        "current":     32.0,   # above crit=28
        "acoustic":    95.0,   # above crit=90
        "desc": "All 4 sensors in fault — maximum severity, SNS email dispatched",
    },
    "warning_only": {
        "vibration":   11.5,   # above warn=10, below crit=16
        "temperature": 90.0,   # above warn=88, below crit=100
        "current":     25.0,   # above warn=22, below crit=28
        "acoustic":    80.0,   # above warn=78, below crit=90
        "desc": "Warning-level readings — health drops but no SNS alert",
    },
    "restore_normal": {
        "vibration":   6.5,
        "temperature": 72.0,
        "current":     16.5,
        "acoustic":    63.0,
        "desc": "Normal readings restored — machine returns to healthy state",
    },
}

SENSOR_TOPICS = {
    "vibration":   "factory/machine_01/vibration",
    "temperature": "factory/machine_01/temperature",
    "current":     "factory/machine_01/current",
    "acoustic":    "factory/machine_01/acoustic",
}

SENSOR_UNITS = {
    "vibration": "mm/s", "temperature": "°C",
    "current": "A",      "acoustic": "dB",
}


def _publish_to_iot(topic: str, payload: dict) -> bool:
    """Publish a single message to AWS IoT Core via paho."""
    import sys
    sys.path.insert(0, ".")
    try:
        from iot_client import connect_iot, create_iot_client
        client = create_iot_client(client_id=f"imhm-demo-injector-{int(time.time())}")
        connect_iot(client, max_retries=3)
        rc = client.publish(topic, json.dumps(payload), qos=1)
        time.sleep(0.05)
        client.loop_stop()
        client.disconnect()
        return rc.rc == 0
    except Exception as e:
        logger.error("IoT publish error: %s", e)
        return False


@demo_bp.route("/inject", methods=["POST"])
def inject_fault():
    """
    Publish burst of fault readings to IoT Core for the requested scenario.
    The fog node will pick these up in its next 10s aggregation window.
    """
    body = request.get_json(silent=True) or {}
    scenario_id = body.get("scenario", "multi_fault")

    if scenario_id not in SCENARIOS:
        return jsonify({"error": f"Unknown scenario '{scenario_id}'. Valid: {list(SCENARIOS.keys())}"}), 400

    sc  = SCENARIOS[scenario_id]
    mid = "machine_01"

    logger.info("Demo injection: scenario=%s burst=%d", scenario_id, BURST)

    published = 0
    errors    = 0

    # Publish BURST messages per sensor — saturates the 10s fog window
    for i in range(BURST):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for sensor, value in sc.items():
            if sensor in ("desc",):
                continue
            payload = {
                "machine_id":  mid,
                "sensor_type": sensor,
                "timestamp":   ts,
                "value":       float(value),
                "unit":        SENSOR_UNITS.get(sensor, ""),
                "status":      "fault" if scenario_id != "restore_normal" else "ok",
            }
            topic = SENSOR_TOPICS[sensor]
            ok = _publish_to_iot(topic, payload)
            if ok:
                published += 1
            else:
                errors += 1
        time.sleep(0.1)   # brief gap between bursts

    if errors > 0 and published == 0:
        return jsonify({
            "error": "Could not connect to IoT Core. Check certs and IOT_ENDPOINT in .env",
            "published": 0,
            "errors": errors,
        }), 500

    return jsonify({
        "ok":        True,
        "scenario":  scenario_id,
        "published": published,
        "errors":    errors,
        "message":   sc["desc"] + f" ({published} messages published to IoT Core).",
        "note":      "Dashboard will update on next fog dispatch (~10s). Click Refresh Now.",
    })


@demo_bp.route("/scenarios", methods=["GET"])
def list_scenarios():
    """List available demo scenarios."""
    return jsonify([
        {"id": k, "desc": v["desc"]} for k, v in SCENARIOS.items()
    ])
