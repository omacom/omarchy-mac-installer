# Synthetic directories and symbolic links

This extends the [authenticated restoration experiment](RESTORE.md) to explicit directories and selected relative symlinks. It remains a disposable Linux experiment. The [Try integration fixture](FIXTURE.md) keeps its existing file-only schema and behavior; no production capability is advertised by this addition.

## Experimental manifest v2

The original `omarchy-migration-probe/1` file-only format remains readable. `omarchy-migration/bundle/2` (formerly `omarchy-migration-probe/2`) adds a required `kind` to each entry. The top-level fields are `schema`, canonical UUID `export_id`, `entries` and, for collected exports, `provenance` (see [CONTRACT.md](CONTRACT.md#bundle-and-encryption)). Entries retain an index-derived `objects/00000000` identity even when they have no payload object.

| Kind | Exact entry fields | Archive payload |
| --- | --- | --- |
| `file` | `path`, `object`, `kind`, `bytes`, `sha256`, `mode`, `mtime_ns` | One numbered regular-file member. |
| `directory` | `path`, `object`, `kind`, `mode`, `mtime_ns` | None; metadata lives in the encrypted manifest. |
| `symlink` | `path`, `object`, `kind`, `target`, `mtime_ns` | None; original link text lives in the encrypted manifest. |

Every ancestor below the selected root must appear explicitly as a directory. Duplicate paths, a file/link used as an ancestor, missing parents, invalid field sets, unknown types, malformed paths, NULs, and invalid metadata are rejected. Existing manifest/entry/content limits remain in force; v2 additionally limits each path component to 255 UTF-8 bytes and each nonempty link target to 4096 UTF-8 bytes. Link targets are data and are never passed to a shell.

The TAR still contains only the manifest and numbered regular-file payloads. TAR directory/link/device/PAX records are rejected. The existing validator stages only regular-file bytes and exposes the handle only after complete age authentication. No directory or link is created in the destination during decryption.

`make_tree_manifest(paths)` accepts an explicit mapping of synthetic relative names to source paths. It records links with `lstat`/`readlink`, hashes regular files without following their final path component, and refuses special files. Archive writing also refuses a regular source replaced by a symlink or special file. Source parent directories must still be trusted and stable: this is not a home scanner, early credential-selection implementation, or consistent capture of live files. The tree walk in the tests operates only on generated fixtures.

## Directory behavior

Apply processes directories before files, in ancestor order. Empty directories therefore survive. New directories use mode 0700. Existing owned directories that are not writable by others can be retained; the importer never chmods them or explicitly resets their timestamps. Adding children can naturally change a directory's timestamps.

Directories the job creates start at mode 0700. After every publication, deepest first, each created directory whose descendants all finished receives its source mode (without group or other write) and mtime, and reports `directory metadata restored`; so children never disturb a parent's mtime and a read-only source directory never blocks its own contents. A directory with an unfinished descendant stays 0700 with `metadata deferred` until a retry completes it. Pre-existing destination directories are journaled as retained and never changed. A crash between the mode change and the journal update is recognized on retry: a created directory may show either its interim or its final mode.

The v2 journal records durable intent before `mkdir`, then synchronizes and records the created directory's inode/owner/mode before any child is written. If interruption leaves intent without that witness, retry preserves either presence or absence and reports a conflict; it does not adopt a coincidentally matching directory. A directory that appears during creation is also preserved as a conflict. Descendants of a conflicting directory remain blocked while unrelated entries can proceed.

The journal also records retained existing directories and matching existing files/links. Later removal, replacement, or changed witnessed metadata is a conflict, so retry does not recreate a user's deletion. Directory timestamps and child listings are not part of that witness: they naturally change as children are restored. No directory metadata finalization occurs on retry.

## Link behavior

| Source link | Behavior |
| --- | --- |
| Relative link directly reaching a declared regular file or directory through declared directory components | Eligible after its actual destination dependencies are verified. Preserve exact link text and link mtime. |
| Absolute path, escape above the selected root, missing/unselected target, symlink chain, or traversal through another link | Keep the original as an inert encrypted manifest record; report `inert`; create no destination link. |
| Otherwise eligible link whose destination target/directory is conflicting, missing, replaced, or unsafe | Report `conflict`; preserve any existing entry. |

Resolution checks each component before processing `..`. For example, `d/empty/../file` requires `d/empty` to be a declared and verified directory. A link at `d/alias` cannot be bypassed by lexically collapsing `d/alias/../file`. Links to the undeclared root itself are inert in this slice. Cyclic link-to-link references and chains are unsupported; the importer never recursively traverses directory aliases.

Links are processed after regular files. Each target and traversed directory must have a successful result in the current application, and its actual destination identity/content/metadata is rechecked before preparing the link. A safe source manifest cannot turn a preserved destination symlink into a newly published escape. A directory target need not have every descendant restored; a link to that directory does not claim its contents are complete.

The importer creates a private pending symlink, sets its own mtime without following it, records its inode/link-text witness, and publishes a hardlink to that symlink inode using `follow_symlinks=False`. It synchronizes directory entries before completion and removes only its known temporary link. Pending links are never repaired through their target. After uncertain publication, a missing link remains a conflict. These Linux behaviors are exercised with the actual filesystem; they are not a general cross-platform symlink guarantee.

The destination namespace must remain quiescent during each operation, as in the original experiment. Component checks prevent stale plans and several publication races; they do not establish protection against arbitrary simultaneous same-user namespace moves. Process interruption tests do not establish filesystem power-loss durability.

## Verification and limits

With the [verified age dependency](PROBE.md#run-checks) configured and Git available:

```bash
PYTHONPATH=lib python3 -W error::ResourceWarning -m unittest discover -s test -t test -p test_tree.py -v
```

The tests cover typed manifests and rejected TAR link records, explicit/empty directories, original link text, absolute/escaping/dangling/chained/cyclic links retained inertly, component-wise `..` handling, changed destination dependencies, unsafe target links, interrupted/racing directory creation, preservation of later deletions, and recovery after an actual SIGKILL immediately after symlink publication.

A real synthetic Git repository is committed, modified, given an untracked file, encrypted, and restored. Its commit identity, worktree status, and changed/untracked contents are compared. The test strips inherited Git environment overrides, disables external configuration/hooks, and verifies a separate caller-supplied repository/index sentinel stays untouched. It does not establish support for submodules, linked worktrees, external object stores, Git LFS, or every Git configuration.

[Explicit regular-file replacement with private backups](REPLACEMENT.md) is now a separate synthetic extension. Source discovery and credential holdouts, Try-specific transformations, directory metadata finalization, hardlink preservation, ACLs/xattrs, sparse layout, stable application capture, safe plaintext cleanup after abrupt death, shared-runtime packaging, and native boot/encryption integration remain unfinished. Tickets 04–05 remain in progress. No personal home, real source VM, native disk, or production application data is used by these checks.
