#!/usr/bin/env python3
"""Build, gate, sign and publish an Omarchy Mac channel catalog.

The Mac catalog workflow (.github/workflows/mac-catalog.yml) runs each stage
as its own job, so the job that holds the catalog key does nothing but sign:

  prepare         validate the inputs, refuse an image without a passing VM
                  evidence record for its exact sha256, optionally check that
                  every artifact URL serves the pinned bytes, and write the
                  unsigned catalog-<channel>.json
  check-evidence  re-check a built catalog's payload digest against the
                  evidence record (the signing job runs it again)
  check-engine    refuse an rc or stable catalog whose engine is not the one
                  Engine/source-lock.json pins, unless the owner allows that
                  digest (edge is exempt; the signing job runs it)
  sign          sign a catalog with the Ed25519 key in an environment
                  variable or file, wrap it in the channel envelope and verify
                  the result against the trust root
  verify-envelope verify a signed envelope against the trust root
  verify          `verify PUBLIC_KEY CATALOG SIGNATURE`, the portable form of
                  `catalog-signing.swift verify`
  promote         build an rc or stable catalog from a published (or named)
                  channel's signed catalog: verify its signature, keep every
                  pinned artifact and digest, bump only the sequence. Real
                  promotion is parked and needs --real --confirm
  check-promotion re-check a promoted catalog against its signed source (the
                  signing job runs it again)
  publish         publish a verified envelope to the catalog host, refusing a
                  sequence that does not move forward
  public-key      write the public key of a signing key (a setup check)

Signing uses OpenSSL 3 (`pkeyutl -rawin`), so it runs on a Linux or macOS
runner. The signature is the same raw 64-byte Ed25519 signature that
`catalog-signing.swift sign-keychain` makes, and the envelope is the one
`publish-channels envelope` writes; `catalog-signing.swift verify` and the
app's verifier accept both.

The key is the 32 raw Ed25519 private-key bytes (`catalog-signing.swift
generate` writes them), carried in an environment variable as base64. It goes
to OpenSSL on stdin, never on a command line or on disk, and is dropped from
the environment before any child process starts.

The input, evidence and host formats are documented in
docs/catalog-release.md.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import datetime
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import supported_models  # noqa: E402
import r2_s3  # noqa: E402

IDENTITY = ROOT / "Packaging" / "identity.conf"
DEFAULT_TRUST_ROOT = ROOT / "Release" / "trust-root.ed25519.pub"
SOURCE_LOCK = ROOT / "Engine" / "source-lock.json"
PUBLISH_CHANNELS = ROOT / "scripts" / "publish-channels"

SCHEMA_VERSION = 4
# Channels a catalog is built for from an image and its VM evidence: edge from
# an edge image, rc and stable from an image of that lane (built from the
# channel for a release tag, image-builder/bin/mac-image-check name). Promotion
# (parked) re-signs a published catalog's exact pins instead.
CHANNELS = ("edge", "rc", "stable")
RELEASE_CHANNELS = ("rc", "stable")
PROMOTED_CHANNELS = ("rc", "stable")
SIGNED_CHANNELS = ("edge", "rc", "stable")
# A catalog is promoted only forward along this order: edge to rc or
# stable, rc to stable.
CHANNEL_ORDER = ("edge", "rc", "stable")
# The only catalog keys a promotion changes outright. `channel` is among them
# because a promoted catalog is served at the target channel's URL and must say
# so; its new value is checked against the target, not left free. `models` is
# compared on its own: a promotion may drop a Mac the target has not qualified
# for, but never change what a surviving one pins.
PROMOTION_FIELDS = ("sequence", "issuedAt", "channel")
PROMOTION_COMPARED_SEPARATELY = ("models",)
# The harness's fresh-install scenarios (omarchy-mac-pkgs
# tools/acceptance/mac-image): every one must pass for the image's sha256.
REQUIRED_SCENARIOS = ("first-boot", "conversion", "second-boot")
SCENARIO_RESULTS = ("pass", "fail", "skip")
MAXIMUM_PART_COUNT = 16
MAXIMUM_CATALOG_BYTES = 1_048_576

SHA256 = re.compile(r"^[0-9a-f]{64}$")
ENGINE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REVISION = re.compile(r"^[0-9a-f]{40}$")
ASAHI_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
ENGINE_VERSION = re.compile(r"^v[0-9A-Za-z+._-]{1,127}$")
INSTALLER_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
# An image name (image-builder/bin/mac-image-check name):
# omarchy-mac-<channel>-<YYYY.MM.DD>-<package set id>-<image id>, the image id
# being the first 12 hex digits of the payload's sha256. Images built before
# the image id was added (their releases, and the catalogs that pin them, are
# live) are named without it, and a catalog may still be prepared for one.
IMAGE_NAME = re.compile(r"^omarchy-mac-([a-z]+)-[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9a-f]{12}(?:-([0-9a-f]{12}))?$")
# An rc or stable image is named from its release tag (without the v) and Mac
# revision; its payload's sha256 stays its identity, and a respin takes the
# next revision rather than replacing a published release.
_RELEASE_VERSION = r"^omarchy-mac-(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
_RELEASE_SUFFIX = r"-r[1-9][0-9]{0,2}-[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9a-f]{12}$"
RELEASE_IMAGE_NAMES = {
    # A bare -rc is a final tag built on the rc lane (channel_set.release_image_label).
    "rc": re.compile(_RELEASE_VERSION + r"-rc(?:[1-9][0-9]*)?" + _RELEASE_SUFFIX),
    "stable": re.compile(_RELEASE_VERSION + _RELEASE_SUFFIX),
}
FINAL_TAG_ON_RC = re.compile(_RELEASE_VERSION + r"-rc" + _RELEASE_SUFFIX)
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,254}$")

# PKCS#8 and SubjectPublicKeyInfo prefixes of an Ed25519 key (RFC 8410).
PRIVATE_KEY_DER_PREFIX = bytes.fromhex("302e020100300506032b657004220420")
PUBLIC_KEY_DER_PREFIX = bytes.fromhex("302a300506032b6570032100")


class Refusal(Exception):
    """An input, evidence record or host state the release must not accept."""


# ── identity and inputs ──────────────────────────────────────────────────────


def identity() -> dict[str, str]:
    values = {}
    for line in IDENTITY.read_text().splitlines():
        match = re.fullmatch(r'(INSTALLER_[A-Z0-9_]+)="([^"]*)"', line.strip())
        if match:
            values[match.group(1)] = match.group(2)
    return values


def stopgap_repo() -> str:
    repo = identity()["INSTALLER_CATALOG_STOPGAP_REPO"]
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", repo):
        raise Refusal(f"invalid stopgap repository: {repo}")
    return repo


def artifact_bases() -> list[str]:
    """Where a catalog may point: immutable release paths only."""
    catalog_base = identity()["INSTALLER_CATALOG_BASE"]
    return [
        f"{catalog_base}/releases/",
        f"{catalog_base}/engine/",
        f"https://github.com/{stopgap_repo()}/releases/download/",
    ]


def installer_bases() -> list[str]:
    catalog_base = identity()["INSTALLER_CATALOG_BASE"]
    return [
        f"{catalog_base}/installer/",
        f"https://github.com/{stopgap_repo()}/releases/download/",
    ]


def check_url(url: object, field: str, bases: list[str], file_name: str | None = None) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise Refusal(f"{field} must be a URL string")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise Refusal(f"{field} must be https: {url}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or "%" in url:
        raise Refusal(f"{field} must be a plain https URL without credentials, query or escapes: {url}")
    segments = parsed.path.split("/")
    if any(segment in ("", ".", "..") for segment in segments[1:]):
        raise Refusal(f"{field} has an empty or relative path segment: {url}")
    if not any(url.startswith(base) for base in bases):
        raise Refusal(f"{field} is not under an allowed release location ({', '.join(bases)}): {url}")
    github_prefix = f"https://github.com/{stopgap_repo()}/releases/download/"
    if url.startswith(github_prefix):
        rest = url[len(github_prefix):].split("/")
        # Catalog tags are the only mutable releases; nothing a catalog pins
        # may live there, and an asset sits directly under its tag.
        if len(rest) != 2 or rest[0].startswith("catalog-"):
            raise Refusal(f"{field} must name an asset of an immutable release tag: {url}")
    if file_name is not None and segments[-1] != file_name:
        raise Refusal(f"{field} must end with its fileName {file_name}: {url}")
    return url


def check_keys(document: object, field: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(document, dict):
        raise Refusal(f"{field} must be a JSON object")
    keys = set(document)
    missing = required - keys
    if missing:
        raise Refusal(f"{field} is missing {', '.join(sorted(missing))}")
    unknown = keys - required - optional
    if unknown:
        raise Refusal(f"{field} has unknown keys {', '.join(sorted(unknown))}")
    return document


def check_artifact(document: object, field: str, allow_parts: bool = False) -> dict:
    optional = {"parts"} if allow_parts else set()
    document = check_keys(document, field, {"url", "fileName", "sizeBytes", "sha256"}, optional)
    name = document["fileName"]
    if not isinstance(name, str) or not FILE_NAME.match(name):
        raise Refusal(f"{field}.fileName must be a plain file name: {name}")
    size = document["sizeBytes"]
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise Refusal(f"{field}.sizeBytes must be a positive integer")
    if not isinstance(document["sha256"], str) or not SHA256.match(document["sha256"]):
        raise Refusal(f"{field}.sha256 must be 64 lowercase hex digits")
    check_url(document["url"], f"{field}.url", artifact_bases(), name)
    if "parts" in document:
        parts = document["parts"]
        if not isinstance(parts, list) or not 2 <= len(parts) <= MAXIMUM_PART_COUNT:
            raise Refusal(f"{field}.parts must list 2 to {MAXIMUM_PART_COUNT} parts")
        names = set()
        total = 0
        for index, part in enumerate(parts):
            part = check_artifact(part, f"{field}.parts[{index}]")
            if part["fileName"] == name or part["fileName"] in names:
                raise Refusal(f"{field}.parts[{index}].fileName must be distinct: {part['fileName']}")
            names.add(part["fileName"])
            total += part["sizeBytes"]
        if total != size:
            raise Refusal(f"{field}.parts sum to {total} bytes but sizeBytes is {size}")
    return document


def check_engine(document: object) -> dict:
    fields = {"version", "downstreamRevision"}
    asahi = {"asahiInstallerTag", "asahiInstallerRevision", "asahiInstallerDataRevision"}
    document = check_keys(document, "engine", {"url", "fileName", "sizeBytes", "sha256"} | fields, asahi)
    artifact = check_artifact({key: document[key] for key in ("url", "fileName", "sizeBytes", "sha256")}, "engine")
    if not isinstance(document["version"], str) or not ENGINE_VERSION.match(document["version"]):
        raise Refusal(f"engine.version is not an engine version: {document['version']}")
    lock = json.loads(SOURCE_LOCK.read_text())
    resolved = {
        "version": document["version"],
        "downstreamRevision": document["downstreamRevision"],
        "asahiInstallerTag": document.get("asahiInstallerTag", lock["upstream_installer"]["tag"]),
        "asahiInstallerRevision": document.get("asahiInstallerRevision", lock["upstream_installer"]["commit"]),
        "asahiInstallerDataRevision": document.get(
            "asahiInstallerDataRevision", lock["upstream_metadata"]["commit"]
        ),
        "artifact": artifact,
    }
    if not isinstance(resolved["asahiInstallerTag"], str) or not ASAHI_TAG.match(resolved["asahiInstallerTag"]):
        raise Refusal("engine.asahiInstallerTag must look like vX.Y.Z")
    for key in ("downstreamRevision", "asahiInstallerRevision", "asahiInstallerDataRevision"):
        if not isinstance(resolved[key], str) or not REVISION.match(resolved[key]):
            raise Refusal(f"engine.{key} must be a 40-character lowercase hex revision")
    return resolved


def pipeline_engine(document: object) -> dict:
    """The engine block for .github/mac-release-pipeline.json's run: the pin
    from Engine/source-lock.json, the only place the engine is pinned, plus the
    config's downstreamRevision. The archive is served from the catalog base's
    engine/<version>/, where mac-mirror.yml copies it from its published
    engine-<version> release; --verify-remote refuses a catalog whose engine
    was never mirrored."""
    document = check_keys(document, "engine", {"downstreamRevision"})
    lock = json.loads(SOURCE_LOCK.read_text())
    version = lock["downstream_overlay"]["version"]
    artifact = lock["validation_artifact"]
    engine = {
        "version": version,
        "url": f"{identity()['INSTALLER_CATALOG_BASE']}/engine/{version}/{artifact['filename']}",
        "fileName": artifact["filename"],
        "sizeBytes": artifact["size_bytes"],
        "sha256": artifact["sha256"],
        "downstreamRevision": document["downstreamRevision"],
    }
    check_engine(engine)
    return engine


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def engine_app_floor(engine: dict | None) -> tuple[int, ...]:
    """The oldest app ENGINE runs under. Engine/source-lock.json records it for
    the engine it pins (downstream_overlay.minimum_app_version), matched by the
    archive's digest, not its name: engines from that pin on carry no decoder
    and run the app's signed Contents/Helpers copy, which older apps lack.
    Every other engine needs app 2.1.0, the floor of every catalog."""
    lock = json.loads(SOURCE_LOCK.read_text())
    # A raw engine block carries its digest at the top; check_engine moves it under artifact.
    digest = None if engine is None else engine.get("sha256", engine.get("artifact", {}).get("sha256"))
    if digest == lock["validation_artifact"]["sha256"]:
        return version_tuple(lock["downstream_overlay"].get("minimum_app_version", "2.1.0"))
    return (2, 1, 0)


def check_installer(document: object, engine: dict | None = None) -> dict:
    document = check_keys(document, "installer", {"minimumVersion", "latestVersion", "downloadURL"})
    for key in ("minimumVersion", "latestVersion"):
        if not isinstance(document[key], str) or not INSTALLER_VERSION.match(document[key]):
            raise Refusal(f"installer.{key} must look like X.Y.Z")
    if version_tuple(document["minimumVersion"]) > version_tuple(document["latestVersion"]):
        raise Refusal("installer.minimumVersion is newer than installer.latestVersion")
    # Every engine since .18 sends recommendation fields that only app 2.1.0
    # and later decode, so every catalog turns older apps away.
    if version_tuple(document["minimumVersion"]) < (2, 1, 0):
        raise Refusal("every catalog needs app 2.1.0 or later: installer.minimumVersion must be at least 2.1.0")
    floor = engine_app_floor(engine)
    if version_tuple(document["minimumVersion"]) < floor:
        wanted = ".".join(map(str, floor))
        raise Refusal(f"this engine needs app {wanted} or later: installer.minimumVersion must be at least {wanted}")
    check_url(document["downloadURL"], "installer.downloadURL", installer_bases())
    return document


def check_app_floor(installer: dict, channel: str) -> None:
    """Refuse, before anything is uploaded, an app floor below what a Mac
    CHANNEL offers needs (supported_models.catalog_errors refuses the catalog
    itself later)."""
    errors = supported_models.app_minimum_errors(
        installer["minimumVersion"], supported_models.boards_for(channel))
    if errors:
        raise Refusal("; ".join(errors))


def check_image_name(name: object, channel: str, payload_sha256: str | None = None) -> str:
    """NAME, an image built for CHANNEL. A name with an image id must name
    PAYLOAD_SHA256 when given; an older name without one is accepted as is.
    An rc or stable catalog takes only an image of that lane, named from its release tag."""
    if channel in RELEASE_CHANNELS:
        if not isinstance(name, str) or RELEASE_IMAGE_NAMES[channel].fullmatch(name) is None:
            shape = "X.Y.Z-rc[N]" if channel == "rc" else "X.Y.Z"
            raise Refusal(f"image must be omarchy-mac-<{shape}>-r<revision>-<YYYY.MM.DD>-<12 hex package set id>,"
                          f" not built for the {channel} lane: {name}")
        return name
    match = IMAGE_NAME.fullmatch(name) if isinstance(name, str) else None
    if match is None:
        raise Refusal("image must be omarchy-mac-<channel>-<YYYY.MM.DD>-<12 hex package set id>-<12 hex image id>"
                      f" (or, for an image built before image ids, without the image id): {name}")
    if match.group(1) != channel:
        raise Refusal(f"image {name} was not built for the {channel} channel")
    if match.group(2) is not None and payload_sha256 is not None and match.group(2) != payload_sha256[:12]:
        raise Refusal(f"image {name} names image id {match.group(2)}, not the payload's sha256 {payload_sha256}")
    return name


def check_release_image(image: str, channel: str, payload: dict, metadata: dict, evidence: dict) -> None:
    """catalog-rc and catalog-stable take only the image of that lane that
    passed VM acceptance. Such a name carries no image id, so it is tied to the
    image three ways: the evidence records that image
    (mac-acceptance-record.py --image, the build's artifact name), the payload
    is that image's zip for the lane (the lane and date mac-image.yml stages),
    and every artifact is in the image's own release (as mac-upload.py publishes
    it). A payload of another lane and its evidence under this name fail all three."""
    recorded = evidence.get("image")
    if recorded is None:
        raise Refusal(f"the VM evidence names no image: catalog-{channel} takes only the {channel} image {image} that passed acceptance")
    if recorded != image:
        raise Refusal(f"the VM evidence is for {recorded}, not {image}")
    date = re.search(r"-r[1-9][0-9]{0,2}-([0-9]{4}\.[0-9]{2}\.[0-9]{2})-[0-9a-f]{12}$", image).group(1)
    expected = f"omarchy-{date}-aarch64-apple-silicon-mac-{channel}-os-package.zip"
    if payload["fileName"] != expected:
        raise Refusal(f"the payload {payload['fileName']} is not {image}'s {channel} zip ({expected})")
    urls = [("payload", payload["url"]), *(("payload part", part["url"]) for part in payload.get("parts", [])),
            ("installer_data", metadata["url"])]
    for field, url in urls:
        if urllib.parse.urlsplit(url).path.split("/")[-2] != image:
            raise Refusal(f"{field} {url} is not in {image}'s release")


def check_channel(channel: str) -> str:
    if channel not in CHANNELS:
        raise Refusal(f"channel must be one of {', '.join(CHANNELS)} {channel}")
    return channel


def check_signed_channel(channel: str) -> str:
    if channel not in SIGNED_CHANNELS:
        raise Refusal(f"channel must be one of {', '.join(SIGNED_CHANNELS)}: {channel}")
    return channel


def check_promotion_channels(source: str, channel: str) -> tuple[str, str]:
    if channel not in PROMOTED_CHANNELS:
        raise Refusal(f"a promotion writes {' or '.join(PROMOTED_CHANNELS)}, not {channel}")
    if source not in CHANNEL_ORDER or CHANNEL_ORDER.index(source) >= CHANNEL_ORDER.index(channel):
        raise Refusal(f"{channel} cannot be promoted from {source}: promotion goes edge to rc or stable, rc to stable")
    return source, channel


def check_evidence(document: object, image_sha256: str) -> dict:
    """Refuse unless the VM evidence record passed for exactly IMAGE_SHA256."""
    if document is None:
        raise Refusal("no VM evidence record: an image is published only after it passes VM acceptance")
    if not isinstance(document, dict):
        raise Refusal("the VM evidence record must be a JSON object")
    # Extra keys are allowed so the acceptance job can record more; these
    # are the ones a publish depends on.
    for key in ("image_sha256", "result", "scenarios", "harness_commit", "runner", "run_url"):
        if key not in document:
            raise Refusal(f"the VM evidence record has no {key}")
    recorded = document["image_sha256"]
    if not isinstance(recorded, str) or not SHA256.match(recorded):
        raise Refusal("the VM evidence record's image_sha256 must be 64 lowercase hex digits")
    if recorded != image_sha256:
        raise Refusal(f"the VM evidence is for image sha256 {recorded}, not {image_sha256}")
    if document["result"] != "pass":
        raise Refusal(f"the VM evidence for {image_sha256} did not pass (result: {document['result']!r})")
    scenarios = document["scenarios"]
    if not isinstance(scenarios, list) or not scenarios:
        raise Refusal("the VM evidence record lists no scenarios")
    results = {}
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict) or not isinstance(scenario.get("name"), str):
            raise Refusal(f"VM evidence scenarios[{index}] must be an object with a name")
        if scenario.get("result") not in SCENARIO_RESULTS:
            raise Refusal(f"VM evidence scenario {scenario['name']} has no pass, fail or skip result")
        if scenario["name"] in results:
            raise Refusal(f"VM evidence lists scenario {scenario['name']} twice")
        results[scenario["name"]] = scenario["result"]
    failed = [name for name, result in results.items() if result == "fail"]
    if failed:
        raise Refusal(f"the VM evidence says pass but scenarios failed: {', '.join(failed)}")
    for name in REQUIRED_SCENARIOS:
        if results.get(name) != "pass":
            raise Refusal(f"the VM evidence has no passing {name} scenario")
    if not isinstance(document["harness_commit"], str) or not REVISION.match(document["harness_commit"]):
        raise Refusal("the VM evidence record's harness_commit must be a 40-character hex revision")
    if not isinstance(document["runner"], str) or not 0 < len(document["runner"]) <= 200:
        raise Refusal("the VM evidence record must name its runner")
    run_url = document["run_url"]
    if not isinstance(run_url, str) or urllib.parse.urlsplit(run_url).scheme != "https":
        raise Refusal("the VM evidence record's run_url must be https")
    return document


def load_json(path: Path | None, field: str) -> object:
    if path is None:
        return None
    if not path.is_file() or path.is_symlink():
        raise Refusal(f"{field}: missing or unsafe file {path}")
    try:
        return json.loads(path.read_text())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refusal(f"{field} is not valid JSON: {error}") from error


# ── catalog ──────────────────────────────────────────────────────────────────


def build_catalog(
    channel: str,
    image: str,
    payload: dict,
    engine: dict,
    metadata: dict,
    evidence: object,
    installer: dict,
    now: datetime.datetime,
) -> dict:
    check_channel(channel)
    check_image_name(image, channel)
    payload = check_artifact(payload, "payload", allow_parts=True)
    check_image_name(image, channel, payload["sha256"])
    metadata = check_artifact(metadata, "installer_data")
    engine = check_engine(engine)
    check_evidence(evidence, payload["sha256"])
    if channel in RELEASE_CHANNELS:
        check_release_image(image, channel, payload, metadata, evidence)
    installer = check_installer(installer, engine)

    def plain(artifact: dict) -> dict:
        return {"sourceURL": artifact["url"], "fileName": artifact["fileName"], "sizeBytes": artifact["sizeBytes"]}

    payload_artifact = plain(payload)
    if "parts" in payload:
        payload_artifact["parts"] = [
            dict(plain(part), sha256=f"sha256:{part['sha256']}") for part in payload["parts"]
        ]
    offered = supported_models.boards_for(channel)
    notes = supported_models.notes()
    models = []
    for identifier in offered:
        model = {
            "deviceIdentifier": identifier,
            "status": "enabled",
            "asahiInstallerTag": engine["asahiInstallerTag"],
            "asahiInstallerRevision": engine["asahiInstallerRevision"],
            "asahiInstallerDataRevision": engine["asahiInstallerDataRevision"],
            "downstreamRevision": engine["downstreamRevision"],
            "engineVersion": engine["version"],
            "engineDigest": f"sha256:{engine['artifact']['sha256']}",
            "metadataDigest": f"sha256:{metadata['sha256']}",
            "payloadDigest": f"sha256:{payload['sha256']}",
            "evidenceRevision": image,
            "engineArtifact": plain(engine["artifact"]),
            "metadataArtifact": plain(metadata),
            "payloadArtifact": payload_artifact,
        }
        if identifier in notes:
            # The app ignores keys it does not know; ticket 12 shows this.
            model["previewNote"] = notes[identifier]
        models.append(model)
    catalog = {
        "schemaVersion": SCHEMA_VERSION,
        # The channel this catalog is signed for. The app checks it against the
        # channel URL it fetched from, so an envelope published to the wrong
        # channel is refused rather than installed.
        "channel": channel,
        "sequence": int(now.timestamp()),
        "issuedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    catalog["installer"] = installer
    catalog["models"] = models
    errors = supported_models.catalog_errors(catalog)
    if errors:
        raise Refusal("; ".join(errors))
    return catalog


def catalog_engine_digest(catalog: dict) -> str:
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list) or not models:
        raise Refusal("the catalog lists no models")
    digests = [model.get("engineDigest") if isinstance(model, dict) else None for model in models]
    for digest in digests:
        if not isinstance(digest, str) or not ENGINE_DIGEST.fullmatch(digest):
            raise Refusal(f"the catalog has a model whose engine digest is missing or malformed: {digest!r}")
    if len(set(digests)) != 1:
        raise Refusal("the catalog's models pin different engines; a catalog pins exactly one engine")
    return digests[0]


def check_engine_pin(channel: str, catalog: dict, allowed: str | None) -> str:
    """An rc or stable catalog runs the engine Engine/source-lock.json pins
    (on main, in the signing job): the app installs with the catalog's engine,
    not the one it bundles, so a catalog left on an older engine ships that
    engine's bugs with every new app. ALLOWED, the owner's explicit
    allow-engine-digest, admits one other digest: a rollback or a recorded
    lane hold (docs/catalog-release.md, "Lane holds"). Edge is exempt."""
    channel = check_signed_channel(channel)
    if allowed and not ENGINE_DIGEST.fullmatch(allowed):
        raise Refusal(f"allow-engine-digest must be sha256:<64 lowercase hex>, not {allowed}")
    if channel not in RELEASE_CHANNELS:
        if allowed:
            raise Refusal(f"allow-engine-digest is for rc and stable; catalog-{channel} is not engine-gated")
        return f"engine_gate=exempt channel={channel} engine={catalog_engine_digest(catalog)}"
    digest = catalog_engine_digest(catalog)
    try:
        locked = json.loads(SOURCE_LOCK.read_text())["validation_artifact"]["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        locked = None
    if not isinstance(locked, str) or not SHA256.fullmatch(locked):
        raise Refusal(f"{SOURCE_LOCK.name} pins no readable validation_artifact.sha256")
    pinned = f"sha256:{locked}"
    if allowed:
        if digest != allowed:
            raise Refusal(f"catalog-{channel} pins engine {digest}, not the allowed {allowed}")
        status = "held" if digest != pinned else "lock"
        return f"engine_gate={status} channel={channel} engine={digest} lock={pinned} allowed={allowed}"
    if digest != pinned:
        raise Refusal(
            f"catalog-{channel} pins engine {digest}, but Engine/source-lock.json pins {pinned}: regenerate the "
            f"catalog with the locked engine, or, to keep this engine for an owner-approved rollback or lane hold "
            f"(docs/catalog-release.md, \"Lane holds\"), pass allow-engine-digest={digest}"
        )
    return f"engine_gate=lock channel={channel} engine={digest} lock={pinned}"


def catalog_bytes(catalog: dict) -> bytes:
    data = (json.dumps(catalog, indent=2) + "\n").encode()
    if len(data) > MAXIMUM_CATALOG_BYTES:
        raise Refusal(f"the catalog is {len(data)} bytes; the app reads at most {MAXIMUM_CATALOG_BYTES}")
    return data


def catalog_payload_digest(catalog: dict) -> str:
    digests = {model.get("payloadDigest") for model in catalog.get("models", [])}
    if len(digests) != 1:
        raise Refusal("the catalog must pin exactly one payload digest")
    digest = digests.pop()
    if not isinstance(digest, str) or not digest.startswith("sha256:") or not SHA256.match(digest[7:]):
        raise Refusal(f"the catalog's payload digest is malformed: {digest}")
    return digest[7:]


# ── promotion ────────────────────────────────────────────────────────────────


def promotion_artifacts(catalog: dict) -> list[tuple[str, dict]]:
    """The payload, engine and installer_data a signed catalog pins, as
    check_artifact documents, refusing a catalog whose models disagree on
    them or point outside the immutable release locations."""
    models = catalog.get("models")
    if not isinstance(models, list) or not models:
        raise Refusal("the source catalog lists no models")
    pins = set()
    for model in models:
        if not isinstance(model, dict):
            raise Refusal("the source catalog has a model that is not an object")
        try:
            pins.add(json.dumps({key: model[key] for key in (
                "payloadDigest", "engineDigest", "metadataDigest",
                "payloadArtifact", "engineArtifact", "metadataArtifact",
            )}, sort_keys=True))
        except KeyError as error:
            raise Refusal(f"model {model.get('deviceIdentifier')} pins no {error.args[0]}") from None
    if len(pins) != 1:
        raise Refusal("the source catalog's models pin different artifacts; a promotion needs exactly one image")
    pin = json.loads(pins.pop())
    return [
        ("payload", check_artifact(
            from_catalog("payload", pin["payloadArtifact"], pin["payloadDigest"], True), "payload", True)),
        ("engine", check_artifact(from_catalog("engine", pin["engineArtifact"], pin["engineDigest"]), "engine")),
        ("installer_data", check_artifact(
            from_catalog("installer_data", pin["metadataArtifact"], pin["metadataDigest"]), "installer_data")),
    ]


def from_catalog(field: str, artifact: object, digest: object, allow_parts: bool = False) -> dict:
    """A catalog artifact ({sourceURL, fileName, sizeBytes, parts?}) and its
    sha256:<hex> digest as the input document check_artifact takes."""
    if not isinstance(artifact, dict) or not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise Refusal(f"the source catalog's {field} pin is malformed")
    unknown = set(artifact) - {"sourceURL", "fileName", "sizeBytes"} - ({"parts"} if allow_parts else set())
    if unknown:
        raise Refusal(f"the source catalog's {field} has unknown keys {', '.join(sorted(unknown))}")
    document = {"url": artifact.get("sourceURL"), "fileName": artifact.get("fileName"),
                "sizeBytes": artifact.get("sizeBytes"), "sha256": digest[7:]}
    if "parts" in artifact:
        parts = artifact["parts"]
        if not isinstance(parts, list) or not all(isinstance(part, dict) for part in parts):
            raise Refusal(f"the source catalog's {field}.parts must be a list of objects")
        document["parts"] = [
            from_catalog(f"{field}.parts[{index}]", {k: v for k, v in part.items() if k != "sha256"},
                         part.get("sha256"))
            for index, part in enumerate(parts)
        ]
    return document


def check_promotion_source(source: dict) -> None:
    """An image of a final tag built on the rc lane stays on rc: stable builds its own."""
    for model in source.get("models", []):
        image = model.get("evidenceRevision")
        if isinstance(image, str) and FINAL_TAG_ON_RC.fullmatch(image):
            raise Refusal(f"{image} is a final tag built on the rc lane: it is never promoted")


def promote_catalog(source: dict, now: datetime.datetime, channel: str,
                    source_channel: str | None = None) -> dict:
    """SOURCE with its sequence, issue time and channel replaced and any Mac
    the target has not qualified for dropped: every surviving model, pin,
    digest and the installer block keep exactly the source's values. CHANNEL is
    the destination; SOURCE_CHANNEL judges the source's own coverage, for a
    catalog published before catalogs named their channel."""
    check_channel(channel)
    if source.get("schemaVersion") != SCHEMA_VERSION:
        raise Refusal("the source catalog is not a schema 4 catalog")
    check_promotion_source(source)
    promotion_artifacts(source)
    errors = supported_models.catalog_errors(source, channel=source_channel)
    if errors:
        raise Refusal("the source catalog does not cover its own channel: " + "; ".join(errors))
    sequence = int(now.timestamp())
    if sequence <= source["sequence"]:
        raise Refusal(f"sequence {sequence} does not follow the source catalog's {source['sequence']}")
    promoted = json.loads(json.dumps(source))
    promoted["sequence"] = sequence
    promoted["issuedAt"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    promoted["channel"] = channel
    offered = set(supported_models.boards_for(channel))
    promoted["models"] = [
        model for model in promoted["models"] if model.get("deviceIdentifier") in offered
    ]
    check_same_pins(source, promoted, channel)
    return promoted


def check_same_pins(source: dict, promoted: dict, channel: str) -> None:
    """Refuse unless PROMOTED differs from SOURCE only in its sequence, issue
    time and channel, carries exactly CHANNEL's Macs with every surviving model
    unchanged, and names a CHANNEL reached forward along CHANNEL_ORDER from the
    source's."""
    skipped = PROMOTION_FIELDS + PROMOTION_COMPARED_SEPARATELY

    def rest(document: dict) -> dict:
        return {key: value for key, value in document.items() if key not in skipped}

    if not isinstance(promoted, dict):
        raise Refusal("the promoted catalog is not a JSON object")
    before, after = rest(source), rest(promoted)
    if before != after:
        changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
        allowed = f"{', '.join(PROMOTION_FIELDS[:-1])} and {PROMOTION_FIELDS[-1]}"
        raise Refusal(f"the promoted catalog changes more than {allowed}: {', '.join(changed)}")
    if not isinstance(promoted.get("sequence"), int) or promoted["sequence"] <= source["sequence"]:
        raise Refusal("the promoted catalog's sequence does not follow its source's")
    check_promoted_models(source, promoted, channel)
    if promoted.get("channel") != channel:
        raise Refusal(f"the promoted catalog is for {promoted.get('channel')!r}, not the {channel} it is published to")

    declared = source.get("channel")
    if declared is not None:
        if declared not in CHANNEL_ORDER:
            raise Refusal(f"the source catalog names an unknown channel {declared!r}")
        if CHANNEL_ORDER.index(declared) >= CHANNEL_ORDER.index(channel):
            raise Refusal(f"{declared} is not promoted to {channel}: promotion only moves forward along "
                          f"{' to '.join(CHANNEL_ORDER)}")


