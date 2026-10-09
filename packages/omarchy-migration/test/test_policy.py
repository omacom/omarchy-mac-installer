import copy
import json
from pathlib import Path
import unittest

from omarchy_migration import contract, policy, survey

HERE = Path(__file__).resolve().parent
DOCUMENT = json.loads(survey.POLICY_PATH.read_text())

# Try's seeded file at e1a0dbe (guest/native-overlay/etc/skel/...).
SEEDED_MENU = b'''{
  "setup.try-omarchy": {
    "icon": "\xef\x84\xb3",
    "label": "Try Omarchy Settings",
    "description": "Open the Mac app settings",
    "action": "omarchy-native-settings",
    "when": "test -w /dev/virtio-ports/dev.tryomarchy.settings"
  }
}
'''

# What Try appended to input.lua for VMs created 2026-09-09..21 (1ec823c,
# guest/scripts/materialize-omarchy.sh), before the keyboard line existed.
PINCH_ONLY_BLOCK = b'''
-- Try Omarchy's host pinch device carries gestures only.
dofile("/usr/share/try-omarchy/pinch-input.lua")
'''

# What Try's existing-VM repair (guest/scripts/migrate-user-fixes.py) appends
# to older VMs since 2026-10-02 (d56a3c0): a newline, then
# guest/native-overlay/usr/share/try-omarchy/pinch-input.lua verbatim; the
# policy cites that file, which holds the text.
REPAIRED_BLOCK = b'''
-- This device carries reconstructed pinch contacts, not physical fingers.
-- Keep taps and keyboard palm rejection from changing those gestures.
hl.device({
  name = "qemu-virtio-pinch-touchpad",
  tap_to_click = false,
  disable_while_typing = false,
})
'''

# The same file after the Touch ID and integrations entries and a user edit.
EXTENDED_MENU = b'''{
  // My own shortcuts stay.
  "setup.try-omarchy": {
    "label": "Try Omarchy Settings",
    "action": "omarchy-native-settings"
  },
  "setup.security.touch-id": {"label":"Touch ID","action":"try-omarchy-touch-id"},
  "launch.notes": {"label": "Notes", "action": "obsidian"},  /* keep */
  "setup.try-omarchy-integrations": {"label":"Try Omarchy Integrations","action":"/usr/local/bin/try-omarchy-integrations"},
}
'''


