# Engine overlay hardening

These engine overlay changes were split out of [PR 29](https://github.com/omacom/omarchy-mac-installer/pull/29), whose app-side hardening is recorded in `docs/battle-testing.md`. They change locked engine inputs, so they ship with a refreshed source lock and the reproducibly rebuilt engine `.29`.

## Changes and regression coverage

| Failure | Prevention | Regression coverage |
| --- | --- | --- |
| A resumed run encounters another APFS partition at the same location | Reconcile the saved target evidence before the next mutation; give a specific checkpoint-mismatch diagnostic and preserve the journal | Changed UUID, identifier, size, offset and type all produce the named failure; journal bytes remain unchanged; simulation retains the preparation checkpoint without enabling a fresh install |
| Container geometry changes during preflight | Check the source endpoint and APFS type before resizing | Changed source endpoint produces zero resize and partition-creation calls |
| A repair ZIP lacks a later image after an earlier partition has already been overwritten | Check every replacement's member, local header, size and decoder before opening any writable target; require manifest image sizes to fit their partitions and 4 KiB raw-device alignment | Missing/mis-sized later member leaves the earlier partition untouched; oversized and unaligned images produce no repair candidate |
| A non-reproducible engine build replaces the previous good output | Validate the temporary archive's modes, size and digest before atomic promotion; clean up on failure | Whole-script fixture uses mocked build tools and verifies the previous bytes survive all three rejection paths |

## Repair manifest compatibility and producer contract

The fixture `Engine/overlay/tests/fixtures/repair-manifest-canary-2004.json` preserves the exact bytes of a real manifest from the recorded physical M1 canary sequence 2004. It was extracted from [`sequence-2004-state.tar.gz` at source revision `92a9054f4565b37739ac3bd4f0fb4fcf8bd48625`](https://github.com/maralcbr/omarchy-mx-mac/blob/92a9054f4565b37739ac3bd4f0fb4fcf8bd48625/evidence/apple-silicon/2026-08-29-m1-canary-2004/sequence-2004-state.tar.gz), member `sequence-2004-ed71f0ec-1f5b-41c0-bb68-d27d89c54070/candidate/repair-manifest-2002.json`. The archive SHA-256 is `bfe1e461dae4745255e82cf281786a2cd554a9c0846ff5a400a6404e46ea88e5`; the exact manifest SHA-256 is `3fd0b3033d91666965e157a0fa271523aa5cafa3826721f53789c13d8edaa059`, pinned by the regression test.

For every rewritten role (`replacement_content[role].payload_member` is non-null), producers must describe both the existing hashed content and replacement image as positive whole-byte sizes no larger than that role's partition and divisible by 4096. The payload member's uncompressed size must equal the replacement size. Preserved roles have a null member and identical existing/replacement content identities. This is the consumer's required contract, checked before repair writes; it is not a claim that an unavailable producer implementation enforces it.

The recorded manifest meets that contract: boot has 2,147,483,648 existing and replacement bytes in a 2,147,483,648-byte partition; root has 34,359,738,368 existing and replacement bytes in a 132,267,376,640-byte partition. Both are 4096-aligned. Stub and EFI are preserved. The test passes the unmodified manifest through repair candidate discovery with mocked live disk/filesystem inspection and expects one candidate. It is a compatibility characterization, not a new failure reproduced against the old implementation. This archive was explicitly a non-release physical canary; it establishes compatibility with that real consumed artifact, not all published or future repair manifests. No current production manifest or producer implementation is inferred from it. A subsequent [release and producer evidence search](repair-manifest-release-research.md) verified signed public catalogs and inspected image-producer source, but did not locate a released repair manifest or its generator; this review requirement remains open.

## Release boundary

These edits ship in engine `.29` (`installer-v0.9.2-omarchy.29.tar.gz`, 17,844,091 bytes, SHA-256 `3a87e43b023e050c725d2e005bddd3721cf00f5e77104635e6a1e35411804d50`), recorded in `Engine/source-lock.json` with the refreshed source hashes and reproduced as described in `docs/extraction.md`. The packager, Swift locator and release-input templates pin `.29`. Before merging or distributing, validate the assembled package and qualify interrupted stage-one resume and repair on authorized supported hardware. `apple,j614s` remains blocked.

PR 29 teaches the app the engine's `prepared resume target does not match checkpoint` diagnostic, so no further Swift change is needed when this engine ships after it.

## Remaining engine work

1. **Repair durability (before enabling physical repair).** The repair writer still uses `fsync`; the fresh-install image writer has the macOS raw-device synchronization primitive. Reuse that primitive with a fake flush seam and verify it on authorized hardware. Malformed member tables now fail before writes, but corruption or I/O failure encountered during actual writing can still leave a partial repair.
2. **Geometry identity boundary.** The new resize check rejects endpoint drift but does not introduce a new UUID-bearing plan format or an OS-level lock against another disk management process. Revalidate live resize limits/identity as close to mutation as practical; qualify behavior if another process changes the disk. The existing engine and diskutil checks remain necessary.
