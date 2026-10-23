# Mac release pipeline

`.github/workflows/mac-release-pipeline.yml` runs one edge image from build to a signed edge catalog. It is the one Aurora image every supported Mac installs, M1, M2, M3 and the MacBook Neo alike ([hardware scope](../image-builder/README.md#hardware-scope)), and the catalog pins it for all 35 boards. `edge-dispatch.yml` starts it (`dry-run=false`, on `main`) when an edge publish changes a package the image installs. You can also start it by hand, and a manual run is a dry run unless you say otherwise. The only manual step is the owner's approval of the catalog signature.

| Job | Workflow | Runs when | Gates it keeps |
| --- | --- | --- | --- |
| `plan` | here | always | every run starts only from `main`, dry runs included (the image builds only from `main`), never for a pull request |
| `build` | `mac-image.yml` | after `plan` | no secrets; the native Recovery app built on macOS (`recovery-app.yml`) and required in the image; the release guard; a build attestation on the zip |
| `acceptance` | `mac-acceptance.yml` | after `build` | main only, never for a pull request; the zip's sha256 and build attestation checked; the VM harness's `fresh-install` scenarios on the VM runner ([runner](mac-acceptance-runner.md)); fails unless the evidence record for the zip's sha256 passes; a passing record is attested |
| `catalog-inputs` | here | acceptance passed | `.github/mac-release-pipeline.json` must name a published app release (a dry run only notes it), before anything is public |
| `upload` | `mac-upload.yml` | acceptance `result == pass`, catalog inputs read | `publish` environment (main only); evidence and attestation re-checked; never overwrites; publishes the draft as a prerelease (never latest) on a real run |
| `catalog` | `mac-catalog.yml` | real run, all of the above succeeded | evidence re-checked for the payload's digest; every URL serves its pinned bytes; `catalog-release` (owner approval, main only) signs |

A dry run uploads to a `dryrun-` scratch release and deletes it, and signs no catalog. A catalog dry run needs files the app can download, so run `mac-catalog.yml` by hand for that.

## What passes between jobs

- `build` outputs `name` (also the image artifact's name), `payload` (the zip's file name) and `sha256`. Inside it, the macOS `recovery` job (`recovery-app.yml`) builds the native Recovery app and keeps it as the GitHub artifact `native-recovery-input` with its inventory digest as an output; the image job checks the downloaded app against that digest and builds with `--recovery-app`, so every image the pipeline (and `edge-dispatch.yml`) builds declares `recovery_gui: omarchy-native-v1` and carries `esp/aurora/recovery/Omarchy Installer.app`.
- The image never leaves Namespace: `build` (on Namespace's Apple-backed Linux ARM64 runner), `acceptance` and `upload` (on Namespace runners) pass it through Namespace's artifact storage (`namespace-actions/upload-artifact` and `download-artifact`), about 10 s for the 3.4 GB zip each way. No pull request reaches a Namespace runner: acceptance's VM job waits for its GitHub-hosted `guard`, and the build and upload jobs carry the same refusal in their own `if:` (the build also requires `main`). The small artifacts (evidence, logs) stay GitHub artifacts.
- `acceptance` uploads the artifact `acceptance-evidence`, holding `acceptance-evidence.json` ([format](mac-acceptance-evidence.md)), and outputs `result`, `evidence-artifact` and `evidence-run-id` (this run). It also keeps the harness's logs as `acceptance-harness-logs`. The pipeline's `runner` input (empty by default) overrides the VM job's runner.
- `upload` downloads the image and evidence artifacts from this run. After a real upload it outputs `payload` and `installer-data` (the catalog's inputs, built from the files it uploaded and verified) and `evidence` (the record it checked).
- `catalog-inputs` outputs the catalog's `engine` block (the engine `Engine/source-lock.json` pins, with the config's `downstreamRevision`) and the `installer` block of `.github/mac-release-pipeline.json`.

## Where the jobs run

Namespace builds and publishes the image. Namespace is a Distinguished Corporate Patron of the Omacom Foundation, giving $100,000 a year in compute for three years for ARM builds and QA ([announcement](https://omarchy.org/news/2026/10/namespace-joins-as-a-distinguished-corporate-patron/)), so its runners are our CI, like GitHub's. `build` runs on Namespace's Apple-backed Linux ARM64 runner. `upload` runs on a Namespace runner too, with `contents: write` in the `publish` environment (and, once ticket 13 adds it, the R2 token). It is the first job with write access that runs off GitHub's hosted runners. The guards and the jobs that hold `id-token`, `attest` in `mac-image.yml` and in `mac-acceptance.yml`, stay on GitHub-hosted runners.

So when acceptance and upload run `gh attestation verify --deny-self-hosted-runners`, they check that the attestation was signed by `mac-image.yml` on `main` in its GitHub-hosted `attest` job, over the digest `build` reported. They do not check that the zip was built on a GitHub-hosted runner: Namespace built it.

## Secrets

The only secret is `CATALOG_SIGNING_KEY`, an environment secret of `catalog-release`. The pipeline passes it to `mac-catalog.yml` by name and never uses `secrets: inherit`. GitHub delivers an environment secret to a called workflow only when the caller passes it, and only to the job that runs in that environment. A throwaway probe (run 38553165283) confirmed this: passed by name or with inherit, the secret reached the job in the environment but not a job without it, and not passed, it reached neither. A second probe (run 38558712893) checked one more level of nesting, which is what happens once #69 moves signing into `mac-catalog-sign.yml`: when the pipeline passes the key by name and `mac-catalog.yml` forwards it, by name or with `inherit`, the key reaches the sign job. When the pipeline passes nothing, it stays empty. `mac-catalog.yml` can only forward what it was given, which here is the key alone. When ticket 13 puts an R2 token in `publish`, the pipeline's `upload` job must pass that token by name in the same way.

The catalog takes the evidence record the upload checked, and the upload took it from this run's acceptance artifact, with the attestation `mac-acceptance.yml`'s `attest` job made for it. The acceptance workflow uses no secrets.

## `.github/mac-release-pipeline.json`

- `engine`: only `{"downstreamRevision"}`. The run's engine is the one `Engine/source-lock.json` pins, the only place the engine is pinned ([Engine versions](engine-versions.md)); `catalog-inputs` adds its version, file name, size and digest, and its URL in the `engine-<version>` release (`catalog_release.pipeline_engine`). Ticket 13 moves the archive to `mac.omarchy.org/v1/engine/`.
- `installer`: the app compatibility block, `{"minimumVersion", "latestVersion", "downloadURL"}`: app 2.3.0, the floor `Engine/source-lock.json` sets for its engine (`downstream_overlay.minimum_app_version`), and its published `.dmg`. A `null` block (no app release at all) makes `catalog-inputs` refuse on a real run with a message saying so, before the upload, so nothing is made public. `minimumVersion` 2.3.0 makes every edge user, M1, M2 and M3 included, update the app before their next install.

## New installs only

The image is what a new install writes. This change does not migrate a Mac that already runs Omarchy: an existing M1, M2 or M3 install still has `m1n1-aurora`, `uboot-asahi`, stock `mesa` and `asahi-audio`. The Aurora packages carry no `replaces=` on those names, and nothing here moves them; later package or hook changes could, and would need their own review. The builder's substitution and the policy's refusal of the old boot chain apply to the image alone; they are not an upgrade path. Moving installed Macs to the Aurora stack would be a separate change, with its own owner.

## Promotion

Promotion to rc and stable is parked. When unparked, it carries the edge catalog unchanged: the Aurora image, the edge runtime, the MacBook Neo and app 2.3.0 ([what a promotion carries](catalog-release.md#promotion-parked)). Accepting that is a maintainer decision to record first.

## Qualification on edge

Edge is for the testing team, so the MacBook Neo and the M3 Macs are qualified there, on real hardware, after an edge release: the catalog marks them untested (`scripts/supported-models.json`). Nothing waits for a hardware install before an image reaches edge; the automated gates above (the release guard, inspection, VM acceptance and both attestations) still stop every image that fails them. The VM harness boots the image's userland under an M1 Pro identity with a generic kernel; m1n1, Stage 1, the Aurora kernel and every board's hardware are qualified on a real Mac.
