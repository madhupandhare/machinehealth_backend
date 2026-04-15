"""
cloud/api/demo_routes.py
-------------------------
Single endpoint used by the "Simulate Critical" button in the dashboard header.

POST /api/demo/inject  { "scenario": "multi_fault" | "restore_normal" | ... }
GET  /api/demo/status  — check IoT connection

How it works:
  1. React button calls POST /api/demo/inject
  2. Flask publishes BURST fault readings to all 4 IoT Core sensor topics
  3. Fog node already subscribed — picks up readings in current 10s window
  4. Fog node computes health_score=0, machine_state=critical
  5. Fog node calls sns.publish() directly → SNS email to subscribers
  6. Dashboard auto-refreshes (or user clicks Refresh)
"""
import json
import logging
import os
import ssl
import sys
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

sys.path.insert(0, ".")
logger  = logging.getLogger("api.demo")
demo_bp = Blueprint("demo", __name__, url_prefix="/api/demo")

# Number of messages per sensor per inject call.
# Fog window = 10s. With BURST=15 messages published quickly,
# all readings in that window will be fault values.
BURST = 15

# Sensor topics
TOPICS = {
    "vibration":   "factory/machine_01/vibration",
    "temperature": "factory/machine_01/temperature",
    "current":     "factory/machine_01/current",
    "acoustic":    "factory/machine_01/acoustic",
}
UNITS = {
    "vibration": "mm/s", "temperature": "°C",
    "current": "A",      "acoustic": "dB",
}

# Scenario fault values (all sensors critical = score drops to 0)
SCENARIOS = {
    "multi_fault": {
        "vibration":   26.0,   # crit threshold = 16
        "temperature": 112.0,  # crit threshold = 100
        "current":     33.0,   # crit threshold = 28
        "acoustic":    96.0,   # crit threshold = 90
        "status":      "fault",
        "desc":        "All 4 sensors critical. Health score → 0. SNS email dispatched.",
    },
    "restore_normal": {
        "vibration":   6.5,
        "temperature": 72.0,
        "current":     16.5,
        "acoustic":    63.0,
        "status":      "ok",
        "desc":        "Normal readings restored. Health score → 100.",
    },
    "vibration_fault": {
        "vibration":   28.5,
        "temperature": 74.0,
        "current":     17.0,
        "acoustic":    66.0,
        "status":      "fault",
        "desc":        "Vibration critical (28.5 mm/s). Score drops ~75 pts.",
    },
    "temp_fault": {
        "vibration":   7.0,
        "temperature": 115.0,
        "current":     17.0,
        "acoustic":    66.0,
        "status":      "fault",
        "desc":        "Temperature critical (115 °C). Score drops ~75 pts.",
    },
}


def _make_mqtt_client():
    """
    Create and connect a paho MQTT client to IoT Core.
    Returns connected client or None on failure.
    """
    try:
        import paho.mqtt.client as mqtt

        # Load config + env
        import yaml
        cfg_path = os.path.join(os.path.dirname(__file__), "../../config.yaml")
        with open(cfg_path) as f:
            raw = f.read()
        for k, v in os.environ.items():
            raw = raw.replace(f"${{{k}}}", v)
        cfg = yaml.safe_load(raw)
        iot = cfg["iot"]

        endpoint = os.environ.get("IOT_ENDPOINT", iot.get("endpoint", ""))
        cert     = os.environ.get("PATH_TO_CERT",         iot.get("cert_path", "certs/device.pem.crt"))
        key      = os.environ.get("PATH_TO_PRIVATE_KEY",  iot.get("private_key_path", "certs/private.pem.key"))
        ca       = os.environ.get("PATH_TO_ROOT_CA",      iot.get("root_ca_path", "certs/AmazonRootCA1.pem"))
        port     = int(iot.get("port", 8883))

        for f in (cert, key, ca):
            if not os.path.isfile(f):
                logger.error("Cert file not found: %s", f)
                return None, f"Certificate not found: {f}"

        connected = {"ok": False, "err": None}

        def on_connect(c, u, f, rc):
            connected["ok"] = rc == 0
            if rc != 0:
                connected["err"] = f"rc={rc} (policy not attached or cert inactive)"

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cafile=ca)
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        ctx.check_hostname = True
        ctx.verify_mode    = ssl.CERT_REQUIRED

        client = mqtt.Client(
            client_id=f"imhm-demo-{int(time.time())}",
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        client.tls_set_context(ctx)
        client.on_connect = on_connect
        client.connect(endpoint, port, keepalive=30)
        client.loop_start()

        # Wait up to 5s for connection
        for _ in range(50):
            if connected["ok"]:
                break
            time.sleep(0.1)

        if not connected["ok"]:
            client.loop_stop()
            return None, connected.get("err") or "Timed out connecting to IoT Core"

        return client, None

    except Exception as e:
        logger.error("MQTT client creation failed: %s", e)
        return None, str(e)


def _publish_burst(client, scenario: dict) -> dict:
    """Publish BURST messages for each sensor using an already-connected client."""
    published = 0
    errors    = 0
    status    = scenario.get("status", "fault")

    for _ in range(BURST):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for sensor in ("vibration", "temperature", "current", "acoustic"):
            value = scenario.get(sensor, 0)
            payload = {
                "machine_id":  "machine_01",
                "sensor_type": sensor,
                "timestamp":   ts,
                "value":       float(value),
                "unit":        UNITS[sensor],
                "status":      status,
            }
            try:
                rc = client.publish(TOPICS[sensor], json.dumps(payload), qos=1)
                if rc.rc == 0:
                    published += 1
                else:
                    errors += 1
            except Exception as e:
                errors += 1
                logger.warning("Publish error: %s", e)
        time.sleep(0.06)   # small gap between bursts

    return {"published": published, "errors": errors}


# ── API endpoints ─────────────────────────────────────────────────────────────

@demo_bp.route("/inject", methods=["POST"])
def inject():
    """
    Inject fault readings for the requested scenario.
    Called by the "Simulate Critical" button in the React dashboard header.
    """
    body        = request.get_json(silent=True) or {}
    scenario_id = body.get("scenario", "multi_fault")

    if scenario_id not in SCENARIOS:
        return jsonify({
            "error": f"Unknown scenario '{scenario_id}'",
            "valid": list(SCENARIOS.keys()),
        }), 400

    scenario = SCENARIOS[scenario_id]
    logger.info("Demo inject: scenario=%s burst=%d", scenario_id, BURST)

    # Connect to IoT Core
    client, err = _make_mqtt_client()
    if client is None:
        return jsonify({
            "error":  "Could not connect to AWS IoT Core",
            "detail": err,
            "fix":    "Check certs in certs/ folder and IOT_ENDPOINT in .env",
        }), 500

    # Publish burst
    result = _publish_burst(client, scenario)

    # Disconnect cleanly
    try:
        client.loop_stop()
        client.disconnect()
    except Exception:
        pass

    if result["published"] == 0:
        return jsonify({
            "error":   "No messages published successfully",
            "errors":  result["errors"],
        }), 500

    return jsonify({
        "ok":           True,
        "scenario":     scenario_id,
        "published":    result["published"],
        "errors":       result["errors"],
        "message":      scenario["desc"],
        "note":         "Fog node will process in ~10s. Click Refresh to see updated state.",
        "sns_expected": scenario_id not in ("restore_normal",),
    })


@demo_bp.route("/status", methods=["GET"])
def status():
    """Quick connectivity check — tries to connect to IoT Core."""
    client, err = _make_mqtt_client()
    if client:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
        return jsonify({"iot_connected": True})
    return jsonify({"iot_connected": False, "error": err})
