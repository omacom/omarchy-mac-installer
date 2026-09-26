# Application message review — 2026-09-26

Status: **Approved and implemented.** Scott approved every row on 2026-09-26, editing N001, N022, N037, N039 and N040; rows marked "Requested" are his own wording.

This round follows the Try Omarchy look and the rename to Omarchy Installer. It lists only strings whose wording changes, plus strings added since the [2026-09-07 review](Message-review.md) that were reviewed and kept. The earlier review remains the baseline for everything else.

Voice: short, plain statements with the fact first, then what to do. Sentence case in source (buttons draw uppercase). No exclamation marks and no "please". macOS names (Startup Disk, Recovery, FileVault). "This Mac", not "your computer" or "your Mac" where either works. Decimal GB. Oxford commas, as in the rest of the app.

Consistent terms introduced by this round:

- **installation service** and **removal service**: the privileged helper, named for the job at hand (was "system installation service", "removal helper" and "helper").
- **installation record** and **removal record**: the journals people are asked to check (was also "removal journal"), in the app and in the helper’s removal messages.
- **Omarchy Installer package**: what people run again to repair the service (was "the Omarchy installer package").
- **download**: the Omarchy image being fetched and verified (was "installation files" in progress titles).

Rows marked 🔒 are meaning-locked (Recovery steps, or erase, remove, or resize actions): the wording gets clearer, but steps, their order, and their warnings do not change.

Source references are relative to the repository root.

## Changes

