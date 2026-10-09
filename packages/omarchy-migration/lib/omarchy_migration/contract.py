"""Strict validators for the versioned omarchy-migration contract documents.

Pure standard-library checks with no file system, network, or subprocess
effects beyond reading the documents named on the command line. See
CONTRACT.md for the meaning of each field.
"""

import argparse
import json
from pathlib import PurePosixPath
import re
import sys
import uuid

PREFIX = "omarchy-migration/"
CAPABILITIES = PREFIX + "capabilities/1"
INVENTORY = PREFIX + "inventory/2"
EXPORT_REQUEST = PREFIX + "export-request/2"
PROGRESS = PREFIX + "progress/1"
RECEIPT = PREFIX + "receipt/1"
PLAN = PREFIX + "plan/1"
REPORT = PREFIX + "report/1"
POLICY = PREFIX + "policy/2"
BUNDLE = PREFIX + "bundle/2"
BUNDLE_FORMAT = "age-v1-scrypt"

MAX_DOCUMENT = 1024 * 1024
MAX_REQUEST = 8192
MAX_ITEMS = 1024  # list lengths inside a document
# Hard ceilings sized for real homes. The working bound is tighter: an
# importer refuses a bundle whose declared content exceeds its free space.
MAX_ENTRIES = 200_000
MAX_EXPANDED = 256 * 1024 ** 3
MAX_MANIFEST = 64 * 1024 ** 2
# age adds 16 bytes per 64 KiB chunk and TAR adds headers and padding per entry.
MAX_CIPHERTEXT = MAX_EXPANDED + MAX_MANIFEST + 1024 ** 3
MAX_PATH = 4096
MAX_COMPONENT = 255
MAX_DEPTH = 64
MAX_LABEL = 128

OPERATIONS = ("inventory", "export", "plan", "apply", "report")
PHASES = ("preparing", "capturing", "finalizing", "complete", "cancelled", "failed")
CATEGORY_OUTCOMES = ("restored", "partial", "skipped", "failed", "unavailable")
PROVIDERS = ("try-omarchy", "native")
ARCHITECTURES = ("aarch64",)
STORE_CATEGORIES = ("credentials", "browser-profile")
RULE_ACTIONS = ("exclude", "transform", "preserve")
MATCH_KINDS = ("exact", "tree")
TRANSFORMS = ("strip-appended-block", "remove-json-keys")

LABEL = re.compile(r"[a-z0-9][a-z0-9._/-]*")
CODE = re.compile(r"[a-z][a-z0-9_]*")
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+~_-]{0,63}")
FILE_PATTERN = re.compile(r"[A-Za-z0-9*?._-]+")


class ContractError(ValueError):
    """A document violates the contract; ``code`` is stable, ``where`` locates it."""

    def __init__(self, code, where):
        super().__init__(f"{code} at {where}")
        self.code = code
        self.where = where


def _fail(code, where):
    raise ContractError(code, where)


def _object(value, where, required, optional=()):
    if not isinstance(value, dict):
        _fail("not_an_object", where)
    keys = set(value)
    missing = set(required) - keys
    if missing:
        _fail("missing_field", f"{where}.{sorted(missing)[0]}")
    unknown = keys - set(required) - set(optional)
    if unknown:
        _fail("unknown_field", f"{where}.{sorted(unknown)[0]}")
    return value


def _integer(value, where, low=0, high=2**63 - 1):
    if type(value) is not int:
        _fail("not_an_integer", where)
    if not low <= value <= high:
        _fail("out_of_range", where)
    return value


def _boolean(value, where):
    if type(value) is not bool:
        _fail("not_a_boolean", where)
    return value


def _pattern(value, where, pattern, limit=MAX_LABEL):
    if not isinstance(value, str) or len(value.encode()) > limit or not pattern.fullmatch(value):
        _fail("invalid_string", where)
    return value


def _label(value, where):
    return _pattern(value, where, LABEL)


def _enum(value, where, choices):
    if value not in choices:
        _fail("unsupported_value", where)
    return value


