# EFI encryption handoff

The installer records the encryption checkbox and selected release lane in `omarchy/install.conf` on the Omarchy EFI partition after the engine finishes the macOS stage. Without that file, first boot defaults to encryption enabled. Failure to save the file produces “The encryption choice wasn’t saved, so Omarchy will encrypt its disk at first boot.”

## Failure observed on the M3 Pro

Installer 2.3.11, whose source is tagged at `c5979c1`, completed the macOS stage on an M3 Pro (`apple,j514s`) running macOS 27.0 (26A428) on 2026-11-01. The execution journal ended with `outcome=awaiting_recovery` at 05:50:04 local time. At 05:50:06, the subsequent EFI unmount failed with status `0x10` (`EBUSY`) and Disk Management dissenter code `49168`. The EFI volume remained mounted and its `omarchy` directory contained no `install.conf`. A later read-only `lsof` check found Spotlight's `mds` and `mds_stores` holding files on that volume. This identifies a busy-volume failure; the later process snapshot makes Spotlight the likely blocker, rather than proving which process held each vnode at the exact failure time.

The same code remains on `main` at `982094e`. `DiskutilInstallConfESPOperator.mount` first tries to unmount every already-mounted ESP so it can remount at a private path. That normal unmount throws before `InstallConfESPMountWriter` reaches the atomic write. The helper maps this pre-write failure to `.notRecorded`, which the app renders as the warning above. The existing test `testBusyExistingMountIsNeverForcedOrWrittenThrough` explicitly required this outcome.

![Installer 2.3.11 showing the encryption-choice warning on the M3 Pro](images/efi-encryption-warning-2026-11-01.png)

Commit `df9de0e` introduced the pre-write unmount to fix a different failure: macOS can report a successful mount command while leaving the volume at its existing mount path. Writing blindly to the requested private directory can then appear successful without reaching EFI. Removing device or mount verification would reintroduce that bug.

## Corrected behavior

The helper locates the EFI partition inside the completed installation plan's exact store and extent. If it is already mounted and writable, the helper opens that mount root and verifies the opened filesystem's source device, mount root and read-only flag, together with the named directory's device and inode. A newly mounted volume must still appear at the requested private path and pass the same checks.

Mount-root verification canonicalizes the path with `realpath(3)` before comparing it with the kernel's mount path. Foundation's URL normalization can remove `/private`, while the kernel reports `/private/var/...` for a mount requested under `/var/...`; comparing those different forms rejects a valid private mount. The FAT-image integration tests cover both an existing mount and repeated fresh private mounts with the production verifier, without replacing it with a test double. A third test runs the production writer on FAT32, replaces `encrypt=1` with `encrypt=0`, then detaches and reattaches the image before checking the exact document and absence of temporary files. Only the plan's partition metadata is stubbed for that writer test.

The helper keeps the verified root descriptor open while creating and opening the `omarchy` directory. Descriptor-relative operations reject symlinks and traversal onto another filesystem. A unique temporary file is written and synchronized before an atomic rename. After the rename, the helper attempts `fsync` and `F_FULLFSYNC` on the `omarchy` directory, and on its parent if it created `omarchy`, before a bounded regular-file readback checks the exact document. These directory syncs are best-effort because filesystem support varies; the saved-but-unconfirmed result still applies when the later unmount fails. Mount-path replacement cannot redirect these operations into another directory.

Every file and directory descriptor closes before the normal unmount attempt. If EFI is still busy, the helper reports the existing `.unconfirmed(encrypt:)` outcome after saving the document. The saved opt-out message therefore remains available for `encrypt=0`. Mount-directory cleanup uses only `rmdir`; it never recursively deletes a mounted filesystem's contents. No force unmount is used.

## Busy unmount after saving on the M3 Pro

