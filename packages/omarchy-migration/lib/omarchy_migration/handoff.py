"""Hand a claimed export to the owner who runs the import.

Runs as root through `sudo /usr/lib/omarchy-migration/handoff`. Moves each
claimed export into the invoking user's
~/.local/share/omarchy-migration/incoming/<export_id>/, writing the home only
after dropping to that user, and prepares the user's private work folder
beside the home for the restore job and its scratch.
"""

import argparse
import contextlib
import errno
import fcntl
import json
import os
from pathlib import Path
import pwd
import stat
import sys

from . import contract
from .claim import STATE

INCOMING = (".local", "share", "omarchy-migration", "incoming")
WORK = ".omarchy-migration"
FICLONE = 0x40049409
COPY_CHUNK = 4 * 1024 * 1024
SPACE_MARGIN = 64 * 1024 * 1024
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class HandoffError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def invoking_user(environ, lookup=pwd.getpwuid):
    """The account sudo ran this for: a regular user whose name matches its uid."""
    try:
        uid = int(environ["SUDO_UID"])
        name = environ["SUDO_USER"]
    except (KeyError, ValueError):
        raise HandoffError("not_run_by_sudo") from None
    if uid < 1000:
        raise HandoffError("user_refused")
    try:
        account = lookup(uid)
    except KeyError:
        raise HandoffError("user_refused") from None
    if account.pw_name != name or not account.pw_dir.startswith("/"):
        raise HandoffError("user_refused")
    return account


def _child(parent, name, mode, owner):
    """Open a directory below `parent` without following links, creating it when missing."""
    created = False
    try:
        os.mkdir(name, mode, dir_fd=parent)
        created = True
    except FileExistsError:
        pass
    try:
        fd = os.open(name, DIRECTORY, dir_fd=parent)
    except OSError:
        raise HandoffError("unsafe_home") from None
    metadata = os.fstat(fd)
    if metadata.st_uid != owner or metadata.st_mode & 0o022:
        os.close(fd)
        raise HandoffError("unsafe_home")
    if created:
        os.fsync(parent)
    return fd


def open_incoming(home, export_id):
    """The user's own incoming folder for one export, reached from the home without following links."""
    try:
        fd = os.open(home, DIRECTORY)
    except OSError:
        raise HandoffError("unsafe_home") from None
    metadata = os.fstat(fd)
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
        os.close(fd)
        raise HandoffError("unsafe_home")
    for name in (*INCOMING, export_id):
        child = _child(fd, name, 0o700, os.geteuid())
        os.close(fd)
        fd = child
    os.fchmod(fd, 0o700)
    return fd


def _copy(source, output, size):
    """Clone the bytes when the filesystem can share them, or copy them."""
    try:
        fcntl.ioctl(output, FICLONE, source)
        return
    except OSError:
        pass
    status = os.fstatvfs(output)
    if status.f_bavail * status.f_frsize < size + SPACE_MARGIN:
        raise HandoffError("insufficient_space")
    done = 0
    while done < size:
        piece = os.pread(source, min(COPY_CHUNK, size - done), done)
        if not piece:
            raise HandoffError("claimed_export_changed")
        view = memoryview(piece)
        while view:
            view = view[os.write(output, view):]
        done += len(piece)


def _publish(directory, name, source, size):
    partial = name + ".partial"
    with contextlib.suppress(FileNotFoundError):
        os.unlink(partial, dir_fd=directory)
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
    try:
        _copy(source, fd, size)
        if os.fstat(fd).st_size != size:
            raise HandoffError("claimed_export_changed")
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(partial, dir_fd=directory)
        raise
    os.close(fd)
    os.rename(partial, name, src_dir_fd=directory, dst_dir_fd=directory)


def deliver(home, export_id, bundle, receipt):
    """As the receiving user: write the bundle and receipt descriptors into the home's incoming folder."""
    directory = open_incoming(home, export_id)
    try:
        for name, source in (("bundle.age", bundle), ("receipt.json", receipt)):
            _publish(directory, name, source, os.fstat(source).st_size)
        os.fsync(directory)
    finally:
        os.close(directory)
    return Path(home, *INCOMING, export_id)


