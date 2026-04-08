"""
iot_client.py  (patched v2 — Windows + Python 3.12 compatible)
---------------------------------------------------------------
Shared AWS IoT Core MQTT connection factory.

All sensors and the fog node import from here so certificate paths and
TLS configuration are defined in exactly one place.

Uses paho-mqtt with mutual TLS (port 8883) — the same protocol as before
but connecting to AWS IoT Core instead of a local Mosquitto broker.
"""

import json
import logging
import os
import ssl
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import paho.mqtt.client as mqtt
import yaml

_log = logging.getLogger("iot_client")

# ── Config helpers ────────────────────────────────────────────────────────────

_cfg_cache: Optional[dict] = None


def load_config(path: str = "config.yaml") -> dict:
    global _cfg_cache
    if _cfg_cache is None:
        with open(path) as f:
            raw = f.read()
        # Expand ${VAR} from environment
        for key, val in os.environ.items():
            raw = raw.replace(f"${{{key}}}", val)
        _cfg_cache = yaml.safe_load(raw)
    return _cfg_cache


def get_logger(name: str) -> logging.Logger:
    cfg = load_config()
    lc = cfg.get("logging", {})
    logging.basicConfig(
        level=getattr(logging, lc.get("level", "INFO")),
        format=lc.get("format", "%(asctime)s [%(levelname)s] %(name)s — %(message)s"),
    )
    return logging.getLogger(name)


# ── Payload builder ───────────────────────────────────────────────────────────

def build_payload(machine_id: str, sensor_type: str, value: float, unit: str, status: str = "ok") -> str:
    return json.dumps({
        "machine_id": machine_id,
        "sensor_type": sensor_type,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "value": round(value, 4),
        "unit": unit,
        "status": status,
    })


# ── Value generator ───────────────────────────────────────────────────────────

import random


def generate_value(normal_range: list, fault_range: list, fault_prob: float) -> tuple:
    if random.random() < fault_prob:
        return random.uniform(*fault_range), "fault"
    v = random.uniform(*normal_range)
    v += random.gauss(0, (normal_range[1] - normal_range[0]) * 0.02)
    return max(0.0, v), "ok"


# ── IoT Core MQTT client factory ──────────────────────────────────────────────

def create_iot_client(
    client_id: str,
    on_message: Optional[Callable] = None,
    on_connect_extra: Optional[Callable] = None,
) -> mqtt.Client:
    """
    Create and return a paho MQTT client pre-configured for AWS IoT Core.
    The caller must still call client.connect() and client.loop_start().

    Parameters
    ----------
    client_id          : unique MQTT client ID string
    on_message         : optional callback(client, userdata, msg)
    on_connect_extra   : optional callback run after successful connect
    """
    cfg = load_config()
    iot = cfg["iot"]

    cert   = iot["cert_path"]
    key    = iot["private_key_path"]
    ca     = iot["root_ca_path"]

    for path in (cert, key, ca):
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"Certificate file not found: {path}\n"
                "Place AWS IoT Core certificates in the certs/ directory.\n"
                "See docs/aws_setup.md for instructions."
            )

    # Build a proper SSL context (Windows + Python 3.12 safe)
    try:
        tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls_ctx.load_verify_locations(cafile=ca)
        tls_ctx.load_cert_chain(certfile=cert, keyfile=key)
        tls_ctx.check_hostname = True
        tls_ctx.verify_mode = ssl.CERT_REQUIRED
    except Exception:
        # Fallback for older paho/ssl combinations
        tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS)  # type: ignore[attr-defined]
        tls_ctx.load_verify_locations(cafile=ca)
        tls_ctx.load_cert_chain(certfile=cert, keyfile=key)
        tls_ctx.verify_mode = ssl.CERT_REQUIRED
        tls_ctx.check_hostname = False

    client = mqtt.Client(client_id=client_id, clean_session=True,
                         protocol=mqtt.MQTTv311)
    client.tls_set_context(tls_ctx)

    def _on_connect(c, userdata, flags, rc):
        if rc == 0:
            _log.info("IoT Core connected (client_id=%s)", client_id)
            if on_connect_extra:
                on_connect_extra(c, userdata, flags, rc)
        else:
            _log.error("IoT Core connect failed rc=%d (client_id=%s)", rc, client_id)

    def _on_disconnect(c, userdata, rc):
        _log.warning("IoT Core disconnected rc=%d (client_id=%s) — will reconnect", rc, client_id)

    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    if on_message:
        client.on_message = on_message

    return client


def connect_iot(client: mqtt.Client, max_retries: int = 10) -> None:
    """Connect to IoT Core with exponential back-off."""
    cfg = load_config()
    iot = cfg["iot"]
    endpoint  = os.environ.get("IOT_ENDPOINT", iot.get("endpoint", ""))
    port      = int(iot.get("port", 8883))
    keepalive = int(iot.get("keepalive", 60))

    print(f"DEBUG: Using IoT endpoint = {endpoint}")

    if not endpoint or "${" in endpoint:
        raise ValueError(
            "IOT_ENDPOINT is not set.\n"
            "Add it to your .env file.\n"
            "Find it at: AWS Console → IoT Core → Settings → Device data endpoint"
        )

    for attempt in range(1, max_retries + 1):
        try:
            client.connect(endpoint, port, keepalive)
            client.loop_start()
            time.sleep(2.0)
            return
        except Exception as exc:
            _log.warning("Connect attempt %d/%d failed: %s", attempt, max_retries, exc)
            time.sleep(min(2 ** attempt, 30))

    raise ConnectionError(
        f"Could not connect to AWS IoT Core at {endpoint}:{port}.\n"
        f"Run: python diagnose_iot.py"
    )
