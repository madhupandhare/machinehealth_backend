"""
sensors/acoustic_sensor.py
---------------------------
Acoustic sensor — publishes dB readings directly to AWS IoT Core.
Topic: factory/machine_01/acoustic
"""
import sys, time
sys.path.insert(0, ".")
from iot_client import build_payload, connect_iot, create_iot_client, generate_value, get_logger, load_config

logger = get_logger("sensor.acoustic")

def run():
    cfg = load_config()
    mid = cfg["machine"]["id"]
    sc  = cfg["sensors"]["acoustic"]
    client = create_iot_client(client_id="imhm-acoustic-sensor-01")
    connect_iot(client)
    logger.info("Acoustic sensor publishing to '%s'", sc["topic"])
    try:
        while True:
            val, status = generate_value(sc["normal_range"], sc["fault_range"], sc["fault_injection_probability"])
            client.publish(sc["topic"], build_payload(mid, "acoustic", val, sc["unit"], status), qos=1)
            logger.debug("acoustic=%.2f %s [%s]", val, sc["unit"], status)
            time.sleep(sc["publish_interval_seconds"])
    except KeyboardInterrupt:
        logger.info("Acoustic sensor stopped.")
    finally:
        client.loop_stop(); client.disconnect()

if __name__ == "__main__":
    run()
