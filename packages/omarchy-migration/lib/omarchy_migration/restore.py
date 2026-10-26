"""Synthetic, additive restoration experiment; never a real-home CLI.

Authenticate once into private scratch, then plan/apply against a caller-owned
disposable destination. Explicit regular-file replacement retains private backups.
"""

import contextlib
import ctypes
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

from . import probe


# /6-/8: an append-only log of a snapshot line followed by changed entries;
# an "applied" tree directory means this job created it and may finalize it.
JOURNAL_SCHEMA = "omarchy-migration-restore-probe/6"
TREE_JOURNAL_SCHEMA = "omarchy-migration-restore-probe/7"
REPLACEMENT_JOURNAL_SCHEMA = "omarchy-migration-restore-probe/8"
JOURNAL_NAME = "journal.json"
# Base size plus room per manifest entry for its snapshot record and updates.
JOURNAL_BASE_LIMIT = 4 * 1024 * 1024
JOURNAL_ENTRY_LIMIT = 3 * 1024
# Free space left untouched when authenticating a bundle into scratch.
SCRATCH_MARGIN = 64 * 1024 * 1024


def _free_bytes(path):
    status = os.statvfs(path)
    return status.f_bavail * status.f_frsize
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _replay(raw):
    """Rebuild a journal document from its snapshot line and change records.

    A final line without its newline is a torn append from an interrupted
    save; it was never durable, so the action it recorded never started.
    Returns (document, number of lines kept, whether a torn tail was dropped).
    """
    lines = raw.split(b"\n")
    torn = False
    if lines and lines[-1] == b"":
        lines.pop()
    elif len(lines) > 1:
        lines.pop()
        torn = True
    if not lines:
        raise probe.Rejected("invalid restore journal")
    try:
        document = json.loads(lines[0], object_pairs_hook=probe.unique_json_pairs)
        if not isinstance(document, dict) or not isinstance(document.get("entries"), dict):
            raise ValueError()
        for line in lines[1:]:
            record = json.loads(line, object_pairs_hook=probe.unique_json_pairs)
            if (not isinstance(record, dict) or set(record) != {"object", "entry"}
                    or not isinstance(record["object"], str)):
                raise ValueError()
            document["entries"][record["object"]] = record["entry"]
    except (ValueError, UnicodeError) as error:
        raise probe.Rejected("invalid restore journal") from error
    return document, len(lines), torn


def read_journal(path):
    """The current journal document at a path, for inspection and tests."""
    return _replay(Path(path).read_bytes())[0]


@dataclass(frozen=True)
class Action:
    path: str
    status: str
    reason: str
    backup: str | None = None