def check_promoted_models(source: dict, promoted: dict, channel: str) -> None:
    """A promotion carries exactly CHANNEL's Macs. It may drop one the target
    has not qualified for; it may never add a Mac or change what one pins."""
    before = {model.get("deviceIdentifier"): model for model in source.get("models", [])}
    after = {model.get("deviceIdentifier"): model for model in promoted.get("models", [])}
    if len(after) != len(promoted.get("models", [])):
        raise Refusal("the promoted catalog lists a Mac twice")
    added = sorted(identifier for identifier in after if identifier not in before)
    if added:
        raise Refusal(f"a promotion cannot add a Mac the source does not carry: {', '.join(added)}")
    changed = sorted(
        identifier for identifier, model in after.items() if before[identifier] != model
    )
    if changed:
        raise Refusal(f"the promoted catalog changes what a Mac pins: {', '.join(changed)}")
    offered = set(supported_models.boards_for(channel))
    missing = sorted(offered - set(after))
    if missing:
        raise Refusal(f"the promoted {channel} catalog is missing Macs it must offer: {', '.join(missing)}")
    stray = sorted(set(after) - offered)
    if stray:
        raise Refusal(f"the promoted {channel} catalog offers Macs {channel} has not qualified: "
                      f"{', '.join(stray)}")
    if not after:
        # The coverage check accepts an empty catalog (it means "no release on
        # this channel yet"), but reaching that by withdrawing the last Mac from
        # a channel that has one is a mistake, not a withdrawal.
        raise Refusal(f"{channel} would offer no Mac at all: close the channel rather than "
                      "withdraw its last Mac")
    dropped = sorted(set(before) - set(after))
    if dropped:
        print(f"promotion_dropped={','.join(dropped)}", file=sys.stderr)


