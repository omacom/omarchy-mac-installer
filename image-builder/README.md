# Apple Silicon image producer

Builds the full-OS payload the Omarchy Mac installer installs, from a signed candidate set, and inspects it before packaging. [One image producer](../docs/image-producer.md) records why this producer and where its parts come from.

## Build an image

An aarch64 Linux host with Docker, loop devices and passwordless sudo for Docker, plus `curl`, `gpg`, `python3` (3.11 or newer), `bsdtar`, `jq` and `gh`:

```bash
image-builder/bin/mac-image-inputs candidates MANIFEST set/
image-builder/bin/mac-image-inputs resolve inputs --candidates set/ --cache cache/
image-builder/bin/build-mac-image --inputs inputs --candidates set/ --cache cache/ out/
```

- `candidates` downloads the release a candidate `manifest.json` names (the set's packages, their signatures and `signing.json`). Nothing in it is trusted yet.
- `resolve` verifies the set against the key pinned in `builder/candidate-trust/` and writes the inputs record: the set's receipt, manifest and source commit, the Omarchy channel's database (`--channel`, default edge), the asahi-alarm and Arch Linux ARM databases and the Arch Linux ARM root filesystem, each by sha256, with the servers they come from. `--cache` keeps the databases and root filesystem.
- `build-mac-image` imports the set again (exclusively, read-only), then builds in a privileged Arch Linux ARM container. It writes the zip, `installer_data.json`, `INSPECTION`, `PROVENANCE`, `IMAGE`, the inputs record and the build logs to `out/`. With `--cache`, the package archives it downloads stay in `cache/pkg`, so a later build from the same record installs the same bytes even after the servers move on.

A build takes about 15 minutes and 25 GB of disk. On a host with a desktop session, an automounter (udiskie, gvfs) would mount the filesystems the build puts on loop devices, and ask for a password to do it: run the build as root or with passwordless sudo, and it hides every loop device from udisks with a transient udev rule while it runs. Without either, it refuses to run beside an automounter.

## What a build does

1. Installs a base system and `omarchy-settings` first, so its pacman platform guard is resident, then writes the root-owned image-target manifest (`/var/lib/omarchy/image/target`: `format=1`, `platform=apple-silicon`, read by deferred hardware setup and the platform guard), then installs the runtime with `omarchy-base.packages` and, last, the Apple set: `omarchy-apple.packages`, the Aurora kernel, m1n1, U-Boot and the Limine hook. Set packages are installed by their `omarchy-candidates/` name, so no other repository can supply them. Base names the pinned repositories lack are recorded as `unavailable=` in `PROVENANCE`.
2. Runs the runtime's own `omarchy-apply-system --defer-provisioning --first-install` in an isolated chroot. The chroot never sees the build host's hardware: its device tree names the image's platform, it has UEFI (as U-Boot provides) without EFI variables, and no PCI, USB, input or DMI devices, so hardware setup installs the same packages on any aarch64 host whose kernel runs with a device tree (an Apple Silicon Mac, most arm64 boards; on an ACPI-only host the image's platform checks find no device tree and the build stops at the Limine activation). A runtime with deferred hardware setup (omacom/omarchy-mac#528) queues its hardware steps from the manifest; an older one runs them here for the image's platform.
3. Applies the Apple presets (`80-omarchy-mac*.preset`, including omarchy-mac's audio preset), stages owner provisioning with the pinned Node.js, rebuilds the initramfs and GRUB, runs `update-m1n1` with `LC_ALL=C` so the device trees go into m1n1's stage 2 in one order, activates Limine with the runtime's GRUB compatibility and Limine leaves, and arms first boot with the boot package's own `arm` command.
4. Seals a copy of the root into `@factory`, then inspects the images against the set's archives and packages them.

## Inspection

`INSPECTION` (JSON) records each check. A missing or mismatched boot component fails the build:

- every set package installed at the set's version, `omarchy-mac-boot` and `limine-mkinitcpio-hook` at or above the minimums in `builder/candidate-trust/policy.json`, no refused package (`linux-asahi`, `m1n1`, `omarchy-apple-boot`, `omarchy-first-boot`)
- m1n1's stage 2 on the ESP is the set's m1n1, then every device tree of the set's kernel in C order, then the set's U-Boot, then exactly the options the image's `/etc/m1n1.conf` sets; every Mac the set's U-Boot supports has an Aurora device tree there
- every file and link of every set package is in the image with the set's bytes and modes (m1n1, U-Boot, the kernel and device trees, the boot package's hooks and scripts, the Limine gate, the runtime), and Limine's loader is the bytes its pinned package installed
- Limine at `EFI/BOOT/BOOTAA64.EFI` is the installed `limine` package's, its menu boots `omarchy_linux-aurora.efi` (with a matching BLAKE2 hash when the menu carries one), and the UKI's kernel is the set's, its release, os-release and command line match the image
- the initramfs the UKI embeds carries the boot package's encryption and vendor firmware units and their activation links
- the pacman hooks that rebuild the UKI and redeploy Limine come from their packages, the Apple gate included
- the image-target manifest, first boot, owner provisioning, the Limine gate and the fresh-image `deferred-steps` contract
- the installed pacman configuration is the runtime's aarch64 template for the channel, and the installed-system checks (`builder/verify_installed_system.py`) pass
- `@factory` is sealed for the set without fresh-image or owner state

`bin/mac-image-check` also holds the payload to the installer engine's contract and checks `PROVENANCE` and `IMAGE` against the bytes beside them.

## Reproducibility

`IMAGE` records the inputs (the builder commit and whether its tree was clean, the set's receipt, manifest and source commit, and the inputs record's digest) and `package_set_sha256`, the sha256 of every installed package's name, version and archive sha256. Two builds from the same builder commit, inputs record and candidate set give the same `package_set_sha256` and the same input lines; any changed input changes them. Filesystem images are not byte-identical between builds, and the default UKI's initramfs is autodetected as mkinitcpio does, so its module list follows the build host's devices; the Mac's boot modules come from the asahi hook either way.

## Checks

`bash image-builder/test/all` runs the source checks: the importer against signed fixture sets (tampering, a foreign key, a key that travels with the set, missing, extra and refused packages, versions below a minimum, a file with two owners, a runtime package from another commit), the inspection against fixture images (every boot component missing or mismatched, a set file rewritten, a wrong `@factory`), the inputs record and the builder's own decisions. They need Bash 5, Python 3.11 or newer, GnuPG, jq and bsdtar, and no container, network or root.

## Sources

`bin/` is mx-mac's image builder from maralcbr/omarchy-pkgs `asahi-quattro` at `7e2f6cfe` (`bin/build-mac-image`, `bin/mac-image-inputs`, `bin/mac-image-check`, and `mac-image-finalize` as `builder/finalize-root`), adapted to the candidate set. `builder/candidate_set.py`, `builder/apple_limine.py` and `builder/verify_installed_system.py` come from the candidate importer, Limine contract and installed-system verifier of this repository's #2.
