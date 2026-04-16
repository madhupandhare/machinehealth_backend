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
logger = logging.getLogger("api.demo")
demo_bp = Blueprint("demo", __name__, url_prefix="/api/demo")

BURST = 15

TOPICS = {
    "vibration": "factory/machine_01/vibration",
    "temperature": "factory/machine_01/temperature",
    "current": "factory/machine_01/current",
    "acoustic": "factory/machine_01/acoustic",
}

UNITS = {
    "vibration": "mm/s",
    "temperature": "°C",
    "current": "A",
    "acoustic": "dB",
}

SCENARIOS = {
    "multi_fault": {
        "vibration": 26.0,
        "temperature": 112.0,
        "current": 33.0,
        "acoustic": 96.0,
        "status": "fault",
        "desc": "All 4 sensors critical. Health score → 0. SNS email dispatched.",
    },
    "restore_normal": {
        "vibration": 6.5,
        "temperature": 72.0,
        "current": 16.5,
        "acoustic": 63.0,
        "status": "ok",
        "desc": "Normal readings restored. Health score → 100.",
    },
    "vibration_fault": {
        "vibration": 28.5,
        "temperature": 74.0,
        "current": 17.0,
        "acoustic": 66.0,
        "status": "fault",
        "desc": "Vibration critical (28.5 mm/s). Score drops ~75 pts.",
    },
    "temp_fault": {
        "vibration": 7.0,
        "temperature": 115.0,
        "current": 17.0,
        "acoustic": 66.0,
        "status": "fault",
        "desc": "Temperature critical (115 °C). Score drops ~75 pts.",
    },
}


def _load_config():
    """
    Load config.yaml and return the iot section.
    """
    import yaml

    cfg_path = os.path.join(os.path.dirname(__file__), "../../config.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = f.read()

    for k, v in os.environ.items():
        raw = raw.replace(f"${{{k}}}", v)

    cfg = yaml.safe_load(raw) or {}
    return cfg.get("iot", {})


def _resolve_iot_settings():
    """
    Resolve all IoT settings from config.yaml with env overrides.
    """
    iot = _load_config()

    endpoint = os.environ.get("IOT_ENDPOINT", iot.get("endpoint", ""))
    cert = os.environ.get("PATH_TO_CERT", iot.get("cert_path", "certs/device.pem.crt"))
    key = os.environ.get("PATH_TO_PRIVATE_KEY", iot.get("private_key_path", "certs/private.pem.key"))
    ca = os.environ.get("PATH_TO_ROOT_CA", iot.get("root_ca_path", "certs/AmazonRootCA1.pem"))
    port = int(os.environ.get("IOT_PORT", iot.get("port", 8883)))
    keepalive = int(os.environ.get("IOT_KEEPALIVE", iot.get("keepalive", 30)))

    return {
        "endpoint": endpoint,
        "cert": cert,
        "key": key,
        "ca": ca,
        "port": port,
        "keepalive": keepalive,
    }


def _make_mqtt_client():
    """
    Create and connect a paho MQTT client to IoT Core.
    Returns connected client or None on failure.
    """
    try:
        import paho.mqtt.client as mqtt

        settings = _resolve_iot_settings()
        endpoint = settings["endpoint"]
        cert = settings["cert"]
        key = settings["key"]
        ca = settings["ca"]
        port = settings["port"]
        keepalive = settings["keepalive"]

        if not endpoint:
            return None, "IOT endpoint is missing in config.yaml"

        for f in (cert, key, ca):
            if not os.path.isfile(f):
                logger.error("Cert file not found: %s", f)
                return None, f"Certificate not found: {f}"

        connected = {"ok": False, "err": None}

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                connected["ok"] = True
            else:
                connected["err"] = f"connect rc={rc} (policy not attached or cert inactive)"

        def on_disconnect(client, userdata, rc):
            if rc != 0:
                logger.warning("Unexpected MQTT disconnect rc=%s", rc)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cafile=ca)
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED

        client = mqtt.Client(
            client_id=f"imhm-demo-{int(time.time() * 1000)}",
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        client.tls_set_context(ctx)
        client.on_connect = on_connect
        client.on_disconnect = on_disconnect

        logger.info("Connecting to AWS IoT Core at %s:%s ...", endpoint, port)
        client.connect(endpoint, port, keepalive=keepalive)
        client.loop_start()

        while True:
            if connected["ok"]:
                return client, None
            if connected["err"]:
                break
            time.sleep(0.1)

        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass

        return None, connected["err"]

    except Exception as e:
        logger.error("MQTT client creation failed: %s", e, exc_info=True)
        return None, str(e)


def _publish_burst(client, scenario: dict) -> dict:
    """Publish BURST messages for each sensor using an already-connected client."""
    published = 0
    errors = 0
    status = scenario.get("status", "fault")

    for _ in range(BURST):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for sensor in ("vibration", "temperature", "current", "acoustic"):
            value = scenario.get(sensor, 0)
            payload = {
                "machine_id": "machine_01",
                "sensor_type": sensor,
                "timestamp": ts,
                "value": float(value),
                "unit": UNITS[sensor],
                "status": status,
            }
            try:
                rc = client.publish(TOPICS[sensor], json.dumps(payload), qos=1)
                if getattr(rc, "rc", 1) == 0:
                    published += 1
                else:
                    errors += 1
                    logger.warning(
                        "Publish failed for topic=%s rc=%s",
                        TOPICS[sensor],
                        getattr(rc, "rc", None),
                    )
            except Exception as e:
                errors += 1
                logger.warning("Publish error: %s", e, exc_info=True)
        time.sleep(0.06)

    return {"published": published, "errors": errors}


@demo_bp.route("/inject", methods=["POST"])
def inject():
    """
    Inject fault readings for the requested scenario.
    Called by the "Simulate Critical" button in the React dashboard header.
    """
    body = request.get_json(silent=True) or {}
    scenario_id = body.get("scenario", "multi_fault")

    if scenario_id not in SCENARIOS:
        return jsonify(
            {
                "error": f"Unknown scenario '{scenario_id}'",
                "valid": list(SCENARIOS.keys()),
            }
        ), 400

    scenario = SCENARIOS[scenario_id]
    logger.info("Demo inject: scenario=%s burst=%d", scenario_id, BURST)

    client, err = _make_mqtt_client()
    if client is None:
        return jsonify(
            {
                "error": "Could not connect to AWS IoT Core",
                "detail": err,
                "fix": "Check config.yaml endpoint/cert paths and ensure port 8883 is reachable.",
            }
        ), 500

    result = _publish_burst(client, scenario)

    try:
        client.loop_stop()
        client.disconnect()
    except Exception:
        pass

    if result["published"] == 0:
        return jsonify(
            {
                "error": "No messages published successfully",
                "errors": result["errors"],
            }
        ), 500

    return jsonify(
        {
            "ok": True,
            "scenario": scenario_id,
            "published": result["published"],
            "errors": result["errors"],
            "message": scenario["desc"],
            "note": "Fog node will process in ~10s. Click Refresh to see updated state.",
            "sns_expected": scenario_id not in ("restore_normal",),
        }
    )


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