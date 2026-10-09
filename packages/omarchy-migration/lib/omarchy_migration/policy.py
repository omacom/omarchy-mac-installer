"""Apply a validated omarchy-migration policy to home-relative source paths.

Pure functions over paths and bytes: classification, mount detection for link
targets, and the two data-only transforms. Callers own all file access.
"""

from collections import namedtuple
import fnmatch
import json
import re

from . import contract

# Transforms read whole files; policy targets are small configuration files.
MAX_TRANSFORM_INPUT = 1024 * 1024

Match = namedtuple("Match", "kind item")
Result = namedtuple("Result", "data status")
NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


class Policy:
    def __init__(self, document):
        if contract.validate(document) != contract.POLICY:
            raise contract.ContractError("unsupported_schema", "$.schema")
        self.document = json.loads(json.dumps(document))
        self.revision = document["revision"]
        self.stores = self.document["credential_stores"]
        self.share_stores = self.document["share_stores"]
        self.mounts = self.document["mounts"]
        self.rules = self.document["rules"]

    def match(self, path):
        """Return the store or rule governing a home-relative path, or None.

        Stores come first; validation guarantees no rule overlaps a store or
        another rule. Excluding a path also excludes anything beneath it, so
        selecting a descendant cannot reach excluded contents.
        """
        contract.home_path(path, "path")
        for store in self.stores:
            if any(_beneath(path, root) for root in store["roots"]):
                return Match("store", store)
        for rule in self.rules:
            covers_children = rule["match"] == "tree" or rule["action"] == "exclude"
            if path == rule["path"] or (covers_children and _beneath(path, rule["path"])):
                return Match("rule", rule)
        return None

    def share_store(self, path, directories_only=False):
        """The share store a path inside a shared folder names, or None.

        Directory patterns match as trailing path components at any depth;
        file patterns match the final name unless `directories_only`. Only
        names are examined.
        """
        parts = path.split("/")
        for store in self.share_stores:
            for pattern in store["directories"]:
                wanted = pattern.split("/")
                if parts[-len(wanted):] == wanted:
                    return store
            if not directories_only and any(fnmatch.fnmatchcase(parts[-1], pattern) for pattern in store["files"]):
                return store
        return None

    def mount(self, target):
        """Return the mount an absolute link target points into, or None.

        Targets with '..' are not resolved lexically and match no mount; as
        absolute links they are still recorded only as inert metadata.
        """
        if not isinstance(target, str) or not target.startswith("/"):
            return None
        parts = [part for part in target.split("/") if part not in ("", ".")]
        if ".." in parts:
            return None
        for mount in self.mounts:
            prefix = [part for part in mount["path"].split("/") if part]
            if parts[: len(prefix)] == prefix:
                return mount
        return None

    def transform(self, rule, data):
        """Apply a transform rule to file bytes and report what happened."""
        if rule["action"] != "transform":
            raise ValueError("rule has no transform")
        if len(data) > MAX_TRANSFORM_INPUT:
            return Result(None, "too-large")
        spec = rule["transform"]
        if spec["type"] == "strip-appended-block":
            blocks = [spec["block"].encode()] + [item["block"].encode() for item in spec.get("earlier", [])]
            markers = [marker.encode() for marker in spec["markers"]] if "markers" in spec else None
            return strip_appended_block(data, *blocks, markers=markers)
        return remove_json_keys(data, spec["keys"])


def _beneath(path, root):
    return path == root or path.startswith(root + "/")


def _block_lines(block):
    return {line.strip() for line in block.replace(b"\r\n", b"\n").split(b"\n") if line.strip()}


def strip_appended_block(data, *blocks, markers=None):
    """Remove one provider block that starts on a line boundary.

    `blocks` are every version the provider has written; the file holds at
    most one of them. A block may use LF or CRLF endings and may lack its
    final newline at the end of the file. No block leaves the file unchanged.
    More than one occurrence is ambiguous, and a line left after removal that
    carries a marker (or, without markers, repeats a block line) is residual;
    both return no data so the caller withholds the file.
    """
    variants = set()
    for block in blocks:
        variants |= {block, block.replace(b"\n", b"\r\n")}
    variants |= {variant[:-2] if variant.endswith(b"\r\n") else variant[:-1]
                 for variant in list(variants) if variant.endswith(b"\n")}
    found = set()
    for variant in variants:
        index = data.find(variant)
        while index != -1:
            boundary = variant[:1] in (b"\n", b"\r") or index == 0 or data[index - 1:index] == b"\n"
            ends = index + len(variant)
            whole = variant.endswith(b"\n") or ends == len(data)
            if boundary and whole:
                found.add((index, ends))
            index = data.find(variant, index + 1)
    # A shorter variant found inside a longer match is the same occurrence.
    spans = [span for span in found
             if not any(other != span and other[0] <= span[0] and span[1] <= other[1] for other in found)]
    if markers is None:
        lines = set().union(*(_block_lines(block) for block in blocks))
        leftover = lambda text: any(line.strip() in lines for line in text.split(b"\n"))
    else:
        leftover = lambda text: any(marker in line for line in text.split(b"\n") for marker in markers)
    if not spans:
        return Result(None, "residual") if leftover(data) else Result(data, "not-applicable")
    if len(spans) > 1:
        return Result(None, "ambiguous")
    start, end = spans[0]
    result = data[:start] + data[end:]
    if leftover(result):
        return Result(None, "residual")
    return Result(result, "applied")


