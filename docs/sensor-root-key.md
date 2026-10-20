# Touch ID root-key extraction: draft qualification boundary

The Neo sensor needs a complete device-local combined root-key container. A bare `IMG4` containing an `MSRk` payload is not sufficient. Keep the outer `comb`, its `fdrd` image and manifest, and its `secb` trust bundle byte-for-byte. Never synthesize a manifest or combine records from different machines.

The draft `root_key` parser checks the envelope and preserves the supplied bytes. `collect(..., root_key_record=...)` can include that complete record as `apple/msrk-j700.bin` alongside calibration. These checks are structural only; they do not establish a valid certificate chain or sensor binding.

The production adapter deliberately does not supply this optional argument yet. Automatic collection remains unimplemented: identify the sealed record in the selected machine's protected factory store, verify that the store belongs to the selected internal disk, and reject missing, stale or ambiguous records before passing one to the collector. Do not replace this with a scan for arbitrary old records in raw disk data.

Before merging, compare the complete result against an accepted capture from the same Touch ID-equipped Neo, confirm sensor binding, and verify a sensor session after a cold boot and fresh installer run. Keep keys and captures private. The currently available Neo has no Touch ID, and the previously tested sensor-equipped machine and its capture are unavailable. Synthetic fixture acceptance is not hardware qualification.

This work is separate from country-policy extraction and encryption recovery. It does not change either path or enable fingerprint setup by default.

The root-key record is device-private. Its required mode is root-owned `0600` in staging and on the installed system; containing private staging directories use `0700`. Automatic collection must remain inactive until the archive writer and installation path preserve that mode. General world-readable firmware defaults are unsuitable for this record.
