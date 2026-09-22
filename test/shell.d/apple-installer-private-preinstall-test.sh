#!/bin/bash
# Exercise the packaged guard with fixture hardware and installation paths.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
python3 - "$root" <<'PY'
from pathlib import Path
import os
import subprocess
import sys
import tempfile

source = (Path(sys.argv[1]) / 'Packaging/private-test/scripts/preinstall').read_text()
with tempfile.TemporaryDirectory() as work:
    work = Path(work)
    paths = [work / name for name in ('app', 'daemon', 'helper', 'state')]
    for original, fixture in zip(('/Applications/Omarchy MX Mac Installer.app', '/Library/LaunchDaemons/com.omarchy.mx.installer.helper.plist', '/Library/PrivilegedHelperTools/com.omarchy.mx.installer.helper', '/var/db/com.omarchy.mx.installer'), paths):
        source = source.replace(original, str(fixture))
    source = source.replace('/Library/PrivilegedHelperTools', str(work / 'privileged'))
    source = source.replace('/usr/sbin/sysctl -n hw.targettype', 'printf "%s\\n" "$TEST_BOARD"')
    source = source.replace('/usr/bin/sw_vers -productVersion', 'printf "%s\\n" "$TEST_MACOS"')
    source = source.replace('/bin/launchctl print system/com.omarchy.mx.installer.helper', 'test "$TEST_LOADED" = 1')
    script = work / 'preinstall'
    script.write_text(source)

    def check(board, accepted, volume='/', macos='26.6.2', loaded='0'):
        result = subprocess.run(['bash', str(script), 'fixture.pkg', '/', volume], env=dict(os.environ, TEST_BOARD=board, TEST_MACOS=macos, TEST_LOADED=loaded), capture_output=True, text=True)
        assert (result.returncode == 0) == accepted, (board, volume, result.stderr)

    for board in ('j433', 'j434', 'j504', 'j613', 'j615', 'j514s', 'j514c', 'j514m', 'j516s', 'j516c', 'j516m'):
        check(board, True)
        check(board.upper() + 'AP', True)
    for board in ('j614s', 'j616s', 'j604', 'j773g', 'j313', 'j575d', 'unknown', ''):
        check(board, False)
        check(board + 'AP', False)
    check('j516s', False, '/Volumes/Other')
    check('j516s', False, macos='14.8.3')
    check('j516s', False, macos='invalid')
    check('j516s', True, macos='15.0')
    check('j516s', False, loaded='1')
    privileged = work / 'privileged'
    privileged.symlink_to(work / 'missing')
    check('j516s', False)
    privileged.unlink()
    for path in paths:
        path.write_text('preserve me')
        check('j516s', False)
        assert path.read_text() == 'preserve me'
        path.unlink()
        path.symlink_to(work / 'missing')
        check('j516s', False)
        assert path.is_symlink()
        path.unlink()
print('PASS: all 11 M3 boards, AP normalization, other hardware/volume rejection and existing-state preservation')
PY
