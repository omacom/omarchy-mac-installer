# Engine versions

The installation engine is pinned in one place: `Engine/source-lock.json`. `downstream_overlay.version` names it, and `validation_artifact` holds its archive name, size and SHA-256. The engine's identity is its digest; the name is a label for people and release tags.

## Names

New engines are named `v0.9.2-omarchy.<YYYYMMDD>.<content id>`, for example `v0.9.2-omarchy.20261014.3f9a1c2e07b4`.

- The date is the day the name was chosen. It only helps people see which engine is newer.
- The content id is the first 12 hex characters of a SHA-256 over the lock's build inputs: the upstream installer and metadata, the downstream overlay (its files, patch, capability and modes, but not its name), the incremental base and upstream delta, the build inputs, toolchain and recipe, and the engine metadata digest. Two branches that change the engine differently get different names, so nobody has to coordinate the next number, and a rebase that changes no input keeps the id.

`v0.9.2-omarchy.50`, the last engine named by number, keeps its name only while the lock holds the inputs it was built from: `Engine/engine_pin.py` freezes their content id in `LEGACY`. `check` refuses every other name that isn't a well-formed dated one, such as a hand-picked `.51`. So the next engine change must take a dated name. Nothing compares or parses names: the catalog and the app pin the exact archive by digest, and every catalog requires app 2.1.0 or later whatever its engine is called.

An engine that needs a newer app says so in the lock: `downstream_overlay.minimum_app_version` (2.3.0 since the engine stopped carrying the restore-image decoder, which only app 2.3.0 and later ship and pass). It is a build input, so it is part of the content id. `scripts/make-unsigned-catalog.py` refuses a lower `installer.minimum_version`, and `catalog_release.py` refuses a lower `installer.minimumVersion` for the engine whose SHA-256 is the lock's. Other engines keep the 2.1.0 floor.

## Who reads the pin

Nothing else in the repository carries a copy of the pin; everything reads the lock:

- the app: `OmarchyEnginePinPlugin` generates `ValidationEngineArtifactLocator`'s version, file name, digest and size from the lock at build time;
- `Packaging/read-validation-engine.py`, which `Packaging/build-app.sh` and `Engine/prepare-engine.py` (and so `.github/workflows/mac-app-release.yml`) use;
- `Engine/rebuild-python-overlay.py`, `scripts/assemble-candidate-v8.sh` and `scripts/cutover-wizard`;
- `.github/workflows/mac-release-pipeline.yml`, whose config adds only the engine's `downstreamRevision`;
- `scripts/make-unsigned-catalog.py`.

`Engine/tests/test_engine_pin.py`, run by `./test/all`, fails if any file outside the lock, docs and evidence repeats the engine's digest, or if `engine_pin.py check` finds a problem: a name that is neither dated nor `LEGACY`, an impossible date, or a content id that no longer matches the lock's build inputs. `scripts/make-unsigned-catalog.py` also refuses an engine archive whose size or SHA-256 isn't the lock's.

## Changing the engine

1. Change the overlay and update its digests in the lock, as before.
2. `python3 Engine/engine_pin.py name --write` names the engine and its archive in the lock.
3. Rebuild the engine (`Engine/rebuild-python-overlay.py`) and record its `size_bytes` and `sha256` in `validation_artifact`.
4. `./test/all`.
5. Before the release pipeline runs on it, publish the archive as this repository's `engine-<version>` release. `catalog_release.pipeline_engine` builds the engine URL from that release name, and `mac-catalog.yml`'s `--verify-remote` refuses a missing or different archive. When mac.omarchy.org hosts engines, that URL changes in `pipeline_engine`, not in the config.

When two branches that both changed the engine meet, Git reports a conflict only in the lock (and in any overlay source both changed). Resolve the overlay sources and the lock's build inputs, then run steps 2 to 5 again: the merged engine gets a new content id, so it can never be mistaken for either branch's engine.
