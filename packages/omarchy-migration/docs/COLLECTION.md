# Policy-aware disposable collection

This synthetic library captures a caller-created, quiescent fixture tree into a private temporary snapshot. The existing v2 exporter consumes only those copied objects. It advances collection behavior for tickets 04–05 without installing a scanner, accepting a real-home CLI argument, or claiming supported application exports. The runnable Try fixture remains unchanged.

## Separate selection and trusted policy

The request chooses source roots, archive names, and explicitly selected synthetic adapters:

```json
{
  "schema": "omarchy-migration-collection-request/3",
  "request_id": "00000000-0000-4000-8000-000000000001",
  "selection": [{"source": "", "archive": ""}],
  "selected_adapters": [],
  "selected_mounts": [],
  "selected_share_stores": []
}
```

The caller separately supplies a trusted [`omarchy-migration/policy/2`](CONTRACT.md#policy) document; the request cannot change protected paths, rules or mounts. The source root stands for the owner's home, so policy paths are relative to it. Tests use a synthetic policy with fake stores and the real [Try policy](../lib/omarchy_migration/policies/try-omarchy-90c8210.json) against a synthetic Try home. Store adapters must be `fixture-` adapters in this collector.

These are fake-store rules. Actual application locations, alternate paths/environment settings, supported source versions, and supported export modes still need qualification. The generic collector does not run adapters; the trusted capability set can enable a test-only byte-copy policy for a fake store. It must never be advertised as real SSH/browser/Codex support. Unsupported stores remain withheld under explicit selection.

Requests use exact field sets and bounded normalized paths; the policy is validated by the contract. Unknown schemas/adapters, invalid policies (including overlapping store roots or a rule inside a store), duplicate selections/adapters, and conflicting source/archive selections reject. An empty source selects fixture-root contents; an empty archive is allowed only with that source. Nonempty archive roots have one path component in this slice; deeper mappings require a later ancestor-metadata contract. Depth is limited to 64 components, entry/report count to the existing probe limit, and captured bytes to the existing expanded-content limit.

## Shared Mac folders

The request's `selected_mounts` lists policy mounts whose contents should be copied, such as `mac-share` for `/mnt/mac`. Unselected mounts are never read. For a selected mount, only a home link whose target is exactly the mount root (the link Try generates, for example `~/Work`) is materialized: the archive gets an ordinary directory at the link's path holding the mount's contents, reported as `included` with reason `mount-materialized`. Further links to the same root stay inert (`mount-already-materialized`), and links deeper into a mount stay inert (`mount-link`), so nothing is copied twice. `share_roots` maps a mount id to the directory to read (the policy's mount path by default; tests use a fixture directory).

Share roots are resolved once (the trusted mount path may contain links) and must neither contain nor sit inside the home or the snapshot location. Inside a shared folder the home rules and stores do not apply, because its paths are the Mac's files rather than home paths. Instead the policy's `share_stores` recognize credential locations by name at any depth (`.ssh`, `.gnupg`, `.aws`, `Library/Keychains`, browser profile folders, private-key file names such as `*.pem`); each is held back unopened as `held-out`/`unselected-store` with its store and mount ids unless the request lists it in `selected_share_stores`. Below the resolved root the walk uses the share's own pinned mount, never follows links, and skips rather than fails on entries the home walk would treat as fatal: a different mount (`other-filesystem`), a group- or world-writable directory (`unsafe-permissions`), an unreadable entry (`unreadable`), multiply linked files, special files, and relative links that would leave the share once it is renamed to its link's path (`share-escape`). A skipped entry is rolled back completely, including anything it had already registered or written. Ownership is not required to match. An unreadable share root still fails the export, as do names that cannot be represented and the overall item limit. The Mac can change these files while Try exports, so a shared folder gets the per-entry before/after checks but not the home's final whole-tree recheck; a change detected inside a directory still aborts the export so it can be retried.

## Capture interface and isolation

```python
with collect_fixture(disposable_source, request, trusted_policy,
                     supported_adapters=("fixture-ssh-bytes/1",),
                     snapshot_parent=private_scratch_parent) as snapshot:
    probe.encrypt(age, synthetic_secret, output,
                  lambda stream: probe.write_archive(stream, snapshot.manifest, snapshot.paths))
```

The caller supplies existing owned directories. The source must not be writable by others, and the snapshot parent must have mode 0700, lie outside the source, and remain caller-controlled and quiescent. Temporary-directory creation uses the checked parent's pathname; this is not a privileged facility for a hostile parent namespace. The collector refuses root execution and symlink root/parent components at the final open. Caller-supplied parent paths are trusted.

Source traversal uses pinned directory descriptors, nofollow opens, owner checks, and Linux descriptor mount identities. Nested mounts, including same-filesystem bind mounts, reject before payload reads or directory enumeration. No test mounts a filesystem; the mount rejection regression injects a differing descriptor identity. All generic regular files with multiple hardlinks are deferred before reading, preventing a protected file from leaking through an ordinary hardlink alias under the current unsupported-hardlink policy.

