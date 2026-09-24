# Linux image builder

The installer application and its Linux image producer are reviewed in this repository. The producer source lives in `image-builder/`; the macOS application/engine layout is unchanged. Runtime changes remain in `omacom/omarchy-mac:quattro-upstream`, and package recipes remain in `omarchy-mac/omarchy-pkgs-aarch64`.

## Import and provenance

`image-builder-import.json` records original file hashes/modes, the source tree and commit `dbc46807e3c341b82fa518c2db2416464a087dd3` from the previously tested builder. `image-builder/archiso` retains gitlink `424e78130db2af6c1ceb55b442d7914b1109ff2b`, declared in the root `.gitmodules`. Initialize it with `git submodule update --init image-builder/archiso`.

The imported nightly publication workflow is deliberately excluded. Historical release/sign/upload commands and documentation remain attributed source, not configured publication destinations for this fork. No image publishing workflow is activated by this import. Historical validation records describe their original source and are not qualification of the relocated producer.

The builder now distinguishes its logical source root from the enclosing Git root. Source paths stay builder-relative; Git history/status remain scoped to those paths. Frozen producer inputs preserve the repository prefix and copied Git metadata. The admission-receipt schema and authorization checks are unchanged; its paths identify logical input groups. A relocated producer has new identities; do not relabel prior checkpoints or reuse build-25 qualification for changed sources.

## Checks and entrypoints

Run `bash test/all` for portable installer checks and `bash image-builder/test/all` for VM-free producer checks. The latter needs Bash 5, Python, Git, jq, libarchive tools, GnuPG, and the filesystem/archive utilities installed by the source-check CI job. Its lease fixtures need a private temporary directory under a trusted user-owned parent, rather than a world-writable `/tmp` ancestor. Tests create fixture Git commits; disable signing for the test process only if local Git configuration otherwise signs every commit.

The producer entrypoint is `image-builder/bin/omarchy-iso-make`; it resolves its source root from its own location. See the imported builder documentation for explicit private candidate inputs. Building, signing, booting a VM, and publishing are separate operations and are not part of the ordinary PR source-check job.

## Qualification boundary

This source relocation and the coordinated runtime/package refactor require a newly pinned candidate. Before physical testing, validate package ownership/dependencies and VM installation/second boot. Scott's subsequent M3 qualification includes installation, encrypted reboot, recovery-key unlock and factory reset through another owner setup. Keep the existing pilot frozen and its tested build available. No merge or production release is authorized by source-check success.
