# SPDX-License-Identifier: MIT
# Ported from the MT7932 policy extractor by Chris Kearney, used under the MIT license.
# policy_parse follows the kernel driver's mt7932_policy_parse by Ace and Ryan Murray,
# used under the MIT license with their agreement (2026-10-20).
# See Engine/overlay/ORIGINS-mt7932-policy.md.
"""Derive the MacBook Neo's MT7932 Wi-Fi country policy (J7RP) from its tables.

mt7932-fullmac loads mediatek/mt7932/policy/world-XZ.bin, and the file of
the regulatory domain in use, such as policy/US.bin. Each is the chip's
command stream for that country, built here from five text tables that
macOS ships for this chip (IZUBA_TxPwrLimit_MT79x1.dat, _SAR.dat,
_2gCommonPath.dat, _SDB.dat and IZUBA_db.dat). Nothing here reads a driver
or writes a file: table bytes go in, policy bytes come out. No power-table
or firmware data is stored in this module; every policy is derived on the
Mac being installed.

load_power_rows turns the power table's rows into one row per channel, and
build_country_policy assembles one country from those rows and the other
tables. The 0xca records are chip configuration strings for a country
change. Table 0 is the unsupported-rate bitmap, both passes included.

Inputs and pins. The engine's inputs are the pinned 25G83 tables that
omarchy_apple_aea's J700 range recipe fetches by raw_sha256, not the live
macOS's. Input and output checks guard the derivation:

- PINNED_VERSION holds each table's {Ver:...} line as 25G83 ships it, and
  the format line where there is one. A table with another version line is
  refused before any row is read (a later macOS's IZUBA_db.dat, for one, has
  a newer channel matrix).
- PINNED_INPUT verifies every source table before parsing, including fields
  used only by a country outside the original world/US pair.
- PINNED_OUTPUT checks the qualified world, US and Canada results. Every
  complete country is generated from the same authenticated tables and
  checked with policy_parse before publication. PINNED_INVENTORY checks the
  path and blob digest of every generated policy, including untested countries.

build_country_policy gives table 2 six records (2.4 GHz channels 9 to 14)
for every country, as macOS does. The stock mt7932_policy_parse accepts six only for XZ and requires
five for every other country, so non-XZ files drop the channel-14 record
(five_record_table2). Channel 14 is not enabled on Linux, so nothing is lost
today. policy_parse follows the stock parser and gates each file.
"""

import hashlib
import os

EINVAL = 22
POLICY_MAX = 16384

POWER_NAME = "IZUBA_TxPwrLimit_MT79x1.dat"
SAR_NAME = "IZUBA_TxPwrLimit_SAR.dat"
COMMON_NAME = "IZUBA_TxPwrLimit_2gCommonPath.dat"
DB_NAME = "IZUBA_db.dat"
SDB_NAME = "IZUBA_TxPwrLimit_SDB.dat"
TABLES = (POWER_NAME, SAR_NAME, COMMON_NAME, DB_NAME, SDB_NAME)

PINNED_INPUT = {
    POWER_NAME: "03ee51df5231d131c1976f1d2ea7f81a1396988e63a132a318a9893e8ef5ec38",
    SAR_NAME: "d8dc9dfff2ad2d1ddf301d892e60c93f476603992f71de9e742194a395ca850d",
    COMMON_NAME: "43185e334cb074b9c89b71aa6440d5d22cc45e9d8dc1fdc66c8a4933f2376b6c",
    DB_NAME: "a61e0b70cbc9a381fc41ff9afa8f036cbd032cadad66209134d4a5adfa18c8b2",
    SDB_NAME: "042906f4b23f05d82f211549b22f195a57d3437b137009fda0a0e40935abbfd0",
}

# The version lines of the 26.6.2 (25G83) tables the J700 range recipe pins.
PINNED_VERSION = {
    POWER_NAME: "{Ver:Izuba_WLAN_Final_dedicated_v8 Date:2025-12-18 06:17:25}",
    SAR_NAME: "{Ver:J700_Izuba_WLAN_EVT_SAR_Cap_Input_v3.00 Date:2025-10-30 03:06:21}",
    COMMON_NAME: "{Ver:Izuba_WLAN_Final_PT_common_path_v1 Date:2025-10-30 02:30:10}",
    DB_NAME: "{Ver:2026 Spring_WiFi Channel Matrix_2.4GHz_5GHz_6GHz_Snrs_V2.0 Date:2025-12-11 11:03:09}",
    SDB_NAME: "{Ver:Izuba_SDB_WLAN_P2_PT_v1 Date:2025-08-09 05:19:31}",
}
PINNED_FORMAT = {
    POWER_NAME: "<Ver:03>",
    SAR_NAME: "<Ver:01>",
    COMMON_NAME: None,
    DB_NAME: None,
    SDB_NAME: None,
}

# Required baseline countries. Complete country rows are enumerated below.
POLICIES = (
    ("mediatek/mt7932/policy/world-XZ.bin", "XZ"),
    ("mediatek/mt7932/policy/US.bin", "US"),
)
# Qualified outputs from the pinned tables; each brought Wi-Fi up on Neo.
PINNED_OUTPUT = {
    "mediatek/mt7932/policy/world-XZ.bin": "cbc887013ca6cc31ea1a326cba485a2c89c70ad6d3f85bbf4bbda65f60ebb97b",
    "mediatek/mt7932/policy/US.bin": "9e519590d4a66972cfd7909132f3567c7b47a5252f89d345c1e7083a7d5bb9fe",
    "mediatek/mt7932/policy/CA.bin": "cf6f05261985ae48efa4c2802d645c26e6fb13d7eed3a5a64888ef372497f0da",
}

# SHA-256 of sorted ASCII "path<TAB>blob-sha256<LF>" records for all 244 outputs.
PINNED_INVENTORY = "37419c03c0ba222d60eb78fdbdf9fed9ef1d005ef76a8bae0255d4a978defd5d"

TEMPLATE_SECTIONS = frozenset({"MH"})
NOT_LINUX_COUNTRY = frozenset({"X0", "X2", "X3"})
SAR_ONLY = frozenset({"A1", "A3", "A6", "AQ", "CU", "IR", "KP"})


class PolicyError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class SchemaError(PolicyError):
    pass


CHANS_2G = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)
CHANS_5G = (
    36, 38, 40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 62, 64,
    100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120, 122, 124,
    126, 128, 132, 134, 136, 138, 140, 142, 144, 149, 151, 153, 155,
    157, 159, 161, 163, 165, 167, 169, 171, 173, 175, 177, 181,
)
BATCH = 8

