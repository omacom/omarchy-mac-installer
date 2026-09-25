# Apple image producer

This repository has one Apple Silicon image producer. This page records how the two producers that exist today become one, and the interface the port builds to. The decision comes from the Apple Silicon convergence plan (omacom/omarchy-mac#512, "Installer and images"). The port is a separate change; until it lands, `image-builder/` still holds the builder imported by #2, and no release path uses it.

## Decision

The base is mx-mac's image builder: `bin/build-mac-image`, `bin/mac-image-inputs` and `bin/mac-image-check` from maralcbr/omarchy-pkgs `asahi-quattro` at `7e2f6cfe`, with their tests. It keeps #2's authenticated candidate importer and its installed-system and ownership checks. The omarchy-iso-derived builder that #2 imported into `image-builder/` (from `dbc46807`) is removed by the port.

Why this base:

- It already builds the payload the 2.0.10 app and engine install: fixed UUIDs, ESP volume id and sizes, the zip members and `installer_data.json` that `_validate_full_os_package` checks. Since 2026-09-22 it has built mx-mac's published fresh-install payloads, including the current rc image. Those images ship Limine behind m1n1 and U-Boot with the Aurora kernel, and they pass mx-mac's image VM acceptance.
- It is small: three scripts of about 2,700 lines and 1,700 lines of tests. The imported builder is about 270 files and 70,000 lines. Most of that is ISO, archinstall, checkpoint and stage machinery for linux-asahi and GRUB products the plan retires.
- One inputs record pins every moving input by sha256, so each image traces back to the bytes it was built from.

Why keep #2's importer: `build-mac-image` today trusts the fork. It installs a runtime release of `install-asahi-quattro` from maralcbr/omarchy-mx-mac and a `CANDIDATE` descriptor signed with the fork's repository key. The plan replaces that with signed candidate packages pinned to one `quattro-upstream` commit. `builder/quattro-candidate.py` already verifies such a set without trusting keys that arrive with it:

- a pinned signer policy: primary and signing-subkey fingerprints, plus the digest of the public key file
- a signed receipt, the manifest digest it names, and each archive's sha256 and detached signature
- each archive's `.PKGINFO` name, architecture, version and dependencies, and the source revision embedded in the package
- the exact package set, one source revision for the runtime packages, and single ownership of every boot payload file
- a read-only frozen copy that a failed import cannot leave half-written

## What comes from where

| Part | Source | In the ported producer |
| --- | --- | --- |
| Host/container split, verified ALARM root filesystem, isolated chroot, loop and mount cleanup | `build-mac-image` | kept |
| Payload contract: filesystems, UUIDs, subvolumes, sealed `@factory`, zip, `installer_data.json` | `build-mac-image` | kept unchanged, so the 2.0.10 app and engine take the image |
| First boot and owner provisioning armed, Node pin staged, presets, `mac-image-finalize`, Limine activated through the runtime's own leaf, `update-m1n1` | `build-mac-image` | kept |
| Inputs record (ALARM snapshot databases and root filesystem, asahi-alarm database) | `mac-image-inputs` | kept; fork runtime and `[omarchy]`/`[omarchy-aurora]` channel fields replaced by the candidate set |
| Payload, image, tree, provenance and descriptor checks | `mac-image-check` | kept and extended by the inspection below |
| Fork runtime installer: `fetch_verified_installer`, `run_installer`, `fetch_bundle_manifest` | `build-mac-image` | removed |
| Authenticated candidate importer and its tests | #2 `builder/quattro-candidate.py`, `test/unit/test_quattro_candidate.py` | kept; schema extended for the plan's set |
| Signer policy and public key | #2 `builder/quattro-trust/` | kept as the mechanism; the key becomes the test-lane key from the candidate set |
| Duplicate, overlap and replacement checks between the candidate set and the platform | #2 `builder/quattro-dependencies.py` | kept as a library for the transaction check |
| Real pacman transaction in a disposable root before image assembly | #2 `builder/quattro-package-install-check.sh` | kept as a fast pre-check |
| Installed-system checks: pacman sections, required packages, enabled units, Limine loader, UKI sections, menu and root UUID | #2 `builder/verify-asahi-installed-system.py` | kept; GRUB and linux-asahi branches dropped |
| Volume icon | #2 `builder/branding/omarchy-volume.icns` | kept; same bytes as `build-mac-image` pins (`cf26ed5d…`) |
| ISO media, archiso gitlink, archinstall configurator, VM tooling, asahi stages, checkpoints, leases, orchestrator, products, branding manifests, publication and upload commands | #2 `image-builder/` | removed |
| `release-mac-image.yml` publication workflow | omarchy-pkgs | not ported; building stays an owner-run step until release qualification |

The private M3 pilot's image recipe (the `omarchy-mx-mac-limine-private` product and `private-limine-qualification.py`) is not ported. The plan scopes M3 out, and that recipe stays reproducible from #2's head, `5274846`. The app side of the pilot stays: the `InstallerBuildProfile` private profiles, `Packaging/private-test/` and their tests.

## Interface

```
image-builder/bin/build-mac-image --inputs FILE --candidates DIR [--cache DIR] [--dry-run] OUT
```

- `--inputs` is the record `mac-image-inputs resolve` writes. It pins the ALARM snapshot databases and root filesystem, the asahi-alarm database, the package and mirror hosts, and the candidate set: its receipt sha256 and its `quattro-upstream` source commit. The hosts move out of script constants into this record. That also retires the `image-builder/` package-host exemption in `test/shell.d/apple-installer-identity-test.sh`.
- `--candidates` is the signed candidate set directory: `signing.json` and its signature, `manifest.json`, `omarchy-base.packages`, `omarchy-apple.packages`, and each archive with its `.sig`. The importer checks it against the inputs record's receipt and source commit before any network access or chroot.
- The candidate set replaces the lane argument. The kernel, m1n1, U-Boot and Limine hook come from the set, not from a channel.
- Trust comes only from files in this repository. No flag or environment variable supplies a key or a policy.
- `OUT` receives the zip, `installer_data.json`, `PROVENANCE` and `IMAGE`, as today.

Candidate set, as the importer will accept it:

- **Runtime group** (source revision = the pinned `quattro-upstream` commit): `omarchy`, `omarchy-settings`, `omarchy-mac`, `omarchy-mac-boot`
- **Boot group** (source revision = the pinned omarchy-pkgs recipe commit): `linux-aurora`, `linux-aurora-headers`, `m1n1-aurora`, `uboot-asahi`, `limine-mkinitcpio-hook`, `limine-snapper-sync`
- **Minimum versions:** the manifest records one for `omarchy-mac-boot` and one for `limine-mkinitcpio-hook`, the versions the Limine enablement needs. The importer refuses a set below either.
- **Refused outright:** `omarchy-apple-boot`, `omarchy-first-boot`, `linux-asahi` and the Asahi `m1n1`.

Build steps inside the container:

1. Import and freeze the candidate set with the importer.
2. Publish the frozen archives as a local file repository, `[omarchy-candidates]`, ordered first. Install every candidate by its repository-qualified name (`omarchy-candidates/uboot-asahi`, `omarchy-candidates/m1n1-aurora`, …): on `quattro-upstream`, `[asahi-alarm]` is ordered before `[omarchy]`, so a plain `uboot-asahi` or `m1n1` request resolves to asahi-alarm.
3. Resolve the whole transaction in a disposable root first. Every candidate name must come from `[omarchy-candidates]`, and no file may have two owners.
4. Assemble the image with the `build-mac-image` flow. `run_installer` becomes the candidate's packaged installer, run in the chroot with the owner deferred. The platform stays declared by `OMARCHY_MAC_TARGET` for now; the root-owned image-target manifest replaces it later.
5. Inspect, then package. The build fails on a missing or mismatched boot component:
   - the Limine UKI embeds an initramfs, and that initramfs carries `omarchy-mac-boot`'s encryption hook
   - the ESP's m1n1 stage 1 and `m1n1/boot.bin` come from the candidate `m1n1-aurora`, with the Aurora DTBs of every supported model and the candidate U-Boot
   - `EFI/BOOT/BOOTAA64.EFI` is the candidate Limine
   - installed versions equal the manifest's and meet the minimums
6. `PROVENANCE` and `IMAGE` record the receipt and manifest sha256, the source commit, the signer fingerprint and each archive's sha256. They drop the `installer=install-asahi-quattro` and runtime release lines.

**Reproducible** means: the same inputs record and candidate set give the same installed package set (name, version and archive sha256) and the same `IMAGE` input lines. Any changed input changes them. Byte-identical filesystem images are not required.

## Tests

- `test/mac-image` moves over with the builder and gains importer and inspection cases: a tampered archive, a wrong signer, a missing or extra package, a version below a minimum, a boot file with two owners, and an ESP whose m1n1, DTB, U-Boot or Limine differs from the candidate.
- #2's importer unit tests run in the portable suite.
- The imported builder's own suite and its CI job go with it.
- None of these need Mac hardware. The M2 Max install of the first image is the separate hardware gate.
