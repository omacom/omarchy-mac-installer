# SPDX-License-Identifier: MIT
# Ported from the MT7932 policy extractor by Chris Kearney, used under the MIT license.
# policy_parse follows the kernel driver's mt7932_policy_parse by Ace and Ryan Murray,
# used under the MIT license with their agreement (2026-10-20).
# See Engine/overlay/ORIGINS-mt7932-policy.md.
"""The MT7932 country policy builder, its text grammar and its parser gate.

Synthetic fixtures only. No firmware image, power table, or unit identity
is stored in this test. Set OMARCHY_NEO_IZUBA to a directory holding the five
25G83 tables to also derive the real policy and check its pinned hashes.
"""

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import omarchy_mt7932_policy as policy  # noqa: E402

tool = policy
EINVAL = policy.EINVAL
# Table 2 starts after the header, the mode records, table 0 and table 1.
TABLE2 = 16 + 532 + 1020


def build_policy(alpha=b"XZ", modes=2, mode_length=1, mode_first=0, table2=None,
                 pads=None, channels=None, trailing=b"", size_override=None):
    tables = []
    for index in range(13):
        if 1 <= index <= 9:
            count = 5 if index == 9 else 8
            if index == 2:
                count = 6 if alpha == b"XZ" else 5
            if index == 2 and table2 is not None:
                count = table2
            number = 44 + count * 122
            table = bytearray(number)
            table[0] = 3
            table[1] = 0x0A
            table[2:4] = number.to_bytes(2, "little")
            table[4] = count
            table[5] = 1 if index <= 2 else 2
            table[6] = 1 if index in (2, 9) else 0
            table[8:10] = alpha
            table[12:44] = (pads or {}).get(index, b"P" * 32)
            for channel in range(count):
                expected = (index - 1) * 8 + channel + 1
                table[44 + channel * 122] = (channels or {}).get((index, channel), expected)
        else:
            lengths = {0: 532, 10: 144, 11: 58, 12: 265}
            types = {0: 5, 10: 1, 11: 4, 12: 2}
            number = lengths[index]
            table = bytearray(number)
            table[1] = 2
            table[2:4] = number.to_bytes(2, "little")
            table[7] = types[index]
            table[12:44] = b"Q" * 32
            if index == 10:
                table[8:10] = alpha
        tables.append(table)
    body = bytearray()
    for _ in range(modes):
        mode = bytearray(328)
        mode[0:4] = mode_first.to_bytes(4, "little")
        mode[4:8] = mode_length.to_bytes(4, "little")
        body += mode
    body += b"".join(tables)
    total = 16 + len(body)
    header = bytearray(16)
    header[0:4] = b"J7RP"
    header[4:6] = (1).to_bytes(2, "little")
    header[6:8] = alpha
    header[8] = modes
    header[9] = 13
    header[12:16] = (size_override if size_override is not None else total).to_bytes(4, "little")
    return bytes(header + body + trailing)


def groups_text(columns, row):
    chunks = []
    for name, cols in columns.items():
        chunks.append("<" + name + ", " + ", ".join(cols) + ">")
        if row == "empty":
            pass
        elif row == "y" and name == "ofdm":
            chunks.append("ch038," + ",".join(["Y"] * len(cols)))
        else:
            chunks.append("ch001," + ",".join(["1"] * len(cols)))
        chunks.append("</" + name + ">")
    return "\r\n".join(chunks)


def power_text(sections=("XZ", "US"), row="value", version=None, format_line="<Ver:03>"):
    version = version or tool.PINNED_VERSION[tool.POWER_NAME]
    parts = [version]
    if format_line is not None:
        parts.append(format_line)
    body = groups_text(tool.POWER_COLUMNS, "y" if row == "y" else ("empty" if row == "empty" else "value"))
    for section in sections:
        parts.append(f"[{section}]\r\n" + body)
    return "\r\n".join(parts) + "\r\n"


def sar_text(sections=("XZ", "US")):
    parts = [tool.PINNED_VERSION[tool.SAR_NAME], "<Ver:01>"]
    columns = tool.SAR_TEXT_COLUMNS["tab"]
    rows = ["<tab, " + ", ".join(columns) + ">"]
    for index in range(1, 21):
        rows.append(f"subband{index}," + ",".join(["1"] * len(columns)))
    rows.append("</tab>")
    body = "\r\n".join(rows)
    for section in sections:
        parts.append(f"[{section}]\r\n" + body)
    return "\r\n".join(parts) + "\r\n"


def common_text(sections=("XZ", "US")):
    parts = [tool.PINNED_VERSION[tool.COMMON_NAME]]
    body = "<common_path_backoff, backoff>\r\nch001,1\r\n</common_path_backoff>"
    for section in sections:
        parts.append(f"[{section}]\r\n" + body)
    return "\r\n".join(parts) + "\r\n"


