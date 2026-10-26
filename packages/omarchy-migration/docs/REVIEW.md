# Reviewable import plans and reports

`review.py` turns the [restorer](RESTORE.md) into the two documents an import review needs: a [`plan/1`](CONTRACT.md#public-documents) before anything changes, and a `report/1` afterwards. It inherits every boundary of the restorer it wraps. The native import (`omarchy-migration import`, see [native import](../../../docs/native-import.md)) runs it against the owner's home with `--defaults /etc/skel`.

## Run

Use Linux and Python 3.11+. Export a bundle with the [fixture](FIXTURE.md) first, then:

```bash
bin/omarchy-migration plan \
  --bundle job/bundle.age --receipt job/receipt.json \
  --target /path/to/disposable-target --job /path/to/private-restore-job \
  --passphrase-fd 3 3<passphrase-file

bin/omarchy-migration apply --plan-id <plan_id from the plan> \
  --bundle job/bundle.age --receipt job/receipt.json \
  --target /path/to/disposable-target --job /path/to/private-restore-job \
  --passphrase-fd 3 3<passphrase-file
```

Options shared by both operations:

- `--defaults DIR` lets the plan replace a regular file whose destination is byte-identical to `DIR/<same relative path>`. Each replacement keeps a private backup in the job ([REPLACEMENT](REPLACEMENT.md#replacing-untouched-defaults)). Any other existing file stays a conflict.
- `--summary FILE` also writes a private per-path summary (`omarchy-migration/import-summary/1`, a new 0600 file) for the owner's own review: counts per category, the paths that replace a default, the paths that stay with their reason code, what the export's policy left out, and the Try home's Omarchy theme and background (`omarchy`), which the import applies with Omarchy's commands. Omarchy's current theme state, `.local/state/omarchy/current`, is never written: Omarchy regenerates it, so its entries are omitted as `regenerated_by_omarchy`. It never leaves the owner's machine.
- `--scratch DIR` puts the decrypted scratch in a private folder under `DIR` instead of the system temp folder, which is often memory-backed.
- `--age PATH` and `--age-sha256 DIGEST` name the age executable. Without them, the verified test dependency (`OMARCHY_TEST_AGE`, [PROBE](PROBE.md#run-checks)) is used when set, then the packaged `age` beside the library, checked against the digest in `age.sha256`.

The passphrase is read from a file descriptor, never from arguments: at most 1024 bytes plus one optional trailing newline; empty input, CR, NUL or a second line is refused as `passphrase_invalid`. The descriptor must reach end of file. A passphrase that doesn't open the bundle fails with `passphrase_incorrect`, so a caller can ask again. Operational errors print one JSON object with an `error` code to standard error and exit 1; command-line usage errors, including `--plan-id` on `plan` or its absence on `apply`, exit 2 with usage text. Errors never include the passphrase or destination paths.

## What the plan binds

The plan is computed after the bundle is fully authenticated. Its `bundle_sha256` is the digest of the exact ciphertext bytes that age authenticated, hashed while they were fed to age, not a separate read of the file. The public receipt must name the same export, ciphertext digest and policy revision as the authenticated bundle and its provenance, or planning fails with `receipt_mismatch`; a bundle without provenance fails with `provenance_missing`. `plan_id` is derived from that digest, the destination account, the restorer's pinned job binding (manifest digest, export, target and job directory identities, approved replacements), the canonical receipt including its policy revision, and every planned action. With `--defaults`, the binding also holds every bundle path that has a default and that default's digest, so the plan changes when a default changes. A destination that still holds its default plans as `replace`; one that differs from it plans as a conflict with `native_file_changed`. It changes when the destination, job directory, receipt or any planned action changes. An empty export cannot be planned (`empty_bundle`). `apply` re-plans in its own process and refuses with `plan_changed`, writing nothing, unless the result has the reviewed `plan_id`.

Counts map every restorer action, including directories, onto the contract: `create`, `present` (including entries this job already restored), `replace`, `conflict` and `inert`. `omit` stays 0 because unselected categories never enter the bundle. `required_bytes` is the size of files to be written, and for each replacement also the size of the original it keeps a private copy of. Package reinstall counts are zero until application support exists. `plan` warns on standard error (`insufficient_space`, with the missing bytes) when the target's free space is below `required_bytes` plus a 64 MiB margin for the journal and job copies; `apply` refuses with `insufficient_space` and writes nothing.

## What the report says

`report/1` groups results by [category](../lib/omarchy_migration/categories.py). Directories count as restored entries. Any status other than the restorer's known results is refused rather than guessed. A category is `restored` when everything selected is in place, `partial` when some entries restored and some conflicted, `failed` when nothing restored because of conflicts, and `skipped` when everything was omitted. `omitted` counts inert entries, such as a link into the Mac shared folder. A category whose entries lost metadata the bundle cannot carry also lists `metadata_not_preserved`. Reasons are stable codes mapped from the restorer's messages (`destination_exists`, `destination_changed`, `dependency_unavailable`, `link_not_restorable` and others); a test fails if the restorer gains a message without a code. `job_id` is derived from the restorer's job binding, so retries of one job report under the same id. Per-file detail stays in the private job.

## Limits

Directories the job created get their source mode and mtime once everything beneath them is restored (`directory_metadata_restored`); until then they report `directory_metadata_deferred`. If `apply` fails partway, for example because a destination directory disappears, the command exits 1 with an error code and no report; the restore journal keeps what was published, and running `plan` and `apply` again continues the same job. Plans are summaries for review, not a substitute for the restorer's own journal and conflict checks, which still run during `apply`. The command inherits every restorer boundary: a quiescent destination, and no privilege, account creation or application installation.