def rule(rule_id):
    return next(item for item in DOCUMENT["rules"] if item["id"] == rule_id)


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.policy = policy.Policy(DOCUMENT)

    def test_stores_govern_their_whole_tree(self):
        self.assertEqual(self.policy.match(".ssh/id_ed25519"), ("store", self.policy.stores[0]))
        self.assertEqual(self.policy.match(".config/chromium/Default/Cookies").item["id"], "chromium")

    def test_exact_rules_do_not_cover_children_or_siblings(self):
        self.assertEqual(self.policy.match(".config/hypr/monitors.lua").item["id"], "try-hypr-monitors")
        self.assertIsNone(self.policy.match(".config/hypr/monitors.lua.bak"))
        self.assertIsNone(self.policy.match(".config/hypr"))
        self.assertIsNone(self.policy.match(".config/chromium-flags.conf.d/x"))

    def test_tree_rules_cover_descendants(self):
        self.assertEqual(self.policy.match(".local/state/omarchy/toggles/hypr/flags.lua").item["id"],
                         "omarchy-hypr-toggles")

    def test_excluding_a_directory_excludes_its_descendants(self):
        self.assertEqual(self.policy.match(".local/share/omarchy/bin/omarchy").item["id"], "omarchy-runtime-link")
        self.assertIsNone(self.policy.match(".config/fcitx5/profile/extra"))

    def test_revision_two_decisions_from_the_first_real_survey(self):
        stores = {".mozilla/firefox/abc.default/cookies.sqlite": "firefox", ".pi/agent/auth.json": "pi",
                  ".local/share/pki/nssdb/cert9.db": "nss", ".local/share/keyrings/login.keyring": "gnome-keyring"}
        for path, store in stores.items():
            self.assertEqual(self.policy.match(path), ("store", next(s for s in self.policy.stores if s["id"] == store)))
        excluded = {".local/share/mise/installs/node/22/bin/node": "mise-installs",
                    ".local/share/voxtype/models/base.bin": "voxtype-models",
                    ".local/state/wireplumber/default-nodes": "wireplumber-state",
                    ".local/state/omarchy/clipboard-images/1.png": "omarchy-clipboard-images",
                    ".local/state/omarchy/clipboard-history.json": "omarchy-clipboard-history",
                    ".local/state/omarchy/migrations/1700000000.sh": "omarchy-migrations",
                    ".local/state/omarchy/first-run.log": "omarchy-first-run-log",
                    ".local/state/omarchy/notifications.json": "omarchy-notifications-file"}
        for path, rule_id in excluded.items():
            match = self.policy.match(path)
            self.assertEqual((match.kind, match.item["id"], match.item["action"]), ("rule", rule_id, "exclude"), path)
        for path in (".pi/agent/settings.json", ".pi/agent/themes/omarchy-system.json", ".config/mise/config.toml",
                     ".local/state/omarchy/current/theme/name", ".local/state/omarchy/agents/state.json",
                     ".local/share/applications/chatgpt.desktop", ".local/share/zoxide/db.zo"):
            self.assertIsNone(self.policy.match(path), path)
        self.assertEqual(self.policy.match(".local/state/omarchy/toggles/hypr/flags.lua").item["action"], "preserve")

    def test_revision_three_stores_from_the_windows_importer_comparison(self):
        for path, store in ((".config/google-chrome/Default/Cookies", "google-chrome"),
                            (".config/vivaldi-snapshot/Default/Login Data", "vivaldi"),
                            (".config/microsoft-edge-dev/Local State", "microsoft-edge"),
                            (".config/net.imput.helium/Default/Cookies", "helium"),
                            (".config/mozilla/firefox/profiles.ini", "firefox-xdg"),
                            (".cargo/credentials.toml", "cargo"),
                            (".local/share/opencode/auth.json", "opencode"),
                            (".config/zed/credentials.json", "zed")):
            match = self.policy.match(path)
            self.assertEqual((match.kind, match.item["id"]), ("store", store), path)
        for path in (".cargo/config.toml", ".config/zed/settings.json", ".local/share/opencode/sessions/a.json"):
            self.assertIsNone(self.policy.match(path), path)
        self.assertEqual(self.policy.share_store("old/.config/google-chrome")["id"], "share-browser-profiles")

    def test_try_settings_desktop_override_is_excluded_but_other_entries_migrate(self):
        match = self.policy.match(".local/share/applications/try-omarchy-settings.desktop")
        self.assertEqual((match.item["id"], match.item["action"]), ("try-settings-desktop-override", "exclude"))
        self.assertIsNone(self.policy.match(".local/share/applications/try-omarchy-settings.desktop.bak"))

    def test_menu_removal_handles_the_current_try_entry(self):
        current = b'{\n  "setup.try-omarchy": {\n    "icon": "x",\n    "iconFont": "omarchy",\n    "label": "Try Omarchy Settings"\n  }\n}\n'
        self.assertEqual(json.loads(self.policy.transform(rule("try-menu-entries"), current).data), {})

    def test_share_stores_match_names_at_any_depth_and_nothing_else(self):
        cases = {".ssh": "share-ssh", "Users-backup/scott/.ssh": "share-ssh", "Library/Keychains": "share-macos-keychains",
                 "old-mac/Library/Application Support/Google/Chrome": "share-browser-profiles",
                 "deploy/server.pem": "share-private-keys", "id_ed25519": "share-private-keys"}
        for path, store in cases.items():
            self.assertEqual(self.policy.share_store(path)["id"], store, path)
        for path in ("Keychains", ".ssh-notes", "Documents/talk.key", "Library/Application Support/Google",
                     "id_ed25519.pub", "notes/pem.txt"):
            self.assertIsNone(self.policy.share_store(path), path)

    def test_unknown_personal_paths_are_not_matched(self):
        for path in ("Documents/report.md", ".config/nvim/init.lua", ".bashrc", ".sshconfig"):
            self.assertIsNone(self.policy.match(path), path)

    def test_match_rejects_unsafe_paths(self):
        for path in ("../.ssh", "/etc/passwd", ".config//hypr", ""):
            with self.assertRaises(contract.ContractError):
                self.policy.match(path)

    def test_links_into_the_mac_share_are_recognized(self):
        self.assertEqual(self.policy.mount("/mnt/mac")["id"], "mac-share")
        self.assertEqual(self.policy.mount("/mnt/mac/Projects/x")["id"], "mac-share")
        self.assertEqual(self.policy.mount("//mnt/./mac/a")["id"], "mac-share")
        self.assertIsNone(self.policy.mount("/mnt/macintosh"))
        self.assertIsNone(self.policy.mount("Projects/x"))
        self.assertIsNone(self.policy.mount("/home/../mnt/mac"))
        self.assertIsNone(self.policy.mount("/mnt/mac/../../etc"))

    def test_policy_rejects_documents_that_are_not_policies(self):
        with self.assertRaises(contract.ContractError):
            policy.Policy(json.loads((HERE / "fixtures/valid/plan.json").read_text()))

    def test_policy_keeps_its_own_copy(self):
        document = copy.deepcopy(DOCUMENT)
        loaded = policy.Policy(document)
        document["rules"].clear()
        self.assertTrue(loaded.rules)


