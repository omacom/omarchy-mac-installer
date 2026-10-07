"""Policy-aware snapshots of caller-owned disposable fixture trees.

The source root stands for the owner's home: policy paths are relative to it.
No home CLI or production adapter. Sources must be quiescent; change detection
does not establish an atomic application/database snapshot.
"""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import posixpath
import shutil
import stat
import tempfile
import types
import uuid

from . import contract, policy as migration_policy
from . import probe


REQUEST_SCHEMA = "omarchy-migration-collection-request/3"
REPORT_SCHEMA = "omarchy-migration-collection-report/2"
# Free space left untouched when copying a snapshot.
SNAPSHOT_MARGIN = 64 * 1024 * 1024
# Untouched copies of transformed files, restored as migration-owned files.
ORIGINALS_ROOT = ".local/share/omarchy-migration/originals"
MAX_DEPTH = 64
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _path(value, *, empty=False):
    try:
        if (type(value) is not str or (not value and not empty) or "\0" in value
                or len(value.encode("utf-8")) > 4096
                or value.count("/") >= MAX_DEPTH
                or (value and any(part in ("", ".", "..") or len(part.encode("utf-8")) > 255
                                  for part in value.split("/")))):
            raise probe.Rejected("invalid collection relative path")
    except UnicodeError as error:
        raise probe.Rejected("invalid collection path encoding") from error
    return value


def _beneath(path, root):
    return not root or path == root or path.startswith(root + "/")


