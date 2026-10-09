# omarchy-migration contract v1

Status: experimental draft. Until the first release, draft schemas (including `bundle/2` and its provenance) may change without a version bump and older drafts are not readable; no bundle outside tests exists yet. Developed for ticket 04, validated by `contract.py` and its fixtures. The disposable collector applies the policy through `policy.py`, the runnable fixture exporter emits and consumes the export documents, and `review.py` emits `plan` and `report` from the restorer; transforms run at export, so restore needs no provider rules. It formalizes the documents the disposable probe already exchanges so that Try, the installer and the native importer can be built against one versioned interface. It is not yet production code; the canonical home remains the shared Omarchy runtime (`bin/omarchy-migration`, `install/migration/omarchy_migration/`).

## Documents

Every document is a UTF-8 JSON object whose `schema` names exactly one document type and version. Version numbers change only on an incompatible change; a consumer rejects any schema it does not list in its capabilities.

| Schema | Visibility | Producer → consumer | Purpose |
| --- | --- | --- | --- |
| `omarchy-migration/capabilities/1` | public | module → app | Module version, operations, documents, bundle formats, policy revisions and credential adapters |
| `omarchy-migration/inventory/2` | public | source module → Try → installer | Source identity and per-category counts for review; no paths |
| `omarchy-migration/export-request/2` | public | app → source module | The user's selection, bound to one inventory and policy revision |
| `omarchy-migration/progress/1` | public | source module → app | One event per line on stdout |
| `omarchy-migration/receipt/1` | public | source module → installer → staging | Ciphertext identity and size estimates; the only document that travels with the bundle |
| `omarchy-migration/plan/1` | public | native module → installer UI | Reviewable summary of an import plan |
| `omarchy-migration/report/1` | public | native module → app | Per-category outcome with reason codes |
| `omarchy-migration/policy/2` | trusted input | shipped with the module | Credential stores, mounts and provider rules for one source build |
| `omarchy-migration/bundle/2` | private | inside the ciphertext | Manifest with authenticated provenance, and TAR objects |

Private documents never cross an app boundary in plaintext: the bundle manifest, restore journal, collection report and per-file results stay inside the ciphertext or the owner's 0700 job directory.

## Common rules

- Unknown and missing fields are errors. Duplicate keys, `NaN`/`Infinity` and non-UTF-8 input are rejected before validation.
- Documents are at most 1 MiB; an export request is at most 8 KiB.
- Identifiers are canonical lowercase UUID strings. Digests are 64 lowercase hex characters. Labels (`id`, `policy_revision`, adapter ids) match `[a-z0-9][a-z0-9._/-]*`, at most 128 bytes. Reason and error codes match `[a-z][a-z0-9_]*`.
- Integers are JSON integers, never booleans. Lists inside a document hold at most 1024 items. Entry counts are bounded by 200,000, expanded bundle content by 256 GiB, a bundle manifest by 64 MiB, and ciphertext by that content plus manifest plus 1 GiB of format overhead. These are backstops; the working bound is free space, estimated in whole 4 KiB blocks per file. An importer refuses a bundle that does not fit in its scratch space before decrypting any object, `plan` reports the target space a restore needs (including backups for replacements) and `apply` refuses without it, and a collector refuses a snapshot that does not fit.
- Home paths in a policy are relative to the owner's home: no leading `/`, no empty, `.` or `..` component, at most 64 components of 255 bytes and 4096 bytes in total.
- No document carries a passphrase, a serialized shell command, a destination home path or an absolute source path. Destination identity comes from the trusted caller, never from imported metadata.

## Public documents

**capabilities** lists `module {name: "omarchy-migration", version}`, the supported `operations` (subset of inventory, export, plan, apply, report), the `documents` schemas it reads or writes (including itself), `bundle_formats` (`age-v1-scrypt`), the `policy_revisions` it carries, and `adapters`. An adapter is `{id, category, available}`; an unavailable adapter must give a `reason` code and an available one must not. Unsupported stores are advertised as unavailable instead of falling through to raw copying.

**inventory** binds `inventory_id` to `source {provider, architecture, omarchy_version, account_uid}` and a `policy_revision`. `categories` give `{id, files, bytes, default_selected}`; `credential_stores` give `{id, category, present, adapter_available}`. Stores are never selected by default. `mounts` lists each shared folder: `{id, linked, measured}`, plus `files`, `bytes` and the `share_stores` found inside it (`{id, category}`) when it was measured. A UI offers the share as its own choice and each store inside it as a further, unticked choice.

**export-request** binds `request_id` to `inventory_id` and `policy_revision`, and selects at least one category, zero or more credential stores, shared folders (`mounts`) and stores inside them (`share_stores`) by id. Share stores without a selected share are rejected. The transfer passphrase reaches the exporter through a terminal or pipe, never in this document, argv, logs or staged files.

**progress** has `request_id`, `sequence` (from 1 per invocation) and `phase`: preparing, capturing, finalizing, complete, cancelled or failed. `complete` adds `receipt` (whose `request_id` must match) and `reused`; `failed` adds an `error` code. `request_id` is null only on `failed` when the request itself could not be parsed. A reused completed job may emit only `complete`.

**receipt** binds `request_id`, `export_id` and `policy_revision` to `bundle {format, schema, bytes, sha256}` and `estimates {expanded_bytes, entries}` used for capacity preflight. It has no filename or private paths; staging chooses the location.

**plan** binds `plan_id` to `export_id`, the authenticated `bundle_sha256` and `policy_revision`, names the destination `account_uid` (1000 or above) and summarizes `actions` (create, present, replace, conflict, omit, inert), `packages` (reinstall, manual) and `required_bytes`. Applying requires the exact plan; a changed destination becomes a conflict.