class StripBlockTests(unittest.TestCase):
    def setUp(self):
        self.policy = policy.Policy(DOCUMENT)

    def test_try_input_block_is_removed_and_user_edits_survive(self):
        block = rule("try-hypr-input-overrides")["transform"]["block"].encode()
        original = b"input {\n  kb_layout = us\n}\n" + block + b"\n-- my mouse speed\nsensitivity = 0.3\n"
        result = self.policy.transform(rule("try-hypr-input-overrides"), original)
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.data, b"input {\n  kb_layout = us\n}\n\n-- my mouse speed\nsensitivity = 0.3\n")

    def test_chromium_flag_line_is_removed_only_on_a_line_boundary(self):
        flags = rule("try-chromium-wayland-ime")
        result = self.policy.transform(flags, b"--ozone-platform=wayland\n--enable-wayland-ime\n")
        self.assertEqual(result, ("--ozone-platform=wayland\n".encode(), "applied"))
        untouched = b"--no-enable-wayland-ime\n"
        self.assertEqual(self.policy.transform(flags, untouched), (untouched, "not-applicable"))

    def test_missing_block_leaves_file_unchanged(self):
        data = b"-- nothing from Try here\n"
        self.assertEqual(self.policy.transform(rule("try-hypr-input-overrides"), data), (data, "not-applicable"))

    def test_crlf_and_missing_final_newline_variants_are_stripped(self):
        flags = rule("try-chromium-wayland-ime")
        self.assertEqual(self.policy.transform(flags, b"--x\r\n--enable-wayland-ime\r\n"), (b"--x\r\n", "applied"))
        self.assertEqual(self.policy.transform(flags, b"--x\n--enable-wayland-ime"), (b"--x\n", "applied"))
        block = rule("try-hypr-input-overrides")["transform"]["block"]
        crlf = b"input {}\r\n" + block.replace("\n", "\r\n").encode()
        self.assertEqual(self.policy.transform(rule("try-hypr-input-overrides"), crlf), (b"input {}\r\n", "applied"))

    def test_leftover_block_lines_are_residual(self):
        input_rule = rule("try-hypr-input-overrides")
        partial = b'input {}\ndofile("/usr/share/try-omarchy/pinch-input.lua")\n'
        self.assertEqual(self.policy.transform(input_rule, partial), (None, "residual"))
        self.assertEqual(self.policy.transform(rule("try-chromium-wayland-ime"), b"  --enable-wayland-ime  \n"),
                         (None, "residual"))

    def test_every_block_try_has_shipped_is_stripped(self):
        mine = b"input {\n  kb_layout = us\n}\n-- my mouse speed\nsensitivity = 0.3\n"
        for name, block in (("current", rule("try-hypr-input-overrides")["transform"]["block"].encode()),
                            ("factory 2026-09-09..21", PINCH_ONLY_BLOCK),
                            ("repaired since 2026-10-02", REPAIRED_BLOCK)):
            with self.subTest(name=name):
                result = self.policy.transform(rule("try-hypr-input-overrides"), mine + block)
                self.assertEqual(result, (mine, "applied"))

    def test_residue_is_judged_by_try_markers_not_generic_lua(self):
        own_device = (b'hl.device({\n  name = "my-trackpad",\n  tap_to_click = false,\n'
                      b'  disable_while_typing = false,\n})\n')
        input_rule = rule("try-hypr-input-overrides")
        self.assertEqual(self.policy.transform(input_rule, own_device), (own_device, "not-applicable"))
        self.assertEqual(self.policy.transform(input_rule, own_device + REPAIRED_BLOCK), (own_device, "applied"))
        leftover = own_device + b'hl.device({\n  name = "qemu-virtio-pinch-touchpad",\n})\n'
        self.assertEqual(self.policy.transform(input_rule, leftover), (None, "residual"))

    def test_earlier_blocks_cite_the_try_commit_that_wrote_them(self):
        transform = rule("try-hypr-input-overrides")["transform"]
        self.assertEqual({item["block"].encode() for item in transform["earlier"]}, {PINCH_ONLY_BLOCK, REPAIRED_BLOCK})
        for damage in ({"commit": "1ec823c"}, {"path": "/abs"}, {"block": ""}, {"block": "-- no marker here\n"}):
            document = copy.deepcopy(DOCUMENT)
            target = next(item for item in document["rules"] if item["id"] == "try-hypr-input-overrides")
            target["transform"]["earlier"][0].update(damage)
            with self.subTest(damage=damage), self.assertRaises(contract.ContractError):
                contract.validate(document)

    def test_repeated_block_is_ambiguous(self):
        line = b"--enable-wayland-ime\n"
        self.assertEqual(self.policy.transform(rule("try-chromium-wayland-ime"), line * 2), (None, "ambiguous"))

    def test_oversized_input_is_refused(self):
        data = b"x" * (policy.MAX_TRANSFORM_INPUT + 1)
        self.assertEqual(self.policy.transform(rule("try-chromium-wayland-ime"), data), (None, "too-large"))

    def test_non_transform_rule_cannot_be_applied(self):
        with self.assertRaises(ValueError):
            self.policy.transform(rule("try-hypr-monitors"), b"")


