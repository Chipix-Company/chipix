#!/usr/bin/env bash
# Register desktop release metadata in Convex appInfo for auto-update.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

VERSION=""
PLATFORM="all"
MIN_SUPPORTED_VERSION=""
UPDATE_FEED_URL=""
DOWNLOAD_URL=""
RELEASE_NOTES=""
FORCE_UPDATE="false"
GITHUB_TAG=""
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-AffanShaikhsurab/chipix_release}"
CONVEX_FEED_FILE=""
CONVEX_SITE_URL="${CONVEX_SITE_URL:-https://next-swordfish-62.convex.site}"
CONVEX_DEPLOYMENT="${CONVEX_DEPLOYMENT:-next-swordfish-62}"

usage() {
  cat <<'EOF'
Usage: set-release-version.sh --version <semver> [options]

Options:
  --version <semver>              Required release version (e.g. 0.2.0)
  --platform <all|win32|linux>    Platform row to upsert (default: all)
  --min-supported-version <ver>   Minimum supported client version
  --update-feed-url <url>         Base URL for electron-updater metadata
  --download-url <url>            Direct installer fallback URL
  --release-notes <text>          Release notes shown in UpdateGate
  --force-update                  Mark update as required
  --github-tag <tag>              Build feed URL from GitHub release tag
  --upload-convex-feed <path>     Upload latest*.yml to Convex Storage (~KB).
                                  Uses Convex feed URL so private GitHub repos
                                  still expose version metadata publicly.
  --convex-site-url <url>         Convex site origin (default: next-swordfish-62)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="$2"
      shift 2
      ;;
    --platform)
      PLATFORM="$2"
      shift 2
      ;;
    --min-supported-version)
      MIN_SUPPORTED_VERSION="$2"
      shift 2
      ;;
    --update-feed-url)
      UPDATE_FEED_URL="$2"
      shift 2
      ;;
    --download-url)
      DOWNLOAD_URL="$2"
      shift 2
      ;;
    --release-notes)
      RELEASE_NOTES="$2"
      shift 2
      ;;
    --force-update)
      FORCE_UPDATE="true"
      shift
      ;;
    --github-tag)
      GITHUB_TAG="$2"
      UPDATE_FEED_URL="https://github.com/${GITHUB_REPOSITORY}/releases/download/${GITHUB_TAG}/"
      shift 2
      ;;
    --upload-convex-feed)
      CONVEX_FEED_FILE="$2"
      shift 2
      ;;
    --convex-site-url)
      CONVEX_SITE_URL="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${VERSION}" ]]; then
  echo "--version is required" >&2
  usage
  exit 1
fi

if [[ -z "${MIN_SUPPORTED_VERSION}" ]]; then
  MIN_SUPPORTED_VERSION="${VERSION}"
fi

if [[ -z "${UPDATE_FEED_URL}" && -z "${CONVEX_FEED_FILE}" ]]; then
  UPDATE_FEED_URL="https://github.com/${GITHUB_REPOSITORY}/releases/download/v${VERSION}/"
fi

if [[ -z "${DOWNLOAD_URL}" && -n "${GITHUB_TAG}" ]]; then
  DOWNLOAD_URL="https://github.com/${GITHUB_REPOSITORY}/releases/download/${GITHUB_TAG}/ChipVerify_Desktop-${VERSION}-redhat-x86_64.AppImage"
fi

convex_args=()
if [[ -z "${CONVEX_DEPLOY_KEY:-}" ]]; then
  convex_args+=(--deployment "${CONVEX_DEPLOYMENT}")
fi

convex_run() {
  (cd "${REPO_ROOT}" && npx convex run "$@" "${convex_args[@]}")
}

resolve_node_bin() {
  if command -v node >/dev/null 2>&1; then
    command -v node
    return 0
  fi
  if [[ -x "/mnt/c/Program Files/nodejs/node.exe" ]]; then
    printf '%s' "/mnt/c/Program Files/nodejs/node.exe"
    return 0
  fi
  echo "node executable not found" >&2
  return 1
}

NODE_BIN="$(resolve_node_bin)"