def check_promotion_mode(channel: str, real: bool, confirm: str | None, source_sha256: str | None) -> None:
    """Promotion is parked: a real run needs the owner to type the channel and
    pin the exact source catalog. A dry run needs neither."""
    if not real:
        return
    if confirm != f"promote-{channel}":
        raise Refusal(f"promotion is parked until the kernel work lands; a real promotion needs "
                      f"--confirm promote-{channel} (the workflow's confirm input) and the owner's approval")
    if not source_sha256:
        raise Refusal("a real promotion must pin the source catalog's sha256 (--source-sha256)")


# ── remote verification ──────────────────────────────────────────────────────


class HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        if urllib.parse.urlsplit(new_url).scheme != "https":
            raise Refusal(f"refusing a redirect to a non-https URL: {new_url}")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def open_url(url: str, timeout: float = 60, headers: dict[str, str] | None = None):
    opener = urllib.request.build_opener(HttpsOnlyRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": "omarchy-mac-catalog", **(headers or {})})
    return opener.open(request, timeout=timeout)


REMOTE_READ_ATTEMPTS = 8


def remote_digest(url: str, expected_size: int, whole=None, exact: bool = True) -> tuple[str, int]:
    # A multi-gigabyte read through the CDN can stall or close early; resume
    # from the bytes already hashed instead of failing the catalog. With exact,
    # expected_size is the pinned length; otherwise it is only an upper bound.
    digest = hashlib.sha256()
    size = 0
    for attempt in range(REMOTE_READ_ATTEMPTS):
        headers = {"Range": f"bytes={size}-"} if size else None
        try:
            with (open_url(url, headers=headers) if headers else open_url(url)) as response:
                if size and response.status != 206:
                    raise Refusal(f"{url} ignored a resume request (HTTP {response.status})")
                while block := response.read(8 * 1024 * 1024):
                    size += len(block)
                    if size > expected_size:
                        raise Refusal(f"{url} serves more than its pinned {expected_size} bytes")
                    digest.update(block)
                    if whole is not None:
                        whole.update(block)
            if exact and size < expected_size:
                raise http.client.IncompleteRead(b"", expected_size - size)
            return digest.hexdigest(), size
        except (TimeoutError, ConnectionError, http.client.IncompleteRead, urllib.error.URLError) as error:
            if isinstance(error, urllib.error.HTTPError):
                raise
            if exact and size == expected_size:
                return digest.hexdigest(), size
            if attempt + 1 == REMOTE_READ_ATTEMPTS:
                raise
            print(f"{url}: read stopped at {size} bytes ({error!r}); resuming", file=sys.stderr)
            time.sleep(5)
    raise AssertionError("unreachable")


# The installer dmg is not pinned by size in the catalog; this bounds the read.
MAXIMUM_INSTALLER_BYTES = 1024 ** 3


def verify_remote_installer(installer: dict) -> None:
    """Download the app the catalog offers and check it against the .sha256
    published beside it (publish-app writes it; mac-mirror.yml copies both),
    so a catalog never names a dmg that isn't there or isn't what was released."""
    url = installer["downloadURL"]
    name = url.rsplit("/", 1)[1]
    try:
        with open_url(f"{url}.sha256") as response:
            line = response.read(1024).decode(errors="replace")
    except urllib.error.HTTPError as error:
        raise Refusal(f"installer.downloadURL: {url}.sha256 answers HTTP {error.code}; mirror the app first") from None
    match = re.fullmatch(r"([0-9a-f]{64})  (\S+)\n?", line)
    if match is None or match.group(2) != name:
        raise Refusal(f"installer.downloadURL: {url}.sha256 does not read '<sha256>  {name}'")
    try:
        digest, size = remote_digest(url, MAXIMUM_INSTALLER_BYTES, exact=False)
    except urllib.error.HTTPError as error:
        raise Refusal(f"installer.downloadURL: {url} answers HTTP {error.code}; mirror the app first") from None
    if digest != match.group(1):
        raise Refusal(f"installer.downloadURL: {url} serves sha256 {digest} ({size} bytes), not its .sha256's {match.group(1)}")
    print(f"remote_verified=installer {digest}")


def verify_remote_artifact(artifact: dict, field: str) -> None:
    """Download ARTIFACT (or its parts) and check every pinned size and digest."""
    whole = hashlib.sha256()
    pieces = artifact.get("parts") or [artifact]
    for piece in pieces:
        digest, size = remote_digest(piece["url"], piece["sizeBytes"], whole)
        if (digest, size) != (piece["sha256"], piece["sizeBytes"]):
            raise Refusal(
                f"{field}: {piece['url']} serves sha256 {digest} ({size} bytes), "
                f"not the pinned {piece['sha256']} ({piece['sizeBytes']} bytes)"
            )
    if whole.hexdigest() != artifact["sha256"]:
        raise Refusal(f"{field}: the parts do not join into the pinned sha256 {artifact['sha256']}")
    print(f"remote_verified={field} {artifact['sha256']}")


# ── signing ──────────────────────────────────────────────────────────────────


def find_openssl() -> str:
    candidates = [os.environ.get("OMARCHY_OPENSSL"), shutil.which("openssl"),
                  "/opt/homebrew/opt/openssl@3/bin/openssl", "/usr/local/opt/openssl@3/bin/openssl"]
    for candidate in candidates:
        if not candidate or not os.access(candidate, os.X_OK):
            continue
        result = subprocess.run([candidate, "version"], capture_output=True, text=True)
        if result.returncode == 0 and re.match(r"OpenSSL [3-9]\.", result.stdout):
            return candidate
    raise Refusal("OpenSSL 3 or newer is required for Ed25519 raw signing (set OMARCHY_OPENSSL)")


def decode_private_key(text: bytes, source: str, raw_allowed: bool) -> bytes:
    if raw_allowed and len(text) == 32:
        return text
    try:
        seed = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise Refusal(f"the signing key in {source} is not base64 (or, in a file, 32 raw bytes)") from None
    if len(seed) != 32:
        raise Refusal(f"the signing key in {source} is not a 32-byte Ed25519 key")
    return seed


def load_private_key(key_env: str | None, key_file: Path | None) -> bytes:
    if (key_env is None) == (key_file is None):
        raise Refusal("pass exactly one of --key-env or --key-file")
    if key_env is not None:
        value = os.environ.pop(key_env, None)
        if not value:
            raise Refusal(f"the signing key variable {key_env} is empty or unset")
        return decode_private_key(value.encode(), key_env, raw_allowed=False)
    if not key_file.is_file() or key_file.is_symlink():
        raise Refusal(f"missing or unsafe key file: {key_file}")
    return decode_private_key(key_file.read_bytes(), str(key_file), raw_allowed=True)


def openssl_public_key(openssl: str, seed: bytes) -> bytes:
    result = subprocess.run(
        [openssl, "pkey", "-inform", "DER", "-in", "/dev/stdin", "-pubout", "-outform", "DER"],
        input=PRIVATE_KEY_DER_PREFIX + seed, capture_output=True,
    )
    if result.returncode != 0 or len(result.stdout) != 44 or not result.stdout.startswith(PUBLIC_KEY_DER_PREFIX):
        raise Refusal("OpenSSL could not derive the public key from the signing key")
    return result.stdout[-32:]


def openssl_sign(openssl: str, seed: bytes, message: Path) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        signature = Path(directory) / "signature"
        result = subprocess.run(
            [openssl, "pkeyutl", "-sign", "-keyform", "DER", "-inkey", "/dev/stdin",
             "-rawin", "-in", str(message), "-out", str(signature)],
            input=PRIVATE_KEY_DER_PREFIX + seed, capture_output=True,
        )
        if result.returncode != 0 or not signature.is_file() or signature.stat().st_size != 64:
            raise Refusal("OpenSSL failed to sign the catalog")
        return signature.read_bytes()


def openssl_verify(openssl: str, public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(public_key) != 32 or len(signature) != 64:
        return False
    with tempfile.TemporaryDirectory() as directory:
        files = {"key": PUBLIC_KEY_DER_PREFIX + public_key, "message": message, "signature": signature}
        for name, data in files.items():
            (Path(directory) / name).write_bytes(data)
        result = subprocess.run(
            [openssl, "pkeyutl", "-verify", "-pubin", "-keyform", "DER", "-inkey", f"{directory}/key",
             "-rawin", "-in", f"{directory}/message", "-sigfile", f"{directory}/signature"],
            capture_output=True,
        )
        return result.returncode == 0


def read_trust_root(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink() or path.stat().st_size != 32:
        raise Refusal(f"the trust root must be a 32-byte public key file: {path}")
    return path.read_bytes()


def fingerprint(public_key: bytes) -> str:
    return "sha256:" + hashlib.sha256(public_key).hexdigest()


def make_envelope(catalog: Path, signature: Path, output: Path, trust_root: Path) -> None:
    """The channel envelope, through the one tool that defines it. Its own
    signature check runs this script's `verify`, the portable equivalent of
    `catalog-signing.swift verify`."""
    tool = [sys.executable, str(Path(__file__).resolve())]
    if any(any(character.isspace() for character in part) for part in tool):
        raise Refusal("the envelope step needs a Python and checkout path without whitespace")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("CATALOG_SIGNING")}
    environment["OMARCHY_TRUST_ROOT"] = str(trust_root)
    environment["OMARCHY_CATALOG_SIGNING_TOOL"] = " ".join(tool)
    result = subprocess.run(
        ["bash", str(PUBLISH_CHANNELS), "envelope", "--catalog", str(catalog),
         "--signature", str(signature), "--output", str(output)],
        capture_output=True, text=True, env=environment,
    )
    if result.returncode != 0:
        raise Refusal(f"publish-channels envelope failed: {result.stderr.strip()}")


def decode_envelope(data: bytes) -> tuple[bytes, bytes]:
    try:
        document = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Refusal("the envelope is not JSON") from None
    if not isinstance(document, dict) or set(document) != {"schema_version", "catalog", "signature"}:
        raise Refusal("the envelope must have exactly schema_version, catalog and signature")
    if document["schema_version"] != 1:
        raise Refusal("unsupported envelope schema")
    try:
        catalog = base64.b64decode(document["catalog"], validate=True)
        signature = base64.b64decode(document["signature"], validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise Refusal("the envelope's catalog or signature is not base64") from None
    if not catalog or len(catalog) > MAXIMUM_CATALOG_BYTES or len(signature) != 64:
        raise Refusal("the envelope's catalog or signature has the wrong size")
    return catalog, signature


def verify_envelope(data: bytes, trust_root: bytes, openssl: str | None = None) -> dict:
    catalog, signature = decode_envelope(data)
    if not openssl_verify(openssl or find_openssl(), trust_root, catalog, signature):
        raise Refusal(f"the catalog signature does not verify against the trust root {fingerprint(trust_root)}")
    try:
        document = json.loads(catalog)
    except json.JSONDecodeError:
        raise Refusal("the signed catalog is not JSON") from None
    if not isinstance(document, dict) or document.get("schemaVersion") != SCHEMA_VERSION:
        raise Refusal("the signed catalog is not a schema 4 catalog")
    if not isinstance(document.get("sequence"), int) or document["sequence"] <= 0:
        raise Refusal("the signed catalog has no positive sequence")
    return document


# ── hosts ────────────────────────────────────────────────────────────────────


class ScratchHost:
    """A directory standing in for the host: dry runs and tests."""

    def __init__(self, directory: Path):
        self.directory = directory

    def describe(self, channel: str) -> str:
        return str(self.directory / f"catalog-{channel}.signed.json")

    def fetch(self, channel: str, timeout: float = 60) -> bytes | None:
        path = self.directory / f"catalog-{channel}.signed.json"
        return path.read_bytes() if path.is_file() else None

    def preflight(self, channel: str) -> None:
        pass

    def publish(self, channel: str, envelope: Path) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"catalog-{channel}.signed.json"
        temporary = target.with_suffix(".tmp")
        shutil.copyfile(envelope, temporary)
        os.replace(temporary, target)


class GitHubReleaseHost:
    """The stopgap host: one prerelease per channel, tag catalog-<channel>,
    whose catalog-<channel>.signed.json asset is replaced in place. Catalogs
    are the only mutable objects; images and engines live in immutable
    releases. Ticket 13 adds the catalog base host (INSTALLER_CATALOG_BASE)
    beside this one."""

    def __init__(self, repo: str):
        self.repo = repo

    def describe(self, channel: str) -> str:
        return f"https://github.com/{self.repo}/releases/download/catalog-{channel}/catalog-{channel}.signed.json"

    def fetch(self, channel: str, timeout: float = 60) -> bytes | None:
        try:
            with open_url(self.describe(channel), timeout=timeout, headers=NO_CACHE) as response:
                data = response.read(MAXIMUM_CATALOG_BYTES * 2 + 1)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise
        if len(data) > MAXIMUM_CATALOG_BYTES * 2:
            raise Refusal("the published catalog envelope is oversized")
        return data

    def gh(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(["gh", *arguments], capture_output=True, text=True)
        if check and result.returncode != 0:
            raise Refusal(f"gh {' '.join(arguments[:2])} failed: {result.stderr.strip()}")
        return result

    def release(self, tag: str) -> dict | None:
        result = self.gh("api", f"repos/{self.repo}/releases/tags/{tag}", check=False)
        if result.returncode != 0:
            if "Not Found" in result.stderr or "404" in result.stderr:
                return None
            raise Refusal(f"reading the {tag} release failed: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def asset_ids(self, tag: str) -> dict[str, int]:
        release = self.release(tag)
        return {asset["name"]: asset["id"] for asset in (release or {}).get("assets", [])}

    def preflight(self, channel: str) -> None:
        """Refuse while an earlier publish left its staged catalog behind: it
        may be newer than whatever is (or is not) served, so nothing may be
        published over it until someone resolves it by hand."""
        release = self.release(f"catalog-{channel}")
        if release is not None and (release.get("draft") or not release.get("prerelease")):
            raise Refusal(f"the catalog-{channel} release must be a published prerelease")
        staged = f"catalog-{channel}.signed.json.next"
        if staged in self.asset_ids(f"catalog-{channel}"):
            raise Refusal(f"an earlier publish left {staged} on the catalog-{channel} release; "
                          "check it, then rename it into place or delete it by hand")

    def publish(self, channel: str, envelope: Path) -> None:
        """Upload under a staging name, then swap it in: the old asset is
        deleted only once the new bytes are on the release, and the window
        without a catalog is one rename."""
        tag = f"catalog-{channel}"
        name = f"catalog-{channel}.signed.json"
        staged = f"{name}.next"
        release = self.release(tag)
        if release is None:
            target = os.environ.get("GITHUB_SHA", "main")
            self.gh("release", "create", tag, "--repo", self.repo, "--prerelease", "--latest=false",
                    "--target", target, "--title", f"Omarchy Mac {channel} catalog",
                    "--notes", f"The signed {channel} channel catalog, replaced in place by the Mac catalog "
                    "workflow. Not a release: images and engines live in their own immutable releases.")
        elif release.get("draft") or not release.get("prerelease"):
            raise Refusal(f"the {tag} release must be a published prerelease")
        self.preflight(channel)
        with tempfile.TemporaryDirectory() as directory:
            upload = Path(directory) / staged
            shutil.copyfile(envelope, upload)
            self.gh("release", "upload", tag, str(upload), "--repo", self.repo)
        assets = self.asset_ids(tag)
        if staged not in assets:
            raise Refusal(f"the staged {staged} is not on the {tag} release after upload")
        if name in assets:
            self.gh("api", "-X", "DELETE", f"repos/{self.repo}/releases/assets/{assets[name]}")
        for attempt in range(3):
            renamed = self.gh("api", "-X", "PATCH", f"repos/{self.repo}/releases/assets/{assets[staged]}",
                              "-f", f"name={name}", check=False)
            if renamed.returncode == 0:
                return
            time.sleep(5)
        raise Refusal(f"could not rename {staged} to {name} on {tag}: {renamed.stderr.strip()}; "
                      "rename it by hand to restore the channel")


class R2CatalogHost:
    """The catalog base (INSTALLER_CATALOG_BASE) on R2: catalog-<channel>.signed.json
    under the base's path in the bucket, replaced in place by one PUT, as the
    x86 side replaces its latest.json and omarchy.db. The publish job's
    per-channel concurrency group keeps two publishes apart; a PUT that timed
    out and lands after a newer one leaves an older sequence served, which the
    next publish of the newer catalog repairs. The bucket's lock must not cover
    these keys (only images, engines and apps are immutable)."""

    def __init__(self, endpoint: r2_s3.Endpoint, base: str):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.query or not parsed.path.strip("/"):
            raise Refusal(f"invalid catalog base: {base}")
        self.endpoint = endpoint
        self.base = base.rstrip("/")
        self.prefix = parsed.path.strip("/") + "/"

    @classmethod
    def from_environment(cls) -> "R2CatalogHost":
        try:
            endpoint = r2_s3.Endpoint.parse(os.environ.get("R2_BUCKET", "") or "omarchy-mac",
                                            os.environ.get("R2_ENDPOINT", ""), os.environ.get("R2_ACCESS_KEY_ID", ""),
                                            os.environ.get("R2_SECRET_ACCESS_KEY", ""))
        except r2_s3.R2Error as error:
            raise Refusal(str(error)) from None
        return cls(endpoint, identity()["INSTALLER_CATALOG_BASE"])

    def describe(self, channel: str) -> str:
        return f"{self.base}/catalog-{channel}.signed.json"

    def fetch(self, channel: str, timeout: float = 60) -> bytes | None:
        """What an app is served: the URL it fetches, unchanged."""
        try:
            with open_url(self.describe(channel), timeout=timeout, headers=NO_CACHE) as response:
                data = response.read(MAXIMUM_CATALOG_BYTES * 2 + 1)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise
        if len(data) > MAXIMUM_CATALOG_BYTES * 2:
            raise Refusal("the published catalog envelope is oversized")
        return data

    def preflight(self, channel: str) -> None:
        pass

    def publish(self, channel: str, envelope: Path) -> None:
        data = envelope.read_bytes()
        key = f"{self.prefix}catalog-{channel}.signed.json"
        try:
            with self.endpoint.request("PUT", key, headers={"Content-Type": "application/json"},
                                       body=io.BytesIO(data), size=len(data),
                                       payload_sha256=hashlib.sha256(data).hexdigest(), timeout=60) as response:
                response.read()
        except urllib.error.HTTPError as error:
            text = r2_s3.refusal_text(f"store {key}", error)
            if "ObjectLockedByBucketPolicy" in text:
                text += "; the bucket lock covers the catalogs, which must stay replaceable"
            raise Refusal(text) from None
        except r2_s3.R2Error as error:
            raise Refusal(str(error)) from None


def catalog_host(spec: str):
    if spec.startswith("scratch:"):
        return ScratchHost(Path(spec[len("scratch:"):]))
    if spec == "github-release":
        return GitHubReleaseHost(stopgap_repo())
    if spec == "r2":
        return R2CatalogHost.from_environment()
    raise Refusal(f"unknown catalog host {spec!r} (r2, github-release or scratch:DIR)")


def catalog_hosts(spec: str) -> list:
    """A comma list of hosts, written in order: the first is the catalog base,
    the rest mirror it (the GitHub stopgap, for apps that read it)."""
    names = [name.strip() for name in spec.split(",")]
    if "" in names or len(set(names)) != len(names):
        raise Refusal(f"invalid catalog host list: {spec!r}")
    return [catalog_host(name) for name in names]


def live_sequence(host, channel: str, trust_root: bytes, openssl: str) -> int:
    """The published catalog's sequence, or 0 when the channel has none."""
    data = host.fetch(channel)
    if data is None:
        return 0
    try:
        return verify_envelope(data, trust_root, openssl)["sequence"]
    except Refusal as error:
        raise Refusal(
            f"the catalog published at {host.describe(channel)} does not verify ({error}); "
            "remove it by hand before publishing over it"
        ) from None


# ── commands ─────────────────────────────────────────────────────────────────


def check_repository() -> None:
    """Only this repository's own runs publish its catalogs: a workflow_call
    from elsewhere would check out the caller's code with the caller's token."""
    repository = os.environ.get("GITHUB_REPOSITORY")
    if repository is not None and repository != stopgap_repo():
        raise Refusal(f"catalogs are released from {stopgap_repo()}, not {repository}")


def command_prepare(arguments) -> None:
    check_repository()
    channel = check_channel(arguments.channel)
    now = (
        datetime.datetime.strptime(arguments.now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        if arguments.now
        else datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    )
    payload = load_json(arguments.payload, "payload")
    engine = load_json(arguments.engine, "engine")
    metadata = load_json(arguments.installer_data, "installer_data")
    evidence = load_json(arguments.evidence, "evidence") if arguments.evidence else None
    installer = load_json(arguments.installer, "installer")
    catalog = build_catalog(channel, arguments.image, payload, engine, metadata, evidence, installer, now)
    if arguments.verify_remote:
        verify_remote_artifact(check_artifact(payload, "payload", True), "payload")
        verify_remote_artifact(check_engine(engine)["artifact"], "engine")
        verify_remote_artifact(check_artifact(metadata, "installer_data"), "installer_data")
        verify_remote_installer(catalog["installer"])
    if arguments.check_live:
        trust_root = read_trust_root(arguments.trust_root)
        live = live_sequence(catalog_host(arguments.host), channel, trust_root, find_openssl())
        if catalog["sequence"] <= live:
            raise Refusal(f"sequence {catalog['sequence']} does not follow the published {live}")
    data = catalog_bytes(catalog)
    arguments.out.mkdir(parents=True, exist_ok=True)
    output = arguments.out / f"catalog-{channel}.json"
    output.write_bytes(data)
    print(f"catalog={output}")
    print(f"catalog_sha256={hashlib.sha256(data).hexdigest()}")
    print(f"sequence={catalog['sequence']}")
    print(f"image={arguments.image}")
    print(f"payload_sha256={payload['sha256']}")
    print(f"models={len(catalog['models'])} untested={sum('previewNote' in m for m in catalog['models'])}")


def command_check_evidence(arguments) -> None:
    # Evidence gates only the catalogs built from an image.
    channel = check_channel(arguments.catalog.name.removeprefix("catalog-").removesuffix(".json"))
    catalog = load_json(arguments.catalog, "catalog")
    digest = catalog_payload_digest(catalog)
    evidence = check_evidence(load_json(arguments.evidence, "evidence") if arguments.evidence else None, digest)
    if channel in RELEASE_CHANNELS:
        images = {model.get("evidenceRevision") for model in catalog["models"]}
        if len(images) != 1:
            raise Refusal(f"catalog-{channel} must name exactly one image")
        image = check_image_name(images.pop(), channel)
        pin = catalog["models"][0]
        check_release_image(image, channel, from_catalog("payload", pin["payloadArtifact"], pin["payloadDigest"], True),
                       from_catalog("installer_data", pin["metadataArtifact"], pin["metadataDigest"]), evidence)
    print(f"evidence=passed {digest}")


def command_check_engine(arguments) -> None:
    channel = arguments.catalog.name.removeprefix("catalog-").removesuffix(".json")
    print(check_engine_pin(channel, load_json(arguments.catalog, "catalog"), arguments.allow_engine_digest or None))


def command_sign(arguments) -> None:
    # Read (and drop from the environment) the key before anything else runs.
    seed = load_private_key(arguments.key_env, arguments.key_file)
    openssl = find_openssl()
    trust_root = read_trust_root(arguments.trust_root)
    if openssl_public_key(openssl, seed) != trust_root:
        raise Refusal(f"the signing key does not match the trust root {fingerprint(trust_root)}")
    catalog_path = arguments.catalog
    if not catalog_path.is_file() or catalog_path.is_symlink():
        raise Refusal(f"missing or unsafe catalog: {catalog_path}")
    catalog = catalog_path.read_bytes()
    if len(catalog) > MAXIMUM_CATALOG_BYTES:
        raise Refusal("the catalog is too large for the app")
    signature = openssl_sign(openssl, seed, catalog_path)
    del seed
    if not openssl_verify(openssl, trust_root, catalog, signature):
        raise Refusal("the new signature does not verify against the trust root")
    channel = catalog_path.name.removeprefix("catalog-").removesuffix(".json")
    check_signed_channel(channel)
    arguments.out.mkdir(parents=True, exist_ok=True)
    signature_path = arguments.out / f"catalog-{channel}.json.sig"
    signature_path.write_bytes(signature)
    envelope = arguments.out / f"catalog-{channel}.signed.json"
    make_envelope(catalog_path, signature_path, envelope, arguments.trust_root)
    signed, _ = decode_envelope(envelope.read_bytes())
    if signed != catalog:
        raise Refusal("the envelope does not carry the catalog that was signed")
    verify_envelope(envelope.read_bytes(), trust_root, openssl)
    print(f"envelope={envelope}")
    print(f"catalog_sha256={hashlib.sha256(catalog).hexdigest()}")
    print(f"trust_root_fingerprint={fingerprint(trust_root)}")
    print("catalog_signature=passed")


def command_verify(arguments) -> None:
    """`verify PUBLIC_KEY CATALOG SIGNATURE`, as catalog-signing.swift takes it."""
    public_key = read_trust_root(arguments.public_key)
    if not openssl_verify(find_openssl(), public_key, arguments.catalog.read_bytes(), arguments.signature.read_bytes()):
        raise Refusal("the catalog signature does not verify")
    print("catalog_signature=passed")


def command_verify_envelope(arguments) -> None:
    trust_root = read_trust_root(arguments.trust_root)
    data = arguments.envelope.read_bytes()
    document = verify_envelope(data, trust_root)
    catalog, _ = decode_envelope(data)
    if arguments.expect_sha256 and hashlib.sha256(catalog).hexdigest() != arguments.expect_sha256:
        raise Refusal("the signed catalog is not the one this run prepared")
    print(f"catalog_sha256={hashlib.sha256(catalog).hexdigest()}")
    print(f"sequence={document['sequence']}")
    print(f"trust_root_fingerprint={fingerprint(trust_root)}")
    print("catalog_signature=passed")


def read_source(host, source: str, trust_root: bytes, openssl: str) -> tuple[bytes, bytes, dict]:
    """The source channel's published envelope, its signed catalog bytes and
    the catalog, refusing anything the trust root did not sign."""
    data = host.fetch(source)
    if data is None:
        raise Refusal(f"no {source} catalog is published at {host.describe(source)}; nothing to promote")
    try:
        document = verify_envelope(data, trust_root, openssl)
    except Refusal as error:
        raise Refusal(f"the {source} catalog at {host.describe(source)} is refused: {error}") from None
    catalog, _ = decode_envelope(data)
    return data, catalog, document


def command_promote(arguments) -> None:
    check_repository()
    source_channel, channel = check_promotion_channels(arguments.source, arguments.channel)
    expected = arguments.source_sha256 or None
    if expected is not None and not SHA256.match(expected):
        raise Refusal("--source-sha256 must be 64 lowercase hex digits")
    check_promotion_mode(channel, arguments.real, arguments.confirm, expected)
    now = (
        datetime.datetime.strptime(arguments.now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        if arguments.now
        else datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    )
    openssl = find_openssl()
    trust_root = read_trust_root(arguments.trust_root)
    host = catalog_host(arguments.host)
    envelope, source_bytes, source = read_source(host, source_channel, trust_root, openssl)
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if expected is not None and source_sha256 != expected:
        raise Refusal(f"the published {source_channel} catalog is sha256 {source_sha256}, not the pinned {expected}")
    promoted = promote_catalog(source, now, channel, source_channel=source_channel)
    artifacts = promotion_artifacts(promoted)
    if arguments.verify_remote:
        # Read back what the catalog pins; nothing is built or uploaded.
        for field, artifact in artifacts:
            verify_remote_artifact(artifact, field)
    if arguments.check_live:
        live = live_sequence(host, channel, trust_root, openssl)
        if promoted["sequence"] <= live:
            raise Refusal(f"sequence {promoted['sequence']} does not follow the published {channel} {live}")
    data = catalog_bytes(promoted)
    arguments.out.mkdir(parents=True, exist_ok=True)
    (arguments.out / "channel").write_text(f"{channel}\n")
    (arguments.out / "source-channel").write_text(f"{source_channel}\n")
    (arguments.out / "source.signed.json").write_bytes(envelope)
    output = arguments.out / f"catalog-{channel}.json"
    output.write_bytes(data)
    payload = dict(artifacts)["payload"]
    print(f"mode={'real' if arguments.real else 'dry-run'}")
    print(f"source={host.describe(source_channel)}")
    print(f"source_catalog_sha256={source_sha256}")
    print(f"source_sequence={source['sequence']}")
    print(f"catalog={output}")
    print(f"catalog_sha256={hashlib.sha256(data).hexdigest()}")
    print(f"sequence={promoted['sequence']}")
    print(f"image={promoted['models'][0].get('evidenceRevision')}")
    print(f"payload_sha256={payload['sha256']}")
    print(f"engine_sha256={dict(artifacts)['engine']['sha256']}")
    print(f"installer_data_sha256={dict(artifacts)['installer_data']['sha256']}")
    print(f"models={len(promoted['models'])}")


def command_check_promotion(arguments) -> None:
    source_channel, channel = check_promotion_channels(arguments.source_channel, arguments.channel)
    if arguments.catalog.name != f"catalog-{channel}.json":
        raise Refusal(f"the promoted catalog must be catalog-{channel}.json, not {arguments.catalog.name}")
    trust_root = read_trust_root(arguments.trust_root)
    envelope = arguments.source.read_bytes()
    source = verify_envelope(envelope, trust_root)
    if arguments.source_sha256:
        source_sha256 = hashlib.sha256(decode_envelope(envelope)[0]).hexdigest()
        if source_sha256 != arguments.source_sha256:
            raise Refusal(f"the source catalog is sha256 {source_sha256}, not the pinned {arguments.source_sha256}")
    check_promotion_source(source)
    promoted = load_json(arguments.catalog, "catalog")
    check_same_pins(source, promoted, channel)
    promotion_artifacts(promoted)
    print(f"promotion=checked {source_channel} to {channel}, sequence {source['sequence']} to {promoted['sequence']}")


# GitHub can keep serving a replaced release asset's old bytes for minutes
# (2026-10-21: stale 61 s after the upload; 2026-10-24: about 120 s).
READBACK_DELAYS = (5, 5, 10, 10, 15, 15, 20, 30, 30, 30, 30, 30, 30, 30)
# Each readback fetch is bounded so that every attempt timing out, plus the
# delays, still ends inside the publish job's 15 minutes.
READBACK_TIMEOUT = 20
NO_CACHE = {"Cache-Control": "no-cache", "Pragma": "no-cache"}


def read_back(host, channel: str, data: bytes, sleep=time.sleep) -> None:
    """Read the published catalog back as a client would, riding out
    transient errors and a stale cache, and refuse if it never appears."""
    for attempt, delay in enumerate((0, *READBACK_DELAYS), start=1):
        if delay:
            sleep(delay)
        try:
            served = host.fetch(channel, timeout=READBACK_TIMEOUT)
        except (OSError, urllib.error.URLError) as error:
            print(f"readback attempt {attempt}: {error}", file=sys.stderr)
            continue
        if served == data:
            return
        found = "nothing" if served is None else f"{len(served)} other bytes (sha256 {hashlib.sha256(served).hexdigest()[:12]})"
        print(f"readback attempt {attempt}: {host.describe(channel)} serves {found}, not the new catalog", file=sys.stderr)
    raise Refusal(f"{host.describe(channel)} does not serve the catalog just published")


def command_publish(arguments) -> None:
    check_repository()
    channel = check_signed_channel(arguments.channel)
    openssl = find_openssl()
    trust_root = read_trust_root(arguments.trust_root)
    data = arguments.envelope.read_bytes()
    document = verify_envelope(data, trust_root, openssl)
    # Every host is checked before any is written, so a refusal on the
    # second never leaves the first ahead.
    pending = []
    for host in catalog_hosts(arguments.host):
        if host.fetch(channel) == data:
            # A retry after a publish whose readback failed: already live.
            print(f"published={host.describe(channel)} (already live)")
            continue
        host.preflight(channel)
        live = live_sequence(host, channel, trust_root, openssl)
        if document["sequence"] <= live:
            raise Refusal(f"sequence {document['sequence']} does not follow the published {live} at {host.describe(channel)}")
        pending.append((host, live))
    for host, live in pending:
        host.publish(channel, arguments.envelope)
        read_back(host, channel, data)
        print(f"published={host.describe(channel)}")
        print(f"sequence={document['sequence']} (was {live})")


def command_public_key(arguments) -> None:
    seed = load_private_key(arguments.key_env, arguments.key_file)
    public_key = openssl_public_key(find_openssl(), seed)
    arguments.output.write_bytes(public_key)
    print(f"trust_root_fingerprint={fingerprint(public_key)}")


def parse_arguments(argv: list[str]):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare")
    prepare.add_argument("--channel", required=True)
    prepare.add_argument("--image", required=True)
    prepare.add_argument("--payload", type=Path, required=True)
    prepare.add_argument("--engine", type=Path, required=True)
    prepare.add_argument("--installer-data", type=Path, required=True)
    prepare.add_argument("--evidence", type=Path)
    prepare.add_argument("--installer", type=Path, required=True)
    prepare.add_argument("--verify-remote", action="store_true")
    prepare.add_argument("--check-live", action="store_true")
    prepare.add_argument("--host", default="github-release")
    prepare.add_argument("--trust-root", type=Path, default=DEFAULT_TRUST_ROOT)
    prepare.add_argument("--now", help="YYYY-MM-DDTHH:MM:SSZ (tests only)")
    prepare.add_argument("--out", type=Path, required=True)

    promote = commands.add_parser("promote")
    promote.add_argument("--channel", required=True)
    promote.add_argument("--source", required=True, help="the channel whose published catalog is promoted")
    promote.add_argument("--source-sha256", default="", help="sha256 of the source's signed catalog")
    promote.add_argument("--real", action="store_true", help="a real promotion (parked: needs --confirm)")
    promote.add_argument("--confirm", default="", help="promote-<channel> for a real promotion")
    promote.add_argument("--verify-remote", action="store_true")
    promote.add_argument("--check-live", action="store_true")
    promote.add_argument("--host", default="github-release")
    promote.add_argument("--trust-root", type=Path, default=DEFAULT_TRUST_ROOT)
    promote.add_argument("--now", help="YYYY-MM-DDTHH:MM:SSZ (tests only)")
    promote.add_argument("--out", type=Path, required=True)

    check_promotion = commands.add_parser("check-promotion")
    check_promotion.add_argument("--channel", required=True)
    check_promotion.add_argument("--source-channel", required=True)
    check_promotion.add_argument("--catalog", type=Path, required=True)
    check_promotion.add_argument("--source", type=Path, required=True)
    check_promotion.add_argument("--source-sha256", default="")
    check_promotion.add_argument("--trust-root", type=Path, default=DEFAULT_TRUST_ROOT)

    evidence = commands.add_parser("check-evidence")
    evidence.add_argument("--catalog", type=Path, required=True)
    evidence.add_argument("--evidence", type=Path)

    engine = commands.add_parser("check-engine")
    engine.add_argument("--catalog", type=Path, required=True)
    engine.add_argument("--allow-engine-digest", default="",
                        help="sha256:<hex>, an owner-approved rc or stable engine other than the locked one")

    for name in ("sign", "public-key"):
        command = commands.add_parser(name)
        command.add_argument("--key-env")
        command.add_argument("--key-file", type=Path)
        if name == "sign":
            command.add_argument("--catalog", type=Path, required=True)
            command.add_argument("--trust-root", type=Path, default=DEFAULT_TRUST_ROOT)
            command.add_argument("--out", type=Path, required=True)
        else:
            command.add_argument("--output", type=Path, required=True)

    verify = commands.add_parser("verify")
    verify.add_argument("public_key", type=Path)
    verify.add_argument("catalog", type=Path)
    verify.add_argument("signature", type=Path)

    verify = commands.add_parser("verify-envelope")
    verify.add_argument("--envelope", type=Path, required=True)
    verify.add_argument("--trust-root", type=Path, default=DEFAULT_TRUST_ROOT)
    verify.add_argument("--expect-sha256")

    publish = commands.add_parser("publish")
    publish.add_argument("--channel", required=True)
    publish.add_argument("--envelope", type=Path, required=True)
    publish.add_argument("--host", required=True)
    publish.add_argument("--trust-root", type=Path, default=DEFAULT_TRUST_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    handler = {
        "prepare": command_prepare,
        "check-evidence": command_check_evidence,
        "check-engine": command_check_engine,
        "promote": command_promote,
        "check-promotion": command_check_promotion,
        "sign": command_sign,
        "verify": command_verify,
        "verify-envelope": command_verify_envelope,
        "publish": command_publish,
        "public-key": command_public_key,
    }[arguments.command]
    try:
        handler(arguments)
    except Refusal as error:
        print(f"catalog_release: refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
