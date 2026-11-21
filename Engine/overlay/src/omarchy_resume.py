# SPDX-License-Identifier: MIT
"""Explicit recovery of the released native-Recovery staging interruption.

The privileged helper owns discovery, authorization and input snapshots. This
path keeps the original plan and journal; it never relaxes normal retry rules.
"""

import hashlib
import contextlib
import ctypes
import errno
import os
from pathlib import Path
import stat
import sys

from omarchy_execution import ExecutionPlan
from omarchy_stage1 import run_stage1


ORIGINAL_ENGINES = {
    "v0.9.2-omarchy.20261022.31425f17fb9b":
        "sha256:5fea6b561e6fb82260f573bae099c70ba2e9998df56d38cf285987432320d4b5",
}
BOUND_CHECKPOINT = "interrupted-stub-and-esp-bound"
REBOUND_CHECKPOINT = "interrupted-stub-and-esp-rebound"
REBOUND_CHECKPOINT_V2 = "interrupted-stub-and-esp-rebound-2"
# The initial resume engine verified the target but rejected Apple's framework
# links before reconstruction. Retain its evidence when upgrading this guard.
PREVIOUS_GUARD_ENGINE = "sha256:f314b55eeb6268cc92503ea5f5cec799035b0ecb6b78b83bfaa5c3c319ac985d"
PREVIOUS_RESUME_ENGINES = {
    PREVIOUS_GUARD_ENGINE,
    "sha256:559c2bb177f062baa0d0a4830ab611ab272f2ec90c7e4d0589fba09fdb55b32f",
    "sha256:2e3a5a138c25912ec7301a0380614371f96f2f8a70b48fdd3ff0bf8f899c0b68",
    # The reviewed PR engine before rebasing onto the partition-receipt fixes.
    "sha256:ca2484488365ad34abacfead5400e959b6f5f88ffba011a97ea278693bef77e8",
}
BOOTABILITY_FRAMEWORK_LINKS = {
    "Resources": "Versions/Current/Resources",
    "BootabilityBrain": "Versions/Current/BootabilityBrain",
    "Versions/Current": "A",
}


class ResumeError(ValueError):
    pass


def _binding_checkpoint(name, payload):
    if name in (BOUND_CHECKPOINT, REBOUND_CHECKPOINT, REBOUND_CHECKPOINT_V2):
        return True
    prefix = REBOUND_CHECKPOINT + "-"
    suffix = name.removeprefix(prefix)
    return (name.startswith(prefix) and len(suffix) == 64
            and all(c in "0123456789abcdef" for c in suffix)
            and payload["evidence_digest"] == "sha256:" + suffix)


def original_plan(journal):
    """Recover authority from the helper's private, validated original journal."""
    record = journal.plan_payload
    if (record is None or record["candidate_kind"] not in ("resize", "free")
            or ORIGINAL_ENGINES.get(record["engine_version"]) != record["engine_digest"]
            or journal.inspection_payload["support"] != "supported"):
        raise ResumeError("this installation record is not supported for resumption")
    allowed_checkpoints = {
        "apfs-target-prepared", BOUND_CHECKPOINT, REBOUND_CHECKPOINT, REBOUND_CHECKPOINT_V2,
        "stub-and-esp-installed",
        "recovery-handoff-prepared",
    }
    bindings = {name for name, payload in journal.checkpoints.items()
                if _binding_checkpoint(name, payload)}
    allowed_checkpoints.update(bindings)
    if (not {"apfs_preparation_started", "stub_and_esp_started"} <= journal.events
            or journal.events - {
                "apfs_preparation_started", "stub_and_esp_started", "recovery_handoff_started"}
            or "apfs-target-prepared" not in journal.checkpoints
            or set(journal.checkpoints) - allowed_checkpoints
            or (bindings - {BOUND_CHECKPOINT}
                and BOUND_CHECKPOINT not in journal.checkpoints)
            or ("stub-and-esp-installed" in journal.checkpoints
                and BOUND_CHECKPOINT not in journal.checkpoints)
            or ("recovery_handoff_started" in journal.events
                and "stub-and-esp-installed" not in journal.checkpoints)
            or (journal.completion_outcome is not None
                and (journal.completion_outcome != "awaiting_recovery"
                     or "recovery-handoff-prepared" not in journal.checkpoints
                     or BOUND_CHECKPOINT not in journal.checkpoints))):
        raise ResumeError("installation is not at the supported Recovery staging boundary")
    candidates = [c for c in journal.inventory_payload["candidates"]
                  if c["kind"] == record["candidate_kind"]
                  and c["source_identifier"] == record["source_identifier"]]
    if len(candidates) != 1:
        raise ResumeError("original installation candidate is ambiguous")
    candidate = candidates[0]
    return ExecutionPlan(
        binding_digest=Path(journal.path).stem,
        operation="install", plan_digest=record["plan_digest"],
        device_identifier=record["device_identifier"],
        store_identifier=record["store_identifier"], layout_digest=record["layout_digest"],
        candidate_kind=record["candidate_kind"], source_identifier=record["source_identifier"],
        offset_bytes=record["offset_bytes"], length_bytes=record["length_bytes"],
        minimum_install_bytes=candidate["minimum_install_bytes"],
        minimum_container_bytes=candidate["minimum_container_bytes"],
        candidate_identity_digest=None, engine_version=record["engine_version"],
        engine_digest=record["engine_digest"], metadata_digest=record["metadata_digest"],
        payload_digest=record["payload_digest"], repair_manifest_digest=None,
        required_human_steps=tuple(record["required_human_steps"]),
    )