def _uuid(value, where):
    try:
        if isinstance(value, str) and str(uuid.UUID(value)) == value:
            return value
    except ValueError:
        pass
    _fail("invalid_uuid", where)


def _list(value, where, item, minimum=0, maximum=MAX_ITEMS, unique=True):
    if not isinstance(value, list):
        _fail("not_a_list", where)
    if not minimum <= len(value) <= maximum:
        _fail("out_of_range", where)
    results = [item(element, f"{where}[{index}]") for index, element in enumerate(value)]
    if unique:
        seen = set()
        for index, result in enumerate(results):
            key = json.dumps(result, sort_keys=True)
            if key in seen:
                _fail("duplicate_item", f"{where}[{index}]")
            seen.add(key)
    return results


def home_path(value, where):
    """A relative path below the owner's home: no empty, '.', or '..' parts."""
    if not isinstance(value, str) or not value or "\0" in value or value.startswith("/"):
        _fail("unsafe_path", where)
    if len(value.encode()) > MAX_PATH:
        _fail("unsafe_path", where)
    parts = value.split("/")
    if len(parts) > MAX_DEPTH:
        _fail("unsafe_path", where)
    for part in parts:
        if part in ("", ".", "..") or len(part.encode()) > MAX_COMPONENT:
            _fail("unsafe_path", where)
    return value


def _overlaps(first, second):
    a, b = PurePosixPath(first).parts, PurePosixPath(second).parts
    return a[: len(b)] == b or b[: len(a)] == a


def _capabilities(document):
    _object(document, "$", ("schema", "module", "operations", "documents", "bundle_formats",
                            "policy_revisions", "adapters"))
    _object(document["module"], "$.module", ("name", "version"))
    if document["module"]["name"] != "omarchy-migration":
        _fail("unsupported_value", "$.module.name")
    _pattern(document["module"]["version"], "$.module.version", VERSION)
    _list(document["operations"], "$.operations", lambda v, w: _enum(v, w, OPERATIONS), minimum=1)
    documents = _list(document["documents"], "$.documents",
                      lambda v, w: _enum(v, w, tuple(VALIDATORS)), minimum=1)
    if CAPABILITIES not in documents:
        _fail("missing_value", "$.documents")
    _list(document["bundle_formats"], "$.bundle_formats",
          lambda v, w: _enum(v, w, (BUNDLE_FORMAT,)), minimum=1)
    _list(document["policy_revisions"], "$.policy_revisions", _label, minimum=1)

    def adapter(value, where):
        _object(value, where, ("id", "category", "available"), ("reason",))
        _label(value["id"], f"{where}.id")
        _enum(value["category"], f"{where}.category", STORE_CATEGORIES)
        if _boolean(value["available"], f"{where}.available") == ("reason" in value):
            _fail("inconsistent_fields", f"{where}.reason")
        if "reason" in value:
            _pattern(value["reason"], f"{where}.reason", CODE)
        return value["id"]

    _list(document["adapters"], "$.adapters", adapter)


def _source(value, where):
    _object(value, where, ("provider", "architecture", "omarchy_version", "account_uid"))
    _enum(value["provider"], f"{where}.provider", PROVIDERS)
    _enum(value["architecture"], f"{where}.architecture", ARCHITECTURES)
    _pattern(value["omarchy_version"], f"{where}.omarchy_version", VERSION)
    _integer(value["account_uid"], f"{where}.account_uid", 1000, 2**31 - 1)


