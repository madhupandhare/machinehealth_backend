# Deploying to AWS Elastic Beanstalk with Docker

This guide walks through containerising IMHM and deploying it to AWS Elastic
Beanstalk (EBS) using a Docker single-container environment.

---

## Architecture After Deployment

```
Internet
   │
   ▼
EBS Load Balancer (port 80)
   │
   ▼
EC2 Instance (Docker container)
   ├── supervisord
   │     ├── sensors (×4)  ──► AWS IoT Core (port 8883 TLS)
   │     ├── fog_node      ──► AWS IoT Core (subscribe + publish)
   │     └── gunicorn      ──► Flask dashboard (port 5000 → 80)
   │
   └── /app/certs/  (AWS Secrets Manager or S3 presigned — see Step 4)
```

---

## Prerequisites

- AWS CLI installed and configured (`aws configure`)
- EB CLI installed: `pip install awsebcli`
- Docker Desktop running locally
- ECR repository created (or use Docker Hub)
- All AWS services from `docs/aws_setup.md` already configured

---

## Step 1 — Build and Test Locally

```bash
cd imhm-v2

# Copy your certs into the certs/ directory first
cp ~/Downloads/device.pem.crt       certs/
cp ~/Downloads/private.pem.key      certs/
cp ~/Downloads/AmazonRootCA1.pem    certs/

# Copy .env.example to .env and fill in values
cp .env.example .env
# Edit .env with your IOT_ENDPOINT, SNS_TOPIC_ARN, etc.

# Build and run with Docker Compose
docker compose up --build

# Open dashboard
open http://localhost:5000

# Verify health endpoint
curl http://localhost:5000/api/machines
```

---

## Step 2 — Push Image to Amazon ECR

```bash
# Set your account ID and region
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=eu-west-1
REPO_NAME=imhm

# Create ECR repository (one-time)
aws ecr create-repository --repository-name $REPO_NAME --region $REGION

# Authenticate Docker to ECR
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin \
  $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com

# Build, tag, and push
docker build -t imhm:latest .
docker tag imhm:latest $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME:latest
docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME:latest

echo "Image URI: $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME:latest"
```

---

## Step 3 — Update Dockerrun.aws.json

Open `Dockerrun.aws.json` and replace the `<YOUR_AWS_ACCOUNT_ID>` and
`<YOUR_REGION>` placeholders with your actual values:

```json
{
  "AWSEBDockerrunVersion": "1",
  "Image": {
    "Name": "123456789012.dkr.ecr.eu-west-1.amazonaws.com/imhm:latest",
    "Update": "true"
  },
  "Ports": [
    {
      "ContainerPort": "5000",
      "HostPort": "80"
    }
  ]
}
```

---

## Step 4 — Handle Certificates Securely on EBS

Never bake certificates into your Docker image. Use one of these approaches:

### Option A — AWS Secrets Manager (recommended)

1. **Secrets Manager → Store a new secret → Other type**
2. Create three secrets:
   - `imhm/cert/device_crt` → paste contents of `device.pem.crt`
   - `imhm/cert/private_key` → paste contents of `private.pem.key`
   - `imhm/cert/root_ca` → paste contents of `AmazonRootCA1.pem`

3. Add a `.ebextensions/03_certs.config` file:

```yaml
files:
  "/app/certs/device.pem.crt":
    mode: "000400"
    owner: root
    group: root
    content: |
      # This will be replaced by the container startup script
      PLACEHOLDER

container_commands:
  01_fetch_certs:
    command: |
      aws secretsmanager get-secret-value \
        --secret-id imhm/cert/device_crt \
        --query SecretString --output text \
        > /app/certs/device.pem.crt
      aws secretsmanager get-secret-value \
        --secret-id imhm/cert/private_key \
        --query SecretString --output text \
        > /app/certs/private.pem.key
      aws secretsmanager get-secret-value \
        --secret-id imhm/cert/root_ca \
        --query SecretString --output text \
        > /app/certs/AmazonRootCA1.pem
      chmod 400 /app/certs/*.pem* /app/certs/*.key
```

Add `secretsmanager:GetSecretValue` to the EBS instance role.

### Option B — S3 Private Bucket (simpler for testing)

```bash
# Upload certs to a private S3 bucket
aws s3 cp certs/device.pem.crt    s3://my-imhm-certs/certs/device.pem.crt
aws s3 cp certs/private.pem.key   s3://my-imhm-certs/certs/private.pem.key
aws s3 cp certs/AmazonRootCA1.pem s3://my-imhm-certs/certs/AmazonRootCA1.pem
```

