#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SOURCE_ENV="$(mktemp)"
STAGED_ENV="$(mktemp)"
trap 'rm -f "${SOURCE_ENV}" "${STAGED_ENV}"' EXIT

cat >"${SOURCE_ENV}" <<'EOF'
MODEL_PROVIDER=gemini
CHIPVERIFY_LLM_PROVIDER=gemini
GOOGLE_API_KEY=must-not-survive
CHIPVERIFY_LLM_API_KEY=must-not-survive
BEDROCK_API_KEY=stale-key
EOF

export REPO_ROOT STAGED_ENV
export PYTHON_BIN="${PYTHON_BIN:-python3}"
export CHIPVERIFY_LLM_PROVIDER=bedrock
export MODEL_PROVIDER=bedrock
export CHIPVERIFY_REQUIRE_PACKAGED_LLM_KEY=true
export CHIPVERIFY_DEMO_BEDROCK_API_KEY=test-bedrock-key

# shellcheck source=/dev/null
source "${SCRIPT_DIR}/stage_embedded_runtime_env.sh"
stage_embedded_runtime_env "${SOURCE_ENV}"

require_value() {
  local key="$1"
  local expected="$2"
  grep -qE "^${key}=${expected}$" "${STAGED_ENV}"
}

require_value CHIPVERIFY_LLM_PROVIDER bedrock
require_value MODEL_PROVIDER bedrock
require_value BEDROCK_REGION us-east-1
require_value BEDROCK_MODEL deepseek.v3.2
require_value BEDROCK_API_BASE 'https://bedrock-mantle\.us-east-1\.api\.aws/v1'
require_value CHIPVERIFY_CLOUD_CONTROL_DISABLED true
require_value CHIPVERIFY_LLM_API_KEY_REQUIRED true
require_value BEDROCK_API_KEY test-bedrock-key

if grep -qE '^(GOOGLE_API_KEY|GEMINI_API_KEY|CHIPVERIFY_LLM_API_KEY)=' "${STAGED_ENV}"; then
  echo "Unrelated provider credentials leaked into the staged environment." >&2
  exit 1
fi

echo "Bedrock runtime environment staging test passed."
