# Industrial Machine Health Monitor v2
### Fog & Edge Computing — AWS IoT Core + SNS + Docker + EBS

A complete Python project demonstrating Fog Computing for industrial machine
health monitoring. **No Mosquitto** — all MQTT flows directly through AWS IoT Core.

```
┌──────────────────────────────────────────────────────────┐
│                    EDGE / FOG LAYER                      │
│                                                          │
│  vibration_sensor.py ─┐                                  │
│  temperature_sensor.py─┤──► AWS IoT Core (TLS 8883)      │
│  current_sensor.py  ──┤       topic: factory/machine_01/# │
│  acoustic_sensor.py ──┘                                  │
│                                ▼                         │
│                fog_node.py (subscribes + aggregates)     │
│                  │  rolling window RMS + means           │
│                  │  threshold anomaly detection          │
│                  │  health score 0–100                   │
│                  └──► IoT Core topic: plant/processed/.. │
└──────────────────────────────────────────────────────────┘
                         │
                    IoT Rule (SQL)
                         │
                    ┌────▼────┐
                    │   SQS   │  machine-health-queue
                    └────┬────┘
                         │
                    ┌────▼──────────────────────┐
                    │   Lambda (IMHMProcessor)  │
                    │  ├─ DynamoDB: LatestStatus│
                    │  ├─ DynamoDB: History     │
                    │  ├─ S3: archive           │
                    │  └─ SNS ──► Email/SMS     │  ← CRITICAL alerts
                    └───────────────────────────┘
                         │
                    ┌────▼─────────────────────────┐
                    │  Flask Dashboard + REST API  │
                    │  Sinusoidal waveform charts  │
                    │  Deployed on EBS via Docker  │
                    └──────────────────────────────┘
```

---

## What Changed from v1

| Feature | v1 | v2 |
|---------|----|----|
| MQTT broker | Local Mosquitto | AWS IoT Core (direct) |
| Sensor connection | `localhost:1883` | `endpoint:8883` (TLS) |
| Critical alerts | None | AWS SNS → email/SMS |
| Waveform charts | Aggregated line charts | Sinusoidal oscilloscope display |
| Deployment | Local Python only | Docker + AWS Elastic Beanstalk |

---

## Project Structure

```
imhm-v2/
│
├── config.yaml                    Central config (sensor ranges, thresholds, AWS)
├── iot_client.py                  Shared IoT Core connection factory
├── requirements.txt
├── .env.example
├── .gitignore
├── run_local.py                   One-command local launcher
├── Dockerfile
├── docker-compose.yml             Local Docker testing
├── Dockerrun.aws.json             EBS Docker config
│
├── .ebextensions/
│   ├── 01_env.config              EBS environment variables
│   └── 02_iam.config              EBS instance IAM policy reference
│
├── docker/
│   └── supervisord.conf           Runs all 6 processes inside one container
│
├── certs/                         AWS IoT certificates (gitignored)
│   └── README.md
│
├── sensors/
│   ├── vibration_sensor.py        → IoT Core: factory/machine_01/vibration
│   ├── temperature_sensor.py      → IoT Core: factory/machine_01/temperature
│   ├── current_sensor.py          → IoT Core: factory/machine_01/current
│   └── acoustic_sensor.py         → IoT Core: factory/machine_01/acoustic
│
├── fog/
│   ├── fog_node.py                Main orchestrator
│   ├── aggregator.py              Thread-safe rolling window
│   ├── detection.py               Threshold anomaly detection
│   ├── health_score.py            0–100 scoring
│   └── local_state.py             In-memory store + waveform buffers
│
├── cloud/
│   ├── lambda_processor/
│   │   └── lambda_function.py     SQS → DynamoDB + S3 + SNS
│   └── api/
│       ├── app.py                 Flask factory
│       └── routes.py              REST endpoints incl. /waveform/<sensor>
│
├── dashboard/
│   ├── views.py
│   └── templates/
│       ├── base.html              Oscilloscope industrial dark theme
│       ├── overview.html          Machine cards with health score rings
│       ├── machine_detail.html    4 sinusoidal waveforms + score timeline
│       └── 404.html
│
├── docs/
│   ├── aws_setup.md               IoT Core + SQS + Lambda + DynamoDB + SNS
│   └── ebs_deploy.md              Docker → ECR → Elastic Beanstalk
│
└── tests/
    └── test_smoke.py              9 unit tests (all logic, no AWS needed)
```

---

## Implementation Order

Follow this sequence when building or studying the project:

1. `config.yaml` — understand all parameters first
2. `iot_client.py` — the single connection factory used everywhere
3. `sensors/vibration_sensor.py` — understand the sensor loop; others are identical
4. `fog/aggregator.py` — rolling window and RMS/mean logic
5. `fog/detection.py` — threshold rules
6. `fog/health_score.py` — scoring formula
7. `fog/local_state.py` — in-memory store including waveform buffers
8. `fog/fog_node.py` — assembles all fog modules
9. `cloud/lambda_processor/lambda_function.py` — SQS handler with SNS alerts
10. `cloud/api/routes.py` — REST API including `/waveform/<sensor>`
11. `dashboard/templates/machine_detail.html` — oscilloscope waveform charts
12. `Dockerfile` + `docker/supervisord.conf` — containerisation
13. `docs/aws_setup.md` — connect to real AWS
14. `docs/ebs_deploy.md` — deploy to Elastic Beanstalk

