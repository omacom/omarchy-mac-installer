#!/usr/bin/env python3
"""The Mac catalog release: build, evidence gate, CI signing, hosts, dry run,
and promotion to rc and stable.

Driven through scripts/catalog_release.py's command line, as the workflow
runs it, with a throwaway Ed25519 key. Signing needs OpenSSL 3; on a Mac the
signatures are also checked with catalog-signing.swift verify.
"""
from __future__ import annotations

import base64
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "catalog_release.py"
SIGNING_TOOL = ROOT / "scripts" / "catalog-signing.swift"
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "mac-catalog.yml"
SIGN_WORKFLOW = WORKFLOWS / "mac-catalog-sign.yml"
PROMOTE_WORKFLOW = WORKFLOWS / "mac-promote.yml"
FIXTURE = ROOT / "Tests" / "OmarchyAppleInstallerTrustCoreTests" / "Fixtures" / "ci-signed-catalog"

spec = importlib.util.spec_from_file_location("catalog_release", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

try:
    OPENSSL = release.find_openssl()
except release.Refusal:
    OPENSSL = None
if OPENSSL is None and os.environ.get("CI"):
    raise SystemExit("CI must provide OpenSSL 3 for the catalog signing tests")

NOW = "2026-10-14T00:00:00Z"
SEQUENCE = int(datetime.datetime(2026, 10, 14, tzinfo=datetime.timezone.utc).timestamp())
REVISION = "1" * 40


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def release_base(tag: str) -> str:
    return f"https://github.com/{release.stopgap_repo()}/releases/download/{tag}"


def artifact(tag: str, name: str, data: bytes) -> dict:
    return {"url": f"{release_base(tag)}/{name}", "fileName": name, "sizeBytes": len(data), "sha256": sha(data)}


PAYLOAD_PARTS = [b"payload-part-zero" * 50, b"payload-part-one" * 40]
PAYLOAD = b"".join(PAYLOAD_PARTS)
# The image name: lane, date, package set id and image id (the payload's sha256).
IMAGE = f"omarchy-mac-edge-2026.10.14-0123456789ab-{sha(PAYLOAD)[:12]}"
# An image named before image ids were added: its release and the catalog pinning it are live.
LEGACY_IMAGE = "omarchy-mac-edge-2026.10.14-0123456789ab"
# An rc-lane image (mac-image-check name rc): its release tag, Mac revision, date and package set id.
RC_IMAGE = "omarchy-mac-4.5.0-rc2-r1-2026.10.28-0123456789ab"
# The zips mac-image.yml stages: each lane's, named for the date its inputs were resolved.
RC_ZIP = "omarchy-2026.10.28-aarch64-apple-silicon-mac-rc-os-package.zip"
STABLE_IMAGE = "omarchy-mac-4.5.0-r1-2026.10.28-0123456789ab"
FINAL_ON_RC_IMAGE = "omarchy-mac-4.5.0-rc-r1-2026.10.28-0123456789ab"
STABLE_ZIP = "omarchy-2026.10.28-aarch64-apple-silicon-mac-stable-os-package.zip"
EDGE_ZIP = "omarchy-2026.10.28-aarch64-apple-silicon-mac-edge-os-package.zip"
ENGINE = b"engine-tarball" * 30
METADATA = b'{"os_list": []}\n'
ZIP = f"{IMAGE}.zip"


def payload_document() -> dict:
    document = artifact(IMAGE, ZIP, PAYLOAD)
    document["parts"] = [
        artifact(IMAGE, f"{ZIP}.part{index:02d}", part) for index, part in enumerate(PAYLOAD_PARTS)
    ]
    return document


def engine_document() -> dict:
    return dict(
        artifact("engine-v0.9.2-omarchy.28", "installer-v0.9.2-omarchy.28.tar.gz", ENGINE),
        version="v0.9.2-omarchy.28",
        downstreamRevision=REVISION,
    )


def installer_document() -> dict:
    # 2.3.17 is the floor of a catalog that offers an untested Mac (supported_models.UNTESTED_APP_FLOOR).
    return {"minimumVersion": "2.3.17", "latestVersion": "2.3.17",
            "downloadURL": f"{release_base('app-v2.3.17')}/Omarchy-Installer.dmg"}


def evidence_document(image_sha256: str = sha(PAYLOAD)) -> dict:
    return {
        "image_sha256": image_sha256,
        "result": "pass",
        "scenarios": [
            {"name": "first-boot", "result": "pass"},
            {"name": "conversion", "result": "pass"},
            {"name": "second-boot", "result": "pass"},
        ],
        "harness_commit": "2" * 40,
        "runner": "namespace linux-on-apple arm64",
        "run_url": "https://github.com/example/acceptance/actions/runs/1",
    }


def generate_key(directory: Path) -> tuple[str, Path]:
    """A throwaway key: its base64 seed and its trust-root file."""
    der = subprocess.run(
        [OPENSSL, "genpkey", "-algorithm", "ed25519", "-outform", "DER"], capture_output=True, check=True
    ).stdout
    seed = der[-32:]
    trust_root = directory / "trust-root.ed25519.pub"
    trust_root.write_bytes(release.openssl_public_key(OPENSSL, seed))
    return base64.b64encode(seed).decode(), trust_root


class ReleaseTestCase(unittest.TestCase):
    """A throwaway key, host tools that record and fail, and the CLI."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="omarchy-catalog-release-"))
        self.addCleanup(shutil.rmtree, self.directory)
        self.key, self.trust_root = generate_key(self.directory)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.calls = self.directory / "calls.log"
        self.calls.write_text("")
        # Anything that would reach a real host records itself and fails.
        for tool in ("gh", "curl", "aws", "wget"):
            shim = self.bin / tool
            shim.write_text(f'#!/bin/bash\nprintf "%s\\n" "{tool} $*" >>"{self.calls}"\nexit 97\n')
            shim.chmod(0o755)
        self.environment = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", OMARCHY_OPENSSL=OPENSSL)
        self.environment.pop("CATALOG_SIGNING_KEY", None)
        self.environment.pop("GITHUB_REPOSITORY", None)

    def write(self, name: str, document: object) -> Path:
        path = self.directory / name
        path.write_text(json.dumps(document))
        return path

    script = SCRIPT

    def use_tested_only_manifest(self) -> None:
        """Run the CLI from a copy of scripts/ whose supported-models.json has no
        untested Mac, as before any Mac went untested: rc and stable then take
        every supported Mac, so edge can be promoted. The rest of the repository
        is linked in unchanged."""
        tree = self.directory / "repository"
        tree.mkdir()
        for entry in ROOT.iterdir():
            if entry.name != "scripts":
                (tree / entry.name).symlink_to(entry)
        shutil.copytree(ROOT / "scripts", tree / "scripts", symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
        manifest_path = tree / "scripts" / "supported-models.json"
        manifest = json.loads(manifest_path.read_text())
        untested = set(manifest.get("untested", {}))
        manifest["supported"] = [identifier for identifier in manifest["supported"] if identifier not in untested]
        manifest["untested"] = {}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        self.script = tree / "scripts" / "catalog_release.py"

    def run_tool(self, *arguments: str, environment: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.script), *arguments],
            capture_output=True, text=True, env=environment or self.environment,
        )

    def prepare(self, evidence: object = "default", payload: dict | None = None, out: str = "out",
                *extra: str) -> subprocess.CompletedProcess:
        arguments = [
            "prepare", "--channel", "edge", "--image", IMAGE,
            "--payload", str(self.write("payload.json", payload or payload_document())),
            "--engine", str(self.write("engine.json", engine_document())),
            "--installer-data", str(self.write("metadata.json", artifact(IMAGE, "installer_data.json", METADATA))),
            "--installer", str(self.write("installer.json", installer_document())),
            "--now", NOW, "--out", str(self.directory / out), *extra,
        ]
        if evidence is not None:
            document = evidence_document() if evidence == "default" else evidence
            arguments += ["--evidence", str(self.write("evidence.json", document))]
        return self.run_tool(*arguments)

    def sign(self, catalog: Path, key: str | None = None, trust_root: Path | None = None,
             out: str = "signed") -> subprocess.CompletedProcess:
        environment = dict(self.environment, CATALOG_SIGNING_KEY=self.key if key is None else key)
        return self.run_tool(
            "sign", "--key-env", "CATALOG_SIGNING_KEY", "--catalog", str(catalog),
            "--trust-root", str(trust_root or self.trust_root), "--out", str(self.directory / out),
            environment=environment,
        )

    def assert_refused(self, result: subprocess.CompletedProcess, reason: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("refused", result.stderr)
        self.assertIn(reason, result.stderr)

    def built_catalog(self) -> Path:
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.directory / "out" / "catalog-edge.json"

    def signed_at(self, now: str, out: str) -> Path:
        result = self.prepare("default", None, out, "--now", now)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sign(self.directory / out / "catalog-edge.json", out=f"{out}-signed").returncode, 0)
        return self.directory / f"{out}-signed" / "catalog-edge.signed.json"


@unittest.skipIf(OPENSSL is None, "OpenSSL 3 is required")
class CatalogReleaseTests(ReleaseTestCase):
    # ── build, sign, verify ──────────────────────────────────────────────────

    def test_round_trip_enables_every_mac_and_pins_the_digests(self) -> None:
        catalog_path = self.built_catalog()
        catalog = json.loads(catalog_path.read_text())
        supported, _ = release.supported_models.load()
        untested = release.supported_models.untested()
        self.assertEqual([model["deviceIdentifier"] for model in catalog["models"]], supported)
        self.assertEqual(catalog["schemaVersion"], 4)
        self.assertEqual(catalog["sequence"], SEQUENCE)
        self.assertNotIn("expiresAt", catalog)
        for model in catalog["models"]:
            self.assertEqual(model["status"], "enabled")
            self.assertEqual(model["payloadDigest"], f"sha256:{sha(PAYLOAD)}")
            self.assertEqual(model["engineDigest"], f"sha256:{sha(ENGINE)}")
            self.assertEqual(model["metadataDigest"], f"sha256:{sha(METADATA)}")
            self.assertEqual(model["evidenceRevision"], IMAGE)
            self.assertEqual(
                [part["sha256"] for part in model["payloadArtifact"]["parts"]],
                [f"sha256:{sha(part)}" for part in PAYLOAD_PARTS],
            )
            self.assertEqual(model.get("previewNote"), untested.get(model["deviceIdentifier"]))

        signed = self.sign(catalog_path)
        self.assertEqual(signed.returncode, 0, signed.stderr)
        self.assertNotIn(self.key, signed.stdout + signed.stderr)
        envelope = self.directory / "signed" / "catalog-edge.signed.json"
        verified = self.run_tool("verify-envelope", "--envelope", str(envelope), "--trust-root", str(self.trust_root),
                                 "--expect-sha256", sha(catalog_path.read_bytes()))
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIn("catalog_signature=passed", verified.stdout)
        document = json.loads(envelope.read_text())
        self.assertEqual(set(document), {"schema_version", "catalog", "signature"})
        self.assertEqual(base64.b64decode(document["catalog"]), catalog_path.read_bytes())
        self.assertEqual(self.calls.read_text(), "")

        if platform.system() == "Darwin" and shutil.which("xcrun"):
            signature = self.directory / "signed" / "catalog-edge.json.sig"
            swift = subprocess.run(
                ["xcrun", "swift", str(SIGNING_TOOL), "verify", str(self.trust_root), str(catalog_path),
                 str(signature)], capture_output=True, text=True,
            )
            self.assertEqual(swift.stdout.strip(), "catalog_signature=passed", swift.stderr)

    def test_a_tampered_envelope_does_not_verify(self) -> None:
        self.assertEqual(self.sign(self.built_catalog()).returncode, 0)
        envelope = self.directory / "signed" / "catalog-edge.signed.json"
        document = json.loads(envelope.read_text())
        catalog = base64.b64decode(document["catalog"]).replace(b'"enabled"', b'"disabled"', 1)
        document["catalog"] = base64.b64encode(catalog).decode()
        envelope.write_text(json.dumps(document))
        result = self.run_tool("verify-envelope", "--envelope", str(envelope), "--trust-root", str(self.trust_root))
        self.assert_refused(result, "does not verify")

    def test_the_app_fixture_verifies_and_covers_every_mac(self) -> None:
        trust_root = (FIXTURE / "trust-root.ed25519.pub").read_bytes()
        document = release.verify_envelope((FIXTURE / "catalog-edge.signed.json").read_bytes(), trust_root, OPENSSL)
        # The fixture was signed before the M4 and M5 Macs went untested on edge: it names every Mac
        # that is not untested today, which is what a release channel offers.
        self.assertEqual(sorted(model["deviceIdentifier"] for model in document["models"]),
                         sorted(release.supported_models.channel_supported("stable")))

    # ── the evidence gate ────────────────────────────────────────────────────

    def test_refuses_without_evidence(self) -> None:
        self.assert_refused(self.prepare(evidence=None), "no VM evidence record")
        self.assertFalse((self.directory / "out").exists())

    def test_refuses_failed_evidence(self) -> None:
        self.assert_refused(self.prepare(evidence=dict(evidence_document(), result="fail")), "did not pass")

    def test_refuses_evidence_for_another_image(self) -> None:
        self.assert_refused(self.prepare(evidence=evidence_document("f" * 64)), "not " + sha(PAYLOAD))

    def test_refuses_evidence_missing_a_required_scenario(self) -> None:
        evidence = evidence_document()
        evidence["scenarios"] = evidence["scenarios"][:2]
        self.assert_refused(self.prepare(evidence=evidence), "no passing second-boot")

    def test_refuses_a_pass_with_a_failed_scenario(self) -> None:
        evidence = evidence_document()
        evidence["scenarios"].append({"name": "update", "result": "fail"})
        self.assert_refused(self.prepare(evidence=evidence), "scenarios failed: update")

    def test_refuses_malformed_evidence(self) -> None:
        self.assert_refused(self.prepare(evidence=dict(evidence_document(), harness_commit="main")), "harness_commit")
        self.assert_refused(self.prepare(evidence=["pass"]), "JSON object")
        missing = evidence_document()
        del missing["run_url"]
        self.assert_refused(self.prepare(evidence=missing), "no run_url")

    def test_the_signing_job_rechecks_the_evidence(self) -> None:
        catalog = self.built_catalog()
        ok = self.run_tool("check-evidence", "--catalog", str(catalog),
                           "--evidence", str(self.write("e.json", evidence_document())))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        other = self.run_tool("check-evidence", "--catalog", str(catalog),
                              "--evidence", str(self.write("e.json", evidence_document("e" * 64))))
        self.assert_refused(other, "not " + sha(PAYLOAD))
        self.assert_refused(self.run_tool("check-evidence", "--catalog", str(catalog)), "no VM evidence")
        renamed = self.directory / "catalog-stable.json"
        shutil.copyfile(catalog, renamed)
        self.assert_refused(self.run_tool("check-evidence", "--catalog", str(renamed),
                                          "--evidence", str(self.write("e.json", evidence_document()))),
                            "not built for the stable lane")
        renamed = self.directory / "catalog-beta.json"
        shutil.copyfile(catalog, renamed)
        self.assert_refused(self.run_tool("check-evidence", "--catalog", str(renamed),
                                          "--evidence", str(self.write("e.json", evidence_document()))),
                            "channel must be one of edge, rc, stable")

    # ── inputs ───────────────────────────────────────────────────────────────

    def test_refuses_artifacts_outside_the_release_locations(self) -> None:
        payload = payload_document()
        payload["parts"][0]["url"] = payload["parts"][0]["url"].replace("github.com", "example.com")
        self.assert_refused(self.prepare(payload=payload), "not under an allowed release location")
        payload = payload_document()
        payload["url"] = f"{release_base('catalog-edge')}/{ZIP}"
        self.assert_refused(self.prepare(payload=payload), "immutable release tag")
        payload = payload_document()
        payload["url"] = payload["url"] + "?x=1"
        self.assert_refused(self.prepare(payload=payload), "plain https URL")

    def test_refuses_inconsistent_parts(self) -> None:
        payload = payload_document()
        payload["sizeBytes"] += 1
        self.assert_refused(self.prepare(payload=payload), "parts sum to")
        payload = payload_document()
        payload["parts"][1]["fileName"] = "other.zip"
        self.assert_refused(self.prepare(payload=payload), "must end with its fileName")

    def test_refuses_another_channel_or_image_name(self) -> None:
        result = self.run_tool("prepare", "--channel", "beta", "--image", IMAGE, "--payload", "x",
                               "--engine", "x", "--installer-data", "x", "--installer", "x", "--out", str(self.directory / "o"))
        self.assert_refused(result, "channel must be one of edge, rc, stable")
        with self.assertRaisesRegex(release.Refusal, "image must be"):
            release.check_image_name("omarchy-mac-edge-latest", "edge")

    def test_image_names_with_and_without_an_image_id(self) -> None:
        self.assertEqual(release.check_image_name(IMAGE, "edge", sha(PAYLOAD)), IMAGE)
        self.assertEqual(release.check_image_name(LEGACY_IMAGE, "edge", sha(PAYLOAD)), LEGACY_IMAGE)
        with self.assertRaisesRegex(release.Refusal, "names image id"):
            release.check_image_name(IMAGE, "edge", "a" * 64)
        for name in (f"{IMAGE}-0123456789ab", f"{LEGACY_IMAGE}-0123456789AB", f"{LEGACY_IMAGE}-0123456789a",
                     f"{LEGACY_IMAGE}-", "omarchy-mac-edge-2026.10.14", f" {IMAGE}", 7):
            with self.subTest(name), self.assertRaisesRegex(release.Refusal, "image must be"):
                release.check_image_name(name, "edge")
        with self.assertRaisesRegex(release.Refusal, "not built for the rc lane"):
            release.check_image_name(IMAGE, "rc")

    def test_rc_takes_an_rc_lane_image_named_from_its_tag(self) -> None:
        self.assertEqual(release.check_image_name(RC_IMAGE, "rc", sha(PAYLOAD)), RC_IMAGE)
        with self.assertRaisesRegex(release.Refusal, "not built for the rc lane"):
            release.check_image_name(IMAGE, "rc", sha(PAYLOAD))
        with self.assertRaisesRegex(release.Refusal, "image must be"):
            release.check_image_name(RC_IMAGE, "edge", sha(PAYLOAD))
        for name in ("omarchy-mac-4.5.0-r1-2026.10.28-0123456789ab", "omarchy-mac-4.5.0-rc2-r0-2026.10.28-0123456789ab",
                     "omarchy-mac-v4.5.0-rc2-r1-2026.10.28-0123456789ab", f"{RC_IMAGE}-0123456789ab"):
            with self.subTest(name), self.assertRaisesRegex(release.Refusal, "not built for the rc lane"):
                release.check_image_name(name, "rc")

    def stable_inputs(self, image: str = STABLE_IMAGE, recorded: str | None = STABLE_IMAGE,
                      zip_name: str = STABLE_ZIP) -> tuple[dict, dict, dict]:
        payload = artifact(image, zip_name, PAYLOAD)
        metadata = artifact(image, "installer_data.json", METADATA)
        evidence = evidence_document()
        if recorded is not None:
            evidence["image"] = recorded
        return payload, metadata, evidence

    def build_stable(self, payload: dict, metadata: dict, evidence: object, image: str = STABLE_IMAGE) -> dict:
        now = datetime.datetime(2026, 10, 28, tzinfo=datetime.timezone.utc)
        return release.build_catalog("stable", image, payload, engine_document(), metadata, evidence,
                                     installer_document(), now)

    def test_stable_takes_a_stable_lane_image_named_from_its_final_tag(self) -> None:
        self.assertEqual(release.check_image_name(STABLE_IMAGE, "stable", sha(PAYLOAD)), STABLE_IMAGE)
        for name in (IMAGE, RC_IMAGE, "omarchy-mac-v4.5.0-r1-2026.10.28-0123456789ab",
                     "omarchy-mac-4.5.0-r0-2026.10.28-0123456789ab", f"{STABLE_IMAGE}-0123456789ab"):
            with self.subTest(name), self.assertRaisesRegex(release.Refusal, "not built for the stable lane"):
                release.check_image_name(name, "stable")
        with self.assertRaisesRegex(release.Refusal, "not built for the rc lane"):
            release.check_image_name(STABLE_IMAGE, "rc")

    def test_a_final_tag_built_on_rc_goes_to_catalog_rc_and_never_to_stable(self) -> None:
        self.assertEqual(release.check_image_name(FINAL_ON_RC_IMAGE, "rc", sha(PAYLOAD)), FINAL_ON_RC_IMAGE)
        with self.assertRaisesRegex(release.Refusal, "not built for the stable lane"):
            release.check_image_name(FINAL_ON_RC_IMAGE, "stable")
        with self.assertRaisesRegex(release.Refusal, "never promoted"):
            release.check_promotion_source({"models": [{"evidenceRevision": FINAL_ON_RC_IMAGE}]})
        release.check_promotion_source({"models": [{"evidenceRevision": RC_IMAGE}, {"evidenceRevision": STABLE_IMAGE}]})
        payload, metadata, evidence = self.stable_inputs(recorded=FINAL_ON_RC_IMAGE, zip_name=RC_ZIP)
        with self.assertRaisesRegex(release.Refusal, "not built for the stable lane"):
            self.build_stable(payload, metadata, evidence, image=FINAL_ON_RC_IMAGE)

    def test_a_catalog_stable_entry_takes_the_stable_image_that_passed_acceptance(self) -> None:
        catalog = self.build_stable(*self.stable_inputs())
        self.assertEqual({model["evidenceRevision"] for model in catalog["models"]}, {STABLE_IMAGE})
        self.assertEqual({model["payloadArtifact"]["fileName"] for model in catalog["models"]}, {STABLE_ZIP})

    def test_another_lanes_payload_or_evidence_under_a_stable_name_is_refused(self) -> None:
        with self.assertRaisesRegex(release.Refusal, f"is not {STABLE_IMAGE}'s stable zip"):
            self.build_stable(*self.stable_inputs(zip_name=RC_ZIP))
        with self.assertRaisesRegex(release.Refusal, f"the VM evidence is for {RC_IMAGE}, not {STABLE_IMAGE}"):
            self.build_stable(*self.stable_inputs(recorded=RC_IMAGE))
        with self.assertRaisesRegex(release.Refusal, "the VM evidence names no image"):
            self.build_stable(*self.stable_inputs(recorded=None))
        payload, metadata, evidence = self.stable_inputs()
        with self.assertRaisesRegex(release.Refusal, f"is not in {STABLE_IMAGE}'s release"):
            self.build_stable(artifact(RC_IMAGE, STABLE_ZIP, PAYLOAD), metadata, evidence)

    def test_the_signing_job_holds_catalog_stable_to_its_image_again(self) -> None:
        payload, metadata, evidence = self.stable_inputs()
        catalog = self.directory / "catalog-stable.json"
        catalog.write_bytes(release.catalog_bytes(self.build_stable(payload, metadata, evidence)))
        ok = self.run_tool("check-evidence", "--catalog", str(catalog), "--evidence", str(self.write("e.json", evidence)))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assert_refused(self.run_tool("check-evidence", "--catalog", str(catalog),
                                          "--evidence", str(self.write("e.json", dict(evidence_document(), image=RC_IMAGE)))),
                            f"the VM evidence is for {RC_IMAGE}, not {STABLE_IMAGE}")

    def rc_inputs(self, image: str = RC_IMAGE, recorded: str | None = RC_IMAGE) -> tuple[dict, dict, dict]:
        """The rc image's upload, as mac-upload.py reports it (its lane's zip in its own release), and its
        VM evidence, which records the image it tested (RECORDED, none when None)."""
        payload = artifact(image, RC_ZIP, PAYLOAD)
        metadata = artifact(image, "installer_data.json", METADATA)
        evidence = evidence_document()
        if recorded is not None:
            evidence["image"] = recorded
        return payload, metadata, evidence

    def build_rc(self, payload: dict, metadata: dict, evidence: object, image: str = RC_IMAGE) -> dict:
        now = datetime.datetime(2026, 10, 28, tzinfo=datetime.timezone.utc)
        return release.build_catalog("rc", image, payload, engine_document(), metadata, evidence,
                                     installer_document(), now)

    def test_a_catalog_rc_entry_takes_the_rc_image_that_passed_acceptance(self) -> None:
        catalog = self.build_rc(*self.rc_inputs())
        self.assertEqual({model["evidenceRevision"] for model in catalog["models"]}, {RC_IMAGE})
        self.assertEqual({model["payloadDigest"] for model in catalog["models"]}, {f"sha256:{sha(PAYLOAD)}"})
        self.assertEqual({model["payloadArtifact"]["fileName"] for model in catalog["models"]}, {RC_ZIP})
        payload, metadata, _ = self.rc_inputs()
        for evidence, message in ((None, "no VM evidence record"),
                                  (dict(evidence_document("e" * 64), image=RC_IMAGE), "not " + sha(PAYLOAD)),
                                  (dict(evidence_document(), result="fail", image=RC_IMAGE), "did not pass")):
            with self.subTest(message), self.assertRaisesRegex(release.Refusal, message):
                self.build_rc(payload, metadata, evidence)

    def test_an_edge_payload_under_an_rc_name_is_refused(self) -> None:
        # An edge upload and its passing evidence, offered as catalog-rc under an invented rc name.
        with self.assertRaisesRegex(release.Refusal, "the VM evidence names no image"):
            self.build_rc(payload_document(), artifact(IMAGE, "installer_data.json", METADATA), evidence_document())
        with self.assertRaisesRegex(release.Refusal, f"the VM evidence is for {IMAGE}, not {RC_IMAGE}"):
            self.build_rc(payload_document(), artifact(IMAGE, "installer_data.json", METADATA),
                          dict(evidence_document(), image=IMAGE))
        # Even with evidence that names the rc image, an edge zip is not the rc image's.
        with self.assertRaisesRegex(release.Refusal, f"is not {RC_IMAGE}'s rc zip"):
            self.build_rc(artifact(RC_IMAGE, EDGE_ZIP, PAYLOAD), artifact(RC_IMAGE, "installer_data.json", METADATA),
                          dict(evidence_document(), image=RC_IMAGE))
        with self.assertRaisesRegex(release.Refusal, f"is not in {RC_IMAGE}'s release"):
            self.build_rc(artifact(IMAGE, RC_ZIP, PAYLOAD), artifact(RC_IMAGE, "installer_data.json", METADATA),
                          dict(evidence_document(), image=RC_IMAGE))

    def test_a_recorded_name_that_is_not_the_catalogs_is_refused(self) -> None:
        respin = RC_IMAGE.replace("-r1-", "-r2-")
        payload, metadata, evidence = self.rc_inputs(recorded=respin)
        with self.assertRaisesRegex(release.Refusal, f"the VM evidence is for {respin}, not {RC_IMAGE}"):
            self.build_rc(payload, metadata, evidence)
        # The rc zip of another day under this name.
        payload, metadata, evidence = self.rc_inputs()
        other_day = artifact(RC_IMAGE, RC_ZIP.replace("2026.10.28", "2026.10.27"), PAYLOAD)
        with self.assertRaisesRegex(release.Refusal, f"is not {RC_IMAGE}'s rc zip"):
            self.build_rc(other_day, metadata, evidence)
        # Its parts and installer data stay in its release too.
        payload, metadata, evidence = self.rc_inputs()
        with self.assertRaisesRegex(release.Refusal, f"is not in {RC_IMAGE}'s release"):
            self.build_rc(payload, artifact(IMAGE, "installer_data.json", METADATA), evidence)

    def test_the_signing_job_holds_catalog_rc_to_its_image_again(self) -> None:
        payload, metadata, evidence = self.rc_inputs()
        catalog = self.directory / "catalog-rc.json"
        catalog.write_bytes(release.catalog_bytes(self.build_rc(payload, metadata, evidence)))
        ok = self.run_tool("check-evidence", "--catalog", str(catalog), "--evidence", str(self.write("e.json", evidence)))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        for other, message in ((evidence_document(), "the VM evidence names no image"),
                               (dict(evidence_document(), image=IMAGE), f"the VM evidence is for {IMAGE}, not {RC_IMAGE}")):
            with self.subTest(message):
                self.assert_refused(self.run_tool("check-evidence", "--catalog", str(catalog),
                                                  "--evidence", str(self.write("e.json", other))), message)

    def test_a_catalog_names_its_image_and_still_takes_an_older_name(self) -> None:
        now = datetime.datetime(2026, 10, 14, tzinfo=datetime.timezone.utc)
        metadata = artifact(IMAGE, "installer_data.json", METADATA)

        def build(image: str, payload: dict) -> dict:
            return release.build_catalog("edge", image, payload, engine_document(), metadata, evidence_document(),
                                         installer_document(), now)

        for image in (IMAGE, LEGACY_IMAGE):
            with self.subTest(image):
                catalog = build(image, payload_document())
                self.assertEqual({model["evidenceRevision"] for model in catalog["models"]}, {image})
        # Another build's payload (a builder fix on the same packages and day) is not this image.
        other = artifact(IMAGE, ZIP, b"a rebuilt payload")
        with self.assertRaisesRegex(release.Refusal, "names image id"):
            build(IMAGE, other)

    def test_only_an_untested_mac_carries_a_preview_note(self) -> None:
        note = "Untested: qualified on edge next"
        untested = release.supported_models.untested
        release.supported_models.untested = lambda: {"apple,j700": note}
        self.addCleanup(setattr, release.supported_models, "untested", untested)
        catalog = release.build_catalog(
            "edge", IMAGE, payload_document(), engine_document(),
            artifact(IMAGE, "installer_data.json", METADATA), evidence_document(), installer_document(),
            datetime.datetime(2026, 10, 14, tzinfo=datetime.timezone.utc))
        for model in catalog["models"]:
            with self.subTest(model["deviceIdentifier"]):
                self.assertEqual(model["status"], "enabled")
                if model["deviceIdentifier"] == "apple,j700":
                    self.assertEqual(model["previewNote"], note)
                else:
                    self.assertNotIn("previewNote", model)

    def untested_mac(self, identifier: str = "apple,j700") -> str:
        note = "Untested: qualified on edge next"
        untested = release.supported_models.untested
        release.supported_models.untested = lambda: {identifier: note}
        self.addCleanup(setattr, release.supported_models, "untested", untested)
        return note

    def edge_catalog(self) -> dict:
        return release.build_catalog(
            "edge", IMAGE, payload_document(), engine_document(),
            artifact(IMAGE, "installer_data.json", METADATA), evidence_document(), installer_document(),
            datetime.datetime(2026, 10, 14, tzinfo=datetime.timezone.utc))

    def test_rc_and_stable_leave_out_an_untested_mac(self) -> None:
        self.untested_mac()
        supported, _ = release.supported_models.load()
        catalog = self.build_rc(*self.rc_inputs())
        identifiers = [model["deviceIdentifier"] for model in catalog["models"]]
        self.assertNotIn("apple,j700", identifiers)
        self.assertEqual(identifiers, [identifier for identifier in supported if identifier != "apple,j700"])
        self.assertFalse(any("previewNote" in model for model in catalog["models"]))
        self.assertIn("apple,j700", [model["deviceIdentifier"] for model in self.edge_catalog()["models"]])

    def test_an_rc_or_stable_catalog_with_an_untested_or_preview_mac_is_refused(self) -> None:
        note = self.untested_mac()
        edge = self.edge_catalog()
        for channel in ("rc", "stable"):
            with self.subTest(channel):
                errors = release.supported_models.catalog_errors(edge, channel)
                self.assertIn(f"apple,j700 is untested: {channel} offers only Macs installed on real hardware", errors)
                self.assertIn(f"apple,j700 carries a preview note: {channel} offers no preview Mac", errors)
        self.assertEqual(release.supported_models.catalog_errors(edge, "edge"), [])
        self.assertEqual(release.supported_models.catalog_errors(edge), [])
        self.assertTrue(note)

    def test_the_envelope_check_takes_the_catalog_s_channel(self) -> None:
        self.untested_mac()
        rc = self.write("catalog-rc.json", self.build_rc(*self.rc_inputs()))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(release.supported_models.main(["check-catalog", str(rc), "rc"]), 0)
            self.assertEqual(release.supported_models.main(["check-catalog", str(rc)]), 1)
            edge = self.write("catalog-edge.json", self.edge_catalog())
            self.assertEqual(release.supported_models.main(["check-catalog", str(edge), "stable"]), 1)

    def test_a_catalog_with_an_untested_mac_needs_an_app_that_can_finish_its_install(self) -> None:
        # An app before 2.3.17 partitions an M4 and then refuses its Recovery payload inventory.
        catalog = self.edge_catalog()
        self.assertTrue(any("previewNote" in model for model in catalog["models"]))
        self.assertEqual(release.supported_models.catalog_errors(catalog, "edge"), [])
        for minimum in ("2.3.16", "2.1.0", "2.3", None):
            with self.subTest(minimum):
                older = dict(catalog, installer=dict(catalog["installer"], minimumVersion=minimum))
                self.assertIn("a catalog offering an untested Mac needs installer.minimumVersion 2.3.17 or later, "
                              "the first app that can finish an install on one",
                              release.supported_models.catalog_errors(older, "edge"))
        # With no untested Mac in the manifest, the same catalog without its notes needs no floor.
        untested = release.supported_models.untested
        release.supported_models.untested = lambda: {}
        self.addCleanup(setattr, release.supported_models, "untested", untested)
        tested = dict(catalog, installer=dict(catalog["installer"], minimumVersion="2.1.0"),
                      models=[{k: v for k, v in model.items() if k != "previewNote"} for model in catalog["models"]])
        self.assertFalse(any("needs installer.minimumVersion" in error
                             for error in release.supported_models.catalog_errors(tested, "edge")))

    def test_an_untested_mac_without_its_note_still_needs_the_floor_and_the_note(self) -> None:
        # A hand-made catalog (make-unsigned-catalog.py) enabling an M4 with no previewNote
        # must not let an older app partition it.
        catalog = self.edge_catalog()
        held = release.supported_models.untested()
        self.assertTrue(held)
        bare = dict(catalog, installer=dict(catalog["installer"], minimumVersion="2.3.0"),
                    models=[{k: v for k, v in model.items() if k != "previewNote"} for model in catalog["models"]])
        for channel in (None, "edge"):
            with self.subTest(channel):
                errors = release.supported_models.catalog_errors(bare, channel)
                self.assertIn("a catalog offering an untested Mac needs installer.minimumVersion 2.3.17 or later, "
                              "the first app that can finish an install on one", errors)
                for identifier in held:
                    self.assertIn(f"{identifier} is untested but carries no preview note: "
                                  "a catalog shows each untested Mac its note", errors)

    def test_a_developer_board_is_named_as_one_in_any_channel_catalog(self) -> None:
        developer = release.supported_models.developer_boards
        catalog = self.edge_catalog()
        release.supported_models.developer_boards = lambda: {"apple,j804": "M6 Air, developer catalog only"}
        self.addCleanup(setattr, release.supported_models, "developer_boards", developer)
        catalog["models"].append(dict(catalog["models"][0], deviceIdentifier="apple,j804"))
        for channel in (None, "edge", "rc", "stable"):
            with self.subTest(channel):
                self.assertIn("apple,j804 is a developer board: only a developer build's sealed catalog offers it",
                              release.supported_models.catalog_errors(catalog, channel))

    # ── signing ──────────────────────────────────────────────────────────────

    def test_refuses_a_key_that_is_not_the_trust_root(self) -> None:
        other = self.directory / "other"
        other.mkdir()
        other_key, _ = generate_key(other)
        self.assert_refused(self.sign(self.built_catalog(), key=other_key), "does not match the trust root")
        self.assertFalse((self.directory / "signed").exists())

    def test_refuses_a_malformed_or_missing_key(self) -> None:
        catalog = self.built_catalog()
        self.assert_refused(self.sign(catalog, key="not-a-key"), "is not base64")
        # 32 characters in the variable are base64 text, never a raw key.
        self.assert_refused(self.sign(catalog, key="A" * 32), "not a 32-byte")
        self.assert_refused(self.sign(catalog, key=base64.b64encode(b"short").decode()), "not a 32-byte")
        self.assert_refused(self.sign(catalog, key=""), "empty or unset")

    def test_the_key_file_form_signs_the_same_bytes(self) -> None:
        catalog = self.built_catalog()
        key_file = self.directory / "key.raw"
        key_file.write_bytes(base64.b64decode(self.key))
        result = self.run_tool("sign", "--key-file", str(key_file), "--catalog", str(catalog),
                               "--trust-root", str(self.trust_root), "--out", str(self.directory / "from-file"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sign(catalog).returncode, 0)
        # Ed25519 is deterministic: one key, one catalog, one signature.
        self.assertEqual((self.directory / "from-file" / "catalog-edge.signed.json").read_bytes(),
                         (self.directory / "signed" / "catalog-edge.signed.json").read_bytes())

    # ── hosts and the dry run ────────────────────────────────────────────────

    def test_a_dry_run_touches_nothing_but_scratch(self) -> None:
        """The workflow's dry run: prepare, sign, publish to a scratch host."""
        scratch = self.directory / "scratch-host"
        result = self.prepare("default", None, "out", "--check-live", "--host", f"scratch:{scratch}",
                              "--trust-root", str(self.trust_root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sign(self.directory / "out" / "catalog-edge.json").returncode, 0)
        envelope = self.directory / "signed" / "catalog-edge.signed.json"
        published = self.run_tool("publish", "--channel", "edge", "--envelope", str(envelope),
                                  "--host", f"scratch:{scratch}", "--trust-root", str(self.trust_root))
        self.assertEqual(published.returncode, 0, published.stderr)
        self.assertEqual((scratch / "catalog-edge.signed.json").read_bytes(), envelope.read_bytes())
        self.assertEqual(self.calls.read_text(), "", "a dry run must not call any host tool")

    def test_publish_refuses_a_sequence_that_does_not_move_forward(self) -> None:
        scratch = self.directory / "scratch-host"

        def publish(envelope: Path) -> subprocess.CompletedProcess:
            return self.run_tool("publish", "--channel", "edge", "--envelope", str(envelope),
                                 "--host", f"scratch:{scratch}", "--trust-root", str(self.trust_root))

        newer = self.signed_at(NOW, "newer")
        older = self.signed_at("2026-10-13T00:00:00Z", "older")
        self.assertEqual(publish(newer).returncode, 0)
        self.assert_refused(publish(older), f"does not follow the published {SEQUENCE}")
        self.assertEqual((scratch / "catalog-edge.signed.json").read_bytes(), newer.read_bytes())
        (scratch / "catalog-edge.signed.json").write_text("{}")
        self.assert_refused(publish(newer), "does not verify")

    def publish_to(self, envelope: Path, *hosts: Path) -> subprocess.CompletedProcess:
        return self.run_tool("publish", "--channel", "edge", "--envelope", str(envelope),
                             "--host", ",".join(f"scratch:{host}" for host in hosts), "--trust-root", str(self.trust_root))

    def test_a_catalog_goes_to_every_host_in_order(self) -> None:
        base, mirror = self.directory / "base", self.directory / "mirror"
        envelope = self.signed_at(NOW, "both")
        result = self.publish_to(envelope, base, mirror)
        self.assertEqual(result.returncode, 0, result.stderr)
        for host in (base, mirror):
            self.assertEqual((host / "catalog-edge.signed.json").read_bytes(), envelope.read_bytes())
        published = [line for line in result.stdout.splitlines() if line.startswith("published=")]
        self.assertEqual(published, [f"published={base}/catalog-edge.signed.json", f"published={mirror}/catalog-edge.signed.json"])

    def test_every_host_is_checked_before_any_is_written(self) -> None:
        base, mirror = self.directory / "base", self.directory / "mirror"
        older, newer = self.signed_at("2026-10-13T00:00:00Z", "older"), self.signed_at(NOW, "newer")
        self.assertEqual(self.publish_to(newer, mirror).returncode, 0)
        self.assert_refused(self.publish_to(older, base, mirror), f"does not follow the published {SEQUENCE} at {mirror}")
        self.assertFalse((base / "catalog-edge.signed.json").exists(), "the base must not get ahead of a refused mirror")

    def test_a_host_already_serving_the_catalog_is_skipped_and_the_next_still_published(self) -> None:
        base, mirror = self.directory / "base", self.directory / "mirror"
        envelope = self.signed_at(NOW, "retry")
        self.assertEqual(self.publish_to(envelope, base).returncode, 0)
        result = self.publish_to(envelope, base, mirror)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"published={base}/catalog-edge.signed.json (already live)", result.stdout)
        self.assertEqual((mirror / "catalog-edge.signed.json").read_bytes(), envelope.read_bytes())

    def test_a_failed_first_host_writes_no_mirror(self) -> None:
        base, mirror = self.directory / "base", self.directory / "mirror"
        base.write_text("not a directory: the base host's write fails")
        result = self.publish_to(self.signed_at(NOW, "first-fails"), base, mirror)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((mirror / "catalog-edge.signed.json").exists(), "the mirror must never get ahead of the base")

    def test_a_failed_mirror_resumes_without_rewriting_the_base(self) -> None:
        base, mirror = self.directory / "base", self.directory / "mirror"
        envelope = self.signed_at(NOW, "mirror-fails")
        mirror.write_text("not a directory: the mirror's write fails")
        self.assertNotEqual(self.publish_to(envelope, base, mirror).returncode, 0)
        self.assertEqual((base / "catalog-edge.signed.json").read_bytes(), envelope.read_bytes())
        written = (base / "catalog-edge.signed.json").stat().st_mtime_ns
        mirror.unlink()
        result = self.publish_to(envelope, base, mirror)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"published={base}/catalog-edge.signed.json (already live)", result.stdout)
        self.assertEqual((base / "catalog-edge.signed.json").stat().st_mtime_ns, written)
        self.assertEqual((mirror / "catalog-edge.signed.json").read_bytes(), envelope.read_bytes())

    def test_a_host_list_names_each_host_once(self) -> None:
        for spec in ("github-release,github-release", "r2,", "", "s3"):
            with self.subTest(spec):
                with self.assertRaises(release.Refusal):
                    release.catalog_hosts(spec)

    def fake_github(self) -> Path:
        """A gh stand-in holding one repository's releases in a directory."""
        state = self.directory / "github"
        (state / "assets").mkdir(parents=True)
        gh = self.bin / "gh"
        gh.write_text(f"""#!{sys.executable}
import json, os, shutil, sys
from pathlib import Path
state = Path({str(state)!r})
arguments = sys.argv[1:]
with open({str(self.calls)!r}, "a") as log:
    log.write("gh " + " ".join(arguments) + "\\n")
release = state / "release.json"
def load():
    return json.loads(release.read_text())
def save(document):
    release.write_text(json.dumps(document))
if arguments[:1] == ["api"] and "/releases/tags/" in arguments[1]:
    if not release.exists():
        sys.stderr.write("gh: Not Found (HTTP 404)\\n")
        sys.exit(1)
    print(release.read_text())
elif arguments[:2] == ["release", "create"]:
    save({{"draft": False, "prerelease": True, "assets": []}})
elif arguments[:2] == ["release", "upload"]:
    if (state / "upload-fails").exists():
        sys.exit(1)
    source = Path(arguments[3])
    document = load()
    identifier = 1 + max([asset["id"] for asset in document["assets"]] + [0])
    document["assets"].append({{"id": identifier, "name": source.name}})
    shutil.copyfile(source, state / "assets" / source.name)
    save(document)
elif arguments[:3] == ["api", "-X", "PATCH"] and (state / "rename-fails").exists():
    sys.exit(1)
elif arguments[:3] in (["api", "-X", "DELETE"], ["api", "-X", "PATCH"]):
    identifier = int(arguments[3].rsplit("/", 1)[1])
    document = load()
    asset = next(asset for asset in document["assets"] if asset["id"] == identifier)
    if arguments[2] == "DELETE":
        document["assets"].remove(asset)
        (state / "assets" / asset["name"]).unlink()
    else:
        name = arguments[5].split("=", 1)[1]
        os.replace(state / "assets" / asset["name"], state / "assets" / name)
        asset["name"] = name
    save(document)
else:
    sys.exit(97)
""")
        return state

    def publish_to_fake_github(self, envelope: Path, state: Path, hosts: str = "github-release") -> tuple[int, str]:
        host = release.GitHubReleaseHost(release.stopgap_repo())
        served = state / "assets" / "catalog-edge.signed.json"

        def fake_open(url, timeout=60, headers=None):
            self.assertEqual(url, host.describe("edge"))
            if not served.exists():
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
            return contextlib.closing(io.BytesIO(served.read_bytes()))

        arguments = ["publish", "--channel", "edge", "--envelope", str(envelope), "--host", hosts,
                     "--trust-root", str(self.trust_root)]
        saved = release.open_url, dict(os.environ)
        release.open_url = fake_open
        os.environ.update(PATH=self.environment["PATH"], OMARCHY_OPENSSL=OPENSSL)
        os.environ.pop("GITHUB_REPOSITORY", None)
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                status = release.main(arguments)
            return status, output.getvalue()
        finally:
            release.open_url = saved[0]
            os.environ.clear()
            os.environ.update(saved[1])

    def test_the_github_release_host_swaps_the_catalog_in(self) -> None:
        state = self.fake_github()
        served = state / "assets" / "catalog-edge.signed.json"
        first = self.signed_at("2026-10-14T00:00:00Z", "first")
        status, output = self.publish_to_fake_github(first, state)
        self.assertEqual(status, 0, output)
        calls = self.calls.read_text().splitlines()
        self.assertTrue(any(c.startswith("gh release create catalog-edge") and "--prerelease --latest=false" in c
                            for c in calls), calls)
        self.assertFalse(any("--clobber" in c for c in calls))
        self.assertEqual(served.read_bytes(), first.read_bytes())

        # A retry of the same envelope is a no-op, not a sequence refusal.
        status, output = self.publish_to_fake_github(first, state)
        self.assertEqual(status, 0, output)
        self.assertIn("already live", output)

        # A failed upload leaves the published catalog in place.
        second = self.signed_at("2026-10-15T00:00:00Z", "second")
        (state / "upload-fails").touch()
        status, output = self.publish_to_fake_github(second, state)
        self.assertEqual(status, 1, output)
        self.assertEqual(served.read_bytes(), first.read_bytes())
        (state / "upload-fails").unlink()
        status, output = self.publish_to_fake_github(second, state)
        self.assertEqual(status, 0, output)
        self.assertEqual(served.read_bytes(), second.read_bytes())
        self.assertEqual(sorted(p.name for p in (state / "assets").iterdir()), ["catalog-edge.signed.json"])

    def test_a_failed_swap_blocks_every_later_publish(self) -> None:
        state = self.fake_github()
        saved_sleep = release.time.sleep
        release.time.sleep = lambda seconds: None
        try:
            self.assertEqual(self.publish_to_fake_github(self.signed_at("2026-10-14T00:00:00Z", "a"), state)[0], 0)
            newer = self.signed_at("2026-10-16T00:00:00Z", "c")
            (state / "rename-fails").touch()
            status, output = self.publish_to_fake_github(newer, state)
            self.assertEqual(status, 1, output)
            self.assertIn("rename it by hand", output)
            (state / "rename-fails").unlink()
            # The channel now serves nothing; an older envelope must not slip in.
            status, output = self.publish_to_fake_github(self.signed_at("2026-10-15T00:00:00Z", "b"), state)
            self.assertEqual(status, 1, output)
            self.assertIn("left catalog-edge.signed.json.next", output)
            self.assertEqual((state / "assets" / "catalog-edge.signed.json.next").read_bytes(), newer.read_bytes())
        finally:
            release.time.sleep = saved_sleep

    def test_a_staged_leftover_on_the_stopgap_holds_back_the_base_too(self) -> None:
        state = self.fake_github()
        base = self.directory / "base"
        saved_sleep = release.time.sleep
        release.time.sleep = lambda seconds: None
        try:
            self.assertEqual(self.publish_to_fake_github(self.signed_at("2026-10-14T00:00:00Z", "a"), state)[0], 0)
            (state / "rename-fails").touch()
            self.assertEqual(self.publish_to_fake_github(self.signed_at("2026-10-16T00:00:00Z", "c"), state)[0], 1)
            (state / "rename-fails").unlink()
            status, output = self.publish_to_fake_github(self.signed_at("2026-10-17T00:00:00Z", "d"), state,
                                                         f"scratch:{base},github-release")
            self.assertEqual(status, 1, output)
            self.assertIn("left catalog-edge.signed.json.next", output)
            self.assertFalse((base / "catalog-edge.signed.json").exists())
        finally:
            release.time.sleep = saved_sleep

    def test_a_draft_stopgap_release_holds_back_the_base_too(self) -> None:
        state = self.fake_github()
        base = self.directory / "base"
        (state / "release.json").write_text('{"draft": true, "prerelease": true, "assets": []}')
        status, output = self.publish_to_fake_github(self.signed_at(NOW, "draft-mirror"), state, f"scratch:{base},github-release")
        self.assertEqual(status, 1)
        self.assertIn("must be a published prerelease", output)
        self.assertFalse((base / "catalog-edge.signed.json").exists())

    def test_the_github_release_host_refuses_a_draft(self) -> None:
        state = self.fake_github()
        (state / "release.json").write_text('{"draft": true, "prerelease": true, "assets": []}')
        status, output = self.publish_to_fake_github(self.signed_at(NOW, "draft"), state)
        self.assertEqual(status, 1)
        self.assertIn("must be a published prerelease", output)

    def test_only_this_repository_releases_catalogs(self) -> None:
        environment = dict(self.environment, GITHUB_REPOSITORY="someone/fork")
        result = self.run_tool("publish", "--channel", "edge", "--envelope", "x", "--host", "scratch:/nonexistent",
                               environment=environment)
        self.assert_refused(result, "not someone/fork")

    # ── remote verification ──────────────────────────────────────────────────

    def test_remote_verification_checks_every_part_and_the_whole(self) -> None:
        served = {part["url"]: data for part, data in zip(payload_document()["parts"], PAYLOAD_PARTS)}
        saved = release.open_url
        release.open_url = lambda url, timeout=60: contextlib.closing(io.BytesIO(served[url]))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                release.verify_remote_artifact(payload_document(), "payload")
            first = payload_document()["parts"][0]["url"]
            served[first] = served[first][:-1] + b"X"
            with self.assertRaisesRegex(release.Refusal, "not the pinned"):
                release.verify_remote_artifact(payload_document(), "payload")
            served[first] = PAYLOAD_PARTS[0] + b"extra"
            with self.assertRaisesRegex(release.Refusal, "more than its pinned"):
                release.verify_remote_artifact(payload_document(), "payload")
        finally:
            release.open_url = saved

    def test_remote_digest_resumes_a_stalled_read(self) -> None:
        data = bytes(range(256)) * 64
        requests = []

        class Stalling(io.BytesIO):
            status = 200

            def read(self, size=-1):
                if self.tell() >= 4096:
                    raise TimeoutError("stalled")
                return super().read(min(size, 1024))

        def serve(url, timeout=60, headers=None):
            requests.append(headers)
            if headers is None:
                return contextlib.closing(Stalling(data))
            body = io.BytesIO(data[int(headers["Range"][6:-1]):])
            body.status = 206
            return contextlib.closing(body)

        saved, sleep = release.open_url, release.time.sleep
        release.open_url, release.time.sleep = serve, lambda seconds: None
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                digest, size = release.remote_digest("https://example.test/p", len(data))
            self.assertEqual((digest, size), (hashlib.sha256(data).hexdigest(), len(data)))
            self.assertEqual(requests, [None, {"Range": "bytes=4096-"}])

            def ignore_range(url, timeout=60, headers=None):
                return contextlib.closing(Stalling(data))

            release.open_url = ignore_range
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(release.Refusal, "ignored a resume request"):
                    release.remote_digest("https://example.test/p", len(data))

            requests.clear()

            def close_early(url, timeout=60, headers=None):
                requests.append(headers)
                start = int(headers["Range"][6:-1]) if headers else 0
                body = io.BytesIO(data[start:] if headers else data[:3000])
                body.status = 206 if headers else 200
                return contextlib.closing(body)

            release.open_url = close_early
            with contextlib.redirect_stderr(io.StringIO()):
                digest, size = release.remote_digest("https://example.test/p", len(data))
            self.assertEqual((digest, size), (hashlib.sha256(data).hexdigest(), len(data)))
            self.assertEqual(requests, [None, {"Range": "bytes=3000-"}])

            class StallAfterEnd(io.BytesIO):
                status = 206

                def read(self, size=-1):
                    block = super().read(size)
                    if not block:
                        raise TimeoutError("stalled before the end of the body")
                    return block

            def always_stall(url, timeout=60, headers=None):
                start = int(headers["Range"][6:-1]) if headers else 0
                stop = len(data) if len(requests) + 1 >= release.REMOTE_READ_ATTEMPTS else start + 512
                requests.append(headers)
                body = StallAfterEnd(data[start:stop])
                body.status = 206 if headers else 200
                return contextlib.closing(body)

            requests.clear()
            release.open_url = always_stall
            with contextlib.redirect_stderr(io.StringIO()):
                digest, size = release.remote_digest("https://example.test/p", len(data))
            self.assertEqual((digest, size), (hashlib.sha256(data).hexdigest(), len(data)))
            self.assertEqual(len(requests), release.REMOTE_READ_ATTEMPTS)
        finally:
            release.open_url, release.time.sleep = saved, sleep

    def test_remote_verification_reads_the_app_against_its_sidecar(self) -> None:
        base = release.identity()["INSTALLER_CATALOG_BASE"]
        url = f"{base}/installer/2.4.0/App-2.4.0.dmg"
        dmg = b"notarized disk image"
        served = {url: dmg, f"{url}.sha256": f"{hashlib.sha256(dmg).hexdigest()}  App-2.4.0.dmg\n".encode()}

        def fake_open(requested, timeout=60, headers=None):
            if requested not in served:
                raise urllib.error.HTTPError(requested, 404, "Not Found", {}, io.BytesIO())
            return contextlib.closing(io.BytesIO(served[requested]))
        saved = release.open_url
        release.open_url = fake_open
        self.addCleanup(setattr, release, "open_url", saved)
        installer = {"minimumVersion": "2.4.0", "latestVersion": "2.4.0", "downloadURL": url}
        with contextlib.redirect_stdout(io.StringIO()):
            release.verify_remote_installer(installer)
        served[url] = b"tampered"
        with self.assertRaisesRegex(release.Refusal, "not its .sha256's"):
            release.verify_remote_installer(installer)
        served[f"{url}.sha256"] = b"%s  Other.dmg\n" % hashlib.sha256(dmg).hexdigest().encode()
        with self.assertRaisesRegex(release.Refusal, "does not read"):
            release.verify_remote_installer(installer)
        del served[f"{url}.sha256"]
        with self.assertRaisesRegex(release.Refusal, "answers HTTP 404; mirror the app first"):
            release.verify_remote_installer(installer)

    # ── the workflows ────────────────────────────────────────────────────────

    def test_the_key_is_read_in_one_approved_step_of_one_workflow(self) -> None:
        texts = {path.name: path.read_text() for path in sorted(WORKFLOWS.glob("*.y*ml"))}
        # The release pipeline only passes the key on to mac-catalog.yml by name
        # (test_release_pipeline.py checks it passes nothing else, to nothing else).
        self.assertEqual([name for name, text in texts.items() if "CATALOG_SIGNING_KEY }}" in text],
                         ["mac-catalog-sign.yml", "mac-release-pipeline.yml"])
        self.assertEqual([name for name, text in texts.items() if "environment: catalog-release" in text],
                         ["mac-catalog-sign.yml"])
        text = texts["mac-catalog-sign.yml"]
        self.assertEqual(text.count("secrets.CATALOG_SIGNING_KEY"), 1)
        self.assertEqual(text.count("environment: catalog-release"), 1)
        # The key and the R2 write key never share a job.
        publish_job = text.split("\n  publish:\n")[1]
        self.assertNotIn("CATALOG_SIGNING_KEY", publish_job)
        self.assertEqual(sorted({line.split("secrets.")[1].split()[0] for line in publish_job.splitlines() if "secrets." in line}),
                         ["R2_ACCESS_KEY_ID", "R2_ENDPOINT", "R2_SECRET_ACCESS_KEY"])
        self.assertIn("on:\n  workflow_call:\n", text)
        self.assertNotIn("workflow_dispatch", text)
        sign_job = text.split("\n  sign:\n")[1].split("\n  publish:\n")[0]
        self.assertIn("environment: catalog-release", sign_job)
        self.assertNotIn("R2_", sign_job)
        self.assertIn("secrets.CATALOG_SIGNING_KEY", sign_job)
        self.assertIn("contents: read", sign_job)
        self.assertIn("github.repository == 'omacom/omarchy-mac-installer'", sign_job)
        self.assertIn("github.ref == 'refs/heads/main'", sign_job)
        # Each gate runs only for its own channels, before the key is read.
        gate_step = sign_job.split("Check the catalog")[1].split("- name: Sign")[0]
        self.assertIn("evidence:edge | evidence:rc | evidence:stable)", gate_step)
        self.assertIn("promotion:rc | promotion:stable)", gate_step)
        publish_job = text.split("\n  publish:\n")[1]
        self.assertIn("needs.sign.result == 'success' && !inputs.dry-run", publish_job)
        self.assertNotIn("secrets.CATALOG_SIGNING_KEY", publish_job)

    def test_both_catalog_writers_sign_through_the_shared_job(self) -> None:
        for workflow, gate in ((WORKFLOW, "evidence"), (PROMOTE_WORKFLOW, "promotion")):
            text = workflow.read_text()
            self.assertNotIn("pull_request", text)
            self.assertNotIn("secrets.", text)
            release_job = text.split("\n  release:\n")[1]
            self.assertIn("uses: ./.github/workflows/mac-catalog-sign.yml", release_job)
            self.assertIn(f"gate: {gate}", release_job)
            self.assertIn("dry-run: ${{ inputs.dry-run }}", release_job)
            self.assertIn("secrets: inherit", release_job)

    def test_promotion_is_parked_behind_approval_and_an_explicit_input(self) -> None:
        text = PROMOTE_WORKFLOW.read_text()
        self.assertIn("PARKED", text.split("\non:\n")[0])
        triggers = text.split("\non:\n")[1].split("\npermissions:")[0]
        self.assertIn("  workflow_dispatch:\n", triggers)
        for trigger in ("push:", "schedule:", "workflow_call:", "repository_dispatch:", "pull_request"):
            self.assertNotIn(trigger, triggers)
        dry_run = triggers.split("      dry-run:\n")[1].split("\n      confirm:")[0]
        self.assertIn("default: true", dry_run)
        self.assertIn("default: ''", triggers.split("      confirm:\n")[1])
        approve = text.split("\n  approve:\n")[1].split("\n  release:\n")[0]
        self.assertIn("environment: promote", approve)
        self.assertIn('promote-$CHANNEL', approve)
        self.assertIn("needs: [prepare, approve]", text.split("\n  release:\n")[1])
        prepare = text.split("\n  prepare:\n")[1].split("\n  approve:\n")[0]
        self.assertIn("--real --confirm", prepare)
        self.assertIn("--verify-remote --check-live", prepare)


