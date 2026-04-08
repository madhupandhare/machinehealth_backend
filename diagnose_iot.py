"""
diagnose_iot.py
---------------
Run this BEFORE fog_node.py to diagnose exactly why IoT Core is
rejecting the connection.

Usage:
    cd imhm-v2
    python diagnose_iot.py

It checks every known cause of rc=7 (NOT_AUTHORIZED) one by one
and tells you exactly what to fix.
"""

import os
import ssl
import socket
import sys
import time

# ── Try to load dotenv ────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("[OK] .env loaded via python-dotenv")
except ImportError:
    print("[WARN] python-dotenv not installed — reading env vars directly")

# ── Try to load yaml ──────────────────────────────────────────────────────────
try:
    import yaml
    with open("config.yaml") as f:
        raw = f.read()
    for k, v in os.environ.items():
        raw = raw.replace(f"${{{k}}}", v)
    cfg = yaml.safe_load(raw)
    iot_cfg = cfg.get("iot", {})
    print("[OK] config.yaml loaded")
except Exception as e:
    print(f"[FAIL] Cannot load config.yaml: {e}")
    sys.exit(1)

# ── Resolve values (env vars take priority over config.yaml) ─────────────────
endpoint    = os.environ.get("IOT_ENDPOINT") or iot_cfg.get("endpoint", "")
cert_path   = os.environ.get("PATH_TO_CERT") or iot_cfg.get("cert_path", "certs/device.pem.crt")
key_path    = os.environ.get("PATH_TO_PRIVATE_KEY") or iot_cfg.get("private_key_path", "certs/private.pem.key")
ca_path     = os.environ.get("PATH_TO_ROOT_CA") or iot_cfg.get("root_ca_path", "certs/AmazonRootCA1.pem")
client_id   = os.environ.get("CLIENT_ID") or "imhm-fog-node-01"
port        = 8883

print("\n" + "="*60)
print("IMHM — IoT Core Connection Diagnostics")
print("="*60)

# ── CHECK 1: Endpoint ─────────────────────────────────────────────────────────
print(f"\n[CHECK 1] IoT Endpoint")
if not endpoint or "${" in endpoint:
    print(f"  [FAIL] IOT_ENDPOINT is not set or still contains a placeholder.")
    print(f"         Value: '{endpoint}'")
    print(f"  FIX  : Set IOT_ENDPOINT in your .env file.")
    print(f"         Find it at: AWS Console → IoT Core → Settings → Device data endpoint")
    sys.exit(1)
else:
    print(f"  [OK]  endpoint = {endpoint}")

# ── CHECK 2: Certificate files exist ─────────────────────────────────────────
print(f"\n[CHECK 2] Certificate files")
missing = False
for label, path in [("device cert", cert_path), ("private key", key_path), ("root CA", ca_path)]:
    if os.path.isfile(path):
        size = os.path.getsize(path)
        print(f"  [OK]  {label}: {path}  ({size} bytes)")
        if size < 100:
            print(f"  [WARN] File is suspiciously small — may be empty or corrupt")
    else:
        print(f"  [FAIL] {label}: {path}  — FILE NOT FOUND")
        missing = True

if missing:
    print("\n  FIX  : Download certificates from AWS IoT Core:")
    print("         Console → IoT Core → Manage → Things → your_thing → Certificates")
    print("         Or create a new certificate: Security → Certificates → Create")
    print(f"         Place files as:")
    print(f"           {cert_path}")
    print(f"           {key_path}")
    print(f"           {ca_path}")
    sys.exit(1)

# ── CHECK 3: Certificate content format ──────────────────────────────────────
print(f"\n[CHECK 3] Certificate content validity")
with open(cert_path) as f:
    cert_content = f.read()
with open(key_path) as f:
    key_content = f.read()
with open(ca_path) as f:
    ca_content = f.read()

if "-----BEGIN CERTIFICATE-----" not in cert_content:
    print(f"  [FAIL] {cert_path} does not look like a PEM certificate.")
    print(f"         First 80 chars: {cert_content[:80]!r}")
    print(f"  FIX  : Re-download the device certificate from IoT Core.")
    sys.exit(1)
print(f"  [OK]  device cert is valid PEM")

if "-----BEGIN RSA PRIVATE KEY-----" not in key_content and "-----BEGIN PRIVATE KEY-----" not in key_content and "-----BEGIN EC PRIVATE KEY-----" not in key_content:
    print(f"  [FAIL] {key_path} does not look like a PEM private key.")
    print(f"         First 80 chars: {key_content[:80]!r}")
    print(f"  FIX  : Re-download the private key from IoT Core (only available once at creation).")
    sys.exit(1)
print(f"  [OK]  private key is valid PEM")

if "-----BEGIN CERTIFICATE-----" not in ca_content:
    print(f"  [FAIL] {ca_path} does not look like a valid CA certificate.")
    print(f"  FIX  : Download AmazonRootCA1.pem from:")
    print(f"         https://www.amazontrust.com/repository/AmazonRootCA1.pem")
    sys.exit(1)
print(f"  [OK]  root CA is valid PEM")

# ── CHECK 4: TLS handshake (can we even reach the endpoint?) ─────────────────
print(f"\n[CHECK 4] Network connectivity to {endpoint}:8883")
try:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=ca_path)
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    with socket.create_connection((endpoint, port), timeout=10) as sock:
        with context.wrap_socket(sock, server_hostname=endpoint) as ssock:
            print(f"  [OK]  TLS handshake succeeded!")
            print(f"        Cipher: {ssock.cipher()}")
            print(f"        TLS version: {ssock.version()}")
