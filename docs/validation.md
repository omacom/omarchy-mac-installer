# Standalone installer validation

## Candidate boundary

This candidate extracts source and adjusts repository-relative paths. It does not alter the Swift runtime, Python engine overlay, source lock, release trust configuration or Linux payload. The provenance record identifies the original source; record `git rev-parse HEAD` whenever testing a candidate.

The Linux source checks are `bash test/all`. They compile Python, check shell syntax, run the engine and catalog unit tests, and exercise the preclean, package-plist, branding and publication scripts with fixtures. They do not qualify a macOS app bundle, engine binary or physical installation.

## Local evidence

The extraction was checked on 2026-09-20 as the non-root `scott` user on aarch64 Linux with Python 3.14.7. The portable suite passed: 37 engine tooling tests, 104 engine overlay tests and 17 catalog tests (158 Python tests), plus all existing preclean and three extracted packaging/publication shell tests. Python compilation and shell syntax checks passed.

The same source revision, `3f1e30bb265e77da04ce03533be0a7dddaa8c84c`, was then validated on Scott's M4 Pro (`Mac16,7`, arm64), running macOS 26.6.2 (25G83), Xcode 27.0 (27A266a) and Swift 6.4. XcodeBuildMCP 2.7.0 was staged inside the validation workspace from its official standalone archive, verified against the publisher's SHA-256 `bd724a2c0e6ffe027b3f46257e66d626149a64cd045f4867124bd683b3cf081a`.

| Check | Result |
| --- | --- |
| Strict Swift formatting | Passed |
| Debug Swift tests | 402 passed, 0 failed, 0 skipped |
| Release Swift tests | 396 passed, 0 failed, 0 skipped |
| Recorded journal fixture and blocked-host tests | Passed within both Swift suites |
| Development app assembly | Passed with the exact pinned inspection engine |
| Ad-hoc app and helper signatures | Deep/strict verification and reciprocal code requirements passed |
| Bundle structure | Executables, helper plist, release inputs and engine verified |
| Debug simulation launch | Running; Scott confirmed the simulation window is visible |

The app was assembled with a separate development copy of the release descriptor using an ad-hoc helper requirement. The tracked production descriptor, public key and source code were unchanged. No production signing credentials, helper registration or installation operation were used.

Swift 6.4 emits three capture warnings in inherited code: `PayloadPrefetch.swift:94`, `PayloadPrefetch.swift:523` and `InstallerSession.swift:635`. They concern inner weak captures inside an implicitly strong outer capture. They did not fail builds or tests, and the extraction does not change that runtime behavior.

The Python/shell suite was not repeated on macOS: its installed Python 3.9.6 and Bash 3.2 are below this runner's declared prerequisites. Its complete Linux result remains separately recorded. A full visual scenario walkthrough, complete native engine rebuild and physical installation qualification are still pending. Exact logs, app file hashes, isolated-copy results and transfer artifacts are retained alongside the local checkout in the extraction workspace's `evidence/` and `artifacts/` directories. Documentation-only follow-ups do not change the tested implementation.

## macOS build handoff

Scott has an M3 Air and an M4 Pro available with macOS. Either can be the primary build machine. Build once against the recorded revision and use the second machine for a separate UI or host-support check when useful; do not run duplicate qualification jobs for the same inputs.

1. Copy or clone the standalone repository and check out the candidate revision. Record `git rev-parse HEAD`, `sw_vers`, `uname -m`, `xcodebuild -version` and `xcrun swift --version` with the results. The package requires macOS 15+ and Swift 6.2+; use an Xcode toolchain meeting those requirements and XcodeBuildMCP.
2. Run the portable suite with Bash 5 and its listed dependencies.
3. Lint with Xcode's bundled formatter, then run both Swift configurations:

```bash
xcrun swift-format lint --strict --recursive Package.swift Sources Tests
xcodebuildmcp swift-package test --package-path "$PWD" --configuration debug
xcodebuildmcp swift-package test --package-path "$PWD" --configuration release
```

4. If those pass, use the existing `Development/Run Simulation.command` launcher for a debug-only UI review. Keep one app instance on the review machine. Follow [the simulation scenario matrix](../Development/Simulation.md), including unsupported hosts, failed downloads, changed plans, interrupted execution and Recovery handoff. Simulation uses dummy credentials; it is not a live install or an inspection of that Mac's actual eligibility.
5. Record failures and the exact test counts/logs. Verify the imported journal fixture is exercised and unsupported models remain rejected. Mac hardware support needs a separate real host inspection against the intended engine and signed catalog; simulation does not establish it.

These steps require no helper registration, real machine-owner credentials, production signing, disk changes or release publication. If a run asks to cross one of those boundaries, stop and review that as a separate operation.

## Before packaging or installation

For another app assembly, supply the exact authenticated inspection engine described in [extraction.md](extraction.md#inherited-prerequisites-and-open-issues) and review the resulting app structure and code-signing requirements. The initial ad-hoc assembly passed; a complete native engine rebuild remains a separate unvalidated path. The current publisher's catalog, public key and signing identity are not interchangeable with a new Omacom release identity.

An M3 Air may be a physical validation candidate only after its exact model, engine support and intended payload are checked. The M4 Pro is available for macOS development; `apple,j614s` remains explicitly blocked. No hardware restriction is relaxed by this extraction.

Physical installation needs its own backup, recovery, image/package revision and authorization record. Later release qualification must cover installation, Recovery boot, first boot, update, encrypted-install behavior and recovery on the assembled shared stack. Passing this source suite does not satisfy those checks.
