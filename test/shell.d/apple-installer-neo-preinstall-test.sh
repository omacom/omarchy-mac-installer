#!/bin/bash
# The Neo hardware package admits j700ap and refuses every other board.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
source "$root/Packaging/identity.conf"
export INSTALLER_APP_NAME INSTALLER_LEGACY_APP_NAME INSTALLER_APP_IDENTIFIER INSTALLER_HELPER_IDENTIFIER
python3 - "$root" <<'PY'
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])
app_name, legacy_name, app_id, helper_id = (os.environ[name] for name in (
    'INSTALLER_APP_NAME', 'INSTALLER_LEGACY_APP_NAME', 'INSTALLER_APP_IDENTIFIER', 'INSTALLER_HELPER_IDENTIFIER'))
with tempfile.TemporaryDirectory() as work:
    work = Path(work)
    subprocess.run(['bash', str(root / 'Packaging/private-test/render-identity.sh'),
                    str(root / 'Packaging/neo-test/preinstall'), str(work / 'rendered')], check=True)
    source = (work / 'rendered').read_text()
    assert '@APP_' not in source and '@LEGACY_' not in source and '@HELPER_' not in source
    assert 'j700' in source and 'j613' not in source
    paths = [work / name for name in ('app', 'legacy', 'daemon', 'helper', 'state')]
    for original, fixture in zip((
        f'/Applications/{app_name}.app', f'/Applications/{legacy_name}.app',
        f'/Library/LaunchDaemons/{helper_id}.plist', f'/Library/PrivilegedHelperTools/{helper_id}',
        f'/var/db/{app_id}'), paths):
        source = source.replace(original, str(fixture))
    source = source.replace('/Library/PrivilegedHelperTools', str(work / 'privileged'))
    source = source.replace('/usr/sbin/sysctl -n hw.targettype', 'printf "%s\\n" "$TEST_BOARD"')
    source = source.replace('/usr/bin/sw_vers -productVersion', 'printf "%s\\n" "$TEST_MACOS"')
    source = source.replace(f'/bin/launchctl print "system/{helper_id}"', 'test "$TEST_LOADED" = 1')
    script = work / 'preinstall'
    script.write_text(source)

    def check(board, accepted, volume='/', macos='27.2', loaded='0'):
        result = subprocess.run(
            ['bash', str(script), 'fixture.pkg', '/', volume],
            env=dict(os.environ, TEST_BOARD=board, TEST_MACOS=macos, TEST_LOADED=loaded),
            capture_output=True, text=True)
        assert (result.returncode == 0) == accepted, (board, volume, macos, result.stderr)

    check('j700', True)
    check('j700ap', True)
    check('J700AP', True)
    for board in ('j613', 'j516s', 'j614s', 'j433', 'unknown', ''):
        check(board, False)
        check(board + 'AP', False)
    check('j700', False, '/Volumes/Other')
    check('j700', False, macos='14.8.3')
    check('j700', False, loaded='1')
    privileged = work / 'privileged'
    privileged.symlink_to(work / 'missing')
    check('j700', False)
    privileged.unlink()
    for path in paths:
        path.write_text('preserve me')
        check('j700', False)
        assert path.read_text() == 'preserve me'
        path.unlink()
print('PASS: Neo package admits j700ap only and keeps the existing-install refusal')
PY
