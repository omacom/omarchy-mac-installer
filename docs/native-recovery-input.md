# Native Recovery fallback input

An image declaring `recovery_gui: omarchy-native-v1` installs its own pinned, attested Recovery app. When an older image has no native Recovery declaration, a current installer can instead supply its signed Recovery template. An older installer that supplies no template keeps the image's existing legacy path.

For that fallback, the app serializes its bundled template into `native-recovery.json`, a bounded dictionary of relative file names and base64 file data. The handoff manifest fixes its name, size and SHA-256. The helper imports the regular file through the submitted directory descriptor, reconstructs the app in a private directory with fixed permissions, and verifies its signature against the helper's own requirement. Symlinks, unsafe paths, per-install data and oversized inputs are refused. Neither the GUI nor the image chooses the signing requirement.

The helper keeps the verified template in a private per-job directory and removes it after the engine runs. It never publishes the fallback to the shared account-handoff template location. The engine accepts the three template environment keys as optional for released installer apps, but validates the complete input and the original source owner before disk changes whenever a template is supplied. It stages the unchanged signed app with separate per-install configuration, firmware and boot payloads. The existing interrupted-publication path still applies.

An engine that receives a helper template must advertise `nativeRecoveryInput: helper-template-v1`; the helper rejects an older engine before execution. Images with a native Recovery app do not require this capability. This PR adds no automatic Recovery-template refresh during shutdown handoff, so it introduces no new downgrade path. The existing account handoff remains unchanged.

Publish the rebuilt engine named by `Engine/source-lock.json` with the matching installer build before running the release pipeline. A completed legacy installation is not converted by replaying disk-install checkpoints.