GROUPS = (
    "cck", "ofdm", "ht20", "ht40", "vht20", "vht40", "vht80", "vht160",
    "ru26", "ru52", "ru106", "ru242", "ru484", "ru996", "ru996X2",
)
GROUP_WIDTH = {
    "cck": 4, "ofdm": 8, "ht20": 24, "ht40": 24,
    "vht20": 30, "vht40": 30, "vht80": 30, "vht160": 30,
}
for _name in GROUPS[8:]:
    GROUP_WIDTH[_name] = 36
_GROUP_INDEX = {name: index for index, name in enumerate(GROUPS)}

# How each of the 15 rate groups of a 432-byte value image becomes payload
# bytes in a version-3 record: (source offset, kind, stride). See expand_record.
DESCRIPTORS = (
    (0x00, 0, 4), (0x04, 1, 8), (0x0D, 1, 8), (0x25, 1, 8),
    (0x3C, 2, 10), (0x5A, 2, 10), (0x78, 2, 10), (0x96, 2, 10),
    (0xB4, 2, 12), (0xD8, 2, 12), (0xFC, 2, 12), (0x120, 2, 12),
    (0x144, 2, 12), (0x168, 2, 12), (0x18C, 2, 12),
)

SAR_COLUMNS = ("siso_wf0", "siso_wf1", "mimo_wf0", "mimo_wf1")
SDB_COLUMNS = (
    "cck_siso_wf0", "cck_siso_wf1", "ofdm_siso_wf0", "ofdm_siso_wf1",
    "ofdma_siso_wf0", "ofdma_siso_wf1", "cck_mimo_wf0", "cck_mimo_wf1",
    "ofdm_mimo_wf0", "ofdm_mimo_wf1", "ofdma_mimo_wf0", "ofdma_mimo_wf1",
)

SAR_TEXT_COLUMNS = {"tab": list(SAR_COLUMNS)}


def _rate_columns(prefix_count, rates):
    columns = []
    for prefix in ("s", "c", "m"):
        columns.extend(f"{prefix}_m{index}" for index in range(rates))
    if len(columns) != prefix_count:
        raise RuntimeError("column layout does not match the requirements")
    return columns


POWER_COLUMNS = {
    "cck": ["c1", "c2", "c5", "c11"],
    "ofdm": ["o6", "o9", "o12", "o18", "o24", "o36", "o48", "o54"],
    "ht20": _rate_columns(24, 8),
    "ht40": _rate_columns(24, 8),
    "vht20": _rate_columns(30, 10),
    "vht40": _rate_columns(30, 10),
    "vht80": _rate_columns(30, 10),
    "vht160": _rate_columns(30, 10),
    "ru26": _rate_columns(36, 12),
    "ru52": _rate_columns(36, 12),
    "ru106": _rate_columns(36, 12),
    "ru242": _rate_columns(36, 12),
    "ru484": _rate_columns(36, 12),
    "ru996": _rate_columns(36, 12),
    "ru996X2": _rate_columns(36, 12),
}


def power_byte(token):
    """One power-table cell as a byte. Y is 0x7f, X and x are -60, digits are one byte."""
    if token == "Y":
        return 0x7F
    if token in ("X", "x"):
        return 0xC4
    sign = 1
    body = token
    if body.startswith("-"):
        sign = -1
        body = body[1:]
    if not body.isdigit():
        return 0xC4
    return (sign * int(body)) & 0xFF


def sar_byte(token):
    if token == "x":
        return 0x7F
    sign = 1
    body = token
    if body.startswith("-"):
        sign = -1
        body = body[1:]
    if not body.isdigit():
        return 0
    return (sign * int(body)) & 0xFF


def sdb_byte(token):
    if token in ("X", "x"):
        return 0x7F
    sign = 1
    body = token
    if body.startswith("-"):
        sign = -1
        body = body[1:]
    if not body.isdigit():
        return 0
    return (sign * int(body)) & 0xFF


def channel_number(label):
    if len(label) < 5 or label[:2] != "ch" or not label[2:5].isdigit():
        raise ValueError(label)
    return int(label[2:5])


def value_image(row):
    """432 dense command bytes: each group's cells, back to back, at its own width."""
    image = bytearray([0x7F]) * 432
    offset = 0
    for name in GROUPS:
        width = GROUP_WIDTH[name]
        cells = row.get(name)
        if cells is not None:
            if len(cells) != width:
                raise ValueError(name)
            image[offset:offset + width] = bytes(power_byte(cell) for cell in cells)
        offset += width
    if offset != 432:
        raise RuntimeError("value image")
    return bytes(image)


def parsed_image(row):
    """Fifteen 36-byte slots, the parsed row after the channel byte.

    Short groups occupy the front of their slot. The bitmap and the 1SS pad
    index this layout, not the dense command image.
    """
    image = bytearray([0x7F]) * (15 * 36)
    for index, name in enumerate(GROUPS):
        cells = row.get(name)
        if cells is None:
            continue
        width = GROUP_WIDTH[name]
        if len(cells) != width:
            raise ValueError(name)
        base = index * 36
        for column, token in enumerate(cells):
            image[base + column] = power_byte(token)
    return bytes(image)


def expand_record(values):
    """121 payload bytes from the version-3 descriptor table. Channel byte is separate."""
    if len(values) != 432:
        raise ValueError("values")
    out = bytearray()
    for src, kind, stride in DESCRIPTORS:
        if kind == 0:
            out.append(values[src])
        elif kind == 1 and src == 4:
            out.extend(values[src + delta] for delta in (0, 4, 6))
        elif kind == 1 and src in (0x0D, 0x25):
            for step in range(3):
                base = src + step * stride
                out.extend(values[base + delta] for delta in (0, 4, 6))
        elif kind == 2:
            for step in range(3):
                base = src + step * stride
                out.extend(values[base + delta] for delta in (0, 3, 5))
    if len(out) != 121:
        raise RuntimeError(f"descriptor produced {len(out)}")
    return out


def channel_width_class(band, channel):
    """Width class 0 to 3 from a channel's offset in its block, or 0xff.

    Channels below 15, and channels a multiple of 4 past their block's
    start, are 0. For band 3 the block starts at 1 or 2, so its result is
    not a bandwidth.
    """
    if channel == 0:
        return 0xFF
    if band in (1, 2):
        if channel < 15:
            return 0
        base = 36 if channel < 149 else 149
    elif band == 3:
        base = 2 if channel == 2 else 1
    else:
        return 0xFF
    if base > channel:
        return 0xFF
    delta = (channel - base) & 0xFF
    if (delta & 3) == 0:
        return 0
    result = 0xFF
    if (delta & 0x1F) == 0x0E:
        result = 3
    if (delta & 0x0F) == 6:
        result = 2
    if (delta & 7) == 2:
        result = 1
    return result


def _signed(byte):
    return byte - 256 if byte >= 128 else byte


