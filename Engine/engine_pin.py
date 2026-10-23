#!/usr/bin/env python3
"""Name the installation engine the source lock pins, and check that name.

Engine/source-lock.json is the only place the engine is pinned: downstream_overlay.version
names it and validation_artifact holds its file name, size and digest. Everything else
reads it there: the app (OmarchyEnginePinPlugin, at build time), Packaging/build-app.sh,
the release workflows and the catalog tools. See docs/engine-versions.md.

  engine_pin.py name [--date YYYYMMDD] [--write]
      The name for the engine the lock describes: v0.9.2-omarchy.<date>.<content id>.
      The content id is a digest of the lock's build inputs, so two branches that change
      the engine differently get different names, and the same inputs get the same id.
      --write puts the name (and the matching archive file name) in the lock.

  engine_pin.py check
      Fail unless the name is a dated name whose date is real and whose content id matches
      the lock's build inputs, or the one engine named before dated names (LEGACY) with the
      inputs it was built from; and fail if the archive name does not follow the name.

After merging two branches that both changed the engine: resolve the lock's build inputs,
run `name --write`, rebuild the engine and record its size and digest.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "Engine" / "source-lock.json"

BASE = "v0.9.2-omarchy"
DATED = re.compile(r"v0\.9\.2-omarchy\.(\d{8})\.([0-9a-f]{12})")

# The last engine named by number, frozen with the content id of the inputs it
# was built from (.50 rebuilds byte-identical from them). Any input change
# under this name, and any other name that isn't dated, fails `check`.
LEGACY = {"v0.9.2-omarchy.50": "2ede760866d5"}

# The lock's build inputs. Everything else in it either describes the result
# (the name and the artifact) or is prose.
INPUTS = (
    "upstream_installer",
    "upstream_metadata",
    "downstream_overlay",
    "incremental_build",
    "build_inputs",
    "build_toolchain",
    "build_recipe",
)


class PinError(Exception):
    pass


def load_lock(path=LOCK):
    return json.loads(Path(path).read_text())


def content_id(lock):
    inputs = {key: lock[key] for key in INPUTS if key in lock}
    inputs["downstream_overlay"] = {
        key: value for key, value in inputs["downstream_overlay"].items() if key != "version"
    }
    inputs["metadata_sha256"] = lock["validation_artifact"]["metadata_sha256"]
    canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()[:12]


def dated_name(lock, date):
    if not re.fullmatch(r"\d{8}", date):
        raise PinError(f"the date must be YYYYMMDD: {date}")
    datetime.strptime(date, "%Y%m%d")
    return f"{BASE}.{date}.{content_id(lock)}"


def archive_name(version):
    return f"installer-{version}.tar.gz"


def dump_json(document):
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def check(lock):
    problems = []
    version = lock["downstream_overlay"]["version"]
    actual = content_id(lock)
    rename = "run `engine_pin.py name --write`"
    if version in LEGACY:
        if LEGACY[version] != actual:
            problems.append(
                f"{version} was built from inputs {LEGACY[version]}, but the lock's build inputs are {actual}; "
                + rename
            )
    elif match := DATED.fullmatch(version):
        try:
            datetime.strptime(match.group(1), "%Y%m%d")
        except ValueError:
            problems.append(f"{version} names an impossible date; " + rename)
        if match.group(2) != actual:
            problems.append(
                f"{version} names content {match.group(2)}, but the lock's build inputs are {actual}; " + rename
            )
    else:
        problems.append(f"{version} is not a dated engine name ({BASE}.<YYYYMMDD>.<content id>); " + rename)
    filename = lock["validation_artifact"]["filename"]
    if filename != archive_name(version):
        problems.append(f"validation_artifact.filename is {filename}, expected {archive_name(version)}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    name = commands.add_parser("name", help="print (or --write) the dated engine name")
    name.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    name.add_argument("--write", action="store_true")
    commands.add_parser("check", help="fail if the lock's engine name is inconsistent")
    args = parser.parse_args(argv)
    lock = load_lock()
    try:
        if args.command == "name":
            version = dated_name(lock, args.date)
            if args.write:
                lock["downstream_overlay"]["version"] = version
                lock["validation_artifact"]["filename"] = archive_name(version)
                LOCK.write_text(dump_json(lock))
            print(version)
        else:
            problems = check(lock)
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1 if problems else 0
    except PinError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
