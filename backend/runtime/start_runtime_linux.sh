#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${CHIPVERIFY_PYTHON_BIN:-python3}"
LLAMA_BIN="${CHIPVERIFY_LLAMACPP_BIN:-llama-server}"
GGUF_PATH="${CHIPVERIFY_GGUF_PATH:-}"

ARTIFACT_MANIFEST_PATH="${CHIPVERIFY_ARTIFACT_MANIFEST_PATH:-}"

LLM_HOST="${CHIPVERIFY_LLM_BIND_HOST:-127.0.0.1}"
LLM_PORT="${CHIPVERIFY_LLM_PORT:-7349}"
LLM_CONTEXT_SIZE="${CHIPVERIFY_LLM_CONTEXT_SIZE:-8192}"
LLM_PARALLEL="${CHIPVERIFY_LLM_PARALLEL:-2}"
LLM_API_KEY_REQUIRED="${CHIPVERIFY_LLM_API_KEY_REQUIRED:-true}"
ALLOW_PUBLIC_BIND="${CHIPVERIFY_ALLOW_PUBLIC_BIND:-false}"

BACKEND_HOST="${CHIPVERIFY_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${CHIPVERIFY_BACKEND_PORT:-7348}"

die() {
    echo "${1}" >&2
    exit 1
}