def _inventory(document):
    _object(document, "$", ("schema", "inventory_id", "source", "policy_revision",
                            "categories", "credential_stores", "mounts"))
    _uuid(document["inventory_id"], "$.inventory_id")
    _source(document["source"], "$.source")
    _label(document["policy_revision"], "$.policy_revision")

    def category(value, where):
        _object(value, where, ("id", "files", "bytes", "default_selected"))
        _label(value["id"], f"{where}.id")
        _integer(value["files"], f"{where}.files")
        _integer(value["bytes"], f"{where}.bytes")
        _boolean(value["default_selected"], f"{where}.default_selected")
        return value["id"]

    def store(value, where):
        _object(value, where, ("id", "category", "present", "adapter_available"))
        _label(value["id"], f"{where}.id")
        _enum(value["category"], f"{where}.category", STORE_CATEGORIES)
        _boolean(value["present"], f"{where}.present")
        _boolean(value["adapter_available"], f"{where}.adapter_available")
        return value["id"]

    def share_store(value, where):
        _object(value, where, ("id", "category"))
        _label(value["id"], f"{where}.id")
        _enum(value["category"], f"{where}.category", STORE_CATEGORIES)
        return value["id"]

    def mount(value, where):
        # A shared folder: contents are counted only when measured, and the
        # credential stores found inside it are offered as their own choices.
        measured = value.get("measured") if isinstance(value, dict) else None
        _object(value, where, ("id", "linked", "measured")
                + (("files", "bytes", "share_stores") if measured is True else ()))
        _label(value["id"], f"{where}.id")
        _boolean(value["linked"], f"{where}.linked")
        _boolean(measured, f"{where}.measured")
        if measured:
            _integer(value["files"], f"{where}.files")
            _integer(value["bytes"], f"{where}.bytes")
            found = _list(value["share_stores"], f"{where}.share_stores", share_store)
            if len(set(found)) != len(found):
                _fail("duplicate_item", f"{where}.share_stores")
        return value["id"]

    categories = _list(document["categories"], "$.categories", category, minimum=1)
    stores = _list(document["credential_stores"], "$.credential_stores", store)
    _list(document["mounts"], "$.mounts", mount, maximum=32)
    if len(set(categories)) != len(categories):
        _fail("duplicate_item", "$.categories")
    if len(set(stores)) != len(stores):
        _fail("duplicate_item", "$.credential_stores")


def _export_request(document):
    _object(document, "$", ("schema", "request_id", "inventory_id", "policy_revision", "selection"))
    _uuid(document["request_id"], "$.request_id")
    _uuid(document["inventory_id"], "$.inventory_id")
    _label(document["policy_revision"], "$.policy_revision")
    selection = _object(document["selection"], "$.selection",
                        ("categories", "credential_stores", "mounts", "share_stores"))
    _list(selection["categories"], "$.selection.categories", _label, minimum=1)
    _list(selection["credential_stores"], "$.selection.credential_stores", _label)
    mounts = _list(selection["mounts"], "$.selection.mounts", _label, maximum=32)
    share_stores = _list(selection["share_stores"], "$.selection.share_stores", _label)
    if share_stores and not mounts:
        # Stores inside a share can only come along with that share.
        _fail("inconsistent_fields", "$.selection.share_stores")


def _receipt(document):
    _object(document, "$", ("schema", "request_id", "export_id", "policy_revision", "bundle", "estimates"))
    _uuid(document["request_id"], "$.request_id")
    _uuid(document["export_id"], "$.export_id")
    _label(document["policy_revision"], "$.policy_revision")
    bundle = _object(document["bundle"], "$.bundle", ("format", "schema", "bytes", "sha256"))
    _enum(bundle["format"], "$.bundle.format", (BUNDLE_FORMAT,))
    _enum(bundle["schema"], "$.bundle.schema", (BUNDLE,))
    _integer(bundle["bytes"], "$.bundle.bytes", 1, MAX_CIPHERTEXT)
    _pattern(bundle["sha256"], "$.bundle.sha256", SHA256)
    estimates = _object(document["estimates"], "$.estimates", ("expanded_bytes", "entries"))
    _integer(estimates["expanded_bytes"], "$.estimates.expanded_bytes", 0, MAX_EXPANDED)
    _integer(estimates["entries"], "$.estimates.entries", 0, MAX_ENTRIES)


