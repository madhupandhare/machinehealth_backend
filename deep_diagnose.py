"""
deep_diagnose.py
----------------
Reads your actual AWS IoT configuration using boto3 (AWS SDK)
and tells you EXACTLY what is wrong with your certificate/policy setup.

Usage:
    cd imhm-v2
    python deep_diagnose.py

Requirements: pip install boto3 python-dotenv
"""

import os
import sys
import json
import ssl
import time

# ── Load .env ─────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Check boto3 ───────────────────────────────────────────────────────────────
try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    print("[FAIL] boto3 not installed. Run:  pip install boto3")
    sys.exit(1)

REGION        = os.environ.get("AWS_REGION", "us-east-1")
CERT_ID       = "c6ed87251673df79e60e1c4f3a8388b6ab0dd059d36746c40a73889ed887b264"
ENDPOINT      = os.environ.get("IOT_ENDPOINT", "")
CERT_PATH     = os.environ.get("PATH_TO_CERT",        "certs/device.pem.crt")
KEY_PATH      = os.environ.get("PATH_TO_PRIVATE_KEY", "certs/private.pem.key")
CA_PATH       = os.environ.get("PATH_TO_ROOT_CA",     "certs/AmazonRootCA1.pem")
CLIENT_ID     = "imhm-fog-node-01"

print("=" * 62)
print("  IMHM — Deep IoT Core Diagnostic")
print(f"  Region   : {REGION}")
print(f"  Cert ID  : {CERT_ID[:16]}...")
print(f"  Endpoint : {ENDPOINT}")
print("=" * 62)

# ── Create IoT client ─────────────────────────────────────────────────────────
try:
    iot = boto3.client("iot", region_name=REGION)
    # Quick credential check
    sts = boto3.client("sts", region_name=REGION)
    identity = sts.get_caller_identity()
    account_id = identity["Account"]
    print(f"\n[OK] AWS credentials working. Account: {account_id}")
except NoCredentialsError:
    print("\n[FAIL] No AWS credentials found.")
    print("  FIX: Add to your .env file:")
    print("       AWS_ACCESS_KEY_ID=your_key")
    print("       AWS_SECRET_ACCESS_KEY=your_secret")
    print("       AWS_REGION=us-east-1")
    sys.exit(1)
except Exception as e:
    print(f"\n[FAIL] AWS error: {e}")
    sys.exit(1)

all_ok = True

# ── CHECK A: Certificate status ───────────────────────────────────────────────
print(f"\n{'─'*62}")
print("CHECK A: Certificate Status")
print(f"{'─'*62}")
try:
    resp   = iot.describe_certificate(certificateId=CERT_ID)
    cert   = resp["certificateDescription"]
    status = cert["status"]
    arn    = cert["certificateArn"]

    if status == "ACTIVE":
        print(f"  [OK]  Certificate is ACTIVE")
        print(f"        ARN: {arn}")
    else:
        print(f"  [FAIL] Certificate status = '{status}'  (must be ACTIVE)")
        print(f"  FIX  : Run this command to activate it:")
        print(f"         aws iot update-certificate --certificate-id {CERT_ID} --new-status ACTIVE --region {REGION}")
        print(f"  OR   : Console → IoT Core → Security → Certificates → Actions → Activate")
        all_ok = False
except ClientError as e:
    print(f"  [FAIL] Could not read certificate: {e}")
    all_ok = False

# ── CHECK B: Policies attached to certificate ─────────────────────────────────
print(f"\n{'─'*62}")
print("CHECK B: Policies Attached to Certificate")
print(f"{'─'*62}")
try:
    resp     = iot.list_attached_policies(target=f"arn:aws:iot:{REGION}:{account_id}:cert/{CERT_ID}")
    policies = resp.get("policies", [])

    if not policies:
        print(f"  [FAIL] No policies attached to this certificate!")
        print(f"  FIX  : Run these commands:")
        print(f"\n  Step 1 — Create the policy:")
        policy_doc = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": ["iot:Connect","iot:Publish","iot:Subscribe","iot:Receive"],
                "Resource": "*"
            }]
        })
        print(f'  aws iot create-policy --policy-name "IMHM-Open-Policy" --policy-document \'{policy_doc}\' --region {REGION}')
        print(f"\n  Step 2 — Attach the policy:")
        print(f'  aws iot attach-policy --policy-name "IMHM-Open-Policy" --target "arn:aws:iot:{REGION}:{account_id}:cert/{CERT_ID}" --region {REGION}')
        all_ok = False
    else:
        print(f"  [OK]  {len(policies)} policy/policies attached:")
        for p in policies:
            print(f"        → {p['policyName']}")

        # Check each policy for required actions
        print(f"\n  Checking policy contents...")
        required_actions = {"iot:Connect", "iot:Publish", "iot:Subscribe", "iot:Receive"}
        found_actions = set()
        has_wildcard_resource = False

        for p in policies:
            pname = p["policyName"]
            try:
                pv = iot.get_policy(policyName=pname)
                doc = json.loads(pv["policyDocument"])
                print(f"\n  Policy: {pname}")
                print(f"  Document:")
                print("  " + json.dumps(doc, indent=2).replace("\n", "\n  "))

                for stmt in doc.get("Statement", []):
                    if stmt.get("Effect") != "Allow":
                        continue
                    actions  = stmt.get("Action", [])
                    resource = stmt.get("Resource", "")
                    if isinstance(actions, str):
                        actions = [actions]
                    for a in actions:
                        if a == "iot:*" or a == "*":
                            found_actions |= required_actions
                        else:
                            found_actions.add(a)
                    if resource == "*" or isinstance(resource, list) and "*" in resource:
                        has_wildcard_resource = True

            except Exception as e:
                print(f"  [WARN] Could not read policy {pname}: {e}")

        missing = required_actions - found_actions
        if missing:
            print(f"\n  [FAIL] Missing IoT actions in policy: {missing}")
            print(f"  FIX  : Update the policy to include all 4 actions:")
            print(f"         iot:Connect, iot:Publish, iot:Subscribe, iot:Receive")
            print(f"         with Resource: \"*\"")
            all_ok = False
        else:
            print(f"\n  [OK]  All required IoT actions are present")

        # Check Connect resource matches client_id
        print(f"\n  Checking iot:Connect resource allows client_id='{CLIENT_ID}'...")
        connect_ok = False
        for p in policies:
            pname = p["policyName"]
            try:
                pv  = iot.get_policy(policyName=pname)
                doc = json.loads(pv["policyDocument"])
                for stmt in doc.get("Statement", []):
                    if stmt.get("Effect") != "Allow":
                        continue
                    actions  = stmt.get("Action", [])
                    resource = stmt.get("Resource", "")
                    if isinstance(actions, str): actions = [actions]
                    if isinstance(resource, str): resource = [resource]
                    has_connect = any(a in ("iot:Connect","iot:*","*") for a in actions)
                    if has_connect:
                        for r in resource:
                            if r == "*" or r.endswith(f":client/{CLIENT_ID}") or r.endswith(":client/imhm-*") or r.endswith(":client/*"):
                                connect_ok = True
                            elif ":client/" in r:
                                allowed_client = r.split(":client/")[-1]
                                print(f"  [WARN] iot:Connect resource allows client '{allowed_client}'")
                                print(f"         but this app uses client_id='{CLIENT_ID}'")
                                print(f"  FIX  : Change policy resource to: arn:aws:iot:{REGION}:{account_id}:client/*")
                                print(f"         OR change resource to:     *")
                                all_ok = False
            except Exception:
                pass

        if connect_ok:
            print(f"  [OK]  iot:Connect resource allows this client_id")