# Step k compares parsed group _PAD_GROUP[k] column 0 with column _PAD_COLUMN[k].
_PAD_GROUP = (4, 5, 6, 7)
_PAD_COLUMN = (10, 10, 10, 10)


def one_ss_pad(rows):
    """32-byte channel mask of the 1SS pad. Each row's `values` is a parsed image.

    A channel's bit is set when, for every width up to its own, column 0 of
    that width's group is at least 10 above column 10.
    """
    pad = bytearray(32)
    for channel, values in rows:
        bandwidth = channel_width_class(2, channel)
        if bandwidth > 3 or channel > 181:
            continue
        if all(
            _signed(values[group * 36]) - _signed(values[group * 36 + column]) >= 10
            for group, column in zip(_PAD_GROUP[:bandwidth + 1], _PAD_COLUMN)
        ):
            pad[channel >> 3] |= 1 << (channel & 7)
    return bytes(pad)


def _rate_mask(rate, bandwidth):
    if rate == 8:
        return 0x30000000
    if rate == bandwidth + 10 and rate > 10:
        return 0x50000000
    if 9 <= rate <= 14:
        return 0x10000000
    return None


# Pass 2 of the unsupported-rate bitmap: the rates it scans, each rate's
# group size, and each width's offset from a channel to its first subchannel.
_PASS2_RATES = (1, 2, 3, 4, 11, 12, 13, 14)
_PASS2_SIZES = (8, 8, 8, 8, 12, 12, 12, 12)
_SUB_DELTAS = (0, 2, 6, 14)


def _pass1(values, bandwidth):
    """Rates 8..bandwidth+11, groups of 12. 0x7f stops the rest of this pass."""
    word = 0
    stop = False
    for rate in range(8, bandwidth + 12):
        if stop:
            break
        for group in range(3):
            if stop:
                break
            marked = False
            for column in range(12):
                byte = values[rate * 36 + group * 12 + column]
                if byte == 0x7F:
                    stop = True
                    marked = False
                    break
                if byte == 0xC4:
                    marked = True
                    break
            if marked:
                mask = _rate_mask(rate, bandwidth)
                if mask is not None:
                    word |= mask
    return word


def _pass2_bit(rate, group):
    """The bitmap bit pass 2 sets for a rate and group. None means no bit."""
    if group > 2:
        return None
    if 1 <= rate <= 4:
        if group == 2:
            return None
        return 1 << (rate * 3 + group - 3)
    if 11 <= rate <= 14:
        return 1 << (rate * 3 + group - 21)
    return None


def center_channel(band, width, channel):
    """Center channel of a channel at a width class. Band 1 returns 0. Width 0 returns the channel."""
    if width == 0:
        return channel & 0xFF
    if band == 2:
        base = 36 if channel < 149 else 149
    elif band == 3:
        if channel == 2:
            return 2
        base = 1
    else:
        return 0
    if base > channel:
        return 0
    delta = channel - base
    if width == 1:
        adjusted = channel + 2 if (delta & 7) == 0 else channel - 2
    elif width == 2:
        adjusted = base + (delta & ~0xF) + 6
    elif width == 3:
        adjusted = base + (delta & ~0x1F) + 14
    else:
        return 0
    return adjusted & 0xFF


def _paint(image, rate, offset, chunk):
    start = rate * 36 + offset
    image[start:start + len(chunk)] = chunk


def _take(values, rate, offset, count):
    start = rate * 36 + offset
    return values[start:start + count]


def _subst(image, rate, offset, values, src_rate, src_offset):
    if image[rate * 36 + offset] == 0x7F:
        _paint(image, rate, offset, _take(values, src_rate, src_offset, 8))


def _fill_pass2(bandwidth, current, sub, center40, center80):
    """The 540-byte image pass 2 scans, spliced from the channel, subchannel and center rows."""
    image = bytearray(15 * 36)
    if bandwidth == 0:
        _paint(image, 1, 0, _take(current, 1, 0, 8))
        _paint(image, 1, 8, _take(current, 4, 10, 8))
        _paint(image, 11, 0, _take(current, 11, 0, 36))
        return image
    _paint(image, 1, 0, _take(sub, 2, 8 * (bandwidth - 1), 8))
    _paint(image, 1, 8, _take(sub, bandwidth + 4, 10, 8))
    if bandwidth == 1:
        _paint(image, 2, 0, _take(current, 2, 0, 16))
        _subst(image, 2, 0, current, 5, 0)
        _subst(image, 2, 8, current, 5, 10)
        _paint(image, 11, 0, _take(current, 11, 0, 36))
        _paint(image, 12, 0, _take(current, 12, 0, 36))
        return image
    _paint(image, 11, 0, _take(sub, bandwidth + 11, 0, 36))
    if bandwidth == 2:
        _paint(image, 2, 0, _take(center40, 2, 16, 8))
        _paint(image, 2, 8, _take(center40, 3, 0, 8))
    else:
        _paint(image, 2, 0, _take(center40, 3, 8, 8))
        _paint(image, 2, 8, _take(center40, 3, 16, 8))
        _paint(image, 12, 0, _take(center40, 14, 0, 36))
        _paint(image, 3, 0, _take(center80, 2, 16, 8))
        _paint(image, 3, 8, _take(center80, 3, 0, 8))
        _subst(image, 3, 0, center80, 7, 0)
        _subst(image, 3, 8, center80, 7, 10)
    _subst(image, 2, 0, center40, bandwidth + 4, 0)
    _subst(image, 2, 8, center40, bandwidth + 4, 10)
    _paint(image, bandwidth + 1, 0, _take(current, 2, 0, 16))
    _subst(image, bandwidth + 1, 0, current, bandwidth + 4, 0)
    _subst(image, bandwidth + 1, 8, current, bandwidth + 4, 10)
    _paint(image, bandwidth + 10, 0, _take(current, bandwidth + 10, 0, 36))
    _paint(image, bandwidth + 11, 0, _take(current, bandwidth + 11, 0, 36))
    return image


def _scan_pass2(image):
    """0xc4 ORs one bit and ends that group. 0x7f ends the rest of the scan."""
    word = 0
    for rate, size in zip(_PASS2_RATES, _PASS2_SIZES):
        for group in range(3):
            marked = False
            abort = False
            for column in range(size):
                byte = image[rate * 36 + group * size + column]
                if byte == 0x7F:
                    abort = True
                    break
                if byte == 0xC4:
                    marked = True
                    break
            if marked:
                bit = _pass2_bit(rate, group)
                if bit is not None:
                    word |= bit
            if abort:
                return word, True
    return word, False


