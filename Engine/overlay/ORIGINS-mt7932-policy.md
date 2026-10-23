# Origin of the MT7932 country policy code

`src/omarchy_mt7932_policy.py` and `tests/test_omarchy_mt7932_policy.py` are ported from the MT7932 policy extractor written by Chris Kearney, used here under the MIT license.

## Licence and authorship

- Chris Kearney wrote the MT7932 policy extractor this is ported from and agreed to its use under the MIT license (2026-10-19).
- `policy_parse` follows the kernel driver's `mt7932_policy_parse` (drivers/net/wireless/mediatek/mt7932/regulatory.h), added by Ace and since changed by Ryan Murray. Ace and Ryan Murray agreed to its use under the MIT license on this pull request (2026-10-20, https://github.com/omacom/omarchy-mac-installer/pull/96#issuecomment-6281760070).

## What was ported

- `extractor/mt7932_policy.py`: the whole builder (cell encodings, the descriptor subsample, table 0's bitmap and pad, tables 1 to 12, the mode records and `build_country_policy`). Its unused `PolicyBlocked` class was dropped.
- `extractor/extract-mt7932.py`: only the parts the policy needs. These are the text-table grammar and parsers, `_assemble_policy`, `join_status`, the version-line pins (re-pinned to the 26.6.2 (25G83) tables the engine fetches) and `policy_parse`, which follows the kernel's `mt7932_policy_parse` (see above). It now encodes the stock driver's rule: six table-2 records for XZ and five for every other country. The CLI, file writes, J7CF packing, firmware region walks and `cal_validate` were not ported, because `omarchy_mt7932` already covers those inputs.
- `extractor/tests/test_mt7932_extract.py`: only the pure-Python tests and their synthetic fixtures. These are the builder map tests, the text grammar rules and the parser rule cases, which now assert the expected values directly rather than comparing against the C oracle.

Added in this repository: the five-record transform for non-XZ countries, `derive()` with its pinned output hashes, the wiring into `omarchy_mt7932.collect` and `omarchy_asahi`, and the tests for those.

## What was not included

- `extractor/tests/mt7932_oracle.c` (GPL-2.0-only) and the `extractor/tests/mt7932-host/` kernel-header stubs.
- The tests that need the C oracle, a C compiler, the linux-aurora tree or its driver sources.
- The notes: `mt7932-neo-extract.rst` (GPL-2.0-only), `regulatory.h.patch`, `column-map.md`, `sections.txt` and `mt7932-extract-manifest.txt`.
- The bundle's `policy/*.bin` files.

## No power-table or firmware data

This code and its tests contain no Apple firmware or power-table data. The tests use synthetic tables only. The installer derives the policy files at install time from the Apple tables it fetches for that Mac, so no policy file is distributed with the engine. The code does hold fixed tables of its own: the record layout (`DESCRIPTORS`), each country's window class (`_WINDOW_CLASS`) and the channel-to-center map (`_CENTER_ROWS`).

The pinned 25G83 inputs derive 244 policies. A canonical SHA-256 inventory covers every sorted path and blob digest; world, US and Canada additionally have individual pins, and every individually pinned output must exist. Only those three have Neo bring-up evidence. Reference-extractor parity and parser acceptance do not constitute hardware qualification of the remaining countries; the expanded coverage is an explicit part of maintainer review.