Protected-path classification happens in source space before stat/open/descendant enumeration and before archive-name remapping. Stores are matched first, then rules. An `exclude` rule, exact or tree, is reported once and neither its path nor anything beneath it is opened or listed, including when a selection root lies beneath it. A `transform` rule applies only to a regular file of at most 1 MiB: the file is captured through the same checked descriptor, the data-only transform runs on the captured bytes, and the result is what the file's own archive path receives (the untouched bytes go only to the originals copy described below). When a transform changes a file, its untouched bytes are also exported as a private (0600) copy under the migration-owned `.local/share/omarchy-migration/originals/<request-id>/` with the original path and mtime, reported as `original-copy`, and the root is recorded in the manifest provenance's `originals`; a collision with a captured entry rejects the export. A missing provider block exports the file unchanged; an ambiguous, residual or unparsable file, an oversized file or a non-file target is withheld as `unsupported` with a `transform-*` reason. `preserve` rules change nothing and are recorded for traceability. Links whose absolute target lies inside a policy mount, such as `~/Work` pointing to `/mnt/mac`, are kept as inert link text with outcome `inert-link`, reason `mount-link` and the mount id. A pruned subtree produces one report item with unknown descendant count; collecting inventory does not inspect the withheld store. Similar-prefix ordinary paths, unfamiliar configuration, and unknown durable data remain eligible. Symlinks are copied as text without traversal. Identity-mapped links use the existing safe/inert v2 rule. If any selection renames a root, all symlinks are deferred with `remapped-link`: v2 cannot explicitly force a source link inert when archive names would accidentally activate it against different data.

Regular files are copied through checked descriptors with size/metadata/identity checks before and after reading. Directories have bounded sorted names and before/after metadata/name checks; a final metadata pass checks captured entries and the original root. Changed sources abort without yielding a handle, and ordinary errors remove only the owned snapshot. These checks require quiescence and do not establish a coherent atomic cutover across live applications, databases, or host shares.

Metadata the bundle cannot carry is recorded, never silently dropped: for each included file and directory the collector lists attribute names (ACLs and other extended attributes), compares allocated blocks with size (sparse files) and checks setuid, setgid and sticky bits, and writes the kinds into provenance `metadata`. The content is still exported. Snapshot files remain 0600 and directories 0700. Original source mode/mtime and link text are recorded in the validated v2 manifest; copied object permissions do not impersonate source final permissions. Original source paths are never supplied to archive writing. Later source edits/deletions cannot change the captured bytes. Mode/mtime/link metadata is the portable scope; ACLs, xattrs, sparse layout, special permission bits, ownership recreation, and hardlink relationships are not preserved. The importer applies directory mode and mtime to directories it creates; see [TREE.md](TREE.md).

## Private report and lifetime

`snapshot.report` uses `omarchy-migration-collection-report/2`. It includes request identity, request/policy/capability digests, supported synthetic adapter IDs, the policy revision, per-source/archive outcomes with the governing `store`, `rule` and `mount` ids, and aggregate counts. Outcomes are `included`, `transformed`, `held-out`, `excluded`, `unsupported`, and `inert-link`; reasons distinguish unavailable/unselected stores, the rule's reason code, transform failures, multiply linked/unreadable files, files of 8 GiB or more (`file-too-large`, beyond what a bundle member holds), special files, remapped links, mount links and unavailable link targets. `complete` means the bounded capture finished with accounted omissions, not that every selected category transferred or that an encrypted export is complete.

The report contains private paths and is not a public receipt. Its exceptions and counts, with the policy revision and the policy and request digests, are also written into the manifest's authenticated `provenance`, so they travel inside the encrypted bundle. Production source/account identity, authorization/selection revision binding, capture evidence, metadata/omission representation and encrypted private report handling remain contract work. No diagnostic upload occurs.

The context owns snapshot lifetime and removes it on normal exit or exception. It has no durable restart/resume protocol. SIGKILL or host failure can leave private plaintext scratch; production encrypted scratch placement, capacity, artifact reconciliation, and cleanup remain necessary. Do not use this experiment on real personal data.

## Verification and next work

```bash
PYTHONPATH=lib python3 -W error::ResourceWarning -m unittest discover -s test -t test -p test_collection.py -v
```

Most cases require only Linux/Python and temporary files. The encrypted roundtrip additionally requires the verified age dependency. Tests prove held-out descendants are neither opened nor listed, default/alternate roots behave consistently, source remapping cannot bypass policy, symlink/hardlink aliases do not copy fake secrets, supported fake opt-in does not enable unavailable adapters, changed sources and resource limits fail before completion, and encrypted restoration retains captured settings/project bytes after later source edits. Policy cases prove excluded paths are never opened, transforms keep only user content with original mode and mtime, untransformable files are withheld, mount links stay inert, and the Try policy cleans a synthetic Try home. Authenticated report provenance in the bundle, original retention and the fixture CLI's move to contract documents are next; live capture and production runtime integration remain separate gates.
