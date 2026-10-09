"""Policy holdouts and exclusions precede traversal; private snapshots isolate later source changes."""

import copy
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import uuid
from unittest.mock import patch

from omarchy_migration import collection, probe, restore
from omarchy_migration.dependency import configured_age


SECRET = b"synthetic-only-otter-maple-window-cobalt"
MTIME = 1720000000123456789


class CollectionTests(unittest.TestCase):
    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest("unprivileged fixture collector")
        self.scratch = tempfile.TemporaryDirectory(prefix="migration-collection-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.source, self.parent = self.root / "source", self.root / "snapshots"
        self.source.mkdir(mode=0o700)
        self.parent.mkdir(mode=0o700)
        self.request = {"schema": collection.REQUEST_SCHEMA, "request_id": str(uuid.uuid4()),
                        "selection": [{"source": "", "archive": ""}], "selected_adapters": [],
                        "selected_mounts": [], "selected_share_stores": []}
        evidence = {"path": "synthetic"}
        self.policy = {
            "schema": "omarchy-migration/policy/2", "revision": "synthetic-default-and-alternate/1",
            "source": {"provider": "try-omarchy", "repository": "https://example.invalid/synthetic",
                       "commit": "0" * 40},
            "credential_stores": [
                {"id": "fake-ssh", "category": "credentials", "roots": [".ssh", "alternate/ssh"],
                 "adapter": "fixture-ssh-bytes/1"},
                {"id": "fake-browser", "category": "browser-profile",
                 "roots": [".config/BraveSoftware", "alternate/config/BraveSoftware"], "adapter": "fixture-browser/1"},
                {"id": "fake-codex", "category": "credentials", "roots": [".codex/auth.json", "alternate/codex/auth.json"],
                 "adapter": None},
                {"id": "fake-vault", "category": "credentials", "roots": [".config/1Password", "alternate/vault"],
                 "adapter": None},
            ],
            "share_stores": [
                {"id": "share-ssh", "category": "credentials", "directories": [".ssh"], "files": []},
                {"id": "share-keys", "category": "credentials", "directories": [], "files": ["id_ed25519", "*.pem"]},
                {"id": "share-keychains", "category": "credentials", "directories": ["Library/Keychains"], "files": []},
            ],
            "mounts": [{"id": "mac-share", "path": "/mnt/mac", "reason": "mac_shared_folder", "evidence": evidence}],
            "rules": [
                {"id": "vm-display", "path": ".config/hypr/monitors.lua", "match": "exact", "action": "exclude",
                 "reason": "display_configuration", "evidence": evidence},
                {"id": "vm-state", "path": ".local/state/vm", "match": "tree", "action": "exclude",
                 "reason": "vm_integration", "evidence": evidence},
                {"id": "vm-menu", "path": ".config/omarchy/extensions/omarchy-menu.jsonc", "match": "exact",
                 "action": "transform", "reason": "vm_integration", "evidence": evidence,
                 "transform": {"type": "remove-json-keys", "format": "jsonc", "keys": ["setup.vm"]}},
                {"id": "vm-flags", "path": ".config/app-flags.conf", "match": "exact", "action": "transform",
                 "reason": "try_appended_fragment", "evidence": evidence,
                 "transform": {"type": "strip-appended-block", "block": "--vm-only\n"}},
                {"id": "keep-toggles", "path": ".local/state/toggles", "match": "tree", "action": "preserve",
                 "reason": "user_state", "evidence": evidence},
            ],
        }
        self.ordinary = {
            ".config/unknown/settings": b"unfamiliar personal configuration\n",
            ".config/theme/selected": b"custom-theme\n",
            ".codex/config.toml": b"synthetic ordinary CLI preferences\n",
            ".local/share/unknown/data": b"ordinary durable data\n",
            ".ssh-personal-notes/readme": b"similar prefix is ordinary\n",
            "Projects/demo/.git/HEAD": b"ref: refs/heads/main\n",
            "Projects/demo/modified": b"uncommitted synthetic work\n",
            "Projects/demo/untracked": b"untracked synthetic work\n",
            "Projects/demo/run": b"#!/bin/bash\ntouch MUST-NOT-RUN\n",
        }
        self.protected = {
            ".ssh/id_fake": b"FAKE-SSH-SECRET",
            "alternate/ssh/id_fake": b"FAKE-ALTERNATE-SSH-SECRET",
            ".config/BraveSoftware/profile/token": b"FAKE-BROWSER-SECRET",
            "alternate/config/BraveSoftware/profile/token": b"FAKE-ALTERNATE-BROWSER-SECRET",
            ".codex/auth.json": b"FAKE-CODEX-SECRET",
            "alternate/codex/auth.json": b"FAKE-ALTERNATE-CODEX-SECRET",
            ".config/1Password/vault": b"FAKE-VAULT-SECRET",
            "alternate/vault/vault": b"FAKE-ALTERNATE-VAULT-SECRET",
        }
        for name, data in {**self.ordinary, **self.protected}.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(data)
            path.chmod(0o750 if name.endswith("/run") else 0o600)
            os.utime(path, ns=(MTIME, MTIME))
        (self.source / "empty").mkdir(mode=0o750)
        (self.source / "readme-link").symlink_to("Projects/demo/modified")
        (self.source / "secret-alias").symlink_to(".ssh/id_fake")
        (self.source / "store-alias").symlink_to(".ssh", target_is_directory=True)

    def capture(self, **kwargs):
        return collection.collect_fixture(self.source, self.request, self.policy,
                                          supported_adapters=("fixture-ssh-bytes/1",),
                                          snapshot_parent=self.parent, **kwargs)

    def assert_private(self, snapshot):
        self.assertEqual(stat.S_IMODE(snapshot.directory.stat().st_mode), 0o700)
        for path in snapshot.paths.values():
            if path.is_file() and not path.is_symlink():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            elif path.is_dir() and not path.is_symlink():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_default_preserves_unknown_config_and_reports_every_pruned_store(self):
        with self.capture() as snapshot:
            self.assertTrue(set(self.ordinary) <= snapshot.paths.keys())
            self.assertTrue(all(snapshot.paths[name].read_bytes() == data for name, data in self.ordinary.items()))
            self.assertTrue(set(self.protected).isdisjoint(snapshot.paths))
            omitted = {item["source"] for item in snapshot.report["entries"] if item["outcome"] == "held-out"}
            self.assertEqual(omitted, {root for store in self.policy["credential_stores"] for root in store["roots"]})
            self.assertEqual(snapshot.report["counts"]["held-out"], 8)
            self.assertEqual(snapshot.report["status"], "complete")
            self.assert_private(snapshot)
            directory = snapshot.directory
        self.assertFalse(directory.exists())
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_held_out_descendants_are_neither_opened_nor_listed(self):
        blocked = [self.source / root for store in self.policy["credential_stores"] for root in store["roots"]]
        opened, scanned = os.open, os.scandir

        def guard_path(value, dir_fd=None):
            if isinstance(value, int):
                path = Path(f"/proc/self/fd/{value}").resolve()
            else:
                base = Path(f"/proc/self/fd/{dir_fd}").resolve() if dir_fd is not None else Path.cwd()
                path = Path(os.path.abspath(base / value))
            self.assertFalse(any(path.is_relative_to(root) for root in blocked), str(path))

        def guarded_open(value, flags, *args, **kwargs):
            guard_path(value, kwargs.get("dir_fd"))
            return opened(value, flags, *args, **kwargs)

        def guarded_scan(value):
            guard_path(value)
            return scanned(value)

        with patch.object(collection.os, "open", side_effect=guarded_open), patch.object(
                collection.os, "scandir", side_effect=guarded_scan), self.capture() as snapshot:
            self.assertEqual(snapshot.report["counts"]["held-out"], 8)

    def test_unreadable_protected_directory_is_pruned_without_entering_it(self):
        protected = self.source / ".ssh"
        protected.chmod(0o000)
        try:
            with self.capture() as snapshot:
                self.assertNotIn(".ssh", snapshot.paths)
        finally:
            protected.chmod(0o700)

    def test_protected_source_remapped_to_ordinary_name_remains_held_out(self):
        self.request["selection"] = [{"source": "alternate/config/BraveSoftware", "archive": "notes"}]
        self.request["selected_adapters"] = ["fixture-browser/1"]
        with self.capture() as snapshot:
            self.assertEqual(snapshot.paths, {})
            self.assertEqual(snapshot.report["entries"][0]["archive"], "notes")
            self.assertEqual(snapshot.report["entries"][0]["reason"], "adapter-unavailable")

    def test_nested_store_policy_uses_source_names_after_ancestor_remap(self):
        self.request["selection"] = [{"source": "alternate", "archive": "Recovered"}]
        with self.capture() as snapshot:
            self.assertNotIn("Recovered/ssh/id_fake", snapshot.paths)
            held = [item for item in snapshot.report["entries"] if item["outcome"] == "held-out"]
            self.assertEqual({item["source"] for item in held},
                             {"alternate/ssh", "alternate/config/BraveSoftware", "alternate/codex/auth.json", "alternate/vault"})

    def test_explicit_supported_fixture_adapter_includes_only_its_fake_stores(self):
        self.request["selected_adapters"] = ["fixture-ssh-bytes/1", "fixture-browser/1"]
        with self.capture() as snapshot:
            self.assertEqual(snapshot.paths[".ssh/id_fake"].read_bytes(), self.protected[".ssh/id_fake"])
            self.assertEqual(snapshot.paths["alternate/ssh/id_fake"].read_bytes(), self.protected["alternate/ssh/id_fake"])
            self.assertNotIn(".config/BraveSoftware", snapshot.paths)
            self.assertNotIn(".codex/auth.json", snapshot.paths)
            self.assertFalse(any(item["source"] == ".ssh" and item["outcome"] == "held-out"
                                 for item in snapshot.report["entries"]))

    def test_symlink_aliases_are_metadata_only_and_held_out_targets_are_inert(self):
        with self.capture() as snapshot:
            by_path = {entry["path"]: entry for entry in snapshot.manifest["entries"]}
            self.assertEqual(by_path["secret-alias"]["target"], ".ssh/id_fake")
            self.assertEqual(by_path["store-alias"]["kind"], "symlink")
            self.assertIsNone(probe.link_target(by_path["secret-alias"], by_path))
            self.assertIsNotNone(probe.link_target(by_path["readme-link"], by_path))
            self.assertEqual(snapshot.report["counts"]["inert-link"], 2)

    def test_selected_symlink_ancestor_is_never_followed(self):
        self.request["selection"] = [{"source": "store-alias/id_fake", "archive": "notes"}]
        with self.assertRaises(OSError):
            with self.capture():
                self.fail("followed a symlink parent")
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_hardlink_alias_is_deferred_before_any_payload_read(self):
        alias = self.source / "Projects/demo/key-notes"
        os.link(self.source / ".ssh/id_fake", alias)
        opened = os.open

        def guard(value, *args, **kwargs):
            self.assertNotEqual(os.fspath(value), "key-notes")
            return opened(value, *args, **kwargs)

        with patch.object(collection.os, "open", side_effect=guard), self.capture() as snapshot:
            self.assertNotIn("Projects/demo/key-notes", snapshot.paths)
            item = next(item for item in snapshot.report["entries"] if item["source"] == "Projects/demo/key-notes")
            self.assertEqual(item["reason"], "multiply-linked-file")

    def test_remapped_links_are_deferred_instead_of_activating_different_targets(self):
        (self.source / "project").mkdir(mode=0o700)
        (self.source / "other").mkdir(mode=0o700)
        (self.source / "project/link").symlink_to("../ordinary")
        self.request["selection"] = [{"source": "project", "archive": "project"},
                                     {"source": "other", "archive": "ordinary"}]
        with self.capture() as snapshot:
            self.assertNotIn("project/link", snapshot.paths)
            item = next(item for item in snapshot.report["entries"] if item["source"] == "project/link")
            self.assertEqual(item["reason"], "remapped-link")

    def test_special_file_is_reported_without_opening_it(self):
        os.mkfifo(self.source / "fifo")
        with self.capture() as snapshot:
            self.assertNotIn("fifo", snapshot.paths)
            self.assertEqual(next(item["reason"] for item in snapshot.report["entries"] if item["source"] == "fifo"),
                             "special-file")

    def test_changed_source_during_copy_fails_and_removes_private_snapshot(self):
        walk = collection._Snapshot._walk

        def change(snapshot, parent, name, source, archive):
            walk(snapshot, parent, name, source, archive)
            if source == "Projects/demo/modified":
                (self.source / source).write_bytes(b"modified after that file was captured")

        with patch.object(collection._Snapshot, "_walk", change), self.assertRaises(probe.Rejected):
            with self.capture():
                self.fail("changed source accepted")
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_directory_changed_during_traversal_fails_before_snapshot_is_exposed(self):
        walk = collection._Snapshot._walk
        changed = False

        def change(snapshot, parent, name, source, archive):
            nonlocal changed
            walk(snapshot, parent, name, source, archive)
            if source == "Projects/demo/modified" and not changed:
                changed = True
                (self.source / "Projects/demo/later").write_bytes(b"late child")

        with patch.object(collection._Snapshot, "_walk", change), self.assertRaisesRegex(probe.Rejected, "directory changed"):
            with self.capture():
                self.fail("changed directory accepted")
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_nested_mount_identity_rejection_precedes_payload_read(self):
        mount = collection._mount
        source_inode = (self.source / "Projects/demo/modified").stat().st_ino

        def different(fd):
            value = mount(fd)
            return value + 1 if os.fstat(fd).st_ino == source_inode else value

        with patch.object(collection, "_mount", side_effect=different), self.assertRaisesRegex(probe.Rejected, "mount differs"):
            with self.capture():
                self.fail("crossed a nested mount")
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_snapshot_parent_inside_source_or_with_unsafe_permissions_is_rejected(self):
        for parent in (self.source, self.source / "snapshots", self.root / "unsafe"):
            if not parent.exists():
                parent.mkdir(mode=0o700)
            if parent.name == "unsafe":
                parent.chmod(0o755)
            with self.subTest(parent=parent), self.assertRaises(probe.Rejected):
                with collection.collect_fixture(self.source, self.request, self.policy, snapshot_parent=parent):
                    self.fail("unsafe snapshot location accepted")

    def test_request_cannot_change_policy_or_enable_unknown_adapter(self):
        baseline = copy.deepcopy(self.request)
        for key, value in (("policy", {}), ("schema", "unknown"), ("request_id", "bad"),
                           ("selected_adapters", ["real-browser"]), ("selected_adapters", [True]),
                           ("selected_adapters", ["fixture-ssh-bytes/1", "fixture-ssh-bytes/1"])):
            self.request = copy.deepcopy(baseline)
            self.request[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(probe.Rejected):
                with self.capture():
                    self.fail("invalid request accepted")
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_invalid_overlapping_or_unrepresentable_selection_is_rejected(self):
        for selection in ([{"source": "../escape", "archive": "notes"}],
                          [{"source": "/absolute", "archive": "notes"}],
                          [{"source": "Projects", "archive": "nested/notes"}],
                          [{"source": "Projects", "archive": ""}],
                          [{"source": "", "archive": ""}, {"source": "Projects", "archive": "projects"}],
                          [{"source": "Projects", "archive": "notes"}, {"source": ".config", "archive": "notes"}],
                          # An ancestor that does not sort next to its descendant.
                          [{"source": "a", "archive": "a"}, {"source": "a-b", "archive": "b"},
                           {"source": "a/c", "archive": "c"}],
                          [{"source": "a", "archive": "a"}, {"source": "a", "archive": "b"}],
                          [{"source": f"entry-{index}", "archive": f"entry-{index}"} for index in range(1025)]):
            self.request["selection"] = selection
            with self.subTest(selection=selection), self.assertRaises(probe.Rejected):
                with self.capture():
                    self.fail("invalid selection accepted")

    def test_a_real_home_with_many_top_level_entries_is_one_selection(self):
        names = [f"entry-{index:03}" for index in range(200)]
        for name in names:
            self.write(f"{name}/file", name.encode())
        self.request["selection"] = [{"source": name, "archive": name} for name in names]
        with self.capture() as snapshot:
            self.assertEqual({path.split("/")[0] for path in snapshot.paths}, set(names))

    def test_policy_revision_overlap_rule_or_capability_mismatch_is_rejected(self):
        baseline = copy.deepcopy(self.policy)
        for damage in ("revision", "overlap", "rule-in-store", "adapter", "schema"):
            self.policy = copy.deepcopy(baseline)
            if damage == "revision":
                self.policy["revision"] = "Future 2"
            elif damage == "overlap":
                self.policy["credential_stores"][0]["roots"].append(".ssh/subdirectory")
            elif damage == "rule-in-store":
                self.policy["rules"][0]["path"] = ".ssh/config"
            elif damage == "adapter":
                self.policy["credential_stores"][0]["adapter"] = "real-ssh/1"
            else:
                self.policy["schema"] = "omarchy-migration-fixture-layout/1"
            with self.subTest(damage=damage), self.assertRaises(probe.Rejected):
                with self.capture():
                    self.fail("invalid policy accepted")

    def test_private_report_binds_capabilities_that_change_effective_selection(self):
        self.request["selected_adapters"] = ["fixture-ssh-bytes/1"]
        with self.capture() as available:
            first = copy.deepcopy(available.report)
            self.assertIn(".ssh/id_fake", available.paths)
        with collection.collect_fixture(self.source, self.request, self.policy,
                                        snapshot_parent=self.parent) as unavailable:
            second = unavailable.report
            self.assertNotIn(".ssh/id_fake", unavailable.paths)
        self.assertEqual(first["request_sha256"], second["request_sha256"])
        self.assertEqual(first["policy_sha256"], second["policy_sha256"])
        self.assertEqual(first["policy_revision"], "synthetic-default-and-alternate/1")
        self.assertNotEqual(first["capabilities_sha256"], second["capabilities_sha256"])

    def test_depth_limit_rejects_before_recursive_capture(self):
        self.request["selection"] = [{"source": "/".join(["dir"] * 65), "archive": "notes"}]
        with self.assertRaisesRegex(probe.Rejected, "relative path"):
            with self.capture():
                self.fail("unbounded depth accepted")
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_root_permission_change_before_capture_is_rejected_for_partial_selection(self):
        self.request["selection"] = [{"source": "Projects", "archive": "Projects"}]
        capture = collection._Snapshot.capture

        def change(snapshot):
            self.source.chmod(0o777)
            return capture(snapshot)

        try:
            with patch.object(collection._Snapshot, "capture", change), self.assertRaisesRegex(probe.Rejected, "unsafe source"):
                with self.capture():
                    self.fail("writable source root accepted")
        finally:
            self.source.chmod(0o700)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_limits_abort_collection_and_remove_snapshot(self):
        for limit in ("MAX_TOTAL", "MAX_ENTRIES"):
            with self.subTest(limit=limit), patch.object(probe, limit, 1), self.assertRaises(probe.Rejected):
                with self.capture():
                    self.fail("limit exceeded")
            self.assertEqual(list(self.parent.iterdir()), [])

    def write(self, name, data, mode=0o600):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(data)
        path.chmod(mode)
        os.utime(path, ns=(MTIME, MTIME))
        return path

    def entry(self, snapshot, source):
        return next(item for item in snapshot.report["entries"] if item["source"] == source)

    def test_excluded_paths_are_reported_and_never_opened(self):
        self.write(".config/hypr/monitors.lua", b"monitor = host display\n")
        self.write(".local/state/vm/session/token", b"vm-only state\n")
        blocked = [self.source / ".config/hypr/monitors.lua", self.source / ".local/state/vm"]
        opened, scanned = os.open, os.scandir

        def guard(value, dir_fd=None):
            if isinstance(value, int):
                path = Path(f"/proc/self/fd/{value}").resolve()
            else:
                base = Path(f"/proc/self/fd/{dir_fd}").resolve() if dir_fd is not None else Path.cwd()
                path = Path(os.path.abspath(base / value))
            self.assertFalse(any(path == root or path.is_relative_to(root) for root in blocked), str(path))

        def guarded_open(value, flags, *args, **kwargs):
            guard(value, kwargs.get("dir_fd"))
            return opened(value, flags, *args, **kwargs)

        def guarded_scan(value):
            guard(value)
            return scanned(value)

        with patch.object(collection.os, "open", side_effect=guarded_open), patch.object(
                collection.os, "scandir", side_effect=guarded_scan), self.capture() as snapshot:
            self.assertNotIn(".config/hypr/monitors.lua", snapshot.paths)
            self.assertFalse(any(path.startswith(".local/state/vm") for path in snapshot.paths))
            self.assertIn(".config/hypr", snapshot.paths)
            display = self.entry(snapshot, ".config/hypr/monitors.lua")
            self.assertEqual((display["outcome"], display["reason"], display["rule"]),
                             ("excluded", "display_configuration", "vm-display"))
            self.assertEqual(self.entry(snapshot, ".local/state/vm")["rule"], "vm-state")
            self.assertEqual(snapshot.report["counts"]["excluded"], 2)

    def test_selecting_beneath_an_excluded_directory_reaches_nothing(self):
        self.policy["rules"][0]["path"] = ".local/share/runtime"
        self.write(".local/share/runtime/bin/tool", b"runtime contents\n")
        self.request["selection"] = [{"source": ".local/share/runtime/bin", "archive": "bin"}]
        opened = os.open

        def guarded_open(value, flags, *args, **kwargs):
            self.assertNotEqual(value, "runtime")
            return opened(value, flags, *args, **kwargs)

        with patch.object(collection.os, "open", side_effect=guarded_open), self.capture() as snapshot:
            self.assertEqual(snapshot.paths, {})
            self.assertEqual(snapshot.report["entries"][0]["outcome"], "excluded")

    def test_selected_excluded_root_is_reported_without_capture(self):
        self.write(".config/hypr/monitors.lua", b"monitor = host display\n")
        self.request["selection"] = [{"source": ".config/hypr/monitors.lua", "archive": "monitors.lua"}]
        with self.capture() as snapshot:
            self.assertEqual(snapshot.paths, {})
            self.assertEqual(snapshot.report["entries"][0]["outcome"], "excluded")

    def test_transform_removes_only_provider_entries_and_keeps_metadata(self):
        menu = b'{\n  "setup.vm": {"label": "VM settings"},\n  "launch.notes": {"label": "Notes"}\n}\n'
        self.write(".config/omarchy/extensions/omarchy-menu.jsonc", menu, 0o640)
        self.write(".config/app-flags.conf", b"--user-choice\n--vm-only\n")
        with self.capture() as snapshot:
            path = ".config/omarchy/extensions/omarchy-menu.jsonc"
            self.assertEqual(json.loads(snapshot.paths[path].read_bytes()), {"launch.notes": {"label": "Notes"}})
            self.assertEqual(snapshot.paths[".config/app-flags.conf"].read_bytes(), b"--user-choice\n")
            item = self.entry(snapshot, path)
            self.assertEqual((item["outcome"], item["reason"], item["rule"]), ("transformed", "vm_integration", "vm-menu"))
            entry = next(entry for entry in snapshot.manifest["entries"] if entry["path"] == path)
            data = snapshot.paths[path].read_bytes()
            self.assertEqual((entry["bytes"], entry["mode"], entry["mtime_ns"]), (len(data), 0o640, MTIME))
            self.assertEqual(snapshot.report["counts"]["transformed"], 2)
            self.assert_private(snapshot)
            root = f"{collection.ORIGINALS_ROOT}/{self.request['request_id']}"
            self.assertEqual(snapshot.manifest["provenance"]["originals"], root)
            original = snapshot.paths[f"{root}/{path}"]
            self.assertEqual(original.read_bytes(), menu)
            entry = next(entry for entry in snapshot.manifest["entries"] if entry["path"] == f"{root}/{path}")
            self.assertEqual((entry["mode"], entry["mtime_ns"]), (0o600, MTIME))
            self.assertEqual(snapshot.paths[f"{root}/.config/app-flags.conf"].read_bytes(), b"--user-choice\n--vm-only\n")
            copy = next(item for item in snapshot.report["entries"] if item["archive"] == f"{root}/{path}")
            self.assertEqual((copy["source"], copy["outcome"], copy["reason"]), (path, "included", "original-copy"))

    def test_transform_without_provider_content_exports_the_file_unchanged(self):
        self.write(".config/app-flags.conf", b"--user-choice\n")
        with self.capture() as snapshot:
            self.assertEqual(snapshot.paths[".config/app-flags.conf"].read_bytes(), b"--user-choice\n")
            item = self.entry(snapshot, ".config/app-flags.conf")
            self.assertEqual((item["outcome"], item["reason"], item["rule"]), ("included", "regular-file", "vm-flags"))
            # Nothing changed, so nothing needs an original copy.
            self.assertIsNone(snapshot.manifest["provenance"]["originals"])
            self.assertFalse(any(path.startswith(collection.ORIGINALS_ROOT) for path in snapshot.paths))

    def test_untransformable_files_are_withheld_and_reported(self):
        self.write(".config/omarchy/extensions/omarchy-menu.jsonc", b'{"setup.vm": {}')
        self.write(".config/app-flags.conf", b"--vm-only\n--vm-only\n")
        with self.capture() as snapshot:
            self.assertNotIn(".config/omarchy/extensions/omarchy-menu.jsonc", snapshot.paths)
            self.assertNotIn(".config/app-flags.conf", snapshot.paths)
            reasons = {item["source"]: item["reason"] for item in snapshot.report["entries"]
                       if item["outcome"] == "unsupported"}
            self.assertEqual(reasons[".config/omarchy/extensions/omarchy-menu.jsonc"], "transform-failed")
            self.assertEqual(reasons[".config/app-flags.conf"], "transform-ambiguous")
            self.assertEqual(list(snapshot.directory.iterdir()).__len__(), len(snapshot.paths))

    def test_oversized_and_non_file_transform_targets_are_withheld(self):
        (self.source / ".config/omarchy/extensions/omarchy-menu.jsonc").mkdir(parents=True, mode=0o700)
        self.write(".config/app-flags.conf", b"x" * (collection.migration_policy.MAX_TRANSFORM_INPUT + 1))
        opened = os.open

        def guarded_open(value, flags, *args, **kwargs):
            self.assertNotIn(value, ("app-flags.conf", "omarchy-menu.jsonc"))
            return opened(value, flags, *args, **kwargs)

        with patch.object(collection.os, "open", side_effect=guarded_open), self.capture() as snapshot:
            self.assertEqual(self.entry(snapshot, ".config/omarchy/extensions/omarchy-menu.jsonc")["reason"],
                             "transform-target-not-file")
            self.assertEqual(self.entry(snapshot, ".config/app-flags.conf")["reason"], "transform-too-large")
            self.assertNotIn(".config/app-flags.conf", snapshot.paths)

    def test_links_into_the_mac_share_are_inert_metadata(self):
        (self.source / "Work").symlink_to("/mnt/mac")
        (self.source / "Notes").symlink_to("/mnt/mac/Projects/notes")
        with self.capture() as snapshot:
            for name in ("Work", "Notes"):
                item = self.entry(snapshot, name)
                self.assertEqual((item["outcome"], item["reason"], item["mount"]), ("inert-link", "mount-link", "mac-share"))
                self.assertIn(name, snapshot.paths)
            self.assertEqual(self.entry(snapshot, "readme-link")["mount"], None)

    def test_metadata_the_bundle_cannot_carry_is_recorded(self):
        tagged = self.write("Documents/tagged.txt", b"tagged\n")
        try:
            os.setxattr(tagged, "user.origin", b"mac")
        except OSError:
            self.skipTest("this filesystem has no user extended attributes")
        sparse = self.source / "Documents/disk.img"
        with sparse.open("wb") as output:
            output.truncate(1024 * 1024)
        sparse.chmod(0o600)
        setuid = self.write("Projects/demo/tool", b"#!/bin/bash\n", 0o4750)
        self.assertTrue(setuid.stat().st_mode & 0o4000)
        listed = os.listxattr

        def with_acl(target, *args, **kwargs):
            names = listed(target, *args, **kwargs)
            if isinstance(target, int) and os.fstat(target).st_ino == (self.source / ".config/theme/selected").stat().st_ino:
                return [*names, "system.posix_acl_access"]
            return names

        with patch.object(collection.os, "listxattr", side_effect=with_acl), self.capture() as snapshot:
            lost = {item["archive"]: item["lost"] for item in snapshot.manifest["provenance"]["metadata"]}
            self.assertEqual(lost["Documents/tagged.txt"], ["extended-attributes"])
            self.assertEqual(lost["Documents/disk.img"], ["sparse"])
            self.assertEqual(lost["Projects/demo/tool"], ["special-permission-bits"])
            self.assertEqual(lost[".config/theme/selected"], ["acl"])
            self.assertNotIn(".config/unknown/settings", lost)
            # The content still arrives; only the listed metadata does not.
            self.assertEqual(snapshot.paths["Documents/disk.img"].stat().st_size, 1024 * 1024)
            probe.validate_manifest(snapshot.manifest)

    def test_preserve_rules_are_traceable_without_changing_content(self):
        self.write(".local/state/toggles/hypr/flags.lua", b"blur = false\n")
        with self.capture() as snapshot:
            self.assertEqual(snapshot.paths[".local/state/toggles/hypr/flags.lua"].read_bytes(), b"blur = false\n")
            self.assertEqual(self.entry(snapshot, ".local/state/toggles/hypr/flags.lua")["rule"], "keep-toggles")
            self.assertEqual(self.entry(snapshot, ".local/state/toggles/hypr/flags.lua")["outcome"], "included")

    def test_try_policy_drives_collection_of_a_synthetic_try_home(self):
        document = json.loads((Path(collection.__file__).resolve().parent / "policies/try-omarchy-82927e9.json").read_text())
        block = next(rule for rule in document["rules"] if rule["id"] == "try-hypr-input-overrides")["transform"]["block"]
        home = {
            ".config/hypr/input.lua": b"input { kb_layout = us }\n" + block.encode(),
            ".config/hypr/monitors.lua": b"monitor = Virtual-1\n",
            ".config/chromium-flags.conf": b"--ozone-platform=wayland\n--enable-wayland-ime\n",
            ".config/omarchy/extensions/omarchy-menu.jsonc": b'{\n  "setup.try-omarchy": {"label": "Try"}\n}\n',
            ".config/omarchy/hooks/pre-refresh-pacman.d/restore-arm-pacman": b"#!/bin/bash\n",
            ".config/chromium/Default/Cookies": b"FAKE-CHROMIUM-COOKIES",
            ".local/share/keyrings/login.keyring": b"FAKE-KEYRING",
            "Documents/notes.md": b"personal\n",
        }
        source = self.root / "try-home"
        source.mkdir(mode=0o700)
        for name, data in home.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(data)
            path.chmod(0o600)
        (source / ".local/share/omarchy").symlink_to("/usr/share/omarchy")
        (source / "Work").symlink_to("/mnt/mac")
        with collection.collect_fixture(source, self.request, document, snapshot_parent=self.parent) as snapshot:
            captured = {name: path.read_bytes() for name, path in snapshot.paths.items() if path.is_file() and not path.is_symlink()}
            self.assertEqual(captured[".config/hypr/input.lua"], b"input { kb_layout = us }\n")
            self.assertEqual(captured[".config/chromium-flags.conf"], b"--ozone-platform=wayland\n")
            self.assertEqual(json.loads(captured[".config/omarchy/extensions/omarchy-menu.jsonc"]), {})
            self.assertEqual(captured["Documents/notes.md"], b"personal\n")
            for absent in (".config/hypr/monitors.lua", ".config/omarchy/hooks/pre-refresh-pacman.d/restore-arm-pacman",
                           ".local/share/omarchy", ".config/chromium/Default/Cookies", ".local/share/keyrings/login.keyring"):
                self.assertNotIn(absent, snapshot.paths)
            self.assertEqual(self.entry(snapshot, "Work")["mount"], "mac-share")
            self.assertEqual(snapshot.report["policy_revision"], "try-omarchy/82927e9/4")
            self.assertEqual(snapshot.report["counts"]["held-out"], 2)
            self.assertEqual(snapshot.report["counts"]["excluded"], 3)
            self.assertEqual(snapshot.report["counts"]["transformed"], 3)
            provenance = snapshot.manifest["provenance"]
            self.assertEqual((provenance["policy_revision"], provenance["policy_sha256"]),
                             ("try-omarchy/82927e9/4", snapshot.report["policy_sha256"]))
            self.assertEqual(provenance["collection"]["counts"], snapshot.report["counts"])
            exceptions = {item["source"]: (item["outcome"], item["rule"] or item["store"] or item["mount"])
                          for item in provenance["collection"]["exceptions"]}
            self.assertEqual(exceptions[".config/hypr/monitors.lua"], ("excluded", "try-hypr-monitors"))
            self.assertEqual(exceptions[".config/hypr/input.lua"], ("transformed", "try-hypr-input-overrides"))
            self.assertEqual(exceptions[".config/chromium"], ("held-out", "chromium"))
            self.assertEqual(exceptions["Work"], ("inert-link", "mac-share"))
            self.assertNotIn("Documents/notes.md", exceptions)

    def make_share(self):
        share = self.root / "mac-share"
        files = {"Projects/plan.md": b"plan\n", "photo.jpg": b"\xff\xd8 synthetic", ".ssh/config": b"Host mac-side\n",
                 "open-dir/x.txt": b"x", "a.txt": b"same inode"}
        for name, data in files.items():
            path = share / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            path.write_bytes(data)
        (share / "open-dir").chmod(0o777)
        self.addCleanup((share / "open-dir").chmod, 0o755)
        os.link(share / "a.txt", share / "b.txt")
        os.mkfifo(share / "pipe")
        (share / "secret.txt").write_bytes(b"unreadable")
        (share / "secret.txt").chmod(0o000)
        self.addCleanup((share / "secret.txt").chmod, 0o600)
        (share / "back-to-share").symlink_to("/mnt/mac")
        (share / "latest").symlink_to("Projects/plan.md")
        (share / "escape").symlink_to("../.bashrc")
        (share / "Projects/up").symlink_to("../photo.jpg")
        (self.source / "Work").symlink_to("/mnt/mac")
        (self.source / "Work-copy").symlink_to("/mnt/mac/")
        (self.source / "Notes").symlink_to("/mnt/mac/Projects")
        return share

    def test_unselected_shared_folder_stays_a_link_and_is_not_read(self):
        share = self.make_share()
        scanned = os.scandir

        def guarded(value):
            self.assertNotEqual(Path(f"/proc/self/fd/{value}").resolve() if isinstance(value, int) else Path(value), share)
            return scanned(value)

        with patch.object(collection.os, "scandir", side_effect=guarded), \
                self.capture(share_roots={"mac-share": str(share)}) as snapshot:
            self.assertEqual(self.entry(snapshot, "Work")["outcome"], "inert-link")
            self.assertFalse(any(path.startswith("Work/") for path in snapshot.paths))

    def test_selected_shared_folder_becomes_an_ordinary_directory(self):
        share = self.make_share()
        self.request["selected_mounts"] = ["mac-share"]
        with self.capture(share_roots={"mac-share": str(share)}) as snapshot:
            self.assertTrue(snapshot.paths["Work"].is_dir())
            self.assertEqual(snapshot.paths["Work/Projects/plan.md"].read_bytes(), b"plan\n")
            # Recognized credential locations in a share wait for their own tick.
            ssh = next(item for item in snapshot.report["entries"] if item["archive"] == "Work/.ssh")
            self.assertEqual((ssh["outcome"], ssh["reason"], ssh["store"], ssh["mount"]),
                             ("held-out", "unselected-store", "share-ssh", "mac-share"))
            self.assertFalse(any(path.startswith("Work/.ssh") for path in snapshot.paths))
            self.assertEqual(snapshot.paths["Work/latest"].readlink(), Path("Projects/plan.md"))
            work = self.entry(snapshot, "Work")
            self.assertEqual((work["outcome"], work["reason"], work["mount"]), ("included", "mount-materialized", "mac-share"))
            reasons = {item["archive"]: (item["outcome"], item["reason"]) for item in snapshot.report["entries"]
                       if item["archive"].startswith("Work/")}
            self.assertEqual(reasons["Work/open-dir"], ("unsupported", "unsafe-permissions"))
            self.assertEqual(reasons["Work/pipe"], ("unsupported", "special-file"))
            self.assertEqual(reasons["Work/a.txt"], ("unsupported", "multiply-linked-file"))
            self.assertEqual(reasons["Work/secret.txt"], ("unsupported", "unreadable"))
            self.assertEqual(reasons["Work/back-to-share"], ("inert-link", "mount-link"))
            # The share is renamed to Work: links must not escape into home files.
            self.assertEqual(reasons["Work/escape"], ("unsupported", "share-escape"))
            self.assertNotIn("Work/escape", snapshot.paths)
            self.assertEqual(reasons["Work/Projects/up"], ("included", "link-metadata"))
            self.assertNotIn("Work/open-dir/x.txt", snapshot.paths)
            self.assertEqual(self.entry(snapshot, "Work-copy")["reason"], "mount-already-materialized")
            self.assertEqual(self.entry(snapshot, "Notes")["reason"], "mount-link")
            self.assertNotIn("Notes/plan.md", snapshot.paths)
            probe.validate_manifest(snapshot.manifest)

    def test_nested_mount_inside_a_share_is_skipped_not_fatal(self):
        share = self.make_share()
        self.request["selected_mounts"] = ["mac-share"]
        mount, inode = collection._mount, (share / "Projects").stat().st_ino

        def different(fd):
            value = mount(fd)
            return value + 1 if os.fstat(fd).st_ino == inode else value

        with patch.object(collection, "_mount", side_effect=different), \
                self.capture(share_roots={"mac-share": str(share)}) as snapshot:
            self.assertEqual(next(item for item in snapshot.report["entries"] if item["archive"] == "Work/Projects")["reason"],
                             "other-filesystem")
            self.assertNotIn("Work/Projects/plan.md", snapshot.paths)
            self.assertIn("Work/photo.jpg", snapshot.paths)

    def test_credentials_in_a_mac_home_share_are_held_back_by_name_until_selected(self):
        share = self.make_share()
        for name, data in {"Library/Keychains/login.keychain-db": b"FAKE-KEYCHAIN", ".ssh/id_ed25519": b"FAKE-KEY",
                           "Projects/deploy/server.pem": b"FAKE-PEM", "Documents/talk.key": b"keynote slides"}.items():
            path = share / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            path.write_bytes(data)
        self.request["selected_mounts"] = ["mac-share"]
        held = [share / ".ssh", share / "Library/Keychains", share / "Projects/deploy/server.pem"]
        opened, scanned = os.open, os.scandir

        def resolve(value, dir_fd=None):
            if isinstance(value, int):
                return Path(f"/proc/self/fd/{value}").resolve()
            base = Path(f"/proc/self/fd/{dir_fd}").resolve() if dir_fd is not None else Path.cwd()
            return Path(os.path.abspath(base / value))

        def guarded_open(value, flags, *args, **kwargs):
            path = resolve(value, kwargs.get("dir_fd"))
            self.assertFalse(any(path == root or path.is_relative_to(root) for root in held), str(path))
            return opened(value, flags, *args, **kwargs)

        def guarded_scan(value):
            path = resolve(value)
            self.assertFalse(any(path == root or path.is_relative_to(root) for root in held), str(path))
            return scanned(value)

        with patch.object(collection.os, "open", side_effect=guarded_open), \
                patch.object(collection.os, "scandir", side_effect=guarded_scan), \
                self.capture(share_roots={"mac-share": str(share)}) as snapshot:
            stores = {item["archive"]: item["store"] for item in snapshot.report["entries"] if item["outcome"] == "held-out"
                      and item["mount"] == "mac-share"}
            self.assertEqual(stores, {"Work/.ssh": "share-ssh", "Work/Library/Keychains": "share-keychains",
                                      "Work/Projects/deploy/server.pem": "share-keys"})
            self.assertEqual(snapshot.paths["Work/Documents/talk.key"].read_bytes(), b"keynote slides")
        self.request["selected_share_stores"] = ["share-ssh"]
        with self.capture(share_roots={"mac-share": str(share)}) as snapshot:
            self.assertEqual(snapshot.paths["Work/.ssh/config"].read_bytes(), b"Host mac-side\n")
            # A key file name inside the ticked folder does not hold it back.
            self.assertEqual(snapshot.paths["Work/.ssh/id_ed25519"].read_bytes(), b"FAKE-KEY")
            self.assertNotIn("Work/Library/Keychains/login.keychain-db", snapshot.paths)
            self.assertNotIn("Work/Projects/deploy/server.pem", snapshot.paths)

    def test_a_ticked_key_pattern_does_not_unlock_stores_inside_a_like_named_directory(self):
        share = self.make_share()
        for name in ("certs.pem/.ssh/id_ed25519", "certs.pem/Library/Keychains/login.keychain-db", "certs.pem/notes.txt"):
            path = share / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            path.write_bytes(b"FAKE")
        self.request["selected_mounts"] = ["mac-share"]
        self.request["selected_share_stores"] = ["share-keys"]
        with self.capture(share_roots={"mac-share": str(share)}) as snapshot:
            held = {item["archive"]: item["store"] for item in snapshot.report["entries"] if item["outcome"] == "held-out"}
            # The directory itself matches the ticked *.pem pattern and comes along...
            self.assertIn("Work/certs.pem/notes.txt", snapshot.paths)
            # ...but folder stores inside it still need their own tick.
            self.assertEqual(held["Work/certs.pem/.ssh"], "share-ssh")
            self.assertEqual(held["Work/certs.pem/Library/Keychains"], "share-keychains")
            self.assertNotIn("Work/certs.pem/.ssh/id_ed25519", snapshot.paths)

    def test_unlistable_share_directory_is_rolled_back_and_skipped(self):
        share = self.make_share()
        self.request["selected_mounts"] = ["mac-share"]
        projects = (share / "Projects").stat().st_ino
        scanned = os.scandir

        def refuse_projects(value):
            if isinstance(value, int) and os.fstat(value).st_ino == projects:
                raise PermissionError("protected by macOS privacy controls")
            return scanned(value)

        with patch.object(collection.os, "scandir", side_effect=refuse_projects), \
                self.capture(share_roots={"mac-share": str(share)}) as snapshot:
            items = [item for item in snapshot.report["entries"] if item["archive"] == "Work/Projects"]
            self.assertEqual([(item["outcome"], item["reason"]) for item in items], [("unsupported", "unreadable")])
            self.assertFalse(any(path.startswith("Work/Projects") for path in snapshot.paths))
            self.assertIn("Work/photo.jpg", snapshot.paths)
            numbered = {path.name for path in snapshot.directory.iterdir() if path.name.isdigit()}
            self.assertEqual(numbered, {path.name for path in snapshot.paths.values() if path.name.isdigit()})
            probe.validate_manifest(snapshot.manifest)

    def test_share_root_cannot_overlap_the_home_or_snapshot_location(self):
        self.make_share()
        self.request["selected_mounts"] = ["mac-share"]
        for root in (self.source, self.source / "Projects", self.root, self.parent):
            with self.subTest(root=root), self.assertRaisesRegex(probe.Rejected, "overlaps"):
                with self.capture(share_roots={"mac-share": str(root)}):
                    self.fail("overlapping share root accepted")

    def test_originals_are_refused_under_a_withheld_location(self):
        self.policy["rules"].append({"id": "no-local-share", "path": ".local/share", "match": "tree", "action": "exclude",
                                     "reason": "vm_integration", "evidence": {"path": "synthetic"}})
        self.write(".config/omarchy/extensions/omarchy-menu.jsonc", b'{"setup.vm": {}, "launch.x": {}}\n')
        with self.assertRaisesRegex(probe.Rejected, "withheld"):
            with self.capture():
                self.fail("originals placed under an excluded path")
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_mount_selection_and_roots_are_validated(self):
        share = self.make_share()
        self.request["selected_share_stores"] = ["not-a-store"]
        with self.assertRaisesRegex(probe.Rejected, "share store selection"):
            with self.capture(share_roots={"mac-share": str(share)}):
                self.fail("unknown share store accepted")
        self.request["selected_share_stores"] = []
        for mounts, roots in ((["unknown-share"], {"mac-share": str(share)}), (["mac-share", "mac-share"], None),
                              ([True], None), (["mac-share"], {"mac-share": "relative/path"}),
                              (["mac-share"], {"mac-share": str(share), "extra": str(share)})):
            self.request["selected_mounts"] = mounts
            with self.subTest(mounts=mounts, roots=roots), self.assertRaises(probe.Rejected):
                with self.capture(share_roots=roots):
                    self.fail("invalid share selection accepted")
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_encrypted_snapshot_roundtrip_survives_source_edits_preserves_metadata_and_holdouts(self):
        age = configured_age()
        if age is None:
            self.skipTest("set verified age for encrypted roundtrip")
        ciphertext = self.root / "snapshot.age"
        target, job = self.root / "destination", self.root / "job"
        target.mkdir(mode=0o700)
        job.mkdir(mode=0o700)
        self.write(".config/app-flags.conf", b"--user-choice\n--vm-only\n")
        share = self.make_share()
        self.request["selected_mounts"] = ["mac-share"]
        with self.capture(share_roots={"mac-share": str(share)}) as snapshot:
            manifest = copy.deepcopy(snapshot.manifest)
            report = copy.deepcopy(snapshot.report)
            for name in self.ordinary:
                (self.source / name).write_bytes(b"source changed after capture")
            probe.encrypt(age, SECRET, ciphertext,
                          lambda stream: probe.write_archive(stream, manifest, snapshot.paths))
            self.assert_private(snapshot)
        with restore.verified_bundle(age, SECRET, ciphertext) as bundle:
            with restore.Restorer(bundle, target, job) as importer:
                restored = importer.apply(importer.plan())
        self.assertNotIn("conflict", {action.status for action in restored})
        for name, content in self.ordinary.items():
            self.assertEqual((target / name).read_bytes(), content)
            self.assertEqual((target / name).stat().st_mtime_ns, MTIME)
        self.assertEqual(stat.S_IMODE((target / "Projects/demo/run").stat().st_mode), 0o750)
        self.assertFalse((target / "MUST-NOT-RUN").exists())
        self.assertFalse((target / "secret-alias").exists())
        self.assertEqual((target / "readme-link").read_bytes(), self.ordinary["Projects/demo/modified"])
        held = [item for item in report["entries"] if item["outcome"] == "held-out"]
        self.assertEqual(len([item for item in held if item["mount"] is None]), 8)
        self.assertEqual([item["archive"] for item in held if item["mount"] == "mac-share"], ["Work/.ssh"])
        self.assertTrue(set(self.protected).isdisjoint(entry["path"] for entry in manifest["entries"]))
        self.assertNotIn(b"FAKE-SSH-SECRET", ciphertext.read_bytes())
        self.assertEqual((target / ".config/app-flags.conf").read_bytes(), b"--user-choice\n")
        self.assertTrue((target / "Work").is_dir() and not (target / "Work").is_symlink())
        self.assertEqual((target / "Work/Projects/plan.md").read_bytes(), b"plan\n")
        self.assertFalse(os.path.lexists(target / "Notes"))


if __name__ == "__main__":
    unittest.main()
