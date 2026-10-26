"""Read-only migration survey of the caller's own home directory.

Lists directories and reads metadata only: it never opens a regular file,
never follows a link, never enters a credential store, an excluded path or
another filesystem, and writes nothing. The result is an inventory/2
document plus a private, local-only detail summary.
"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import platform
import stat
import sys
import uuid

from . import contract, policy as migration_policy
from . import collection
from .display import shown
from .categories import CATEGORIES, DEFAULT_SELECTED, category

POLICY_PATH = migration_policy.SHIPPED
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
MAX_DEPTH = 64
MAX_ENTRIES = 2_000_000
EXAMPLES = 10


class SurveyError(RuntimeError):
    pass


class Survey:
    def __init__(self, policy, max_entries=MAX_ENTRIES):
        self.policy = policy
        self.max_entries = max_entries
        self.entries = 0
        self.counts = {name: Counter() for name in CATEGORIES}
        self.stores = {}
        self.outcomes = {}
        self.linked_mounts = set()
        self.shares = {}
        self.top_level = Counter()

    def note(self, outcome, path, **fields):
        bucket = self.outcomes.setdefault(outcome, {"count": 0, "examples": []})
        bucket["count"] += 1
        if len(bucket["examples"]) < EXAMPLES:
            bucket["examples"].append({"path": path, **fields})

    def run(self, home):
        if os.geteuid() == 0:
            raise SurveyError("run the survey as the account that owns the home, not root")
        fd = os.open(home, DIRECTORY_FLAGS)
        try:
            metadata = os.fstat(fd)
            if metadata.st_uid != os.geteuid():
                raise SurveyError("the home directory is owned by another account")
            self.mount = collection._mount(fd)
            self.device = metadata.st_dev
            self.directory(fd, "", 0)
        finally:
            os.close(fd)

    def directory(self, fd, prefix, depth):
        try:
            with os.scandir(fd) as listing:
                names = sorted(entry.name for entry in listing)
        except OSError:
            self.note("unreadable", prefix or ".")
            return
        for name in names:
            path = f"{prefix}/{name}" if prefix else name
            self.entries += 1
            if self.entries > self.max_entries:
                raise SurveyError(f"more than {self.max_entries} entries; survey stopped")
            try:
                contract.home_path(path, "path")
            except contract.ContractError:
                self.note("unsupported", path, reason="unrepresentable-name")
                continue
            self.entry(fd, name, path, depth)

    def entry(self, parent, name, path, depth):
        match = self.policy.match(path)
        if match and match.kind == "store":
            # Never listed or measured: only its presence is reported.
            store = match.item
            self.stores.setdefault(store["id"], {"category": store["category"], "present": True})
            self.note("held-out", path, store=store["id"])
            return
        if match and match.item["action"] == "exclude":
            self.note("excluded", path, rule=match.item["id"], reason=match.item["reason"])
            return
        try:
            metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            self.note("changed-during-survey", path)
            return
        kind = category(path)
        if stat.S_ISLNK(metadata.st_mode):
            try:
                target = os.readlink(name, dir_fd=parent)
            except OSError:
                self.note("changed-during-survey", path)
                return
            mount = self.policy.mount(target)
            if mount:
                self.note("share-link", path, mount=mount["id"])
                if collection._Snapshot._is_mount_root(target, mount):
                    self.linked_mounts.add(mount["id"])
            else:
                self.counts[kind]["links"] += 1
            return
        if stat.S_ISDIR(metadata.st_mode):
            if depth + 1 >= MAX_DEPTH:
                self.note("unsupported", path, reason="too-deep")
                return
            try:
                fd = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
            except OSError:
                self.note("unreadable", path)
                return
            try:
                if os.fstat(fd).st_dev != self.device or collection._mount(fd) != self.mount:
                    self.note("other-filesystem", path)
                    return
                self.counts[kind]["directories"] += 1
                if lost := collection.metadata_losses(fd, metadata):
                    self.note("metadata-not-preserved", path, lost=" ".join(lost))
                self.directory(fd, path, depth + 1)
            finally:
                os.close(fd)
            return
        if stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                self.note("unsupported", path, reason="multiply-linked-file")
                return
            if not metadata.st_mode & 0o400:
                self.note("unsupported", path, reason="unreadable-owner-file")
                return
            self.counts[kind]["files"] += 1
            self.counts[kind]["bytes"] += metadata.st_size
            # Attribute names only, through the parent descriptor; the file is never opened.
            if DEFAULT_SELECTED[kind] and (lost := collection.metadata_losses(f"/proc/self/fd/{parent}/{name}", metadata)):
                self.note("metadata-not-preserved", path, lost=" ".join(lost))
            if DEFAULT_SELECTED[kind]:
                # Unselected categories (caches) are shown only as a total.
                self.top_level[size_key(path)] += metadata.st_size
            if match and match.item["action"] == "transform":
                # Content is not read, so whether Try's additions are present is unknown.
                self.note("transform", path, rule=match.item["id"])
            return
        self.note("unsupported", path, reason="special-file")

    def measure_share(self, mount, root):
        """Count a shared folder read-only; credential stores are noted by name, never entered."""
        fd = os.open(os.path.realpath(root), DIRECTORY_FLAGS)
        share = {"files": 0, "bytes": 0, "stores": set(), "mount": collection._mount(fd), "device": os.fstat(fd).st_dev}
        try:
            self._share_directory(fd, "", share, 0)
        finally:
            os.close(fd)
        self.shares[mount["id"]] = share

    def _share_directory(self, fd, prefix, share, depth):
        try:
            with os.scandir(fd) as listing:
                names = sorted(entry.name for entry in listing)
        except OSError:
            return
        for name in names:
            path = f"{prefix}/{name}" if prefix else name
            self.entries += 1
            if self.entries > self.max_entries:
                raise SurveyError(f"more than {self.max_entries} entries; survey stopped")
            store = self.policy.share_store(path)
            if store:
                share["stores"].add(store["id"])
                continue
            try:
                metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(metadata.st_mode):
                share["files"] += 1
                share["bytes"] += metadata.st_size
            elif stat.S_ISDIR(metadata.st_mode) and depth + 1 < MAX_DEPTH:
                try:
                    child = os.open(name, DIRECTORY_FLAGS, dir_fd=fd)
                except OSError:
                    continue
                try:
                    if os.fstat(child).st_dev == share["device"] and collection._mount(child) == share["mount"]:
                        self._share_directory(child, path, share, depth + 1)
                finally:
                    os.close(child)

    def mount_documents(self):
        documents = []
        for mount in self.policy.mounts:
            document = {"id": mount["id"], "linked": mount["id"] in self.linked_mounts,
                        "measured": mount["id"] in self.shares}
            if document["measured"]:
                share = self.shares[mount["id"]]
                document.update(files=share["files"], bytes=share["bytes"],
                                share_stores=[{"id": store["id"], "category": store["category"]}
                                              for store in self.policy.share_stores if store["id"] in share["stores"]])
            documents.append(document)
        return documents

    def store_documents(self):
        documents = []
        for store in self.policy.stores:
            documents.append({"id": store["id"], "category": store["category"],
                              "present": store["id"] in self.stores, "adapter_available": False})
        return documents


def size_key(path):
    """Group sizes by top-level entry, or three levels deep inside dot-folders."""
    parts = path.split("/")
    return "/".join(parts[:3]) if parts[0].startswith(".") else parts[0]


def omarchy_version(home, explicit):
    if explicit:
        return explicit
    for candidate in (Path(home) / ".local/share/omarchy/version", Path("/usr/share/omarchy/version")):
        try:
            value = candidate.read_text().strip()
        except OSError:
            continue
        if contract.VERSION.fullmatch(value):
            return value
    return "unknown"


def inventory(survey, home, version, uid=None):
    architecture = platform.machine()
    document = {
        "schema": contract.INVENTORY,
        "inventory_id": str(uuid.uuid4()),
        "source": {"provider": "try-omarchy", "architecture": architecture,
                   "omarchy_version": omarchy_version(home, version),
                   "account_uid": os.getuid() if uid is None else uid},
        "policy_revision": survey.policy.revision,
        "categories": [{"id": name, "files": survey.counts[name]["files"], "bytes": survey.counts[name]["bytes"],
                        "default_selected": DEFAULT_SELECTED[name]} for name in CATEGORIES],
        "credential_stores": survey.store_documents(),
        "mounts": survey.mount_documents(),
    }
    contract.validate(document)
    return document


def size(value):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{value} B"
        value /= 1024


def summary(survey, document):
    lines = [f"Migration survey (policy {document['policy_revision']}, read-only; no file contents were read)", ""]
    lines.append("Categories:")
    for item in document["categories"]:
        default = "selected by default" if item["default_selected"] else "not selected by default"
        lines.append(f"  {item['id']:<20} {item['files']:>8} files  {size(item['bytes']):>11}  ({default})")
    present = [store["id"] for store in document["credential_stores"] if store["present"]]
    lines += ["", "Credential stores found (held back; none can be exported yet): " + (", ".join(present) or "none")]
    for mount in document["mounts"]:
        if mount["measured"]:
            stores = ", ".join(store["id"] for store in mount["share_stores"]) or "none"
            lines += ["", f"Shared folder {mount['id']}: {mount['files']} files, {size(mount['bytes'])}"
                          f" (only if you select it); credential stores inside, offered separately: {stores}"]
        elif mount["linked"]:
            lines += ["", f"Shared folder {mount['id']} is linked from your home; add --measure-shares to size it"]
    labels = {
        "excluded": "Excluded by the Try policy",
        "transform": "Would be cleaned of Try additions (content not checked)",
        "share-link": "Links into the Mac shared folder (not followed)",
        "other-filesystem": "Other filesystems (not entered)",
        "unsupported": "Unsupported (withheld)",
        "metadata-not-preserved": "Copied without some metadata (extended attributes, ACLs, sparseness, setuid bits)",
        "unreadable": "Unreadable (skipped)",
        "changed-during-survey": "Changed during the survey",
    }
    for outcome, label in labels.items():
        bucket = survey.outcomes.get(outcome)
        if not bucket:
            continue
        lines += ["", f"{label}: {bucket['count']}"]
        for example in bucket["examples"]:
            extra = ", ".join(f"{key}={value}" for key, value in example.items() if key != "path")
            lines.append(f"  ~/{example['path']}" + (f"  ({extra})" if extra else ""))
        if bucket["count"] > len(bucket["examples"]):
            lines.append(f"  … and {bucket['count'] - len(bucket['examples'])} more")
    largest = survey.top_level.most_common(EXAMPLES)
    if largest:
        lines += ["", "Largest entries that would migrate by default (dot-folders shown three levels deep):"]
        lines += [f"  ~/{name:<30} {size(total):>11}" for name, total in largest]
    return "\n".join(shown(line) for line in lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--json", action="store_true", help="print the inventory/2 document instead of a summary")
    parser.add_argument("--omarchy-version", help="override the detected Omarchy version")
    parser.add_argument("--measure-shares", action="store_true",
                        help="also count linked shared folders (read-only; credential stores are not entered)")
    arguments = parser.parse_args(argv)
    policy = migration_policy.Policy(json.loads(POLICY_PATH.read_bytes()))
    survey = Survey(policy)
    try:
        survey.run(arguments.home)
        if arguments.measure_shares:
            for mount in policy.mounts:
                if mount["id"] in survey.linked_mounts and os.path.isdir(mount["path"]):
                    survey.measure_share(mount, mount["path"])
        document = inventory(survey, arguments.home, arguments.omarchy_version)
    except (SurveyError, contract.ContractError, OSError) as error:
        print(f"survey: {error}", file=sys.stderr)
        return 1
    print(json.dumps(document, indent=2, sort_keys=True) if arguments.json else summary(survey, document))
    return 0


if __name__ == "__main__":
    sys.exit(main())
