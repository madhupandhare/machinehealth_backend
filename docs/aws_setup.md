# AWS Setup Guide — IMHM v2

Complete walkthrough to configure every AWS service in the pipeline.

```
Sensors ──► IoT Core ──► IoT Rule ──► SQS ──► Lambda ──► DynamoDB
                                                     ├──► S3
                                                     └──► SNS ──► Email/SMS
```

---

## Step 1 — Create an IoT Core Thing

1. **AWS Console → IoT Core → Manage → Things → Create things**
2. Choose **Create single thing** → name it `imhm_machine_01`
3. **Device certificate** → Auto-generate → **Download all four files:**

   | Download | Save as |
   |----------|---------|
   | `xxxxx-certificate.pem.crt` | `certs/device.pem.crt` |
   | `xxxxx-private.pem.key` | `certs/private.pem.key` |
   | `AmazonRootCA1.pem` | `certs/AmazonRootCA1.pem` |

4. **Activate** the certificate.
5. **IoT Core → Settings** → copy your **Device data endpoint**
   e.g. `abc123-ats.iot.eu-west-1.amazonaws.com` → put in `.env` as `IOT_ENDPOINT`

---

## Step 2 — Create and Attach an IoT Policy

**IoT Core → Security → Policies → Create policy** — paste this JSON:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "iot:Connect",
      "Resource": "arn:aws:iot:REGION:ACCOUNT_ID:client/imhm-*"
    },
    {
      "Effect": "Allow",
      "Action": "iot:Publish",
      "Resource": [
        "arn:aws:iot:REGION:ACCOUNT_ID:topic/factory/machine_01/*",
        "arn:aws:iot:REGION:ACCOUNT_ID:topic/plant/processed/machine_health"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "iot:Subscribe",
      "Resource": [
        "arn:aws:iot:REGION:ACCOUNT_ID:topicfilter/factory/machine_01/#",
        "arn:aws:iot:REGION:ACCOUNT_ID:topicfilter/plant/processed/machine_health"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "iot:Receive",
      "Resource": [
        "arn:aws:iot:REGION:ACCOUNT_ID:topic/factory/machine_01/*",
        "arn:aws:iot:REGION:ACCOUNT_ID:topic/plant/processed/machine_health"
      ]
    }
  ]
}
```

Replace `REGION` and `ACCOUNT_ID`. Then attach this policy to your certificate.

---

## Step 3 — Create SQS Queue

1. **SQS → Create queue → Standard**
2. Name: `machine-health-queue`
3. Keep all defaults
4. Note the **Queue URL** and **ARN**

---

## Step 4 — Create IoT Rule (MQTT → SQS)

**IoT Core → Message Routing → Rules → Create rule**

- **Name:** `FogProcessedToSQS`
- **SQL statement** (routes only processed fog payloads):
  ```sql
  SELECT * FROM 'plant/processed/machine_health'
  ```
- **Action:** Send a message to an SQS queue
  - Queue: `machine-health-queue`
  - **Create a new IAM role** when prompted — AWS auto-creates it with `sqs:SendMessage`

> Optional: add a second action to also store raw payloads in S3 directly from IoT Core.

---

## Step 5 — Create DynamoDB Tables

### Table 1: MachineLatestStatus
| Field | Value |
|-------|-------|
| Table name | `MachineLatestStatus` |
| Partition key | `machine_id` (String) |
| Sort key | *(none)* |
| Billing mode | On-demand |

### Table 2: MachineHistory
| Field | Value |
|-------|-------|
| Table name | `MachineHistory` |
| Partition key | `machine_id` (String) |
| Sort key | `timestamp` (String) |
| Billing mode | On-demand |

---

## Step 6 — Create SNS Topic for Critical Alerts

1. **SNS → Topics → Create topic → Standard**
2. Name: `MachineCriticalAlerts`
3. Copy the **ARN** → put in `.env` as `SNS_TOPIC_ARN`
4. **Create subscription:**
   - Protocol: **Email** (or SMS for phone alerts)
   - Endpoint: your email address
   - Confirm the subscription from your inbox

When Lambda detects `machine_state = "critical"` it calls `sns.publish()` and every subscriber receives a full health report.

---

## Step 7 — Create S3 Archive Bucket

1. **S3 → Create bucket** → name e.g. `my-imhm-archive`
2. Block public access: **ON**
3. Copy bucket name → put in `.env` as `S3_BUCKET_NAME`

---

## Step 8 — Deploy Lambda Function

### Package the function
```bash
cd cloud/lambda_processor
pip install boto3 -t package/
cp lambda_function.py package/
cd package && zip -r ../lambda.zip . && cd ..
```

### Create Lambda
1. **Lambda → Create function → Author from scratch**
2. **Name:** `IMHMProcessor`
3. **Runtime:** Python 3.12
4. Upload `lambda.zip`
5. **Handler:** `lambda_function.lambda_handler`

### Lambda Environment Variables
| Key | Value |
|-----|-------|
| `DYNAMO_LATEST_TABLE` | `MachineLatestStatus` |
| `DYNAMO_HISTORY_TABLE` | `MachineHistory` |
| `S3_BUCKET` | `my-imhm-archive` |
| `ENABLE_S3_ARCHIVE` | `true` |
| `SNS_TOPIC_ARN` | `arn:aws:sns:eu-west-1:123456789012:MachineCriticalAlerts` |
| `AWS_REGION` | `eu-west-1` |

### Lambda Execution Role — attach these policies
- `AmazonDynamoDBFullAccess`
- `AmazonS3FullAccess`
- `AmazonSNSFullAccess`
- `AWSLambdaSQSQueueExecutionRole`

### Add SQS Trigger
1. **Lambda → Configuration → Triggers → Add trigger**
2. Source: **SQS** → Queue: `machine-health-queue`
3. Batch size: `10`
4. ✅ Enable **Report batch item failures**

---

## Step 9 — Test the End-to-End Pipeline

### Use IoT Core MQTT Test Client
**IoT Core → MQTT test client → Subscribe to topic:**
```
plant/processed/machine_health
```

Then start the fog node locally:
```bash
python fog/fog_node.py
```
Start sensors:
```bash
python sensors/vibration_sensor.py &
python sensors/temperature_sensor.py &
python sensors/current_sensor.py &
python sensors/acoustic_sensor.py &
```

You should see messages arriving in the MQTT Test Client within 10–15 seconds.

### Verify DynamoDB items
**DynamoDB → Tables → MachineLatestStatus → Explore table items**

Expected item:
```json
{
  "machine_id": "machine_01",
  "latest_timestamp": "2026-03-25T10:30:00Z",
  "health_score": 75,
  "machine_state": "healthy",
  "metrics": "{\"vibration_rms\": 7.2, \"avg_temperature\": 72.1, ...}",
  "anomalies": "{\"vibration_alert\": false, ...}",
  "window_seconds": 10
}
```

### Trigger a Critical Alert manually
```bash
# Publish a high-vibration fault reading directly to IoT Core
# (use the MQTT Test Client in the AWS console)
# Topic: factory/machine_01/vibration
# Payload:
{
  "machine_id": "machine_01",
  "sensor_type": "vibration",
  "timestamp": "2026-03-25T10:30:00Z",
  "value": 28.5,
  "unit": "mm/s",
  "status": "fault"
}
```
Publish this 10+ times within 10 seconds. The fog node will compute a critical health score, Lambda will detect `machine_state = "critical"` and publish to SNS — you should receive an email within seconds.

---

## Sample IoT Rule SQL Variants

**Route everything:**
```sql
SELECT * FROM 'plant/processed/machine_health'
```

**Route only critical/warning (reduces Lambda invocations):**
```sql
SELECT * FROM 'plant/processed/machine_health'
WHERE machine_state IN ('critical', 'warning')
```

**Route only with health score below 60:**
```sql
SELECT * FROM 'plant/processed/machine_health'
WHERE health_score < 60
```