def db_text(labels=("XZ", "US"), field75="0", sixg=(("AL", "0"),)):
    def block(name, count, rows):
        header = "<" + name + "," + ",".join(f"c{index}" for index in range(count)) + ">"
        body = [header]
        for label, marker in rows:
            cells = ["1"] * count
            cells[75] = marker
            body.append(label + "," + ",".join(cells) + ",")
        body.append("</end>")
        return "\r\n".join(body)
    return "\r\n".join((
        tool.PINNED_VERSION[tool.DB_NAME],
        block("2gCC", 76, [(label, field75) for label in labels]),
        block("6gCC", 116, sixg),
    )) + "\r\n"


def sdb_text(cell="0"):
    columns = tool.SDB_COLUMNS
    parts = [tool.PINNED_VERSION[tool.SDB_NAME]]
    parts.append("<sdb, " + ", ".join(columns) + ">")
    for index in range(1, 18):
        parts.append(f"subband{index}," + ",".join([cell] * len(columns)))
    parts.append("</sdb>")
    return "\r\n".join(parts) + "\r\n"


SENTINEL = "424242"


def synthetic_tables():
    """Five synthetic tables that join for XZ and US; US enables DSA."""
    return {
        tool.POWER_NAME: power_text().replace("ch001,1", f"ch001,{SENTINEL}", 1).encode(),
        tool.SAR_NAME: sar_text().encode(),
        tool.COMMON_NAME: common_text().encode(),
        tool.DB_NAME: db_text(sixg=(("AL", "0"), ("US", "1"))).encode(),
        tool.SDB_NAME: sdb_text().encode(),
    }


def synthetic_pins():
    """The pins the synthetic tables would need, for tests through collect()."""
    return {path: hashlib.sha256(blob).hexdigest()
            for path, blob in policy.derive(synthetic_tables(), pins=None, input_pins=None).items()}


