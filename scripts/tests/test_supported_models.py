#!/usr/bin/env python3
"""The per-board qualification floor: what each channel offers, and what the
manifest refuses to say."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import supported_models  # noqa: E402

MANIFEST = Path(__file__).resolve().parents[1] / "supported-models.json"


def write(macs: dict, schema: int = supported_models.SCHEMA_VERSION) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="omarchy-floor-"))
    path = directory / "supported-models.json"
    path.write_text(json.dumps({"schema_version": schema, "macs": macs}))
    return path


M4_BOARDS = {
    f"apple,{board}"
    for board in ("j604", "j623", "j624", "j713", "j715", "j773g", "j614s", "j616s", "j773s", "j614c", "j616c", "j575c")
}


class TodaysManifestTests(unittest.TestCase):
    def test_edge_offers_every_m4_and_rc_and_stable_do_not(self) -> None:
        sets = {channel: supported_models.boards_for(channel) for channel in ("edge", "rc", "stable")}
        self.assertEqual(len(sets["stable"]), 35)
        self.assertEqual(sets["rc"], sets["stable"])
        self.assertEqual(set(sets["edge"]) - set(sets["stable"]), M4_BOARDS)
        self.assertTrue(set(sets["stable"]) <= set(sets["rc"]) <= set(sets["edge"]))

    def test_every_m4_names_the_app_that_can_enrol_a_macho_stage_1(self) -> None:
        # Older apps have no AuraStage1 in their native Recovery, so a catalog
        # that admits one may not offer these boards.
        self.assertEqual(supported_models.app_minimums(), dict.fromkeys(M4_BOARDS, "2.3.17"))
        self.assertEqual(
            supported_models.app_minimum_errors("2.3.16", supported_models.boards_for("edge")),
            [f"{board} needs app 2.3.17 or newer, but the catalog admits 2.3.16" for board in sorted(M4_BOARDS)],
        )
        self.assertEqual(
            supported_models.app_minimum_errors("2.3.17", supported_models.boards_for("edge")), []
        )
        # rc and stable do not offer them, so their floor is untouched.
        self.assertEqual(
            supported_models.app_minimum_errors("2.3.0", supported_models.boards_for("stable")), []
        )

    def test_the_remaining_bring_up_board_is_in_no_channel_and_the_refused_stay_out(self) -> None:
        self.assertEqual(set(supported_models.developer_boards()), {"apple,j815"})
        for channel in ("edge", "rc", "stable"):
            offered = supported_models.boards_for(channel)
            for identifier in ("apple,j815", "apple,j575d"):
                self.assertNotIn(identifier, offered, f"{identifier} in {channel}")
        self.assertEqual(set(supported_models.refused()), {"apple,j575d"})

    def test_only_the_boards_short_of_stable_carry_a_note(self) -> None:
        self.assertEqual(set(supported_models.notes()), M4_BOARDS)

    def test_the_manifest_on_disk_loads(self) -> None:
        self.assertEqual(len(supported_models.load(MANIFEST)), 49)


class LadderTests(unittest.TestCase):
    """Edge's set contains rc's, which contains stable's, by construction."""

    MIXED = {
        "apple,j274": {"qualified": "stable"},
        "apple,j700": {"qualified": "rc", "note": "rc and edge only"},
        "apple,j616s": {"qualified": "edge", "note": "edge only"},
        "apple,j815": {"qualified": "developer", "note": "bring-up"},
        "apple,j575d": {"refused": "no engine entry"},
    }

    def setUp(self) -> None:
        self.path = write(self.MIXED)

    def test_each_channel_offers_every_board_qualified_at_or_above_it(self) -> None:
        self.assertEqual(
            supported_models.boards_for("edge", self.path),
            ["apple,j274", "apple,j616s", "apple,j700"],
        )
        self.assertEqual(
            supported_models.boards_for("rc", self.path), ["apple,j274", "apple,j700"]
        )
        self.assertEqual(supported_models.boards_for("stable", self.path), ["apple,j274"])

    def test_the_sets_nest_so_no_channel_can_offer_what_a_looser_one_does_not(self) -> None:
        edge = set(supported_models.boards_for("edge", self.path))
        rc = set(supported_models.boards_for("rc", self.path))
        stable = set(supported_models.boards_for("stable", self.path))
        self.assertTrue(stable <= rc <= edge)

    def test_a_developer_board_reaches_no_channel(self) -> None:
        for channel in ("edge", "rc", "stable"):
            self.assertNotIn("apple,j815", supported_models.boards_for(channel, self.path))
        self.assertEqual(
            supported_models.developer_boards(self.path), {"apple,j815": "bring-up"}
        )

    def test_a_note_travels_for_a_board_short_of_stable_but_not_a_developer_one(self) -> None:
        self.assertEqual(
            supported_models.notes(self.path),
            {"apple,j700": "rc and edge only", "apple,j616s": "edge only"},
        )


class CoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = write(LadderTests.MIXED)

    def test_a_catalog_must_enable_every_mac_its_channel_offers(self) -> None:
        errors = supported_models.coverage_errors(["apple,j274"], "edge", self.path)
        self.assertEqual(len(errors), 1)
        self.assertIn("every Mac qualified for edge must be enabled", errors[0])
        self.assertIn("apple,j616s", errors[0])
        self.assertIn("apple,j700", errors[0])

    def test_a_channel_may_not_offer_a_mac_that_has_not_reached_it(self) -> None:
        errors = supported_models.coverage_errors(
            ["apple,j274", "apple,j616s"], "stable", self.path
        )
        self.assertIn("apple,j616s has only qualified for edge, so stable may not offer it", errors)

    def test_refused_and_unknown_macs_are_named_with_a_reason(self) -> None:
        errors = supported_models.coverage_errors(
            ["apple,j274", "apple,j575d", "apple,j999"], "stable", self.path
        )
        self.assertIn("apple,j575d is refused: no engine entry", errors)
        self.assertTrue(any("apple,j999 is not in" in error for error in errors))

    def test_exactly_the_channels_set_passes(self) -> None:
        for channel in ("edge", "rc", "stable"):
            wanted = supported_models.boards_for(channel, self.path)
            self.assertEqual(supported_models.coverage_errors(wanted, channel, self.path), [])

    def test_a_private_catalog_owes_the_fully_qualified_macs_and_may_add_any_other(self) -> None:
        required = supported_models.boards_for("stable", self.path)
        self.assertEqual(supported_models.private_coverage_errors(required, self.path), [])
        # Anything the manifest names may be added, including a developer board.
        self.assertEqual(
            supported_models.private_coverage_errors(
                required + ["apple,j700", "apple,j616s", "apple,j815"], self.path
            ),
            [],
        )
        # It is tied to the narrowest channel on purpose: a board reaching edge
        # must not break every private catalog.
        self.assertEqual(
            supported_models.private_coverage_errors([], self.path),
            ["every Mac qualified for stable must be enabled; missing apple,j274"],
        )
        self.assertIn(
            "apple,j575d is refused: no engine entry",
            supported_models.private_coverage_errors(required + ["apple,j575d"], self.path),
        )

    def test_an_unknown_channel_is_refused_rather_than_guessed(self) -> None:
        with self.assertRaises(SystemExit):
            supported_models.boards_for("beta", self.path)
        with self.assertRaises(SystemExit):
            supported_models.coverage_errors([], "developer", self.path)


class AppMinimumTests(unittest.TestCase):
    MACS = {
        "apple,j274": {"qualified": "stable"},
        "apple,j616s": {"qualified": "edge", "note": "edge only", "app_minimum": "2.4.0"},
    }

    def setUp(self) -> None:
        self.path = write(self.MACS)

    def test_a_catalog_may_not_offer_a_mac_its_oldest_app_would_refuse(self) -> None:
        offered = supported_models.boards_for("edge", self.path)
        errors = supported_models.app_minimum_errors("2.3.14", offered, self.path)
        self.assertEqual(
            errors, ["apple,j616s needs app 2.4.0 or newer, but the catalog admits 2.3.14"]
        )

    def test_the_exact_minimum_and_anything_newer_pass(self) -> None:
        offered = supported_models.boards_for("edge", self.path)
        for version in ("2.4.0", "2.4.1", "3.0.0", "2.10.0"):
            self.assertEqual(supported_models.app_minimum_errors(version, offered, self.path), [])

    def test_a_mac_with_no_minimum_constrains_nothing(self) -> None:
        self.assertEqual(
            supported_models.app_minimum_errors("1.0.0", ["apple,j274"], self.path), []
        )

    def test_a_catalog_naming_no_minimum_cannot_carry_a_mac_that_needs_one(self) -> None:
        offered = supported_models.boards_for("edge", self.path)
        errors = supported_models.app_minimum_errors(None, offered, self.path)
        self.assertEqual(
            errors, ["apple,j616s needs app 2.4.0 or newer, but the catalog names no minimum version"]
        )

    def test_the_check_runs_through_catalog_errors(self) -> None:
        catalog = {
            "channel": "edge",
            "installer": {"minimumVersion": "2.3.14"},
            "models": [
                {"deviceIdentifier": identifier, "status": "enabled"}
                for identifier in supported_models.boards_for("edge", self.path)
            ],
        }
        self.assertTrue(
            any("needs app 2.4.0" in error for error in supported_models.catalog_errors(catalog, path=self.path))
        )


class CatalogErrorsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = write(LadderTests.MIXED)

    def models(self, channel: str) -> list[dict]:
        return [
            {"deviceIdentifier": identifier, "status": "enabled"}
            for identifier in supported_models.boards_for(channel, self.path)
        ]

    def test_a_catalog_is_judged_by_the_channel_it_names(self) -> None:
        for channel in ("edge", "rc", "stable"):
            catalog = {"channel": channel, "models": self.models(channel)}
            self.assertEqual(supported_models.catalog_errors(catalog, path=self.path), [], channel)

    def test_an_explicit_channel_outranks_the_one_the_catalog_names(self) -> None:
        catalog = {"channel": "edge", "models": self.models("edge")}
        errors = supported_models.catalog_errors(catalog, channel="stable", path=self.path)
        self.assertTrue(any("stable may not offer it" in error for error in errors))

    def test_a_catalog_naming_no_channel_is_judged_as_a_private_one(self) -> None:
        for channel in ("edge", "rc", "stable"):
            catalog = {"models": self.models(channel)}
            self.assertEqual(
                supported_models.catalog_errors(catalog, path=self.path), [], channel
            )
        # Still closed to a Mac the manifest refuses or does not name.
        stray = {"models": self.models("edge") + [
            {"deviceIdentifier": "apple,j575d", "status": "enabled"}
        ]}
        self.assertTrue(supported_models.catalog_errors(stray, path=self.path))

    def test_a_disabled_model_is_named(self) -> None:
        catalog = {"channel": "edge", "models": self.models("edge")}
        catalog["models"][0]["status"] = "disabled"
        self.assertIn(
            "apple,j274 is listed but disabled",
            supported_models.catalog_errors(catalog, path=self.path),
        )


class ManifestValidationTests(unittest.TestCase):
    def assertRefused(self, macs: dict, fragment: str, schema: int | None = None) -> None:
        path = write(macs, supported_models.SCHEMA_VERSION if schema is None else schema)
        with self.assertRaises(SystemExit) as caught:
            supported_models.load(path)
        self.assertIn(fragment, str(caught.exception))

    def test_an_old_or_future_schema_is_refused(self) -> None:
        self.assertRefused({"apple,j274": {"qualified": "stable"}}, "unsupported schema", schema=1)
        self.assertRefused({"apple,j274": {"qualified": "stable"}}, "unsupported schema", schema=3)

    def test_a_board_needs_exactly_one_of_qualified_and_refused(self) -> None:
        self.assertRefused({"apple,j274": {}}, "must name either")
        self.assertRefused(
            {"apple,j274": {"qualified": "stable", "refused": "no"}}, "must name either"
        )

    def test_an_unknown_qualification_is_refused(self) -> None:
        self.assertRefused({"apple,j274": {"qualified": "beta"}}, "must be qualified for one of")

    def test_a_board_that_reached_stable_may_not_carry_a_note(self) -> None:
        self.assertRefused(
            {"apple,j274": {"qualified": "stable", "note": "careful"}},
            "has reached stable, so its note has nothing to warn about",
        )

    def test_a_refused_board_carries_a_reason_and_nothing_else(self) -> None:
        self.assertRefused({"apple,j274": {"refused": ""}}, "needs a reason")
        self.assertRefused(
            {"apple,j274": {"refused": "no", "note": "hm"}}, "cannot carry note"
        )
        self.assertRefused(
            {"apple,j274": {"refused": "no", "app_minimum": "2.0.0"}}, "cannot carry app_minimum"
        )

    def test_an_app_minimum_is_an_x_y_z_version(self) -> None:
        for value in ("2.4", "v2.4.0", "2.4.0-rc1", "", "latest"):
            self.assertRefused(
                {"apple,j274": {"qualified": "stable", "app_minimum": value}},
                "needs an X.Y.Z app_minimum",
            )

    def test_an_unknown_key_is_refused_rather_than_ignored(self) -> None:
        self.assertRefused(
            {"apple,j274": {"qualified": "stable", "chanel": "edge"}}, "unknown keys: chanel"
        )

    def test_a_board_identifier_must_look_like_one(self) -> None:
        for identifier in ("j274", "apple,", "Apple,J274", "apple,j274!", "apple,j 274"):
            self.assertRefused({identifier: {"qualified": "stable"}}, "is not an apple,<board>")

    def test_a_k_series_board_identifier_is_accepted(self) -> None:
        # The M5 Pro and Max boards are k-prefixed; nothing may assume apple,j.
        path = write({"apple,k116c": {"qualified": "developer", "note": "M5 Max 16-inch"}})
        self.assertEqual(list(supported_models.load(path)), ["apple,k116c"])

    def test_an_empty_manifest_is_refused(self) -> None:
        self.assertRefused({}, "macs maps each Mac to a record")


if __name__ == "__main__":
    unittest.main()
