#!/usr/bin/env python3
"""Which Macs each channel's catalog offers, and which stay out.

`supported-models.json` holds one record per board. A board either names the
highest channel it has qualified for, or the reason it is refused:

    "apple,j274":  {"qualified": "stable"}
    "apple,j616s": {"qualified": "developer", "note": "..."}
    "apple,j575d": {"refused": "..."}

Qualification runs developer, edge, rc, stable. A board appears in a channel's
catalog when it has qualified at or above that channel, so edge carries every
board qualified for edge or better and stable only the fully qualified ones.
Edge's set therefore always contains rc's, which always contains stable's: the
shape cannot express a Mac that stable offers and edge does not, and promotion
can only shrink a set. `developer` is qualification for no channel at all; only
a developer build's sealed catalog may add such a board.

A catalog must enable exactly its channel's set, so a release can neither drop
a Mac it promised nor add one that has not qualified. `note` is the text a
catalog shows for a board short of stable, and a board that has reached stable
may not carry one. `app_minimum` is the oldest installer version that can
admit the board at all.

Usage:
  supported_models.py check-catalog FILE   exit 1 unless FILE covers its channel
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MANIFEST = Path(__file__).resolve().with_name("supported-models.json")
SCHEMA_VERSION = 2
# Rising qualification. A board appears in a channel when its own level is at
# or above that channel's, which is why edge's set is the widest.
QUALIFICATION_ORDER = ("developer", "edge", "rc", "stable")
# The channels a signed catalog is published for; `developer` is not one.
CHANNELS = ("edge", "rc", "stable")
DEVICE_IDENTIFIER = re.compile(r"^apple,[0-9a-z]+$")
INSTALLER_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
RECORD_KEYS = {"qualified", "refused", "note", "app_minimum"}


def _refuse(path: Path, message: str) -> None:
    raise SystemExit(f"{path}: {message}")


def load(path: Path = MANIFEST) -> dict[str, dict]:
    """Every board's record, validated, in the manifest's own order."""
    document = json.loads(path.read_text())
    if document.get("schema_version") != SCHEMA_VERSION:
        _refuse(path, "unsupported schema")
    macs = document.get("macs")
    if not isinstance(macs, dict) or not macs:
        _refuse(path, "macs maps each Mac to a record")
    for identifier, record in macs.items():
        if not DEVICE_IDENTIFIER.fullmatch(identifier):
            _refuse(path, f"{identifier} is not an apple,<board> identifier")
        if not isinstance(record, dict):
            _refuse(path, f"{identifier} must map to a record")
        stray = set(record) - RECORD_KEYS
        if stray:
            _refuse(path, f"{identifier} carries unknown keys: {', '.join(sorted(stray))}")
        if ("qualified" in record) == ("refused" in record):
            _refuse(path, f"{identifier} must name either a qualified channel or a refusal, not both")
        if "refused" in record:
            if not isinstance(record["refused"], str) or not record["refused"]:
                _refuse(path, f"{identifier} needs a reason for its refusal")
            for key in ("note", "app_minimum"):
                if key in record:
                    _refuse(path, f"{identifier} is refused, so it cannot carry {key}")
            continue
        if record["qualified"] not in QUALIFICATION_ORDER:
            _refuse(path, f"{identifier} must be qualified for one of {', '.join(QUALIFICATION_ORDER)}")
        if "note" in record:
            if not isinstance(record["note"], str) or not record["note"]:
                _refuse(path, f"{identifier} needs text for its note")
            if record["qualified"] == "stable":
                _refuse(path, f"{identifier} has reached stable, so its note has nothing to warn about")
        if "app_minimum" in record and not (
            isinstance(record["app_minimum"], str)
            and INSTALLER_VERSION.fullmatch(record["app_minimum"])
        ):
            _refuse(path, f"{identifier} needs an X.Y.Z app_minimum")
    return macs


def check_channel(channel: str) -> str:
    if channel not in CHANNELS:
        raise SystemExit(f"channel must be one of {', '.join(CHANNELS)}, not {channel}")
    return channel


def boards_for(channel: str, path: Path = MANIFEST) -> list[str]:
    """The Macs CHANNEL's catalog must enable, sorted."""
    check_channel(channel)
    wanted = QUALIFICATION_ORDER.index(channel)
    return sorted(
        identifier
        for identifier, record in load(path).items()
        if "qualified" in record
        and QUALIFICATION_ORDER.index(record["qualified"]) >= wanted
    )


def refused(path: Path = MANIFEST) -> dict[str, str]:
    """Each board that stays out of every catalog, and why."""
    return {
        identifier: record["refused"]
        for identifier, record in load(path).items()
        if "refused" in record
    }


def developer_boards(path: Path = MANIFEST) -> dict[str, str]:
    """The boards only a developer build's sealed catalog may add, with why."""
    return {
        identifier: record.get("note", "")
        for identifier, record in load(path).items()
        if record.get("qualified") == "developer"
    }


def notes(path: Path = MANIFEST) -> dict[str, str]:
    """The text a catalog shows for each board that has not reached stable."""
    return {
        identifier: record["note"]
        for identifier, record in load(path).items()
        if "note" in record and record.get("qualified") != "developer"
    }