Add a container_commands hook in `.ebextensions` to `aws s3 cp` the files down at startup.

---

## Step 5 — Initialise Elastic Beanstalk Application

```bash
cd imhm-v2

# Initialise EB app (run once)
eb init imhm-app \
  --platform "Docker running on 64bit Amazon Linux 2023" \
  --region eu-west-1

# This creates .elasticbeanstalk/config.yml
```

---

## Step 6 — Create the EBS Environment

```bash
# Create environment with a t3.small instance (sufficient for this workload)
eb create imhm-production \
  --instance-type t3.small \
  --single \
  --envvars IOT_ENDPOINT=your-endpoint-ats.iot.eu-west-1.amazonaws.com,\
AWS_REGION=eu-west-1,\
DYNAMO_LATEST_TABLE=MachineLatestStatus,\
DYNAMO_HISTORY_TABLE=MachineHistory,\
S3_BUCKET_NAME=my-imhm-archive,\
ENABLE_S3_ARCHIVE=true,\
SNS_TOPIC_ARN=arn:aws:sns:eu-west-1:123456789012:MachineCriticalAlerts

# Wait ~5 minutes for environment to launch
eb status
```

> Use `--single` for a project/demo to avoid load balancer costs.
> Remove it for production to get auto-scaling + health checks.

---

## Step 7 — Set Environment Variables (Alternative Method)

Instead of the CLI flags above, you can use `eb setenv`:

```bash
eb setenv \
  IOT_ENDPOINT=your-endpoint-ats.iot.eu-west-1.amazonaws.com \
  AWS_REGION=eu-west-1 \
  DYNAMO_LATEST_TABLE=MachineLatestStatus \
  DYNAMO_HISTORY_TABLE=MachineHistory \
  S3_BUCKET_NAME=my-imhm-archive \
  SNS_TOPIC_ARN=arn:aws:sns:eu-west-1:123456789012:MachineCriticalAlerts \
  ENABLE_S3_ARCHIVE=true \
  FLASK_DEBUG=false
```

---

## Step 8 — Configure the EBS Instance Role

The EC2 instances need IAM permissions to call AWS services from inside the container.

1. **IAM → Roles → aws-elasticbeanstalk-ec2-role**
2. **Attach policies:**
   - `AmazonDynamoDBFullAccess`
   - `AmazonS3FullAccess`
   - `AmazonSNSFullAccess`
   - `AmazonEC2ContainerRegistryReadOnly` (to pull from ECR)
   - `SecretsManagerReadWrite` (if using Option A for certs)

---

## Step 9 — Deploy

```bash
# Package and deploy (first deploy or after code changes)
eb deploy

# Check logs if something goes wrong
eb logs

# Open the deployed dashboard in your browser
eb open
```

---

## Step 10 — Redeploy After Code Changes

```bash
# Rebuild image and push to ECR
docker build -t imhm:latest .
docker tag imhm:latest $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/imhm:latest
docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/imhm:latest

# Trigger EBS redeploy
eb deploy
```

---

## Useful EBS Commands

```bash
eb status              # environment health and URL
eb health              # detailed instance health
eb logs                # stream recent logs
eb logs --all          # download full logs
eb ssh                 # SSH into the running instance
eb terminate           # destroy the environment (stops billing)
eb open                # open dashboard URL in browser
```

---

## Environment Health Checks

EBS uses the `/api/machines` endpoint (via the HEALTHCHECK in Dockerfile) to
monitor instance health. If the Flask app fails to start, EBS will show the
environment as **Degraded** and attempt to restart the instance.

---

## Cost Estimate (eu-west-1, single instance)

| Service | Estimated Monthly Cost |
|---------|----------------------|
| EBS t3.small (1 instance) | ~$15 |
| DynamoDB (on-demand, low traffic) | ~$1–3 |
| IoT Core (messages + rules) | ~$1–5 |
| S3 (archive storage) | ~$0.50 |
| SNS (email alerts) | Free tier |
| SQS | Free tier |
| Lambda (low invocations) | Free tier |
| **Total** | **~$18–25/month** |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Environment health: Degraded` | `eb logs` — check if certs are missing or IoT endpoint is wrong |
| Dashboard loads but shows no data | Fog node can't connect to IoT Core — check cert paths and policy |
| SNS alert not received | Check SNS subscription is confirmed; check Lambda `SNS_TOPIC_ARN` env var |
| `eb deploy` fails with ECR error | Re-authenticate: `aws ecr get-login-password ... \| docker login ...` |
| High memory usage | Reduce sensor `publish_interval_seconds` or increase instance type |