except ssl.SSLCertVerificationError as e:
    print(f"  [FAIL] TLS certificate verification failed: {e}")
    print(f"  FIX  : The device certificate may not match the endpoint's region.")
    print(f"         Ensure your endpoint and certificates are from the SAME AWS region.")
    sys.exit(1)
except ssl.SSLError as e:
    print(f"  [FAIL] TLS/SSL error: {e}")
    print(f"  FIX  : The certificate and private key may not be a matching pair.")
    print(f"         Re-download both from the SAME IoT Core certificate.")
    sys.exit(1)
except socket.timeout:
    print(f"  [FAIL] Connection timed out")
    print(f"  FIX  : Check your internet connection and firewall. Port 8883 must be open.")
    sys.exit(1)
except socket.gaierror as e:
    print(f"  [FAIL] DNS lookup failed for {endpoint}: {e}")
    print(f"  FIX  : Check IOT_ENDPOINT in your .env — it may be misspelled.")
    print(f"         Expected format: xxxxxx-ats.iot.REGION.amazonaws.com")
    sys.exit(1)
except Exception as e:
    print(f"  [FAIL] Unexpected error: {e}")
    sys.exit(1)

# ── CHECK 5: MQTT connect with paho ──────────────────────────────────────────
print(f"\n[CHECK 5] MQTT connect to IoT Core (paho-mqtt)")
try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("  [FAIL] paho-mqtt not installed. Run: pip install paho-mqtt")
    sys.exit(1)

connect_result = {"rc": None, "error": None}

def on_connect(client, userdata, flags, rc):
    connect_result["rc"] = rc

def on_disconnect(client, userdata, rc):
    if connect_result["rc"] is None:
        connect_result["error"] = rc

client = mqtt.Client(client_id=client_id, clean_session=True)
client.tls_set(ca_certs=ca_path, certfile=cert_path, keyfile=key_path,
               tls_version=ssl.PROTOCOL_TLS_CLIENT)
client.tls_insecure_set(False)
client.on_connect = on_connect
client.on_disconnect = on_disconnect

try:
    client.connect(endpoint, port, keepalive=60)
    client.loop_start()
    for _ in range(30):           # wait up to 6 seconds
        if connect_result["rc"] is not None:
            break
        time.sleep(0.2)
    client.loop_stop()
    client.disconnect()
except Exception as e:
    print(f"  [FAIL] Could not attempt MQTT connect: {e}")
    sys.exit(1)

rc = connect_result["rc"]
if rc == 0:
    print(f"  [OK]  MQTT connected successfully (rc=0) with client_id='{client_id}'")
elif rc == 1:
    print(f"  [FAIL] rc=1: Unacceptable protocol version")
elif rc == 2:
    print(f"  [FAIL] rc=2: Identifier rejected")
    print(f"  FIX  : The client_id '{client_id}' may conflict with an existing connection.")
    print(f"         Try a different CLIENT_ID in your .env file.")
elif rc == 3:
    print(f"  [FAIL] rc=3: Server unavailable — IoT Core endpoint may be wrong region")
elif rc == 4:
    print(f"  [FAIL] rc=4: Bad username or password")
elif rc == 5:
    print(f"  [FAIL] rc=5: Not authorised — IoT policy is not attached to the certificate")
    print(f"  FIX  : AWS Console → IoT Core → Security → Certificates")
    print(f"         Find your certificate → Policies tab → Attach a policy")
elif rc == 7 or rc is None:
    print(f"  [FAIL] rc=7 or no response: Connection refused / not authorised")
    print()
    print("  Most common causes of rc=7:")
    print()
    print("  A) Certificate is NOT ACTIVE in IoT Core")
    print("     → Console → IoT Core → Security → Certificates")
    print("       Find your cert → Actions → Activate")
    print()
    print("  B) IoT POLICY is not attached to the certificate")
    print("     → Console → IoT Core → Security → Certificates")
    print("       Click your cert → Policies tab → Attach policy")
    print("       (create the policy from docs/aws_setup.md Step 2 if needed)")
    print()
    print("  C) The Thing is not attached to the certificate")
    print("     → Console → IoT Core → Security → Certificates")
    print("       Click your cert → Things tab → Attach thing")
    print()
    print("  D) Wrong endpoint for your region")
    print(f"     → Your endpoint: {endpoint}")
    print(f"     → Check: AWS Console → IoT Core → Settings → Device data endpoint")
    print(f"       It must match the region where your Thing was created.")
    print()
    print("  E) client_id in MQTT policy resource doesn't match")
    print(f"     → Your client_id: {client_id}")
    print(f"     → IoT policy Resource should be: arn:aws:iot:REGION:ACCOUNT:client/imhm-*")
    print(f"       OR change it to: arn:aws:iot:REGION:ACCOUNT:client/{client_id}")
else:
    print(f"  [FAIL] rc={rc} — unknown error")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "="*60)
if rc == 0:
    print("ALL CHECKS PASSED — IoT Core connection is working.")
    print("You can now run: python fog/fog_node.py")
else:
    print("CONNECTION FAILED — follow the FIX instructions above.")
    print("After fixing, run this script again to verify.")
print("="*60 + "\n")