class RemoveKeysTests(unittest.TestCase):
    def setUp(self):
        self.policy = policy.Policy(DOCUMENT)
        self.menu = rule("try-menu-entries")

    def keys(self, data):
        return [member[0] for member in policy._Scanner(data.decode()).members()]

    def test_seeded_try_menu_becomes_an_empty_object(self):
        result = self.policy.transform(self.menu, SEEDED_MENU)
        self.assertEqual(result.status, "applied")
        self.assertEqual(self.keys(result.data), [])
        self.assertEqual(json.loads(result.data), {})

    def test_user_entries_and_comments_survive(self):
        result = self.policy.transform(self.menu, EXTENDED_MENU)
        self.assertEqual(result.status, "applied")
        self.assertEqual(self.keys(result.data), ["launch.notes"])
        text = result.data.decode()
        self.assertIn("// My own shortcuts stay.", text)
        self.assertIn('"launch.notes": {"label": "Notes", "action": "obsidian"}', text)
        self.assertNotIn("try-omarchy", text)
        self.assertNotIn("touch-id", text)

    def test_removing_the_last_member_leaves_no_trailing_comma(self):
        data = b'{\n  "launch.notes": {"a": 1},\n  "setup.try-omarchy": {"b": 2}\n}\n'
        result = self.policy.transform(self.menu, data)
        self.assertEqual(json.loads(result.data), {"launch.notes": {"a": 1}})

    def test_file_without_try_keys_is_unchanged(self):
        data = b'{"launch.notes": {"label": "Notes"}}\n'
        self.assertEqual(self.policy.transform(self.menu, data), (data, "not-applicable"))

    def test_strings_that_look_like_keys_or_comments_are_not_parsed_as_such(self):
        data = b'{\n  "launch.x": {"action": "echo \\"setup.try-omarchy\\" // not a comment"},\n  "setup.try-omarchy": {}\n}\n'
        result = self.policy.transform(self.menu, data)
        self.assertEqual(self.keys(result.data), ["launch.x"])
        self.assertIn('// not a comment', result.data.decode())

    def test_removing_the_last_member_keeps_comments_between_members(self):
        data = b'{\n  "launch.notes": 1, // keep\n  // user note\n  "setup.try-omarchy": 2\n}\n'
        result = self.policy.transform(self.menu, data)
        self.assertEqual(self.keys(result.data), ["launch.notes"])
        self.assertIn("// keep", result.data.decode())
        self.assertIn("// user note", result.data.decode())
        self.assertNotIn("launch.notes\": 1,", result.data.decode())

    def test_carriage_return_ends_a_line_comment(self):
        for data in (b'{"launch.a": 1, // c\r "setup.try-omarchy": 2\n}',
                     b'{"launch.a": 1 // c\r, "setup.try-omarchy": 2\n}'):
            with self.subTest(data=data):
                result = self.policy.transform(self.menu, data)
                self.assertEqual(result.status, "applied")
                self.assertNotIn("setup.try-omarchy", result.data.decode())

    def test_malformed_or_ambiguous_input_fails_closed(self):
        for data in (b'{"setup.try-omarchy": {}', b'[]', b'{"a": 1} {"b": 2}',
                     b'{"setup.try-omarchy": 1, "setup.try-omarchy": 2}', b'{"a": tru}',
                     b'{"a": "unterminated}', b'{/* open', b'\xff', b'{"a": NaN}', b'{"a": 1_0}',
                     b'{"a": +1}', b'{"a": Infinity}', b'{"a": 01}', '{"a": 1 // c\u2028 "setup.try-omarchy": 2}'.encode()):
            with self.subTest(data=data):
                self.assertEqual(self.policy.transform(self.menu, data), (None, "failed"))


if __name__ == "__main__":
    unittest.main()
