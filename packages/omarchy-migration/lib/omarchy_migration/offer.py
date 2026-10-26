"""Offer a claimed Try export to the owner once their session starts.

Runs from the omarchy-migration-offer user unit. It notifies when an export
waits that this user hasn't imported or dismissed, and once when the first
boot couldn't claim the staged export.
"""

import argparse
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import contract
from .claim import FAILED_NAME, FAILED_SCHEMA, STATE
from .handoff import INCOMING, pending_exports

OFFER_TITLE = "Bring your files from Try Omarchy"
OFFER_BODY = "Review what comes across from your Try Omarchy home, then import it."
FAILED_TITLE = "Your Try Omarchy files stayed on the Mac"
FAILED_BODY = "This Mac couldn't take the export at first boot. It's still in Try Omarchy's Exports on the Mac."
IMPORT = ("omarchy-launch-floating-terminal-with-presentation", "omarchy-migration-import")
MAX_RECORD = 4096
FALLBACK_PATH = ("/usr/share/omarchy/bin", "/usr/local/bin", "/usr/bin")


def user_state(home):
    return Path(home, ".local/state/omarchy-migration")


def _done(records, export_id):
    return any((records / folder / name).exists()
               for folder, name in (("imported", f"{export_id}.json"), ("dismissed", export_id)))


def handed_off(home):
    """Exports already moved into this home that still hold their bundle."""
    folder = Path(home, *INCOMING)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    exports = []
    for name in names:
        with contextlib.suppress(contract.ContractError):
            contract._uuid(name, "$")
            if (folder / name / "bundle.age").is_file() and not (folder / name).is_symlink():
                exports.append(name)
    return exports


def claim_failure(state):
    """The first boot's failure record, when there is a well-formed one."""
    try:
        fd = os.open(Path(state) / FAILED_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as source:
        data = source.read(MAX_RECORD + 1)
    try:
        record = json.loads(data, object_pairs_hook=contract._unique_pairs, parse_constant=contract._reject_constant)
        if (len(data) > MAX_RECORD or not isinstance(record, dict) or set(record) != {"schema", "export_id", "reason"}
                or record["schema"] != FAILED_SCHEMA):
            return None
        if record["export_id"] is not None:
            contract._uuid(record["export_id"], "$.export_id")
        contract._pattern(record["reason"], "$.reason", contract.CODE)
    except (ValueError, contract.ContractError):
        return None
    return record


def _failure_key(record):
    return f"{record['export_id']} {record['reason']}\n"


def decide(state, home):
    """What to tell this user now: ("failed", record) and ("offer", export ids)."""
    records = user_state(home)
    actions = []
    failure = claim_failure(state)
    if failure is not None:
        try:
            told = (records / "claim-failed-notified").read_text()
        except (OSError, UnicodeDecodeError):
            told = None
        if told != _failure_key(failure):
            actions.append(("failed", failure))
    waiting = sorted({*pending_exports(state), *handed_off(home)})
    waiting = [export_id for export_id in waiting if not _done(records, export_id)]
    if waiting:
        actions.append(("offer", waiting))
    return actions


def remember_failure(home, record):
    records = user_state(home)
    records.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = records / "claim-failed-notified.partial"
    temporary.write_text(_failure_key(record))
    os.replace(temporary, records / "claim-failed-notified")


def _command(name, search):
    return shutil.which(name, path=os.pathsep.join(search))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, default=STATE, help=argparse.SUPPRESS)
    parser.add_argument("--home", type=Path, default=Path.home(), help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    actions = decide(arguments.state, arguments.home)
    if not actions:
        return 0
    search = [*os.environ.get("PATH", "").split(os.pathsep)]
    if os.environ.get("OMARCHY_PATH"):
        search.append(os.path.join(os.environ["OMARCHY_PATH"], "bin"))
    search.extend(FALLBACK_PATH)
    send = _command("omarchy-notification-send", search)
    if send is None:
        print("omarchy-migration offer: omarchy-notification-send is not available", file=sys.stderr)
        return 0
    wait = _command("omarchy-notification-wait", search)
    if wait is not None:
        subprocess.run([wait], check=False)
    shown = []
    for kind, value in actions:
        if kind == "failed":
            shown.append(subprocess.Popen([send, "-u", "critical", FAILED_TITLE, FAILED_BODY]))
            remember_failure(arguments.home, value)
        else:
            shown.append(subprocess.Popen([send, "-u", "critical", OFFER_TITLE, OFFER_BODY, "--exec", *IMPORT]))
    for process in shown:
        process.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
