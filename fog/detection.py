"""
fog/detection.py
-----------------
Threshold-based anomaly and warning detection.
"""
from typing import Optional

class AnomalyDetector:
    def __init__(self, thresholds: dict):
        self.t = thresholds

    def detect(self, metrics: dict) -> dict:
        """Return critical-level anomaly flags."""
        return {
            "vibration_alert":   self._check(metrics.get("vibration_rms"),   self.t.get("vibration_rms_critical", 16.0)),
            "temperature_alert": self._check(metrics.get("avg_temperature"),  self.t.get("temperature_critical",  100.0)),
            "overload_alert":    self._check(metrics.get("avg_current"),       self.t.get("current_critical",       28.0)),
            "acoustic_alert":    self._check(metrics.get("avg_acoustic"),      self.t.get("acoustic_critical",      90.0)),
        }

    def detect_warnings(self, metrics: dict) -> dict:
        """Return warning-level flags (below critical)."""
        return {
            "vibration_warning":   self._check(metrics.get("vibration_rms"),  self.t.get("vibration_rms_warning", 10.0)),
            "temperature_warning": self._check(metrics.get("avg_temperature"), self.t.get("temperature_warning",  88.0)),
            "current_warning":     self._check(metrics.get("avg_current"),     self.t.get("current_warning",      22.0)),
            "acoustic_warning":    self._check(metrics.get("avg_acoustic"),    self.t.get("acoustic_warning",     78.0)),
        }

    @staticmethod
    def _check(value: Optional[float], threshold: float) -> bool:
        return value is not None and value > threshold
