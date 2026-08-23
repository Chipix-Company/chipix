#!/usr/bin/env bash
# Build the RHEL-native .rpm on Depot's fast remote builders (with persistent cache) and
# export it to ./dist-electron — no local container engine required.
#
# Prerequisites (one-time):
#   1. Install the Depot CLI:   npm install -g @depot/cli   (or see https://depot.dev/docs/cli/installation)
#   2. Authenticate:            depot login
#   3. First run prompts you to pick/create a Depot project and saves depot.json.
#
# Then just run:
#   bash scripts/packaging/build_redhat_rpm_depot.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

OUTPUT_DIR="${REPO_ROOT}/dist-electron"

if ! command -v depot >/dev/null 2>&1; then
  echo "Depot CLI not found. Install it with:  npm install -g @depot/cli" >&2
  echo "Then run 'depot login' before retrying." >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"

# Use relative paths so a native depot.exe on Windows/Git-Bash isn't tripped up by
# POSIX-style path conversion. We already cd'd to the repo root above.
echo "Building RHEL RPM on Depot (target: artifact) -> ${OUTPUT_DIR}"
depot build \
  -f Dockerfile.redhat \
  --platform linux/amd64 \
  --target artifact \
  --build-arg "CHIPVERIFY_DEMO_BEDROCK_API_KEY=${CHIPVERIFY_DEMO_BEDROCK_API_KEY:-${BEDROCK_API_KEY:-}}" \
  --build-arg "CHIPVERIFY_LLM_PROVIDER=${CHIPVERIFY_LLM_PROVIDER:-bedrock}" \
  --build-arg "CHIPVERIFY_DEMO_GEMINI_API_KEY=${CHIPVERIFY_DEMO_GEMINI_API_KEY:-}" \
  --build-arg "CHIPVERIFY_DEMO_OPENAI_API_KEY=${CHIPVERIFY_DEMO_OPENAI_API_KEY:-}" \
  -o "type=local,dest=dist-electron" \
  .

echo
echo "Done. Artifacts in ${OUTPUT_DIR}:"
find "${OUTPUT_DIR}" -maxdepth 1 -name '*.rpm' -type f -print || true
RPM_FILE="$(find "${OUTPUT_DIR}" -maxdepth 1 -name '*.rpm' -type f | head -1 || true)"
if [[ -n "${RPM_FILE}" ]]; then
  echo
  echo "Send your friend: ${RPM_FILE}"
  echo "They install it on Red Hat with:  sudo dnf install ./$(basename "${RPM_FILE}")"
fi