def _bit31(word, bandwidth):
    """OR 0x80000000 when a selected rate's group-1 bit is already set."""
    selected = _PASS2_RATES[:bandwidth + 1] + _PASS2_RATES[4:4 + bandwidth + 1]
    for rate in selected:
        shift = rate * 3 - 2 if rate <= 4 else rate * 3 - 20
        if (word >> shift) & 1:
            return word | 0x80000000
    return word


def _find_row(parsed, channel):
    for found, values in parsed:
        if found == channel:
            return values
    return None


def unsupported_bitmap(parsed):
    """The 360-byte unsupported-rate bitmap, including pass 2 and bit 31.

    Only row indices 0..66 are current rows. A channel of 0 is skipped.
    Searches for a subchannel or a center walk the whole list, first match.
    The second return is always false: every row is translated.
    """
    words = [0] * 90
    for index, (channel, values) in enumerate(parsed):
        if index > 66 or channel == 0 or channel > 181:
            continue
        band = 1 if channel < 15 else 2
        bandwidth = channel_width_class(band, channel)
        if bandwidth > 3:
            continue
        limit = (14, 181, 233)[band - 1]
        if channel > limit:
            continue
        slot = channel if channel <= 14 else (channel >> 1) - 1
        word = words[slot] | _pass1(values, bandwidth)
        abort = False
        for sub_index in range(1 << bandwidth):
            if abort:
                break
            sub_channel = (channel - _SUB_DELTAS[bandwidth] + 4 * sub_index) & 0xFF
            sub = _find_row(parsed, sub_channel)
            if sub is None:
                continue
            center40 = center80 = None
            if bandwidth >= 2:
                center40 = _find_row(parsed, center_channel(band, 1, sub_channel))
                if center40 is None:
                    break
            if bandwidth == 3:
                center80 = _find_row(parsed, center_channel(band, 2, sub_channel))
                if center80 is None:
                    break
            image = _fill_pass2(bandwidth, values, sub, center40, center80)
            extra, abort = _scan_pass2(image)
            word |= extra
        words[slot] = _bit31(word, bandwidth)
    body = bytearray()
    for word in words:
        body += word.to_bytes(4, "little")
    if len(body) != 360:
        raise RuntimeError("bitmap")
    return bytes(body), False


def _chunk_counts(total):
    counts = []
    left = total
    while left:
        take = min(BATCH, left)
        counts.append(take)
        left -= take
    return counts


def per_rate_messages(alpha2, rows_2g, rows_5g, parsed_rows):
    """Tables 1–9. rows map a channel to a 432-byte dense image."""
    pad = one_ss_pad(parsed_rows)
    messages = []
    for band, channels, rows in ((1, CHANS_2G, rows_2g), (2, CHANS_5G, rows_5g)):
        counts = _chunk_counts(len(channels))
        cursor = 0
        for index, count in enumerate(counts):
            body = bytearray(44 + count * 122)
            body[0] = 3
            body[1] = 0x0A
            body[2:4] = (len(body)).to_bytes(2, "little")
            body[4] = count
            body[5] = band
            body[6] = 1 if index == len(counts) - 1 else 0
            body[8:10] = alpha2
            body[12:44] = pad
            for slot, channel in enumerate(channels[cursor:cursor + count]):
                # A slice of a bytearray is a copy. Write the record in place.
                at = 44 + slot * 122
                body[at] = channel
                values = rows.get(channel, bytes([0x7F]) * 432)
                body[at + 1:at + 122] = expand_record(values)
            messages.append(bytes(body))
            cursor += count
    if len(messages) != 9:
        raise RuntimeError("per-rate messages")
    return messages


def table0(parsed_rows):
    body, incomplete = unsupported_bitmap(parsed_rows)
    command = bytearray(532)
    command[1] = 2
    command[2:4] = (532).to_bytes(2, "little")
    command[4] = 4
    command[5] = 2
    command[7] = 5
    command[0x2C:0x2C + 360] = body
    return bytes(command), incomplete


def table10(alpha2, subbands):
    """subbands is 20 sequences of 4 tokens, index order."""
    if len(subbands) != 20:
        raise ValueError("sar")
    command = bytearray(144)
    command[1] = 2
    command[2:4] = (144).to_bytes(2, "little")
    command[4] = 20
    command[5] = 1
    command[6] = 1
    command[7] = 1
    command[8:10] = alpha2
    for index, cells in enumerate(subbands):
        if len(cells) != 4:
            raise ValueError("sar")
        slot = 0x2C + index * 5
        command[slot] = index
        for column, token in enumerate(cells):
            command[slot + 1 + column] = sar_byte(token)
    return bytes(command)


def table11(backoffs):
    """backoffs maps channel 1..13 to a token. Missing channels stay 0."""
    command = bytearray(58)
    command[1] = 2
    command[2:4] = (58).to_bytes(2, "little")
    command[4:8] = bytes((13, 1, 1, 4))
    for channel, token in backoffs.items():
        if not 1 <= channel <= 13:
            raise ValueError("common")
        command[0x2C + channel - 1] = int(token) & 0xFF
    return bytes(command)


def table12(subbands):
    if len(subbands) != 17:
        raise ValueError("sdb")
    command = bytearray(265)
    command[1] = 2
    command[2:4] = (265).to_bytes(2, "little")
    command[4] = 17
    command[5] = 1
    command[6] = 1
    command[7] = 2
    for index, cells in enumerate(subbands):
        if len(cells) != 12:
            raise ValueError("sdb")
        slot = 0x2C + index * 13
        command[slot] = index
        for column, token in enumerate(cells):
            command[slot + 1 + column] = sdb_byte(token)
    return bytes(command)


def placeholder_mode():
    """Parser-shaped and empty. Not a command sent to the chip."""
    record = bytearray(328)
    record[4:8] = (1).to_bytes(4, "little")
    return bytes(record)


def chip_config_record(payload):
    """328-byte 0xca body. data_size is the string length and does not include a NUL."""
    raw = payload.encode("ascii")
    if not 1 <= len(raw) <= 320:
        raise ValueError("mode")
    record = bytearray(328)
    record[4:6] = len(raw).to_bytes(2, "little")
    record[8:8 + len(raw)] = raw
    return bytes(record)


# Each country's window class, 1 to 7, for 89 countries. The class is not
# the command's argument. See window_arg.
_WINDOW_CLASS = {}
for _word, _names in {
    1: "US,CA,MX,SV,CO,PR,CR,EC,AR,VI,GT,BR,UM",
    2: "AT,BE,BG,CY,CZ,DK,EE,FR,FI,DE,GR,HU,IS,IT,IE,LV,LI,LT,LU,MT,NL,NO,PL,PT,RO,SK,SI,ES,SE,CH,GB,ZA,TR,AE,SA,HR,RS,XK",
    3: "JP",
    4: "KR",
    5: "AU,HK,NZ,SG,MY,VN,BN,TH,KH,LA,MM,CN",
    6: "TW,PK,NP,BD,CL,PA,VE,UY,LK,MV,AF,MN,BT,MO,PH,PE,DO,GU,FJ,NC,PG,WS,VU",
    7: "IN",
}.items():
    for _name in _names.split(","):
        _WINDOW_CLASS[_name.encode()] = _word