upload_feed_to_convex() {
  local feed_path="$1"
  local download_url="$2"
  local prepared_feed upload_json upload_url storage_id

  if [[ ! -f "${feed_path}" ]]; then
    echo "Convex feed file not found: ${feed_path}" >&2
    exit 1
  fi

  prepared_feed="$(mktemp)"
"${NODE_BIN}" -e "
const fs = require('fs');
const feedPath = process.argv[1];
const downloadUrl = process.argv[2];
const outPath = process.argv[3];
let raw = fs.readFileSync(feedPath, 'utf8');
if (downloadUrl) {
  const fileName = downloadUrl.split('/').pop() || 'installer';
  raw = raw.replace(/^path:\\s*.*/m, 'path: ' + fileName);
  raw = raw.replace(/^([ \\t]*- url:\\s*).*/m, '\$1' + downloadUrl);
}
fs.writeFileSync(outPath, raw);
" "${feed_path}" "${download_url}" "${prepared_feed}"

  upload_json="$(convex_run controlPlane:createDesktopReleaseUploadUrl "{\"kind\":\"update_feed\"}")"
  upload_url="$("${NODE_BIN}" -e "const row=JSON.parse(process.argv[1]); process.stdout.write(row.uploadUrl||'');" "${upload_json}")"
  if [[ -z "${upload_url}" ]]; then
    echo "Convex upload URL missing for ${feed_path}" >&2
    exit 1
  fi

  storage_id="$(
    curl -fsS -X POST \
      -H "Content-Type: text/yaml" \
      --data-binary @"${prepared_feed}" \
      "${upload_url}" \
      | "${NODE_BIN}" -e "const row=JSON.parse(require('fs').readFileSync(0,'utf8')); if(!row.storageId){process.exit(1)}; process.stdout.write(row.storageId);"
  )"
  rm -f "${prepared_feed}"
  printf '%s' "${storage_id}"
}

FEED_STORAGE_ID=""
if [[ -n "${CONVEX_FEED_FILE}" ]]; then
  echo "Uploading update feed to Convex Storage: ${CONVEX_FEED_FILE}"
  FEED_STORAGE_ID="$(upload_feed_to_convex "${CONVEX_FEED_FILE}" "${DOWNLOAD_URL}")"
  UPDATE_FEED_URL=""
  echo "Using Convex-hosted update feed for platform=${PLATFORM}"
fi

payload="$("${NODE_BIN}" -e "
const payload = {
  platform: process.argv[1],
  version: process.argv[2],
  minSupportedVersion: process.argv[3],
  updateFeedUrl: process.argv[4] || undefined,
  downloadUrl: process.argv[5] || undefined,
  releaseNotes: process.argv[6],
  forceUpdate: process.argv[7] === 'true',
};
console.log(JSON.stringify(payload));
" "${PLATFORM}" "${VERSION}" "${MIN_SUPPORTED_VERSION}" "${UPDATE_FEED_URL}" "${DOWNLOAD_URL}" "${RELEASE_NOTES}" "${FORCE_UPDATE}")"

echo "Upserting Convex appInfo for platform=${PLATFORM} version=${VERSION}"
convex_run appInfo:upsert "${payload}"

release_payload="$("${NODE_BIN}" -e "
const payload = {
  platform: process.argv[1],
  version: process.argv[2],
  minSupportedVersion: process.argv[3],
  releaseNotes: process.argv[6],
  forceUpdate: process.argv[7] === 'true',
};
if (process.argv[4]) payload.updateFeedUrl = process.argv[4];
if (process.argv[5]) payload.downloadUrl = process.argv[5];
if (process.argv[8]) payload.updateFeedStorageId = process.argv[8];
console.log(JSON.stringify(payload));
" "${PLATFORM}" "${VERSION}" "${MIN_SUPPORTED_VERSION}" "${UPDATE_FEED_URL}" "${DOWNLOAD_URL}" "${RELEASE_NOTES}" "${FORCE_UPDATE}" "${FEED_STORAGE_ID}")"

echo "Upserting Convex desktopReleases for platform=${PLATFORM}"
convex_run controlPlane:publishDesktopRelease "${release_payload}"
