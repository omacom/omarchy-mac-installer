"""Export a real Omarchy home into one encrypted bundle and its receipt.

Driven by an export-request/2 document. Collection goes through the
policy-aware collector, encryption through age with the transfer passphrase,
and the bundle is decrypted and checked once before its receipt is written.
Progress is reported as progress/1 documents.
"""

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from . import collection, contract, probe
from . import policy as migration_policy
from .categories import category
from .dependency import admit_age

POLICY_PATH = Path(__file__).resolve().parent / "policies/try-omarchy-82927e9.json"
CAPTURE_ATTEMPTS = 3
# Collector rejections caused by a home changing under a live capture. The
# export boot captures a quiet home; a trial can run in a working session.
LIVE_CHANGES = frozenset({
    "source directory changed during capture", "source directory replaced",
    "source file replaced before capture", "source file grew during capture",
    "source file changed during capture", "source entry changed during capture",
    "source changed before snapshot completion", "source root changed before completion",
})
MAX_PASSPHRASE = 1024


class ExportError(ValueError):
    pass


def selection_roots(home, categories):
    """Top-level home entries whose category was selected, as collection roots."""
    names = []
    with os.scandir(home) as listing:
        for entry in listing:
            if category(entry.name) in categories:
                names.append(entry.name)
    return [{"source": name, "archive": name} for name in sorted(names)]


def check_request(request, policy):
    if contract.validate(request) != contract.EXPORT_REQUEST:
        raise ExportError("unsupported_request")
    if request["policy_revision"] != policy.revision:
        raise ExportError("policy_revision_mismatch")
    selection = request["selection"]
    stores = {store["id"]: store for store in policy.stores}
    if not set(selection["credential_stores"]) <= set(stores):
        raise ExportError("unknown_credential_store")
    # No production credential adapter exists yet; selected stores cannot be exported.
    if selection["credential_stores"]:
        raise ExportError("credential_store_unavailable")
    if not set(selection["mounts"]) <= {mount["id"] for mount in policy.mounts}:
        raise ExportError("unknown_mount")
    if not set(selection["share_stores"]) <= {store["id"] for store in policy.share_stores}:
        raise ExportError("unknown_share_store")


def read_passphrase(descriptor):
    data = b""
    while len(data) < MAX_PASSPHRASE + 2:
        piece = os.read(descriptor, MAX_PASSPHRASE + 2 - len(data))
        if not piece:
            break
        data += piece
    secret = data[:-1] if data.endswith(b"\n") else data
    if not secret or len(secret) > MAX_PASSPHRASE or any(byte in secret for byte in b"\n\r\0"):
        raise ExportError("passphrase_invalid")
    return secret


def _private_directory(path):
    path.mkdir(mode=0o700)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise ExportError("unsafe_output_directory")


def _write_document(path, value):
    data = (json.dumps(value, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def export_home(request, home, output, *, age, secret, policy_document, share_roots=None, scratch=None,
                emit=lambda phase, **fields: None):
    """Write bundle.age, receipt.json and request.json into a new private `output`."""
    policy = migration_policy.Policy(policy_document)
    check_request(request, policy)
    home, output = Path(home), Path(output)
    selection = request["selection"]
    collection_request = {
        "schema": collection.REQUEST_SCHEMA, "request_id": request["request_id"],
        "selection": selection_roots(home, selection["categories"]), "selected_adapters": [],
        "selected_mounts": sorted(selection["mounts"]), "selected_share_stores": sorted(selection["share_stores"]),
    }
    if not collection_request["selection"]:
        raise ExportError("nothing_selected")
    _private_directory(output)
    _write_document(output / "request.json", request)
    emit("preparing")
    with tempfile.TemporaryDirectory(prefix="omarchy-migration-export-", dir=scratch) as temporary:
        parent = Path(temporary)
        parent.chmod(0o700)
        for attempt in range(1, CAPTURE_ATTEMPTS + 1):
            try:
                with collection.collect_fixture(home, collection_request, policy_document,
                                                snapshot_parent=parent, share_roots=share_roots) as snapshot:
                    manifest = snapshot.manifest
                    emit("capturing")
                    encrypted = probe.encrypt(age, secret, output / "bundle.age",
                                              lambda stream: probe.write_archive(stream, manifest, snapshot.paths))
                break
            except probe.Rejected as error:
                if str(error) not in LIVE_CHANGES or attempt == CAPTURE_ATTEMPTS:
                    raise
    emit("finalizing")
    if probe.decode(age, secret, output / "bundle.age") != manifest:
        raise ExportError("validation_failed")
    receipt = {
        "schema": contract.RECEIPT, "request_id": request["request_id"], "export_id": manifest["export_id"],
        "policy_revision": policy.revision,
        "bundle": {"format": contract.BUNDLE_FORMAT, "schema": contract.BUNDLE,
                   "bytes": encrypted["bytes"], "sha256": encrypted["sha256"]},
        "estimates": {"expanded_bytes": sum(entry.get("bytes", 0) for entry in manifest["entries"]),
                      "entries": len(manifest["entries"])},
    }
    contract.validate(receipt)
    _write_document(output / "receipt.json", receipt)
    emit("complete", receipt=receipt, reused=False)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", type=Path, required=True, help="an export-request/2 document")
    parser.add_argument("--output", type=Path, required=True, help="new directory for bundle.age and receipt.json")
    parser.add_argument("--passphrase-fd", type=int, required=True, help="read the transfer passphrase from this descriptor")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--age", default=None, help="age executable (default: age on PATH)")
    parser.add_argument("--age-sha256", default=None, help="require this digest of the age executable")
    parser.add_argument("--scratch", type=Path, default=None, help="private scratch parent (default: system temp)")
    parser.add_argument("--share-root", action="append", default=[], metavar="MOUNT=PATH",
                        help="read a selected shared folder from PATH instead of its mount point")
    arguments = parser.parse_args(argv)
    request_id, sequence = None, 0

    def emit(phase, **fields):
        nonlocal sequence
        sequence += 1
        event = {"schema": contract.PROGRESS, "request_id": request_id, "sequence": sequence, "phase": phase, **fields}
        contract.validate(event)
        print(json.dumps(event, sort_keys=True), flush=True)

    try:
        raw = arguments.request.read_bytes()
        if len(raw) > contract.MAX_REQUEST:
            raise ExportError("oversized_request")
        try:
            request = contract.parse(raw)
        except contract.ContractError:
            raise ExportError("invalid_request") from None
        request_id = request.get("request_id")
        age = admit_age(arguments.age, arguments.age_sha256)
        secret = read_passphrase(arguments.passphrase_fd)
        roots = {}
        for item in arguments.share_root:
            mount, _, path = item.partition("=")
            roots[mount] = path
        export_home(request, arguments.home, arguments.output, age=age, secret=secret,
                    policy_document=json.loads(POLICY_PATH.read_bytes()), share_roots=roots or None,
                    scratch=arguments.scratch, emit=emit)
        return 0
    except ExportError as error:
        emit("failed", error=str(error))
        return 1
    except (probe.Rejected, contract.ContractError, OSError, RuntimeError) as error:
        # The progress code stays stable; the diagnostic is for the log only.
        print(f"omarchy-migration export: {type(error).__name__}: {error}", file=sys.stderr)
        emit("failed", error="export_operation_failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
