#!/bin/bash
# Remove the app and helper this Neo hardware package installed, so the
# package can be installed again. It does not change Gatekeeper, quarantine,
# or the disk.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
# shellcheck source=../identity.conf
source "$root/Packaging/identity.conf"
if (( EUID != 0 )); then
  echo "Run with sudo: sudo bash Packaging/neo-test/uninstall.sh" >&2
  exit 1
fi
/bin/launchctl bootout "system/$INSTALLER_HELPER_IDENTIFIER" 2>/dev/null || true
rm -f \
  "/Library/PrivilegedHelperTools/$INSTALLER_HELPER_IDENTIFIER" \
  "/Library/LaunchDaemons/$INSTALLER_HELPER_IDENTIFIER.plist"
rm -rf \
  "/var/db/$INSTALLER_APP_IDENTIFIER" \
  "/Applications/$INSTALLER_APP_NAME.app" \
  "/Applications/$INSTALLER_LEGACY_APP_NAME.app"
echo "Removed the Neo hardware-test app and helper. Gatekeeper was not changed."
