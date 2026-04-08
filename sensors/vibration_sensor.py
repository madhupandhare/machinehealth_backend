"""
sensors/vibration_sensor.py
----------------------------
Vibration sensor — publishes mm/s readings directly to AWS IoT Core.

Topic: factory/machine_01/vibration

Run:
    cd imhm-v2
    python sensors/vibration_sensor.py
"""
import sys, time
sys.path.insert(0, ".")

from iot_client import build_payload, connect_iot, create_iot_client, generate_value, get_logger, load_config

logger = get_logger("sensor.vibration")

def run():
    cfg   = load_config()
    mid   = cfg["machine"]["id"]
    sc    = cfg["sensors"]["vibration"]

    client = create_iot_client(client_id="imhm-vibration-sensor-01")
    connect_iot(client)

    logger.info("Vibration sensor publishing to '%s' every %ss", sc["topic"], sc["publish_interval_seconds"])
    try:
        while True:
            val, status = generate_value(sc["normal_range"], sc["fault_range"], sc["fault_injection_probability"])
            payload = build_payload(mid, "vibration", val, sc["unit"], status)
            rc = client.publish(sc["topic"], payload, qos=1)
            if rc.rc == 0:
                logger.debug("vibration=%.3f %s [%s]", val, sc["unit"], status)
            else:
                logger.warning("Publish failed rc=%d", rc.rc)
            time.sleep(sc["publish_interval_seconds"])
    except KeyboardInterrupt:
        logger.info("Vibration sensor stopped.")
    finally:
        client.loop_stop(); client.disconnect()

if __name__ == "__main__":
    run()
