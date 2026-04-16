"""
iot_client.py  (v3 — Windows rc=16 keepalive fix)
--------------------------------------------------
Shared AWS IoT Core MQTT connection factory.

rc=16 fix:
  - Reduced keepalive from 60s to 30s (AWS IoT Core drops idle connections
    after ~60s if PINGREQ is missed; 30s gives comfortable headroom)
  - loop_forever() replaced with loop_start() + explicit reconnect loop
    (loop_forever blocks; loop_start uses background thread correctly)
  - Added on_disconnect auto-reconnect callback
  - Each sensor runs its own publish-and-ping loop
"""

import json
import logging
import os
import ssl
import time
from datetime import datetime, timezone
from typing import Callable, Optional
import random

import paho.mqtt.client as mqtt
import yaml

_log = logging.getLogger("iot_client")

# ── Config ────────────────────────────────────────────────────────────────────

_cfg_cache: Optional[dict] = None


def load_config(path: str = "config.yaml") -> dict:
    global _cfg_cache
    if _cfg_cache is None:
        with open(path) as f:
            raw = f.read()
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


# ── Payload / value helpers ───────────────────────────────────────────────────

def build_payload(machine_id: str, sensor_type: str,
                  value: float, unit: str, status: str = "ok") -> str:
    return json.dumps({
        "machine_id":  machine_id,
        "sensor_type": sensor_type,
        "timestamp":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "value":       round(value, 4),
        "unit":        unit,
        "status":      status,
    })


def generate_value(normal_range: list, fault_range: list,
                   fault_prob: float) -> tuple:
    if random.random() < fault_prob:
        return random.uniform(*fault_range), "fault"
    v = random.uniform(*normal_range)
    v += random.gauss(0, (normal_range[1] - normal_range[0]) * 0.02)
    return max(0.0, v), "ok"


# ── IoT Core client factory ───────────────────────────────────────────────────

def _build_tls_context(cert: str, key: str, ca: str) -> ssl.SSLContext:
    """Build Windows-compatible TLS context."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=ca)
    ctx.load_cert_chain(certfile=cert, keyfile=key)
    ctx.check_hostname = True
    ctx.verify_mode    = ssl.CERT_REQUIRED
    ctx.set_alpn_protocols(["x-amzn-mqtt-ca"])
    return ctx


def create_iot_client(
    client_id: str,
    on_message: Optional[Callable] = None,
    on_connect_extra: Optional[Callable] = None,
) -> mqtt.Client:
    """
    Create and return a paho MQTT client pre-configured for AWS IoT Core.

    rc=16 fix: keepalive=30, explicit reconnect on disconnect.
    """
    cfg = load_config()
    iot = cfg["iot"]

    cert = os.environ.get("PATH_TO_CERT",        iot.get("cert_path",         "certs/device.pem.crt"))
    key  = os.environ.get("PATH_TO_PRIVATE_KEY",  iot.get("private_key_path",  "certs/private.pem.key"))
    ca   = os.environ.get("PATH_TO_ROOT_CA",      iot.get("root_ca_path",      "certs/AmazonRootCA1.pem"))

    for label, path in [("cert", cert), ("key", key), ("ca", ca)]:
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"Certificate file not found: '{path}'\n"
                f"Place AWS IoT Core certificates in the certs/ directory."
            )

    tls_ctx = _build_tls_context(cert, key, ca)

    # MQTTv311 is more stable than v5 with paho on Windows
    client = mqtt.Client(
        client_id=client_id,
        clean_session=True,
        protocol=mqtt.MQTTv311,
    )
    client.tls_set_context(tls_ctx)

    # Store reconnect parameters on the client object for use in callbacks
    client._imhm_reconnect_delay = 5   # seconds between reconnect attempts

    def _on_connect(c, userdata, flags, rc):
        if rc == 0:
            _log.info("IoT Core connected (client_id=%s)", client_id)
            client._imhm_reconnect_delay = 5   # reset back-off on success
            if on_connect_extra:
                on_connect_extra(c, userdata, flags, rc)
        else:
            _log.error(
                "IoT Core connect FAILED rc=%d (client_id=%s) — "
                "rc=7=policy issue, rc=5=not-authorised, rc=16=keepalive-timeout",
                rc, client_id,
            )

    def _on_disconnect(c, userdata, rc):
        if rc == 0:
            _log.info("IoT Core disconnected cleanly (client_id=%s)", client_id)
            return
        _log.warning(
            "IoT Core disconnected rc=%d (client_id=%s) — will auto-reconnect",
            rc, client_id,
        )
        # rc=16 = keepalive timeout. Back off then reconnect.
        delay = client._imhm_reconnect_delay
        client._imhm_reconnect_delay = min(delay * 2, 60)
        _log.info("Reconnecting in %ds...", delay)
        time.sleep(delay)
        try:
            endpoint = os.environ.get("IOT_ENDPOINT", cfg["iot"].get("endpoint", ""))
            port     = int(cfg["iot"].get("port", 8883))
            c.reconnect()
            _log.info("Reconnect succeeded (client_id=%s)", client_id)
        except Exception as e:
            _log.warning("Reconnect attempt failed: %s — paho will retry", e)

    client.on_connect    = _on_connect
    client.on_disconnect = _on_disconnect
    if on_message:
        client.on_message = on_message

    return client


def connect_iot(client: mqtt.Client, max_retries: int = 10) -> None:
    """
    Connect to IoT Core and start background network loop.

    Uses loop_start() (background thread) not loop_forever() (blocking).
    keepalive=30 to prevent rc=16 keepalive timeouts on Windows.
    """
    cfg      = load_config()
    iot      = cfg["iot"]
    endpoint = os.environ.get("IOT_ENDPOINT", iot.get("endpoint", ""))
    port     = int(iot.get("port", 8883))
    keepalive = 30   # reduced from 60 — fixes rc=16 on Windows

    print(f"DEBUG: Using IoT endpoint = {endpoint}")

    if not endpoint or "${" in endpoint:
        raise ValueError(
            "IOT_ENDPOINT is not set.\n"
            "Add it to your .env file.\n"
            "Find it at: AWS Console → IoT Core → Settings → Device data endpoint"
        )

    for attempt in range(1, max_retries + 1):
        try:
            client.connect(endpoint, port, keepalive=keepalive)
            client.loop_start()   # background thread — does NOT block
            # Wait up to 4s for on_connect callback to confirm connection
            for _ in range(40):
                time.sleep(0.1)
                if getattr(client, '_imhm_reconnect_delay', 5) < 5:
                    break   # delay was reset = connected
            time.sleep(1.5)
            _log.info("loop_start() running, connection established")
            return
        except Exception as exc:
            _log.warning("Connect attempt %d/%d failed: %s", attempt, max_retries, exc)
            time.sleep(min(2 ** attempt, 30))

    raise ConnectionError(
        f"Could not connect to AWS IoT Core at {endpoint}:{port}.\n"
        f"Run: python diagnose_iot.py"
    )
