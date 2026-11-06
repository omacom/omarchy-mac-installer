# Omarchy Mac Installer

A macOS app that installs [Omarchy](https://github.com/omacom/omarchy) on an Apple Silicon Mac, next to macOS. The app is named **Omarchy Installer** and uses the `org.omarchy.installer` identifiers (`Packaging/identity.conf`). It was extracted from [maralcbr/omarchy-mx-mac](https://github.com/maralcbr/omarchy-mx-mac) into [omacom/omarchy-mac-installer](https://github.com/omacom/omarchy-mac-installer); the Git history and attribution are preserved (see [extraction provenance](docs/extraction.md)).

Status: every published installer release is a GitHub pre-release; the current notarized app is [Omarchy Installer 2.3.15](https://github.com/omacom/omarchy-mac-installer/releases/tag/v2.3.15); newer ones appear on the [releases page](https://github.com/omacom/omarchy-mac-installer/releases). Since installer 2.3.14 the app opens on the stable channel by default, which offers Omarchy 4.5. See [Release channels](#release-channels).

Installing Omarchy changes the partition layout of your Mac's internal disk and its boot policy. Read [Before you install](#before-you-install) first.

## Contents

- [Supported Macs](#supported-macs)
- [Before you install](#before-you-install)
- [Install](#install)
- [Release channels](#release-channels)
- [What the installer does](#what-the-installer-does)
- [Update, remove and recover](#update-remove-and-recover)
- [Troubleshooting and bug reports](#troubleshooting-and-bug-reports)
- [Contributing](#contributing)
- [Related repositories and where to ask](#related-repositories-and-where-to-ask)
- [Upgrading from a `com.omarchy.mx` preview](#upgrading-from-a-comomarchymx-preview)
- [Source layout](#source-layout)
- [Apple restore inputs](#apple-restore-inputs)
- [License](#license)

## Supported Macs

Which Macs a release supports is decided by that release's signed catalog, not by the app alone. The source of truth in this repository is [`scripts/supported-models.json`](scripts/supported-models.json). Every catalog that offers a Mac release enables all of the 35 boards in that file from one image:

- M1: Mac mini, MacBook Air, MacBook Pro 13-inch, iMac 24-inch, MacBook Pro 14/16-inch (M1 Pro/Max), Mac Studio (M1 Max/Ultra)
- M2: MacBook Air 13/15-inch, MacBook Pro 13-inch, Mac mini (M2, M2 Pro), MacBook Pro 14/16-inch (M2 Pro/Max), Mac Studio (M2 Max/Ultra), Mac Pro
- M3: iMac 24-inch, MacBook Pro 14/16-inch (M3, M3 Pro, M3 Max), MacBook Air 13/15-inch
- MacBook Neo (`apple,j700`)

The per-model names are in [`MacModelNames.swift`](Sources/OmarchyInstallerUXCore/MacModelNames.swift). A Mac whose identifier is not in the signed catalog gets the "This Mac isn't supported yet" screen and cannot be installed. A Mac can be inspected without changing anything: the app checks before it asks for a password.

Not supported:

- The M3 Ultra Mac Studio (`apple,j575d`): the engine can inspect and plan it, but no Omarchy image supports it yet. Public catalogs refuse it; only a developer build's sealed catalog may add it for bring-up.
- M4 and later, for example the MacBook Pro 14-inch M4 Pro (`apple,j614s`). Public builds keep these blocked until official support and physical qualification both exist.

Untested: the M3 Macs and the MacBook Neo are marked untested in `supported-models.json`. According to [`docs/mac-release-pipeline.md`](docs/mac-release-pipeline.md#qualification-on-edge), the testing team qualifies them on real hardware on edge, after an edge release.

Why a catalog check exists: `make-unsigned-catalog.py` refuses inputs that miss, add or refuse a board, and `publish-channels envelope` refuses to wrap a catalog that lists a Mac but leaves any of them out or disabled. Only an empty "No Mac release yet" catalog lists none. `os-promote` does not re-check an envelope staged before this rule, so a rollback to an older catalog stays possible.

## Before you install

- **Back up macOS first.** The installer shrinks your macOS container and adds partitions. The install screen has a checkbox that you must tick ("I have a current backup and approve the disk layout/resize above") before Install unlocks. Local Time Machine snapshots are restore points on the startup disk and are not a backup.
- **Disk space.** The app computes an allocation from your disk and shows a size you can edit in whole gigabytes. It recommends leaving macOS about 38 GB free and warns if the plan leaves less. Free space shown in macOS is not necessarily space the installer can release for a new partition; if it cannot, it says so and does not change the disk.
- **Encryption.** The installed system encrypts its disk at first boot with the password you choose then. The installer text says: "Your first-boot password encrypts Omarchy. Keep it safe: there's no recovery key." The app records the encryption setting in `omarchy/install.conf` on the Omarchy EFI partition; if it cannot be saved, the app tells you Omarchy will encrypt at first boot. See [EFI encryption handoff](docs/efi-encryption-handoff.md).
- **FileVault.** The app checks the FileVault state of macOS while inspecting the Mac.
- **macOS and hardware.** The app requires macOS 15 or newer (`LSMinimumSystemVersion` 15.0). The MacBook Neo target is recorded as qualified only for firmware 26.6.2 (`Recovery/README.md`).
- **Power and network.** Keep the Mac open and plugged in during installation (the app says so). The install downloads several gigabytes; the app resumes stalled downloads. It also needs HTTPS access to Apple's restore-image servers, see [Apple restore inputs](#apple-restore-inputs).
- **Accounts.** You need a macOS administrator account and its password: the app sets up a privileged helper and authorizes the boot change with your password. Do not run an install while another installer or removal runs on the Mac.
- **Shutdown and Recovery.** The last stage happens in macOS Recovery: you shut down, hold the power button until "Loading startup options" appears, choose Omarchy, click Continue and sign in with your macOS account. Plan for it before you start.

## Install

The simplest way is [Homebrew](https://brew.sh):

```sh
brew install omacom/mac/omarchy
```

This installs **Omarchy Installer** into `/Applications` and opens it; continue at step 4 below. Homebrew prints a deprecation warning about `postflight`, which is expected. The [omacom/homebrew-mac](https://github.com/omacom/homebrew-mac) tap picks up new installer releases automatically.

To install from the disk image instead:

1. Download [`Omarchy-Mac-Installer-2.3.15.dmg`](https://github.com/omacom/omarchy-mac-installer/releases/download/v2.3.15/Omarchy-Mac-Installer-2.3.15.dmg) and [`Omarchy-Mac-Installer-2.3.15.dmg.sha256`](https://github.com/omacom/omarchy-mac-installer/releases/download/v2.3.15/Omarchy-Mac-Installer-2.3.15.dmg.sha256) from the [2.3.15 release](https://github.com/omacom/omarchy-mac-installer/releases/tag/v2.3.15). If a newer `Omarchy Installer <version>` release is on the [releases page](https://github.com/omacom/omarchy-mac-installer/releases), use that instead. Ignore the `catalog-*` and `engine-*` pre-releases: they hold data the app reads, not the app.
2. Check the download. From the folder that holds both files:

   ```sh
   shasum -a 256 -c Omarchy-Mac-Installer-2.3.15.dmg.sha256
   ```

   Each release's notes also give the digest; for 2.3.15 it is `a6ee0cc214fff9f9d194772a5b4492c055c8f74d8572c1183874449d445f3d11`.
3. Open the disk image and move **Omarchy Installer** to `/Applications`. The disk image holds the app and an `/Applications` link.
4. Open the app. It checks the Mac model, macOS version, power, FileVault and free disk space, then shows the plan. Choose the disk size, tick the backup box, click **Install**, and enter your macOS administrator account and password when asked. Allow the app to add its helper if macOS asks. The helper shows up under Login Items in System Settings.
5. Keep the Mac awake and plugged in until the app says the first stage is done. Then follow the on-screen **Finish setup** steps in Recovery (see [What the installer does](#what-the-installer-does)).

The release pipeline also copies the dmg and its `.sha256` to `https://mac.omarchy.org/v1/installer/<version>/`; the catalog's `downloadURL` points there ([`docs/catalog-release.md`](docs/catalog-release.md)).

The download is a plain app, not a command-line installer. There is no `curl | sh` path for this installer. For the older shell-based path used by the `omarchy-mac` fork, see [Related repositories](#related-repositories-and-where-to-ask).

## Release channels

The app can read one of three catalogs, all signed with the Omacom catalog key and fetched from `https://mac.omarchy.org/v1/`:

| Channel | Catalog | Use |
| --- | --- | --- |
| Stable | `catalog-stable.signed.json` | The default since installer 2.3.14 ([#169](https://github.com/omacom/omarchy-mac-installer/pull/169)). Offers Omarchy 4.5. |
| Release candidate (rc) | `catalog-rc.signed.json` | Release candidates before they reach stable. Was the default in installer 2.3.13 ([#159](https://github.com/omacom/omarchy-mac-installer/pull/159)). |
| Edge | `catalog-edge.signed.json` | Where the M3 Macs and the MacBook Neo are qualified on real hardware (see [Supported Macs](#supported-macs)). |

The channels match Omarchy's. Dev is a developer choice made after installation, not an installer channel.

To switch channel, open the **Release channel** menu in the menu bar and pick one. Entries read "Couldn't load", "No Mac release yet", "Couldn't verify" or "Not available for this Mac" when that channel cannot be used right now; **Check Channels Again** re-reads them. The choice lives in memory only: every launch opens on the default (stable), and a choice never persists to another run. The menu locks once preparation, authorization or installation has started, so a plan made against one channel is never installed from another. The three catalog addresses are baked into the signed app; the menu cannot point at a source the app was not built to trust. Developer and private-test builds hide this menu.

## What the installer does

The screens, in order, as the app words them (`PlainLanguage.swift`):

1. **Check.** Inspects the Mac: model, macOS version, power, FileVault and free disk space. A Mac that is not in the signed catalog stops here ("This Mac isn't supported yet"). One that cannot be confirmed stops at "This Mac isn't ready yet". If Omarchy is already installed, the app says so and offers [removal](#update-remove-and-recover).
2. **Plan.** Fetches and verifies the channel's signed catalog (Ed25519, trust root in `Release/trust-root.ed25519.pub`) and downloads the pinned engine and image, checking their sizes and SHA-256 digests before use. Shows the disk bar with macOS, free space and Omarchy, lets you change the Omarchy size, and holds **Install** until the download is verified and the backup box is ticked.
3. **Authorize.** Takes your macOS administrator account and password, installs the privileged helper and checks that the account is a macOS administrator. The password is not stored in the journal.
4. **Install.** The helper runs the pinned Asahi-based engine, which goes through these recorded phases: checking the disk; removing a previous Omarchy installation if one exists; preparing disk space (shrinking the macOS APFS container); writing boot files (stub and EFI); preparing the final setup; authorizing startup; installing Omarchy. The app tails the engine journal for progress and verifies the result against the installation record when it finishes. Afterwards the helper writes the encryption choice to `omarchy/install.conf` on the EFI partition.
5. **Finish setup (Recovery).** The app asks you to shut down, hold the power button until "Loading startup options" appears, choose Omarchy, click Continue and sign in with your macOS account. Omarchy's native Recovery app then asks for the owner password, installs and enrolls startup software, and shows **Installation complete!** with **Restart**. It can also create an optional recovery account (hold Shift on the finished screen, or Utilities → Create Recovery Account…); skipping it can be done later with **Installation → Set Up Recovery Password…** in the main app. See [`Recovery/README.md`](Recovery/README.md).
6. **First boot of Omarchy.** Choose the Omarchy volume at startup (hold the power button when the Mac starts to pick macOS or Omarchy) and set the disk password. This repository does not cover what happens after that; see the [Mac packages](https://github.com/omacom/omarchy-mac-pkgs).

The helper is a root launchd daemon registered through `SMJobBless`; the app and helper check each other's code signature, and the helper authenticates every XPC client. The destructive steps are guarded by plans bound to a digest of the disk layout, so a plan made for one layout will not run on another.

## Update, remove and recover

**Update the installer app.** If you installed with Homebrew, run `brew upgrade omarchy`. Otherwise download the newer dmg from the releases page and replace the app in `/Applications`. A catalog can set a minimum app version; an older app is told to update (the app has a **Download the installer** action).

**Update Omarchy after installing.** This repository does not cover it. See [Omarchy's manual](https://omarchy.org/manual/) and the [Mac manual](https://github.com/omacom/omarchy-mac-pkgs/tree/main/mac-manual).

**Reinstall.** If Omarchy is already installed, the app detects it and removes the previous installation as the first phase of a new install. Use that only with a current backup.

**Remove Omarchy.** Choose **Installation → Remove Omarchy…** while no install or download runs. The app shows the space that returns to macOS and requires you to type the phrase `delete omarchy installation and data`, plus a macOS administrator account and password. Removal deletes the Omarchy partitions and grows the macOS container back over them. It refuses a layout it does not recognize (unknown or partial installations, external boot, more than one APFS store, mismatched names or UUIDs, and `apple,j614s`) rather than guess. If the layout shows only unallocated space right after macOS, it offers **Free space only** to return it. Details and interruption rules: [`Development/Removal.md`](Development/Removal.md). That document says physical removal had not been executed when it was written.

If you installed the app with Homebrew, `brew uninstall --zap omarchy` removes the app, its helper and its support files. It does not remove Omarchy from the disk: use **Remove Omarchy…** in the app first.

If a removal record is unfinished, the app will not install or remove again until it is resolved. Never delete or edit that record to get around the block: compare its partition UUIDs with the real layout first. A failed resize can leave all Omarchy partitions removed and the space unallocated. Do not repeat the deletion. The helper keeps its records in `/var/db/org.omarchy.installer`.

**Recover a failed or interrupted install.**

- A failed disk-space step reports that the disk has not been changed. Use **Check available space** to make a new plan.
- If Apple's startup authorization step fails, use **Retry Recovery authorization**; the app first rechecks the plan, the installed files and the last completed step and cannot resize the disk or rewrite the system in the retry.
- To start over, quit the app and reopen it, or use **Start over**. A simulation-only **Reset simulation** exists in debug builds.

## Troubleshooting and bug reports

Common messages and what they mean:

- "This Mac isn't supported yet": the Mac's model is not in the release's catalog. See [Supported Macs](#supported-macs).
- "Couldn't load" in the Release channel menu: no catalog is published for that channel, or the network failed. Choose **Check Channels Again**. "No Mac release yet" means a signed empty catalog is published.
- "The encryption choice wasn't saved, so Omarchy will encrypt its disk at first boot": the installer could not write `omarchy/install.conf` to the EFI partition. Fixed for busy volumes in installer 2.3.12 ([#156](https://github.com/omacom/omarchy-mac-installer/pull/156)); see [EFI encryption handoff](docs/efi-encryption-handoff.md).
- "Not enough free space": the download needs more room than is available. Free space on macOS and run **Check available space**.
- "An earlier removal didn't finish" or "The installer couldn't clear an earlier installation's record": see [Update, remove and recover](#update-remove-and-recover). Keep the records for support.
- macOS asks about Login Items: the privileged helper appears under System Settings → General → Login Items.

Collecting information for a bug report:

1. Note the installer version (it is in the dmg file name), the channel, `sw_vers`, and the Mac's model and chip. In Terminal, `sysctl hw.model hw.targettype` prints the model and the `apple,jNNN` board that the catalog uses.
2. On an engine failure, choose **Copy error details** in the app. A copy is usually saved as `~/Library/Logs/Omarchy Installer/engine-failure-<time>.log`.
3. In Recovery, open **Utilities → Show Details** to see the log (`Recovery/README.md`).
4. Screenshot the error screen. Do not post your macOS password or recovery password anywhere, and check any file before you attach it.

Open an issue at [omacom/omarchy-mac-installer issues](https://github.com/omacom/omarchy-mac-installer/issues) with those details.

## Contributing

There is no `CONTRIBUTING.md`; the conventions below come from `AGENTS.md` and `docs/validation.md`.

**Build and test from source.**

- Portable suite, on Linux or macOS with Bash 5+, Python 3.12+, Git and GNU `sha256sum`. It uses temporary fixtures and mocked publishing/SSH commands and installs nothing:

  ```bash
  bash test/all
  ```

  On macOS, use a Bash 5 installation explicitly if the system Bash is older.
- Swift package: macOS 15+ and Swift 6.2+ (Xcode 26.2 in CI):

  ```bash
  xcrun swift-format lint --strict --recursive Package.swift Sources Tests
  xcodebuildmcp swift-package test --package-path "$PWD" --configuration debug
  xcodebuildmcp swift-package test --package-path "$PWD" --configuration release
  ```

  [XcodeBuildMCP](docs/validation.md#macos-build-handoff) 2.7.0 is what CI and the validation notes use.
- Recovery app: on macOS run `Recovery/test-all.sh <output>`; build with `Recovery/build-recovery.sh` (real) or `Recovery/build-preview.sh` (preview, cannot enroll).
- Simulation: `Development/Run Simulation.command` launches the debug app with `--simulate`, which uses an in-memory environment and never touches the disk, the helper or real credentials. Release builds refuse `--simulate`. See [Simulation guide](Development/Simulation.md).
- Packaging needs an authenticated engine archive, see [Packaging/README.md](Packaging/README.md) and [the extraction prerequisites](docs/extraction.md#inherited-prerequisites-and-open-issues).

**CI.** [Installer checks](.github/workflows/checks.yml) runs on every pull request and on pushes to `main`: portable checks on Ubuntu 24.04 and macOS checks (strict Swift formatting, debug and release tests) on an Apple Silicon macOS runner. Details: [`docs/validation.md`](docs/validation.md#continuous-integration). The release pipeline is in [`docs/mac-release-pipeline.md`](docs/mac-release-pipeline.md).

**Conventions** (from `AGENTS.md`):

- Python: four-space indentation; validate with `py_compile` and the focused engine tests.
- Swift: format with Xcode's bundled `swift-format`, strict lint.
- Shell: two-space indentation, `#!/bin/bash`, Bash 5 `[[ ]]` and `(( ))`; quote the right-hand variable in `[[ == ]]` comparisons.
- Markdown: keep lines unwrapped except at structural boundaries.
- `./test/all` stays unprivileged and must never run live disk, helper registration, signing, publication or deployment operations.
- Preserve the engine lock, release trust configuration and host restrictions; changes to those need a separate review.
- Record validation against the exact commit with toolchain and host details. Linux results do not count for macOS requirements.
- Get the owner's explicit authorization before helper registration, privileged execution, disk or boot-policy changes, production signing or notarization, publication, deployment or physical-device work.

**Pin pull requests.** Publishing an app or engine release opens a pull request that moves the pins in `.github/mac-release-pipeline.json` (for example [#160](https://github.com/omacom/omarchy-mac-installer/pull/160)). Merge it after `mac-mirror.yml` has copied the release to the mirror. See [`docs/mac-release-pipeline.md`](docs/mac-release-pipeline.md).

The preserved release and cutover tools keep the original publisher's endpoints and signing identity and need review before operational use ([`docs/extraction.md`](docs/extraction.md)). Publishing, signing and physical installation need separate preparation and authorization.

Further reading:

- [Extraction provenance and boundaries](docs/extraction.md)
- [Validation and macOS handoff](docs/validation.md)
- [Catalog release](docs/catalog-release.md) and [release pipeline](docs/mac-release-pipeline.md)
- [Linux image producer](docs/image-builder.md): the Apple Silicon image producer is `image-builder/`, building from a signed candidate set with the authenticated candidate importer and inspecting each image before it is packaged. See also [how to build](image-builder/README.md) and [one image producer](docs/image-producer.md).

## Related repositories and where to ask

| Repository | Role |
| --- | --- |
| [omacom/omarchy](https://github.com/omacom/omarchy) | Omarchy itself, the desktop that gets installed |
| [omacom/omarchy-mac-pkgs](https://github.com/omacom/omarchy-mac-pkgs) | The Apple Silicon add-on packages (`omarchy-mac`, `omarchy-mac-boot`), release tooling, and the Mac manual. Package changes go there |
| [omacom/omarchy-pkgs](https://github.com/omacom/omarchy-pkgs) | Recipes that build and publish those packages to pkgs.omarchy.org |
| [omacom/omarchy-mac](https://github.com/omacom/omarchy-mac) | The earlier Omarchy 4 fork for Macs, installed by script through Asahi Alarm. It remains for Macs that update from it. It is not this installer and takes no package changes |

Questions and help: the community [Discord](https://discord.gg/KNQRk7dMzy) (the homepage of omacom/omarchy-mac). Bugs and feature requests: [issues on this repository](https://github.com/omacom/omarchy-mac-installer/issues); for a package or Linux-side problem, use the issues of [omarchy-mac-pkgs](https://github.com/omacom/omarchy-mac-pkgs/issues) or [omarchy](https://github.com/omacom/omarchy/issues).

## Upgrading from a `com.omarchy.mx` preview

Earlier preview and lab builds used `com.omarchy.mx.installer`. The new app has the same name, so it replaces the old one in `/Applications`, but it installs its own helper and never touches the old helper, state or a copy still named Omarchy MX Mac Installer: the old helper stays registered (and listed under Login Items in System Settings), and an install or removal record the old app left is not seen by the new one. Finish or clean up any interrupted install or removal with the old app before switching. The package removes a copy left under the old name, Omarchy MX Mac Installer, only when it already carries the new identifiers. To remove what the old app left:

```sh
sudo launchctl bootout system/com.omarchy.mx.installer.helper
sudo rm -f /Library/LaunchDaemons/com.omarchy.mx.installer.helper.plist /Library/PrivilegedHelperTools/com.omarchy.mx.installer.helper
sudo rm -rf /var/db/com.omarchy.mx.installer /var/db/com.omarchy.mx.installer.aside
sudo rm -rf "/Applications/Omarchy MX Mac Installer.app"
rm -rf ~/Library/Application\ Support/com.omarchy.mx.installer* ~/Library/Caches/com.omarchy.mx.installer
defaults delete com.omarchy.mx.installer 2>/dev/null
```

The Application Support line also frees any image the old app downloaded. A Mac that ran the old installer package can also drop its receipt with `sudo pkgutil --forget com.omarchy.mx.installer.pkg`.

## Source layout

| Directory | Responsibility |
| --- | --- |
| `Sources/`, `Tests/`, `Package.swift` | Swift macOS app, trust core, helper, simulation and tests |
| `Engine/` | Pinned Asahi source lock, Python overlay, build tooling and tests |
| `Packaging/` | App and macOS package assembly |
| [`Recovery/`](Recovery/README.md) | Omarchy Step 2 GUI, standalone macOS 13 target and app packaging |
| `Release/` | Release descriptor (channels, default channel) and public trust root; see [`Release/README.md`](Release/README.md) |
| `scripts/` | Catalog and release tools, with fixture tests; `supported-models.json` |
| `image-builder/` | Producer of the Apple Silicon Linux image |
| `test/` | Portable aggregate runner and extracted packaging tests |
| `evidence/` | Recorded journal fixture required by the Swift tests and preview |
| `docs/`, `Development/`, `Design/` | Design notes, runbooks, validation records, simulation guide and UI designs |

## Apple restore inputs

Firmware preparation needs HTTPS access to Apple's restore-image CDN (`updates.cdn-apple.com`) and, for AEA-encrypted inputs, Apple's public release-key service (`wkms-public.apple.com` or `fcs-keys-pub-prod.cdn-apple.com`). An offline Linux image bundle does not remove that Apple-input dependency. The helper requests only the admitted FCS key URL from the archive metadata; it sends no installation credentials. Range extraction checks the recipe, response extent, ciphertext authentication and resulting file hashes before publishing any firmware.

The helper is built from the [Swift source in this repository](Engine/RestoreImage/README.md), ported from Eryk Wieliczko's MIT implementation. Its recorded source hashes, toolchain, two-build comparison and binary digest are checked alongside the engine source lock. It ships in the app at `Contents/Helpers/omarchy-restore-image`, signed by the app's release signing (Developer ID, hardened runtime, secure timestamp, no entitlements), not inside the engine archive. The privileged helper passes an install's engine a root-only copy whose signature satisfies the app team's requirement, and the engine refuses to run an install without one. The helper requires macOS 14 or newer; the installer app already requires macOS 15 or newer.


## License

[MIT](LICENSE). The original MIT license, copyright notice and Git attribution are retained. External engine dependencies retain their own licenses; their exact revisions are recorded in `Engine/source-lock.json`. The restore-image helper is derived from an MIT implementation (see [Engine/RestoreImage/LICENSE](Engine/RestoreImage/LICENSE)).
