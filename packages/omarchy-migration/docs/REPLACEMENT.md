# Approved regular-file replacement experiment

This extends [authenticated restoration](RESTORE.md) with explicitly approved replacement of regular files in a caller-owned destination. The only approval the commands give is `--defaults`, which replaces files that still hold their shipped default (below). Without it, the importer preserves differing destination files as conflicts.

## Approval and interface

```python
with verified_bundle(age, synthetic_secret, ciphertext) as bundle:
    with Restorer(bundle, disposable_target, private_job,
                  replace=(".config/demo/settings",)) as importer:
        plan = importer.plan()
        report = importer.apply(plan)
```

`replace` is an explicit collection of unique selected regular-file paths. Unknown paths, directories, links, duplicate names, and a bare string are rejected. Approval is bound to the manifest, target, private job, and sorted path set in experimental journal `omarchy-migration-restore-probe/3`. A retry must provide the same set; earlier jobs cannot acquire replacement permission. Default jobs retain their existing `/1` or `/2` schema and behavior. Both bundle formats remain supported.

Planning changes no destination files. An approved differing file becomes `replace` only if it is an owned, readable regular file with safe parents and no earlier journal entry. A matching existing file is retained, and an absent entry is created normally. Approval never permits overwriting a later edit or recreating a later deletion from a previous attempt. Unsafe leaves or parents remain conflicts.

The caller reviews the plan and keeps destination directories and file contents quiescent throughout application. `os.replace` does not provide an inode compare-and-swap: another writer in the final check/rename interval can be overwritten. This experiment is unprivileged and does not claim protection against hostile or concurrent same-user writers. Production integration must coordinate application/session preparation before using replacement.

## Replacing untouched defaults

`Restorer(..., replace_matching={path: sha256})` approves each named regular file only while its destination's bytes have that digest. `omarchy-migration plan --defaults DIR` builds the mapping from the files under `DIR` (for the native import, `/etc/skel`), reached without following links. A destination that matches plans as `replace`. A destination that differs plans as a conflict, "existing file differs from its default", with the code `native_file_changed`, and stays as it is. An absent or matching destination behaves as in any job.

The mapping is part of the job binding (`replacement_originals`) beside the approved paths, so the plan and its `plan_id` change with any default, and a retry must bring the same defaults. A replacement that already completed is reconciled from the journal, like any other, so a retry works after the destination stops matching its default.

## Backup and publication

1. Copy the observed original into a new job-local regular file, checking its identity, bytes, digest, metadata, and stability during the copy. The backup uses mode 0600 and preserves nanosecond mtime; original mode and ownership provenance remain in its journal record. Never hardlink the backup to the original inode.
2. Synchronize the backup and job directory. Prepare and synchronize the authenticated replacement through the existing staging path.
3. Persist replacement intent with the original fingerprint, backup basename/fingerprint, and prepared replacement fingerprint. Synchronize the journal and job directory before replacement.
4. Verify the private backup, then recheck the destination and parent identities against the plan. A changed input becomes a conflict with the original destination preserved.
5. Journal that a swap is under way, then exchange the prepared regular file and the destination atomically (`renameat2` with `RENAME_EXCHANGE`). The file the swap moved out is the one the destination held. Unless it is still the planned original, an application wrote it since: it is swapped back, and the replacement becomes a conflict, "destination changed during publication". Otherwise it stays in the job as `<backup>.displaced`, so a write through a descriptor an application still holds lands in a private file and is never lost. Where the system or filesystem can't exchange names, the prepared file moves over the destination with a plain rename, and the destination must be quiescent. Synchronize both directories before recording completion.
6. Retain the independent backup. Normal temporary cleanup never deletes journaled backups.

If preparation fails normally before replacement intent, remove only the backup inode created by that invocation and synchronize removal. Abrupt death before intent can still leave unreferenced private artifacts; this experiment does not sweep or reconcile them. After durable intent, backup and pending evidence remain even when the destination conflicts.

## Recovery and reports

| Observed state on retry | Result |
| --- | --- |
| Exact recorded replacement inode, bytes, mode, and mtime, plus an intact private backup | Resynchronize both directories and reconcile completion without replacing again. A swap journaled as under way is settled first: a displaced file that isn't the planned original is swapped back, and the result is a conflict. |
| Original still present after intent | Conflict: interruption and a later user rollback cannot be distinguished safely. |
| Destination edited, missing, replaced, or otherwise uncertain | Conflict: preserve its current state. |
| Backup changed, missing, symlinked, or multiply linked | Conflict: preserve the destination and report incomplete recovery. |

`Action.backup` carries the opaque job-local basename when a backup record exists, including conflicts. A reference alone is not proof that the backup is intact; use the result status/reason and journal validation. A successful first replacement reports `replaced`; verified retries report `restored`. The private journal stores original metadata for later recovery tooling, but this slice implements no automatic rollback, original ownership recreation, or backup deletion. Backups and journals contain personal plaintext in production, so encrypted placement, lifetime, capacity estimates, and explicit cleanup remain integration requirements.

## Checks and remaining work

With the verified age dependency configured, run:

```bash
PYTHONPATH=lib python3 -W error::ResourceWarning -m unittest discover -s test -t test -p test_replacement.py -v
```

The cases cover explicit/default approval, v1/v2 bundles, matching-file retention, independent backups despite original hardlinks, parent/leaf changes, backup/preparation/intent/completion failures, both post-rename directory-sync failures, backup tampering, conservative retries, and actual SIGKILL immediately after replacement. Process interruption is not filesystem power-loss qualification. Directory/link/extended-metadata replacement is unsupported. Source capture, credential-aware collection, Try transformations, production contracts/packaging, app consent, native provisioning, and physical qualification remain separate work in tickets 04–05 and later gates.