require_executable() {
    local exe="${1:-}"
    local env_name="${2:-}"
    local friendly_name="${3:-command}"

    if [[ -z "${exe}" ]]; then
        die "${friendly_name} is not set. Set ${env_name}."
    fi

    if [[ "${exe}" == */* ]]; then
        if [[ ! -x "${exe}" ]]; then
            die "${friendly_name} not found or not executable: ${exe} (set ${env_name})"
        fi
        return 0
    fi

    if ! command -v "${exe}" >/dev/null 2>&1; then
        die "${friendly_name} '${exe}' not found on PATH (set ${env_name} to an absolute path if needed)."
    fi
}

require_python_module() {
    local module_name="${1:-}"
    if [[ -z "${module_name}" ]]; then
        die "Internal error: require_python_module missing module_name"
    fi

    if ! "${PYTHON_BIN}" -c "import ${module_name}" >/dev/null 2>&1; then
        die "Python at '${PYTHON_BIN}' cannot import '${module_name}'. Install backend dependencies (e.g. pip install -r backend/requirements.txt) or set CHIPVERIFY_PYTHON_BIN to the correct venv python."
    fi
}

probe_health_once() {
    local url="${1:-}"
    if [[ -z "${url}" ]]; then
        return 1
    fi

    PROBE_URL="${url}" "${PYTHON_BIN}" - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

url = os.environ.get("PROBE_URL", "")
if not url:
    sys.exit(1)

try:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=2) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except Exception:
            payload = None
        if isinstance(payload, dict) and payload.get("status") == "ok":
            sys.exit(0)
except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
    pass

sys.exit(1)
PY
}

print_log_tail_redacted() {
    local log_path="${1:-}"
    local max_lines="${2:-60}"

    if [[ -z "${log_path}" || ! -f "${log_path}" ]]; then
        return 0
    fi

    LOG_PATH="${log_path}" LOG_LINES="${max_lines}" REDACT_VALUE="${CHIPVERIFY_LLM_API_KEY:-}" "${PYTHON_BIN}" - <<'PY'
import os
from collections import deque

path = os.environ.get("LOG_PATH")
lines = int(os.environ.get("LOG_LINES", "60"))
redact = os.environ.get("REDACT_VALUE", "")

try:
    tail = deque(maxlen=lines)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            tail.append(line.rstrip("\n"))
except Exception:
    raise SystemExit(0)

for line in tail:
    if redact:
        line = line.replace(redact, "<redacted>")
    print(line)
PY
}

normalize_bool() {
    local raw="${1:-}"
    case "${raw,,}" in
        1|true|yes|on) echo "true" ;;
        *) echo "false" ;;
    esac
}

is_public_bind_host() {
    local host="${1:-}"
    case "${host}" in
        "0.0.0.0"|"::"|"[::]"|"*") return 0 ;;
        *) return 1 ;;
    esac
}

LLM_API_KEY_REQUIRED="$(normalize_bool "${LLM_API_KEY_REQUIRED}")"
ALLOW_PUBLIC_BIND="$(normalize_bool "${ALLOW_PUBLIC_BIND}")"

if [[ -z "${GGUF_PATH}" ]]; then
    echo "CHIPVERIFY_GGUF_PATH is required and must point to a GGUF file."
    exit 1
fi

if [[ ! -f "${GGUF_PATH}" ]]; then
    echo "GGUF file not found: ${GGUF_PATH}"
    exit 1
fi

require_executable "${PYTHON_BIN}" "CHIPVERIFY_PYTHON_BIN" "Python"
require_executable "${LLAMA_BIN}" "CHIPVERIFY_LLAMACPP_BIN" "llama.cpp server (llama-server)"
require_python_module "uvicorn"

if [[ -n "${ARTIFACT_MANIFEST_PATH}" ]]; then
    if [[ ! -f "${ARTIFACT_MANIFEST_PATH}" ]]; then
        die "Artifact manifest not found: ${ARTIFACT_MANIFEST_PATH} (set CHIPVERIFY_ARTIFACT_MANIFEST_PATH)"
    fi

    echo "Verifying runtime artifacts with manifest: ${ARTIFACT_MANIFEST_PATH}"
    "${PYTHON_BIN}" "${SCRIPT_DIR}/verify_artifacts.py" \
        --manifest "${ARTIFACT_MANIFEST_PATH}" \
        --llama-bin "${LLAMA_BIN}" \
        --gguf-path "${GGUF_PATH}" \
        --require-expected
fi

if [[ "${LLM_API_KEY_REQUIRED}" == "true" && -z "${CHIPVERIFY_LLM_API_KEY:-}" ]]; then
    echo "CHIPVERIFY_LLM_API_KEY is required because CHIPVERIFY_LLM_API_KEY_REQUIRED=true."
    exit 1
fi

if [[ "${ALLOW_PUBLIC_BIND}" != "true" ]]; then
    if is_public_bind_host "${LLM_HOST}"; then
        echo "Refusing public model runtime bind host '${LLM_HOST}'. Set CHIPVERIFY_ALLOW_PUBLIC_BIND=true to override."
        exit 1
    fi
    if is_public_bind_host "${BACKEND_HOST}"; then
        echo "Refusing public backend bind host '${BACKEND_HOST}'. Set CHIPVERIFY_ALLOW_PUBLIC_BIND=true to override."
        exit 1
    fi
fi

LOG_DIR="${CHIPVERIFY_LOG_DIR:-${BACKEND_DIR}/logs}"
mkdir -p "${LOG_DIR}"

LLAMA_CMD=(
    "${LLAMA_BIN}"
    -m "${GGUF_PATH}"
    --host "${LLM_HOST}"
    --port "${LLM_PORT}"
    -c "${LLM_CONTEXT_SIZE}"
    -np "${LLM_PARALLEL}"
)

if [[ -n "${CHIPVERIFY_LLM_API_KEY:-}" ]]; then
    LlamaApiKey="${CHIPVERIFY_LLM_API_KEY}"
    LLAMA_CMD+=(--api-key "${LlamaApiKey}")
fi

echo "Starting llama.cpp server on ${LLM_HOST}:${LLM_PORT}"
"${LLAMA_CMD[@]}" >"${LOG_DIR}/llama-server.log" 2>&1 &
LLAMA_PID=$!

cleanup() {
    if kill -0 "${LLAMA_PID}" >/dev/null 2>&1; then
        echo "Stopping llama.cpp server (pid=${LLAMA_PID})"
        kill "${LLAMA_PID}" >/dev/null 2>&1 || true
        wait "${LLAMA_PID}" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT INT TERM

HEALTH_URL="http://${LLM_HOST}:${LLM_PORT}/health"
READY=0
for _ in $(seq 1 90); do
    if ! kill -0 "${LLAMA_PID}" >/dev/null 2>&1; then
        echo "llama.cpp server exited unexpectedly before becoming ready (pid=${LLAMA_PID})."
        echo "See log: ${LOG_DIR}/llama-server.log"
        echo "---- llama-server.log (tail) ----"
        print_log_tail_redacted "${LOG_DIR}/llama-server.log" 60 || true
        echo "-------------------------------"
        READY=0
        break
    fi

    if probe_health_once "${HEALTH_URL}"; then
        READY=1
        break
    fi
    sleep 1
done

if [[ "${READY}" -ne 1 ]]; then
    echo "Local runtime did not become ready in time: ${HEALTH_URL}"
    echo "See log: ${LOG_DIR}/llama-server.log"
    echo "---- llama-server.log (tail) ----"
    print_log_tail_redacted "${LOG_DIR}/llama-server.log" 60 || true
    echo "-------------------------------"
    exit 1
fi

MODEL_ALIAS_DEFAULT="$(basename "${GGUF_PATH}")"
export CHIPVERIFY_LLM_PROVIDER="local"
export CHIPVERIFY_LLM_BASE_URL="http://${LLM_HOST}:${LLM_PORT}/v1"
export CHIPVERIFY_LLM_MODEL_ALIAS="${CHIPVERIFY_LLM_MODEL_ALIAS:-${MODEL_ALIAS_DEFAULT}}"
export CHIPVERIFY_LLM_API_KEY_REQUIRED="${LLM_API_KEY_REQUIRED}"

echo "Starting backend on ${BACKEND_HOST}:${BACKEND_PORT}"
cd "${BACKEND_DIR}"
"${PYTHON_BIN}" -m uvicorn main:app --host "${BACKEND_HOST}" --port "${BACKEND_PORT}"