class _Skip(Exception):
    """An entry inside a shared folder that is reported and skipped, not fatal."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _contracts(request, document, supported):
    if (not isinstance(request, dict)
            or set(request) != {"schema", "request_id", "selection", "selected_adapters", "selected_mounts",
                                "selected_share_stores"}
            or request["schema"] != REQUEST_SCHEMA):
        raise probe.Rejected("collection request fields/schema")
    try:
        if type(request["request_id"]) is not str or str(uuid.UUID(request["request_id"])) != request["request_id"]:
            raise ValueError()
    except ValueError as error:
        raise probe.Rejected("collection request identity") from error
    selection = request["selection"]
    # A real home selects each top-level entry of its chosen categories.
    if not isinstance(selection, list) or not 1 <= len(selection) <= contract.MAX_ITEMS:
        raise probe.Rejected("collection selections")
    for item in selection:
        if not isinstance(item, dict) or set(item) != {"source", "archive"}:
            raise probe.Rejected("collection selection fields")
        _path(item["source"], empty=True)
        _path(item["archive"], empty=True)
        # This slice remaps whole roots, without inventing ancestor metadata.
        if (not item["archive"] and item["source"]) or "/" in item["archive"]:
            raise probe.Rejected("collection archive root requires one component")
    # Two roots overlap when one equals or is an ancestor of the other; check
    # each root's ancestors against the set rather than every pair.
    for key in ("source", "archive"):
        values = [item[key] for item in selection]
        present = set(values)
        if len(present) != len(values) or ("" in present and len(values) > 1):
            raise probe.Rejected("overlapping collection selections")
        for value in values:
            parts = value.split("/")
            if any("/".join(parts[:end]) in present for end in range(1, len(parts))):
                raise probe.Rejected("overlapping collection selections")
    try:
        # The contract validates store, mount and rule shapes and overlaps.
        loaded = migration_policy.Policy(document)
    except contract.ContractError as error:
        raise probe.Rejected(f"trusted policy: {error}") from error
    adapters = set()
    for store in loaded.stores:
        adapter = store["adapter"]
        if adapter is not None:
            # This disposable collector never enables a production adapter.
            if not adapter.startswith("fixture-") or adapter in adapters:
                raise probe.Rejected("fixture adapter identity")
            adapters.add(adapter)
    chosen = request["selected_adapters"]
    if (not isinstance(chosen, list) or any(type(value) is not str for value in chosen)
            or len(set(chosen)) != len(chosen) or not set(chosen) <= adapters
            or not isinstance(supported, (tuple, list, set, frozenset))
            or any(type(value) is not str for value in supported) or not set(supported) <= adapters):
        raise probe.Rejected("fixture adapter selection/capability")
    mounts = request["selected_mounts"]
    if (not isinstance(mounts, list) or any(type(value) is not str for value in mounts)
            or len(set(mounts)) != len(mounts) or not set(mounts) <= {mount["id"] for mount in loaded.mounts}):
        raise probe.Rejected("mount selection")
    chosen_stores = request["selected_share_stores"]
    if (not isinstance(chosen_stores, list) or any(type(value) is not str for value in chosen_stores)
            or len(set(chosen_stores)) != len(chosen_stores)
            or not set(chosen_stores) <= {store["id"] for store in loaded.share_stores}):
        raise probe.Rejected("share store selection")
    for value in (request, document):
        if len(_json_bytes(value)) > probe.MAX_MANIFEST:
            raise probe.Rejected("collection contract size")
    return loaded


def metadata_losses(target, metadata):
    """Kinds of metadata on an entry that the bundle will not carry.

    `target` is an open descriptor or a path read without following links.
    Only attribute names are listed; no contents are read.
    """
    losses = set()
    try:
        names = os.listxattr(target) if isinstance(target, int) else os.listxattr(target, follow_symlinks=False)
    except OSError:
        names = []  # Filesystems without extended attributes have none to lose.
    acl_names = ("system.posix_acl_access", "system.posix_acl_default", "system.nfs4_acl")
    if any(name in acl_names for name in names):
        losses.add("acl")
    if any(name not in acl_names for name in names):
        losses.add("extended-attributes")
    if stat.S_ISREG(metadata.st_mode) and metadata.st_size and metadata.st_blocks * 512 < metadata.st_size:
        losses.add("sparse")
    if metadata.st_mode & 0o7000:
        losses.add("special-permission-bits")
    return sorted(losses)


def _metadata(value):
    return tuple(getattr(value, key) for key in (
        "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size",
        "st_mtime_ns", "st_ctime_ns", "st_nlink",
    ))


def _mount(fd):
    # st_dev alone cannot detect bind mounts of the same filesystem.
    with open(f"/proc/self/fdinfo/{fd}", encoding="ascii") as info:
        for line in info:
            if line.startswith("mnt_id:"):
                return int(line.split(":", 1)[1])
    raise probe.Rejected("source mount identity unavailable")


class _Snapshot:
    def __init__(self, root_fd, root, directory, request, policy, supported, share_roots, budget):
        self.root_fd, self.root, self.directory = root_fd, root, directory
        self.request, self.policy = request, policy
        self.supported = frozenset(supported)
        self.mount = _mount(root_fd)
        self.share_roots = share_roots
        self.budget = budget
        self.footprint = 0  # whole-block estimate of what the snapshot occupies
        self.losses = {}  # archive path -> metadata the bundle cannot carry
        self.original_losses = {}  # transformed archive path -> its source's losses
        # Set only while walking a selected shared folder's contents.
        self.share = None
        self.materialized = set()
        self.paths, self.metadata, self.history = {}, {}, {}
        self.originals = []
        self.items, self.total = [], 0
        self.report = None
        self.manifest = None

    def _guard(self, fd):
        metadata = os.fstat(fd)
        if self.share is not None:
            # Shared folders hold the Mac's files: odd entries are skipped, not fatal.
            if _mount(fd) != self.share["mount"]:
                raise _Skip("other-filesystem")
            if stat.S_ISDIR(metadata.st_mode) and metadata.st_mode & 0o022:
                raise _Skip("unsafe-permissions")
            return
        if _mount(fd) != self.mount or metadata.st_uid != os.geteuid():
            raise probe.Rejected("source owner or mount differs")
        if stat.S_ISDIR(metadata.st_mode) and metadata.st_mode & 0o022:
            raise probe.Rejected("unsafe source directory")

    def _room(self, size):
        """Whether `size` more bytes fit the content ceiling and free space."""
        return (self.total + size <= probe.MAX_TOTAL
                and self.footprint + probe.footprint(size) <= self.budget)

    def _match(self, path):
        # Home policy describes home paths; shared folder contents are not.
        return self.policy.match(path) if path and self.share is None else None

    def _store(self, path):
        match = self._match(path)
        if match and match.kind == "store":
            adapter = match.item["adapter"]
            if adapter is None or adapter not in self.supported:
                return match.item["id"], "adapter-unavailable"
            if adapter not in self.request["selected_adapters"]:
                return match.item["id"], "unselected-store"
        return None

    def _rule(self, path):
        match = self._match(path)
        return match.item if match and match.kind == "rule" else None

    def _report(self, source, archive, outcome, reason, store=None, rule=None, mount=None):
        if len(self.items) >= probe.MAX_ENTRIES:
            raise probe.Rejected("collection item limit")
        self.items.append({"source": source, "archive": archive, "outcome": outcome,
                           "reason": reason, "store": store, "rule": rule, "mount": mount})

    @contextlib.contextmanager
    def _parent(self, path):
        fd = os.dup(self.root_fd)
        prefix = []
        try:
            for part in path.split("/")[:-1]:
                prefix.append(part)
                child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
                self._guard(fd)
                metadata = os.fstat(fd)
                if metadata.st_mode & 0o022:
                    raise probe.Rejected("unsafe source ancestor")
                self.history.setdefault("/".join(prefix), _metadata(metadata))
            yield fd
        finally:
            os.close(fd)

    def _names(self, fd):
        names = []
        with os.scandir(fd) as entries:
            for entry in entries:
                _path(entry.name)
                names.append(entry.name)
                if len(names) > probe.MAX_ENTRIES:
                    raise probe.Rejected("source directory item limit")
        return sorted(names)

    def _directory(self, fd, source, archive, metadata):
        self._guard(fd)
        if self.share is None and metadata.st_mode & 0o022:
            raise probe.Rejected("unsafe source directory")
        names = self._names(fd)
        if self.share is None:
            self.history[source] = _metadata(metadata)
        for name in names:
            self._walk(fd, name, f"{source}/{name}" if source else name,
                       f"{archive}/{name}" if archive else name)
        if _metadata(os.fstat(fd)) != _metadata(metadata) or self._names(fd) != names:
            raise probe.Rejected("source directory changed during capture")

    def _walk(self, parent, name, source, archive):
        if self.share is None:
            return self._entry(parent, name, source, archive)
        mark = (len(self.items), set(self.paths), self.total)
        try:
            self._entry(parent, name, source, archive)
        except (_Skip, PermissionError) as error:
            self._rollback(*mark)
            reason = error.reason if isinstance(error, _Skip) else "unreadable"
            self._report(source, archive, "unsupported", reason, mount=self.share["id"])

    def _rollback(self, items, paths, total):
        """Forget everything a skipped shared-folder entry registered or wrote."""
        del self.items[items:]
        self.total = total
        for key in set(self.paths) - paths:
            del self.paths[key], self.metadata[key]
        kept = {path.name for path in self.paths.values()}
        for path in self.directory.iterdir():
            if path.name.isdigit() and path.name not in kept:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()

    def _entry(self, parent, name, source, archive):
        _path(source)
        _path(archive)
        if self.share is not None:
            # Recognized credential locations are held back by name, unopened,
            # unless the user ticked them.
            relative = archive[len(self.share["archive"]) + 1:]
            parts = relative.split("/")
            # Inside a store the user ticked, everything comes along: no other
            # pattern (such as a key file name) holds part of it back.
            inside_selected = any(
                # Only folder patterns make a ticked store; a directory merely named like
                # a key file (certs.pem/) must not unlock unticked stores beneath it.
                (found := self.policy.share_store("/".join(parts[:depth]), directories_only=True))
                and found["id"] in self.request["selected_share_stores"] for depth in range(1, len(parts)))
            store = None if inside_selected else self.policy.share_store(relative)
            if store and store["id"] not in self.request["selected_share_stores"]:
                self._report(source, archive, "held-out", "unselected-store", store=store["id"], mount=self.share["id"])
                return
        excluded = self._store(source)
        if excluded:
            self._report(source, archive, "held-out", excluded[1], excluded[0])
            return
        rule = self._rule(source)
        if rule and rule["action"] == "exclude":
            # Like a store, an excluded path is never opened or listed.
            self._report(source, archive, "excluded", rule["reason"], rule=rule["id"])
            return
        rule_id = rule["id"] if rule else None
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if self.share is None and before.st_uid != os.geteuid():
            raise probe.Rejected("source entry owner differs")
        destination = self.directory / str(len(self.paths))
        transform = rule if rule and rule["action"] == "transform" else None
        if transform and not stat.S_ISREG(before.st_mode):
            self._report(source, archive, "unsupported", "transform-target-not-file", rule=rule_id)
            return
        if stat.S_ISDIR(before.st_mode):
            fd = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
            try:
                if _metadata(os.fstat(fd)) != _metadata(before):
                    raise probe.Rejected("source directory replaced")
                # Check before registering, so a skipped directory leaves nothing behind.
                self._guard(fd)
                destination.mkdir(mode=0o700)
                if lost := metadata_losses(fd, before):
                    self.losses[archive] = lost
                self.paths[archive], self.metadata[archive] = destination, before
                self._report(source, archive, "included", "directory", rule=rule_id)
                self._directory(fd, source, archive, before)
            finally:
                os.close(fd)
        elif stat.S_ISREG(before.st_mode):
            if before.st_nlink != 1 or (self.share is None and not before.st_mode & 0o400):
                self._report(source, archive, "unsupported",
                             "multiply-linked-file" if before.st_nlink != 1 else "unreadable-owner-file",
                             rule=rule_id)
                return
            if not self._room(before.st_size):
                raise probe.Rejected("collection byte limit")
            if transform and before.st_size > migration_policy.MAX_TRANSFORM_INPUT:
                self._report(source, archive, "unsupported", "transform-too-large", rule=rule_id)
                return
            fd = os.open(name, FILE_FLAGS, dir_fd=parent)
            captured = bytearray() if transform else None
            with os.fdopen(fd, "rb") as source_file:
                self._guard(fd)
                if _metadata(os.fstat(fd)) != _metadata(before):
                    raise probe.Rejected("source file replaced before capture")
                lost = metadata_losses(fd, before)
                count = 0
                output_fd = None if transform else os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with contextlib.ExitStack() as stack:
                    output = stack.enter_context(os.fdopen(output_fd, "wb")) if output_fd is not None else None
                    while piece := source_file.read(min(probe.CHUNK, before.st_size - count + 1)):
                        count += len(piece)
                        if count > before.st_size:
                            raise probe.Rejected("source file grew during capture")
                        if output is None:
                            captured += piece
                        else:
                            output.write(piece)
                if count != before.st_size or _metadata(os.fstat(fd)) != _metadata(before):
                    raise probe.Rejected("source file changed during capture")
            outcome, reason = "included", "regular-file"
            if transform:
                result = self.policy.transform(transform, bytes(captured))
                if result.status not in ("applied", "not-applicable"):
                    # Withhold rather than export a file the policy cannot clean.
                    self._report(source, archive, "unsupported", f"transform-{result.status}", rule=rule_id)
                    return
                if not self._room(len(captured) + len(result.data)):
                    raise probe.Rejected("collection byte limit")
                if result.status == "applied":
                    outcome, reason = "transformed", transform["reason"]
                    original = self.directory / f"original-{len(self.originals)}"
                    original_fd = os.open(original, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(original_fd, "wb") as output:
                        output.write(captured)
                    self.total += len(captured)
                    self.footprint += probe.footprint(len(captured))
                    self.original_losses[archive] = lost
                    self.originals.append((source, archive, original, before.st_mtime_ns))
                output_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(output_fd, "wb") as output:
                    output.write(result.data)
                count = len(result.data)
            self.total += count
            self.footprint += probe.footprint(count)
            self.paths[archive], self.metadata[archive] = destination, before
            if lost:
                self.losses[archive] = lost
            self._report(source, archive, outcome, reason, rule=rule_id)
        elif stat.S_ISLNK(before.st_mode):
            target = os.readlink(name, dir_fd=parent)
            if not target or "\0" in target or len(target.encode("utf-8")) > 4096:
                raise probe.Rejected("source link target limit")
            mount = self.policy.mount(target)
            if (mount and self.share is None and mount["id"] in self.request["selected_mounts"]
                    and self._is_mount_root(target, mount)):
                if mount["id"] in self.materialized:
                    # Kept as inert link text, like any other link into a mount.
                    destination.symlink_to(target)
                    self.paths[archive], self.metadata[archive] = destination, before
                    self._report(source, archive, "inert-link", "mount-already-materialized", mount=mount["id"])
                else:
                    self._materialize(source, archive, mount)
                after = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if _metadata(after) != _metadata(before):
                    raise probe.Rejected("source entry changed during capture")
                return
            if self.share is not None and not target.startswith("/"):
                # The share was renamed to its link's path: a relative link
                # leaving it would point at home files instead of the Mac's.
                root = self.share["archive"]
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(archive), target))
                if resolved != root and not resolved.startswith(root + "/"):
                    raise _Skip("share-escape")
            if any(item["source"] != item["archive"] for item in self.request["selection"]):
                # V2 has no explicit inert-link flag. A renamed selection
                # could make an old target accidentally refer to new data.
                self._report(source, archive, "unsupported", "remapped-link")
                return
            destination.symlink_to(target)
            self.paths[archive], self.metadata[archive] = destination, before
            if mount:
                # Recorded as inert metadata; mounted contents need their own selection.
                self._report(source, archive, "inert-link", "mount-link", rule=rule_id, mount=mount["id"])
            else:
                self._report(source, archive, "included", "link-metadata", rule=rule_id)
        else:
            self._report(source, archive, "unsupported", "special-file", rule=rule_id)
            return
        after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if _metadata(after) != _metadata(before):
            raise probe.Rejected("source entry changed during capture")
        if self.share is None:
            self.history[source] = _metadata(before)

    @staticmethod
    def _is_mount_root(target, mount):
        if not target.startswith("/"):
            return False
        parts = [part for part in target.split("/") if part not in ("", ".")]
        return parts == [part for part in mount["path"].split("/") if part]

    def _materialize(self, source, archive, mount):
        """Copy a selected shared folder's contents in place of its home link."""
        fd = os.open(self.share_roots[mount["id"]], DIRECTORY_FLAGS)
        try:
            metadata = os.fstat(fd)
            self.share = {"id": mount["id"], "mount": _mount(fd), "archive": archive}
            try:
                self._guard(fd)
            except _Skip as skip:
                self._report(source, archive, "unsupported", skip.reason, mount=mount["id"])
                return
            destination = self.directory / str(len(self.paths))
            destination.mkdir(mode=0o700)
            self.paths[archive], self.metadata[archive] = destination, metadata
            if lost := metadata_losses(fd, metadata):
                self.losses[archive] = lost
            self._report(source, archive, "included", "mount-materialized", mount=mount["id"])
            self._directory(fd, source, archive, metadata)
            self.materialized.add(mount["id"])
        finally:
            self.share = None
            os.close(fd)

    def originals_root(self):
        return f"{ORIGINALS_ROOT}/{self.request['request_id']}" if self.originals else None

    def _add_originals(self):
        """Place untouched copies under the migration-owned originals root."""
        root = self.originals_root()
        if root is None:
            return
        withheld = {item["archive"] for item in self.items if item["outcome"] != "included"}
        for source, archive, original, mtime_ns in self.originals:
            path = f"{root}/{archive}"
            _path(path)
            parts = path.split("/")
            for depth in range(1, len(parts)):
                ancestor = "/".join(parts[:depth])
                if ancestor in withheld:
                    raise probe.Rejected("originals location is withheld by policy or selection")
                if ancestor in self.paths:
                    if not self.paths[ancestor].is_dir() or self.paths[ancestor].is_symlink():
                        raise probe.Rejected("originals location collides with a captured entry")
                    continue
                directory = self.directory / f"originals-directory-{len(self.paths)}"
                directory.mkdir(mode=0o700)
                self.paths[ancestor] = directory
                self.metadata[ancestor] = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_mtime_ns=mtime_ns)
                self._report(ancestor, ancestor, "included", "directory")
            if path in self.paths:
                raise probe.Rejected("originals location collides with a captured entry")
            self.paths[path] = original
            if self.original_losses.get(archive):
                # The untouched copy lacks the same attributes as its source.
                self.losses[path] = self.original_losses[archive]
            # Private copies, whatever the original's mode.
            self.metadata[path] = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_mtime_ns=mtime_ns)
            self._report(source, path, "included", "original-copy")

    def capture(self):
        self._guard(self.root_fd)
        root_metadata = os.fstat(self.root_fd)
        self.history[""] = _metadata(root_metadata)
        for selection in self.request["selection"]:
            source, archive = selection["source"], selection["archive"]
            excluded = self._store(source)
            rule = self._rule(source)
            if excluded:
                self._report(source, archive, "held-out", excluded[1], excluded[0])
            elif rule and rule["action"] == "exclude":
                # Checked before opening ancestors, which may be the excluded path.
                self._report(source, archive, "excluded", rule["reason"], rule=rule["id"])
            elif source:
                with self._parent(source) as parent:
                    self._walk(parent, source.split("/")[-1], source, archive)
            elif not archive:
                self._directory(self.root_fd, "", "", root_metadata)
            else:
                destination = self.directory / str(len(self.paths))
                destination.mkdir(mode=0o700)
                self.paths[archive], self.metadata[archive] = destination, root_metadata
                self._report("", archive, "included", "directory")
                self._directory(self.root_fd, "", archive, root_metadata)
        # Reopen through the pinned root without following aliases. Metadata
        # checks detect changes but cannot prove a single atomic cutover.
        for source, expected in self.history.items():
            if source:
                with self._parent(source) as parent:
                    actual = os.stat(source.split("/")[-1], dir_fd=parent, follow_symlinks=False)
            else:
                actual = os.fstat(self.root_fd)
            if _metadata(actual) != expected:
                raise probe.Rejected("source changed before snapshot completion")
        reopened = os.open(self.root, DIRECTORY_FLAGS)
        try:
            if _metadata(os.fstat(reopened)) != _metadata(root_metadata):
                raise probe.Rejected("source root changed before completion")
        finally:
            os.close(reopened)
        self._add_originals()
        self.manifest = probe.make_tree_manifest(self.paths)
        by_path = {entry["path"]: entry for entry in self.manifest["entries"]}
        for entry in self.manifest["entries"]:
            metadata = self.metadata[entry["path"]]
            entry["mtime_ns"] = metadata.st_mtime_ns
            if probe.entry_kind(entry) != "symlink":
                entry["mode"] = stat.S_IMODE(metadata.st_mode) & 0o777
        probe.validate_manifest(self.manifest)
        for item in self.items:
            entry = by_path.get(item["archive"])
            if item["reason"] == "link-metadata" and probe.link_target(entry, by_path) is None:
                item.update(outcome="inert-link", reason="target-unavailable")
        outcomes = probe.PROVENANCE_OUTCOMES
        self.report = {
            "schema": REPORT_SCHEMA, "request_id": self.request["request_id"],
            "request_sha256": hashlib.sha256(_json_bytes(self.request)).hexdigest(),
            "policy_sha256": hashlib.sha256(_json_bytes(self.policy.document)).hexdigest(),
            "supported_adapters": sorted(self.supported),
            "capabilities_sha256": hashlib.sha256(_json_bytes(sorted(self.supported))).hexdigest(),
            "policy_revision": self.policy.revision, "status": "complete",
            "entries": self.items, "counts": {outcome: sum(item["outcome"] == outcome for item in self.items)
                                               for outcome in outcomes},
        }
        # Bind what was withheld or changed into the authenticated manifest.
        self.manifest["provenance"] = {
            "policy_revision": self.policy.revision,
            "policy_sha256": self.report["policy_sha256"],
            "request_sha256": self.report["request_sha256"],
            "originals": self.originals_root(),
            # Only entries that made it into the bundle; skipped ones left with their losses.
            "metadata": [{"archive": archive, "lost": self.losses[archive]}
                         for archive in sorted(self.losses) if archive in self.paths],
            "collection": {"counts": dict(self.report["counts"]),
                           "exceptions": [dict(item) for item in self.items if item["outcome"] != "included"]},
        }
        probe.validate_manifest(self.manifest)
        if len(_json_bytes(self.manifest)) > probe.MAX_MANIFEST:
            raise probe.Rejected("collection manifest size")


