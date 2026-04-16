"""sensors/current_sensor.py — rc=16 reconnect-safe"""
import sys, time
sys.path.insert(0, ".")
from iot_client import (build_payload, connect_iot, create_iot_client,
                         generate_value, get_logger, load_config)
logger = get_logger("sensor.current")
def run():
    cfg = load_config(); mid = cfg["machine"]["id"]; sc = cfg["sensors"]["current"]
    client = create_iot_client(client_id="imhm-current-sensor-01")
    connect_iot(client)
    logger.info("Current sensor publishing to '%s'", sc["topic"])
    consecutive_fails = 0
    try:
        while True:
            if not client.is_connected():
                logger.warning("Not connected — waiting..."); time.sleep(3)
                consecutive_fails += 1
                if consecutive_fails > 20: break
                continue
            consecutive_fails = 0
            val, status = generate_value(sc["normal_range"], sc["fault_range"], sc["fault_injection_probability"])
            client.publish(sc["topic"], build_payload(mid, "current", val, sc["unit"], status), qos=1)
            logger.debug("current=%.3f %s [%s]", val, sc["unit"], status)
            time.sleep(sc["publish_interval_seconds"])
    except KeyboardInterrupt:
        logger.info("Current sensor stopped.")
    finally:
        client.loop_stop(); client.disconnect()
if __name__ == "__main__":
    run()