LOCKED_ENGINE = "sha256:" + json.loads((ROOT / "Engine" / "source-lock.json").read_text())["validation_artifact"]["sha256"]
HELD_ENGINE = "sha256:" + "5" * 64


@unittest.skipIf(OPENSSL is None, "OpenSSL 3 is required")
class EngineGateTests(ReleaseTestCase):
    """2.3.16 shipped while catalog-rc and catalog-stable still pinned engine
    20261022: the app installs with the catalog's engine, so rc and stable
    must pin Engine/source-lock.json's unless the owner allows another."""

    def catalog(self, channel: str, digest: object = LOCKED_ENGINE, image: str = IMAGE) -> Path:
        document = json.loads(self.built_catalog().read_text())
        for model in document["models"]:
            model["engineDigest"] = digest
            model["evidenceRevision"] = image
        return self.write(f"catalog-{channel}.json", document)

    def check(self, catalog: Path, allowed: str | None = None) -> subprocess.CompletedProcess:
        extra = ("--allow-engine-digest", allowed) if allowed is not None else ()
        return self.run_tool("check-engine", "--catalog", str(catalog), *extra)

    def test_rc_and_stable_take_the_locked_engine(self) -> None:
        for channel in ("rc", "stable"):
            with self.subTest(channel):
                result = self.check(self.catalog(channel))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"engine_gate=lock channel={channel} engine={LOCKED_ENGINE}", result.stdout)

    def test_rc_and_stable_refuse_another_engine_without_the_owners_digest(self) -> None:
        for channel in ("rc", "stable"):
            with self.subTest(channel):
                result = self.check(self.catalog(channel, HELD_ENGINE))
                self.assert_refused(result, f"catalog-{channel} pins engine {HELD_ENGINE}, but Engine/source-lock.json pins {LOCKED_ENGINE}")
                self.assertIn(f"allow-engine-digest={HELD_ENGINE}", result.stderr)
                self.assert_refused(self.check(self.catalog(channel, HELD_ENGINE), ""), "regenerate the catalog")

    def test_the_owners_digest_admits_exactly_that_engine(self) -> None:
        result = self.check(self.catalog("rc", HELD_ENGINE), HELD_ENGINE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"engine_gate=held channel=rc engine={HELD_ENGINE} lock={LOCKED_ENGINE}", result.stdout)
        self.assert_refused(self.check(self.catalog("stable", HELD_ENGINE), "sha256:" + "6" * 64),
                            f"pins engine {HELD_ENGINE}, not the allowed sha256:{'6' * 64}")
        # Naming the locked digest itself is redundant, not a hold.
        same = self.check(self.catalog("stable"), LOCKED_ENGINE)
        self.assertEqual(same.returncode, 0, same.stderr)
        self.assertIn("engine_gate=lock", same.stdout)
        self.assert_refused(self.check(self.catalog("stable"), HELD_ENGINE), "not the allowed")

    def test_a_malformed_allowed_digest_is_refused(self) -> None:
        for allowed in ("5" * 64, "sha256:" + "5" * 63, "sha256:" + "A" * 64, f" {HELD_ENGINE}", f"{HELD_ENGINE}\n"):
            with self.subTest(allowed):
                self.assert_refused(self.check(self.catalog("rc", HELD_ENGINE), allowed), "allow-engine-digest must be")

    def test_every_model_must_pin_one_well_formed_engine(self) -> None:
        for channel in ("edge", "rc"):
            document = json.loads(self.catalog(channel).read_text())
            mixed = json.loads(json.dumps(document))
            mixed["models"][0]["engineDigest"] = HELD_ENGINE
            missing = json.loads(json.dumps(document))
            del missing["models"][-1]["engineDigest"]
            for name, broken, reason in (
                ("mixed", mixed, "pin different engines"),
                ("missing", missing, "missing or malformed"),
                ("non-string", dict(document, models=[dict(m, engineDigest=[1]) for m in document["models"]]), "malformed"),
                ("bare hex", dict(document, models=[dict(m, engineDigest="5" * 64) for m in document["models"]]), "malformed"),
                ("no models", dict(document, models=[]), "lists no models"),
            ):
                with self.subTest(channel=channel, case=name):
                    self.assert_refused(self.check(self.write(f"catalog-{channel}.json", broken), HELD_ENGINE
                                                   if channel == "rc" else None), reason)

    def test_edge_is_exempt_and_takes_no_allowed_digest(self) -> None:
        result = self.check(self.catalog("edge", HELD_ENGINE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"engine_gate=exempt channel=edge engine={HELD_ENGINE}", result.stdout)
        self.assert_refused(self.check(self.catalog("edge", HELD_ENGINE), HELD_ENGINE), "is for rc and stable")
        self.assert_refused(self.check(self.write("catalog-beta.json", {"models": []})), "channel must be one of")

    def test_an_unreadable_lock_refuses_even_with_an_allowed_digest(self) -> None:
        saved = release.SOURCE_LOCK
        lock = self.directory / "source-lock.json"
        try:
            release.SOURCE_LOCK = lock
            for text in (None, "{}", '{"validation_artifact": {"sha256": "short"}}', "not json"):
                if text is not None:
                    lock.write_text(text)
                with self.subTest(text), self.assertRaisesRegex(release.Refusal, "pins no readable"):
                    release.check_engine_pin("rc", {"models": [{"engineDigest": HELD_ENGINE}]}, HELD_ENGINE)
        finally:
            release.SOURCE_LOCK = saved

    def test_an_allowed_engine_never_waives_the_final_tag_refusal(self) -> None:
        # The held rc catalog's image is a final tag built on rc; promoting it to stable stays refused in
        # prepare and in the signing job's promotion gate, though the engine gate passes with its digest.
        self.use_tested_only_manifest()
        source = self.catalog("rc", HELD_ENGINE, FINAL_ON_RC_IMAGE)
        self.assertEqual(self.sign(source, out="rc-signed").returncode, 0)
        envelope = self.directory / "rc-signed" / "catalog-rc.signed.json"
        host = self.directory / "scratch-host"
        published = self.run_tool("publish", "--channel", "rc", "--envelope", str(envelope),
                                  "--host", f"scratch:{host}", "--trust-root", str(self.trust_root))
        self.assertEqual(published.returncode, 0, published.stderr)
        self.assert_refused(self.run_tool("promote", "--channel", "stable", "--source", "rc", "--host", f"scratch:{host}",
                                          "--trust-root", str(self.trust_root), "--now", LATER,
                                          "--out", str(self.directory / "promoted")), "never promoted")
        document = json.loads(source.read_text())
        promoted = self.write("catalog-stable.json", dict(document, sequence=document["sequence"] + 1, issuedAt=LATER))
        engine = self.check(promoted, HELD_ENGINE)
        self.assertEqual(engine.returncode, 0, engine.stderr)
        self.assertIn("engine_gate=held channel=stable", engine.stdout)
        self.assert_refused(self.run_tool("check-promotion", "--channel", "stable", "--source-channel", "rc",
                                          "--catalog", str(promoted), "--source", str(envelope),
                                          "--trust-root", str(self.trust_root)), "never promoted")

    def test_every_signing_path_runs_the_engine_gate_before_approval_and_before_the_key(self) -> None:
        sign = SIGN_WORKFLOW.read_text()
        self.assertIn("      allow-engine-digest:\n", sign.split("\n    secrets:\n")[0])
        sign_job = sign.split("\n  sign:\n")[1].split("\n  publish:\n")[0]
        gate_step = sign_job.split("Check the catalog")[1].split("- name: Sign")[0]
        self.assertIn("ALLOW_ENGINE_DIGEST: ${{ inputs.allow-engine-digest }}", gate_step)
        self.assertGreater(gate_step.index("check-engine"), gate_step.index("esac"))
        self.assertIn('--allow-engine-digest "$ALLOW_ENGINE_DIGEST"', gate_step)
        self.assertIn("GITHUB_STEP_SUMMARY", gate_step)
        for workflow in (WORKFLOW, PROMOTE_WORKFLOW):
            with self.subTest(workflow.name):
                text = workflow.read_text()
                triggers = text.split("\non:\n")[1].split("\npermissions:")[0]
                expected = 2 if "workflow_call:" in triggers else 1
                self.assertEqual(triggers.count("      allow-engine-digest:\n"), expected)
                prepare = text.split("\n  prepare:\n")[1].split("\n  approve:\n")[0].split("\n  release:\n")[0]
                engine_step = prepare.split("- name: Check the engine")[1].split("- uses:")[0]
                self.assertIn('--allow-engine-digest "$ALLOW_ENGINE_DIGEST"', engine_step)
                self.assertIn("GITHUB_STEP_SUMMARY", engine_step)
                self.assertLess(prepare.index("- name: Check the engine"), prepare.index("name: catalog-unsigned"))
                release_job = text.split("\n  release:\n")[1]
                self.assertIn("allow-engine-digest: ${{ inputs.allow-engine-digest }}", release_job)


LATER = "2026-10-20T00:00:00Z"
LATEST = "2026-10-21T00:00:00Z"


@unittest.skipIf(OPENSSL is None, "OpenSSL 3 is required")
class PromotionTests(ReleaseTestCase):
    """Promotion re-signs a published catalog's pins for rc or stable."""

    def setUp(self) -> None:
        super().setUp()
        self.host = self.directory / "scratch-host"
        # An edge catalog with an untested Mac is never promoted; these tests promote one without.
        self.use_tested_only_manifest()

    def publish(self, channel: str, envelope: Path) -> subprocess.CompletedProcess:
        return self.run_tool("publish", "--channel", channel, "--envelope", str(envelope),
                             "--host", f"scratch:{self.host}", "--trust-root", str(self.trust_root))

    def published_edge(self) -> Path:
        envelope = self.signed_at(NOW, "edge")
        self.assertEqual(self.publish("edge", envelope).returncode, 0)
        return envelope

    def promote(self, channel: str = "rc", source: str = "edge", now: str = LATER, out: str = "promoted",
                *extra: str) -> subprocess.CompletedProcess:
        return self.run_tool("promote", "--channel", channel, "--source", source, "--host", f"scratch:{self.host}",
                             "--trust-root", str(self.trust_root), "--check-live", "--now", now,
                             "--out", str(self.directory / out), *extra)

    def signed_catalog(self, envelope: Path) -> tuple[bytes, dict]:
        catalog, _ = release.decode_envelope(envelope.read_bytes())
        return catalog, json.loads(catalog)

    def test_a_dry_run_promotes_the_edge_bytes_to_rc_then_stable(self) -> None:
        edge = self.published_edge()
        edge_bytes, edge_catalog = self.signed_catalog(edge)
        envelopes = {}
        for channel, source, now in (("rc", "edge", LATER), ("stable", "rc", LATEST)):
            result = self.promote(channel, source, now, f"{channel}-unsigned")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("mode=dry-run", result.stdout)
            catalog = self.directory / f"{channel}-unsigned" / f"catalog-{channel}.json"
            self.assertEqual((self.directory / f"{channel}-unsigned" / "channel").read_text(), f"{channel}\n")
            checked = self.run_tool("check-promotion", "--channel", channel, "--source-channel", source,
                                    "--catalog", str(catalog), "--trust-root", str(self.trust_root),
                                    "--source", str(self.directory / f"{channel}-unsigned" / "source.signed.json"))
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(self.sign(catalog, out=f"{channel}-signed").returncode, 0)
            envelopes[channel] = self.directory / f"{channel}-signed" / f"catalog-{channel}.signed.json"
            published = self.publish(channel, envelopes[channel])
            self.assertEqual(published.returncode, 0, published.stderr)

        sequences = [edge_catalog["sequence"]]
        for channel, now in (("rc", LATER), ("stable", LATEST)):
            verified = self.run_tool("verify-envelope", "--envelope", str(envelopes[channel]),
                                     "--trust-root", str(self.trust_root))
            self.assertIn("catalog_signature=passed", verified.stdout)
            _, catalog = self.signed_catalog(envelopes[channel])
            sequences.append(catalog["sequence"])
            self.assertEqual(catalog["issuedAt"], now)
            # The same models, pins, digests and installer block as edge.
            for key in set(edge_catalog) | set(catalog):
                if key not in ("sequence", "issuedAt"):
                    self.assertEqual(catalog.get(key), edge_catalog.get(key), key)
            for model in catalog["models"]:
                self.assertEqual(model["payloadDigest"], f"sha256:{sha(PAYLOAD)}")
                self.assertEqual(model["engineDigest"], f"sha256:{sha(ENGINE)}")
                self.assertEqual(model["metadataDigest"], f"sha256:{sha(METADATA)}")
                self.assertEqual(model["evidenceRevision"], IMAGE)
        self.assertEqual(sequences, sorted(set(sequences)))
        # Edge is untouched and nothing reached a real host.
        self.assertEqual((self.host / "catalog-edge.signed.json").read_bytes(), edge.read_bytes())
        self.assertEqual(sorted(path.name for path in self.host.iterdir()),
                         ["catalog-edge.signed.json", "catalog-rc.signed.json", "catalog-stable.signed.json"])
        self.assertEqual(self.calls.read_text(), "", "a dry run must not call any host tool")

    def test_promotion_rereads_every_pinned_artifact_and_uploads_nothing(self) -> None:
        self.published_edge()
        served = {part["url"]: data for part, data in zip(payload_document()["parts"], PAYLOAD_PARTS)}
        served[engine_document()["url"]] = ENGINE
        served[artifact(IMAGE, "installer_data.json", METADATA)["url"]] = METADATA
        requested = []

        def fake_open(url, timeout=60, headers=None):
            requested.append(url)
            return contextlib.closing(io.BytesIO(served[url]))

        arguments = ["promote", "--channel", "rc", "--source", "edge", "--host", f"scratch:{self.host}",
                     "--trust-root", str(self.trust_root), "--verify-remote", "--now", LATER,
                     "--out", str(self.directory / "remote")]
        saved = release.open_url, dict(os.environ)
        release.open_url = fake_open
        os.environ.update(PATH=self.environment["PATH"], OMARCHY_OPENSSL=OPENSSL)
        os.environ.pop("GITHUB_REPOSITORY", None)
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(release.main(arguments), 0, output.getvalue())
            self.assertEqual(sorted(requested), sorted(served))
            first = payload_document()["parts"][0]["url"]
            served[first] = served[first][:-1] + b"X"
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(release.main(arguments), 1)
            self.assertIn("not the pinned", output.getvalue())
        finally:
            release.open_url = saved[0]
            os.environ.clear()
            os.environ.update(saved[1])
        self.assertEqual(self.calls.read_text(), "")

    def test_a_tampered_source_catalog_is_refused(self) -> None:
        self.assert_refused(self.promote(), "no edge catalog is published")
        edge = self.published_edge()
        served = self.host / "catalog-edge.signed.json"

        document = json.loads(edge.read_text())
        catalog = base64.b64decode(document["catalog"]).replace(
            f"sha256:{sha(PAYLOAD)}".encode(), f"sha256:{'f' * 64}".encode())
        document["catalog"] = base64.b64encode(catalog).decode()
        served.write_text(json.dumps(document))
        self.assert_refused(self.promote(), "does not verify")

        other = self.directory / "other"
        other.mkdir()
        other_key, _ = generate_key(other)
        result = self.prepare("default", None, "foreign")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sign(self.directory / "foreign" / "catalog-edge.json", key=other_key,
                                   trust_root=other / "trust-root.ed25519.pub", out="foreign-signed").returncode, 0)
        shutil.copyfile(self.directory / "foreign-signed" / "catalog-edge.signed.json", served)
        self.assert_refused(self.promote(), "does not verify")

        shutil.copyfile(edge, served)
        edge_sha256 = sha(self.signed_catalog(edge)[0])
        self.assert_refused(self.promote("rc", "edge", LATER, "promoted", "--source-sha256", "e" * 64),
                            f"sha256 {edge_sha256}, not the pinned")
        self.assertFalse((self.directory / "promoted").exists())
        self.assertEqual(self.promote("rc", "edge", LATER, "promoted", "--source-sha256", edge_sha256).returncode, 0)

    def test_real_promotion_is_parked_without_the_explicit_input(self) -> None:
        edge = self.published_edge()
        edge_sha256 = sha(self.signed_catalog(edge)[0])
        pin = ("--source-sha256", edge_sha256)
        self.assert_refused(self.promote("rc", "edge", LATER, "real", "--real", *pin), "promotion is parked")
        self.assert_refused(self.promote("rc", "edge", LATER, "real", "--real", "--confirm", "promote-stable", *pin),
                            "--confirm promote-rc")
        self.assert_refused(self.promote("rc", "edge", LATER, "real", "--real", "--confirm", "promote-rc"),
                            "must pin the source catalog")
        self.assertFalse((self.directory / "real").exists())
        result = self.promote("rc", "edge", LATER, "real", "--real", "--confirm", "promote-rc", *pin)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mode=real", result.stdout)

    def test_the_signing_job_rechecks_the_promotion(self) -> None:
        self.published_edge()
        self.assertEqual(self.promote().returncode, 0)
        out = self.directory / "promoted"
        catalog_path = out / "catalog-rc.json"
        original = json.loads(catalog_path.read_text())

        def check(document: dict | None = None, source: Path = out / "source.signed.json",
                  name: str = "catalog-rc.json") -> subprocess.CompletedProcess:
            path = catalog_path if document is None else self.write(name, document)
            return self.run_tool("check-promotion", "--channel", "rc", "--source-channel", "edge",
                                 "--catalog", str(path), "--source", str(source), "--trust-root", str(self.trust_root))

        self.assertEqual(check().returncode, 0)
        source_sha256 = sha(release.decode_envelope((out / "source.signed.json").read_bytes())[0])
        pinned = self.run_tool("check-promotion", "--channel", "rc", "--source-channel", "edge",
                               "--catalog", str(catalog_path), "--source", str(out / "source.signed.json"),
                               "--trust-root", str(self.trust_root), "--source-sha256", source_sha256)
        self.assertEqual(pinned.returncode, 0, pinned.stderr)
        mispinned = self.run_tool("check-promotion", "--channel", "rc", "--source-channel", "edge",
                                  "--catalog", str(catalog_path), "--source", str(out / "source.signed.json"),
                                  "--trust-root", str(self.trust_root), "--source-sha256", "d" * 64)
        self.assert_refused(mispinned, "not the pinned")
        changed = json.loads(json.dumps(original))
        changed["models"][0]["engineDigest"] = f"sha256:{'a' * 64}"
        self.assert_refused(check(changed, name="catalog-rc.json"), "changes more than sequence and issuedAt: models")
        changed = json.loads(json.dumps(original))
        changed["models"][0]["payloadArtifact"]["parts"][0]["sourceURL"] = f"{release_base('other')}/{ZIP}.part00"
        self.assert_refused(check(changed, name="catalog-rc.json"), "models")
        changed = dict(original, installer=dict(original["installer"], minimumVersion="2.0.0"))
        self.assert_refused(check(changed, name="catalog-rc.json"), "installer")
        changed = dict(original, models=original["models"][1:])
        self.assert_refused(check(changed, name="catalog-rc.json"), "models")
        changed = dict(original, sequence=SEQUENCE)
        self.assert_refused(check(changed, name="catalog-rc.json"), "does not follow")
        self.assert_refused(check(original, name="catalog-stable.json"), "must be catalog-rc.json")
        tampered = json.loads((out / "source.signed.json").read_text())
        tampered["signature"] = base64.b64encode(b"\0" * 64).decode()
        self.assert_refused(check(source=self.write("source.signed.json", tampered)), "does not verify")

    def test_a_promotion_of_an_untested_or_preview_mac_is_refused(self) -> None:
        now = datetime.datetime(2026, 10, 29, tzinfo=datetime.timezone.utc)
        note = "Untested: qualified on edge next"
        untested = release.supported_models.untested
        release.supported_models.untested = lambda: {"apple,j700": note}
        self.addCleanup(setattr, release.supported_models, "untested", untested)
        source = release.build_catalog(
            "edge", IMAGE, payload_document(), engine_document(),
            artifact(IMAGE, "installer_data.json", METADATA), evidence_document(), installer_document(),
            datetime.datetime(2026, 10, 14, tzinfo=datetime.timezone.utc))
        for channel in ("rc", "stable"):
            with self.subTest(channel), self.assertRaisesRegex(
                    release.Refusal, f"not what {channel} offers: .*apple,j700 carries a preview note"):
                release.promote_catalog(source, now, channel)
        # Once the Mac leaves untested, a source that still carries its note stays refused.
        release.supported_models.untested = lambda: {}
        with self.assertRaisesRegex(release.Refusal, "apple,j700 carries a preview note"):
            release.promote_catalog(source, now, "rc")
        for model in source["models"]:
            model.pop("previewNote", None)
        self.assertEqual(release.promote_catalog(source, now, "rc")["models"], source["models"])

    def test_promotion_only_moves_forward(self) -> None:
        for channel, source in (("rc", "rc"), ("rc", "stable"), ("edge", "edge"), ("stable", "stable"),
                                ("beta", "edge"), ("rc", "nightly")):
            result = self.promote(channel, source)
            self.assertEqual(result.returncode, 1, (channel, source))
            self.assertIn("refused", result.stderr)

    def test_a_promotion_must_follow_the_published_target(self) -> None:
        self.published_edge()
        self.assertEqual(self.promote("rc", "edge", LATEST, "newer").returncode, 0)
        self.assertEqual(self.sign(self.directory / "newer" / "catalog-rc.json", out="newer-signed").returncode, 0)
        self.assertEqual(self.publish("rc", self.directory / "newer-signed" / "catalog-rc.signed.json").returncode, 0)
        self.assert_refused(self.promote("rc", "edge", LATER, "older"), "does not follow the published rc")



