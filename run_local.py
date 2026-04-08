#!/usr/bin/env python3
"""
run_local.py
------------
Launches all sensors + fog node + dashboard as colour-coded subprocesses.
Useful for local development WITHOUT Docker.

Usage:
    cd imhm-v2
    python run_local.py
"""
import signal, subprocess, sys, threading, time

PROCS = [
    ("FOG ", "fog/fog_node.py",               "\033[96m"),
    ("DASH", "cloud/api/app.py",              "\033[97m"),
    ("VIB ", "sensors/vibration_sensor.py",   "\033[93m"),
    ("TMP ", "sensors/temperature_sensor.py", "\033[92m"),
    ("CUR ", "sensors/current_sensor.py",     "\033[95m"),
    ("ACO ", "sensors/acoustic_sensor.py",    "\033[94m"),
]
RESET = "\033[0m"
children = []

def stream(proc, label, color):
    for line in proc.stdout:
        sys.stdout.write(f"{color}[{label}]{RESET} {line}")
        sys.stdout.flush()

def shutdown(sig, frame):
    print(f"\n\033[91m[LAUNCHER] Stopping...\033[0m")
    for p in children:
        p.terminate()
    time.sleep(1)
    for p in children:
        try: p.kill()
        except: pass
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

print("\033[96m[LAUNCHER] IMHM v2 — AWS IoT Core mode\033[0m")
print("\033[96m[LAUNCHER] Dashboard → http://localhost:5000\033[0m")
print("\033[96m[LAUNCHER] Ctrl+C to stop all.\033[0m\n")

for label, script, color in PROCS:
    p = subprocess.Popen([sys.executable, script],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1)
    children.append(p)
    threading.Thread(target=stream, args=(p, label, color), daemon=True).start()
    time.sleep(0.5)

while True:
    time.sleep(1)
    for p in children:
        if p.poll() is not None:
            print(f"\033[91m[LAUNCHER] Process pid={p.pid} exited unexpectedly.\033[0m")
