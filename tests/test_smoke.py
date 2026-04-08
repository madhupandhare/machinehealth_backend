"""
tests/test_smoke.py — run with: python tests/test_smoke.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def test_aggregator():
    from fog.aggregator import Aggregator
    from datetime import datetime, timezone
    import math
    a = Aggregator(window_seconds=60)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for v in [3.0, 4.0, 0.0]:
        a.ingest("vibration", v, ts)
    m = a.compute_metrics()
    expected = round(math.sqrt((9+16+0)/3), 4)
    assert abs(m["vibration_rms"] - expected) < 0.001, m
    print("PASS: aggregator rms")

def test_aggregator_mean():
    from fog.aggregator import Aggregator
    from datetime import datetime, timezone
    a = Aggregator(window_seconds=60)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for v in [70.0, 80.0, 90.0]:
        a.ingest("temperature", v, ts)
    assert a.compute_metrics()["avg_temperature"] == 80.0
    print("PASS: aggregator mean")

def test_detection_clear():
    from fog.detection import AnomalyDetector
    d = AnomalyDetector({"vibration_rms_critical":16,"temperature_critical":100,
                          "current_critical":28,"acoustic_critical":90})
    flags = d.detect({"vibration_rms":5,"avg_temperature":70,"avg_current":15,"avg_acoustic":60})
    assert not any(flags.values())
    print("PASS: detection no anomalies")

def test_detection_critical():
    from fog.detection import AnomalyDetector
    d = AnomalyDetector({"vibration_rms_critical":16,"temperature_critical":100,
                          "current_critical":28,"acoustic_critical":90})
    flags = d.detect({"vibration_rms":20,"avg_temperature":70,"avg_current":15,"avg_acoustic":60})
    assert flags["vibration_alert"] is True
    assert flags["temperature_alert"] is False
    print("PASS: detection vibration_alert")

def test_health_score_full():
    from fog.health_score import calculate
    score, state = calculate({}, {})
    assert score == 100 and state == "healthy"
    print("PASS: health score 100/healthy")

def test_health_score_critical():
    from fog.health_score import calculate
    anomalies = {k: True for k in ["vibration_alert","temperature_alert","overload_alert","acoustic_alert"]}
    score, state = calculate(anomalies, {})
    assert score == 0 and state == "critical"
    print("PASS: health score 0/critical")

def test_health_score_warning():
    from fog.health_score import calculate
    warnings = {"vibration_warning": True, "temperature_warning": True,
                "current_warning": False, "acoustic_warning": False}
    score, state = calculate({}, warnings)
    assert score == 80
    print("PASS: health score 80")

def test_local_state():
    from fog.local_state import LocalStateStore
    s = LocalStateStore()
    p = {"machine_id":"m1","timestamp":"2026-01-01T00:00:00Z","window_seconds":10,
         "metrics":{},"anomalies":{"vibration_alert":True},"health_score":30,"machine_state":"critical"}
    s.update(p)
    assert s.get_latest("m1")["health_score"] == 30
    assert len(s.get_history("m1")) == 1
    assert len(s.get_alerts()) == 1
    print("PASS: local state store")

def test_waveform_store():
    from fog.local_state import LocalStateStore
    s = LocalStateStore()
    for i in range(5):
        s.push_waveform("m1", "vibration", float(i), "2026-01-01T00:00:00Z")
    data = s.get_waveform("m1", "vibration")
    assert len(data) == 5
    assert data[2]["v"] == 2.0
    print("PASS: waveform store")

if __name__ == "__main__":
    tests = [test_aggregator, test_aggregator_mean, test_detection_clear,
             test_detection_critical, test_health_score_full, test_health_score_critical,
             test_health_score_warning, test_local_state, test_waveform_store]
    passed = failed = 0
    for t in tests:
        try:    t(); passed += 1
        except Exception as e: print(f"FAIL: {t.__name__} — {e}"); failed += 1
    print(f"\n{'='*40}\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