def window_arg(alpha2):
    """Argument of `coex tasar_set 13 N`, or None when that command is not sent."""
    word = _WINDOW_CLASS.get(alpha2)
    if word is None:
        return None
    if word == 1:
        return 1 if alpha2 == b"CA" else 0
    if word == 4:
        return 3
    if word == 2:
        return 2
    return 0


def country_modes(alpha2, dsa):
    """The 0xca chip configuration records for a country, from its `dsa` value.

    `dsa` is 1 only when the last matching db.dat row's comma field 75
    (after the country code) parses as 1. On a 2g row that field is `dsa`.
    A later 6g row overwrites it with field 75 of that row (`6g157`). The
    field is read as base 8 from the last slash-separated piece, so `3/1/2`
    is 2 and does not select the third record.
    """
    if len(alpha2) != 2:
        raise ValueError("alpha2")
    if dsa == 1:
        modes = [
            chip_config_record("SarEnableConfig 0"),
            chip_config_record("coex tasar_set 0 1"),
        ]
        arg = window_arg(alpha2)
        if arg is not None:
            modes.append(chip_config_record(f"coex tasar_set 13 {arg}"))
        return modes
    return [
        chip_config_record("SarEnableConfig 1"),
        chip_config_record("coex tasar_set 0 0"),
    ]


def strtoul_base8(token):
    """C strtoul of the last slash piece, base 8. The low 8 bits are kept."""
    piece = token.split("/")[-1]
    index = 0
    while index < len(piece) and piece[index] in " \t\n\r\v\f":
        index += 1
    negative = False
    if index < len(piece) and piece[index] in "+-":
        negative = piece[index] == "-"
        index += 1
    value = 0
    while index < len(piece) and piece[index] in "01234567":
        value = (value * 8 + int(piece[index])) & 0xFFFFFFFF
        index += 1
    if negative:
        value = (-value) & 0xFFFFFFFF
    return value & 0xFF


def dsa_from_rows(rows):
    """File order of matching db rows. Each row is the comma fields after the country code."""
    value = 0
    for fields in rows:
        if len(fields) > 75:
            value = strtoul_base8(fields[75])
    return value


def dsa_from_sections(groups, country):
    """Walk db groups in file order. A later 6g row overwrites field 75.

    2g field 75 is the column `dsa`. 6g field 75 is the column `6g157`.
    Each is read as base 8 from the last slash piece, so a token such as
    `3/1/2` gives 2. The comparison with 1 happens in country_modes.
    """
    matched = []
    for _name, rows in groups.items():
        for label, fields in rows:
            if len(fields) <= 75 or fields[75] == "":
                raise ValueError("label")
            if label == country:
                matched.append(fields)
    return dsa_from_rows(matched)


# Each 2.4 and 5 GHz primary channel with its 40, 80 and 160 MHz centers.
# center_bandwidth compares only the centers, never the primary channel.
_CENTER_ROWS = tuple((channel, 0, 0, 0) for channel in range(1, 15)) + (
    (36, 38, 42, 50), (40, 38, 42, 50), (44, 46, 42, 50), (48, 46, 42, 50),
    (52, 54, 58, 50), (56, 54, 58, 50), (60, 62, 58, 50), (64, 62, 58, 50),
    (100, 102, 106, 114), (104, 102, 106, 114), (108, 110, 106, 114), (112, 110, 106, 114),
    (116, 118, 122, 114), (120, 118, 122, 114), (124, 126, 122, 114), (128, 126, 122, 114),
    (132, 134, 138, 0), (136, 134, 138, 0), (140, 142, 138, 0), (144, 142, 138, 0),
    (149, 151, 155, 163), (153, 151, 155, 163), (157, 159, 155, 163), (161, 159, 155, 163),
    (165, 167, 171, 163), (169, 167, 171, 163), (173, 175, 171, 163), (177, 175, 171, 163),
    (181, 0, 0, 0),
)


def center_bandwidth(channel):
    """1, 2, or 3 when the channel is a center, else 0xff. Never 0."""
    for _primary, bw40, bw80, bw160 in _CENTER_ROWS:
        if channel == bw40:
            return 1
        if channel == bw80:
            return 2
        if channel == bw160:
            return 3
    return 0xFF


def _power_span(label):
    """chNNN, chNNN-cdd, chNNN-MMM, or chNNN-MMM-cdd."""
    if len(label) < 5 or label[:2] != "ch" or not label[2:5].isdigit():
        raise ValueError("label")
    start = int(label[2:5])
    if len(label) == 5:
        return start, start, False, "plain"
    if label[5] != "-":
        raise ValueError("label")
    if len(label) == 9 and label[6:9] == "cdd":
        return start, start, True, "bonded"
    if len(label) >= 9 and label[6:9].isdigit():
        end = int(label[6:9])
        if len(label) == 9:
            return start, end, False, "bonded"
        if label[9:] == "-cdd":
            return start, end, True, "bonded"
    raise ValueError("label")


def _put(row, group_index, offset, cells):
    name = GROUPS[group_index]
    width = GROUP_WIDTH[name]
    if offset < 0 or offset + len(cells) > width:
        raise ValueError("range")
    row[name][offset:offset + len(cells)] = list(cells)


def _bonded_destination(group_index, bw_start, bw_end, cdd):
    """Group index and column offset on the end channel. No text-group write."""
    if group_index in (4, 5, 6):
        return bw_start + 4, 0
    if group_index != 1:
        if group_index in (11, 12, 13):
            return bw_start + 11, 0
        return group_index, 0
    if bw_start == bw_end:
        return 2, 8
    if bw_start == 2 and bw_end == 1:
        return (3, 0) if cdd else (2, 16)
    if bw_start == 3 and bw_end == 1:
        return (3, 16) if cdd else (3, 8)
    if bw_start == 3 and bw_end == 2:
        return (3, 0) if cdd else (2, 16)
    return 2, (bw_start - 1) * 8


