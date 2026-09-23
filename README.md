# Omarchy Mac Installer

Development source for the Apple Silicon macOS installer, extracted from [maralcbr/omarchy-mx-mac](https://github.com/maralcbr/omarchy-mx-mac) into [omacom/omarchy-mac-installer](https://github.com/omacom/omarchy-mac-installer).

This first extraction preserves the installer behavior and Git attribution. The app still carries its original **Omarchy MX Mac Installer** identity and release configuration. Integration with the shared Omarchy packages and Linux installation image is the next, separate change. This candidate is not an installation release.

## Source layout

| Directory | Responsibility |
| --- | --- |
| `Sources/`, `Tests/`, `Package.swift` | Swift macOS app, trust core, helper, simulation and tests |
| `Engine/` | Pinned Asahi source lock, Python overlay, build tooling and tests |
| `Packaging/` | App and macOS package assembly |
| `Release/` | Inherited public trust root and release descriptor |
| `scripts/` | Catalog and release tools, with fixture tests |
| `test/` | Portable aggregate runner and extracted packaging tests |
| `evidence/` | Recorded journal fixture required by the Swift tests and preview |

## Start with the tests

The portable suite requires Bash 5+, Python 3.12+, Git and standard Unix tools, including GNU `sha256sum`. It runs on Linux and can run on macOS with those tools installed:

```bash
bash test/all
```

On macOS, use a Bash 5 installation explicitly if the system Bash is older. The suite uses temporary fixtures and mocked publishing/SSH commands; it does not install anything. The source directory is sufficient; no desktop checkout or engine binary is needed for these tests. The Linux suite passed, followed by strict Swift formatting, 402 debug tests, 396 release tests and ad-hoc app assembly on an M4 Pro. The debug simulation also launched successfully; see the [recorded validation and limits](docs/validation.md#local-evidence).

The Swift package requires macOS 15+ and Swift 6.2+. Follow [validation.md](docs/validation.md) for Xcode checks and a simulation-only review. App packaging additionally requires an authenticated engine archive; see [the extraction prerequisites](docs/extraction.md#inherited-prerequisites-and-open-issues) before following the inherited [packaging guide](Packaging/README.md).

## Review and next steps

Start by reviewing the [standalone adaptations against the unchanged extraction](https://github.com/omacom/omarchy-mac-installer/compare/0d6f8661a5ad7b263d6167f0afea7cae033419a0...main). The original commit mapping and validation evidence are linked below.

Changes to `main` go through pull requests with approval from someone other than the author. The [CI checks](docs/validation.md#continuous-integration) cover portable tests, Swift formatting and both Swift build configurations. Define the Linux image and shared package inputs in separate changes. Physical installation qualification follows the assembled image/package integration.

- [Extraction provenance and boundaries](docs/extraction.md): original revision, preserved history, inherited build issues and future shared-package integration.
- [Validation and macOS handoff](docs/validation.md): what has been checked and what remains.
- [Simulation guide](Development/Simulation.md): debug-only UI scenarios that avoid real installation.

The preserved release and cutover tools target the original publisher's infrastructure. They are not a release procedure for Omacom. Publishing, signing, privileged helper registration and physical installation require separate preparation and authorization.

## License

The original [MIT license](LICENSE), copyright notice and Git attribution are retained. External engine dependencies retain their own licenses; their exact revisions are recorded in `Engine/source-lock.json`.

## Linux image producer

The Linux image-builder source is maintained in `image-builder/`. See [source ownership, provenance and checks](docs/image-builder.md).