def _progress(document):
    phase = document.get("phase") if isinstance(document, dict) else None
    extra = {"complete": ("receipt", "reused"), "failed": ("error",)}.get(phase, ())
    _object(document, "$", ("schema", "request_id", "sequence", "phase") + extra)
    _enum(phase, "$.phase", PHASES)
    _integer(document["sequence"], "$.sequence", 1)
    if document["request_id"] is None:
        # Only a request that could not be parsed has no identity to report.
        if phase != "failed":
            _fail("missing_value", "$.request_id")
    else:
        _uuid(document["request_id"], "$.request_id")
    if phase == "complete":
        _boolean(document["reused"], "$.reused")
        receipt = document["receipt"]
        if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT:
            _fail("unsupported_schema", "$.receipt.schema")
        try:
            _receipt(receipt)
        except ContractError as error:
            _fail(error.code, "$.receipt" + error.where[1:])
        if receipt["request_id"] != document["request_id"]:
            _fail("identity_mismatch", "$.receipt.request_id")
    if phase == "failed":
        _pattern(document["error"], "$.error", CODE)


def _plan(document):
    _object(document, "$", ("schema", "plan_id", "export_id", "bundle_sha256", "policy_revision",
                            "destination", "actions", "packages", "required_bytes"))
    _uuid(document["plan_id"], "$.plan_id")
    _uuid(document["export_id"], "$.export_id")
    _pattern(document["bundle_sha256"], "$.bundle_sha256", SHA256)
    _label(document["policy_revision"], "$.policy_revision")
    destination = _object(document["destination"], "$.destination", ("account_uid",))
    _integer(destination["account_uid"], "$.destination.account_uid", 1000, 2**31 - 1)
    actions = _object(document["actions"], "$.actions", ("create", "present", "replace", "conflict", "omit", "inert"))
    for name, value in actions.items():
        _integer(value, f"$.actions.{name}", 0, MAX_ENTRIES)
    packages = _object(document["packages"], "$.packages", ("reinstall", "manual"))
    for name, value in packages.items():
        _integer(value, f"$.packages.{name}", 0, MAX_ENTRIES)
    _integer(document["required_bytes"], "$.required_bytes", 0, MAX_EXPANDED)


def _report(document):
    _object(document, "$", ("schema", "job_id", "export_id", "plan_id", "categories"))
    _uuid(document["job_id"], "$.job_id")
    _uuid(document["export_id"], "$.export_id")
    _uuid(document["plan_id"], "$.plan_id")

    def category(value, where):
        _object(value, where, ("id", "outcome", "restored", "conflicts", "omitted", "reasons"))
        _label(value["id"], f"{where}.id")
        _enum(value["outcome"], f"{where}.outcome", CATEGORY_OUTCOMES)
        for name in ("restored", "conflicts", "omitted"):
            _integer(value[name], f"{where}.{name}", 0, MAX_ENTRIES)
        _list(value["reasons"], f"{where}.reasons", lambda v, w: _pattern(v, w, CODE))
        return value["id"]

    ids = _list(document["categories"], "$.categories", category, minimum=1)
    if len(set(ids)) != len(ids):
        _fail("duplicate_item", "$.categories")


