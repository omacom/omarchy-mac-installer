#!/bin/bash
# The Neo package must pin the decoder and Recovery template. An ad hoc helper
# whose environment only names the app cdhash has no Developer ID team, so
# both requirements are otherwise nil and the engine reports
# restoreImageUnavailable.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
python3 - "$root" <<'PY'
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1])
enum = (root / "Sources/OmarchyAppleInstaller/PinnedAsahiEngineExecutor.swift").read_text()
start = enum.index("public enum PinnedAsahiEngineExecutionError")
body = enum[start:enum.index("\n  }", start)]
cases = re.findall(r"^\s*case\s+(\w+)(\()?", body, re.M)
names = [name for name, _ in cases]
payloads = [name for name, paren in cases if paren]
plain = [name for name, paren in cases if not paren]
ordinal = names.index("restoreImageUnavailable")
payload_first = (payloads + plain).index("restoreImageUnavailable")
assert ordinal == 12 and payload_first == 12, (ordinal, payload_first, names)

builder = (root / "Packaging/private-test/build-tester-pkg.sh").read_text()
assert 'profile == "neo"' in builder
assert "private_code_requirement \"$decoder\"" in builder
assert "private_code_hash \"$template\"" in builder
assert "write-daemon-plist.py" in builder

writer = root / "Packaging/private-test/write-daemon-plist.py"
client = 'identifier "app" and cdhash H"' + ("a" * 40) + '"'
decoder = 'identifier "decoder" and cdhash H"' + ("b" * 40) + '"'
template = 'cdhash H"' + ("c" * 40) + '"'
with tempfile.TemporaryDirectory() as work:
    work = Path(work)
    plain = work / "m3.plist"
    neo = work / "neo.plist"
    subprocess.run([sys.executable, str(writer), str(plain), client, "helper"], check=True)
    subprocess.run(
        [sys.executable, str(writer), str(neo), client, "helper", decoder, template],
        check=True)
    plain_env = plistlib.loads(plain.read_bytes())["EnvironmentVariables"]
    neo_env = plistlib.loads(neo.read_bytes())["EnvironmentVariables"]
assert plain_env == {"OMARCHY_CLIENT_CODE_SIGNING_REQUIREMENT": client}
assert neo_env["OMARCHY_CLIENT_CODE_SIGNING_REQUIREMENT"] == client
assert neo_env["OMARCHY_RESTORE_IMAGE_REQUIREMENT"] == decoder
assert neo_env["OMARCHY_RECOVERY_TEMPLATE_REQUIREMENT"] == template
print("PASS: code 12 is restoreImageUnavailable, and only the Neo daemon plist pins the decoder and Recovery template")
PY
