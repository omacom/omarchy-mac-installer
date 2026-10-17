#!/bin/bash
# Compile, assemble and inspect the unsigned release layout without installing it.
set -euo pipefail

if (( $# != 1 )) || [[ $(uname -s) != Darwin ]]; then
  echo 'Usage (macOS): validate-app-assembly.sh OUTPUT_DIRECTORY' >&2
  exit 64
fi
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p "$1"
output=$(cd -- "$1" && pwd)
mkdir "$output/release"
cp "$root/Release/trust-root.ed25519.pub" "$output/release/trust-root.ed25519.pub"
bash "$root/scripts/make-release-descriptor" \
  --public-key "$output/release/trust-root.ed25519.pub" \
  --output "$output/release/release.json"
# shellcheck source=identity.conf
source "$root/Packaging/identity.conf"
OMARCHY_APP_VERSION=0.0.0-ci OMARCHY_APP_BUILD_NUMBER=1 OMARCHY_APP_SIGNING_IDENTITY=none \
  OMARCHY_TEAM_ID="$INSTALLER_TEAM_ID" \
  bash "$root/Packaging/build-app.sh" "$output/release" "$output/app"
app="$output/app/$INSTALLER_APP_NAME.app"
python3 "$root/Packaging/release/release-guard" "$app"
