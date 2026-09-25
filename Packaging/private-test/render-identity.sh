#!/bin/bash
# Write SOURCE to the new file DEST with @APP_NAME@, @APP_IDENTIFIER@ and
# @HELPER_IDENTIFIER@ replaced from Packaging/identity.conf.
set -euo pipefail
(( $# == 2 )) || { echo 'usage: render-identity.sh SOURCE NEW_DEST' >&2; exit 64; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
source "$root/Packaging/identity.conf"
[[ -f $1 && ! -L $1 ]] || { echo "render-identity: missing or unsafe source: $1" >&2; exit 1; }
[[ ! -e $2 && ! -L $2 ]] || { echo "render-identity: destination exists: $2" >&2; exit 1; }
text=$(<"$1")
text=${text//@APP_NAME@/"$INSTALLER_APP_NAME"}
text=${text//@APP_IDENTIFIER@/"$INSTALLER_APP_IDENTIFIER"}
text=${text//@HELPER_IDENTIFIER@/"$INSTALLER_HELPER_IDENTIFIER"}
# noclobber creates DEST exclusively, so a raced file or symlink is never followed.
set -C
printf '%s\n' "$text" >"$2"
