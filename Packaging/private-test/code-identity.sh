#!/bin/bash
# Native macOS code-directory identity helpers. Sourced by private packagers.
private_code_hash() {
  local details digest
  details=$(/usr/bin/codesign --display --verbose=4 "$1" 2>&1) || return 1
  digest=$(printf '%s\n' "$details" | /usr/bin/sed -n 's/^CDHash=//p')
  [[ $digest =~ ^[0-9a-f]{40}$ ]] || return 1
  printf '%s\n' "$digest"
}

private_code_requirement() {
  local digest
  digest=$(private_code_hash "$1") || return 1
  printf 'identifier "%s" and cdhash H"%s"\n' "$2" "$digest"
}