def load_power_rows(records):
    """Apply power rows in file order onto the 67 template channels.

    Each record is (group name, label, cells). Missing groups stay the
    template token X, which is the 0xc4 parsed-row fill. A channel that is
    not in the template is skipped. The last store to a slot wins.
    """
    template = {
        channel: {name: ["X"] * GROUP_WIDTH[name] for name in GROUPS}
        for channel in (*CHANS_2G, *CHANS_5G)
    }
    for group, label, cells in records:
        if group not in _GROUP_INDEX:
            raise ValueError("label")
        if not cells:
            continue
        start, end, cdd, kind = _power_span(label)
        group_index = _GROUP_INDEX[group]
        if kind == "plain":
            if start not in template:
                continue
            _put(template[start], group_index, 0, cells)
            if group_index == 1 and center_bandwidth(start) in (1, 2, 3):
                if len(cells) != 8:
                    raise ValueError("range")
                _put(template[start], 2, 0, cells)
            continue
        if center_bandwidth(start) == 0xFF:
            if start not in template:
                continue
            _put(template[start], group_index, 0, cells)
            continue
        if end not in template:
            continue
        dest, offset = _bonded_destination(
            group_index, center_bandwidth(start), center_bandwidth(end), cdd)
        _put(template[end], dest, offset, cells)
    return (
        {channel: template[channel] for channel in CHANS_2G},
        {channel: template[channel] for channel in CHANS_5G},
    )


def sar_matrix(records):
    """20 rows of 4 tokens. Missing subbands stay x. The last row wins."""
    rows = [["x"] * 4 for _ in range(20)]
    for label, cells in records:
        if len(cells) != 4:
            raise ValueError("label")
        rows[_subband_index(label, 20)] = list(cells)
    return rows


def sdb_matrix(records):
    """17 rows of 12 tokens. Missing subbands stay x. Y is rejected."""
    rows = [["x"] * 12 for _ in range(17)]
    for label, cells in records:
        if len(cells) != 12 or any(cell == "Y" for cell in cells):
            raise ValueError("label")
        rows[_subband_index(label, 17)] = list(cells)
    return rows


def _subband_index(label, count):
    if not label.startswith("subband") or not label[7:].isdigit():
        raise ValueError("label")
    index = int(label[7:])
    if not 1 <= index <= count:
        raise ValueError("label")
    return index - 1


def common_map(records):
    """ch001..ch013. A later row for the same channel wins."""
    found = {}
    for label, cells in records:
        if len(label) != 5 or label[:2] != "ch" or not label[2:5].isdigit() or len(cells) != 1:
            raise ValueError("label")
        channel = int(label[2:5])
        if not 1 <= channel <= 13:
            raise ValueError("label")
        found[channel] = cells[0]
    return found


def assemble_j7rp(alpha2, tables, modes):
    if len(alpha2) != 2 or not (2 <= len(modes) <= 3) or len(tables) != 13:
        raise ValueError("shape")
    blob = bytearray(b"J7RP")
    blob += (1).to_bytes(2, "little")
    blob += alpha2
    blob.append(len(modes))
    blob.append(13)
    blob += b"\x00\x00"
    blob += b"\x00\x00\x00\x00"
    for mode in modes:
        if len(mode) != 328:
            raise ValueError("mode")
        blob += mode
    for table in tables:
        blob += table
    blob[12:16] = len(blob).to_bytes(4, "little")
    return bytes(blob)


def _split_spec(spec):
    dense = {}
    parsed = []
    for channel, row in spec.items():
        dense[channel] = value_image(row)
        parsed.append((channel, parsed_image(row)))
    return dense, parsed


def build_tables(alpha2, spec_2g, spec_5g, sar, common, sdb):
    """spec maps a channel to {group: [tokens]}. SAR and SDB are token rows."""
    dense_2g, parsed_2g = _split_spec(spec_2g)
    dense_5g, parsed_5g = _split_spec(spec_5g)
    parsed = parsed_2g + parsed_5g
    bitmap, incomplete = table0(parsed)
    messages = per_rate_messages(alpha2, dense_2g, dense_5g, parsed)
    tables = [bitmap, *messages, table10(alpha2, sar), table11(common), table12(sdb)]
    return tables, incomplete


def build_country_policy(alpha2, rows_2g, rows_5g, sar, common, sdb, dsa):
    """Assemble one country. The return is bytes, not a file.

    Table 2 has six records for every country. The sixth record is channel
    14's power data. Channel-number and pad checks in the parser stay
    limited to XZ.
    """
    tables, _incomplete = build_tables(alpha2, rows_2g, rows_5g, sar, common, sdb)
    return assemble_j7rp(alpha2, tables, country_modes(alpha2, dsa))


def _content_lines(text):
    if "\x00" in text:
        raise SchemaError("grammar")
    lines = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            continue
        lines.append(raw.strip())
    if not lines:
        raise SchemaError("grammar")
    return lines


def _split_fields(line):
    return [field.strip() for field in line.split(",")]


def _integer_field(field):
    if field == "Y":
        return False
    if not field or field[0] == "+":
        return False
    body = field[1:] if field[0] == "-" else field
    return bool(body) and all(character in "0123456789" for character in body)


def _parse_sections(lines, pinned_columns, allow_y, filename, keep_cells=False):
    version = lines[0]
    cursor = 1
    format_line = None
    if cursor < len(lines) and lines[cursor].startswith("<Ver:"):
        format_line = lines[cursor]
        cursor += 1
    # Stop before any section row is interpreted when the pin does not match.
    _pin(filename, version, format_line)
    sections = {}
    current = None
    group = None
    columns = None
    while cursor < len(lines):
        line = lines[cursor]
        cursor += 1
        if line.startswith("[") and line.endswith("]"):
            if group is not None or "," in line[1:-1] or not line[1:-1]:
                raise SchemaError("grammar")
            section_name = line[1:-1]
            if section_name in sections:
                raise SchemaError("grammar")
            current = {}
            sections[section_name] = current
            continue
        if line.startswith("</") and line.endswith(">"):
            if group is None or line[2:-1] != group or current is None:
                raise SchemaError("grammar")
            group = None
            columns = None
            continue
        if line.startswith("<") and line.endswith(">") and not line.startswith("</"):
            if current is None or group is not None:
                raise SchemaError("grammar")
            fields = _split_fields(line[1:-1])
            if not fields or not fields[0] or any(not field for field in fields):
                raise SchemaError("grammar")
            group = fields[0]
            columns = fields[1:]
            if group in current:
                raise SchemaError("grammar")
            if pinned_columns is not None:
                expected = pinned_columns.get(group)
                if expected is None or columns != expected:
                    raise SchemaError("columns")
            elif len(columns) != 1:
                raise SchemaError("columns")
            current[group] = []
            continue
        if group is None or current is None:
            raise SchemaError("grammar")
        fields = _split_fields(line)
        if len(fields) != 1 + len(columns) or not fields[0] or any(field == "" for field in fields):
            raise SchemaError("grammar")
        for field in fields[1:]:
            if allow_y and field == "Y":
                continue
            if not _integer_field(field):
                raise SchemaError("grammar")
        if keep_cells:
            current[group].append((fields[0], fields[1:]))
        else:
            current[group].append(fields[0])
    if group is not None or not sections:
        raise SchemaError("grammar")
    if pinned_columns is not None:
        for groups in sections.values():
            if set(groups) != set(pinned_columns):
                raise SchemaError("columns")
    else:
        for groups in sections.values():
            if set(groups) != {"common_path_backoff"}:
                raise SchemaError("columns")
    return sections


