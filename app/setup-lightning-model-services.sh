#!/usr/bin/env bash
set -euo pipefail

# Reproducibly start all hosted model APIs in a Lightning Studio.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || command -v python)}"
BACKEND_DIR="$ROOT_DIR/app/backend"

"$PYTHON_BIN" -m pip install --user lightning-sdk
"$PYTHON_BIN" -m pip install -e "$BACKEND_DIR[embedding,generation,stt]"

for service in embedding stt generation; do
  key="EDGENTRAG_${service^^}_API_TOKEN"
  if [[ -z "${!key:-}" ]]; then
    read -r -s -p "$key: " value
    printf '\n'
    export "$key=$value"
  fi
  [[ -n "${!key}" ]] || { echo "$key cannot be empty" >&2; exit 1; }
done

export EDGENTRAG_EMBEDDING_DEVICE="${EDGENTRAG_EMBEDDING_DEVICE:-cpu}"
export EDGENTRAG_STT_DEVICE="${EDGENTRAG_STT_DEVICE:-auto}"
export EDGENTRAG_GENERATION_DEVICE="${EDGENTRAG_GENERATION_DEVICE:-auto}"
export EDGENTRAG_STT_MODEL_NAME="${EDGENTRAG_STT_MODEL_NAME:-Systran/faster-whisper-small.en}"

exec "$PYTHON_BIN" - "$ROOT_DIR" <<'PY'
import json, os, signal, socket, subprocess, sys, time, urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from lightning_sdk import Studio

root = Path(sys.argv[1])
services = {"embedding": 8001, "stt": 8002, "generation": 8003}
processes = []

def stop(*_):
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    raise SystemExit(0)

signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

for name, port in services.items():
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise SystemExit(f"port {port} is occupied: {exc}")
    log_path = Path(f"/tmp/edgentrag-{name}.log")
    env = os.environ.copy()
    # Keep this service's token, but do not leak the other service tokens.
    for other in services:
        if other != name:
            env.pop(f"EDGENTRAG_{other.upper()}_API_TOKEN", None)
    log = log_path.open("w")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", f"edgentrag.{name}.app:app", "--host", "0.0.0.0", "--port", str(port)],
        cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    processes.append(process)
    for _ in range(90):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
                print(name, "ready:", json.load(response).get("status"), flush=True)
                break
        except Exception:
            if process.poll() is not None:
                raise SystemExit(f"{name} exited; inspect {log_path}")
            time.sleep(1)
    else:
        raise SystemExit(f"{name} health check timed out; inspect {log_path}")

studio = Studio()
existing = {
    str(getattr(item, "port", getattr(item, "local_port", item))): item
    for item in studio.list_ports()
}
urls = {}
for name, port in services.items():
    info = existing.get(str(port)) or studio.add_ports(port)[0]
    candidates = getattr(info, "urls", None)
    if not candidates:
        raise SystemExit(f"Lightning returned no URL for port {port}")
    url = candidates[0].rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise SystemExit(f"Lightning returned an invalid public URL for port {port}")
    urls[name] = url
print("EDGENTRAG_USE_COLAB_FOR_EMBEDDING=false", flush=True)
print("EDGENTRAG_USE_COLAB_FOR_LLM=false", flush=True)
for name, url in urls.items():
    print(f"EDGENTRAG_LIGHTNING_{name.upper()}_SERVICE_URL={url}", flush=True)
print("Press Ctrl-C to stop them.", flush=True)
while True:
    time.sleep(60)
PY
