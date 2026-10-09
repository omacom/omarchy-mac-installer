# Runnable synthetic exporter for integration development

This command supplies the first executable fixture in the [collaboration plan](../../../docs/try-omarchy-migration/collaboration-plan.md). It speaks the [migration contract](CONTRACT.md): `capabilities/1`, `inventory/2`, `export-request/2`, `progress/1` and `receipt/1`. It builds a fixed synthetic Try home (projects, settings, Try's own `input.lua`, `chromium-flags.conf`, `monitors.lua` and menu file, fake SSH and Brave secrets, and a `Work` link to `/mnt/mac`), collects it through the [Try policy](../lib/omarchy_migration/policies/try-omarchy-82927e9.json) with the [disposable collector](COLLECTION.md), and encrypts a `bundle/2` archive. It has no source-home argument, does not discover a real account, and is not the production migration API.

## Run

Use Linux and Python 3.11+; inventory and export collect through Linux descriptor mount identities. For export, configure the independently verified age 1.3.2 executable and hash described in [README.md](PROBE.md). Capabilities and inventory need no crypto dependency.

```bash
bin/omarchy-migration fixture capabilities
bin/omarchy-migration fixture inventory

bin/omarchy-migration fixture export \
  --request test/fixtures/fixture-request.json \
  --output-directory /absolute/path/to/a/new-private-job
```

The parent directory must exist; the command creates the job directory with mode 0700. The [sample request](../test/fixtures/fixture-request.json) uses a public fixture UUID; assign a new canonical UUID for a new integration job. A request is an `export-request/2` document bound to the fixture's `inventory_id` (derived from the synthetic content and policy revision, so it is stable) and its policy revision `try-omarchy/82927e9/4/fixture`. It selects categories (`files-and-projects`, `configuration`, `caches`; see `categories.py`), credential stores, the synthetic Mac shared folder (`mounts: ["mac-share"]`, copied in place of the `Work` link) and stores found inside it (`share_stores`, for example `share-ssh`) by id. The inventory measures that share and lists `share-ssh` as found inside it. Contract violations, duplicate JSON keys and requests larger than 8 KiB fail with `invalid_request` or `oversized_request` before creating output; a different inventory, policy revision, unknown category or store, or a store without an available adapter fails with `inventory_changed`, `policy_revision_mismatch`, `unknown_category`, `unknown_credential_store` or `credential_store_unavailable`.

All generated contents and the transfer passphrase are deliberately public test data. The passphrase is **synthetic-only-otter-maple-window-cobalt**. Selecting the `ssh` or `brave` store adds the fake entries through fixture-only byte-copy adapters (`fixture-ssh-bytes/1`, `fixture-browser/1`); every other store is advertised as unavailable. Do not use this command or passphrase for personal data.

## Observable behavior

An export emits `preparing`, `capturing`, `finalizing`, then `complete`; a reused job emits only `complete`. Each event is a validated `progress/1` document with the request ID (null only when the request could not be parsed) and a sequence number scoped to this CLI invocation. The completed event carries a `receipt/1` with the request ID, export ID, policy revision, ciphertext format, schema, byte count and SHA-256, and size estimates. The ciphertext is always `bundle.age` in the job directory. Private manifest filenames remain encrypted. The exported data has Try's integration content removed by the policy: `monitors.lua` and held-out stores are absent, Try's blocks and menu keys are stripped, and the `Work` link is inert.

The job directory contains `request.json`, `bundle.age`, and `receipt.json`. Completion requires the entire bundle to be authenticated and its decoded manifest compared with the selected synthetic input. Ciphertext, receipt, and directory entries are synchronized before the completed event. A bare ciphertext file is not a completed job; consumers require the receipt and verify bytes against it. Receipt/digest checking is not app authentication or proof of user consent.

Repeating the same request against a completed job validates the receipt, ciphertext, and export identity, then returns the same result with `reused: true`. It does not rewrite the bundle. A changed selection/request, changed ciphertext, symlinked job, or unrelated directory fails. A concurrent or interrupted job without a complete receipt returns `job_incomplete_use_new_directory`; the fixture does not start another writer or resume incomplete encryption. The Try controller remains responsible for identifying/attaching to its own active process. Use a new job directory after an incomplete fixture run, retaining the old one for inspection.

SIGINT/SIGTERM request cooperative cancellation. Checks occur between phases and archive writes; cancellation may wait for the current bounded age operation. Cancellation before receipt publication returns exit 130 and a `cancelled` event without a completed receipt. Once durable receipt publication starts, completion may win the race. SIGKILL, controller death, and power loss are not recovered by this fixture. Ordinary failure returns exit 1 and a `failed` event; command-line syntax errors use argparse's exit 2.

For deterministic cancellation/UI experiments, add `--pause-before-capture 30` and signal the owned process after its `preparing` event. The delay is limited to 30 seconds and is fixture-only. No real VM or application is stopped by this command.

## Limits

This is an explicit synthetic protocol, separate from the proposed production `omarchy-migration` command. It supplies inventory/export examples and a completed-job retry contract, not real consent, VM/account binding, authenticated IPC, application quiescence, importer/extraction, or arbitrary job recovery. The caller must own the output location; the fixture is not hardened against another same-user process maliciously replacing paths during an operation. The existing probe's PTY, process/resource, link/metadata, and live-capture limitations still apply.

The tests exercise the actual CLI and age executable, contract validation of every emitted document, request binding, category and fake-credential selection, an end-to-end restore that keeps personal content without Try integrations, repeated completion, changed request/ciphertext/receipt, unrelated directories/symlinks, malformed requests, cancellation, and duplicate invocation while active. These checks make the integration fixture usable for development; they do not qualify real user-data migration.
