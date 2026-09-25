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
- A set holds the runtime built from one omarchy-mac commit (`omarchy`, `omarchy-settings`, `omarchy-mac`), the boot packages, and may hold the rest of the Apple default set's closure that the Apple repository order takes from an Omarchy channel. Boot and closure packages may come from that channel (the set records each file's `pkgs.omarchy.org` URL), and so may `omarchy-mac-boot` (`channel_runtime_packages` in the policy), whose recorded source revision the import keeps. Every archive is still signed by the set's key.
- `resolve` verifies the set against the key pinned in `builder/candidate-trust/` and writes the inputs record: the set's receipt, manifest and source commit, the Omarchy channel's database (`--channel`, default edge), the asahi-alarm and Arch Linux ARM databases and the Arch Linux ARM root filesystem, each by sha256, with the servers they come from. `--cache` keeps the databases and root filesystem.
- `build-mac-image` imports the set again (exclusively, read-only), then builds in a privileged Arch Linux ARM container. It writes the zip, `installer_data.json`, `INSPECTION`, `PROVENANCE`, `IMAGE`, the inputs record and the build logs to `out/`. With `--cache`, the package archives it downloads stay in `cache/pkg`, so a later build from the same record installs the same bytes even after the servers move on.

A build takes about 15 minutes and 25 GB of disk. On a host with a desktop session, an automounter (udiskie, gvfs) would mount the filesystems the build puts on loop devices, and ask for a password to do it: run the build as root or with passwordless sudo, and it hides every loop device from udisks with a transient udev rule while it runs. Without either, it refuses to run beside an automounter. devmon and udevil mount without udisks, so the build always refuses to run beside them.

## What a build does

1. Installs a base system and `omarchy-settings` first, so its pacman platform guard is resident, then writes the root-owned image-target manifest (`/var/lib/omarchy/image/target`: `format=1`, `platform=apple-silicon`, read by deferred hardware setup and the platform guard), then installs the runtime with `omarchy-base.packages` and `omarchy-aarch64.packages` (as `omarchy-pkg-defaults apple-silicon` composes them) and, last, the Apple set: `omarchy-apple.packages`, the Aurora kernel, m1n1, U-Boot, the Limine hook, and the speaker stack's model profiles and DSP chain (`alsa-ucm-conf-asahi`, `asahi-audio`), which the runtime's audio step would otherwise fetch on a first boot that may have no network. Set packages are installed by their `omarchy-candidates/` name, so no other repository can supply them. Base names the pinned repositories lack are recorded as `unavailable=` in `PROVENANCE`.
2. Runs the runtime's own `omarchy-apply-system --defer-provisioning --first-install` in an isolated chroot. The chroot never sees the build host's hardware: its device tree names the image's platform, it has UEFI (as U-Boot provides) without EFI variables, and no PCI, USB, input or DMI devices, so hardware setup installs the same packages on any aarch64 host whose kernel runs with a device tree (an Apple Silicon Mac, most arm64 boards; on an ACPI-only host the image's platform checks find no device tree and the build stops at the Limine activation). A runtime with deferred hardware setup (omacom/omarchy-mac#528) queues its hardware steps from the manifest; an older one runs them here for the image's platform.
3. Sets the image's GRUB defaults, from which the Limine command line is derived (`quiet splash`, and `plymouth.ignore-serial-consoles` as on mx-mac, since the Mac's device tree registers a serial console), and Plymouth's `omarchy` theme, which omarchy-settings leaves to Arch Linux ARM's `bgrt` on Apple Silicon. Applies the Apple presets (`80-omarchy-mac*.preset`, including omarchy-mac's audio preset), stages owner provisioning with the pinned Node.js, rebuilds the initramfs and GRUB, runs `update-m1n1` with `LC_ALL=C` so the device trees go into m1n1's stage 2 in one order, activates Limine with the runtime's GRUB compatibility and Limine leaves, and arms first boot with the boot package's own `arm` command.
4. Copies the root into a fresh image, keeping snapper's `/.snapshots` a btrfs subvolume (a file copy would flatten it into a plain directory and snapper would fail on the Mac), seals a snapshot of it into `@factory`, then inspects the images against the set's archives and packages them.

## Inspection

`INSPECTION` (JSON) records each check. A missing or mismatched boot component fails the build:

- every set package installed at the set's version, `omarchy-mac-boot` and `limine-mkinitcpio-hook` at or above the minimums in `builder/candidate-trust/policy.json`, no refused package (`linux-asahi`, `m1n1`, `omarchy-apple-boot`, `omarchy-first-boot`)
- m1n1's stage 2 on the ESP is the set's m1n1, then every device tree of the set's kernel in C order, then the set's U-Boot, then exactly the options the image's `/etc/m1n1.conf` sets; every Mac the set's U-Boot supports has an Aurora device tree there
- every file and link of every runtime and boot package of the set is in the image with the set's bytes and modes (m1n1, U-Boot, the kernel and device trees, the boot package's hooks and scripts, the Limine gate, the runtime), and Limine's loader is the bytes its pinned package installed; the closure's packages are held to the set's versions and archives, since the runtime may restyle their files
- Limine at `EFI/BOOT/BOOTAA64.EFI` is the installed `limine` package's, its menu boots `omarchy_linux-aurora.efi` (with a matching BLAKE2 hash when the menu carries one), and the UKI's kernel is the set's, its release, os-release and command line match the image
- the initramfs the UKI embeds carries the boot package's encryption and vendor firmware units and their activation links
- the unlock screen is Plymouth's `omarchy` theme, in the image and in the UKI's initramfs, and the UKI's command line has `quiet splash plymouth.ignore-serial-consoles`
- the pacman hooks that rebuild the UKI and redeploy Limine come from their packages, the Apple gate included
- the image-target manifest, first boot, owner provisioning, the Limine gate and the fresh-image `deferred-steps` contract
- `/.snapshots` is an empty btrfs subvolume under snapper's root configuration, or absent with a deferred hardware step queued to create it; a plain directory fails
- the installed pacman configuration is the runtime's Apple Silicon template for the channel (`default/pacman/apple-silicon`, or `aarch64` on an older runtime) with the aarch64 mirror list, as the runtime stages it on a Mac, plus the test image's pin below, and the installed-system checks (`builder/verify_installed_system.py`) pass, `alsa-ucm-conf-asahi` and `asahi-audio` included; Bluetooth counts as enabled when its hardware step is queued for first boot
- `@factory` is sealed for the set without fresh-image or owner state