def verify_input(path, expected_digest):
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        details = os.fstat(source.fileno())
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid()
                or details.st_mode & 0o077 or details.st_size <= 0):
            raise ResumeError("resume input is not a private helper snapshot")
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if "sha256:" + digest != expected_digest:
        raise ResumeError("resume input differs from the original authorized payload")


def _reject_acl(descriptor):
    if sys.platform != "darwin":
        return
    libc = ctypes.CDLL(None, use_errno=True)
    libc.acl_get_fd.argtypes = [ctypes.c_int]
    libc.acl_get_fd.restype = ctypes.c_void_p
    libc.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                  ctypes.POINTER(ctypes.c_void_p)]
    libc.acl_free.argtypes = [ctypes.c_void_p]
    acl = libc.acl_get_fd(descriptor)
    if not acl:
        # macOS returns ENOENT for an fd without an extended ACL.
        if ctypes.get_errno() == errno.ENOENT:
            return
        raise ResumeError("could not verify the stub's access controls")
    try:
        entry = ctypes.c_void_p()
        result = libc.acl_get_entry(acl, 0, ctypes.byref(entry))  # ACL_FIRST_ENTRY
        if result == 0 or ctypes.get_errno() != errno.EINVAL:
            raise ResumeError("stub write paths have extended access controls")
    finally:
        libc.acl_free(acl)


def _check_write_tree(parent, name, device, relative, allowed_links):
    try:
        details = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(details.st_mode):
        if (relative in allowed_links
                and os.path.normpath(os.readlink(name, dir_fd=parent))
                == os.path.normpath(allowed_links[relative])):
            return
        raise ResumeError("stub write path contains a symbolic link: " + relative)
    if (details.st_dev != device or details.st_uid != os.geteuid()
            or details.st_mode & 0o022
            or not (stat.S_ISDIR(details.st_mode) or stat.S_ISREG(details.st_mode))
            or (stat.S_ISREG(details.st_mode) and details.st_nlink != 1)):
        raise ResumeError("stub write tree is not private to the privileged installer")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    if stat.S_ISDIR(details.st_mode):
        flags |= os.O_DIRECTORY
    descriptor = os.open(name, flags, dir_fd=parent)
    try:
        _reject_acl(descriptor)
        if stat.S_ISDIR(details.st_mode):
            for child in os.listdir(descriptor):
                _check_write_tree(descriptor, child, device, relative + "/" + child, allowed_links)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def protected_stub_trees(trees):
    """Seal target roots before checking stale trees used by upstream writers.

    Holding directory fds also prevents ordinary unmount. Child directories
    must already deny non-root writes, so a pre-opened fd cannot bypass the
    sealed root to rename a checked write path. Files cannot have hard links
    or ACL grants that could have allowed an outside writer to retain an fd.
    """
    opened = []
    try:
        for path, names, allowed_links in trees:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
            details = os.fstat(fd)
            opened.append((fd, stat.S_IMODE(details.st_mode)))
            if details.st_uid != os.geteuid():
                raise ResumeError("stub volume owner changed")
            _reject_acl(fd)
            os.fchmod(fd, 0o700)
            for name in names:
                _check_write_tree(fd, name, details.st_dev, name, allowed_links)
        yield
    finally:
        for fd, mode in reversed(opened):
            try:
                os.fchmod(fd, mode)
            finally:
                os.close(fd)


def resume_stage1(plan, journal, adapter):
    """Bind before mutation, finish staging, then use normal Recovery handoff."""
    if original_plan(journal) != plan:
        raise ResumeError("original installation plan changed")
    target = journal.checkpoint_evidence("apfs-target-prepared")
    installed = "stub-and-esp-installed" in journal.checkpoints
    if installed:
        adapter.validate_installed_checkpoint(
            plan, target, journal.checkpoint_evidence("stub-and-esp-installed"))
    else:
        bindings = [name for name, payload in journal.checkpoints.items()
                    if _binding_checkpoint(name, payload)]
        checkpoint = bindings[-1] if bindings else BOUND_CHECKPOINT
        previous = (journal.checkpoint_evidence(checkpoint)
                    if checkpoint in journal.checkpoints else None)
        bound = adapter.validate_interrupted_staging(plan, target, previous)
        if previous is not None and previous != bound:
            checkpoint = REBOUND_CHECKPOINT + "-" + hashlib.sha256(bound).hexdigest()
        _checkpoint(journal, checkpoint, bound)
    os.umask(0o022)
    with adapter.protect_resume(plan):
        if not installed:
            evidence = adapter.resume_interrupted_staging(plan, bound)
            _checkpoint(journal, "stub-and-esp-installed", evidence)
        return run_stage1(plan, journal, adapter)


def _checkpoint(journal, identifier, evidence):
    journal.checkpoint(identifier, "stub_and_esp", evidence)
    # The evidence's new directory entry must survive before reconstruction.
    directory = os.open(Path(journal.path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
