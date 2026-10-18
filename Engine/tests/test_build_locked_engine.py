"""Exercise final-artifact admission with disposable, non-building tool fixtures."""

import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


class LockedEnginePublicationTests(unittest.TestCase):
    def test_only_a_verified_build_replaces_the_previous_artifact(self):
        for failure in ("mode", "size", "digest", None):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                engine, checkout, cache, output, tools = (
                    root / name for name in ("engine", "checkout", "cache", "output", "tools")
                )
                for directory in (engine, checkout, cache, output, tools, root / "target"):
                    directory.mkdir()
                script = engine / "build-locked-engine.sh"
                shutil.copyfile(Path(__file__).resolve().parents[1] / script.name, script)
                (engine / "verify-source-lock.py").write_text("")
                shutil.copyfile(Path(__file__).resolve().parents[1] / "verify-archive-modes.py",
                                engine / "verify-archive-modes.py")
                (engine / "metadata.json").write_text("{}")
                (checkout / ".git").mkdir()
                (checkout / "tests").mkdir()
                (checkout / "tests/test_omarchy_fixture.py").write_text(
                    "import unittest\nclass Fixture(unittest.TestCase):\n    def test_ready(self): pass\n")
                (checkout / "src").mkdir()
                for name in ("main.py", "omarchy_fixture.py"):
                    (checkout / "src" / name).write_text("")
                certificate = checkout / "package/Frameworks/Python.framework/Versions/Current/etc/openssl/cert.pem"
                certificate.parent.mkdir(parents=True)
                certificate.write_text("fixture")
                (checkout / "dl").mkdir()
                (checkout / "dl/certifi-cacert-2026.07.22.pem").write_text("fixture")
                self.executable(checkout / "build.sh", "exit 0")

                archive = root / "archive.tar"
                with tarfile.open(archive, "w") as tar:
                    member = tarfile.TarInfo("fixture")
                    member.size = 3
                    member.mode = 0o666 if failure == "mode" else 0o644
                    tar.addfile(member, io.BytesIO(b"new"))
                payload = archive.read_bytes()
                tool_records = {}
                for name in ("clang", "lld", "rustc", "cargo", "make", "bsdtar", "cpio", "gtar", "gzip", "seven_zip"):
                    body = 'if [[ ${1:-} == --version ]]; then echo "fixture 1"; exit; fi\n'
                    if name == "rustc":
                        body += "echo " + shlex.quote(str(root / "target"))
                    elif name == "seven_zip":
                        body += "printf '\\nfixture 1\\n'"
                    elif name == "gtar":
                        body += "cat " + shlex.quote(str(archive))
                    elif name == "gzip":
                        body += "cat"
                    else:
                        body += "exit 0"
                    self.executable(tools / name, body)
                    tool_records[name] = {"path": str(tools / name), "version": "fixture 1"}
                self.executable(tools / "git", "exit 0")
                # Keep the macOS build script's scratch directory portable and isolated.
                self.executable(tools / "mktemp",
                                'if [[ ${1:-} == -d ]]; then exec /usr/bin/mktemp -d "$TMPDIR/build.XXXXXX"; '
                                'else exec /usr/bin/mktemp "$@"; fi')
                self.executable(tools / "stat", "exec " + shlex.quote(sys.executable)
                                + ' -c \'import os,sys; print(os.stat(sys.argv[-1]).st_size)\' "$@"')
                filename = "installer-fixture.tar.gz"
                lock = {
                    "build_toolchain": {"tools": tool_records, "rust_target": "fixture", "source_date_epoch": 0},
                    "build_inputs": [],
                    "downstream_overlay": {"patch": {"path": "unused.patch"}, "files": [],
                                           "metadata": {"path": "metadata.json"}, "version": "fixture"},
                    "validation_artifact": {"filename": filename,
                                            "size_bytes": len(payload) + (failure == "size"),
                                            "sha256": "0" * 64 if failure == "digest" else hashlib.sha256(payload).hexdigest()},
                }
                (engine / "source-lock.json").write_text(json.dumps(lock))
                final = output / filename
                final.write_bytes(b"previous-good-artifact")
                result = subprocess.run(
                    ["bash", str(script), str(checkout), str(cache), str(output)],
                    env={**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"], "TMPDIR": str(root)},
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode == 0, failure is None, result.stdout + result.stderr)
                if failure:
                    expected = {"mode": "unsafe engine archive mode", "size": "size does not reproduce",
                                "digest": "digest does not reproduce"}[failure]
                    self.assertIn(expected, result.stdout + result.stderr)
                self.assertEqual(final.read_bytes(), payload if failure is None else b"previous-good-artifact")
                self.assertEqual(list(output.glob(".installer.*")), [])

    @staticmethod
    def executable(path, body):
        path.write_text("#!/bin/bash\nset -euo pipefail\n" + body + "\n")
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