`bin/mac-image-check` also holds the payload to the installer engine's contract and checks `PROVENANCE` and `IMAGE` against the bytes beside them.

## Test images keep the candidate runtime

Every set the importer takes is a test candidate set (`candidate_only`), and its runtime is versioned `4.0.0.alpha.quattro…`, which sorts below the channel's `omarchy` (4.0.2 on edge). A `pacman -Syu` on the installed Mac would replace it with the channel's. So a test image's `/etc/pacman.conf` starts its `[options]` with a marked block, `IgnorePkg = omarchy omarchy-mac omarchy-settings`: the packages built from the set's source commit (`builder/test_image_pin.py`, recorded as `test_image_pin` in `IMAGE` and `PROVENANCE`). Everything else, the boot package and kernel included, follows the channel. Deleting the three marked lines makes the Mac follow the channel. On this runtime `omarchy-refresh-pacman` and `omarchy-channel-set` leave an Apple Silicon Mac's pacman.conf alone (no aarch64 channel is qualified yet); once they rewrite it from the template, the pin goes with it. The runtime's templates and every other default are unchanged. A local candidate repository ordered first was not used: the image would have to trust the candidate test key, or install it unsigned.

## First boot acceptance

Inspection is not boot qualification. After the owner's first login on the Mac, as root:

- `btrfs subvolume show /.snapshots` succeeds, and `snapper list` and `systemctl start snapper-cleanup.service` finish without error
- `plymouth-set-default-theme` prints `omarchy`, and `/proc/cmdline` has `splash plymouth.ignore-serial-consoles`
- `pacman -Q alsa-ucm-conf-asahi asahi-audio` lists both, and in the owner's session `wpctl status` shows the model's `audio_effect.<model>-convolver` (on the M2 Max, `j416-convolver`) as the default sink, not a `stereo-fallback` one

## Reproducibility

`IMAGE` records the inputs (the builder commit and whether its tree was clean, the set's receipt, manifest and source commit, and the inputs record's digest) and `package_set_sha256`, the sha256 of every installed package's name, version and archive sha256. Two builds from the same builder commit, inputs record and candidate set give the same `package_set_sha256` and the same input lines; any changed input changes them. Filesystem images are not byte-identical between builds, and the default UKI's initramfs is autodetected as mkinitcpio does, so its module list follows the build host's devices; the Mac's boot modules come from the asahi hook either way.

## Checks

`bash image-builder/test/all` runs the source checks: the importer against signed fixture sets (tampering, a foreign key, a key that travels with the set, missing and refused packages, closure and channel packages and their sources, versions below a minimum, a file with two owners, a runtime package from another commit), the inspection against fixture images (every boot component missing or mismatched, a set file rewritten, a wrong `@factory`, a `bgrt` or missing splash, a flattened `/.snapshots`, a missing speaker stack, a missing or different test image pin), the inputs record and the builder's own decisions. They need Bash 5, Python 3.11 or newer, GnuPG, jq and bsdtar, and no container, network or root.

## Sources

`bin/` is mx-mac's image builder from maralcbr/omarchy-pkgs `asahi-quattro` at `7e2f6cfe` (`bin/build-mac-image`, `bin/mac-image-inputs`, `bin/mac-image-check`, and `mac-image-finalize` as `builder/finalize-root`), adapted to the candidate set. `builder/candidate_set.py`, `builder/apple_limine.py` and `builder/verify_installed_system.py` come from the candidate importer, Limine contract and installed-system verifier of this repository's #2.
