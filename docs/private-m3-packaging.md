# Private M3 baseline packaging

Impact class: app capability/workspace isolation, private catalog and packaging. Linux image, image metadata and `.17` engine remain pinned and unchanged. Source base is `6f45ad29f75c606a1d0c0657f9f9809cfd23c299`. No existing baseline or encryption/Limine branch is changed.

The opt-in signed Info.plist flag `OmarchyPrivatePlainTest` is inserted only when `OMARCHY_PRIVATE_PLAIN_TEST=1` is supplied to the app packager. It disables encryption in the UI, initializes and resets the session to plain, and rejects an encrypted request at the live execution boundary. The private build hides the channel menu because every sealed channel points to the same Asahi baseline; it must not advertise an Aurora choice. Normal app builds preserve their existing behavior. Private builds use the isolated user workspace `~/Library/Application Support/com.omarchy.mx.installer.private-m3-20260922`, including catalog acceptance state, so experimental sequences cannot poison the public channel cache.

The helper service identity remains shared with the original app. The private installer package must reject an already-installed app or helper before changing either. It must not replace a user's existing helper. The default publisher identity in the generic package script is not used. An unsigned review package is not notarized tester delivery.

Validation before distribution: Swift format/lint, debug and release session/catalog suites, package layout and signatures, unsupported-host guard, offline staging checks, and a simulation preview of the plain-only UI. No helper registration, installation, disk mutation, production signing or publication is part of this build. Production signing/notarization needs separate authorization and available credentials.