def _policy(document):
    _object(document, "$", ("schema", "revision", "source", "credential_stores", "share_stores", "mounts", "rules"))
    _label(document["revision"], "$.revision")
    source = _object(document["source"], "$.source", ("provider", "repository", "commit"))
    _enum(source["provider"], "$.source.provider", PROVIDERS)
    _pattern(source["repository"], "$.source.repository", re.compile(r"https://[A-Za-z0-9./_-]+"), MAX_PATH)
    _pattern(source["commit"], "$.source.commit", COMMIT)
    roots = []

    def store(value, where):
        _object(value, where, ("id", "category", "roots", "adapter"))
        _label(value["id"], f"{where}.id")
        _enum(value["category"], f"{where}.category", STORE_CATEGORIES)
        for index, root in enumerate(_list(value["roots"], f"{where}.roots", home_path, minimum=1, maximum=32)):
            roots.append((root, f"{where}.roots[{index}]"))
        if value["adapter"] is not None:
            _label(value["adapter"], f"{where}.adapter")
        return value["id"]

    stores = _list(document["credential_stores"], "$.credential_stores", store, minimum=1, maximum=32)
    if len(set(stores)) != len(stores):
        _fail("duplicate_item", "$.credential_stores")

    def mount(value, where):
        _object(value, where, ("id", "path", "reason", "evidence"))
        _label(value["id"], f"{where}.id")
        path = value["path"]
        if not isinstance(path, str) or not path.startswith("/") or path == "/":
            _fail("unsafe_path", f"{where}.path")
        home_path(path[1:], f"{where}.path")
        _pattern(value["reason"], f"{where}.reason", CODE)
        _evidence(value["evidence"], f"{where}.evidence")
        return value["id"]

    def share_store(value, where):
        # Recognized by name inside selected shared folders; never by content.
        _object(value, where, ("id", "category", "directories", "files"))
        _label(value["id"], f"{where}.id")
        _enum(value["category"], f"{where}.category", STORE_CATEGORIES)

        def suffix(item, location):
            home_path(item, location)
            if item.count("/") >= 8:
                _fail("unsafe_path", location)
            return item

        def name(item, location):
            return _pattern(item, location, FILE_PATTERN)

        directories = _list(value["directories"], f"{where}.directories", suffix, maximum=32)
        files = _list(value["files"], f"{where}.files", name, maximum=32)
        if not directories and not files:
            _fail("missing_value", f"{where}.directories")
        return value["id"]

    share_stores = _list(document["share_stores"], "$.share_stores", share_store, maximum=32)
    if len(set(share_stores)) != len(share_stores) or set(share_stores) & set(stores):
        _fail("duplicate_item", "$.share_stores")
    mounts = _list(document["mounts"], "$.mounts", mount, maximum=32)
    if len(set(mounts)) != len(mounts):
        _fail("duplicate_item", "$.mounts")
    rule_paths = []

    def rule(value, where):
        action = value.get("action") if isinstance(value, dict) else None
        _object(value, where, ("id", "path", "match", "action", "reason", "evidence")
                + (("transform",) if action == "transform" else ()))
        _label(value["id"], f"{where}.id")
        rule_paths.append((home_path(value["path"], f"{where}.path"), f"{where}.path"))
        _enum(value["match"], f"{where}.match", MATCH_KINDS)
        _enum(action, f"{where}.action", RULE_ACTIONS)
        _pattern(value["reason"], f"{where}.reason", CODE)
        _evidence(value["evidence"], f"{where}.evidence")
        if action == "transform":
            _transform(value["transform"], value["match"], f"{where}.transform")
        return value["id"]

    ids = _list(document["rules"], "$.rules", rule, minimum=1)
    if len(set(ids)) != len(ids):
        _fail("duplicate_item", "$.rules")
    for index, (path, where) in enumerate(roots):
        for other, _ in roots[index + 1:]:
            if _overlaps(path, other):
                _fail("overlapping_paths", where)
    for path, where in rule_paths:
        for root, _ in roots:
            if _overlaps(path, root):
                _fail("overlapping_paths", where)
    seen = set()
    for path, where in rule_paths:
        if path in seen:
            _fail("duplicate_item", where)
        seen.add(path)
    # Nested rules would make the outcome depend on rule order.
    for index, (path, where) in enumerate(rule_paths):
        for other, _ in rule_paths[index + 1:]:
            if path != other and _overlaps(path, other):
                _fail("overlapping_paths", where)


def _evidence(value, where):
    _object(value, where, ("path",), ("sha256",))
    _pattern(value["path"], f"{where}.path", re.compile(r"[A-Za-z0-9][A-Za-z0-9./_-]*"), MAX_PATH)
    if ".." in value["path"].split("/"):
        _fail("unsafe_path", f"{where}.path")
    if "sha256" in value:
        _pattern(value["sha256"], f"{where}.sha256", SHA256)


def _block(block, where):
    if not isinstance(block, str) or not block.strip() or len(block.encode()) > 8192 or "\0" in block:
        _fail("invalid_string", where)
    return block


