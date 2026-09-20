# Installer extraction

The proposed home is `omacom/omarchy-mac-installer`. This is a local source candidate extracted from [maralcbr/omarchy-mx-mac at 4db862da7a0957758c504c5a3e041202019dbabf](https://github.com/maralcbr/omarchy-mx-mac/tree/4db862da7a0957758c504c5a3e041202019dbabf/apps/omarchy-apple-installer). It has not been published or qualified for installation.

## Provenance

The installer directory becomes the repository root. The source repository's MIT [LICENSE](../LICENSE), original icon, three installer shell tests, their shared support file, and the recorded journal fixture are also retained. The exact path list and tool revision are in [extraction-source.json](extraction-source.json).

The import retains 114 relevant commits, including their original authors, committers, dates and messages. Commit IDs change because the repository trees and ancestry change; original commit signatures cannot authenticate rewritten commits. [extraction-commit-map.txt](extraction-commit-map.txt) maps each retained original commit to its extracted commit. The original repository remains the authority for original signatures and omitted desktop history.

The extraction base is `0d6f8661a5ad7b263d6167f0afea7cae033419a0`. All 206 files and their Git modes at that revision were compared with the selected source paths and match exactly. Subsequent commits contain standalone path adjustments and documentation so reviewers can separate those changes from the import.

The installer work and its recorded Git attribution are preserved, including Marcelo Alcantara's contributions and upstream Omarchy contributors. The root copyright notice remains unchanged. Asahi engine sources are external dependencies identified by [Engine/source-lock.json](../Engine/source-lock.json); their own licenses and notices must accompany distributed artifacts.

## Reproducing the import

Use the recorded source revision, not a moving branch. In a dedicated source clone, create a temporary `export/installer` branch at that revision, then export the listed paths with `git fast-export --show-original-ids --signed-tags=strip --tag-of-filtered-object=rewrite --reencode=no --use-done-feature export/installer -- PATHS...`. All paths must be available if using a sparse checkout.

In a separate empty Git repository initialized on `extract/installer`, feed that stream to the recorded `git-filter-repo` version:

```bash
python3 /path/to/git-filter-repo --stdin \
  --preserve-commit-hashes --preserve-commit-encoding \
  --path-rename apps/omarchy-apple-installer/: \
  --refname-callback 'return b"refs/heads/extract/installer"' < installer.fast-export
```

Compare the resulting tree with `extraction_base_tree` in the source record. This operation belongs only in a disposable extraction repository; never filter the shared desktop checkout.

## Boundaries of this change

This split preserves the current application identity, support gates, engine lock, release catalog URLs and public trust root. It does not make the inherited release configuration an Omacom release. Historical design notes and release helpers retain references to their original environment; those references are not setup instructions for the new repository.

The next integration should separately define the shared Linux image and package inputs, encrypted-install handoff, kernel selection, release ownership and validation evidence. In particular, the Linux payload must supply the shared runtime, settings and `omarchy-mac` package before hardware setup. The macOS app and the Linux image remain separate build products.

## Inherited prerequisites and open issues

- `Packaging/build-app.sh` requires the authenticated `installer-v0.9.0-omarchy.14.tar.gz` archive, SHA-256 `9e9277384b6c9e8b269cc79b1b24df7bfcdcbb898a596a677b74d1d18050aebe`, in `Engine/artifacts/`. This binary is not tracked. This is the inspection engine used before any downloads. The source lock records installation engine `.17`; those versions deliberately differ, as documented in `ValidationEngineArtifact.swift`. For the macOS assembly check, the exact `.14` archive was recovered from the [original published app](https://downloads.aicodelabs.com.au/installer/previews/20260918-e1b8abc05135/Omarchy-MX-Mac-Installer.zip) and verified against the pinned size and digest before use. No version or trust check was relaxed.
- `Engine/build-locked-engine.sh` still reads `downstream_overlay.metadata.path`, while the current source lock omits it and the verifier rejects that field. A complete native engine rebuild has not been validated. The authenticated-base Python overlay rebuild is a separate path; its tests are included.
- The native engine lock records exact tools and machine-specific paths. Keep those reproducibility constraints until deliberately reviewed; silently substituting another toolchain would not validate the recorded artifact.
- Release scripts retain the original publisher's endpoints, signing identity and historical defaults. Creating the new repository does not establish new signing or publishing authority. The old cutover and candidate helpers are retained for history and require review before operational use. Their repository paths now resolve inside this checkout; the cutover helper accepts an external image via `OMARCHY_OS_PAYLOAD`, otherwise retaining its sibling `omarchy-iso` convention.
- The exact model `apple,j614s` remains blocked. Availability of an M4 Pro for development does not qualify it as an installation target.

See [validation.md](validation.md) for the passing Linux and macOS checks and remaining qualification work.
