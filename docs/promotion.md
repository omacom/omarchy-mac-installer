# Optional ProMotion setup

The plan review offers an independent **Enable ProMotion** action when the built-in display has one unambiguous, usable ProMotion mode with the same logical dimensions, backing pixel dimensions and IO flags as its current mode. The preference is optional and does not participate in disk-plan approval, installer eligibility, helper registration or engine execution. The text explains that this changes macOS too and may affect battery life. Simulation performs no host display reads or writes.

The operation re-enumerates the display immediately before switching, uses Quartz's permanent display configuration transaction, then checks the selected mode and its ProMotion designation again. It does not change resolution, scaling, rotation or display origins. Mirrored displays are left to System Settings. Missing or ambiguous metadata uses the manual Settings path when high-refresh capability is known; no numeric refresh rate alone qualifies as ProMotion. A failed operation is reported as unconfirmed rather than retried or treated as an installation failure. The preference remains selected if the user subsequently cancels installation; System Settings is the way to change it back.

## Private API boundary

Quartz exposes modes and nominal refresh rates, but no documented ProMotion flag. `ProMotionMetadata` dynamically resolves the read-only SkyLight symbols `SLSGetNumberOfDisplayModes`, `SLSGetDisplayModeDescriptionOfLength` and `SLSIsDisplayModeProMotion`. The flag takes a mode-table index, not an IODisplayModeID. The known 212-byte description is used only to correlate the index with a publicly enumerated mode; logical dimensions, pixel dimensions and IO flags must match before the marker is trusted. Missing symbols, failed reads, inconsistent descriptions or contradictory markers discard the metadata. There are no private mode-setting calls or new privileges.

ABI reference: [AllRez AppleMisc.h at 433f3513](https://github.com/joevt/AllRez/blob/433f3513225199f44e0e6bc90294b2be8df6b9a8/AllRez/AppleMisc.h). Public transaction documentation: [CGConfigureDisplayWithDisplayMode](https://developer.apple.com/documentation/coregraphics/cgconfiguredisplaywithdisplaymode(_:_:_:_:)) and [CGCompleteDisplayConfiguration](https://developer.apple.com/documentation/coregraphics/cgcompletedisplayconfiguration(_:_:)). These private query signatures and structure layouts are not an Apple compatibility guarantee. A maintainer should explicitly accept that dependency before merging, or retain only the manual Settings action.

## Motivation and evidence

On a 16-inch M3 Max (J516C), Omarchy 4.5.0-3 / linux-aurora 7.2.11.aurora14-1 had the PR #182 timing fix verified active, but a direct atomic KMS test measured 60.020902 FPS. The owner found macOS set to 60 Hz, manually selected ProMotion and booted back into the normal Linux kernel. The same test then measured 120.184533 FPS. Before the change, submission-to-display-firmware-completion was about 15.18 ms, with completions spaced 16.67 ms apart. Desktop pacing remained around 60 FPS with its normal settings, so this is not a general desktop performance fix or a battery-life claim.

This supports a retained display-state hypothesis, not a proven firmware mechanism. Related report on a different board: [linux-aurora #31](https://github.com/omacom/linux-aurora/issues/31). The manual workaround is measured; **this programmatic implementation has not yet been tested on macOS hardware**.

## Qualification before merge

- Run Xcode's strict Swift formatting and XcodeBuildMCP debug/release tests, including `ProMotionSelectionTests`, plus the repository's macOS packaging checks. Linux portable checks do not validate this Swift code.
- On M1 Pro and M3 Max, start with macOS set to 60 Hz. Confirm that only the matching adaptive mode is offered, click the action, and confirm System Settings actually reads ProMotion. Verify the setting after quitting the app, logging out and rebooting, then repeat the Linux direct-display measurement.
- Check already-enabled, changed/scaled resolution, external-only/clamshell, mirrored and non-ProMotion Macs, plus unreadable/missing private metadata. Confirm no external mode or geometry changes.
- Test configuration rejection and post-change verification failure, and confirm Install remains available when skipped or unsuccessful. Check the native display-change confirmation behaviour of `.permanently` on each supported macOS version.
- Confirm preview/simulation cannot inspect or change the host display. No disk installation is needed to test the preference action.
