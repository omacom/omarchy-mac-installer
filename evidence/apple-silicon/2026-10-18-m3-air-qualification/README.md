# M3 MacBook Air qualification (J613, J615)

This record lets the catalog drop the "Untested" note for the 13-inch (`apple,j613`) and 15-inch (`apple,j615`) M3 MacBook Airs. Every other M3 Mac keeps it. Each Air is installed fresh from the public installer on the edge channel, on the OS firmware the catalog offers it.

**Status: pending.** No row below has been filled in from an install yet. This PR stays a draft until both are.

## What each install records

| | J613 (13-inch) | J615 (15-inch) |
| --- | --- | --- |
| Tester | Scott Jones | pending (being recruited) |
| Installer app | 2.2.0 | |
| Catalog | edge, sequence | |
| Engine | | |
| Image (payload sha256) | | |
| macOS and system firmware | | |
| Stub OS firmware | 14.8.3 | 14.8.3 |
| Aurora after update | 2026.10.15.19 or later | |
| GPU cores | | |

Serial numbers, ECIDs, MAC addresses and network names are left out.

## Checks

Each check is run on the installed system, first as installed and again after `omarchy update` to the stated Aurora release.

| Check | J613 | J615 |
| --- | --- | --- |
| Installer: inspection, plan and install finish without a manual step | | |
| First boot: Stage 1, m1n1, U-Boot and Limine reach the owner setup | | |
| Internal panel at native resolution, brightness keys | | |
| GPU: accelerated with `asahi.t8122_start=1` (GL/Vulkan readback); without it, the desktop runs and the GPU is refused cleanly | | |
| Keyboard, trackpad, backlight | | |
| Wi-Fi and Bluetooth | | |
| Speakers and headphone jack | | |
| Suspend (s2idle) and resume, lid close and open | | |
| Touch ID: enrol, match, survives reboot and suspend | | |
| Battery and charging reported | | |
| Reboot to macOS from the startup picker and back | | |
| `dmesg`: no new errors beyond the known list | | |

## Known gaps, not checked here

- The camera (omacom/linux-aurora#117) and external displays (#103, #114).
- The 26.6.2 stub (the J613 Stage 1 path in engine `.32` and later). The catalog does not offer it yet.
- With Aurora's default, the M3 GPU stays off on 14.x firmware (`drivers/gpu/drm/asahi/t8122_start.rs`): the image does not add `asahi.t8122_start=1`, and Aurora decides when that becomes the default.

Earlier J613 reports on other setups (not this record): omacom/linux-aurora#79 (Touch ID on 14.7 and 26.6.2 stubs) and #26 (GPU on 14.7 and 14.8.3 stubs).
