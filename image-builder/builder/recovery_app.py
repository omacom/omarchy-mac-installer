#!/usr/bin/env python3
"""Freeze the native Recovery build as a byte-pinned image input.

This portable check verifies the inventory, not Apple's code signature. The
native build verifies that signature before export; the installer verifies it
again on macOS before staging the unchanged app.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import sys

APP = "Omarchy Installer.app"
MANIFEST = "omarchy-recovery-app.json"
PREFIX = "esp/aurora/recovery/"
MAX_MANIFEST = 1 << 20
MAX_FILE = 128 << 20
MAX_TOTAL = 256 << 20


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def manifest_files(data):
    require(len(data) <= MAX_MANIFEST, "Recovery manifest is too large")
    manifest = json.loads(data)
    require(isinstance(manifest, dict) and set(manifest) == {"schema", "buildMode", "files"}
            and manifest["schema"] == 1 and manifest["buildMode"] == "recovery"
            and isinstance(manifest["files"], dict) and bool(manifest["files"]),
            "invalid Recovery manifest")
    total = 0
    for name, entry in manifest["files"].items():
        require(isinstance(name, str) and name.startswith("Contents/") and "\\" not in name
                and all(p not in ("", ".", "..") for p in name.split("/")), "unsafe Recovery path")
        require(isinstance(entry, dict) and set(entry) == {"sha256", "bytes"}
                and type(entry["bytes"]) is int and 0 <= entry["bytes"] <= MAX_FILE
                and isinstance(entry["sha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), "invalid Recovery file record")
        require(not name.startswith(("Contents/Resources/InstallData/", "Contents/Resources/AuraPayloads/"))
                and PurePosixPath(name).name not in ("config.json", "install.conf", "install-config.json"),
                "Recovery app contains installation data")
        total += entry["bytes"]
    require(total <= MAX_TOTAL, "Recovery app is too large")
    return manifest["files"]


def check_contents(files, read):
    required = {"Contents/Info.plist", "Contents/MacOS/OmarchyRecovery",
                "Contents/Resources/en.lproj/InfoPlist.strings", "Contents/_CodeSignature/CodeResources"}
    require(required <= set(files), "Recovery app is missing required bundle files")
    for name, expected in files.items():
        data = read(name, expected["bytes"])
        require(len(data) == expected["bytes"] and sha256(data) == expected["sha256"],
                "Recovery app differs from its manifest: " + name)
    info = plistlib.loads(read("Contents/Info.plist", files["Contents/Info.plist"]["bytes"]))
    require(info.get("OmarchyRecoveryBuildMode") == "recovery"
            and info.get("CFBundleExecutable") == "OmarchyRecovery"
            and info.get("CFBundleDisplayName") == "Omarchy Installer",
            "Recovery app is not an enrollment build")
    require(info.get("OmarchyRecoveryDataLayout") == "external-install-data-v1",
            "Recovery app uses an unsupported installation data layout")
    strings = "Contents/Resources/en.lproj/InfoPlist.strings"
    require(b'"CFBundleDisplayName" = "Omarchy Installer";' in read(strings, files[strings]["bytes"]),
            "Recovery app lacks its localized launch name")
    require(files["Contents/MacOS/OmarchyRecovery"]["bytes"] > 0, "Recovery executable is empty")


def regular_bytes(path, limit):
    require(stat.S_ISREG(path.lstat().st_mode), "Recovery input is not a regular file: " + str(path))
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, "Recovery input exceeds its recorded size: " + str(path))
    return data


def validate(directory, expected=None):
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), "Recovery input directory is missing or linked")
    app = directory / APP
    require(app.is_dir() and not app.is_symlink(), "Recovery app is missing or linked")
    data = regular_bytes(directory / MANIFEST, MAX_MANIFEST)
    digest = sha256(data)
    require(expected is None or digest == expected, "Recovery manifest differs from the pinned input")
    files = manifest_files(data)
    actual = set()
    for path in app.rglob("*"):
        mode = path.lstat().st_mode
        require(stat.S_ISREG(mode) or stat.S_ISDIR(mode), "Recovery app contains a non-regular entry")
        if stat.S_ISREG(mode):
            actual.add(path.relative_to(app).as_posix())
    require(actual == set(files), "Recovery app inventory differs from its manifest")
    check_contents(files, lambda name, size: regular_bytes(app / name, size))
    return digest


def snapshot(directory, destination, expected):
    validate(directory, expected)
    destination.mkdir()  # A failed snapshot must never be reused.
    shutil.copytree(directory / APP, destination / APP, symlinks=True)
    shutil.copyfile(directory / MANIFEST, destination / MANIFEST, follow_symlinks=False)
    validate(destination, expected)
    return expected


def validate_zip(archive, expected=None):
    name = PREFIX + MANIFEST
    require(archive.getinfo(name).file_size <= MAX_MANIFEST, "Recovery manifest is too large")
    data = archive.read(name)
    require(expected is None or sha256(data) == expected, "Recovery payload differs from the pinned input")
    files = manifest_files(data)
    prefix = PREFIX + APP + "/"
    actual = set()
    for item in archive.infolist():
        if not item.filename.startswith(prefix) or item.is_dir():
            continue
        relative = item.filename[len(prefix):]
        require(relative not in actual, "duplicate Recovery payload entry")
        require((item.external_attr >> 16) & 0o170000 in (0, stat.S_IFREG),
                "Recovery payload contains a non-regular entry")
        actual.add(relative)
    require(actual == set(files), "Recovery payload inventory differs from its manifest")

    def read(relative, size):
        info = archive.getinfo(prefix + relative)
        require(info.file_size == size, "Recovery payload size differs from its manifest")
        return archive.read(info)

    check_contents(files, read)
    return sha256(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        require(not args.output or args.sha256, "snapshot requires a pinned manifest digest")
        print(snapshot(args.directory, args.output, args.sha256) if args.output
              else validate(args.directory, args.sha256))
    except (ValueError, OSError, KeyError, TypeError, plistlib.InvalidFileException) as error:
        print(f"recovery_app: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