class R2CatalogHostTests(unittest.TestCase):
    ENDPOINT = release.r2_s3.Endpoint("account.r2.cloudflarestorage.com", "omarchy-mac", "id", "secret")
    BASE = release.identity()["INSTALLER_CATALOG_BASE"]

    def host(self, answer=None):
        host = release.R2CatalogHost(self.ENDPOINT, self.BASE)
        sent = []

        def request(method, key, **kwargs):
            sent.append((method, key, kwargs, kwargs["body"].read() if kwargs.get("body") else None))
            if isinstance(answer, Exception):
                raise answer
            return io.BytesIO(b"")
        host.endpoint = type("Endpoint", (), {"request": staticmethod(request)})()
        return host, sent

    def test_the_catalog_is_one_put_at_the_url_apps_read(self) -> None:
        host, sent = self.host()
        envelope = Path(tempfile.mkdtemp()) / "catalog-edge.signed.json"
        self.addCleanup(shutil.rmtree, envelope.parent)
        envelope.write_bytes(b'{"catalog": "x"}')
        host.publish("edge", envelope)
        self.assertEqual(host.describe("edge"), f"{self.BASE}/catalog-edge.signed.json")
        self.assertEqual(host.prefix, self.BASE.split("/", 3)[3] + "/")
        [(method, key, kwargs, body)] = sent
        self.assertEqual((method, key, body), ("PUT", "v1/catalog-edge.signed.json", b'{"catalog": "x"}'))
        self.assertEqual(kwargs["headers"], {"Content-Type": "application/json"})
        self.assertEqual(kwargs["payload_sha256"], hashlib.sha256(b'{"catalog": "x"}').hexdigest())

    def test_a_bucket_lock_over_the_catalogs_is_named(self) -> None:
        locked = urllib.error.HTTPError("x", 403, "Forbidden", None,
                                        io.BytesIO(b"<Error><Code>ObjectLockedByBucketPolicy</Code></Error>"))
        host, _ = self.host(locked)
        envelope = Path(tempfile.mkdtemp()) / "e.json"
        self.addCleanup(shutil.rmtree, envelope.parent)
        envelope.write_bytes(b"{}")
        with self.assertRaisesRegex(release.Refusal, "403 ObjectLockedByBucketPolicy.*bucket lock covers the catalogs"):
            host.publish("edge", envelope)

    def test_fetch_reads_the_unchanged_url_without_a_cached_copy(self) -> None:
        opened = []

        def recording_open(url, timeout=60, headers=None):
            opened.append((url, timeout, headers))
            return io.BytesIO(b"catalog")
        original, release.open_url = release.open_url, recording_open
        self.addCleanup(setattr, release, "open_url", original)
        host, _ = self.host()
        self.assertEqual(host.fetch("edge", timeout=release.READBACK_TIMEOUT), b"catalog")
        self.assertEqual(opened, [(f"{self.BASE}/catalog-edge.signed.json", release.READBACK_TIMEOUT, release.NO_CACHE)])

    def test_without_credentials_r2_is_refused_before_anything(self) -> None:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("R2_")}
        original = os.environ.copy()
        os.environ.clear()
        os.environ.update(environment)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(original)))
        with self.assertRaisesRegex(release.Refusal, "no R2 credentials"):
            release.catalog_host("r2")


