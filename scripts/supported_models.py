#!/usr/bin/env python3
"""The Macs every Omarchy Mac catalog must enable, and the ones it must refuse.

`supported-models.json` is the single list: every M1, M2 and M3 Mac the
pinned engine can inspect, and the MacBook Neo, all installed from the one
Aurora image. A catalog that offers any Mac offers all of them, so a release
or preview can never ship a subset by accident. A channel with no Mac release
yet (an empty catalog) is the only exception. rc and stable offer only the
supported Macs that are not `untested`: an untested Mac reaches them only
once it has been installed on real hardware and leaves `untested`. `refused` names each board that
stays out and why. `developer` names each board only a developer build's
sealed catalog may add, with its own image; channel catalogs never carry one,
so check-catalog rejects it like any unknown board. `untested` marks the
supported Macs no image has been qualified on yet, with the note a catalog
shows for each; the testing team qualifies them on edge, and rc and stable
never offer them. The M3 boards and the Neo have been installed and run on
real hardware; the M4 boards the engine names are untested; the M5 Air stays developer-only.

Usage:
  supported_models.py check-catalog FILE [CHANNEL]   exit 1 unless FILE covers every Mac CHANNEL offers
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MANIFEST = Path(__file__).resolve().with_name("supported-models.json")
# The channels that offer only Macs installed on real hardware.
RELEASE_CHANNELS = ("rc", "stable")


def load(path: Path = MANIFEST) -> tuple[list[str], dict[str, str]]:
    document = json.loads(path.read_text())
    if document.get("schema_version") != 1:
        raise SystemExit(f"{path}: unsupported schema")
    supported, refused = document["supported"], document["refused"]
    if len(set(supported)) != len(supported):
        raise SystemExit(f"{path}: supported lists a Mac twice")
    if set(supported) & set(refused):
        raise SystemExit(f"{path}: a Mac is both supported and refused")
    if set(document.get("developer", {})) & (set(supported) | set(refused)):
        raise SystemExit(f"{path}: a developer board is also supported or refused")
    return supported, refused


def developer_boards(path: Path = MANIFEST) -> dict[str, str]:
    """The boards only a developer build's sealed catalog may add, with why."""
    load(path)
    return dict(json.loads(path.read_text()).get("developer", {}))

def untested(path: Path = MANIFEST) -> dict[str, str]:
    """Supported Macs no Omarchy image has been installed on yet, each with
    the note a catalog shows for it."""
    supported, _ = load(path)
    notes = json.loads(path.read_text()).get("untested", {})
    if not isinstance(notes, dict) or not all(
        isinstance(note, str) and note for note in notes.values()
    ):
        raise SystemExit(f"{path}: untested maps each Mac to a note")
    stray = set(notes) - set(supported)
    if stray:
        raise SystemExit(f"{path}: untested names unsupported Macs: {', '.join(sorted(stray))}")
    return notes


def channel_supported(channel: str | None = None) -> list[str]:
    """The Macs a CHANNEL catalog enables: every supported Mac, less the
    untested ones on rc and stable."""
    supported, _ = load()
    held = untested() if channel in RELEASE_CHANNELS else {}
    return [identifier for identifier in supported if identifier not in held]


def coverage_errors(identifiers: list[str], channel: str | None = None) -> list[str]:
    """Why IDENTIFIERS (the Macs a catalog enables) is not exactly the set
    CHANNEL offers. Without a channel, every supported Mac."""
    _, refused = load()
    supported = channel_supported(channel)
    held = untested() if channel in RELEASE_CHANNELS else {}
    developer = developer_boards()
    errors = []
    missing = [identifier for identifier in supported if identifier not in identifiers]
    if missing:
        errors.append(
            "every supported Mac must be enabled; missing "
            + ", ".join(missing)
        )
    for identifier in identifiers:
        if identifier in refused:
            errors.append(f"{identifier} is refused: {refused[identifier]}")
        elif identifier in held:
            errors.append(f"{identifier} is untested: {channel} offers only Macs installed on real hardware")
        elif identifier in developer:
            errors.append(f"{identifier} is a developer board: only a developer build's sealed catalog offers it")
        elif identifier not in supported:
            errors.append(
                f"{identifier} is not in scripts/supported-models.json; "
                "add it there, with its engine and device-tree support, first"
            )
    return errors


# The first app that can finish an install through aura's Mach-O Stage 1: its
# Recovery takes the Mach-O baseline and its Remove recognises aura's boot object
# (omacom/omarchy-mac-installer#238). An older app partitions an M4 and then
# stops at "The old Recovery payload inventory is invalid", so a catalog that
# offers an untested Mac, which today is every M4 and later Mac, must refuse it.
UNTESTED_APP_FLOOR = "2.3.17"


def _version(value: object) -> tuple[int, ...] | None:
    parts = value.split(".") if isinstance(value, str) else []
    return tuple(int(part) for part in parts) if len(parts) == 3 and all(p.isdigit() for p in parts) else None


def catalog_errors(catalog: dict, channel: str | None = None) -> list[str]:
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
    if channel in RELEASE_CHANNELS:
        errors += [
            f"{model.get('deviceIdentifier')} carries a preview note: {channel} offers no preview Mac"
            for model in models
            if "previewNote" in model
        ]
    # Edge shows each untested Mac its note (rc and stable refuse it above).
    held = untested()
    if channel not in RELEASE_CHANNELS:
        errors += [
            f"{model.get('deviceIdentifier')} is untested but carries no preview note: "
            "a catalog shows each untested Mac its note"
            for model in models
            if model.get("deviceIdentifier") in held and "previewNote" not in model
        ]
    # The floor follows the Macs themselves, not only their notes: a catalog that
    # enables an untested Mac without its note still lets an older app partition it.
    if any("previewNote" in model or model.get("deviceIdentifier") in held for model in models):
        installer = catalog.get("installer")
        minimum = _version(installer.get("minimumVersion")) if isinstance(installer, dict) else None
        if minimum is None or minimum < _version(UNTESTED_APP_FLOOR):
            errors.append(f"a catalog offering an untested Mac needs installer.minimumVersion {UNTESTED_APP_FLOOR} "
                          "or later, the first app that can finish an install on one")
    return errors + coverage_errors(enabled, channel)


def main(arguments: list[str]) -> int:
    if len(arguments) not in (2, 3) or arguments[0] != "check-catalog":
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 64
    channel = arguments[2] if len(arguments) == 3 else None
    errors = catalog_errors(json.loads(Path(arguments[1]).read_text()), channel)
    for error in errors:
        print(f"catalog model coverage: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
