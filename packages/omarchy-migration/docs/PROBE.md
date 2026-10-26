# Disposable encrypted-bundle probe

This synthetic experiment exercises an encrypted archive using age 1.3.2. It is not a shipped exporter, home scanner, or home-directory importer.

The [runnable integration fixture](FIXTURE.md) now exposes synthetic capabilities, inventory, export progress, cancellation, and completed-job reuse through a development command.

The [additive restoration experiment](RESTORE.md) authenticates into private scratch before planning and applying regular files to a disposable destination. It preserves existing-file conflicts and detects later edits or deletions on retry.

The [v2 tree extension](TREE.md) additionally preserves explicit/empty directories and selected relative symlinks, keeps unsupported links inert, and roundtrips a real synthetic Git repository with modified and untracked work. New directory metadata is explicitly deferred; no home scanner or production capture support is implied.

The [approved replacement extension](REPLACEMENT.md) backs up selected differing regular files in a private job before replacing them, conservatively reconciles interruptions, and preserves later edits/deletions. Replacement requires a quiescent destination and explicit caller approval; it is not yet a product workflow.

## What the experiment covers

The [credential-aware collection extension](COLLECTION.md) captures caller-created disposable fixture trees into a private snapshot, applies the trusted [contract policy](CONTRACT.md#policy) (store holdouts, exclusions, transforms and mounts) before traversal, and supplies copied objects to the existing v2 exporter. This is separate from real-home and application-adapter support.

- Whole-archive encryption, including the private manifest and filenames, with a deliberately synthetic transfer passphrase.
- Streaming content-digest, mode, and timestamp validation for numbered regular-file objects.
- Credential holdouts come from the shared policy and apply before serialization, with explicit inclusion only through fixture adapters. Fake SSH and Brave examples are not qualified application adapters.
- Rejection of wrong passphrases, tampering/truncation, unsupported schema/age headers, excessive declared work, unsafe paths, duplicate entries, and malformed metadata.
- Passing the already checked age header to the decoder so a subsequent source-header change cannot increase key-derivation work.
- Cancellation and non-overwriting publication of complete ciphertext; a successful decode requires authenticated EOF and a successful age exit.

## Run checks

Use Linux, Python 3.11 or newer, Git for the tree roundtrip case, and a trusted age 1.3.2 executable. The PTY adapter uses Unix terminal facilities and English prompts; only Linux has been exercised. Tests download nothing and do not install packages. Obtain age through a trusted package or verified upstream distribution before selecting it. The executable digest detects changes after that verification; a checksum alone does not establish provenance.

From the repository root, configure the absolute executable path and its SHA-256 after independent verification:

```bash
export OMARCHY_TEST_AGE=/absolute/path/to/verified/age
export OMARCHY_TEST_AGE_SHA256=the_64_character_lowercase_sha256_of_that_executable
python3 -W error::ResourceWarning -m unittest discover \
  -s test -t test -p 'test_*.py'
```

The suite fails on a missing/mismatched digest or an age version other than 1.3.2. Without `OMARCHY_TEST_AGE`, the suite explicitly skips; a skipped run is not encryption-test evidence. `./test/all` includes this discovery with the same environment contract. Different architectures can use their own independently verified age 1.3.2 binaries and corresponding hashes; no binary is included in this repository.

Tests create fake source files and encrypted output in a private temporary directory and clean it afterward. The public synthetic passphrase is never intended for real exports; it crosses a private non-echoing PTY instead of argv, environment, or a passphrase file. A 36 MiB streaming fixture measures Python allocations, excluding the age subprocess and its KDF memory.

## Deliberate limits

`decode` validates and hashes the archive; it does not extract files. The experimental `verified_bundle` interface reuses that validator to stage private numbered objects, then allows restoration through `Restorer`. The v2 manifest represents directories and symbolic links as authenticated metadata; TAR links, hardlinks, PAX extensions, and device entries remain rejected. Directory metadata finalization, broader link support, extended metadata, production replacement/backup integration, full account mapping, and real-home qualification remain production work.

Direct probe mappings still stat/hash inputs and later reopen them. The disposable collector isolates copied bytes and detects source changes under a quiescent-fixture contract; it does not establish consistent capture of live applications. Its policy-driven holdouts exercise early pruning and aliases, but production recognized paths, actual layouts and application support remain unqualified.

The header admission policy deliberately accepts only the pinned passphrase profile with scrypt logN 18. It is not a general age decoder. The maintained age implementation performs cryptographic authentication. The PTY/subprocess adapter, its 60-second deadline, and `preexec_fn` are experimental and are not a supported multithreaded GUI integration API. Production process/memory bounds and long-running export progress remain unfinished.

The probe synchronizes ciphertext before non-overwriting publication but does not synchronize the output directory. Its tests establish cancellation/non-overwrite behavior, not crash-durable export completion. Production finalization requires its own durability contract.

No real personal data, application store, credential, VM disk, or native target is accessed by these tests. No installer/Try app connection, application sign-in continuity, reboot survival, or native hardware support is established.