class ReadBackTests(unittest.TestCase):
    """The publish readback outlasts a stale cache, but not forever."""

    class Host:
        def __init__(self, replies):
            self.replies = list(replies)
            self.fetches = 0
            self.timeouts: list[float] = []

        def describe(self, channel: str) -> str:
            return f"host/catalog-{channel}.signed.json"

        def fetch(self, channel: str, timeout: float = 60) -> bytes | None:
            self.fetches += 1
            self.timeouts.append(timeout)
            reply = self.replies.pop(0) if self.replies else b"stale"
            if isinstance(reply, Exception):
                raise reply
            return reply

    def read_back(self, host) -> list[int]:
        slept: list[int] = []
        with contextlib.redirect_stderr(io.StringIO()):
            release.read_back(host, "edge", b"new", sleep=slept.append)
        return slept

    def test_a_cache_stale_for_over_a_minute_is_ridden_out(self) -> None:
        host = self.Host([b"old"] * 8 + [b"new"])
        slept = self.read_back(host)
        self.assertEqual(host.fetches, 9)
        self.assertGreater(sum(slept), 61, "2026-10-21 served the old asset 61 s after the upload")
        self.assertLessEqual(sum(slept), sum(release.READBACK_DELAYS))

    def test_the_first_read_needs_no_wait(self) -> None:
        self.assertEqual(self.read_back(self.Host([b"new"])), [])

    def test_transient_errors_are_retried(self) -> None:
        host = self.Host([OSError("reset"), urllib.error.URLError("timeout"), None, b"new"])
        self.assertEqual(len(self.read_back(host)), 3)
        self.assertEqual(host.fetches, 4)

    def test_a_catalog_that_never_appears_is_refused(self) -> None:
        host = self.Host([])
        with self.assertRaisesRegex(release.Refusal, "does not serve the catalog just published"):
            self.read_back(host)
        self.assertEqual(host.fetches, len(release.READBACK_DELAYS) + 1)

    def test_the_window_stays_bounded(self) -> None:
        self.assertGreaterEqual(sum(release.READBACK_DELAYS), 240)
        self.assertLessEqual(sum(release.READBACK_DELAYS), 600)

    def test_every_fetch_hanging_still_ends_inside_the_publish_job(self) -> None:
        host = self.Host([])
        with self.assertRaises(release.Refusal):
            self.read_back(host)
        self.assertEqual(set(host.timeouts), {release.READBACK_TIMEOUT})
        worst = sum(release.READBACK_DELAYS) + (len(release.READBACK_DELAYS) + 1) * release.READBACK_TIMEOUT
        self.assertLess(worst, 12 * 60, "mac-catalog-sign.yml's publish job has 15 minutes, checkout and the swap included")

    def test_a_stale_read_is_logged(self) -> None:
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            release.read_back(self.Host([b"old", b"new"]), "edge", b"new", sleep=lambda seconds: None)
        self.assertIn("serves 3 other bytes", log.getvalue())

    def test_the_github_host_asks_for_an_uncached_copy(self) -> None:
        sent = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def recording_open(url, timeout=60, headers=None):
            sent.append((url, timeout, headers))
            return Response(b"catalog")
        original, release.open_url = release.open_url, recording_open
        self.addCleanup(setattr, release, "open_url", original)
        host = release.GitHubReleaseHost("omacom/omarchy-mac-installer")
        self.assertEqual(host.fetch("edge", timeout=release.READBACK_TIMEOUT), b"catalog")
        self.assertEqual(sent, [(host.describe("edge"), release.READBACK_TIMEOUT, release.NO_CACHE)])
        self.assertEqual(release.NO_CACHE["Cache-Control"], "no-cache")

    def test_open_url_puts_the_headers_on_the_request(self) -> None:
        opened = []

        class Opener:
            def open(self, request, timeout=None):
                opened.append((request, timeout))
                return io.BytesIO(b"")
        original = release.urllib.request.build_opener
        release.urllib.request.build_opener = lambda *handlers: Opener()
        self.addCleanup(setattr, release.urllib.request, "build_opener", original)
        release.open_url("https://example.invalid/catalog", timeout=7, headers=release.NO_CACHE)
        request, timeout = opened[0]
        self.assertEqual(timeout, 7)
        self.assertEqual(request.get_header("Cache-control"), "no-cache")
        self.assertEqual(request.get_header("Pragma"), "no-cache")
        self.assertEqual(request.get_header("User-agent"), "omarchy-mac-catalog")


if __name__ == "__main__":
    unittest.main()