@contextlib.contextmanager
def collect_fixture(root, request, policy, *, supported_adapters=(), snapshot_parent, share_roots=None):
    """Capture only an explicitly supplied, caller-created synthetic source.

    `share_roots` maps a policy mount id to the directory holding its contents
    (the policy's mount path by default); only mounts the request selects are read.
    """
    loaded = _contracts(request, policy, supported_adapters)
    roots = {mount["id"]: mount["path"] for mount in loaded.mounts}
    roots.update(share_roots or {})
    if set(roots) != {mount["id"] for mount in loaded.mounts} or any(
            not isinstance(path, (str, os.PathLike)) or not os.path.isabs(path) for path in roots.values()):
        raise probe.Rejected("shared folder roots")
    # Resolve trusted mount paths once; the walk then opens them without following links.
    roots = {key: os.path.realpath(path) for key, path in roots.items()}
    home, scratch = os.path.realpath(root), os.path.realpath(snapshot_parent)
    for key in request["selected_mounts"]:
        share = roots[key]
        for other in (home, scratch):
            if os.path.commonpath([share, other]) in (share, other):
                raise probe.Rejected("shared folder overlaps the home or snapshot location")
    request = json.loads(_json_bytes(request))
    if os.geteuid() == 0:
        raise probe.Rejected("fixture collection requires an unprivileged owner")
    root, parent = Path(root).absolute(), Path(snapshot_parent).absolute()
    root_fd = os.open(root, DIRECTORY_FLAGS)
    try:
        metadata = os.fstat(root_fd)
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise probe.Rejected("unsafe fixture root")
        parent_fd = os.open(parent, DIRECTORY_FLAGS)
        try:
            parent_metadata = os.fstat(parent_fd)
            if parent_metadata.st_uid != os.geteuid() or stat.S_IMODE(parent_metadata.st_mode) != 0o700:
                raise probe.Rejected("snapshot parent must be private and owned")
            if parent.resolve().is_relative_to(root.resolve()):
                raise probe.Rejected("snapshot parent must be outside source")
            with tempfile.TemporaryDirectory(prefix="migration-collected-", dir=parent) as temporary:
                directory = Path(temporary)
                if directory.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(directory.resolve()):
                    raise probe.Rejected("snapshot and source must be separate")
                status = os.statvfs(directory)
                budget = max(0, status.f_bavail * status.f_frsize - SNAPSHOT_MARGIN)
                snapshot = _Snapshot(root_fd, root, directory, request, loaded, supported_adapters, roots, budget)
                snapshot.capture()
                yield snapshot
        finally:
            os.close(parent_fd)
    finally:
        os.close(root_fd)