Installer 2.3.13 (build 122) completed the macOS stage on the same M3 Pro on 2026-11-02, still on macOS 27.0 (26A428). The engine recorded `outcome=awaiting_recovery` at 15:47:29. At 15:47:30 the helper saved `omarchy/install.conf` (`encrypt=1`, `lane=rc`) into the existing `/Volumes/EFI - OMARC` mount, read it back, then tried the normal unmount twice. Both attempts failed with status `0x10` (`EBUSY`) and dissenter code `49168`, so the app showed “Encryption was recorded, but the installer could not confirm the disk was unmounted.”

Spotlight was not holding the volume through its index this time. In the unified log its server, `mds`, approved both unmounts, closed the store (“Final Store Closed on Volume”) and finished its approval callback before the kernel unmount ran. It then logged “Unmount canceled. All stores already closed. Schedule disk … remount” and reopened the index. Ten minutes later a manual `diskutil unmount disk0s4` failed the same way. `lsof -f` and `fuser -c` then named only `mds` and `mds_stores`, and `mds` kept two read-only descriptors on the volume root through every attempt. Disabling indexing with `mdutil -i off` did not release them.

Restarting the server with `launchctl kickstart -k system/com.apple.metadata.mds` did: the next normal unmount succeeded at once. The fresh server then released the volume normally in five further mount and unmount cycles, with indexing on and off. The old server's records explain why it did not: during the engine run, at 15:46:44, it handled an unmount for another device, created disk record `0x75531b00f0` for it, and later reused that record for the new EFI mount. Apple's source is not available, so this is the most likely mechanism rather than a proven one.

A disposable FAT32 image on the same Mac did not reproduce the stuck server. Spotlight indexed the image, yet normal unmounts succeeded after the helper's exact write sequence and after each variant without the rename, directory syncs or readback. The write itself does not keep the volume busy.

## Spotlight restart before the second unmount

`DiskutilInstallConfESPOperator.unmount` keeps the normal `diskutil unmount`. When it is refused, the helper restarts Spotlight's server once and repeats the same normal unmount. launchd starts the server again immediately and its indexes are kept, so the cost is a brief Spotlight restart on a path that previously ended with the warning. A volume that is still busy afterwards keeps the saved-but-unconfirmed result. Nothing is forced.

## Validation boundary

The busy-mount regression must fail on the unmodified source and pass with this fix. Focused Swift coverage also checks wrong mounts, read-only mounts, atomic replacement, post-write unmount failure for both encryption choices, temporary-file cleanup, symlink rejection, pinned-directory behavior after path replacement and descriptor closure before unmount.

These checks exercise temporary directories, disposable FAT32 disk images and mocked disk operations. They do not qualify a new installation, change an installed EFI partition, register a helper, sign an app or publish a release. A physical installation remains a separate check against a packaged candidate.

Local checks on 2026-11-01 used the M5 with macOS 26.6.2 (25G83), Xcode 27.2 (27B52), Swift 6.4.2 and checksum-pinned XcodeBuildMCP 2.7.0. The original busy-mount regression failed on unmodified `982094e`. Review then identified missing real-verifier coverage: two disposable FAT-image tests failed on the initial PR revision `eea78ae`, exposing the `/private` mismatch. The final FAT32 variants exercise the production verifier for existing mounts and repeated fresh private mounts without root privileges. A separate disposable FAT32 probe confirmed FSKit was in use and returned success from both `fsync` and `F_FULLFSYNC` on its directory descriptor.

The corrected source passed strict package-wide Swift formatting, all three real FAT32 integration tests, 811 complete debug tests and 797 release tests, with zero failures and one skip in each complete configuration. The tested `InstallConfESP.swift` SHA-256 is `1ae7561ec7d659906ef65bf871727243922ae8fc77f77b6f77218592f6cb2185`. The portable suite passed for the initial PR revision; these follow-ups change Swift code and its documentation. The earlier read-only probe on the M3 accepted its existing macOS 27 FSKit EFI mount and rejected another device and a subdirectory, without writing or unmounting anything. App assembly, signing and a new physical installation were not performed locally.
