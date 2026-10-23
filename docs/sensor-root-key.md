# Touch ID root-key extraction

The Neo sensor needs a complete device-local combined root-key container. A bare `IMG4` containing an `MSRk` payload is not sufficient. Keep the outer `comb`, its `fdrd` image and manifest, and its `secb` trust bundle byte-for-byte. Never synthesize a manifest or combine records from different machines.

The draft `root_key` parser checks the envelope and preserves the supplied bytes. `collect(..., root_key_record=...)` can include that complete record as `apple/msrk-j700.bin` alongside calibration. These checks are structural only; they do not establish a valid certificate chain or sensor binding.

## Source

macOS keeps the records in `/System/Volumes/Hardware/FactoryData/System/Library/Caches/com.apple.factorydata`, on the Hardware volume of the Mac's own iBoot System Container, readable without root. `factory_root_key()` rebuilds the container from three of them:

- `MSRk-<sensor>`: an `IMG4` whose `IM4P` carries the key. Its own factory manifest chains to a root the enclave does not trust, so only the `IM4P` is used.
- `seal-<ECID>`: the FDR sealing manifest (`IM4M`), which does chain to `secb`.
- `trustobject-<sha256>`: the trust bundle, used as `secb`. It is the named bundle whose bytes and digest match `trustobject-current`; older bundles that a macOS update leaves in the cache are ignored. Without `trustobject-current`, exactly one named bundle must be present and match its name.

The result is `comb { fdrd: IMG4 { IM4P(MSRk), [0] seal }, secb: trustobject }`. That's the layout macOS uses for the other records in the same cache (`appv`, `fCfg` carry the same seal and trust bundle), so no manifest is synthesized and nothing crosses machines. Exactly one `MSRk` and one `seal` record are required, and anything missing, ambiguous or mismatched is rejected.

On a Touch ID-equipped Neo (macOS 26.6.2, 25G83) this rebuilds a 12,688-byte container, byte-identical to the one captured from macOS that already brings the sensor up on Linux and enrols. `root_key()` accepts it.

## Calibration

The calibration that `collect()` carves from the iBoot System Container is either an FSCl `comb` record or a standalone signed FSC2 `IMG4`. The enclave loads only the combined layout: it refuses the plain FSC2 image (`LOAD_CALIBRATION` fails), and accepts the same `IM4P` under this Mac's `seal-<ECID>` with the trust bundle as `secb`, the same records the root key uses. `factory_calibration()` rebuilds it that way, and `collect()` writes the result as `apple/mesacal-j700.bin`, so the copy in `vendorfw` that Linux loads after every boot is the sealed one. An FSCl record already has this layout and is kept as it is. If the factory records are missing, collection of a standalone FSC2 calibration fails rather than installing a file the enclave refuses.

The calibration is not the device-private key, so its collection stays on by default.

Collection stays off in the adapter (`COLLECT_NEO_ROOT_KEY`). `collect()` adds the record to the vendor firmware package, so turning collection on would place a copy of the key in each of these, and every one must be dealt with first:

- `vendorfw/firmware.tar` and `vendorfw/firmware.cpio` on the ESP. FAT has no per-file permissions, so a mode inside the archive does not protect the file on disk. Either keep the record out of these archives under its own path, or make sure the ESP is readable only by root wherever it is mounted.
- The per-install Recovery firmware baseline (`Recovery/README.md`), which copies those archives into `Aurora/Recovery/InstallData/AuraPayloads` on the stub's macOS volume.
- The installed system, which must keep `apple/msrk-j700.bin` root-owned `0600`.

Turning collection on changes installer behaviour, so it belongs in its own change, together with the cold boot after a fresh install with the collected file, which is still to be done.

This work is separate from country-policy extraction and encryption recovery. It does not change either path or enable fingerprint setup by default.

The root-key record is device-private. Its required mode is root-owned `0600` in staging and on the installed system; containing private staging directories use `0700`. Automatic collection must remain inactive until every copy listed above is protected. General world-readable firmware defaults are unsuitable for this record.
