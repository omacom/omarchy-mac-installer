# MacBook Neo ad hoc hardware test

This builds an ad hoc installer and an unsigned package that registers its helper on a MacBook Neo (`hw.targettype` `j700ap`) only. It is not a public release and not the private M3 package. The M3 package still admits only its eleven boards. Production `SMJobBless` and Developer ID signing are unchanged. The package is not notarized, so macOS asks for a per-item approval. Do not disable Gatekeeper, and do not clear quarantine.

Build on an Apple Silicon Mac with Xcode, from the repository root. The pinned engine is reproduced into `Engine/artifacts`; the committed restore-image decoder is reused.

```bash
python3 Engine/prepare-engine.py --output Engine/artifacts
release=$(mktemp -d /tmp/omarchy-neo-release.XXXXXX)
cp Release/trust-root.ed25519.pub "$release/"
bash scripts/make-release-descriptor \
  --public-key "$release/trust-root.ed25519.pub" \
  --output "$release/release.json" \
  --default-channel stable --stopgap --adhoc
out=$(mktemp -d /tmp/omarchy-neo-app.XXXXXX)
OMARCHY_NEO_HARDWARE_TEST=1 \
  OMARCHY_APP_VERSION=0.0.0-neo \
  OMARCHY_APP_BUILD_NUMBER=$(date -u +%y%m%d%H%M) \
  bash Packaging/private-test/build-adhoc-app.sh "$release" "$out"
bash Packaging/private-test/build-tester-pkg.sh \
  "$out/Omarchy Installer.app" "$HOME/Desktop/Omarchy-Neo-Hardware-Test.pkg"
```

`build-adhoc-app.sh` signs ad hoc, pins the helper's cdhash into the app, and sets the embedded daemon requirement to `never` so that plist cannot register a helper. `build-tester-pkg.sh` copies that exact helper to the package, pins the app and helper cdhashes into the LaunchDaemon, and hashes the daemon plist into `postinstall`. The package preinstall accepts `j700` and `j700ap` and refuses every other board, another volume, macOS before 15, and an installer or helper that is already present.

Install on the Neo:

1. If an Omarchy helper is already registered, remove only that helper, then install. From Terminal:

```bash
sudo launchctl bootout system/org.omarchy.installer.helper
sudo rm -f /Library/PrivilegedHelperTools/org.omarchy.installer.helper \
  /Library/LaunchDaemons/org.omarchy.installer.helper.plist
sudo rm -rf "/Applications/Omarchy Installer.app" "/Applications/Omarchy MX Mac Installer.app"
```

2. Double-click `Omarchy-Neo-Hardware-Test.pkg`. If macOS says the developer cannot be verified, open **System Settings → Privacy & Security**, choose **Open Anyway** for this package, and approve the administrator prompt. Apple's steps are at https://support.apple.com/en-us/102445. Do not turn Gatekeeper off.

3. Open **Omarchy Installer** from Applications. If macOS blocks the app, use **Open Anyway** for the app as well. The package has already registered the helper; Authorize & Install should reach the helper instead of blessing one.