except ClientError as e:
    print(f"  [FAIL] Could not list policies: {e}")
    all_ok = False

# ── CHECK C: Things attached to certificate ───────────────────────────────────
print(f"\n{'─'*62}")
print("CHECK C: Things Attached to Certificate")
print(f"{'─'*62}")
try:
    resp   = iot.list_principal_things(principal=f"arn:aws:iot:{REGION}:{account_id}:cert/{CERT_ID}")
    things = resp.get("things", [])
    if not things:
        print(f"  [WARN] No Things attached to this certificate.")
        print(f"         This is usually NOT the cause of rc=7,")
        print(f"         but attach one anyway for best practice:")
        print(f"  FIX  : Console → IoT Core → Security → Certificates → your cert → Things → Attach")
    else:
        print(f"  [OK]  {len(things)} Thing(s) attached: {things}")
except ClientError as e:
    print(f"  [WARN] Could not list things: {e}")

# ── CHECK D: IoT endpoint matches region ──────────────────────────────────────
print(f"\n{'─'*62}")
print("CHECK D: IoT Endpoint vs Region")
print(f"{'─'*62}")
try:
    ep_resp  = iot.describe_endpoint(endpointType="iot:Data-ATS")
    real_ep  = ep_resp["endpointAddress"]
    print(f"  Real endpoint for account: {real_ep}")
    print(f"  Your .env IOT_ENDPOINT   : {ENDPOINT}")
    if real_ep == ENDPOINT:
        print(f"  [OK]  Endpoints match")
    else:
        print(f"  [FAIL] ENDPOINT MISMATCH!")
        print(f"  FIX  : Update your .env file:")
        print(f"         IOT_ENDPOINT={real_ep}")
        all_ok = False
except Exception as e:
    print(f"  [WARN] Could not verify endpoint: {e}")

# ── CHECK E: Test MQTT connection ─────────────────────────────────────────────
print(f"\n{'─'*62}")
print("CHECK E: Live MQTT Connection Test")
print(f"{'─'*62}")

import paho.mqtt.client as mqtt

connect_rc = {"value": None}

def _on_connect(c, u, f, rc):
    connect_rc["value"] = rc

try:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=CA_PATH)
    ctx.load_cert_chain(certfile=CERT_PATH, keyfile=KEY_PATH)
    ctx.check_hostname = True
    ctx.verify_mode    = ssl.CERT_REQUIRED

    c = mqtt.Client(client_id=CLIENT_ID, clean_session=True, protocol=mqtt.MQTTv311)
    c.tls_set_context(ctx)
    c.on_connect = _on_connect
    c.connect(ENDPOINT, 8883, 60)
    c.loop_start()
    for _ in range(40):
        if connect_rc["value"] is not None:
            break
        time.sleep(0.25)
    c.loop_stop()
    c.disconnect()

    rc = connect_rc["value"]
    if rc == 0:
        print(f"  [OK]  MQTT CONNECTED SUCCESSFULLY (rc=0)")
        print(f"        client_id='{CLIENT_ID}' is accepted by IoT Core")
    else:
        print(f"  [FAIL] MQTT connect returned rc={rc}")
        if rc == 7:
            print(f"\n  rc=7 still means the policy/certificate issue above is not resolved.")
            print(f"  Follow the FIX instructions printed above and run this script again.")
except Exception as e:
    print(f"  [FAIL] Exception: {e}")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*62}")
if all_ok and connect_rc.get("value") == 0:
    print("  ALL CHECKS PASSED — run:  python fog/fog_node.py")
else:
    print("  ACTION REQUIRED — follow the FIX steps printed above.")
    print("  After making changes, run:  python deep_diagnose.py")
    print()
    print("  If you prefer CLI fixes, copy the 'aws iot ...' commands")
    print("  printed above and run them in your terminal.")
print(f"{'='*62}\n")