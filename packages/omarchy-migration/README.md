# omarchy-migration

Moves an Omarchy home from Try Omarchy to a native Omarchy installation on the same Mac: a read-only survey, a policy-driven encrypted export, and a reviewable import. Version: see `version` (experimental, `0.x`). The same pinned build runs on both sides, inside the Try VM as the exporter and on the native system as the importer.

Status: exploration. The survey and the trial run against a real home read-only; the trial restores into a private folder outside the home and compares. The native claim, offer and import are built and tested on synthetic homes, real loop disks and Linux containers, not yet on a Mac install.

## Where this package lives

The package lives in the installer's repository. Try Omarchy vendors a pinned commit of it into its guest package as the exporter, and the installer's image builder installs it into the native system as the importer ([native import](../../docs/native-import.md)), so neither side needs a package repository.

One difference from the other Mac packages: the exporter runs inside the Try Omarchy VM, where `omarchy-hw-apple-silicon` is false, so this package must not sit behind the Apple Silicon platform detector.

## Commands

```bash
omarchy-migration survey      # read-only summary of what a migration would bring
omarchy-migration export ...  # export this home into an encrypted bundle (passphrase on a descriptor)
omarchy-migration trial       # export, restore into /var/tmp and compare; the home is only read
omarchy-migration plan  ...   # review what importing a bundle would do (plan/1)
omarchy-migration apply ...   # import exactly a reviewed plan (report/1)
omarchy-migration claim       # native first boot, as root: claim the export the installer staged
omarchy-migration import      # native, as the owner: review and import a claimed export (omarchy-migration-import)
omarchy-migration offer       # native user session: notify when a claimed export waits (omarchy-migration-offer)
omarchy-migration validate F  # check contract documents
omarchy-migration evidence P C  # check a policy's evidence against a provider checkout
omarchy-migration fixture ... # synthetic exporter: capabilities, inventory, export
```

From this directory, run `bin/omarchy-migration`. Documents follow the versioned [migration contract](docs/CONTRACT.md); the [survey](docs/SURVEY.md), [collection](docs/COLLECTION.md), [restore](docs/RESTORE.md) and [review](docs/REVIEW.md) documents describe each part. Policies ship in `lib/omarchy_migration/policies/`, one per provider build, for example `try-omarchy-90c8210.json`.

## When it is installed and run

- **Native Mac:** the installer includes the package in the system it installs, so no network is needed at first boot. `omarchy-migration-claim.service` runs once, before owner setup, when the installer staged an export. `omarchy-migration-offer.service`, a user unit, offers it at the owner's login, and `omarchy-migration-import` takes it through the sudo handoff and imports it ([native import](../../docs/native-import.md)).
- **Try VM:** installed when the user chooses Continue from Try Omarchy, through Try's integration channel with approval inside the guest (ticket 06), or manually from Omarchy's package repository. It runs only when the user approves a survey or export.

## Package contract

- `install DESTDIR` stages into an absolute root: the commands in `/usr/bin`, the library and `handoff` in `/usr/lib/omarchy-migration/`, the claim unit and the offer user unit with their vendor `.wants` links, the sudo rule (0440), docs and license. It starts nothing.
- `PKGBUILD` builds the native pacman package from this folder with `makepkg`. Its only download is `age` 1.3.2 for arm64, pinned by tarball and binary digest and installed as `/usr/lib/omarchy-migration/age` with `age.sha256`.
- `test/all` runs the package's own tests from a copy of this directory alone, then runs the staged command. As root, it runs only `test/claim-integration.sh`, when loop devices are available: it stages a real bundle on a sparse 40 GiB loop disk, encrypts and grows Root as the native first boot does, then claims, hands off and imports it, for an encrypted and an unencrypted root.
- Runtime dependencies: Python 3.11 or newer, `sudo`, and `age` 1.3. Collection, survey and restore use Linux descriptor mount identities.

Tests that need encryption read `OMARCHY_TEST_AGE` and `OMARCHY_TEST_AGE_SHA256` for an independently verified age binary and skip without them; see [docs/PROBE.md](docs/PROBE.md).

## License

MIT, as Omarchy. See [LICENSE](LICENSE).