def _marker(value, where):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > MAX_LABEL or "\n" in value or "\0" in value:
        _fail("invalid_string", where)
    return value


def _object_item(value, where):
    if not isinstance(value, dict):
        _fail("not_an_object", where)
    return value


def _transform(value, match, where):
    kind = value.get("type") if isinstance(value, dict) else None
    fields = {
        "strip-appended-block": ("block",),
        "remove-json-keys": ("keys", "format"),
    }.get(kind, ())
    if not isinstance(value, dict):
        _fail("not_an_object", where)
    _enum(kind, f"{where}.type", TRANSFORMS)
    optional = ("earlier", "markers") if kind == "strip-appended-block" else ()
    _object(value, where, ("type",) + fields, optional)
    if match != "exact":
        _fail("inconsistent_fields", f"{where}.type")
    if kind == "strip-appended-block":
        blocks = [_block(value["block"], f"{where}.block")]
        # Older versions of the block a provider wrote, each with its source.
        for index, item in enumerate(_list(value.get("earlier", []), f"{where}.earlier", _object_item, maximum=16)):
            at = f"{where}.earlier[{index}]"
            _object(item, at, ("block", "commit", "path"))
            blocks.append(_block(item["block"], f"{at}.block"))
            _pattern(item["commit"], f"{at}.commit", COMMIT)
            home_path(item["path"], f"{at}.path")
        if "markers" in value:
            # Residue is any remaining line with a marker, so every block needs one.
            markers = _list(value["markers"], f"{where}.markers", _marker, minimum=1, maximum=16)
            for index, block in enumerate(blocks):
                if not any(marker in block for marker in markers):
                    _fail("inconsistent_fields", f"{where}.markers" if index == 0 else f"{where}.earlier[{index - 1}].block")
    else:
        _enum(value["format"], f"{where}.format", ("json", "jsonc"))
        _list(value["keys"], f"{where}.keys", _label, minimum=1, maximum=64)


VALIDATORS = {
    CAPABILITIES: _capabilities,
    INVENTORY: _inventory,
    EXPORT_REQUEST: _export_request,
    PROGRESS: _progress,
    RECEIPT: _receipt,
    PLAN: _plan,
    REPORT: _report,
    POLICY: _policy,
}


def validate(document):
    """Validate one parsed document and return its schema identifier."""
    if not isinstance(document, dict):
        _fail("not_an_object", "$")
    schema = document.get("schema")
    if schema not in VALIDATORS:
        _fail("unsupported_schema", "$.schema")
    VALIDATORS[schema](document)
    return schema


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_key", f"$.{key}")
        result[key] = value
    return result


def _reject_constant(value):
    _fail("invalid_json", "$")


def parse(data):
    """Parse UTF-8 JSON bytes strictly (no duplicate keys, NaN, or oversize) and validate."""
    if not isinstance(data, bytes):
        raise TypeError("parse() takes bytes")
    if len(data) > MAX_DOCUMENT:
        _fail("oversized_document", "$")
    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs,
                              parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("invalid_json", "$")
    schema = validate(document)
    if schema == EXPORT_REQUEST and len(data) > MAX_REQUEST:
        _fail("oversized_document", "$")
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate omarchy-migration contract documents.")
    parser.add_argument("command", choices=("validate",))
    parser.add_argument("documents", nargs="+")
    arguments = parser.parse_args(argv)
    status = 0
    for name in arguments.documents:
        result = {"document": name, "valid": True}
        try:
            with open(name, "rb") as stream:
                data = stream.read(MAX_DOCUMENT + 1)
            result["schema"] = parse(data)["schema"]
        except ContractError as error:
            result.update(valid=False, error=error.code, where=error.where)
            status = 1
        except OSError:
            result.update(valid=False, error="unreadable_document", where="$")
            status = 1
        print(json.dumps(result, sort_keys=True))
    return status


if __name__ == "__main__":
    sys.exit(main())
