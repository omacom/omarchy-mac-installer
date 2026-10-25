#!/bin/bash
# Run the Try mode's tests in debug and release, and fail unless every suite
# ran tests and none was skipped (docs/try.md). macOS only.
#
#   test/macos-try-tests.sh [LOG_DIRECTORY]

set -euo pipefail

fail() {
  echo "macos-try-tests: $*" >&2
  exit 1
}

[[ $(uname -s) == "Darwin" ]] || fail "the Try tests run on macOS"
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
logs=${1:-$(mktemp -d "${TMPDIR:-/tmp}/try-tests.XXXXXX")}
mkdir -p "$logs"
suites=(TrialSessionTests TrialVMFolderTests TrialKitUpdateTests)
filter="OmarchyInstallerUXCoreTests\\.($(IFS='|'; echo "${suites[*]}"))"

for configuration in debug release; do
  log="$logs/try-tests-$configuration.log"
  swift test --package-path "$root" --configuration "$configuration" --filter "$filter" >"$log" 2>&1 ||
    { tail -50 "$log" >&2; fail "the Try tests failed in $configuration (see $log)"; }
  if grep -E "^Test Case '.*' skipped" "$log" >&2; then
    fail "Try tests were skipped in $configuration"
  fi
  for suite in "${suites[@]}"; do
    executed=$(grep -A1 -E "^Test Suite '$suite' passed" "$log" | sed -n 's/^[[:space:]]*Executed \([0-9]*\) tests\{0,1\},.*/\1/p')
    [[ $executed =~ ^[1-9][0-9]*$ ]] || fail "$suite ran no tests in $configuration"
    echo "$configuration: $suite ran $executed tests, none skipped"
  done
done