---

## Quick Start — Local (No Docker)

### 1. Install dependencies
```bash
cd imhm-v2
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Place AWS IoT certificates
```bash
# Copy the three files downloaded from IoT Core into:
certs/device.pem.crt
certs/private.pem.key
certs/AmazonRootCA1.pem
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env — fill in IOT_ENDPOINT, SNS_TOPIC_ARN, etc.
```

### 4. Start everything
```bash
python run_local.py
# Open http://localhost:5000
```

---

## Quick Start — Docker (Local)

```bash
# Ensure certs/ is populated and .env is filled in
docker compose up --build
# Open http://localhost:5000
```

---

## Quick Start — Deploy to EBS

```bash
# See docs/ebs_deploy.md for full walkthrough
eb init imhm-app --platform "Docker running on 64bit Amazon Linux 2023" --region eu-west-1
eb create imhm-production --instance-type t3.small --single
eb setenv IOT_ENDPOINT=xxx SNS_TOPIC_ARN=xxx ...
eb deploy
eb open
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/machines` | All machine summaries |
| GET | `/api/machines/<id>/latest` | Latest processed payload |
| GET | `/api/machines/<id>/history?limit=60` | Historical records |
| GET | `/api/machines/<id>/waveform/<sensor>` | Raw waveform data for oscilloscope |
| GET | `/api/alerts?limit=30` | Recent anomaly events |
| GET | `/dashboard` | HTML overview |
| GET | `/dashboard/<id>` | HTML machine detail + waveforms |

---

## Health Score Logic

| Condition | Points |
|-----------|--------|
| Warning threshold exceeded | −10 per sensor |
| Critical threshold exceeded | −25 per sensor |

Score clamped to [0, 100]. State: **≥75 healthy · 40–74 warning · <40 critical**

When state is **critical**, Lambda automatically publishes a full health report
to SNS → all subscribers receive an email/SMS within seconds.

---

## MQTT Topics

| Topic | Publisher | Consumer |
|-------|-----------|----------|
| `factory/machine_01/vibration` | vibration_sensor | fog_node |
| `factory/machine_01/temperature` | temperature_sensor | fog_node |
| `factory/machine_01/current` | current_sensor | fog_node |
| `factory/machine_01/acoustic` | acoustic_sensor | fog_node |
| `plant/processed/machine_health` | fog_node | IoT Rule → SQS → Lambda |

---

## Sinusoidal Waveform Display

The machine detail dashboard shows four **oscilloscope-style** waveform charts:

- **Live mode:** the `/api/machines/<id>/waveform/<sensor>` endpoint streams
  raw individual readings stored by the fog node on every MQTT message.
- **Fallback mode:** when fewer than 10 live points exist, the dashboard
  generates sinusoidal interpolation by expanding each 10-second aggregated
  window into 10 sine sub-samples centred on the window mean. This produces
  realistic-looking waveforms immediately upon startup.

Threshold lines (warning = dashed yellow, critical = dashed red) are overlaid
on every waveform channel.

---

## SNS Alert Format

When `machine_state == "critical"`, Lambda sends this to all SNS subscribers:

```
Subject: 🚨 CRITICAL ALERT — Machine machine_01 Health Score: 25/100

INDUSTRIAL MACHINE CRITICAL HEALTH ALERT
==========================================
Machine ID    : machine_01
Timestamp     : 2026-03-25T10:35:00Z
Health Score  : 25 / 100
Machine State : CRITICAL

--- Aggregated Metrics (last 10s) ---
Vibration RMS   : 22.3 mm/s
Avg Temperature : 108.5 °C
Avg Current     : 18.2 A
Avg Acoustic    : 88.1 dB

--- Active Anomalies ---
  ⚠ VIBRATION ALERT
  ⚠ TEMPERATURE ALERT

Immediate inspection is recommended.
```

---

## Tests

```bash
python tests/test_smoke.py
# 9 passed, 0 failed (no AWS credentials needed)
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `FileNotFoundError: certs/device.pem.crt` | Place IoT Core cert files in `certs/` |
| Fog node logs "No sensor data yet" | Sensors not connecting — check `IOT_ENDPOINT` in `.env` |
| SNS email not arriving | Check subscription is confirmed; verify `SNS_TOPIC_ARN` in Lambda env vars |
| Dashboard shows no machines | Wait 10s after starting fog node; check `/api/machines` JSON |
| EBS environment Degraded | `eb logs` — usually a missing env var or cert |
| Docker build fails | Ensure `docker/supervisord.conf` exists and `supervisor` apt package installs |
