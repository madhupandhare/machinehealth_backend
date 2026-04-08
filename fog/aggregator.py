"""
fog/aggregator.py
------------------
Thread-safe rolling window aggregator.
Computes vibration RMS and mean for temperature, current, acoustic.
"""
import math, threading
from collections import deque
from datetime import datetime, timezone
from typing import Optional

class Aggregator:
    def __init__(self, window_seconds: int = 10):
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._windows: dict[str, deque] = {
            "vibration": deque(), "temperature": deque(),
            "current": deque(), "acoustic": deque(),
        }

    def ingest(self, sensor_type: str, value: float, timestamp_iso: str):
        try:
            ts = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = datetime.now(timezone.utc).timestamp()
        with self._lock:
            if sensor_type not in self._windows:
                return
            self._windows[sensor_type].append((ts, value))
            self._prune(sensor_type, ts)

    def _prune(self, sensor_type: str, now_ts: float):
        cutoff = now_ts - self.window_seconds
        dq = self._windows[sensor_type]
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def _values(self, t: str) -> list:
        return [v for _, v in self._windows.get(t, [])]

    @staticmethod
    def _rms(vals: list) -> Optional[float]:
        if not vals: return None
        return round(math.sqrt(sum(v*v for v in vals) / len(vals)), 4)

    @staticmethod
    def _mean(vals: list) -> Optional[float]:
        if not vals: return None
        return round(sum(vals) / len(vals), 4)

    def compute_metrics(self) -> dict:
        with self._lock:
            now = datetime.now(timezone.utc).timestamp()
            for s in self._windows: self._prune(s, now)
            vib = self._values("vibration")
            tmp = self._values("temperature")
            cur = self._values("current")
            aco = self._values("acoustic")
        return {
            "vibration_rms":   self._rms(vib),
            "avg_temperature": self._mean(tmp),
            "avg_current":     self._mean(cur),
            "avg_acoustic":    self._mean(aco),
        }

    def sample_counts(self) -> dict:
        with self._lock:
            return {k: len(v) for k, v in self._windows.items()}
