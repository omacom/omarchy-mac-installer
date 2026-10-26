# Native import of a Try export

Status: the contract for bringing a Try Omarchy export onto a native install. The export, its publishing and Migrate to Native's discovery are in [Try in a VM](try.md#migrating-to-native) and Try's [native install export](https://github.com/omacom/try-omarchy/blob/main/docs/native-install-export.md). This page covers the rest: staging the export onto the native disk during the install, claiming it at the native system's first boot, and importing it into the owner's home.

## What people see

1. In the installer, **Migrate to Native** chooses an export, and the plan review names it. The install needs room for the export twice, once staged and once claimed, and for its files twice: once imported, and once decrypted while the import runs. A release whose image can't claim an export says so in the plan review, and installs without it.
2. When the install finishes in macOS, the installer stages the export onto the new Root partition. The result screen says whether it did. A failure never stops the install: the export stays in Exports.
3. The first boot of the native system claims the staged export, before owner setup.
4. At the owner's first login, a notification offers **Bring your files from Try Omarchy**. It opens a terminal that asks for the transfer passphrase, shows what the import brings and what it leaves, and imports after a confirmation. **Not now** leaves the offer for the next login. **Don't offer again** ends it, and deletes the copy on the native disk. The export stays in Exports on the Mac.
5. Settings that Omarchy shipped and the owner never changed are replaced by the Try home's versions, with a backup. A file the owner changed on the native system stays, and the summary lists it.
6. The Try home's theme and background are applied with Omarchy's own commands. The import then offers to reboot, so every app picks up the settings.

## Staging

The engine writes `root.img` into the Root partition, and Root takes the rest of the approved extent. Until Linux boots, nothing past the image is in use. After a completed engine run, and after `install.conf`, the app asks the helper to stage the chosen export. The helper:

1. Requires the machine owner's credentials and the completed engine plan, like `writeInstallConf`, and stages once per completed plan.
2. Finds the Root partition: the last Linux partition inside the plan's extent, larger than the floor below. It reads its offset, length and GPT unique partition GUID from `diskutil info`.
3. Reads the bundle from the file handle the app passes, never from a path. It refuses anything that isn't a regular file of the receipt's size.
4. Computes the region (below), writes the bundle to the raw partition in 4 MiB blocks, padded with zeros to 4 KiB, and hashes what it writes. It synchronizes the device, reads the region back and compares the digest with the receipt's.
5. Writes `omarchy/migration.json` on the ESP last, atomically, with a readback, as it writes `install.conf`.

A staged export is a commit marker on the ESP and ciphertext in Root's free space. Without the marker, the first boot finds nothing, and the bytes are free space the filesystem later reuses.

### The region

All numbers are bytes, relative to the start of the Root partition.

- The bundle is `length` bytes. It occupies `written` bytes, `length` rounded up to 4 KiB.
- `end` is the partition length minus 64 MiB, rounded down to 1 MiB.
- `offset` is `end - written`, rounded down to 1 MiB.
- If `[offset, offset + written)` overlaps `[256 GiB - 1 MiB, 256 GiB + 64 MiB)`, `offset` becomes `256 GiB - 1 MiB - written`, rounded down to 1 MiB.
- The export is not staged when `offset` is below `33 GiB + length + 4 GiB`.

The reasons, from the native first boot (`omarchy-mac-boot`):

- `root.img` is at most 32 GiB. Encryption at first boot shrinks the filesystem by 32 MiB and encrypts only its extent, shifting it by the 32 MiB LUKS2 header. Nothing below 32 GiB + 32 MiB is written by encryption, and nothing above it is touched before the claim. The 33 GiB floor leaves room.
- `x-systemd.growfs` grows Btrfs over the whole partition at boot. Btrfs writes a superblock copy at 256 GiB into its device, which is the partition, or the LUKS mapping 32 MiB into it, so the region stays clear of that window. It allocates new chunks first-fit from the start of the device, so the end of the partition stays untouched until the disk is nearly full.
- The 4 GiB margin, and `length`, leave room below the region for the claimed copy.

`packages/omarchy-migration/test/fixtures/staging-regions.json` lists cases both sides test against. `offset: null` means not staged.

### The marker

`omarchy/migration.json` on the ESP, at most 64 KiB, UTF-8 JSON with no duplicate keys:

```json
{
  "schema": "omarchy-migration/staging/1",
  "export_id": "1b4e28ba-2fa1-41d2-883f-0016d3cca427",
  "partition_uuid": "6a1f5c2e-0b7d-4d3e-9c1a-5e2f8b7d4c3a",
  "offset": 101938364416,
  "length": 5368709120,
  "sha256": "64 lowercase hex digits",
  "receipt": { "schema": "omarchy-migration/receipt/1", "...": "the export's receipt.json" }
}
```

UUIDs are lowercase. `receipt` is the export's receipt as an object, with the same `export_id`, `bundle.bytes == length` and `bundle.sha256 == sha256`. Readers refuse unknown keys.

## Claiming at first boot

`omarchy-migration-claim.service` runs once, when the marker exists. It is ordered after `local-fs.target` and `boot-efi.mount`, and before `omarchy-mac-first-boot.service` and `omarchy-provision-owner.service`, so it never waits on owner setup. It needs nothing from the owner. As root it:

1. Reads and checks the marker, and the receipt inside it.
2. Checks that `partition_uuid` is the partition under `/`, directly or through the LUKS mapping, and that the region fits the rules above for that partition's length.
3. Checks that `/` has `length + 4 GiB` free.
4. Copies the region into `/var/lib/omarchy-migration/incoming/<export_id>/bundle.age` through a `.partial` file, hashing it, and writes `receipt.json` beside it. The folder is root's, mode 0700. It fsyncs both and the folder.
5. Lists the export as waiting with an empty, world-readable `pending/<export_id>`, and records the claim in root's `claimed/<export_id>`.
6. Deletes the marker from the ESP.

The claim accepts exactly the offset the region rules give for the partition's length and the bundle's length. It finds the partition under `/` from the mount's source device: the device itself when it is a partition, or the one slave of a `CRYPT-LUKS` device-mapper device. `partition_uuid` must name that partition in `/dev/disk/by-partuuid`, with the length sysfs reports.

Any failure deletes the marker and records `/var/lib/omarchy-migration/claim-failed.json`, mode 0644, so the offer can say the files stayed on the Mac:

```json
{ "schema": "omarchy-migration/claim-failed/1", "export_id": "the marker's, or null", "reason": "digest_mismatch" }
```

Reason codes: `marker_unsafe`, `marker_oversized`, `marker_invalid`, `receipt_invalid`, `receipt_mismatch`, `root_device_unknown`, `root_device_unsupported`, `partition_mismatch`, `region_invalid`, `insufficient_space`, `region_unreadable`, `digest_mismatch`, `state_unsafe` and `claim_operation_failed`. A claim that already completed, including one already handed to its owner, only deletes the marker. An interrupted claim starts its copy again. The command exits 0 after recording a failure, and nothing requires the unit, so a failure never stops the boot.

## The offer and the import

`omarchy-migration-offer.service` is a user unit in `graphical-session.target`. When a claimed export waits and this user hasn't imported or dismissed it, it waits for notifications and sends one that opens `omarchy-migration-import` in a floating terminal. An export waits when root's `pending/<export_id>` lists it, or when it was handed to this user and its bundle is still in their incoming folder. The user's own records are in `~/.local/state/omarchy-migration/`: `imported/<export_id>.json`, `dismissed/<export_id>`, and `claim-failed-notified`. A failed claim is told once, as **Your Try Omarchy files stayed on the Mac**.

`omarchy-migration-import`, as the owner:

1. Takes the claimed export through `sudo /usr/lib/omarchy-migration/handoff`, which the package's sudo rule allows for `wheel` without a password and without arguments. The handoff moves the bundle and receipt into the user's `~/.local/share/omarchy-migration/incoming/<export_id>/`, owned by the user, mode 0700 and 0600. It writes the home only as that user, after dropping root, and never follows a link there. It clones the bundle when the filesystem can, so the move needs no second copy on Btrfs. It also gives the user a private work folder beside the home, `/home/.omarchy-migration/<uid>/`. The restorer publishes by hard link, so its job must share the home's filesystem, and it must sit outside the home. With `/home` on its own subvolume, only a folder under `/home` qualifies. The work folder holds the restore job, with the backups of replaced defaults, and the decrypted scratch, which `/tmp` in memory couldn't hold.
2. Asks for the transfer passphrase, and asks again after a wrong one.
3. Runs `omarchy-migration plan` with `--defaults /etc/skel`, and shows the summary by category: what comes across, what replaces an untouched Omarchy default, and what stays because the native file differs.
4. Runs `omarchy-migration apply` for exactly that plan after the owner confirms.
5. Records the import in `~/.local/state/omarchy-migration/imported/`, and deletes the incoming folder and the scratch. The job stays, with its backups.
6. Applies the Try home's theme with `omarchy-theme-set` and its background with `omarchy-theme-bg-set`, when the theme isn't already the current one. Omarchy keeps the current theme in `~/.local/state/omarchy/current`: its name, a folder it generates from the theme, and a background link that names the home's own path. Copies of those would be stale, so the import leaves that folder to Omarchy (`regenerated_by_omarchy`) and the summary carries the choice instead. A theme Omarchy can't set is named, and the import still finishes.
7. Asks whether to reboot now, and reboots with `omarchy-system reboot` on yes.

`--defaults DIR` lets a plan replace a regular file whose destination is byte-identical to the file at the same relative path under `DIR`. Each replacement keeps a private backup, as approved replacements do ([REPLACEMENT](../packages/omarchy-migration/docs/REPLACEMENT.md)). Any other existing file stays a conflict. The plan counts these replacements as `replace`, its `plan_id` binds every default it compared against, and `apply` refuses with `plan_changed` when a destination changed since the review ([REVIEW](../packages/omarchy-migration/docs/REVIEW.md)).

## Which images can claim

An image that claims exports declares it in its `installer_data.json`: the Omarchy target's `native_import` names the marker schema it reads, `omarchy-migration/staging/1`. The app reads it from the release's metadata when it prepares the plan (`TryExportImageSupport`), and stages only for an image that declares it.

## Packaging

The installer's image builder stages `omarchy-migration` from `packages/omarchy-migration` into the native image with its `install` script, with the pinned `age` it needs, so the claim and the import work offline. The units come enabled. It is staged like owner provisioning's Node.js, not installed through pacman: the image's transactions read only the pinned databases and the signed candidate set, and this package comes from the builder's own commit, which `PROVENANCE` records. `/usr/lib/omarchy-migration/version` names the version staged. pacman doesn't track these files, so an update reaches them only through a new image; the import runs once, on the first login. `packages/omarchy-migration/PKGBUILD` builds the same files as a pacman package, for a native system installed before images carried it. Try Omarchy vendors the same package as its exporter.

## Testing

- `packages/omarchy-migration/test/all` tests the claim, the handoff, the offer and `--defaults` on Linux, and CI runs `test/claim-integration.sh` as root: a staged export on a loop disk, through the first boot's LUKS2 conversion and Btrfs growth, claimed and imported.
- The Swift tests cover the region, the Root locator, the marker and the writer against fakes. `TryExportStagingDiskImageTests` stages through diskutil and the raw device on an attached disk image (`Development/try-export-staging-disk.sh`).
- `Development/try-export-staging-chain.sh AGE` runs the whole path on one disk image: diskutil's layout, a Btrfs root and a real export from Linux, the helper's staging on macOS, then the first boot's encryption, the claim and the import in Linux.
- A real install on a Mac is not automated.

## Not covered

- An install that fails after staging leaves ciphertext in the free space of a partition the next install replaces.
- Exports stay in the Mac's Exports folder after a successful import. Removing them is the person's choice.
- Credential stores, browser profiles and packages don't move, as the export's policy says.