def app_minimums(path: Path = MANIFEST) -> dict[str, str]:
    """The oldest installer version that can admit each board that names one."""
    return {
        identifier: record["app_minimum"]
        for identifier, record in load(path).items()
        if "app_minimum" in record
    }


def coverage_errors(identifiers: list[str], channel: str, path: Path = MANIFEST) -> list[str]:
    """Why IDENTIFIERS (the Macs a catalog enables) is not exactly CHANNEL's set."""
    check_channel(channel)
    macs = load(path)
    wanted = boards_for(channel, path)
    errors = []
    missing = [identifier for identifier in wanted if identifier not in identifiers]
    if missing:
        errors.append(
            f"every Mac qualified for {channel} must be enabled; missing " + ", ".join(missing)
        )
    for identifier in identifiers:
        if identifier in wanted:
            continue
        record = macs.get(identifier)
        if record is None:
            errors.append(
                f"{identifier} is not in scripts/supported-models.json; "
                "add it there, with its engine and device-tree support, first"
            )
        elif "refused" in record:
            errors.append(f"{identifier} is refused: {record['refused']}")
        else:
            errors.append(
                f"{identifier} has only qualified for {record['qualified']}, so {channel} may not offer it"
            )
    return errors


def private_coverage_errors(identifiers: list[str], path: Path = MANIFEST) -> list[str]:
    """Why IDENTIFIERS is not a valid private or developer catalog. Such a
    catalog is not published to a channel, so it must enable everything the
    widest channel offers and may add developer boards on top, but nothing
    refused and nothing the manifest does not name."""
    macs = load(path)
    widest = boards_for(CHANNELS[0], path)
    errors = []
    missing = [identifier for identifier in widest if identifier not in identifiers]
    if missing:
        errors.append(
            f"every Mac qualified for {CHANNELS[0]} must be enabled; missing " + ", ".join(missing)
        )
    for identifier in identifiers:
        if identifier in widest:
            continue
        record = macs.get(identifier)
        if record is None:
            errors.append(
                f"{identifier} is not in scripts/supported-models.json; "
                "add it there, with its engine and device-tree support, first"
            )
        elif "refused" in record:
            errors.append(f"{identifier} is refused: {record['refused']}")
    return errors


def app_minimum_errors(channel_minimum: str | None, identifiers: list[str],
                       path: Path = MANIFEST) -> list[str]:
    """Why CHANNEL_MINIMUM (the catalog's installer.minimumVersion) is too old
    for IDENTIFIERS: a catalog may not offer a Mac that the oldest app it
    admits would refuse."""
    minimums = app_minimums(path)
    needed = {
        identifier: minimums[identifier] for identifier in identifiers if identifier in minimums
    }
    if not needed:
        return []
    if channel_minimum is None:
        return [
            f"{identifier} needs app {version} or newer, but the catalog names no minimum version"
            for identifier, version in sorted(needed.items())
        ]
    if not INSTALLER_VERSION.fullmatch(channel_minimum):
        return [f"the catalog's minimum version {channel_minimum} is not an X.Y.Z version"]

    def parts(version: str) -> tuple[int, ...]:
        return tuple(int(piece) for piece in version.split("."))

    return [
        f"{identifier} needs app {version} or newer, but the catalog admits {channel_minimum}"
        for identifier, version in sorted(needed.items())
        if parts(channel_minimum) < parts(version)
    ]


def catalog_errors(catalog: dict, channel: str | None = None,
                   path: Path = MANIFEST) -> list[str]:
    """Why CATALOG does not cover exactly its channel's set. CHANNEL names the
    channel to judge it against; without one the catalog's own `channel` is
    used, which a catalog published before that field existed does not have."""
    if channel is None:
        channel = catalog.get("channel")
    if channel is not None and channel not in CHANNELS:
        return [f"{channel} is not one of {', '.join(CHANNELS)}"]
    models = catalog.get("models")
    if not isinstance(models, list):
        return ["the catalog has no models list"]
    if not models:
        return []
    enabled = [
        model.get("deviceIdentifier")
        for model in models
        if model.get("status") == "enabled"
    ]
    errors = [
        f"{model.get('deviceIdentifier')} is listed but {model.get('status')}"
        for model in models
        if model.get("status") != "enabled"
    ]
    installer = catalog.get("installer")
    minimum = installer.get("minimumVersion") if isinstance(installer, dict) else None
    # A catalog that names no channel is a private or developer one, not
    # something a channel URL serves, so it is judged by the private rules.
    coverage = (
        coverage_errors(enabled, channel, path)
        if channel is not None
        else private_coverage_errors(enabled, path)
    )
    return errors + coverage + app_minimum_errors(minimum, enabled, path)


def main(arguments: list[str]) -> int:
    if len(arguments) != 2 or arguments[0] != "check-catalog":
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 64
    errors = catalog_errors(json.loads(Path(arguments[1]).read_text()))
    for error in errors:
        print(f"catalog model coverage: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
