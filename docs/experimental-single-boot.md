# Experimental single-boot

Single-boot is off unless the person chooses **Experimental single-boot** and ticks the warning. Alongside macOS stays the default.

The choice deletes the macOS APFS container. It does that only after the recovery account exists and APFS reports it as the Omarchy volume owner, which is the Startup Disk authorization. Until then the installer still shrinks macOS only far enough to create the Omarchy stub, EFI, boot, and root. The iBoot system container (`Apple_APFS_ISC`) and System Recovery (`Apple_APFS_Recovery`) are never deletion targets. If either is missing or is no longer at the ends of the occupied map, deletion stops. Empty space the partition reader inserts, including a gap before iBoot, is not another container, and partition indexes do not have to be contiguous.

A signed catalog can still pin an older engine that has no single-boot candidate. Choosing single-boot then plans and runs the engine pinned into this app. The catalog still supplies the OS image and its metadata. Dual-boot keeps using the catalog engine.

System Recovery can reinstall macOS. A DFU restore from another Mac remains the path if those containers are damaged. Keep a backup. This path has had limited hardware testing.

After the container is gone, free space sits in front of the Omarchy partitions. The macOS installer writes `omarchy/experimental/reclaim-root.sh` and `reclaim-plan.json` on the EFI system partition and sets `macosDeleted` only after the deletion. The script is not run by the installer. Omarchy first boot, in the omarchy-mac image, should run that script when the plan says macOS has been deleted. A person can also run it from Omarchy. It is safe to run again.

The script creates a partition in the freed space, opens LUKS on it when the root is encrypted, adds that device to the mounted btrfs root, and removes the old device so btrfs moves the data. It then rewrites `crypttab`, `fstab`, and the boot command line where they name the old root device, and records the boot partition UUID. It does not move the EFI or boot partition. Relocating those belongs in omarchy-mac if a later image needs it.