class _VerifiedBundle:
    def __init__(self, manifest, directory, ciphertext_sha256):
        self._manifest = manifest
        self._directory = directory
        self._active = True
        self._digest = hashlib.sha256(_json_bytes(manifest)).hexdigest()
        # Digest of the exact ciphertext bytes that age authenticated.
        self.ciphertext_sha256 = ciphertext_sha256

    @property
    def export_id(self):
        return self._manifest["export_id"]

    def read_file(self, path, limit):
        """The bytes of the selected regular file at `path`, or None when it is absent or larger than `limit`."""
        self._check()
        entry = next((entry for entry in self._manifest["entries"] if entry["path"] == path), None)
        if entry is None or probe.entry_kind(entry) != "file":
            return None
        try:
            fd = os.open(Path(self._directory) / entry["object"].split("/")[1], os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return None
        with os.fdopen(fd, "rb") as source:
            data = source.read(limit + 1)
        return data if len(data) <= limit else None

    def link_target(self, path):
        """The stored target of the selected link at `path`, or None."""
        self._check()
        entry = next((entry for entry in self._manifest["entries"] if entry["path"] == path), None)
        return entry["target"] if entry is not None and probe.entry_kind(entry) == "symlink" else None

    def _check(self):
        if not self._active:
            raise probe.Rejected("verified bundle has been closed")


@contextlib.contextmanager
def verified_bundle(age, secret, ciphertext, scratch=None):
    """Yield only after complete authentication; remove plaintext on every exit."""
    with tempfile.TemporaryDirectory(prefix="migration-verified-", dir=scratch) as directory:
        root = Path(directory)

        @contextlib.contextmanager
        def objects(entry):
            # Only the validated numbered object ID is used for scratch names.
            fd = os.open(root / entry["object"].split("/")[1],
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                yield output

        digest = hashlib.sha256()
        manifest = probe._decode(
            age, secret, ciphertext, probe.MAX_CIPHERTEXT,
            objects=objects, digest=digest,
            # Refuse before decrypting objects that cannot fit in scratch.
            budget=max(0, _free_bytes(root) - SCRATCH_MARGIN),
        )
        bundle = _VerifiedBundle(manifest, root, digest.hexdigest())
        try:
            yield bundle
        finally:
            bundle._active = False


def _directory(path):
    fd = os.open(path, DIRECTORY_FLAGS)
    try:
        metadata = os.fstat(fd)
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise probe.Rejected("directory must be owned by caller and not writable by others")
        return fd
    except BaseException:
        os.close(fd)
        raise


RENAME_EXCHANGE = 2


def _exchange(old_dir, old_name, new_dir, new_name):
    """Swap two names atomically with renameat2; False where the system or filesystem can't."""
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (OSError, AttributeError):
        return False
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if renameat2(old_dir, os.fsencode(old_name), new_dir, os.fsencode(new_name), RENAME_EXCHANGE) == 0:
        return True
    error = ctypes.get_errno()
    if error in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
        return False
    raise OSError(error, os.strerror(error))


def _fingerprint_at(directory, name):
    """The regular file at NAME; None when it is gone, "unknown" for anything else."""
    try:
        fd = os.open(name, FILE_FLAGS, dir_fd=directory)
    except FileNotFoundError:
        return None
    except OSError:
        return "unknown"
    try:
        return _fingerprint(fd)
    except (OSError, probe.Rejected):
        return "unknown"
    finally:
        os.close(fd)


def _identity(fd):
    metadata = os.fstat(fd)
    return {"device": metadata.st_dev, "inode": metadata.st_ino, "uid": metadata.st_uid}


def _metadata(metadata):
    return {
        "device": metadata.st_dev, "inode": metadata.st_ino,
        "uid": metadata.st_uid, "gid": metadata.st_gid,
        "bytes": metadata.st_size, "mode": stat.S_IMODE(metadata.st_mode),
        "mtime_ns": metadata.st_mtime_ns,
    }


def _fingerprint(fd):
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_size > probe.MAX_TOTAL:
        raise probe.Rejected("unsupported destination file")
    checksum = hashlib.sha256()
    count = 0
    while piece := os.read(fd, probe.CHUNK):
        count += len(piece)
        if count > probe.MAX_TOTAL:
            raise probe.Rejected("destination file exceeds probe limit")
        checksum.update(piece)
    after = os.fstat(fd)
    if _metadata(before) != _metadata(after) or before.st_ctime_ns != after.st_ctime_ns:
        raise probe.Rejected("destination changed while reading")
    return {**_metadata(after), "sha256": checksum.hexdigest()}


def _matches(entry, observed):
    if observed is None or "blocked" in observed:
        return False
    if probe.entry_kind(entry) == "directory":
        # Structure only: source mode and mtime are applied at finalization.
        return set(observed) == {"device", "inode", "uid", "gid", "mode"}
    if probe.entry_kind(entry) == "symlink":
        return all(observed.get(key) == entry[key] for key in ("target", "mtime_ns"))
    return observed is not None and all(
        observed.get(key) == entry[key] for key in ("bytes", "mode", "mtime_ns", "sha256")
    )


def _final_mode(entry):
    # Never group- or other-writable (the restorer's own checks refuse that),
    # and always owner-traversable so retries can still observe the contents.
    return (entry["mode"] | 0o500) & ~0o022


def _created_directory_matches(entry, saved, observed):
    """A directory this job created, before or after its metadata finalization."""
    if not isinstance(observed, dict) or "blocked" in observed or set(observed) != set(saved):
        return False
    return (all(observed[key] == saved[key] for key in saved if key != "mode")
            and observed["mode"] in (saved["mode"], _final_mode(entry)))


def _ancestors(path):
    parts = path.split("/")
    return ["/".join(parts[:index]) for index in range(1, len(parts))]


def _directory_fingerprint(fd):
    metadata = os.fstat(fd)
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022):
        raise probe.Rejected("unsafe destination directory")
    return {**_identity(fd), "gid": metadata.st_gid, "mode": stat.S_IMODE(metadata.st_mode)}


def _symlink_fingerprint(parent, name):
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISLNK(before.st_mode) or before.st_uid != os.geteuid() or before.st_size > 4096:
        raise probe.Rejected("unsafe destination symlink")
    target = os.readlink(name, dir_fd=parent)
    after = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if _metadata(before) != _metadata(after) or before.st_ctime_ns != after.st_ctime_ns:
        raise probe.Rejected("symlink changed while reading")
    return {
        "device": after.st_dev, "inode": after.st_ino, "uid": after.st_uid,
        "gid": after.st_gid, "mtime_ns": after.st_mtime_ns, "target": target,
    }


class Restorer:
    """One locked job, one authenticated bundle, one pinned disposable target.

    The caller creates an empty 0700 job directory outside the destination on
    the same filesystem. plan() writes nothing in the target; apply(plan)
    accepts only this session's latest plan. No ownership or paths come from
    the archive except validated relative file names.
    """

    def __init__(self, bundle, target, job, *, replace=(), replace_matching=None, generated=()):
        bundle._check()
        if os.geteuid() == 0:
            raise probe.Rejected("run this experiment as an unprivileged owner")
        self.bundle = bundle
        self.target = Path(target).absolute()
        self.job = Path(job).absolute()
        self._stack = contextlib.ExitStack()
        self._plan = None
        self._closed = False
        self._created_parents = {}
        self._tree = bundle._manifest["schema"] == probe.TREE_SCHEMA
        self._entries = {entry["path"]: entry for entry in bundle._manifest["entries"]}
        if (not isinstance(replace, (tuple, list, set, frozenset))
                or any(type(path) is not str for path in replace)
                or len(set(replace)) != len(replace)
                or any(path not in self._entries or probe.entry_kind(self._entries[path]) != "file"
                       for path in replace)):
            raise probe.Rejected("replacement approval must name selected regular files")
        if replace_matching is not None:
            # Each path may be replaced only while its destination has exactly this digest.
            if (replace or not isinstance(replace_matching, dict)
                    or any(type(digest) is not str or len(digest) != 64
                           or any(char not in "0123456789abcdef" for char in digest)
                           for digest in replace_matching.values())):
                raise probe.Rejected("conditional replacement must map selected regular files to digests")
            replace = tuple(replace_matching)
            if any(type(path) is not str or path not in self._entries
                   or probe.entry_kind(self._entries[path]) != "file" for path in replace):
                raise probe.Rejected("replacement approval must name selected regular files")
        self._replace = frozenset(replace)
        # Folders the destination system regenerates itself, which are never written.
        self._generated = tuple(generated)
        self._originals = dict(sorted(replace_matching.items())) if replace_matching else None
        try:
            # Caller paths are trusted, but the final component must not be a
            # symlink. Resolve parents only for containment checks.
            self._target_fd = _directory(self.target)
            self._stack.callback(os.close, self._target_fd)
            self._job_fd = _directory(self.job)
            self._stack.callback(os.close, self._job_fd)
            resolved_target, resolved_job = self.target.resolve(), self.job.resolve()
            if resolved_target.is_relative_to(resolved_job) or resolved_job.is_relative_to(resolved_target):
                raise probe.Rejected("job and target must be separate trees")
            if stat.S_IMODE(os.fstat(self._job_fd).st_mode) != 0o700:
                raise probe.Rejected("job directory must have mode 0700")
            if os.fstat(self._target_fd).st_dev != os.fstat(self._job_fd).st_dev:
                raise probe.Rejected("job and target must share a filesystem")
            try:
                fcntl.flock(self._job_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise probe.Rejected("restore job is already active") from error
            self._binding = {
                "schema": (REPLACEMENT_JOURNAL_SCHEMA if self._replace else
                           TREE_JOURNAL_SCHEMA if self._tree else JOURNAL_SCHEMA),
                "manifest_sha256": bundle._digest,
                "export_id": bundle._manifest["export_id"],
                "target": _identity(self._target_fd),
                "job": _identity(self._job_fd),
            }
            if self._replace:
                self._binding["replacement_paths"] = sorted(self._replace)
            if self._originals:
                self._binding["replacement_originals"] = self._originals
            self._journal = self._load()
            self._written = {key: _json_bytes(value) for key, value in self._journal["entries"].items()}
            # A torn tail must be removed before appending again; an overgrown
            # log is compacted.
            if self._journal_torn or self._journal_lines > 2 * len(self._written) + 16:
                self._snapshot()
        except BaseException:
            self._stack.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._closed = True
        self._stack.close()

    def _check(self):
        self.bundle._check()
        if self._closed:
            raise probe.Rejected("restore job is closed")
        for path, expected in ((self.target, self._binding["target"]), (self.job, self._binding["job"])):
            fd = _directory(path)
            try:
                if _identity(fd) != expected:
                    raise probe.Rejected("restore directory identity changed")
            finally:
                os.close(fd)
        if stat.S_IMODE(os.fstat(self._job_fd).st_mode) != 0o700:
            raise probe.Rejected("job directory must remain private")

    def _journal_limit(self):
        return JOURNAL_BASE_LIMIT + JOURNAL_ENTRY_LIMIT * len(self.bundle._manifest["entries"])

    def _load(self):
        self._journal_lines, self._journal_torn = 0, False
        try:
            fd = os.open(JOURNAL_NAME, FILE_FLAGS, dir_fd=self._job_fd)
        except FileNotFoundError:
            if os.listdir(self._job_fd):
                raise probe.Rejected("unrecognized nonempty restore job")
            return {**self._binding, "entries": {}}
        limit = self._journal_limit()
        with os.fdopen(fd, "rb") as source:
            metadata = os.fstat(fd)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1):
                raise probe.Rejected("unsafe restore journal")
            raw = source.read(limit + 1)
        if len(raw) > limit:
            raise probe.Rejected("restore journal size")
        journal, self._journal_lines, self._journal_torn = _replay(raw)
        if (not isinstance(journal, dict) or set(journal) != {*self._binding, "entries"}
                or any(journal[key] != value for key, value in self._binding.items())):
            raise probe.Rejected("restore journal binding differs")
        entries = journal["entries"]
        allowed = {entry["object"]: entry for entry in self.bundle._manifest["entries"]}
        if not isinstance(entries, dict) or not set(entries) <= allowed.keys():
            raise probe.Rejected("restore journal entries")
        for identity, entry in entries.items():
            kind = probe.entry_kind(allowed[identity])
            states = ("pending", "applied", "retained") if self._tree or self._replace else ("pending", "applied")
            keys = {"state", "file", "temporary"}
            if isinstance(entry, dict) and "backup" in entry:
                keys.add("backup")
                if (kind != "file" or allowed[identity]["path"] not in self._replace
                        or entry.get("state") == "retained"):
                    raise probe.Rejected("unapproved replacement journal entry")
                if "exchanging" in entry:
                    keys.add("exchanging")
                    if not isinstance(entry["exchanging"], bool):
                        raise probe.Rejected("restore journal entry")
            if (not isinstance(entry, dict) or set(entry) != keys
                    or entry["state"] not in states):
                raise probe.Rejected("restore journal entry")
            temporary = entry["temporary"]
            if kind == "directory" or entry["state"] == "retained":
                if temporary is not None:
                    raise probe.Rejected("directory journal temporary identity")
                if kind == "directory" and entry["state"] == "pending" and entry["file"] is None:
                    continue  # Intent before mkdir; uncertainty is never adopted.
            else:
                try:
                    if not isinstance(temporary, str) or str(uuid.UUID(temporary)) != temporary:
                        raise ValueError()
                except ValueError as error:
                    raise probe.Rejected("restore journal temporary identity") from error
            fingerprint_keys = {"device", "inode", "uid", "gid"}
            if kind == "file":
                fingerprint_keys.update(("bytes", "mode", "mtime_ns", "sha256"))
            elif kind == "symlink":
                fingerprint_keys.update(("mtime_ns", "target"))
            else:
                fingerprint_keys.add("mode")
            expected = entry["file"]
            if not isinstance(expected, dict) or set(expected) != fingerprint_keys:
                raise probe.Rejected("restore journal fingerprint")
            if any(type(expected[key]) is not int or expected[key] < 0
                   for key in fingerprint_keys - {"sha256", "target"}):
                raise probe.Rejected("restore journal metadata")
            if kind == "file" and (not isinstance(expected["sha256"], str) or len(expected["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in expected["sha256"])):
                raise probe.Rejected("restore journal digest")
            if kind == "symlink" and expected["target"] != allowed[identity]["target"]:
                raise probe.Rejected("restore journal symlink target")
            if "backup" in entry:
                backup = entry["backup"]
                if not isinstance(backup, dict) or set(backup) != {"name", "file", "original"}:
                    raise probe.Rejected("restore backup record")
                try:
                    name = backup["name"]
                    if (not isinstance(name, str) or str(uuid.UUID(name)) != name
                            or name == temporary):
                        raise ValueError()
                except ValueError as error:
                    raise probe.Rejected("restore backup identity") from error
                for fingerprint in (backup["file"], backup["original"]):
                    if (not isinstance(fingerprint, dict) or set(fingerprint) != fingerprint_keys
                            or any(type(fingerprint[key]) is not int or fingerprint[key] < 0
                                   for key in fingerprint_keys - {"sha256"})
                            or not isinstance(fingerprint["sha256"], str)
                            or len(fingerprint["sha256"]) != 64
                            or any(c not in "0123456789abcdef" for c in fingerprint["sha256"])):
                        raise probe.Rejected("restore backup fingerprint")
                copied, original = backup["file"], backup["original"]
                if (copied["mode"] != 0o600 or copied["uid"] != os.geteuid()
                        or original["uid"] != os.geteuid() or not original["mode"] & 0o400
                        or any(copied[key] != original[key] for key in ("bytes", "sha256", "mtime_ns"))
                        or copied["device"] != expected["device"]
                        or len({(fp["device"], fp["inode"]) for fp in (copied, original, expected)}) != 3):
                    raise probe.Rejected("restore backup binding")
        return journal

    def _backup_valid(self, saved):
        if "backup" not in saved:
            return True
        try:
            fd = os.open(saved["backup"]["name"], FILE_FLAGS, dir_fd=self._job_fd)
            try:
                if os.fstat(fd).st_nlink != 1:
                    return False
                return _fingerprint(fd) == saved["backup"]["file"]
            finally:
                os.close(fd)
        except (OSError, probe.Rejected):
            return False

    def _save(self, *keys):
        """Durably record changed entries: the named ones, or all when none are named.

        Naming the changed entries keeps each save proportional to the change,
        not to the job. Appends one line per change; rewrites a compact
        snapshot when there is no journal yet or the log has grown well past
        the live state.
        """
        entries = self._journal["entries"]
        changed = []
        for key in keys or tuple(entries):
            data = _json_bytes(entries[key])
            if self._written.get(key) != data:
                changed.append((key, data))
        exists = self._journal_lines > 0
        if not exists or self._journal_lines + len(changed) > 2 * len(entries) + 16:
            self._snapshot()
            return
        if not changed:
            return
        fd = os.open(JOURNAL_NAME, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=self._job_fd)
        with os.fdopen(fd, "ab") as output:
            metadata = os.fstat(output.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise probe.Rejected("unsafe restore journal")
            output.write(b"".join(b'{"entry":' + data + b',"object":' + _json_bytes(key) + b"}\n"
                                  for key, data in changed))
            output.flush()
            os.fsync(output.fileno())
        self._written.update(changed)
        self._journal_lines += len(changed)

    def _snapshot(self):
        temporary = f".journal-{uuid.uuid4()}"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self._job_fd)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(_json_bytes(self._journal) + b"\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, JOURNAL_NAME, src_dir_fd=self._job_fd, dst_dir_fd=self._job_fd)
            os.fsync(self._job_fd)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=self._job_fd)
        self._written = {key: _json_bytes(value) for key, value in self._journal["entries"].items()}
        self._journal_lines = 1

    @contextlib.contextmanager
    def _parent(self, path, *, create=False, identities=None):
        fd = os.dup(self._target_fd)
        components = []
        try:
            for part in path.split("/")[:-1]:
                components.append(part)
                created = False
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                    else:
                        os.fsync(fd)
                        created = True
                try:
                    child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    yield None
                    return
                os.close(fd)
                fd = child
                metadata = os.fstat(fd)
                if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
                    raise probe.Rejected("unsafe destination parent")
                prefix = "/".join(components)
                identity = _identity(fd)
                if identities is not None:
                    identities.append((prefix, identity))
                if created:
                    self._created_parents[prefix] = identity
            yield fd
        finally:
            os.close(fd)

    def _observe(self, path, parents, kind="file"):
        try:
            with self._parent(path, identities=parents) as parent:
                if parent is None:
                    return None
                try:
                    name = path.split("/")[-1]
                    if kind == "symlink":
                        return _symlink_fingerprint(parent, name)
                    fd = os.open(name, DIRECTORY_FLAGS if kind == "directory" else FILE_FLAGS, dir_fd=parent)
                except FileNotFoundError:
                    return None
                try:
                    if kind == "directory":
                        return _directory_fingerprint(fd)
                    metadata = os.fstat(fd)
                    if metadata.st_uid != os.geteuid() or not metadata.st_mode & 0o400:
                        raise probe.Rejected("destination is not a readable owner file")
                    return _fingerprint(fd)
                finally:
                    os.close(fd)
        except (OSError, probe.Rejected):
            return {"blocked": True}

    def _action(self, entry, status, reason):
        saved = self._journal["entries"].get(entry["object"], {})
        return Action(entry["path"], status, reason, saved.get("backup", {}).get("name"))

    def destination_bytes(self):
        """The size of each existing destination file the last plan observed."""
        return {entry["path"]: observed["bytes"]
                for entry, (observed, _) in zip(self.bundle._manifest["entries"], self._observations)
                if isinstance(observed, dict) and isinstance(observed.get("bytes"), int)}

    def plan(self):
        self._check()
        actions, observations = [], []
        for entry in self.bundle._manifest["entries"]:
            kind = probe.entry_kind(entry)
            parents = []
            observed = self._observe(entry["path"], parents, kind)
            saved = self._journal["entries"].get(entry["object"])
            if any(entry["path"] == folder or entry["path"].startswith(folder + "/") for folder in self._generated):
                status, reason = "inert", "regenerated by the destination system"
            elif kind == "symlink" and probe.link_target(entry, self._entries) is None:
                status, reason = "inert", "unsupported link retained only in encrypted manifest"
            elif kind == "file" and not entry["mode"] & 0o400:
                status, reason = "conflict", "unreadable source mode is unsupported by this probe"
            elif saved:
                created = kind == "directory" and saved["state"] == "applied"
                if created and _created_directory_matches(entry, saved["file"], observed):
                    # A crash during finalization leaves either mode; both are this job's.
                    status, reason = "restored", "prior publication matches journal"
                elif observed == saved["file"] and _matches(entry, observed) and self._backup_valid(saved):
                    status, reason = "restored", "prior publication matches journal"
                else:
                    # Missing after intent is ambiguous: a user may have
                    # deleted the file after link() but before completion.
                    status, reason = "conflict", "destination changed or publication outcome is uncertain"
            elif observed is None:
                status, reason = "create", "new file"
            elif _matches(entry, observed):
                status, reason = "present", "matching file already exists"
            elif (entry["path"] in self._replace and observed is not None and "blocked" not in observed
                  and self._originals is not None and observed["sha256"] != self._originals[entry["path"]]):
                status, reason = "conflict", "existing file differs from its default"
            elif entry["path"] in self._replace and observed is not None and "blocked" not in observed:
                status, reason = "replace", "approved regular file; private original backup required"
            else:
                status, reason = "conflict", "existing file or unsafe path preserved"
            actions.append(self._action(entry, status, reason))
            observations.append((observed, parents))
        if self._tree:
            by_path = {action.path: action for action in actions}
            for index in self._order():
                entry, action = self.bundle._manifest["entries"][index], actions[index]
                dependencies = _ancestors(entry["path"])
                if probe.entry_kind(entry) == "symlink" and action.status != "inert":
                    target, traversed = probe.link_target(entry, self._entries)
                    dependencies.extend((target, *traversed))
                if action.status != "inert" and any(by_path[path].status == "conflict" for path in dependencies):
                    action = self._action(entry, "conflict", "directory or link dependency is unavailable")
                    actions[index] = by_path[action.path] = action
        self._plan = tuple(actions)
        self._observations = observations
        return self._plan

    def _backup(self, entry, observed):
        """Copy instead of hardlinking, so other original links cannot alter it."""
        name = str(uuid.uuid4())
        output_fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self._job_fd)
        try:
            with os.fdopen(output_fd, "w+b") as output:
                with self._parent(entry["path"]) as parent:
                    if parent is None:
                        raise probe.Rejected("replacement parent disappeared")
                    source_fd = os.open(entry["path"].split("/")[-1], FILE_FLAGS, dir_fd=parent)
                    with os.fdopen(source_fd, "rb") as source:
                        before = os.fstat(source_fd)
                        if not stat.S_ISREG(before.st_mode) or _metadata(before) != {
                            key: observed[key] for key in _metadata(before)
                        }:
                            raise probe.Rejected("original changed before backup")
                        checksum, count = hashlib.sha256(), 0
                        while piece := source.read(probe.CHUNK):
                            count += len(piece)
                            if count > observed["bytes"]:
                                raise probe.Rejected("original grew during backup")
                            checksum.update(piece)
                            output.write(piece)
                        after = os.fstat(source_fd)
                        if (count != observed["bytes"] or checksum.hexdigest() != observed["sha256"]
                                or _metadata(before) != _metadata(after)
                                or before.st_ctime_ns != after.st_ctime_ns):
                            raise probe.Rejected("original changed during backup")
                output.flush()
                os.fchmod(output_fd, 0o600)
                os.utime(output_fd, ns=(observed["mtime_ns"], observed["mtime_ns"]))
                os.fsync(output_fd)
                output.seek(0)
                fingerprint = _fingerprint(output_fd)
            os.fsync(self._job_fd)
            return {"name": name, "file": fingerprint, "original": observed}
        except BaseException:
            os.unlink(name, dir_fd=self._job_fd)
            raise

    def _prepare(self, entry):
        temporary = str(uuid.uuid4())
        if probe.entry_kind(entry) == "symlink":
            os.symlink(entry["target"], temporary, dir_fd=self._job_fd)
            try:
                os.utime(temporary, ns=(entry["mtime_ns"], entry["mtime_ns"]),
                         dir_fd=self._job_fd, follow_symlinks=False)
                fingerprint = _symlink_fingerprint(self._job_fd, temporary)
                os.fsync(self._job_fd)
                return {"state": "pending", "file": fingerprint, "temporary": temporary}
            except BaseException:
                os.unlink(temporary, dir_fd=self._job_fd)
                raise
        fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self._job_fd)
        try:
            with os.fdopen(fd, "w+b") as output:
                path = self.bundle._directory / entry["object"].split("/")[1]
                source_fd = os.open(path, FILE_FLAGS)
                with os.fdopen(source_fd, "rb") as source:
                    if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                        raise probe.Rejected("verified scratch is not a regular file")
                    remaining = entry["bytes"]
                    while remaining:
                        piece = source.read(min(remaining, probe.CHUNK))
                        if not piece:
                            raise probe.Rejected("verified scratch is truncated")
                        output.write(piece)
                        remaining -= len(piece)
                    if source.read(1):
                        raise probe.Rejected("verified scratch grew")
                output.flush()
                os.fchmod(fd, entry["mode"])
                os.utime(fd, ns=(entry["mtime_ns"], entry["mtime_ns"]))
                os.fsync(fd)
                output.seek(0)
                fingerprint = _fingerprint(fd)
                if not _matches(entry, fingerprint):
                    raise probe.Rejected("verified scratch digest differs")
            os.fsync(self._job_fd)
            return {"state": "pending", "file": fingerprint, "temporary": temporary}
        except BaseException:
            os.unlink(temporary, dir_fd=self._job_fd)
            raise

    def _swap(self, entry, saved, parent, name):
        """Exchange the replacement into place, journaled first, so a retry
        settles a swap whose check never ran."""
        saved["exchanging"] = True
        self._save(entry["object"])
        if _exchange(self._job_fd, saved["temporary"], parent, name):
            return True
        saved["exchanging"] = False
        self._save(entry["object"])
        return False

    def _settle_exchange(self, entry, saved, parent=None):
        """The swap is atomic, so the file it moved out under the temporary
        name is the one the destination held. Unless it is still the planned
        original, an application wrote it since: it goes back, and the
        replacement leaves. True when the replacement stays."""
        displaced = _fingerprint_at(self._job_fd, saved["temporary"])
        if displaced is not None and displaced != saved["backup"]["original"]:
            with contextlib.ExitStack() as stack:
                if parent is None:
                    parent = stack.enter_context(self._parent(entry["path"]))
                    if parent is None:
                        raise probe.Rejected("destination parent disappeared")
                _exchange(self._job_fd, saved["temporary"], parent, entry["path"].split("/")[-1])
                os.fsync(parent)
            saved["exchanging"] = False
            self._save(entry["object"])
            return False
        if displaced is not None:
            # An application holding the original open may still write to
            # it, so it stays in the private job beside the backup copy
            # rather than being unlinked.
            os.rename(saved["temporary"], saved["backup"]["name"] + ".displaced",
                      src_dir_fd=self._job_fd, dst_dir_fd=self._job_fd)
            os.fsync(self._job_fd)
        saved["exchanging"] = False
        return True

    def _cleanup(self, saved):
        # A pending object may be hardlinked to a now user-edited destination.
        # Never mutate its bytes or permissions, and only unlink our own inode.
        if saved["temporary"] is None:
            return
        try:
            metadata = os.stat(saved["temporary"], dir_fd=self._job_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        expected = saved["file"]
        if (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)) and (
            metadata.st_dev, metadata.st_ino
        ) == (expected["device"], expected["inode"]):
            os.unlink(saved["temporary"], dir_fd=self._job_fd)
            os.fsync(self._job_fd)

    def _order(self):
        entries = self.bundle._manifest["entries"]
        priority = {"directory": 0, "file": 1, "symlink": 2}
        return sorted(range(len(entries)), key=lambda index: (
            priority[probe.entry_kind(entries[index])],
            entries[index]["path"].count("/") if probe.entry_kind(entries[index]) == "directory" else 0,
            index,
        ))

    def _restore_directory(self, entry, action, observed):
        identity = entry["object"]
        previous = self._journal["entries"].get(identity)
        # Only directories this job created are "applied" and later finalized;
        # pre-existing destination directories are retained untouched.
        created = action.status == "create" or (previous is not None and previous["state"] == "applied")
        with self._parent(entry["path"]) as parent:
            if parent is None:
                raise probe.Rejected("directory parent is unavailable")
            name = entry["path"].split("/")[-1]
            if action.status == "create":
                # mkdir cannot return its inode atomically with creation. A
                # crash before the second journal write leaves an uncertain
                # directory, which retry must not adopt or recreate.
                self._journal["entries"][identity] = {"state": "pending", "file": None, "temporary": None}
                self._save(identity)
                try:
                    os.mkdir(name, 0o700, dir_fd=parent)
                except FileExistsError:
                    return Action(entry["path"], "conflict", "directory appeared during creation"), None
            fd = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
            try:
                if action.status == "create":
                    os.fchmod(fd, 0o700)
                fingerprint = _directory_fingerprint(fd)
                if action.status != "create" and fingerprint != observed:
                    return Action(entry["path"], "conflict", "directory changed during application"), None
                os.fsync(fd)
                if action.status == "create":
                    self._created_parents[entry["path"]] = _identity(fd)
            finally:
                os.close(fd)
            os.fsync(parent)
        self._journal["entries"][identity] = {"state": "applied" if created else "retained",
                                              "file": fingerprint, "temporary": None}
        self._save(identity)  # Establish directory identity before touching children.
        return Action(entry["path"], "directory", "structure retained; source directory metadata deferred"), fingerprint

    def _finalize_directories(self, report):
        """Apply source mode and mtime to directories this job created.

        Runs after every publication, deepest first, so creating children
        cannot disturb a parent's mtime and a read-only source mode cannot
        block its own contents. A directory with an unfinished descendant
        stays private and is finalized by a later retry.
        """
        entries = self.bundle._manifest["entries"]
        finished = {"restored", "replaced", "present", "directory", "inert"}
        directories = [index for index, entry in enumerate(entries) if probe.entry_kind(entry) == "directory"]
        blocked = set()
        for position, other in enumerate(entries):
            if report[position] is None or report[position].status not in finished:
                blocked.update(_ancestors(other["path"]))
        for index in sorted(directories, key=lambda index: -entries[index]["path"].count("/")):
            entry, result = entries[index], report[index]
            saved = self._journal["entries"].get(entry["object"])
            if result is None or result.status != "directory" or saved is None or saved["state"] != "applied":
                continue
            if entry["path"] in blocked:
                continue
            self._check()
            fingerprint = self._finalize_directory(entry, saved)
            if fingerprint is None:
                report[index] = self._action(entry, "conflict", "directory changed during application")
                # Deepest first: its ancestors come later and must stay private.
                blocked.update(_ancestors(entry["path"]))
                continue
            saved["file"] = fingerprint
            self._save(entry["object"])
            report[index] = Action(entry["path"], "directory", "directory metadata restored")

    def _finalize_directory(self, entry, saved):
        """Set mode and mtime through a pinned descriptor; None if it changed.

        Failing to reach the directory (a swapped, unsafe or vanished ancestor
        or directory) is a conflict. Failing to change it, or to make the
        change durable, still aborts like any other publication error.
        """
        reached = False
        try:
            with self._parent(entry["path"]) as parent:
                if parent is None:
                    return None
                fd = os.open(entry["path"].split("/")[-1], DIRECTORY_FLAGS, dir_fd=parent)
                try:
                    if not _created_directory_matches(entry, saved["file"], _directory_fingerprint(fd)):
                        return None
                    reached = True
                    os.fchmod(fd, _final_mode(entry))
                    os.utime(fd, ns=(entry["mtime_ns"], entry["mtime_ns"]))
                    os.fsync(fd)
                    return _directory_fingerprint(fd)
                finally:
                    os.close(fd)
        except (OSError, probe.Rejected):
            if reached:
                raise
            return None

    def _link_ready(self, entry):
        resolution = probe.link_target(entry, self._entries)
        if resolution is None:
            return False
        target, traversed = resolution
        for path in (target, *traversed):
            if path not in self._ready:
                return False
            observed = self._observe(path, [], probe.entry_kind(self._entries[path]))
            if observed != self._ready[path]:
                return False
        return True

    def apply(self, plan):
        self._check()
        if plan is not self._plan or plan is None:
            raise probe.Rejected("apply requires this session's latest plan")
        if self._load() != self._journal:
            raise probe.Rejected("restore journal changed after planning")
        self._plan = None
        self._created_parents = {}
        self._ready = {}
        self._save()
        report = [None] * len(plan)
        for index in self._order():
            entry = self.bundle._manifest["entries"][index]
            action, before = plan[index], self._observations[index]
            kind = probe.entry_kind(entry)
            self._check()
            if action.status == "inert":
                report[index] = action
                continue
            if self._tree and any(path not in self._ready for path in _ancestors(entry["path"])):
                report[index] = self._action(entry, "conflict", "directory dependency is unavailable")
                continue
            parents = []
            observed = self._observe(entry["path"], parents, kind)
            previous_file, planned_parents = before
            parents_match = parents[:len(planned_parents)] == planned_parents and all(
                self._created_parents.get(path) == identity
                for path, identity in parents[len(planned_parents):]
            )
            if observed != previous_file or not parents_match:
                report[index] = self._action(entry, "conflict", "destination changed after planning")
                continue
            if action.status == "conflict":
                report[index] = action
                continue
            if kind == "directory":
                report[index], fingerprint = self._restore_directory(entry, action, observed)
                if fingerprint is not None:
                    self._ready[entry["path"]] = fingerprint
                continue
            if kind == "symlink" and not self._link_ready(entry):
                report[index] = self._action(entry, "conflict", "link target or traversal directory is unavailable")
                continue
            if action.status == "present":
                if self._tree or self._replace:
                    # Retain a witness even for matching pre-existing entries;
                    # their later deletion must not become permission to copy.
                    self._journal["entries"][entry["object"]] = {
                        "state": "retained", "file": observed, "temporary": None,
                    }
                    self._save(entry["object"])
                self._ready[entry["path"]] = observed
                report[index] = action
                continue
            previous = self._journal["entries"].get(entry["object"])
            if action.status == "restored":
                if not self._backup_valid(previous):
                    report[index] = self._action(entry, "conflict", "original backup changed or disappeared")
                    continue
                if previous.get("exchanging") and not self._settle_exchange(entry, previous):
                    report[index] = self._action(entry, "conflict", "destination changed during publication")
                    continue
                # Re-establish directory durability if the earlier process
                # stopped between link publication and its directory fsync.
                with self._parent(entry["path"]) as parent:
                    os.fsync(parent)
                if "backup" in previous:
                    os.fsync(self._job_fd)
                if previous["state"] != "retained":
                    previous["state"] = "applied"
                self._save(entry["object"])
                self._cleanup(previous)
                self._ready[entry["path"]] = observed
                report[index] = Action(action.path, action.status, action.reason,
                                       previous.get("backup", {}).get("name"))
                continue
            backup = self._backup(entry, observed) if action.status == "replace" else None
            try:
                saved = self._prepare(entry)
            except BaseException:
                if backup is not None:
                    # No replacement intent exists yet. Remove only this
                    # invocation's known inode, avoiding repeated full copies.
                    self._cleanup({"temporary": backup["name"], "file": backup["file"]})
                raise
            if backup is not None:
                saved["backup"] = backup
            self._journal["entries"][entry["object"]] = saved
            self._save(entry["object"])  # Durable witness must precede target publication.
            if backup is not None:
                backup_valid = self._backup_valid(saved)
                current_parents = []
                current = self._observe(entry["path"], current_parents, kind)
                if current != observed or current_parents != parents or not backup_valid:
                    report[index] = self._action(entry, "conflict", "replacement inputs changed")
                    continue
            with self._parent(entry["path"], create=not self._tree) as parent:
                if parent is None:
                    raise probe.Rejected("destination parent disappeared")
                try:
                    name = entry["path"].split("/")[-1]
                    if backup is not None and self._swap(entry, saved, parent, name):
                        if not self._settle_exchange(entry, saved, parent):
                            report[index] = self._action(entry, "conflict", "destination changed during publication")
                            continue
                    elif backup is not None:
                        # Without an atomic swap the caller keeps leaves as
                        # well as directories quiescent: replace() is not an
                        # inode compare-and-swap.
                        os.replace(saved["temporary"], name, src_dir_fd=self._job_fd, dst_dir_fd=parent)
                        os.fsync(self._job_fd)
                    else:
                        os.link(saved["temporary"], name,
                                src_dir_fd=self._job_fd, dst_dir_fd=parent, follow_symlinks=False)
                except FileExistsError:
                    report[index] = Action(entry["path"], "conflict", "destination appeared during publication")
                    continue
                os.fsync(parent)
            saved["state"] = "applied"
            self._save(entry["object"])
            self._cleanup(saved)
            self._ready[entry["path"]] = saved["file"]
            report[index] = Action(entry["path"], "replaced" if backup else "restored",
                                   "original retained in private backup" if backup else "new entry published",
                                   backup["name"] if backup else None)
        if self._tree:
            self._finalize_directories(report)
        return tuple(report)
