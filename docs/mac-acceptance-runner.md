# Mac acceptance runner

`mac-acceptance.yml`'s VM job needs an aarch64 Linux host with `/dev/kvm`. GitHub's hosted arm64 runners and Namespace's standard arm64 runners (AmpereOne) have none. The job runs on Namespace's Apple-backed Linux ARM64 runner, `namespace-profile-arm64-linux-apple-12x32` (12 CPU, 32 GB, about 158 GB free): Linux on Apple Silicon, with nested KVM.

The runner is the workflow's `runner` input, a JSON `runs-on` value. Its default is `["namespace-profile-arm64-linux-apple-12x32"]`, in three places in `mac-acceptance.yml` (both triggers' input defaults and the guard's `DEFAULT_RUNNER`; a test keeps them equal).

## The container

The runner is Ubuntu; the harness was written for an Arch Linux ARM host. `scripts/mac-acceptance-container` runs it in a privileged Arch Linux ARM container (the image omarchy-iso builds its aarch64 ISO in, pinned by digest) with the harness's tools and Arch's QEMU 11, the runner's `/dev`, and the run directory at the same path. It also covers three ways the runner differs from that host:

- **Overlay root.** The runner's root file system is overlayfs, which the harness's own overlay mount cannot use as its upper layer. The run's state directory is an ext4 file system in a sparse file (`$ROOT/state.ext4`).
- **No udev.** Reloading udev rules, which only hides the run's devices from a desktop automounter, is a no-op.
- **No FAT driver in the kernel.** The VM job runs on Namespace's default kernel, which has none, so the harness reads and writes the ESP with mtools. (The image build opts into Namespace's bleeding-edge kernel, which has FAT; see `image-builder/README.md`.)

The harness gives the guest a GICv2m for its MSIs, not an ITS (`msi=gicv2m`): nested on Apple silicon the host's virtual GIC has no LPIs, so an emulated ITS's interrupts never reach the guest and its USB and virtio devices hang.

## What the job needs from the runner

- `/dev/kvm`, Docker, passwordless `sudo` for the runner's user, and `gh`, `git`, `python3`, `findmnt`, `losetup` and `dmsetup` on the host.
- 70 GiB free under `/var/tmp`. The job works in `/var/tmp/omarchy-mac-acceptance/<run>-<attempt>/` and removes it at the end, with the image, the run's disks, its mappers and its loop devices.

The harness's own tools come from the container.

## Limits

- **Main only.** The guard job runs on a GitHub-hosted runner and refuses pull requests, any ref but `refs/heads/main` and malformed inputs before the VM job is scheduled. The VM job `needs:` it. `mac-acceptance.yml` has only `workflow_call` and `workflow_dispatch` triggers, and dispatching needs write access.
- **No secrets.** The VM job's token can read this repository's contents, artifacts and attestations, nothing more. The job never receives an `id-token` permission. The evidence record is attested by a separate GitHub-hosted job.
- **Environment.** The VM job runs in the `acceptance` environment, which allows `main` alone and holds no secrets.

The image is checked against its sha256 and its `mac-image.yml` build attestation before the harness runs it. A VM pass never replaces the hardware validation on real Macs.