| ID | Current text | Proposed text | Note | Approval |
| --- | --- | --- | --- | --- |
| N001 | When setup is complete, you can choose Omarchy or macOS at startup. | Omarchy installs next to macOS. You can select either one by holding the power button when the Mac starts. | Scott’s edit (round 2): accurate, since the choice only appears when the power button is held. Lead with what happens, then the benefit. `PlainLanguage.checkSubheadline` | Approved |
| N002 | Checking this Mac | Checking this Mac… | Ellipsis for every in-progress label. `PlainLanguage.inspectingHeadline` | Approved |
| N003 | Checking your Mac model, macOS version, power connection, FileVault, and available disk space. | Checking the Mac model, macOS version, power, FileVault, and free disk space. | Shorter. `PlainLanguage.inspectingSubheadline` | Approved |
| N004 | I have a current backup and approve the disk sizes shown above. | I have a current backup and approve the disk resize above. | Scott’s wording (round 2): shorter, so it doesn’t wrap. `PlainLanguage.planAcknowledgement` | Requested |
| N005 | Downloading installation files | Downloading Omarchy | Says what is downloading. `PlainLanguage.downloadingPackagesTitle` | Approved |
| N006 | Downloading installation files… | Downloading Omarchy… | Same, in the preparation stages. `PlainLanguage.preparingStageTitle(.downloading)` | Approved |
| N007 | Verifying installation files… | Verifying the download… | Same term as N005. `PlainLanguage.prefetchVerifying` | Approved |
| N008 | The installation files could not be downloaded or verified. | The download failed or didn’t pass verification. | Shorter, same two cases. `PlainLanguage.prefetchFailed` | Approved |
| N009 | Checking the signed release… | Checking the release signature… | Says what is checked. `PlainLanguage.preparingStageTitle(.fetchingCatalog)` | Approved |
| N010 | Preparing your installation plan… | Planning the disk layout… | Says what the plan is. `PlainLanguage.preparingStageTitle(.planning)` | Approved |
| N011 | Encrypt this Mac's Linux disk | Encrypt the Omarchy disk | Scott’s wording. `PlainLanguage.encryptLinuxDiskTitle` | Requested |
| N012 | The Linux login password you set at first boot unlocks the disk after setup. | The Omarchy password you select at first boot will unlock the encrypted disk after installation. | Scott’s wording. `PlainLanguage.encryptLinuxDiskPassword` | Requested |
| N013 | A recovery key is shown once at first boot. Write it down. | A recovery key will also be shown in case you ever are unable to use the password. Write it down. | Scott’s wording, keeping “Write it down.” because the key appears only once. Drawn in one help paragraph with N012. Scott’s wording, keeping “Write it down.” because the key appears only once. `PlainLanguage.encryptLinuxDiskRecovery` | Requested |
| N014 | Encryption choice not recorded: first boot will encrypt | The encryption choice wasn’t saved, so Omarchy will encrypt its disk when it first starts. | A sentence, like every other notice. `PlainLanguage.encryptionChoiceNotRecorded` | Approved |
| N015 | Disk encryption was turned off. That choice was recorded. | Disk encryption is off, and that choice was saved. | One sentence. `PlainLanguage.encryptionOptOutRecorded` | Approved |
| N016 | Checking your credentials… | Checking your macOS account… | Same words as the install stage that follows. `PlainLanguage.authorizeChecking` | Approved |
| N017 | This can take a few minutes. After your credentials are accepted, the installer prepares the installation package. | This can take a few minutes. Once your account is confirmed, the installer prepares Omarchy’s files. | Plainer. `PlainLanguage.authorizeStillWorking` | Approved |
| N018 | Use a macOS account authorized to install on this Mac. Your password authorizes the disk changes you reviewed and the Recovery setup. | Use a macOS account that is allowed to install on this Mac. Your password approves the disk changes you reviewed and the Recovery setup. | Avoids "authorized … authorizes". `Screens/CredentialSheet.swift` | Approved |
| N019 | Reviewed allocation | Reviewed size | Plain word. `Screens/CredentialSheet.swift` | Approved |
| N020 🔒 | The installer will verify the approved plan, installation files, disk location, and completed checkpoint before retrying Apple’s boot authorization. This retry cannot resize the disk, change partitions, or rewrite the installed system. | Before retrying Apple’s startup authorization, the installer rechecks the approved plan, the installation files, the disk location, and the last completed step. The retry can’t resize the disk, change partitions, or rewrite the installed system. | Same checks and the same limits; plainer order. `PlainLanguage.recoveryRetryConfirmationBody` | Approved |
| N021 | Keep your Mac open and connected to power. | Keep this Mac open and plugged in. | Shorter. `PlainLanguage.installWarning` | Approved |
| N022 | Live progress is unavailable. The installer will verify the installation record when it receives a result. | Live progress isn’t available. The installer will check the installation record when complete. | Scott’s edit. Plainer. `PlainLanguage.installDegraded` | Approved |
| N023 | Installation files written | Boot files written | Matches the stage it confirms ("Writing boot files…"). `PlainLanguage.checkpointSummary("stub-and-esp-installed")` | Approved |
| N024 🔒 | Save your work before shutting down. When your Mac is off, hold the power button until “Loading startup options” appears. Choose Omarchy → Finish Installation, then sign in with your macOS account. | Save your work first. When this Mac is off, press and hold the power button until “Loading startup options” appears. Choose Omarchy → Finish Installation, then sign in with your macOS account. | Same steps; "press and hold" matches the step list. `PlainLanguage.shutdownConfirmationBody` | Approved |
| N025 🔒 | Shut down | **Shut down this Mac** — help text: Save your work first. | Each Recovery step is now a short title with help text underneath, as Scott asked; same step. `PlainLanguage.recoverySteps` (step 1) | Requested |
| N026 🔒 | When your Mac is off, press and hold the power button until startup options appear. | **Hold the power button** — help text: When this Mac is off, press and hold the power button until “Loading startup options” appears. | Same step. The title carries the action people miss (holding, not pressing); the help text names the exact screen, as the confirmation does. `PlainLanguage.recoverySteps` (step 2) | Requested |
| N027 🔒 | Choose Omarchy → Finish Installation, then sign in with your macOS account. | **Finish installation** — help text: Choose Omarchy → Finish Installation, then sign in with your macOS account. | Same step, instruction unchanged. The fallback list (no signed steps) now shows these same three steps; it used to say "Hold the power button until startup options appear." `PlainLanguage.recoverySteps` (step 3 and fallback) | Requested |
| N027a | Unsupported Recovery instruction: {token}. Save the installation record and get support before continuing. | **Unsupported Recovery instruction: {token}** — help text: Save the installation record and get support before continuing. | Same split for an unknown signed step. `PlainLanguage.recoverySteps` (unknown token) | Requested |
| N028 | Omarchy’s files are installed. Finish setup in Recovery to allow your Mac to start Omarchy. | Omarchy’s files are in place. Finish setup in Recovery so this Mac can start Omarchy. | Plainer. `PlainLanguage.nextActionMessage(.enterRecovery)` | Approved |
| N029 | Release verified; installation files written | Release verified, files written | Shorter fact row. `PlainLanguage.doneVerifiedRows` | Approved |
| N030 | Each Mac model requires physical testing before it is included in a signed installer release. Catalog updates can withdraw support; adding a model requires a new signed release. | Each Mac model is tested on real hardware before a signed release includes it. A catalog update can withdraw support, but adding a model needs a new release. | Plainer, same rules. `PlainLanguage.blockedExplainer` | Approved |
| N031 | Use Copy error details to keep them. A copy is normally saved in ~/Library/Logs/… | Choose Copy error details to keep them. A copy is usually saved in ~/Library/Logs/… | Matches "Choose Check again" elsewhere. `PlainLanguage.engineDiagnosticsLocation` | Approved |
| N032 | The system installation service is missing. Run the Omarchy installer package again, then reopen this app. | The installation service is missing. Open the Omarchy Installer package again, then reopen this app. | Consistent term and the package’s new name. `PlainLanguage.helperNotInstalled` | Approved |
| N033 | Run the Omarchy installer package again, then reopen this app. | Open the Omarchy Installer package again, then reopen this app. | Same. `PlainLanguage.failure` (service not responding) | Approved |
| N034 | Download and run the latest installer package, then reopen this app. | Download and open the latest Omarchy Installer package, then reopen this app. | Same. `PlainLanguage.failure` (installer out of date) | Approved |
| N035 | The previous installation result must be checked before you can start again. Keep your Mac connected to power. | Check the previous installation’s result before starting again. Keep this Mac plugged in. | Fact, then action; shorter. `OnePage/OnePageInstallerView.swift` | Approved |
| N036 | Edit disk size | Change size | Shorter button. `OnePage/OnePageInstallerView.swift` | Approved |
| N037 🔒 | Your macOS files and Apple Recovery will be kept. This cannot be undone. | macOS, your files, and Apple Recovery stay as they are. Removal can’t be undone. | Scott’s edit. Same guarantee and warning. `OmarchyRemovalSheet.swift` | Approved |
| N038 | Type the following to confirm: | Type this to confirm: | Shorter. `OmarchyRemovalSheet.swift` | Approved |
| N039 🔒 | The removal helper is unavailable. Install the current app and helper, then try again. No disk changes were made. | The removal service isn’t available. Open the Omarchy Installer package again, then try again. No disk changes were made. | Scott’s edit. Consistent term and remedy. `OmarchyRemovalSheet.swift` (live and preview) | Approved |
| N040 🔒 | The helper connection was lost. Removal may still be running. Do not restart removal or turn off your Mac. Check the removal journal before continuing. | The connection to the removal service was lost. Removal may still be running. Don’t restart removal or turn off this Mac. Check the removal record before continuing. | Scott’s edit. Same warnings; consistent terms. `OmarchyRemovalSheet.swift` (live and preview) | Approved |
| N041 🔒 | Removal stopped and some Omarchy data may already be deleted. Do not repeat deletion; the removal journal was kept for recovery. | Removal stopped, and some Omarchy data may already be deleted. Don’t start removal again; the removal record was kept for recovery. | Same warning; consistent terms. `OmarchyRemovalSheet.swift` (preview) | Approved |
| N042 🔒 | Omarchy was removed, but returning its space to macOS could not be confirmed. The space may still be unallocated. Do not repeat deletion; the removal journal was kept for recovery. | Omarchy was removed, but the installer couldn’t confirm its space went back to macOS. The space may still be unallocated. Don’t start removal again; the removal record was kept for recovery. | Same warning; consistent terms. `OmarchyRemovalSheet.swift` (preview) | Approved |
| N043 | Check the removal journal and disk layout before making further disk changes. | Check the removal record and disk layout before changing any disks. | Consistent term; shorter. `OmarchyAppleInstallerApp.swift` | Approved |
| N044 | Omarchy will use the space shown above. Finish setup in Recovery after restarting. | Omarchy will use the space selected above. | Scott’s wording; the Recovery screen already covers the rest. Drawn as help text. `OnePage/OnePageInstallerView.swift` | Requested |
| N045 | macOS account name | macOS user | Shorter field label; also its VoiceOver label. `PlainLanguage.authorizeUsernameLabel` | Requested |
| N046 | The account name or password wasn’t accepted. | The user name or password was incorrect. | Scott’s wording; also the headline when the service rejects the password. `PlainLanguage.authorizeRejected` | Requested |
| N047 | To remove it and return its space to macOS, choose Installation → Remove Omarchy from the menu bar. | If you’d like to remove Omarchy and return its space to macOS, select Installation → Remove Omarchy from the menu bar. | Scott’s wording, with the app’s → menu arrow. `OnePage/OnePageInstallerView.swift` | Requested |
| N048 | macOS login password | macOS password | Scott’s wording; also its VoiceOver label. `PlainLanguage.authorizePasswordLabel` | Requested |
| N049 | An earlier removal did not finish. Review the saved removal journal and disk layout before making further disk changes. | An earlier removal didn’t finish. Check the saved removal record and disk layout before changing any disks. | The helper’s own removal messages follow N041–N043 (also "The removal record couldn’t be saved."). `ClosedEngineHelperServer.swift` | Follows N041–N043 |

## Reviewed and kept

Strings added since the 2026-09-07 review that already fit the voice:

- Prefetch: "Waiting for Wi-Fi or Ethernet…", "Download paused", "Try again".
- Encryption: "Encryption was recorded, but the installer could not confirm the disk was unmounted." ("unmounted" is the precise state; no plainer word is accurate.)
- Engine refusals: every `engineFailure` headline, detail, and remedy, including "Check available space".
- Unsupported Macs: "This Mac (…) isn’t included in this release." and "This release supports these Macs (… models): …".
- Channels: "Release channel", "Stable", "Release candidate", and "No release is available on this channel" with its detail and remedy.
- Startup sequence facts: "m1n1 → U-Boot → Limine → Omarchy" and its private variant.

## Out of scope

Verbatim technical and engine diagnostics, debug-only simulation copy (including the removal preview scenario names), machine protocol values, and the engine’s own messages. Private M3 test notices are left for the private-test profile’s owner.