def write_tables(directory, tables=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in (synthetic_tables() if tables is None else tables).items():
        (directory / name).write_bytes(data)


class PolicyParserTests(unittest.TestCase):
    """policy_parse follows the stock driver: six table-2 records only for XZ."""

    def test_policy_rules(self):
        accept = 0
        reject = -EINVAL
        xz = build_policy()
        us = build_policy(alpha=b"US")
        cases = [
            (xz, accept, b"XZ"),
            (build_policy(modes=3), accept, b"XZ"),
            (us, accept, b"US"),
            (xz, reject, b"US"),
            (us, reject, b"XZ"),
            (build_policy(trailing=b"\x00"), reject, b"XZ"),
            (build_policy(size_override=0), reject, b"XZ"),
            (build_policy(modes=1), reject, b"XZ"),
            (build_policy(modes=4), reject, b"XZ"),
            (build_policy(mode_length=0), reject, b"XZ"),
            (build_policy(mode_length=321), reject, b"XZ"),
            (build_policy(mode_length=320), accept, b"XZ"),
            (build_policy(mode_first=1), reject, b"XZ"),
            (build_policy(table2=5), reject, b"XZ"),
            (build_policy(alpha=b"US", table2=5), accept, b"US"),
            (build_policy(alpha=b"US", table2=6), reject, b"US"),
            (build_policy(channels={(1, 0): 2}), reject, b"XZ"),
            (build_policy(channels={(3, 0): 99}), accept, b"XZ"),
            (build_policy(pads={3: b"X" * 32}), reject, b"XZ"),
            (build_policy(pads={9: b"X" * 32}), reject, b"XZ"),
            (build_policy(alpha=b"US", pads={3: b"X" * 32}), accept, b"US"),
            # The bound is the whole u32 at +4, not its low half alone.
            (build_policy(mode_length=1 + (1 << 16)), reject, b"XZ"),
        ]
        for blob, expected, alpha in cases:
            with self.subTest(expected=expected, alpha=alpha, size=len(blob)):
                self.assertEqual(policy.policy_parse(blob, alpha), expected)
        different_fixed = bytearray(xz)
        # Table 0 starts after the header and two mode records. Its pad is not compared.
        table0 = 16 + 2 * 328
        different_fixed[table0 + 12:table0 + 44] = b"Z" * 32
        self.assertEqual(policy.policy_parse(bytes(different_fixed), b"XZ"), accept)
        table10_length_marker = bytes(different_fixed).find(bytes((0, 2)) + (144).to_bytes(2, "little"))
        self.assertGreater(table10_length_marker, table0)
        poked = bytearray(xz)
        poked[table10_length_marker + 12:table10_length_marker + 44] = b"Z" * 32
        self.assertEqual(policy.policy_parse(bytes(poked), b"XZ"), accept)
        poked[table10_length_marker + 8:table10_length_marker + 10] = b"QQ"
        self.assertEqual(policy.policy_parse(bytes(poked), b"XZ"), reject)
        bad_version = bytearray(xz)
        bad_version[4] = 2
        self.assertEqual(policy.policy_parse(bytes(bad_version), b"XZ"), reject)
        bad_reserved = bytearray(xz)
        bad_reserved[10] = 1
        self.assertEqual(policy.policy_parse(bytes(bad_reserved), b"XZ"), reject)
        self.assertEqual(policy.policy_parse(xz + bytes(16385 - len(xz)), b"XZ"), reject)


class FiveRecordTests(unittest.TestCase):
    """Non-XZ files drop table 2's channel-14 record for the stock parser."""

    def test_the_channel_14_record_is_dropped_and_lengths_follow(self):
        six = policy.build_country_policy(b"US", {}, {}, _sar_rows(), {}, _sdb_rows(), 0)
        self.assertEqual(policy.policy_parse(six, b"US"), -EINVAL)
        five = policy.five_record_table2(six)
        self.assertEqual(policy.policy_parse(five, b"US"), 0)
        table2 = TABLE2 + 2 * 328
        self.assertEqual(len(five), len(six) - 122)
        self.assertEqual(int.from_bytes(five[12:16], "little"), len(five))
        self.assertEqual(five[table2 + 4], 5)
        self.assertEqual(int.from_bytes(five[table2 + 2:table2 + 4], "little"), 654)
        self.assertEqual(five[table2 + 44 + 4 * 122], 13)
        end = table2 + 654
        self.assertEqual(five[:12], six[:12])
        self.assertEqual(five[16:table2 + 2], six[16:table2 + 2])
        self.assertEqual(five[table2 + 5:end], six[table2 + 5:end])
        self.assertEqual(five[end:], six[end + 122:])

    def test_three_mode_records_and_shapes_it_cannot_shorten(self):
        six = policy.build_country_policy(b"US", {}, {}, _sar_rows(), {}, _sdb_rows(), 1)
        five = policy.five_record_table2(six)
        self.assertEqual((len(six), len(five)), (10569, 10447))
        self.assertEqual(policy.policy_parse(five, b"US"), 0)
        for blob in (five, b"J7RP", b"nope" + six[4:]):
            with self.subTest(size=len(blob)), self.assertRaises(policy.PolicyError):
                policy.five_record_table2(blob)


class DeriveTests(unittest.TestCase):
    """derive() turns the five tables into parser-gated world-XZ and US files."""

    def test_synthetic_tables_derive_required_policies(self):
        files = policy.derive(synthetic_tables(), pins=None, input_pins=None)
        self.assertEqual(sorted(files), [
            "mediatek/mt7932/policy/US.bin", "mediatek/mt7932/policy/world-XZ.bin",
        ])
        world = files["mediatek/mt7932/policy/world-XZ.bin"]
        country = files["mediatek/mt7932/policy/US.bin"]
        self.assertEqual(len(world), 10241)
        self.assertEqual(len(country), 10447)
        self.assertEqual(policy.policy_parse(world, b"XZ"), 0)
        self.assertEqual(policy.policy_parse(country, b"US"), 0)
        self.assertIn(b"SarEnableConfig 1", world)
        self.assertIn(b"coex tasar_set 0 0", world)
        self.assertIn(b"coex tasar_set 13 0", country)
        table2 = TABLE2 + 2 * 328
        self.assertEqual(world[table2 + 4], 6)
        self.assertEqual(world[table2 + 44], 9)
        self.assertEqual(world[table2 + 44 + 5 * 122], 14)
        table2_us = TABLE2 + 3 * 328
        self.assertEqual(country[table2_us + 4], 5)
        self.assertEqual(country[table2_us + 44], 9)
        self.assertEqual(country[table2_us + 44 + 4 * 122], 13)
        for blob in (world, country):
            self.assertNotIn(SENTINEL.encode(), blob)

    def test_the_pinned_hashes_gate_the_result(self):
        tables = synthetic_tables()
        with self.assertRaisesRegex(policy.PolicyError, "world-XZ.bin is not the pinned policy"):
            policy.derive(tables, input_pins=None)
        pins = synthetic_pins()
        self.assertEqual(policy.derive(tables, pins=pins, input_pins=None, inventory_pin=None), policy.derive(tables, pins=None, input_pins=None))
        wrong = dict(pins)
        wrong["mediatek/mt7932/policy/US.bin"] = "0" * 64
        with self.assertRaisesRegex(policy.PolicyError, "US.bin is not the pinned policy"):
            policy.derive(tables, pins=wrong, input_pins=None, inventory_pin=None)

    def test_version_lines_and_joins_are_checked(self):
        tables = synthetic_tables()
        tables[tool.POWER_NAME] = power_text(version="{Ver:later}").encode()
        with self.assertRaises(policy.SchemaError) as caught:
            policy.derive(tables, pins=None, input_pins=None)
        self.assertEqual(caught.exception.code, "version")
        tables = synthetic_tables()
        tables[tool.SAR_NAME] = sar_text(sections=("XZ",)).encode()
        with self.assertRaisesRegex(policy.PolicyError, "join US missing sar"):
            policy.derive(tables, pins=None, input_pins=None)
        tables = synthetic_tables()
        del tables[tool.SDB_NAME]
        with self.assertRaisesRegex(policy.SchemaError, "missing"):
            policy.derive(tables, pins=None, input_pins=None)
        tables = synthetic_tables()
        tables[tool.SDB_NAME] = "é".encode() + tables[tool.SDB_NAME]
        with self.assertRaises(policy.SchemaError):
            policy.derive(tables, pins=None, input_pins=None)

    def test_all_complete_countries_are_derived(self):
        sections = ("XZ", "US", "CA", "GB", "DE", "JP")
        tables = synthetic_tables()
        tables[tool.POWER_NAME] = power_text(sections=sections + ("MH", "X2", "KP")).encode()
        tables[tool.SAR_NAME] = sar_text(sections=sections).encode()
        tables[tool.COMMON_NAME] = common_text(sections=sections).encode()
        tables[tool.DB_NAME] = db_text(labels=sections).encode()
        files = policy.derive(tables, pins=None, input_pins=None)
        self.assertEqual(set(files), {
            "mediatek/mt7932/policy/" + ("world-XZ" if country == "XZ" else country) + ".bin"
            for country in sections
        })
        for country in sections:
            name = "world-XZ" if country == "XZ" else country
            self.assertEqual(policy.policy_parse(files[f"mediatek/mt7932/policy/{name}.bin"], country.encode()), 0)
        self.assertEqual(files, policy.derive(dict(reversed(list(tables.items()))), pins=None, input_pins=None))

    def test_input_hashes_cover_country_specific_changes(self):
        tables = synthetic_tables()
        pins = {name: hashlib.sha256(data).hexdigest() for name, data in tables.items()}
        policy.derive(tables, pins=None, input_pins=pins)
        tables[tool.SAR_NAME] = tables[tool.SAR_NAME].replace(b"subband1,1", b"subband1,2")
        with self.assertRaisesRegex(policy.SchemaError, "not the pinned input"):
            policy.derive(tables, pins=None, input_pins=pins)

    def test_join_status(self):
        power = sar = common = {"US": {}, "XZ": {}}
        rows = {"US", "XZ"}
        self.assertEqual(policy.join_status("US", power, sar, common, rows), "ready")
        self.assertEqual(policy.join_status("00", power, sar, common, rows), "ready")
        self.assertEqual(policy.join_status("KP", power, sar, common, rows), "sar-only")
        self.assertEqual(policy.join_status("MH", power, sar, common, rows), "template")
        self.assertEqual(policy.join_status("X2", power, sar, common, rows), "not-a-linux-country")
        self.assertEqual(policy.join_status("us", power, sar, common, rows), "invalid")
        self.assertEqual(policy.join_status("GB", power, sar, common, {"GB"}), "missing power,sar,common-path")

    def test_inventory_detects_changes_outside_individual_pins(self):
        files = {"baseline.bin": b"baseline", "DE.bin": b"power values"}
        pins = {"baseline.bin": hashlib.sha256(files["baseline.bin"]).hexdigest()}
        expected = policy.inventory_digest(files)
        policy.verify_outputs(files, pins, expected)
        self.assertEqual(expected, policy.inventory_digest(dict(reversed(list(files.items())))))
        files["DE.bin"] = b"different power values"
        with self.assertRaisesRegex(policy.PolicyError, "complete policy inventory"):
            policy.verify_outputs(files, pins, expected)

    def test_every_individually_pinned_country_must_exist(self):
        files = policy.derive(synthetic_tables(), pins=None, input_pins=None)
        files["mediatek/mt7932/policy/CA.bin"] = b"synthetic country policy"
        pins = {name: hashlib.sha256(blob).hexdigest() for name, blob in files.items()}
        expected = policy.inventory_digest(files)
        del files["mediatek/mt7932/policy/CA.bin"]
        with self.assertRaisesRegex(policy.PolicyError, "missing pinned policy CA.bin"):
            policy.verify_outputs(files, pins, expected)

    def test_derivation_checks_a_pin_even_when_its_country_is_missing(self):
        pins = {"mediatek/mt7932/policy/CA.bin": "0" * 64}
        with self.assertRaisesRegex(policy.PolicyError, "missing pinned policy CA.bin"):
            policy.derive(synthetic_tables(), pins=pins, input_pins=None, inventory_pin=None)

    def test_read_tables_reads_the_five_names(self):
        with tempfile.TemporaryDirectory() as directory:
            write_tables(directory)
            (Path(directory) / "IZUBA_wifi.cfg").write_bytes(b"ignored")
            self.assertEqual(policy.read_tables(directory), synthetic_tables())


@unittest.skipUnless(os.environ.get("OMARCHY_NEO_IZUBA"), "set OMARCHY_NEO_IZUBA to the 25G83 tables")
class PinnedTablesTests(unittest.TestCase):
    """Opt-in: the real 25G83 tables derive exactly the pinned files."""

    def test_the_25g83_tables_derive_the_pinned_policy(self):
        files = policy.derive(policy.read_tables(os.environ["OMARCHY_NEO_IZUBA"]))
        self.assertEqual(len(files), 244)
        self.assertEqual(policy.inventory_digest(files), policy.PINNED_INVENTORY)
        index = "\n".join(sorted(files)) + "\n"
        self.assertEqual(hashlib.sha256(index.encode()).hexdigest(),
                         "32b3392b3b5afeb7bd7002771d6ed0b863ea269618ae67717a8f5a8d43ddb1d0")
        hashes = {path: hashlib.sha256(blob).hexdigest() for path, blob in files.items()}
        self.assertTrue(policy.PINNED_OUTPUT.keys() <= hashes.keys())
        for path, expected in policy.PINNED_OUTPUT.items():
            self.assertEqual(hashes[path], expected)
        for path, blob in files.items():
            country = "XZ" if path.endswith("world-XZ.bin") else Path(path).stem
            self.assertEqual(policy.policy_parse(blob, country.encode("ascii")), 0)


class TextGrammarTests(unittest.TestCase):
    def test_text_rules(self):
        empty = tool.parse_power(power_text(row="empty"))
        self.assertEqual(empty["XZ"]["vht160"], [])
        self.assertEqual(empty["XZ"]["ru996X2"], [])
        yrows = tool.parse_power(power_text(row="y"))
        self.assertEqual(yrows["XZ"]["ofdm"], ["ch038"])
        with self.assertRaises(tool.SchemaError) as caught:
            tool.parse_power(power_text(row="y").replace(
                "ch038,Y,Y,Y,Y,Y,Y,Y,Y", "ch038,Y0,Y,Y,Y,Y,Y,Y,Y", 1))
        self.assertEqual(caught.exception.code, "grammar")
        with self.assertRaises(tool.SchemaError) as caught:
            tool.parse_power(power_text(version="{Ver:other}", row="y") + "not,a,row\n")
        self.assertEqual(caught.exception.code, "version")
        with self.assertRaises(tool.SchemaError) as caught:
            tool.parse_sar(sar_text().replace("subband1,1,1,1,1", "subband1,Y,1,1,1", 1))
        self.assertEqual(caught.exception.code, "grammar")
        with self.assertRaises(tool.SchemaError) as caught:
            tool.parse_sar(sar_text(sections=("US", "US")))
        self.assertEqual(caught.exception.code, "grammar")
        self.assertIn("XZ", tool.parse_common(common_text()))
        with self.assertRaises(tool.SchemaError) as caught:
            tool.parse_common(common_text().replace(
                tool.PINNED_VERSION[tool.COMMON_NAME],
                tool.PINNED_VERSION[tool.COMMON_NAME] + "\r\n<Ver:01>",
                1))
        self.assertEqual(caught.exception.code, "version")
        self.assertEqual(tool.parse_db(db_text())["2gCC"][0], "XZ")
        with self.assertRaises(tool.SchemaError) as caught:
            tool.parse_db(db_text().replace(
                tool.PINNED_VERSION[tool.DB_NAME],
                tool.PINNED_VERSION[tool.DB_NAME] + "\r\n<Ver:01>",
                1))
        self.assertEqual(caught.exception.code, "version")
        rows = tool.parse_sdb(sdb_text(cell="x"))
        self.assertEqual(len(rows), 17)
        with self.assertRaises(tool.SchemaError) as caught:
            tool.parse_sdb(sdb_text(cell="Y"))
        self.assertEqual(caught.exception.code, "grammar")


def _filled_row(**overrides):
    row = {name: ["Y"] * policy.GROUP_WIDTH[name] for name in policy.GROUPS}
    row.update(overrides)
    return row


def _sar_rows():
    return [["0", "0", "0", "0"] for _ in range(20)]


def _sdb_rows():
    return [["0"] * 12 for _ in range(17)]


class PolicyMapTests(unittest.TestCase):
    def test_encodings_and_the_121_byte_subsample(self):
        self.assertEqual(policy.power_byte("Y"), 0x7F)
        self.assertEqual(policy.power_byte("X"), 0xC4)
        self.assertEqual(policy.power_byte("x"), 0xC4)
        self.assertEqual(policy.power_byte("-1"), 0xFF)
        self.assertEqual(policy.sar_byte("x"), 0x7F)
        self.assertEqual(policy.sar_byte("Y"), 0)
        self.assertEqual(policy.sdb_byte("X"), 0x7F)
        self.assertEqual(policy.sdb_byte("Y"), 0)
        row = _filled_row(
            cck=["3", "9", "9", "9"],
            ht20=["Y"] * 24,
        )
        row["ht20"][1] = "X"
        record = policy.expand_record(policy.value_image(row))
        self.assertEqual(record[0], 3)
        self.assertEqual(record[4], 0xC4)
        self.assertNotIn(9, record)

    def test_pad_and_bitmap_use_parsed_slots(self):
        quiet = _filled_row()
        self.assertEqual(policy.one_ss_pad([(1, policy.parsed_image(quiet))])[0], 0)
        marked = _filled_row()
        marked["ru106"][0] = "20"
        marked["ru106"][4] = "0"
        self.assertEqual(policy.one_ss_pad([(1, policy.parsed_image(marked))])[0], 0)
        marked = _filled_row()
        marked["vht20"][0] = "20"
        marked["vht20"][10] = "0"
        pad = policy.one_ss_pad([(1, policy.parsed_image(marked))])
        self.assertEqual(pad[0], 1 << 1)
        wide = _filled_row()
        wide["vht20"][0] = "20"
        wide["vht20"][10] = "0"
        self.assertEqual(policy.one_ss_pad([(38, policy.parsed_image(wide))])[4], 0)
        wide["vht40"][0] = "20"
        wide["vht40"][10] = "0"
        self.assertEqual(policy.one_ss_pad([(38, policy.parsed_image(wide))])[4], 1 << 6)
        unsupported = _filled_row()
        unsupported["ru26"][0] = "X"
        body, incomplete = policy.unsupported_bitmap([(1, policy.parsed_image(unsupported))])
        self.assertFalse(incomplete)
        self.assertEqual(int.from_bytes(body[4:8], "little"), 0x30000000)

    def test_fixed_tables_and_xz_parser(self):
        sar = _sar_rows()
        sar[0] = ["1", "2", "x", "Y"]
        sdb = _sdb_rows()
        sdb[3] = ["X", "8"] + ["0"] * 10
        tables, incomplete = policy.build_tables(
            b"XZ", {1: _filled_row()}, {}, sar, {1: "5", 13: "9"}, sdb)
        self.assertFalse(incomplete)
        self.assertEqual(len(tables[0]), 532)
        self.assertEqual(tables[0][7], 5)
        self.assertEqual(tables[1][4], 8)
        self.assertEqual(tables[2][4], 6)
        self.assertEqual(tables[2][6], 1)
        self.assertEqual(tables[1][44], 1)
        self.assertEqual(tables[2][44], 9)
        self.assertEqual(tables[1][12:44], tables[9][12:44])
        self.assertEqual(tables[10][0x2C:0x2C + 5], bytes((0, 1, 2, 0x7F, 0)))
        self.assertEqual(tables[10][7], 1)
        self.assertEqual(tables[10][8:10], b"XZ")
        self.assertEqual(tables[11][4:8], bytes((13, 1, 1, 4)))
        self.assertEqual(tables[11][0x2C], 5)
        self.assertEqual(tables[11][0x38], 9)
        self.assertEqual(tables[11][0x2D], 0)
        self.assertEqual(tables[12][7], 2)
        self.assertEqual(len(tables[12]), 265)
        slot = 0x2C + 3 * 13
        self.assertEqual(tables[12][slot:slot + 3], bytes((3, 0x7F, 8)))
        blob = policy.build_country_policy(
            b"XZ", {1: _filled_row()}, {}, sar, {1: "5", 13: "9"}, sdb, 0)
        self.assertEqual(len(blob), 10241)
        self.assertEqual(policy.policy_parse(blob, b"XZ"), 0)
        self.assertEqual(blob[24:24 + 17], b"SarEnableConfig 1")
        self.assertEqual(blob[16 + 328 + 8:16 + 328 + 8 + 18], b"coex tasar_set 0 0")
        enabled = policy.country_modes(b"XZ", 1)
        self.assertEqual(enabled[0][8:25], b"SarEnableConfig 0")
        self.assertEqual(len(enabled), 2)
        self.assertEqual(policy.country_modes(b"CA", 1)[2][8:27], b"coex tasar_set 13 1")
        self.assertEqual(policy.country_modes(b"US", 1)[2][8:27], b"coex tasar_set 13 0")
        self.assertEqual(policy.country_modes(b"DE", 1)[2][8:27], b"coex tasar_set 13 2")
        self.assertEqual(policy.country_modes(b"KR", 1)[2][8:27], b"coex tasar_set 13 3")
        self.assertEqual(len(policy.country_modes(b"US", 0)), 2)
        self.assertEqual(policy.dsa_from_rows([["0"] * 75 + ["0"], ["0"] * 75 + ["1"]]), 1)
        self.assertEqual(policy.strtoul_base8("10"), 8)
        self.assertEqual(policy.strtoul_base8("3/1/2"), 2)
        overwritten = policy.dsa_from_sections({
            "2gCC": [("US", ["0"] * 75 + ["1"])],
            "6gCC": [("US", ["0"] * 75 + ["3/1/2"])],
        }, "US")
        self.assertEqual(overwritten, 2)
        self.assertEqual(len(policy.country_modes(b"US", overwritten)), 2)
        wide = policy.build_country_policy(
            b"XZ", {}, {38: _filled_row()}, sar, {}, sdb, 0)
        self.assertEqual(len(wide), 10241)
        self.assertEqual(policy.policy_parse(wide, b"XZ"), 0)

    def test_table0_pass2_and_bit31(self):
        def word(rows, channel):
            body, incomplete = policy.unsupported_bitmap(rows)
            self.assertFalse(incomplete)
            index = channel if channel <= 14 else (channel >> 1) - 1
            return int.from_bytes(body[index * 4:index * 4 + 4], "little")

        def image(*marks):
            row = _filled_row()
            for name, column, token in marks:
                row[name][column] = token
            return policy.parsed_image(row)

        def flat(token):
            row = {name: [token] * policy.GROUP_WIDTH[name] for name in policy.GROUPS}
            return policy.parsed_image(row)

        # Bandwidth 0. OFDM X aborts pass 2 before the RU242 X is scanned.
        ofdm_x = image(("ofdm", 0, "X"), ("ru242", 0, "X"))
        self.assertEqual(word([(1, ofdm_x)], 1), 1)
        # RU242's second group is group 1 of rate 11, so bit 31 is set.
        group1 = _filled_row()
        group1["ofdm"] = ["20"] * 8
        group1["vht20"] = ["20"] * 30
        group1["ru242"] = ["20"] * 12 + ["X"] + ["Y"] * 23
        self.assertEqual(word([(1, policy.parsed_image(group1))], 1), 0x80002000)
        # 40 MHz, no subchannel rows: pass 2 adds nothing and the file is finished.
        self.assertEqual(word([(38, flat("Y"))], 38), 0)
        # Subchannel 36's HT20 X sets bit 0. Y in the next group stops subchannel 40.
        early = {name: ["20"] * policy.GROUP_WIDTH[name] for name in policy.GROUPS}
        early["ht20"][0] = "X"
        early["vht40"][10] = "Y"
        later = {name: ["20"] * policy.GROUP_WIDTH[name] for name in policy.GROUPS}
        later["vht40"][10] = "X"
        self.assertEqual(word([
            (38, flat("20")),
            (36, policy.parsed_image(early)),
            (40, policy.parsed_image(later)),
        ], 38), 1)
        # Both subchannels are scanned. Rate 1's group 1 sets bit 31.
        both = {name: ["20"] * policy.GROUP_WIDTH[name] for name in policy.GROUPS}
        both["ht20"][0] = "X"
        self.assertEqual(word([
            (38, flat("20")),
            (36, policy.parsed_image(both)),
            (40, policy.parsed_image(later)),
        ], 38), 0x80000003)
        # 0x7f in the current HT20 is replaced from VHT40 before the scan.
        current = {name: ["Y"] * policy.GROUP_WIDTH[name] for name in policy.GROUPS}
        current["vht40"] = ["X"] + ["20"] * 29
        self.assertEqual(
            word([(38, policy.parsed_image(current)), (36, flat("20"))], 38),
            1 << 3,
        )
        # 80 MHz. A missing 40 MHz center scans nothing. The present center's
        # HT20 column 16 is what lands on rate 2, and the subchannel's RU996
        # is what lands on rate 11.
        current80 = flat("20")
        sub = {name: ["20"] * policy.GROUP_WIDTH[name] for name in policy.GROUPS}
        sub["ru996"][0] = "X"
        center = flat("20")
        self.assertEqual(
            word([(42, current80), (36, policy.parsed_image(sub))], 42),
            0,
        )
        self.assertEqual(
            word([
                (42, current80),
                (36, policy.parsed_image(sub)),
                (38, center),
            ], 42),
            1 << 12,
        )
        edged = {name: ["20"] * policy.GROUP_WIDTH[name] for name in policy.GROUPS}
        edged["ht20"][16] = "X"
        self.assertEqual(
            word([
                (42, current80),
                (36, flat("20")),
                (38, policy.parsed_image(edged)),
            ], 42),
            1 << 3,
        )
        # 160 MHz. Rate 3 is filled from the 80 MHz center, not the 40 MHz one.
        eighty = {name: ["20"] * policy.GROUP_WIDTH[name] for name in policy.GROUPS}
        eighty["ht20"][16] = "X"
        self.assertEqual(
            word([
                (50, flat("20")),
                (36, flat("20")),
                (38, flat("20")),
                (42, policy.parsed_image(eighty)),
            ], 50),
            1 << 6,
        )
        # Row index 67 is not a current row. Its OFDM X does not set bit 0.
        quiet = flat("Y")
        noisy = image(("ofdm", 0, "X"))
        self.assertEqual(word([(1, quiet)] * 67 + [(1, noisy)], 1), 0)

    def test_the_builder_emits_six_table_two_records_for_every_country(self):
        tables, _ = policy.build_tables(b"US", {}, {}, _sar_rows(), {}, _sdb_rows())
        self.assertEqual(tables[2][4], 6)
        blob = policy.build_country_policy(b"US", {}, {}, _sar_rows(), {}, _sdb_rows(), 0)
        self.assertEqual(len(blob), 10241)
        table2 = TABLE2 + 2 * 328
        self.assertEqual(blob[table2 + 4], 6)
        self.assertEqual(blob[table2 + 44], 9)
        self.assertEqual(blob[table2 + 44 + 5 * 122], 14)
        wide = policy.build_country_policy(b"US", {}, {}, _sar_rows(), {}, _sdb_rows(), 1)
        self.assertEqual(len(wide), 10569)
        self.assertIn(b"coex tasar_set 13 0", wide)

    def test_bonded_rows_follow_the_center_table(self):
        self.assertEqual(policy.center_bandwidth(1), 0xFF)
        self.assertEqual(policy.center_bandwidth(36), 0xFF)
        self.assertEqual(policy.center_bandwidth(38), 1)
        self.assertEqual(policy.center_bandwidth(42), 2)
        self.assertEqual(policy.center_bandwidth(46), 1)
        self.assertEqual(policy.center_bandwidth(50), 3)
        spec_2g, _spec_5g = policy.load_power_rows([("cck", "ch001", ["3", "1", "1", "1"])])
        self.assertEqual(spec_2g[1]["cck"][0], "3")
        self.assertEqual(spec_2g[1]["ofdm"], ["X"] * 8)
        ofdm = ["4"] * 8
        mirrored = policy.load_power_rows([("ofdm", "ch038", ofdm)])[1]
        self.assertEqual(mirrored[38]["ht20"][:8], ofdm)
        self.assertEqual(mirrored[38]["ht20"][8], "X")
        later = policy.load_power_rows([
            ("ofdm", "ch038", ofdm),
            ("ht20", "ch038", ["9"] * 24),
        ])[1]
        self.assertEqual(later[38]["ht20"], ["9"] * 24)
        self.assertEqual(later[38]["ofdm"], ofdm)
        ranged = policy.load_power_rows([("ofdm", "ch036-040", ["5"] * 8)])[1]
        self.assertEqual(ranged[36]["ofdm"], ["5"] * 8)
        self.assertEqual(ranged[40]["ofdm"], ["X"] * 8)
        end_row = policy.load_power_rows([("ofdm", "ch038-042", ["6"] * 8)])[1]
        self.assertEqual(end_row[42]["ht20"][:8], ["6"] * 8)
        self.assertEqual(end_row[42]["ht20"][8], "X")
        self.assertEqual(end_row[38]["ofdm"], ["X"] * 8)
        same = policy.load_power_rows([("ofdm", "ch038-046", ["7"] * 8)])[1]
        self.assertEqual(same[46]["ht20"][8:16], ["7"] * 8)
        self.assertEqual(same[38]["ofdm"], ["X"] * 8)
        cdd = policy.load_power_rows([("ofdm", "ch038-cdd", ["8"] * 8)])[1]
        self.assertEqual(cdd[38]["ht20"][8:16], ["8"] * 8)
        self.assertEqual(cdd[38]["ofdm"], ["X"] * 8)
        vht = ["2"] * 30
        bonded = policy.load_power_rows([("vht20", "ch038-042", vht)])[1]
        self.assertEqual(bonded[42]["vht40"], vht)
        self.assertEqual(bonded[38]["vht20"], ["X"] * 30)
        with self.assertRaises(ValueError) as caught:
            policy.load_power_rows([("nope", "ch001", ["1"])])
        self.assertEqual(str(caught.exception), "label")
        with self.assertRaises(ValueError) as caught:
            policy.load_power_rows([("cck", "ch001", ["1"] * 5)])
        self.assertEqual(str(caught.exception), "range")


if __name__ == "__main__":
    unittest.main()
