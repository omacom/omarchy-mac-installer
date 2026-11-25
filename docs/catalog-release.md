# Mac catalog release

`.github/workflows/mac-catalog.yml` signs and publishes a channel catalog that pins one uploaded image, its engine and its `installer_data.json` by digest. It is a reusable workflow (`workflow_call`) for the edge publish trigger and can be run by hand (`workflow_dispatch`). It writes `edge` from an edge image, `rc` from an rc-lane image (built from the RC channel for a release tag and named from it, `omarchy-mac-4.5.0-rc2-r1-<date>-<package set id>`) and `stable` from a stable-lane image (built for a final tag, `omarchy-mac-4.5.0-r2-<date>-<package set id>`); each needs passing VM evidence for the payload's sha256. An rc or stable name carries no image id, so catalog-rc and catalog-stable are also tied to the image itself: the evidence must record that image (`image`), the payload must be its zip for the lane (`omarchy-<date>-aarch64-apple-silicon-mac-<channel>-os-package.zip`, the name's date), and the payload, its parts and `installer_data.json` must sit in that image's own release. `prepare` and the signing job's `check-evidence` both refuse anything else, such as an edge payload and its evidence under an rc name. Stable catalogs come from this workflow too: the release pipeline calls it with `channel: stable` for a final tag (stable r2 was first signed that way in [run 41969347232](https://github.com/omacom/omarchy-mac-installer/actions/runs/41969347232), 2026-11-05), and a `workflow_dispatch` with `channel=stable` re-signs a published stable image with new pins ([run 44647538695](https://github.com/omacom/omarchy-mac-installer/actions/runs/44647538695) moved stable r2 to engine `v0.9.2-omarchy.20261106.0f73bea79639` on 2026-11-20). `.github/workflows/mac-promote.yml` (see [Promotion](#promotion-parked), parked) can also write rc and stable, by re-signing another channel's catalog. Both sign through `.github/workflows/mac-catalog-sign.yml`, the only workflow that reads the catalog key.

## The Omacom catalog key

- Ed25519, the format `scripts/catalog-signing.swift generate` writes: 32 raw private-key bytes; the public half is the 32 raw bytes of `Release/trust-root.ed25519.pub`.
- Public key fingerprint (pinned in `Release/release.json`): `sha256:bb914ffba2a74d05fd8d57013999376654ff9d7489929c76996a7f7716f27ea3`.
- The private key is the secret `CATALOG_SIGNING_KEY` (base64 of the 32 bytes) of the `catalog-release` environment of omacom/omarchy-mac-installer. The environment deploys from `main` only, needs the owner's approval for every run, and administrators cannot bypass it.
- The owner keeps a backup: the same base64 text as the Keychain generic password `omacom-catalog-signing-key` (account `omarchy`) on the owner's Mac, copied into 1Password. `catalog-signing.swift sign-keychain omacom-catalog-signing-key ...` signs with it locally (break glass); Keychain asks the owner first.
- No other copy exists. Rotating the key means a new trust root, a new `release.json` and a new app build: apps already shipped keep trusting only the old key.

## Jobs

| Job | Holds | Does |
| --- | --- | --- |
| `prepare` | no secrets, `contents: read` | runs the catalog tests, refuses unless the evidence record passed for the image's exact sha256, downloads every pinned artifact (or part) and checks its size and sha256, checks the new sequence follows the published one, writes the unsigned `catalog-<channel>.json` |
| `sign` (`mac-catalog-sign.yml`) | `catalog-release` (owner approval), `contents: read` | checks the catalog is the one `prepare` wrote and re-checks the evidence, signs with OpenSSL 3, wraps the envelope with `publish-channels envelope` and verifies it against `Release/trust-root.ed25519.pub`. A dry run publishes to a scratch directory on the runner and keeps only the log |
| `publish` (`mac-catalog-sign.yml`) | `contents: write`, no catalog signing key; with `catalog-hosts` naming `r2`, the `publish` environment's R2 key; skipped in a dry run | verifies the envelope again, checks every host in `catalog-hosts` (refusing a sequence that does not follow the one it serves) before writing any, then replaces the catalog on each in order and reads it back |

`scripts/catalog_release.py` implements every step; `scripts/tests/test_catalog_release.py` drives it as the workflows do (round trip, evidence refusals, signing refusals, hosts, dry run, promotion). `Tests/OmarchyAppleInstallerTrustCoreTests/CISignedCatalogTests.swift` checks the app accepts an envelope the CI path signed.

`mac-catalog-sign.yml` is `workflow_call` only. Its caller uploads the artifact `catalog-unsigned` (the file `channel`, `catalog-<channel>.json` and the gate's inputs) and names the gate: `evidence` re-runs `check-evidence` on `evidence.json`; `promotion` re-runs `check-promotion` on `source-channel` and `source.signed.json`.

## Callers

Only workflows of omacom/omarchy-mac-installer may call it (the `prepare` job runs only there and `catalog_release.py` refuses another `GITHUB_REPOSITORY`: a caller elsewhere would check out its own code with its own token). Call with `secrets: inherit`: GitHub requires the caller to pass even an environment secret, which otherwise resolves to empty and the signer refuses. The caller must grant `contents: write`. A pipeline in another repository dispatches this workflow instead.

## Inputs

All JSON inputs are strict: unknown keys are refused. Digests are 64 lowercase hex characters without a prefix. Every URL is https, without query, fragment or escapes, ends with its `fileName`, and lies under an immutable release location: `https://mac.omarchy.org/v1/releases/` or `.../v1/engine/`, or an asset of a release of omacom/omarchy-mac-installer whose tag does not start with `catalog-`.

- `image`: `omarchy-mac-<channel>-<YYYY.MM.DD>-<12 hex package set id>-<12 hex image id>`, the name `mac-image-check name` gives, whose image id must be the first 12 hex digits of `payload`'s `sha256`. A name without the image id, the format of images built before it was added (their releases and the catalogs pinning them are live), is still accepted. It becomes every model's `evidenceRevision`.
- `payload` (ticket 07's upload output for the image zip): `{"url", "fileName", "sizeBytes", "sha256", "parts"?}`. `parts` (2 to 16, for a host with a file size limit) is a list of `{"url", "fileName", "sizeBytes", "sha256"}` in order; their sizes sum to `sizeBytes` and their bytes join into `sha256`. The whole-file `url` is informational when parts are given.
- `installer-data`: the image's `installer_data.json` as `{"url", "fileName", "sizeBytes", "sha256"}`.
- `engine`: `{"version", "url", "fileName", "sizeBytes", "sha256", "downstreamRevision"}`, optionally `asahiInstallerTag`, `asahiInstallerRevision` and `asahiInstallerDataRevision`, which default to `Engine/source-lock.json`.
- `installer`: `{"minimumVersion", "latestVersion", "downloadURL"}`, the app compatibility block. Required: every engine since `.18` sends recommendation fields only app 2.1.0 and later decode, so `minimumVersion` must be 2.1.0 or later; `downloadURL` is under `.../v1/installer/` or a non-catalog release of this repository. `mac-mirror.yml` (`artifact: app`, `version`) copies a published app release's dmg and its `.sha256` to `.../v1/installer/<version>/`, once, after the GitHub release is published.
- `evidence`: the VM evidence record below.
- `dry-run`: defaults to true.

## VM evidence record

The VM acceptance job (`mac-acceptance.yml`) writes and attests it; the upload (`mac-upload.yml`) and this workflow consume it.

```json
{
  "image_sha256": "<sha256 of the image zip, 64 lowercase hex>",
  "result": "pass",
  "scenarios": [
    {"name": "first-boot", "result": "pass"},
    {"name": "conversion", "result": "pass"},
    {"name": "second-boot", "result": "pass"}
  ],
  "harness_commit": "<40-hex omarchy-mac-pkgs commit of tools/acceptance/mac-image>",
  "runner": "<runner label or host description>",
  "run_url": "https://github.com/<owner>/<repo>/actions/runs/<id>"
}
```

- `result` is `pass` or `fail`; each scenario's `result` is `pass`, `fail` or `skip`. Scenario names are the harness's (`tools/acceptance/mac-image/run --list`).
- A publish is refused unless `image_sha256` equals the payload's `sha256`, `result` is `pass`, no scenario failed, and `first-boot`, `conversion` and `second-boot` (the harness's `fresh-install`) all passed.
- Other keys may be added and are ignored here.
- The record is trusted as passed in: the workflow checks its shape and digest, not its origin. When chained (ticket 09), it must come from the acceptance job of the same run; run by hand, the owner's approval of the `sign` job is the check that it is genuine. Binding it to a verified acceptance run (fetching `run_url`'s artifact) is left to tickets 06 and 09.

## Hosts

`catalog_release.py publish --host` selects where catalogs go: one host, or a comma list written in order (`r2,github-release`: the catalog base, then the stopgap that released apps read). Every host is checked before any is written, and one already serving the envelope is skipped. Every host serves `catalog-<channel>.signed.json`, the envelope `publish-channels envelope` writes, and is the only mutable object.

- `github-release` (the stopgap): the asset `catalog-<channel>.signed.json` of the prerelease `catalog-<channel>` of the repository named by `INSTALLER_CATALOG_STOPGAP_REPO`, created on first publish (never marked latest). A publish uploads the new envelope as `catalog-<channel>.signed.json.next`, deletes the old asset only once that upload is on the release, and renames the new one into place, so a failed upload leaves the old catalog served and the only gap is one rename. If the rename fails, the staged `.next` asset stays and every later publish refuses until someone renames it into place or deletes it by hand, so an older catalog can never slip into the gap. A retry of an envelope that is already live succeeds without publishing. It is read back from `https://github.com/<repo>/releases/download/catalog-<channel>/catalog-<channel>.signed.json`.
- `scratch:DIR`: a directory, for dry runs and tests.
- `r2` (the catalog base): `catalog-<channel>.signed.json` under `INSTALLER_CATALOG_BASE` (`https://mac.omarchy.org/v1/`), key `v1/catalog-<channel>.signed.json` in the `omarchy-mac` bucket, replaced in place by one PUT and read back at the URL apps fetch. This is how the x86 side publishes its mutable files (`latest.json` in omarchy-iso, `omarchy.db` in omarchy-pkgs): no conditional write and no Cache-Control, with the publish job's per-channel concurrency group as the lock. A PUT that timed out and landed after a newer one leaves an older sequence served, and publishing the newer catalog again repairs it. The bucket lock must not cover these keys. The credentials are the `publish` environment's `R2_ENDPOINT`, `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`, passed by name from the release pipeline through `mac-catalog.yml` to the publish job, the only catalog job that reads them.

`catalog-hosts` (`mac-catalog.yml`, `mac-catalog-sign.yml`) defaults to `github-release` until the bucket lock's rules are confirmed to leave the catalogs replaceable; `r2,github-release` is then the default. Promotion (parked) keeps `github-release`.

The app compiled from `Release/` reads `mac.omarchy.org/v1`, so it does not see the stopgap catalogs. Until ticket 13, `mac-app-release.yml` (`APP_CATALOG_HOST: stopgap`) therefore builds every app with a `release.json` from `scripts/make-release-descriptor --stopgap`, naming each channel's stopgap catalog and the trust root in `Release/`; the release guard accepts exactly those URLs besides the catalog base. Ticket 13 sets `APP_CATALOG_HOST: base`, and apps built before then keep reading the stopgap releases, so those must stay in place until such apps are retired.

## Lane holds

The app installs with the engine its channel's signed catalog pins (each model's `engineDigest`), not the engine it bundles. So an rc or stable catalog must pin the engine `Engine/source-lock.json` pins (`validation_artifact.sha256`), or a new app ships with an older engine's bugs: installer 2.3.16 carried the fix for "native Recovery app differs from its engine manifest" while catalog-rc and catalog-stable still pinned `v0.9.2-omarchy.20261022.31425f17fb9b`.

`catalog_release.py check-engine` enforces it. The `prepare` jobs of `mac-catalog.yml` and `mac-promote.yml` run it and put its decision in the run summary before anyone is asked to approve; the signing job runs it again for both gates, before the key is read. Every model must pin one well-formed digest. For rc and stable it must be the lock's, read from the run's own checkout (the commit the run started from). Edge is exempt.

A rollback or a deliberate hold keeps another engine only when the owner names its exact digest in the `allow-engine-digest` input (`sha256:<64 hex>`) of `mac-catalog.yml` or `mac-promote.yml`. The input is refused for edge, and refused unless the catalog pins exactly that digest. The summary then reads `engine_gate=held`, with the catalog's engine and the lock's. Promotion carries the source's engine, so promoting an off-lock catalog needs its own exception for the target lane; the input never waives another gate, such as the refusal to promote a final tag built on rc. A lane held on an older engine is recorded below with its owner and reason. The hold ends only when a real (not dry-run) run re-signs the lane on the locked engine with no `allow-engine-digest` (a normal rc or stable run does this) and every host serving the lane reads the new catalog back; delete its row then, not before.

| Lane | Engine kept | Catalog (as observed 2026-11-20) | Owner | Since | Reason | Effect |
| --- | --- | --- | --- | --- | --- | --- |
| rc | `v0.9.2-omarchy.20261022.31425f17fb9b`, `sha256:5fea6b561e6fb82260f573bae099c70ba2e9998df56d38cf285987432320d4b5` (the lock pins `v0.9.2-omarchy.20261106.0f73bea79639`, `sha256:a60855716be5cea4f2113a368f784a5fe55a7034602f48fac8bb6e79f796fd7a`) | `catalog-rc.signed.json` sequence 1793801884 (2026-11-04T14:18:04Z), catalog sha256 `12e3bb17c93ca86d4eba4e4b842c6464cf80b7605f3651b921b613e5455bc8b5`; image `omarchy-mac-4.5.0-rc-r1-2026.11.04-f1738add15fc`; `installer.latestVersion` 2.3.14 | maralcbr | 2026-11-20 | Owner decision; stable carries the fix | rc installs can still fail at "Writing boot files" with "native Recovery app differs from its engine manifest", the failure reported in [#213](https://github.com/omacom/omarchy-mac-installer/issues/213) and fixed in engine 20261106 |

While the rc hold stands, any rc re-sign that keeps this engine must pass `allow-engine-digest=sha256:5fea6b561e6fb82260f573bae099c70ba2e9998df56d38cf285987432320d4b5`; one on the locked engine passes no input and ends the hold once published and read back.

Outside the workflows, nothing runs the gate for you. Before a break-glass signing with `catalog-signing.swift sign-keychain`, copy the catalog to `catalog-<destination channel>.json` (the gate reads the lane from the file name) and run `python3 scripts/catalog_release.py check-engine --catalog catalog-<channel>.json` on `main`, with `--allow-engine-digest` only for a recorded hold or an owner-approved rollback. Note that file's sha256, sign exactly that file, and before publishing by hand check the envelope carries it with `verify-envelope --expect-sha256 <that sha256>`.

Known gap: `main`'s lock can move after the owner approves a run, and the run still signs against the lock of the commit it started from; a re-run keeps that commit too, so a run started before this gate existed signs without it. Decline such approvals. The long-term fix is a per-lane record of the approved engine digest, checked again at publish time against current policy rather than the run's checkout.

## Running it

After the workflow is on `main`:

```bash
gh workflow run mac-catalog.yml -R omacom/omarchy-mac-installer -f dry-run=true \
  -f image=... -f payload='{...}' -f engine='{...}' -f installer-data='{...}' -f evidence='{...}'
```

The `sign` job waits for the owner's approval in the run page, in a dry run too: a dry run signs with the real key, then writes only to the runner's scratch directory.

## Rolling edge back

A signed catalog enables exactly the Macs its channel offers today. To re-sign edge on an image that cannot boot some of them, such as one without the M4 boot files, first qualify those Macs for `developer` in `scripts/supported-models.json`. Otherwise the catalog offers them, and the engine refuses their install before it changes the disk.

## Release checklist

Each catalog pins its own engine, so an engine in `Engine/source-lock.json` or in an app release reaches users only once their channel's catalog pins it. Installer 2.3.16 shipped with the AppleDouble fix for "native Recovery app differs from its engine manifest" (engine `v0.9.2-omarchy.20261106.0f73bea79639`), while catalog-stable kept pinning `v0.9.2-omarchy.20261022.31425f17fb9b` until it was re-signed on 2026-11-20 ([#213](https://github.com/omacom/omarchy-mac-installer/issues/213)).

- [ ] Every engine pin bump in `Engine/source-lock.json` ([Engine versions](engine-versions.md)) re-signs every channel, edge, rc and stable, each with `mac-catalog.yml` and that channel's own image.
- [ ] A lane that stays on the older engine is recorded in the pull request that bumps the pin: which lane, which engine it keeps, and why.
- [ ] App release notes claim an engine fix only for the channels whose catalogs already serve that engine. Check what each channel serves:

  ```bash
  for c in edge rc stable; do
    curl -fsSL "https://github.com/omacom/omarchy-mac-installer/releases/download/catalog-$c/catalog-$c.signed.json" |
      jq -r --arg c "$c" '.catalog | @base64d | fromjson | "\($c) app \(.installer.latestVersion) engine \([.models[].engineVersion] | unique | join(","))"'
  done
  ```

## Promotion (parked)

> **Parked.** rc and stable catalogs come from images built for their lane (`mac-release-pipeline.yml` with a release tag). Promotion re-signs another channel's image instead, so its runtime and channel stay the source's. rc and stable promotion waits for the kernel work. Run `mac-promote.yml` only as a dry run until the owner unparks it. A real promotion needs `dry-run` off, `confirm` set to `promote-<channel>` and `source-sha256` pinning the source catalog, then the owner's approval in `promote` and again in `catalog-release`.

**What a promotion carries.** A promotion keeps every pin and installer requirement of the edge catalog it re-signs, for each Mac the target offers (`promote_catalog` in `scripts/catalog_release.py`). Promoting today's edge catalog to rc or stable would therefore ship the unified Aurora image (on an edge image built after omacom/omarchy-pkgs#3025, aura's Stage 2 with `m1n1-aurora`'s raw Stage 1 for the Neo, and aura's Mach-O Stage 1 on M4 and later; on an earlier one, `m1n1-aurora` for both stages; the Aurora U-Boot and Mesa, `speakerguardd`), the edge `omarchy-dev` runtime, the MacBook Neo, and the installer minimum the source catalog carries, all unchanged for every board the target channel offers. A promotion does re-read `supported-models.json`, for one purpose: a board the target has not qualified for is dropped from the promoted catalog, with edge's preview notes, and `check_promoted_models` refuses a promotion that keeps one, adds a board the source lacks, changes what a surviving board pins, or would leave the channel with no Mac at all. It narrows the set of boards and nothing else. Accepting it is a maintainer decision to record before promotion is unparked.

`.github/workflows/mac-promote.yml` (`workflow_dispatch` only) writes a signed `rc` or `stable` catalog over exactly the bytes a published channel catalog pins. Nothing is built and nothing new is uploaded: the image, engine and `installer_data.json` keep their URLs, sizes and digests; only `sequence` (the current Unix time), `issuedAt` and `channel` change, and the Macs and preview notes the target does not carry are dropped. Promotion goes forward only: edge to rc or stable, rc to stable, and `check_same_pins` refuses a source whose own `channel` is not earlier in that order.

| Job | Holds | Does |
| --- | --- | --- |
| `prepare` | no secrets, `contents: read` | fetches `catalog-<source>.signed.json` from the catalog host, verifies it against `Release/trust-root.ed25519.pub` (and `source-sha256` when given), refuses a source whose models pin more than one image or anything outside the immutable release locations, copies it with a new sequence, issue time and target channel, downloads every pinned artifact (or part) to check it still serves its digest, checks the new sequence follows the target channel's published one, and uploads `catalog-unsigned` with the source envelope. A real run without the explicit inputs is refused here |
| `approve` | `promote` (owner approval), no permissions | the promotion decision, made after reading `prepare`'s summary; refuses a real run without the explicit inputs again |
| `sign`, `publish` | `mac-catalog-sign.yml`, gate `promotion` | re-verifies the source envelope and that the new catalog differs from it only in `sequence`, `issuedAt`, `channel` and the dropped Macs and preview notes, signs in `catalog-release`; a dry run writes to scratch and keeps only the log, a real run publishes to the `catalog-<channel>` prerelease |

Why it signs through `mac-catalog-sign.yml` instead of holding the key itself: the key stays a secret of one environment (`catalog-release`) read by one job definition, so rotating it, auditing it or tightening that job happens once, and the `promote` environment holds no secrets at all. The cost is two approvals per promotion run: `promote` approves promoting this catalog, `catalog-release` approves using the key.

The `promote` environment deploys from `main` only and needs the owner's approval; administrators cannot bypass it.

```bash
# Dry run (the default): note source_catalog_sha256 in the prepare summary.
gh workflow run mac-promote.yml -R omacom/omarchy-mac-installer -f channel=rc -f source=edge
# Real promotion, once unparked:
gh workflow run mac-promote.yml -R omacom/omarchy-mac-installer -f channel=rc -f source=edge \
  -f dry-run=false -f confirm=promote-rc -f source-sha256=<source_catalog_sha256>
```

A promoted catalog keeps the source's `evidenceRevision` (the image name, `omarchy-mac-edge-...`): it names the image that passed VM acceptance, not the channel. Released apps read the stopgap catalogs (see [Hosts](#hosts)), so a promoted rc or stable catalog reaches them once it is published.
