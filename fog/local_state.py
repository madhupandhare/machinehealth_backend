"""
fog/local_state.py
-------------------
Thread-safe in-memory store.
Holds latest status, rolling history, and recent alert events.
Used by the Flask dashboard API.
"""
import threading
from collections import deque
from typing import Optional


class LocalStateStore:
    def __init__(self, max_history: int = 300, max_alerts: int = 100):
        self._lock = threading.Lock()
        self._latest: dict[str, dict] = {}
        self._history: dict[str, deque] = {}
        self._max_history = max_history
        self._alerts: deque = deque(maxlen=max_alerts)
        # Raw per-sensor waveform buffers for sinusoidal display (last 120 pts each)
        self._waveforms: dict[str, dict[str, deque]] = {}

    def update(self, payload: dict):
        mid = payload.get("machine_id", "unknown")
        with self._lock:
            self._latest[mid] = payload
            if mid not in self._history:
                self._history[mid] = deque(maxlen=self._max_history)
            self._history[mid].append(payload)
            anomalies = payload.get("anomalies", {})
            if any(anomalies.values()):
                self._alerts.append({
                    "machine_id":      mid,
                    "timestamp":       payload.get("timestamp"),
                    "health_score":    payload.get("health_score"),
                    "machine_state":   payload.get("machine_state"),
                    "active_anomalies": [k for k, v in anomalies.items() if v],
                })

    def push_waveform(self, machine_id: str, sensor_type: str, value: float, timestamp: str):
        """Store individual raw sensor readings for waveform charts."""
        with self._lock:
            if machine_id not in self._waveforms:
                self._waveforms[machine_id] = {}
            if sensor_type not in self._waveforms[machine_id]:
                self._waveforms[machine_id][sensor_type] = deque(maxlen=120)
            self._waveforms[machine_id][sensor_type].append({"t": timestamp, "v": value})

    def get_waveform(self, machine_id: str, sensor_type: str) -> list:
        with self._lock:
            dq = self._waveforms.get(machine_id, {}).get(sensor_type, deque())
            return list(dq)

    def get_all_machines(self) -> list:
        with self._lock:
            return [
                {"machine_id": mid, "health_score": d.get("health_score"),
                 "machine_state": d.get("machine_state"), "timestamp": d.get("timestamp")}
                for mid, d in self._latest.items()
            ]

    def get_latest(self, machine_id: str) -> Optional[dict]:
        with self._lock:
            return self._latest.get(machine_id)

    def get_history(self, machine_id: str, limit: int = 60) -> list:
        with self._lock:
            return list(self._history.get(machine_id, []))[-limit:]

    def get_alerts(self, limit: int = 30) -> list:
        with self._lock:
            return list(self._alerts)[-limit:]


state_store = LocalStateStore()