def parse_power(text):
    return _parse_sections(_content_lines(text), POWER_COLUMNS, True, POWER_NAME)


def parse_power_cells(text):
    return _parse_sections(_content_lines(text), POWER_COLUMNS, True, POWER_NAME, True)


def parse_sar(text):
    return _parse_sections(_content_lines(text), SAR_TEXT_COLUMNS, False, SAR_NAME)


def parse_sar_cells(text):
    return _parse_sections(_content_lines(text), SAR_TEXT_COLUMNS, False, SAR_NAME, True)


def parse_common(text):
    return _parse_sections(_content_lines(text), None, False, COMMON_NAME)


def parse_common_cells(text):
    return _parse_sections(_content_lines(text), None, False, COMMON_NAME, True)


def _parse_db(text, keep_cells):
    lines = _content_lines(text)
    version = lines[0]
    if len(lines) > 1 and lines[1].startswith("<Ver:"):
        raise SchemaError("version")
    _pin(DB_NAME, version, None)
    groups = {}
    current = None
    columns = 0
    for line in lines[1:]:
        if line.startswith("[") and line.endswith("]"):
            raise SchemaError("grammar")
        if line.startswith("</") and line.endswith(">"):
            # Some db tables close both groups with </end>, not the group name.
            closer = line[2:-1]
            if current is None or closer not in (current, "end"):
                raise SchemaError("grammar")
            current = None
            continue
        if line.startswith("<") and line.endswith(">"):
            if current is not None:
                raise SchemaError("grammar")
            fields = _split_fields(line[1:-1])
            if not fields or fields[0] not in ("2gCC", "6gCC") or fields[0] in groups:
                raise SchemaError("grammar")
            if any(not field for field in fields):
                raise SchemaError("grammar")
            expected = 76 if fields[0] == "2gCC" else 116
            if len(fields) - 1 != expected:
                raise SchemaError("columns")
            current = fields[0]
            columns = expected
            groups[current] = []
            continue
        if current is None:
            raise SchemaError("grammar")
        fields = _split_fields(line)
        if len(fields) != columns + 2 or fields[-1] != "" or not fields[0]:
            raise SchemaError("grammar")
        if any(field == "" for field in fields[:-1]):
            raise SchemaError("grammar")
        if keep_cells:
            groups[current].append((fields[0], fields[1:-1]))
        else:
            groups[current].append(fields[0])
    if current is not None or set(groups) != {"2gCC", "6gCC"}:
        raise SchemaError("grammar")
    for labels in groups.values():
        names = [item[0] if keep_cells else item for item in labels]
        if len(names) != len(set(names)):
            raise SchemaError("grammar")
    return groups


def parse_db(text):
    return _parse_db(text, False)


def parse_db_cells(text):
    return _parse_db(text, True)


def parse_sdb(text):
    """Indexed 17 by 12 matrix. X and x are cells. Y is rejected."""
    lines = _content_lines(text)
    version = lines[0]
    if len(lines) > 1 and lines[1].startswith("<Ver:"):
        raise SchemaError("version")
    _pin(SDB_NAME, version, None)
    if len(lines) < 2 or not lines[1].startswith("<") or not lines[1].endswith(">") or lines[1].startswith("</"):
        raise SchemaError("grammar")
    header = _split_fields(lines[1][1:-1])
    if not header or header[0] != "sdb" or tuple(header[1:]) != SDB_COLUMNS:
        raise SchemaError("columns")
    rows = [["x"] * len(SDB_COLUMNS) for _ in range(17)]
    closed = False
    for line in lines[2:]:
        if closed or line.startswith("[") or (line.startswith("<") and not line.startswith("</")):
            raise SchemaError("grammar")
        if line.startswith("</") and line.endswith(">"):
            if line[2:-1] != "sdb":
                raise SchemaError("grammar")
            closed = True
            continue
        parts = _split_fields(line)
        if len(parts) != 1 + len(SDB_COLUMNS) or not parts[0]:
            raise SchemaError("grammar")
        label = parts[0]
        if not label.startswith("subband") or not label[7:].isdigit():
            raise SchemaError("grammar")
        index = int(label[7:])
        if not 1 <= index <= 17:
            raise SchemaError("grammar")
        for cell in parts[1:]:
            if cell in ("X", "x"):
                continue
            if cell == "Y" or not _integer_field(cell):
                raise SchemaError("grammar")
        rows[index - 1] = parts[1:]
    if not closed:
        raise SchemaError("grammar")
    return rows


def _pin(name, version, format_line):
    if version != PINNED_VERSION[name] or format_line != PINNED_FORMAT[name]:
        raise SchemaError("version")


def _u16(data, offset):
    return int.from_bytes(data[offset:offset + 2], "little")


def _u32(data, offset):
    return int.from_bytes(data[offset:offset + 4], "little")


def policy_parse(data, alpha2):
    """The kernel driver's mt7932_policy_parse (Ace, Ryan Murray) in Python; see
    ORIGINS-mt7932-policy.md. Zero means the driver accepts it.

    Table 2 holds six records for XZ and five for every other country.
    """
    if not isinstance(alpha2, (bytes, bytearray)) or len(alpha2) != 2:
        return -EINVAL
    size = len(data)
    if size < 16 or size > POLICY_MAX or data[:4] != b"J7RP":
        return -EINVAL
    if _u16(data, 4) != 1 or data[6:8] != bytes(alpha2):
        return -EINVAL
    modes = data[8]
    if modes < 2 or modes > 3 or data[9] != 13 or data[10] or data[11]:
        return -EINVAL
    if _u32(data, 12) != size:
        return -EINVAL
    at = 16
    for _ in range(modes):
        if size - at < 328 or _u32(data, at) or not _u32(data, at + 4) or _u32(data, at + 4) > 320:
            return -EINVAL
        at += 328
    table1_pad = None
    for index in range(13):
        if size - at < 44:
            return -EINVAL
        start = at
        number = _u16(data, start + 2)
        if number < 44 or number > 1020 or number > size - at:
            return -EINVAL
        if 1 <= index <= 9:
            count = 5 if index == 9 else 8
            if index == 2:
                count = 6 if alpha2 == b"XZ" else 5
            band = 1 if index <= 2 else 2
            last = 1 if index in (2, 9) else 0
            if (data[start] != 3 or data[start + 1] != 0x0A or data[start + 4] != count or
                    data[start + 5] != band or data[start + 6] != last or data[start + 7] or
                    data[start + 8:start + 10] != bytes(alpha2) or data[start + 10] or
                    data[start + 11] or number != 44 + count * 122):
                return -EINVAL
            if alpha2 == b"XZ":
                if index <= 2:
                    for channel in range(count):
                        if data[start + 44 + channel * 122] != (index - 1) * 8 + channel + 1:
                            return -EINVAL
                if index > 1 and data[start + 12:start + 44] != table1_pad:
                    return -EINVAL
            if index == 1:
                table1_pad = data[start + 12:start + 44]
        else:
            lengths = (532, 144, 58, 265)
            types = (5, 1, 4, 2)
            slot = 0 if index == 0 else index - 9
            if number != lengths[slot] or data[start] or data[start + 1] != 2 or data[start + 7] != types[slot]:
                return -EINVAL
            if index == 10 and (data[start + 8:start + 10] != bytes(alpha2) or data[start + 10] or data[start + 11]):
                return -EINVAL
        at += number
    return 0 if at == size else -EINVAL


