#!/usr/bin/env bash
# Build a RHEL .rpm directly with rpmbuild from electron-builder's linux-unpacked output,
# bypassing electron-builder's bundled fpm (whose portable-ruby + auto-dependency path
# fails opaquely with "rpmbuild exit 127" on AlmaLinux 8). rpmbuild itself works fine here.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

UNPACKED="${REPO_ROOT}/dist-electron/linux-unpacked"
OUT="${REPO_ROOT}/dist-electron"
NAME="chipverify-desktop"
PRODUCT="ChipVerify_Desktop"
EXECNAME="chipverify-desktop"
ARCH="x86_64"
VERSION="$(node -p "require('${REPO_ROOT}/package.json').version")"

if [[ ! -d "${UNPACKED}" ]]; then
  echo "linux-unpacked not found: ${UNPACKED} (run electron-builder --linux dir first)" >&2
  exit 1
fi

TOP="$(mktemp -d)"
mkdir -p "${TOP}/SPECS" "${TOP}/RPMS"
STAGE="${TOP}/stage"

# Lay out the install tree in a staging dir; the spec's %install copies it into the
# buildroot (rpmbuild wipes the buildroot at the start of %install, so we must not
# pre-stage directly into it).
install -d "${STAGE}/opt/${PRODUCT}"
cp -a "${UNPACKED}/." "${STAGE}/opt/${PRODUCT}/"

install -d "${STAGE}/usr/bin"
ln -sf "/opt/${PRODUCT}/${EXECNAME}" "${STAGE}/usr/bin/${EXECNAME}"

install -d "${STAGE}/usr/share/applications"
cat > "${STAGE}/usr/share/applications/${EXECNAME}.desktop" <<EOF
[Desktop Entry]
Name=ChipVerify Desktop
Comment=AI-assisted chip verification workspace
Exec=/opt/${PRODUCT}/${EXECNAME} %U
Icon=${EXECNAME}
Type=Application
Categories=Development;Engineering;
StartupNotify=true
Terminal=false
EOF

for sz in 16 32 48 64 128 256; do
  src="${REPO_ROOT}/node_modules/app-builder-lib/templates/icons/electron-linux/${sz}x${sz}.png"
  if [[ -f "${src}" ]]; then
    install -d "${STAGE}/usr/share/icons/hicolor/${sz}x${sz}/apps"
    cp -f "${src}" "${STAGE}/usr/share/icons/hicolor/${sz}x${sz}/apps/${EXECNAME}.png"
  fi
done

SPEC="${TOP}/SPECS/${NAME}.spec"
cat > "${SPEC}" <<EOF
Name: ${NAME}
Version: ${VERSION}
Release: 1
Summary: AI-assisted chip verification workspace
License: Proprietary
URL: https://chipverify.com
Vendor: ChipVerify <support@chipverify.com>
Packager: ChipVerify <support@chipverify.com>
BuildArch: ${ARCH}
AutoReqProv: no
Requires: gtk3, nss, alsa-lib, libnotify, libXScrnSaver, libXtst, libxkbcommon, libdrm, mesa-libgbm, cups-libs, libsecret, at-spi2-core, xdg-utils, bash

# Skip rpm's binary post-processing (strip/bytecompile/build-id) — not needed for a
# prebuilt Electron app and avoids EL8 brp helper issues.
%global __os_install_post %{nil}
%global _build_id_links none
%global debug_package %{nil}

%description
ChipVerify Desktop — unified Electron frontend with an embedded Python verification
backend (RHEL/AlmaLinux/Rocky 8+ compatible, glibc 2.28).

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}
cp -a "${STAGE}/." "%{buildroot}/"

%files
/opt/${PRODUCT}
/usr/bin/${EXECNAME}
/usr/share/applications/${EXECNAME}.desktop
/usr/share/icons/hicolor/*/apps/${EXECNAME}.png

%post
# Chromium sandbox: rely on unprivileged user namespaces (default on RHEL 8/9); on
# hardened hosts where userns is disabled, fall back to the SUID-root helper.
SB="/opt/${PRODUCT}/chrome-sandbox"
if [ -f "\${SB}" ]; then
  if unshare --user --pid true >/dev/null 2>&1; then
    chmod 0755 "\${SB}" 2>/dev/null || true
  else
    chown root:root "\${SB}" 2>/dev/null || true
    chmod 4755 "\${SB}" 2>/dev/null || true
  fi
fi
update-alternatives --install /usr/bin/${EXECNAME} ${EXECNAME} /opt/${PRODUCT}/${EXECNAME} 100 >/dev/null 2>&1 || true
update-desktop-database >/dev/null 2>&1 || true
exit 0

%postun
if [ "\$1" = 0 ]; then
  update-alternatives --remove ${EXECNAME} /opt/${PRODUCT}/${EXECNAME} >/dev/null 2>&1 || true
fi
exit 0

%changelog
* Thu Jun 19 2026 ChipVerify <support@chipverify.com> - ${VERSION}-1
- RHEL-native build (rpmbuild) of the embedded desktop app.
EOF

echo "Building RPM with rpmbuild (version ${VERSION})..."
rpmbuild -bb --define "_topdir ${TOP}" "${SPEC}"

BUILT_RPM="$(find "${TOP}/RPMS" -name '*.rpm' -type f | head -1)"
if [[ -z "${BUILT_RPM}" ]]; then
  echo "rpmbuild finished but no .rpm was produced" >&2
  exit 1
fi

FINAL="${OUT}/${PRODUCT}-${VERSION}-linux-${ARCH}.rpm"
cp -f "${BUILT_RPM}" "${FINAL}"
rm -rf "${TOP}"
echo "RPM_ARTIFACT=${FINAL}"
