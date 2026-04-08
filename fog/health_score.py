"""
fog/health_score.py
--------------------
Health score (0–100) and machine state classification.
  -10 per warning flag
  -25 per critical flag
  ≥75 → healthy | 40-74 → warning | <40 → critical
"""

WARNING_DEDUCTION  = 10
CRITICAL_DEDUCTION = 25

def calculate(anomalies: dict, warnings: dict) -> tuple[int, str]:
    score = 100
    for v in warnings.values():
        if v: score -= WARNING_DEDUCTION
    for v in anomalies.values():
        if v: score -= CRITICAL_DEDUCTION
    score = max(0, min(100, score))
    if score >= 75:   state = "healthy"
    elif score >= 40: state = "warning"
    else:             state = "critical"
    return score, state