class _Malformed(ValueError):
    pass


class _Scanner:
    """Locate the members of a top-level JSONC object without rewriting it."""

    def __init__(self, text):
        self.text = text
        self.position = 0

    def skip(self):
        text = self.text
        while self.position < len(text):
            char = text[self.position]
            if char in " \t\r\n":
                self.position += 1
            elif text.startswith("//", self.position):
                # CR alone also ends a line comment for common JSONC parsers.
                ends = [index for index in (text.find("\n", self.position), text.find("\r", self.position))
                        if index != -1]
                end = min(ends) if ends else len(text)
                if "\u2028" in text[self.position:end] or "\u2029" in text[self.position:end]:
                    raise _Malformed("ambiguous line separator in comment")
                self.position = end
            elif text.startswith("/*", self.position):
                end = text.find("*/", self.position + 2)
                if end == -1:
                    raise _Malformed("unterminated comment")
                self.position = end + 2
            else:
                return

    def expect(self, char):
        self.skip()
        if not self.text.startswith(char, self.position):
            raise _Malformed(f"expected {char}")
        self.position += 1

    def string(self):
        self.skip()
        start = self.position
        if not self.text.startswith('"', start):
            raise _Malformed("expected string")
        index = start + 1
        while index < len(self.text):
            char = self.text[index]
            if char == "\\":
                index += 2
                continue
            if char == '"':
                self.position = index + 1
                try:
                    return json.loads(self.text[start:self.position]), start
                except json.JSONDecodeError as error:
                    raise _Malformed("bad string") from error
            if char == "\n":
                break
            index += 1
        raise _Malformed("unterminated string")

    def value(self):
        self.skip()
        if self.position >= len(self.text):
            raise _Malformed("missing value")
        char = self.text[self.position]
        if char == '"':
            self.string()
        elif char in "{[":
            closing = "}" if char == "{" else "]"
            self.position += 1
            while True:
                self.skip()
                if self.text.startswith(closing, self.position):
                    self.position += 1
                    return
                if char == "{":
                    self.string()
                    self.expect(":")
                self.value()
                self.skip()
                if self.text.startswith(",", self.position):
                    self.position += 1
                elif not self.text.startswith(closing, self.position):
                    raise _Malformed("expected separator")
        else:
            start = self.position
            while self.position < len(self.text) and self.text[self.position] not in ",}] \t\r\n/":
                self.position += 1
            token = self.text[start:self.position]
            if token not in ("true", "false", "null") and not NUMBER.fullmatch(token):
                raise _Malformed("bad literal")

    def members(self):
        """Return (key, key_start, value_end, comma_or_None) for each member."""
        self.expect("{")
        members = []
        while True:
            self.skip()
            if self.text.startswith("}", self.position):
                self.position += 1
                break
            key, start = self.string()
            self.expect(":")
            self.value()
            end = self.position
            self.skip()
            comma = None
            if self.text.startswith(",", self.position):
                comma = self.position
                self.position += 1
            elif not self.text.startswith("}", self.position):
                raise _Malformed("expected separator")
            members.append((key, start, end, comma))
        self.skip()
        if self.position != len(self.text):
            raise _Malformed("trailing content")
        if len({member[0] for member in members}) != len(members):
            raise _Malformed("duplicate key")
        return members


def _line_start(text, index):
    start = text.rfind("\n", 0, index) + 1
    return start if not text[start:index].strip() else index


def _line_end(text, index):
    end = text.find("\n", index)
    end = len(text) if end == -1 else end
    return end + 1 if not text[index:end].strip() and end < len(text) else index


def _remove_member(text, members, index):
    key, start, end, comma = members[index]
    if comma is not None:
        return text[:_line_start(text, start)] + text[_line_end(text, comma + 1):]
    text = text[:_line_start(text, start)] + text[_line_end(text, end):]
    if index > 0 and members[index - 1][3] is not None:
        # Drop only the comma that would now trail the new last member,
        # keeping any comments between the members.
        previous = members[index - 1][3]
        text = text[:previous] + text[previous + 1:]
    return text


def remove_json_keys(data, keys):
    """Remove named top-level members from a JSON or JSONC object.

    Everything else, including comments, stays byte for byte. Input that is
    not a single well-formed object with unique keys is reported as failed.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Result(None, "failed")
    removed = False
    try:
        for key in keys:
            members = _Scanner(text).members()
            names = [member[0] for member in members]
            if key in names:
                text = _remove_member(text, members, names.index(key))
                removed = True
        remaining = [member[0] for member in _Scanner(text).members()]
    except _Malformed:
        return Result(None, "failed")
    if set(keys) & set(remaining):
        return Result(None, "failed")
    return Result(text.encode("utf-8"), "applied" if removed else "not-applicable")
