"""Claim a staged Try export at the native system's first boot.

Runs as root from omarchy-migration-claim.service. Copies the staged region
of the Root partition into /var/lib/omarchy-migration/incoming/<export_id>/,
checks its digest, and deletes the ESP marker. A failure deletes the marker
too and records claim-failed.json with a stable reason code.
"""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from . import contract, staging

MARKER = Path("/boot/efi/omarchy/migration.json")
STATE = Path("/var/lib/omarchy-migration")
FAILED_SCHEMA = "omarchy-migration/claim-failed/1"
FAILED_NAME = "claim-failed.json"
COPY_CHUNK = 4 * 1024 * 1024
SPACE_MARGIN = 4 * 1024 ** 3
SECTOR = 512
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class ClaimError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def block_device_number(target):
    """The major:minor of a block device node, a path or an open descriptor."""
    metadata = os.stat(target)
    if not stat.S_ISBLK(metadata.st_mode):
        raise ClaimError("root_device_unknown")
    return f"{os.major(metadata.st_rdev)}:{os.minor(metadata.st_rdev)}"


def _unescape(field):
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), field)


def mount_source(mountinfo, mount_point):
    """The source device of the topmost mount at `mount_point`."""
    source = None
    try:
        lines = Path(mountinfo).read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        raise ClaimError("root_device_unknown") from None
    for line in lines:
        fields = line.split(" ")
        if " - " not in line or len(fields) < 10:
            continue
        after = line.split(" - ", 1)[1].split(" ")
        if _unescape(fields[4]) == str(mount_point) and len(after) >= 2:
            source = _unescape(after[1])
    if source is None or not source.startswith("/"):
        raise ClaimError("root_device_unknown")
    return source


def _read(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeDecodeError):
        raise ClaimError("root_device_unknown") from None


def _node(sysfs, number):
    node = Path(sysfs) / "dev/block" / number
    if not node.is_symlink() and not node.is_dir():
        raise ClaimError("root_device_unknown")
    return node.resolve()


def root_partition(sysfs, number):
    """The sysfs node of the partition under a root device: itself, or a LUKS mapping's one slave."""
    node = _node(sysfs, number)
    if (node / "partition").is_file():
        return node
    if (node / "dm/uuid").is_file() and _read(node / "dm/uuid").startswith("CRYPT-LUKS"):
        slaves = sorted((node / "slaves").iterdir()) if (node / "slaves").is_dir() else []
        if len(slaves) == 1 and (slaves[0].resolve() / "partition").is_file():
            return slaves[0].resolve()
    raise ClaimError("root_device_unsupported")