def five_record_table2(blob):
    """The same policy with table 2's channel-14 record dropped, for the stock parser.

    Table 2 becomes five records (654 bytes) and the header's total shrinks
    by 122. Every other byte is unchanged.
    """
    if len(blob) < 16 or blob[:4] != b"J7RP":
        raise PolicyError("shape")
    at = 16 + 328 * blob[8]
    for _ in range(2):
        if at + 4 > len(blob):
            raise PolicyError("shape")
        at += _u16(blob, at + 2)
    number = _u16(blob, at + 2)
    if at + number > len(blob) or blob[at + 4] != 6 or number != 44 + 6 * 122:
        raise PolicyError("shape")
    if blob[at + 44 + 5 * 122] != 14:
        raise PolicyError("shape")
    out = bytearray(blob[:at + number - 122] + blob[at + number:])
    out[at + 2:at + 4] = (number - 122).to_bytes(2, "little")
    out[at + 4] = 5
    out[12:16] = len(out).to_bytes(4, "little")
    return bytes(out)


def join_status(country, power, sar, common, db_rows):
    if country == "00":
        country = "XZ"
    if country in TEMPLATE_SECTIONS:
        return "template"
    if country in NOT_LINUX_COUNTRY:
        return "not-a-linux-country"
    if country in SAR_ONLY:
        return "sar-only"
    if len(country) != 2 or any(character < "A" or character > "Z" for character in country):
        return "invalid"
    missing = []
    if country not in power:
        missing.append("power")
    if country not in sar:
        missing.append("sar")
    if country not in common:
        missing.append("common-path")
    if country not in db_rows:
        missing.append("2gCC")
    if missing:
        return "missing " + ",".join(missing)
    return "ready"


def _assemble_policy(country, power, sar, common, db, sdb):
    records = [
        (group, label, cells)
        for group, entries in power[country].items()
        for label, cells in entries
    ]
    spec_2g, spec_5g = load_power_rows(records)
    sar_rows = sar_matrix(sar[country]["tab"])
    backoffs = common_map(common[country]["common_path_backoff"])
    dsa = dsa_from_sections(db, country)
    return build_country_policy(
        country.encode("ascii"), spec_2g, spec_5g, sar_rows, backoffs, sdb, dsa)


def _text(tables, name):
    if name not in tables:
        raise SchemaError("missing " + name)
    try:
        return bytes(tables[name]).decode("ascii")
    except UnicodeError as error:
        raise SchemaError("grammar") from error


def inventory_digest(policies):
    records = "".join(f"{path}\t{hashlib.sha256(policies[path]).hexdigest()}\n"
                      for path in sorted(policies))
    return hashlib.sha256(records.encode("ascii")).hexdigest()


def verify_outputs(policies, pins, inventory_pin):
    for path, expected in pins.items():
        if path not in policies:
            raise PolicyError(f"missing pinned policy {os.path.basename(path)}")
        digest = hashlib.sha256(policies[path]).hexdigest()
        if digest != expected:
            raise PolicyError(f"{os.path.basename(path)} is not the pinned policy (sha256 {digest})")
    if inventory_pin is not None and inventory_digest(policies) != inventory_pin:
        raise PolicyError("complete policy inventory does not match the pinned output")


def derive(tables, pins=PINNED_OUTPUT, input_pins=PINNED_INPUT,
           inventory_pin=PINNED_INVENTORY):
    """{firmware path: bytes} for every complete country, each parser-gated.

    `tables` maps each table's file name to its bytes. With `pins`, every
    pinned output must be present and match, and the complete inventory must
    match `inventory_pin`. Tests pass None for synthetic output checks.
    """
    if input_pins is not None:
        for name in TABLES:
            if name not in tables:
                raise SchemaError("missing " + name)
            if hashlib.sha256(tables[name]).hexdigest() != input_pins.get(name):
                raise SchemaError(name + " is not the pinned input")
    power = parse_power_cells(_text(tables, POWER_NAME))
    sar = parse_sar_cells(_text(tables, SAR_NAME))
    common = parse_common_cells(_text(tables, COMMON_NAME))
    db = parse_db_cells(_text(tables, DB_NAME))
    sdb = parse_sdb(_text(tables, SDB_NAME))
    db_rows = {label for label, _cells in db["2gCC"]}
    policies = {}
    candidates = (set(power) | set(sar) | set(common) | db_rows) - {"00"}
    countries = {
        country for country in candidates
        if join_status(country, power, sar, common, db_rows) == "ready"
    }
    for _path, country in POLICIES:
        status = join_status(country, power, sar, common, db_rows)
        if status != "ready":
            raise PolicyError(f"join {country} {status}")
    for country in sorted(countries, key=lambda value: (value != "XZ", value)):
        name = "world-XZ" if country == "XZ" else country
        path = f"mediatek/mt7932/policy/{name}.bin"
        try:
            blob = _assemble_policy(country, power, sar, common, db, sdb)
        except (ValueError, RuntimeError) as error:
            raise PolicyError(f"{country} grammar: {error}") from error
        if country != "XZ":
            blob = five_record_table2(blob)
        if policy_parse(blob, country.encode("ascii")) != 0:
            raise PolicyError(f"{country} policy does not parse")
        policies[path] = blob
    if pins is not None:
        verify_outputs(policies, pins, inventory_pin)
    return policies


def read_tables(directory):
    """The five tables from a driver extension's IZUBA directory."""
    tables = {}
    for name in TABLES:
        with open(os.path.join(directory, name), "rb") as source:
            tables[name] = source.read()
    return tables