def prepare_work(home, uid, gid, *, trusted=0):
    """The user's private work folder beside the home, on the home's filesystem.

    The restorer publishes by hard link and keeps its job outside the home, so
    the job and the decrypted scratch need a folder like this one.
    """
    home = Path(home)
    try:
        parent = os.open(home.parent, DIRECTORY)
    except OSError:
        raise HandoffError("work_unavailable") from None
    try:
        metadata = os.fstat(parent)
        if metadata.st_uid != trusted or metadata.st_mode & 0o022:
            raise HandoffError("work_unavailable")
        try:
            os.mkdir(WORK, 0o711, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
        try:
            shared = os.open(WORK, DIRECTORY, dir_fd=parent)
        except OSError:
            raise HandoffError("work_unavailable") from None
    finally:
        os.close(parent)
    try:
        metadata = os.fstat(shared)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != trusted or metadata.st_mode & 0o022:
            raise HandoffError("work_unavailable")
        os.fchmod(shared, 0o711)
        try:
            os.mkdir(str(uid), 0o700, dir_fd=shared)
            os.chown(str(uid), uid, gid, dir_fd=shared, follow_symlinks=False)
            os.fsync(shared)
        except FileExistsError:
            pass
        try:
            work = os.open(str(uid), DIRECTORY, dir_fd=shared)
        except OSError:
            raise HandoffError("work_unavailable") from None
    finally:
        os.close(shared)
    try:
        metadata = os.fstat(work)
        # Only root creates entries here: a root-owned empty folder is this
        # handoff's own mkdir, interrupted before its chown.
        if metadata.st_uid == trusted and stat.S_ISDIR(metadata.st_mode) and not os.listdir(work):
            os.fchown(work, uid, gid)
            metadata = os.fstat(work)
        home_metadata = os.stat(home, follow_symlinks=False)
        if metadata.st_uid != uid or metadata.st_dev != home_metadata.st_dev or not stat.S_ISDIR(home_metadata.st_mode):
            raise HandoffError("work_unavailable")
        os.fchmod(work, 0o700)
    finally:
        os.close(work)
    return home.parent / WORK / str(uid)


def pending_exports(state):
    try:
        names = os.listdir(Path(state) / "pending")
    except FileNotFoundError:
        return []
    exports = []
    for name in sorted(names):
        with contextlib.suppress(contract.ContractError):
            exports.append(contract._uuid(name, "$"))
    return exports


def _open_claimed(state, export_id):
    """The claimed bundle and receipt, opened as root, after checking they belong together."""
    directory = os.open(Path(state) / "incoming" / export_id, DIRECTORY)
    try:
        bundle = os.open("bundle.age", FILE, dir_fd=directory)
        receipt = os.open("receipt.json", FILE, dir_fd=directory)
    finally:
        os.close(directory)
    try:
        with os.fdopen(os.dup(receipt), "rb") as source:
            document = contract.parse(source.read(contract.MAX_DOCUMENT + 1))
        os.lseek(receipt, 0, os.SEEK_SET)
        metadata = os.fstat(bundle)
        if (not stat.S_ISREG(metadata.st_mode) or document["export_id"] != export_id
                or metadata.st_size != document["bundle"]["bytes"]):
            raise HandoffError("claimed_export_invalid")
        return bundle, receipt
    except BaseException:
        os.close(bundle)
        os.close(receipt)
        raise


def _as_user(account, work):
    """Run `work()` in a child with the account's identity; its error code comes back."""
    read, write = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read)
        code = ""
        try:
            os.setgroups(os.getgrouplist(account.pw_name, account.pw_gid))
            os.setresgid(account.pw_gid, account.pw_gid, account.pw_gid)
            os.setresuid(account.pw_uid, account.pw_uid, account.pw_uid)
            work()
        except HandoffError as error:
            code = error.code
        except BaseException:
            code = "handoff_operation_failed"
        os.write(write, code.encode())
        os._exit(1 if code else 0)
    os.close(write)
    with os.fdopen(read, "rb") as stream:
        code = stream.read(64).decode(errors="replace")
    _, status = os.waitpid(child, 0)
    if status or code:
        raise HandoffError(code or "handoff_operation_failed")


def _remove_claimed(state, export_id):
    directory = os.open(Path(state) / "incoming", DIRECTORY)
    try:
        export = os.open(export_id, DIRECTORY, dir_fd=directory)
        try:
            for name in os.listdir(export):
                os.unlink(name, dir_fd=export)
        finally:
            os.close(export)
        os.rmdir(export_id, dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(directory)
    pending = os.open(Path(state) / "pending", DIRECTORY)
    try:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(export_id, dir_fd=pending)
        os.fsync(pending)
    finally:
        os.close(pending)


def handoff(account, state=STATE, *, trusted=0):
    work = prepare_work(account.pw_dir, account.pw_uid, account.pw_gid, trusted=trusted)
    result = {"work": str(work), "exports": [], "incoming": str(Path(account.pw_dir, *INCOMING))}
    try:
        lock = os.open(state, DIRECTORY)
    except FileNotFoundError:
        return result
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for export_id in pending_exports(state):
            try:
                bundle, receipt = _open_claimed(state, export_id)
            except FileNotFoundError:
                continue
            try:
                _as_user(account, lambda: deliver(account.pw_dir, export_id, bundle, receipt))
            finally:
                os.close(bundle)
                os.close(receipt)
            _remove_claimed(state, export_id)
            result["exports"].append(export_id)
    finally:
        os.close(lock)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, default=STATE, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise HandoffError("not_root")
        account = invoking_user(os.environ)
        print(json.dumps(handoff(account, arguments.state), sort_keys=True))
        return 0
    except HandoffError as error:
        print(json.dumps({"error": error.code}), file=sys.stderr)
        return 1
    except (OSError, contract.ContractError) as error:
        if isinstance(error, OSError) and error.errno == errno.ENOENT:
            print(json.dumps({"error": "claimed_export_missing"}), file=sys.stderr)
        else:
            print(json.dumps({"error": "handoff_operation_failed"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
