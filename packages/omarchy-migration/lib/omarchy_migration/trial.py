"""Trial migration of this home into a private scratch folder, then compare.

Exports the home with the default categories and a random passphrase that
never leaves memory, restores the bundle into a private folder outside the
home, and compares every entry with the live home. The home is only read.
"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys
import tempfile
import time
import uuid

from . import contract, export, restore, review
from .categories import CATEGORIES, DEFAULT_SELECTED
from .dependency import admit_age

EXAMPLES = 10


def default_request(policy_revision, categories=None):
    chosen = categories or [name for name in CATEGORIES if DEFAULT_SELECTED[name]]
    return {"schema": contract.EXPORT_REQUEST, "request_id": str(uuid.uuid4()), "inventory_id": str(uuid.uuid4()),
            "policy_revision": policy_revision,
            "selection": {"categories": sorted(chosen), "credential_stores": [], "mounts": [], "share_stores": []}}


def _same_file(source, destination, entry):
    try:
        restored = destination.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISREG(restored.st_mode) and destination.read_bytes() == source.read_bytes()
            and stat.S_IMODE(restored.st_mode) == entry["mode"] and restored.st_mtime_ns == entry["mtime_ns"])


def compare(home, target, manifest, results, started_ns):
    """One outcome per manifest entry: what the restored copy shows against the live home."""
    provenance = manifest.get("provenance") or {}
    transformed = {item["archive"] for item in provenance.get("collection", {}).get("exceptions", [])
                   if item["outcome"] == "transformed"}
    originals = provenance.get("originals")
    outcomes, examples = Counter(), {}

    def record(outcome, path):
        outcomes[outcome] += 1
        examples.setdefault(outcome, [])
        if len(examples[outcome]) < EXAMPLES:
            examples[outcome].append(path)

    for entry in manifest["entries"]:
        path, kind = entry["path"], entry["kind"]
        source, destination = Path(home) / path, Path(target) / path
        if originals and (path == originals or path.startswith(originals + "/")):
            record("original-copy", path)
            continue
        status = results.get(path)
        if originals and originals.startswith(path + "/") and not os.path.lexists(source):
            # An ancestor the export created to hold the original copies.
            record("original-copy", path)
            continue
        if status == "inert":
            record("inert", path)
            continue
        if status == "conflict":
            record("unexpected", path)
            continue
        try:
            live = source.lstat()
        except FileNotFoundError:
            record("changed-since-export", path)
            continue
        if max(live.st_mtime_ns, live.st_ctime_ns) >= started_ns:
            record("changed-since-export", path)
        elif kind == "directory":
            record("identical" if destination.is_dir() and not destination.is_symlink() else "unexpected", path)
        elif kind == "symlink":
            same = destination.is_symlink() and os.readlink(destination) == entry["target"]
            record("identical" if same else "unexpected", path)
        elif path in transformed:
            copy = Path(target) / originals / path if originals else None
            kept = copy is not None and copy.is_file() and copy.read_bytes() == source.read_bytes()
            record("transformed" if kept and destination.is_file() else "unexpected", path)
        else:
            record("identical" if _same_file(source, destination, entry) else "unexpected", path)
    return {"outcomes": dict(sorted(outcomes.items())), "examples": examples}


def run_trial(home, workdir, *, age, policy_document, categories=None, emit=lambda phase, **fields: None):
    secret = secrets.token_urlsafe(32).encode()  # a self-test: never shown or stored
    request = default_request(policy_document["revision"], categories)
    started_ns = time.time_ns()
    receipt = export.export_home(request, home, workdir / "export", age=age, secret=secret,
                                 policy_document=policy_document, scratch=workdir, emit=emit)
    target, job = workdir / "restore", workdir / "job"
    for directory in (target, job):
        directory.mkdir(mode=0o700)
    emit("restoring")
    with restore.verified_bundle(age, secret, workdir / "export/bundle.age") as bundle:
        with restore.Restorer(bundle, target, job) as importer:
            actions = importer.plan()
            plan = review.plan_document(bundle, actions, receipt, os.getuid(), importer._binding)
            results = importer.apply(actions)
            report = review.report_document(plan, results, review.job_identity(importer._binding), bundle)
        manifest = bundle._manifest
    comparison = compare(home, target, manifest, {result.path: result.status for result in results}, started_ns)
    provenance = manifest.get("provenance") or {}
    summary = {
        "workdir": str(workdir), "receipt": receipt, "plan": plan, "report": report, "comparison": comparison,
        "exceptions": Counter(item["outcome"] for item in provenance.get("collection", {}).get("exceptions", [])),
        "metadata_losses": len(provenance.get("metadata", [])),
    }
    with open(workdir / "trial-report.json", "w", encoding="utf-8") as output:
        json.dump(summary, output, indent=2, sort_keys=True)
    return summary


def describe(summary, removing=False):
    comparison, receipt = summary["comparison"], summary["receipt"]
    lines = [f"Trial migration (policy {receipt['policy_revision']}): the home was only read.", "",
             f"Bundle: {receipt['bundle']['bytes']} bytes encrypted, {receipt['estimates']['entries']} entries, "
             f"{receipt['estimates']['expanded_bytes']} bytes of content", "", "Restored copy compared with your home:"]
    labels = {"identical": "identical", "transformed": "cleaned of Try additions (original kept)",
              "original-copy": "entries holding original copies of cleaned files", "inert": "links not recreated (as intended)",
              "changed-since-export": "changed in your home since the export (expected while you work)",
              "unexpected": "UNEXPECTED differences"}
    for outcome, label in labels.items():
        if comparison["outcomes"].get(outcome):
            lines.append(f"  {comparison['outcomes'][outcome]:>7}  {label}")
    for outcome in ("unexpected", "changed-since-export"):
        for path in comparison["examples"].get(outcome, []):
            lines.append(f"           {outcome}: ~/{path}")
    held = summary["exceptions"]
    lines += ["", "Not exported, by policy: " + (", ".join(f"{count} {outcome}" for outcome, count in sorted(held.items())) or "nothing")]
    if summary["metadata_losses"]:
        lines.append(f"Copied without some metadata: {summary['metadata_losses']} entries")
    if not removing:
        lines += ["", f"Details: {summary['workdir']}/trial-report.json",
                  f"The restored copy is a private plaintext copy of your files; remove it with: rm -rf {summary['workdir']}"]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--age", default=None, help="age executable (default: age on PATH)")
    parser.add_argument("--workdir-parent", type=Path, default=Path("/var/tmp"),
                        help="where the private trial folder is created (default /var/tmp)")
    parser.add_argument("--category", action="append", choices=CATEGORIES, help="override the default categories")
    parser.add_argument("--remove", action="store_true", help="delete the trial folder when nothing unexpected was found")
    parser.add_argument("--json", action="store_true", help="print the full trial report")
    arguments = parser.parse_args(argv)
    workdir = Path(tempfile.mkdtemp(prefix="omarchy-migration-trial-", dir=arguments.workdir_parent))
    workdir.chmod(0o700)
    if workdir.resolve().is_relative_to(arguments.home.resolve()):
        shutil.rmtree(workdir)
        print("trial: the trial folder must be outside the home", file=sys.stderr)
        return 1
    try:
        age = admit_age(arguments.age)
        summary = run_trial(arguments.home, workdir, age=age,
                            policy_document=json.loads(export.POLICY_PATH.read_bytes()), categories=arguments.category,
                            emit=lambda phase, **fields: print(f"trial: {phase}", file=sys.stderr, flush=True))
    except Exception as error:  # report and keep the folder for inspection
        print(f"trial: failed: {error}\ntrial: folder kept for inspection: {workdir}", file=sys.stderr)
        return 1
    removing = arguments.remove and not summary["comparison"]["outcomes"].get("unexpected")
    print(json.dumps(summary, indent=2, sort_keys=True, default=dict) if arguments.json else describe(summary, removing))
    if removing:
        shutil.rmtree(workdir)
        print(f"Removed {workdir}.")
    return 1 if summary["comparison"]["outcomes"].get("unexpected") else 0


if __name__ == "__main__":
    sys.exit(main())
