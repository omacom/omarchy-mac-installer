# omarchy-migration

Moves an Omarchy home from Try Omarchy to a native Omarchy installation on the same Mac: a read-only survey, a policy-driven encrypted export, and a reviewable import. Version: see `version` (experimental, `0.x`). The same pinned build runs on both sides, inside the Try VM as the exporter and on the native system as the importer.

Status: exploration. The survey and the trial run against a real home read-only; the trial restores into a private folder outside the home and compares. Importing into a real home is proven on synthetic homes and disposable destinations only; do not use `apply` on real personal data yet.

## Where this package lives

This directory is temporary. The intent is to move the package to [omacom/omarchy-mac](https://github.com/omacom/omarchy-mac) as a top-level `omarchy-migration/`, beside `omarchy-mac/` and `omarchy-mac-boot/`, once that repository's reshaped layout (`proposal/main`) becomes its `main`. It is developed here until then so it does not track a moving branch. It already follows that repository's package contract, so the move is mechanical; its `omarchy-pkgs` recipe will pin an omarchy-mac commit after the move.

One difference from the other Mac packages: the exporter runs inside the Try Omarchy VM, where `omarchy-hw-apple-silicon` is false, so this package must not sit behind the Apple Silicon platform detector.

## Commands

```bash
omarchy-migration survey      # read-only summary of what a migration would bring
omarchy-migration export ...  # export this home into an encrypted bundle (passphrase on a descriptor)
omarchy-migration trial       # export, restore into /var/tmp and compare; the home is only read
omarchy-migration plan  ...   # review what importing a bundle would do (plan/1)
omarchy-migration apply ...   # import exactly a reviewed plan (report/1)
omarchy-migration validate F  # check contract documents
omarchy-migration evidence P C  # check a policy's evidence against a provider checkout
omarchy-migration fixture ... # synthetic exporter: capabilities, inventory, export
```

From this directory, run `bin/omarchy-migration`. Documents follow the versioned [migration contract](docs/CONTRACT.md); the [survey](docs/SURVEY.md), [collection](docs/COLLECTION.md), [restore](docs/RESTORE.md) and [review](docs/REVIEW.md) documents describe each part. Policies ship in `lib/omarchy_migration/policies/`, one per provider build, for example `try-omarchy-82927e9.json`.

## When it is installed and run

- **Native Mac:** the installer includes the package in the system it installs, so no network is needed at first boot. A one-shot unit runs the import offer only when the installer has staged a bundle, after owner provisioning and encryption setup succeed. (Unit and staging hand-off: tickets 08–09.)
- **Try VM:** installed when the user chooses Continue from Try Omarchy, through Try's integration channel with approval inside the guest (ticket 06), or manually from Omarchy's package repository. It runs only when the user approves a survey or export.

## Package contract

- `install DESTDIR` stages into an absolute root (`/usr/bin/omarchy-migration`, `/usr/lib/omarchy-migration/`, docs and license) and enables or starts nothing.
- `test/all` runs the package's own tests from a copy of this directory alone, then runs the staged command.
- Runtime dependencies: Python 3.11 or newer and `age` 1.3. Collection, survey and restore use Linux descriptor mount identities.

Tests that need encryption read `OMARCHY_TEST_AGE` and `OMARCHY_TEST_AGE_SHA256` for an independently verified age binary and skip without them; see [docs/PROBE.md](docs/PROBE.md).

## License

MIT, as Omarchy. See [LICENSE](LICENSE).