@contextlib.contextmanager
def open_partition(marker, *, root, sysfs, by_partuuid, mountinfo, device_number):
    """The Root partition under `root`, opened read-only, and its length in bytes."""
    partition = root_partition(sysfs, device_number(mount_source(mountinfo, root)))
    try:
        fd = os.open(Path(by_partuuid) / marker["partition_uuid"], os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        raise ClaimError("partition_mismatch") from None
    try:
        if _node(sysfs, device_number(fd)) != partition:
            raise ClaimError("partition_mismatch")
        try:
            length = int(_read(partition / "size")) * SECTOR
        except ValueError:
            raise ClaimError("root_device_unknown") from None
        if os.lseek(fd, 0, os.SEEK_END) != length:
            raise ClaimError("partition_mismatch")
        yield fd, length
    finally:
        os.close(fd)


def read_marker(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError:
        raise ClaimError("marker_unsafe") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ClaimError("marker_unsafe")
    with os.fdopen(fd, "rb") as source:
        data = source.read(staging.MAX_MARKER + 1)
    try:
        return staging.parse_marker(data)
    except staging.MarkerError as error:
        raise ClaimError(error.code) from None


def delete_marker(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as error:
        print(f"omarchy-migration claim: could not delete the marker: {error}", file=sys.stderr)
        return
    with contextlib.suppress(OSError):
        parent = os.open(Path(path).parent, DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)


def _directory(parent, name, mode):
    try:
        os.mkdir(name, mode, dir_fd=parent)
    except FileExistsError:
        pass
    fd = os.open(name, DIRECTORY, dir_fd=parent)
    metadata = os.fstat(fd)
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
        os.close(fd)
        raise ClaimError("state_unsafe")
    os.fchmod(fd, mode)
    return fd


def _open_state(state):
    Path(state).mkdir(mode=0o755, parents=True, exist_ok=True)
    fd = os.open(state, DIRECTORY)
    metadata = os.fstat(fd)
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
        os.close(fd)
        raise ClaimError("state_unsafe")
    return fd


def _write(directory, name, data, mode):
    partial = name + ".partial"
    with contextlib.suppress(FileNotFoundError):
        os.unlink(partial, dir_fd=directory)
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=directory)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fchmod(fd, mode)
        os.fsync(fd)
    os.rename(partial, name, src_dir_fd=directory, dst_dir_fd=directory)
    os.fsync(directory)


def _document(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def _exists(directory, name):
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _complete(export, marker):
    """Whether an earlier claim already wrote this export's bundle and receipt."""
    try:
        fd = os.open("receipt.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=export)
    except FileNotFoundError:
        return False
    with os.fdopen(fd, "rb") as source:
        try:
            receipt = contract.parse(source.read(contract.MAX_DOCUMENT + 1))
        except contract.ContractError:
            return False
    try:
        bundle = os.stat("bundle.age", dir_fd=export, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return receipt == marker["receipt"] and stat.S_ISREG(bundle.st_mode) and bundle.st_size == marker["length"]


def free_bytes(fd):
    status = os.fstatvfs(fd)
    return status.f_bavail * status.f_frsize


def _copy(partition, offset, length, export, expected):
    name = "bundle.age.partial"
    with contextlib.suppress(FileNotFoundError):
        os.unlink(name, dir_fd=export)
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=export)
    checksum = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb", buffering=0) as output:
            done = 0
            while done < length:
                piece = os.pread(partition, min(COPY_CHUNK, length - done), offset + done)
                if not piece:
                    raise ClaimError("region_unreadable")
                checksum.update(piece)
                view = memoryview(piece)
                while view:
                    view = view[output.write(view):]
                done += len(piece)
            os.fsync(fd)
        if checksum.hexdigest() != expected:
            raise ClaimError("digest_mismatch")
        os.rename(name, "bundle.age", src_dir_fd=export, dst_dir_fd=export)
        os.fsync(export)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name, dir_fd=export)
        raise


def _clear(export):
    for name in os.listdir(export):
        os.unlink(name, dir_fd=export)
    os.fsync(export)


def claim(marker_path=MARKER, *, root=Path("/"), state=STATE, sysfs=Path("/sys"),
          by_partuuid=Path("/dev/disk/by-partuuid"), mountinfo=Path("/proc/self/mountinfo"),
          device_number=None, margin=None):
    """Claim the staged export the marker names. Returns its export id, or None without a marker."""
    device_number = device_number or block_device_number
    margin = SPACE_MARGIN if margin is None else margin
    marker = read_marker(marker_path)
    if marker is None:
        return None
    export_id = marker["export_id"]
    state_fd = _open_state(state)
    try:
        claimed = _directory(state_fd, "claimed", 0o700)
        pending = _directory(state_fd, "pending", 0o755)
        incoming = _directory(state_fd, "incoming", 0o700)
        try:
            if not _exists(claimed, export_id):
                export = _directory(incoming, export_id, 0o700)
                try:
                    if not _complete(export, marker):
                        _clear(export)
                        os.fsync(incoming)
                        with open_partition(marker, root=root, sysfs=sysfs, by_partuuid=by_partuuid,
                                            mountinfo=mountinfo, device_number=device_number) as (partition, length):
                            if marker["offset"] != staging.staging_region(length, marker["length"]):
                                raise ClaimError("region_invalid")
                            if free_bytes(export) < marker["length"] + margin:
                                raise ClaimError("insufficient_space")
                            _copy(partition, marker["offset"], marker["length"], export, marker["sha256"])
                        _write(export, "receipt.json", _document(marker["receipt"]), 0o600)
                        os.fsync(incoming)
                except BaseException:
                    if not _complete(export, marker):
                        with contextlib.suppress(OSError):
                            _clear(export)
                            os.rmdir(export_id, dir_fd=incoming)
                            os.fsync(incoming)
                    raise
                finally:
                    os.close(export)
                _write(pending, export_id, b"", 0o644)
                _write(claimed, export_id, b"", 0o600)
        finally:
            for fd in (claimed, pending, incoming):
                os.close(fd)
    finally:
        os.close(state_fd)
    delete_marker(marker_path)
    return export_id


def record_failure(state, export_id, code):
    state_fd = _open_state(state)
    try:
        _write(state_fd, FAILED_NAME, _document({"schema": FAILED_SCHEMA, "export_id": export_id, "reason": code}), 0o644)
    finally:
        os.close(state_fd)


def _export_id(marker_path):
    """The export id a marker names, when it can be read at all, for the failure record."""
    with contextlib.suppress(OSError, ValueError, UnicodeDecodeError, AttributeError):
        fd = os.open(marker_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None
        with os.fdopen(fd, "rb") as source:
            value = json.loads(source.read(staging.MAX_MARKER + 1)).get("export_id")
            return contract._uuid(value, "$")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--marker", type=Path, default=MARKER)
    parser.add_argument("--root", type=Path, default=Path("/"), help="the mounted system root to claim into")
    parser.add_argument("--state", type=Path, default=None, help="default: ROOT/var/lib/omarchy-migration")
    parser.add_argument("--sysfs", type=Path, default=Path("/sys"))
    parser.add_argument("--by-partuuid", type=Path, default=Path("/dev/disk/by-partuuid"))
    parser.add_argument("--mountinfo", type=Path, default=Path("/proc/self/mountinfo"))
    arguments = parser.parse_args(argv)
    state = arguments.state or arguments.root / STATE.relative_to("/")
    try:
        export_id = claim(arguments.marker, root=arguments.root, state=state, sysfs=arguments.sysfs,
                          by_partuuid=arguments.by_partuuid, mountinfo=arguments.mountinfo)
        print(json.dumps({"claimed": export_id}))
        return 0
    except Exception as error:
        code = error.code if isinstance(error, ClaimError) else "claim_operation_failed"
        if not isinstance(error, ClaimError):
            print(f"omarchy-migration claim: {type(error).__name__}: {error}", file=sys.stderr)
        export_id = _export_id(arguments.marker)
        try:
            record_failure(state, export_id, code)
        except (ClaimError, OSError) as failure:
            print(f"omarchy-migration claim: could not record the failure: {failure}", file=sys.stderr)
        delete_marker(arguments.marker)
        print(json.dumps({"error": code}), file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