**report** binds `job_id`, `export_id` and `plan_id`, and gives each category an `outcome` (restored, partial, skipped, failed, unavailable), counts (restored, conflicts, omitted) and `reasons` codes. Per-file detail stays private.

## Policy

A policy is the trusted, versioned description of one source build. `revision` names it (for example `try-omarchy/82927e9/4`) and `source {provider, repository, commit}` pins the exact provider commit it was derived from.

- **credential_stores** `{id, category, roots, adapter}` are matched first, before traversal, stat or open. A store is exported only when explicitly selected and only through an available adapter. Roots may not overlap each other or any rule.
- **share_stores** `{id, category, directories, files}` recognize credential locations inside an explicitly selected shared folder by name only: `directories` are relative paths matched as trailing components at any depth (for example `.ssh` or `Library/Keychains`), `files` are file-name patterns (for example `*.pem`). Contents are never inspected. A matched entry is held back unopened unless the user selects that share store separately, so a UI can offer each one as its own choice.
- **mounts** `{id, path, reason, evidence}` are absolute guest mount points such as the Mac share at `/mnt/mac`. Links into a mount are recorded as inert and never traversed; shared contents need an explicit selection.
- **rules** `{id, path, match, action, reason, evidence[, transform]}` apply to one `exact` path or a whole `tree`. Actions are `exclude` (never exported; excluding a path also excludes everything beneath it), `preserve` (exported as personal configuration) and `transform` (exported after a declared, data-only change; `exact` matches only). Rules may not nest inside each other, so the outcome never depends on rule order.
- **transforms** are `strip-appended-block` (remove one provider-appended block starting on a line boundary, in LF or CRLF form, with or without its final newline at end of file) and `remove-json-keys` (remove named top-level keys from JSON or JSONC, keeping every other member and comment). A `strip-appended-block` may list `earlier` versions of the block, each citing the provider `commit` and `path` whose text holds it (the `evidence` command checks each one in the provider's history), because files written by older provider builds keep their older block; it may also list `markers`, provider-specific strings every block contains, and then residue means a remaining line with a marker rather than any repeated block line, so ordinary user lines that resemble the block survive. Both fail closed: a repeated block, a block line (or marker) left behind, malformed or non-standard JSONC (including non-JSON numbers and ambiguous line separators in comments) or input over 1 MiB withholds the file. No transform executes code.
- **evidence** `{path[, sha256]}` cites the provider file a rule was derived from. `evidence.py` reports missing or changed evidence in a provider checkout; any drift requires a new policy revision before that provider build is supported. `sync-try` runs this on every Try update.

Unknown personal configuration that matches no rule is preserved. Display, graphics, boot, hardware identity and VM integrations are excluded through rules rather than by guessing.

## Bundle and encryption

The bundle is an age v1 file with exactly one scrypt recipient at work factor 18, a four-line header no longer than 80 bytes per line, and no other stanza. The checked header bytes are what the decoder passes to age. The plaintext is a USTAR stream whose first member is the canonical manifest (`omarchy-migration/bundle/2`), followed by one regular member per file entry in manifest order. The manifest's `provenance` records the policy revision, digests of the policy and collection request, the migration-owned root holding original copies of transformed files (or null), the included entries whose metadata the bundle cannot carry (`metadata`: `{archive, lost}` with `acl`, `extended-attributes`, `sparse`, `special-permission-bits`), plus every collection exception (held out, excluded, transformed, unsupported or inert link, with its store, rule or mount) and outcome counts that must agree with them. Because it is inside the authenticated archive, an importer takes the policy revision from it: a receipt naming another revision is refused, and bundles without provenance cannot be planned. See the probe's `TREE.md` and `README.md` for entry, link and size rules. A bundle is published only after authenticated EOF; truncated, tampered or wrong-passphrase bundles never produce output.

## Error codes

Validation codes: `not_an_object`, `missing_field`, `unknown_field`, `not_an_integer`, `not_a_boolean`, `not_a_list`, `out_of_range`, `invalid_string`, `invalid_uuid`, `unsupported_value`, `unsupported_schema`, `missing_value`, `duplicate_item`, `duplicate_key`, `inconsistent_fields`, `identity_mismatch`, `unsafe_path`, `overlapping_paths`, `invalid_json`, `oversized_document`, `unreadable_document`. Each error also reports a `where` locator such as `$.receipt.bundle.sha256`.

## Fixtures

`fixtures/valid/` holds one document of every public type, sharing one export flow from inventory through report. `fixtures/invalid.json` holds language-neutral negative cases: each names a valid base document, a `set` or `delete` patch at a key path, and the expected code, plus raw byte cases. Other implementations, including the Try Mac app, should run the same cases.

## Differences from the probe

- Each document type now has its own schema instead of sharing `omarchy-migration-fixture/1`.
- The receipt drops `filename` and the probe's `synthetic` flag, and checks digest, identity and size strictly.
- One policy replaces the probe's prefix list (`probe.HOLDOUTS`) and the fixture-only collection layout, adding Chromium and GNOME Keyring stores.
- The request selects categories and stores by id instead of a single `include_credentials` flag.

## Decisions

Recorded 2026-10-02 with Scott.

- Time zone is chosen in the installer, so it is never migrated. Try's `/var/lib/try-omarchy/timezone.json` is outside the home directory and outside the export.
- `.config/chromium-flags.conf` loses Try's `--enable-wayland-ime` block. If native Omarchy needs the flag, its own installation provides it; restore is additive and never replaces a destination default without an approved conflict resolution.
- `.config/hypr/monitors.lua` is excluded as a whole. It describes the Mac host display through Try, including scaling, and does not carry over to native hardware.
