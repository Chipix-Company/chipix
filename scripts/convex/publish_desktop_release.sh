#!/usr/bin/env bash
# Upload installer + update feed to Convex Storage and publish desktopReleases row.
#
# WARNING: Convex Free tier allows only 1 GB total file storage and 1 GB/month egress.
# A single ~380 MB AppImage nearly fills that quota. Prefer GitHub Releases for binaries:
#   ./scripts/convex/set-release-version.sh --version 0.2.1 --platform linux --github-tag v0.2.1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

DEPLOYMENT="${CONVEX_DEPLOYMENT:-next-swordfish-62}"
PLATFORM="win32"
VERSION=""
MIN_SUPPORTED_VERSION=""
RELEASE_NOTES="ChipVerify desktop release"
INSTALLER_PATH=""
UPDATE_FEED_PATH=""
FORCE_UPDATE="false"

find_first_existing() {
  local candidate
  for candidate in "$@"; do
    if [[ -n "${candidate}" && -f "${candidate}" ]]; then
      printf '%s' "${candidate}"
      return 0
    fi
  done
  return 1
}

usage() {
  cat <<'EOF'
Usage: publish_desktop_release.sh --version <semver> [options]

Options:
  --platform <win32|linux|darwin|all>   Target platform (default: win32)
  --version <semver>                    Release version (required)
  --min-supported-version <semver>      Minimum supported client version
  --release-notes <text>                Release notes for UpdateGate
  --installer <path>                    Installer artifact path
  --feed <path>                         latest*.yml update feed path
  --force-update                        Mark update as required
  --deployment <name>                   Convex deployment (default: next-swordfish-62)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --min-supported-version) MIN_SUPPORTED_VERSION="$2"; shift 2 ;;
    --release-notes) RELEASE_NOTES="$2"; shift 2 ;;
    --installer) INSTALLER_PATH="$2"; shift 2 ;;
    --feed) UPDATE_FEED_PATH="$2"; shift 2 ;;
    --force-update) FORCE_UPDATE="true"; shift ;;
    --deployment) DEPLOYMENT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
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

if [[ -z "${INSTALLER_PATH}" ]]; then
  case "${PLATFORM,,}" in
    linux)
      INSTALLER_PATH="$(find_first_existing \
        "dist-redhat/ChipVerify_Desktop-${VERSION}-redhat-x86_64.AppImage" \
        "dist-redhat/ChipVerify Desktop-${VERSION}-redhat-x86_64.AppImage" \
        "dist-electron/ChipVerify_Desktop-${VERSION}-linux-x86_64.AppImage" \
        "dist-electron/ChipVerify Desktop-${VERSION}-linux-x86_64.AppImage" \
        || true)"
      ;;
    darwin)
      INSTALLER_PATH="$(find_first_existing \
        "dist-electron/ChipVerify_Desktop-${VERSION}-mac-x64.dmg" \
        "dist-electron/ChipVerify Desktop-${VERSION}-mac-x64.dmg" \
        || true)"
      ;;
    *)
      INSTALLER_PATH="$(find_first_existing \
        "dist-electron/ChipVerify_Desktop-${VERSION}-win-x64.exe" \
        "dist-electron/ChipVerify Desktop-${VERSION}-win-x64.exe" \
        || true)"
      ;;
  esac
fi

if [[ -z "${UPDATE_FEED_PATH}" ]]; then
  case "${PLATFORM,,}" in
    linux)
      UPDATE_FEED_PATH="$(find_first_existing \
        "dist-redhat/latest-linux.yml" \
        "dist-electron/latest-linux.yml" \
        || true)"
      ;;
    darwin)
      UPDATE_FEED_PATH="dist-electron/latest-mac.yml"
      ;;
    *)
      UPDATE_FEED_PATH="dist-electron/latest.yml"
      ;;
  esac
fi

if [[ ! -f "${INSTALLER_PATH}" ]]; then
  echo "Installer not found: ${INSTALLER_PATH:-<unset>}" >&2
  exit 1
fi
if [[ ! -f "${UPDATE_FEED_PATH}" ]]; then
  echo "Update feed not found: ${UPDATE_FEED_PATH:-<unset>}" >&2
  exit 1
fi

convex_args=()
if [[ -z "${CONVEX_DEPLOY_KEY:-}" ]]; then
  convex_args+=(--deployment "${DEPLOYMENT}")
fi

convex_run() {
  npx convex run "$@" "${convex_args[@]}"
}

upload_to_convex_storage() {
  local kind="$1"
  local file_path="$2"
  local content_type="$3"
  local upload_json upload_url storage_id

  upload_json="$(convex_run controlPlane:createDesktopReleaseUploadUrl "{\"kind\":\"${kind}\"}")"
  upload_url="$(node -e "const row=JSON.parse(process.argv[1]); process.stdout.write(row.uploadUrl||'');" "${upload_json}")"
  if [[ -z "${upload_url}" ]]; then
    echo "Convex upload URL missing for ${file_path}" >&2
    exit 1
  fi

  storage_id="$(
    curl -fsS -X POST \
      -H "Content-Type: ${content_type}" \
      --data-binary @"${file_path}" \
      "${upload_url}" \
      | node -e "const row=JSON.parse(require('fs').readFileSync(0,'utf8')); if(!row.storageId){process.exit(1)}; process.stdout.write(row.storageId);"
  )"
  printf '%s' "${storage_id}"
}

echo "Deploying Convex functions/schema to ${DEPLOYMENT}..."
convex_run controlPlane:seedCloudDefaults "{}" --push >/dev/null

echo "Uploading update feed: ${UPDATE_FEED_PATH}"
feed_storage_id="$(upload_to_convex_storage update_feed "${UPDATE_FEED_PATH}" "text/yaml")"

echo "Uploading installer: ${INSTALLER_PATH}"
installer_storage_id="$(upload_to_convex_storage installer "${INSTALLER_PATH}" "application/octet-stream")"

payload="$(node -e "
const payload = {
  platform: process.argv[1],
  version: process.argv[2],
  minSupportedVersion: process.argv[3],
  releaseNotes: process.argv[4],
  updateFeedStorageId: process.argv[5],
  installerStorageId: process.argv[6],
  forceUpdate: process.argv[7] === 'true',
};
console.log(JSON.stringify(payload));
" "${PLATFORM}" "${VERSION}" "${MIN_SUPPORTED_VERSION}" "${RELEASE_NOTES}" "${feed_storage_id}" "${installer_storage_id}" "${FORCE_UPDATE}")"

echo "Publishing desktop release row for platform=${PLATFORM} version=${VERSION}"
release_id="$(convex_run controlPlane:publishDesktopRelease "${payload}")"

echo "Published release ${VERSION} for ${PLATFORM}"
echo "desktopReleases id: ${release_id}"
echo "updateFeedStorageId: ${feed_storage_id}"
echo "installerStorageId: ${installer_storage_id}"
