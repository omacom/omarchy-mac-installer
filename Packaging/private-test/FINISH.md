# Finish the private M3 tester release

This is a maintainer handoff, not an install-ready release. The unsigned review package must not be presented to Chris as a working download. The pinned image is the plain Asahi/GRUB baseline tested on the 13-inch M3 Air. It does not include the subsequent manually built Aurora trial or encryption/Limine work.

Chris's reported machine is the 16-inch M3 Pro with 36 GB RAM, expected `Mac15,7` / `apple,j516s`. This model is admitted for experimental testing but has not physically booted this image in our validation. The exact identifier must be confirmed by preflight. Eleven M3 board identifiers are included; M3 Ultra, M4 and other generations remain excluded.

## What is complete

- Plain-only app behavior, isolated private catalog/cache state and no misleading Aurora channel choice.
- Immutable catalog, image, metadata and engine checksums; actual sealed catalog signature verified.
- Swift debug and release suites: 310 tests passed in each before the final channel-menu-only edit. The final app compiled in release and passed strict signature checks; the catalog-test portability adjustment passed its focused Mac check.
- Portable source suite, hardware guard fixtures, offline staging failures and verified reuse. The real three assets were staged twice on Linux with public installer state preserved; staging fixtures also passed on macOS.
- Final unsigned package extraction matches the app, includes the expected scripts, pins installation to /Applications, and rejects an actual M4 before helper installation. No helper registration, package installation or disk changes were performed.

## What is still needed

1. Review the plain-only UI in a debug preview carrying `OmarchyPrivatePlainTest=true`. The Mac has an older simulation instance running; its owner should close that window before a new review instance is opened. The release app deliberately refuses `--simulate`.
2. Produce Developer ID signed and notarized app/package using an authorized signing Mac. The build M4 has no valid code-signing identities. The signer needs Developer ID Application and Developer ID Installer certificates with their private keys, a Team ID and an authenticated notarytool keychain profile. Keep private keys on that Mac.
3. Validate the downloaded final package's Gatekeeper behavior, helper installation and app/helper authentication on an authorized M3 Mac, stopping at the reviewed plan. This package registers a system LaunchDaemon and opens the app when installed. Running an actual Linux installation is a separate physical test.
4. Replace the review package in a new tester bundle with the exact accepted, stapled package, remove maintainer-only material, regenerate checksums and test an extracted copy. The earlier preparation archive must remain unchanged.

The repository AGENTS.md requires explicit owner authorization immediately before production signing/notarization, helper registration, privileged execution, disk/boot changes and publication. Preparing this handoff performs none of those actions.

## Signing and notarization commands

These are instructions for the authorized signer, not commands that have already run. Use new output directories. Set these variables to the identities on the signing Mac; no credentials belong in this document or the bundle:

```bash
export OMARCHY_APP_SIGNING_IDENTITY='Developer ID Application: YOUR IDENTITY (TEAMID)'
export OMARCHY_INSTALLER_SIGNING_IDENTITY='Developer ID Installer: YOUR IDENTITY (TEAMID)'
export OMARCHY_TEAM_ID='YOURTEAMID'
export OMARCHY_NOTARY_PROFILE='YOUR_KEYCHAIN_PROFILE'
```

From the extracted maintainer bundle, unpack `source.tar.gz` into a new `source` directory. The archive does not include the engine binary; copy the verified `baseline-assets/installer-v0.9.2-omarchy.17.tar.gz` to `source/Engine/artifacts/` before building. On another Mac, use its Xcode command-line toolchain and the source's documented dependencies.

Create a fresh `signed-release-inputs` copy of `release-inputs`. Bind the helper trust requirement to the real signing team; simply re-signing the existing review app would leave its identifier-only development trust requirements in place:

```bash
cp -R release-inputs signed-release-inputs
/usr/bin/plutil -replace helper_code_signing_requirement \
  -string "anchor apple generic and identifier \"com.omarchy.mx.installer.helper\" and certificate leaf[subject.OU] = \"$OMARCHY_TEAM_ID\"" \
  signed-release-inputs/release.json

OMARCHY_PRIVATE_PLAIN_TEST=1 OMARCHY_APP_BUILD_NUMBER=23 \
  bash source/Packaging/build-app.sh "$PWD/signed-release-inputs" "$PWD/signed-app"

bash source/Packaging/notarize-app.sh "$PWD/signed-app/Omarchy MX Mac Installer.app"

bash source/Packaging/private-test/build-review-pkg.sh \
  "$PWD/signed-app/Omarchy MX Mac Installer.app" "$PWD/Omarchy-M3-component-unsigned.pkg"

/usr/bin/productsign --timestamp --sign "$OMARCHY_INSTALLER_SIGNING_IDENTITY" \
  "$PWD/Omarchy-M3-component-unsigned.pkg" "$PWD/Omarchy-M3-Private-Test.pkg"

xcrun notarytool submit "$PWD/Omarchy-M3-Private-Test.pkg" \
  --keychain-profile "$OMARCHY_NOTARY_PROFILE" --wait
xcrun stapler staple "$PWD/Omarchy-M3-Private-Test.pkg"
xcrun stapler validate "$PWD/Omarchy-M3-Private-Test.pkg"
/usr/sbin/pkgutil --check-signature "$PWD/Omarchy-M3-Private-Test.pkg"
/usr/sbin/spctl --assess --type install --verbose=2 "$PWD/Omarchy-M3-Private-Test.pkg"
```

Stop if notarization is not Accepted or any check fails. Retain the notarization submission IDs and logs with the final artifact hashes. Use the private package builder above: the generic builder does not include this bundle's hardware/existing-installation guard.

The sealed catalog and its public key are already paired; no private catalog key is needed to build the same candidate. All catalog URLs are deliberately non-routable. The assets must be staged before opening the app, and Apple's firmware/recovery downloads can still require Internet access. This catalog is for the fixed private candidate, not a future Aurora update service.

## Eventual instructions for Chris

Only send these with the final verified signed package, after the outstanding release checks pass:

1. Confirm the model, macOS 15 or later, available storage, complete backup, and whether another Linux installation or installer/helper already exists. Existing installations need review; this private package refuses to replace their installer state.
2. Verify the downloaded archive checksum, extract it fully, and run `shasum -a 256 -c SHA256SUMS` from the extracted directory in Terminal.
3. Run `bash "Stage assets.command"` from that directory as the normal macOS user. It verifies and copies the three assets into an isolated user cache, without installing a helper or changing partitions. Allow room for the extracted bundle and staging copy in addition to the space shown by the installer for Linux.
4. Open the signed `Omarchy-M3-Private-Test.pkg`, review the publisher and authorize the helper installation. The app will open. Review its storage plan and the explicit unencrypted-install notice before choosing Install.
5. Follow the app's recovery handoff. Report the first boot, Wi-Fi, software-rendered desktop, explicit microphone selection, audio, suspend/resume and second normal boot. Report exact errors and stop if a disk-operation step fails; do not repeat it blindly.

A failure on the 16-inch M3 Pro is a new test result, not proof that it should behave identically to the tested 13-inch Air. GPU acceleration, camera, Bluetooth, external displays, encryption and snapshots are not qualified by this baseline.
