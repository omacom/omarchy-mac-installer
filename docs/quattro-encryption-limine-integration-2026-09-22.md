# Standalone app encryption/Limine integration preparation

Base: M3 checkpoint `23dc8de`, following validated app `6f45ad29f75c606a1d0c0657f9f9809cfd23c299`. The original `fix/m3-inspection-engine` branch remains unchanged. See [physical evidence](m3-validation-2026-09-22.md).

The net comparison from extraction source `4db862da7a0957758c504c5a3e041202019dbabf` to the pinned runtime ceiling changes 16 app files, solely through #195/#197/#198 version bumps and retirement of the RC-Aurora channel. There is no engine delta. Exclude those unrelated channel/publication changes. `InstallConf` encryption choice and ESP writer already exist here; Linux conversion and owner re-key belong to the runtime and boot package.

Retain standalone path adaptations, release trust, unsupported-host rejection and the verified `.17` engine (SHA256 `ecb61645a9c75ba733425fb300b8b53b09f9dbc297a86acce1e0ee41f36e32e5`). The image must preserve the `omarchy/install.conf` staging directory and honor the existing encryption contract. Do not copy dirty prepared-install work from the old monorepo.

Next app work is contract validation against the coordinated image/private catalog. Preview whether anything actually invalidates the app or engine before rebuilding. Run portable checks and applicable macOS debug/release, packaging/signature and unsupported-host checks for any changed candidate. Physical installation remains a separate concrete owner-reviewed operation.

This branch is a local source-preparation branch. No functional port or new build has been performed. The complete dependency inventory and 168-file disposition map are in the desktop repository, branch `integrate/quattro-encryption-limine`, under `docs/quattro-encryption-limine-{integration,sources,files}-2026-09-22.{md,json,json}` (three separate files). The local desktop worktree is `/home/scott/code/omarchy-worktrees/quattro-encryption-limine`.

The proposed runtime source ceiling is `maralcbr/omarchy-mx-mac` open PR #220 at `d418ab7f95e8ba447df4fb368ddd838a5ffc7943`, including merged #219 at `5e7a409fae1ddc17433d9408e15153b4fe813f7b`. The package/image reference is open `maralcbr/omarchy-pkgs` PR #194 at `68a61cef1aba768c6aae20a0feda2a42e19de6e8`. These heads were verified with GitHub API on September 22. Preserve pins and original attribution; neither open PR is a qualified downloadable candidate.

Keep candidate packages/images private and signed under the existing build-input policy. Preserve the active desktop, unrelated dirty worktrees, installed-user feed and repository trust. No publication, remote update, physical disk operation or boot-policy change is part of this preparation.
