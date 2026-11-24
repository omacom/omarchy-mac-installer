#!/usr/bin/env python3
"""Resolve and freeze an Apple Silicon package set from a pinned Omarchy channel database.

Channel mode builds a public image from what an Omarchy channel serves, instead
of from a hand-signed candidate set. The set is the runtime and boot packages
the trust policy names, read from a snapshot of the channel's repository
database (omarchy.db) whose sha256 the inputs record pinned when it was
resolved. Each archive must be the one that database lists (file name, size,
sha256, name, version and architecture) and carry a detached signature by the
Omarchy package signing key. Trust comes only from the "channel" section of
candidate-trust/policy.json and the key file it pins by sha256 and fingerprint;
the candidate test key is never accepted here, and the Omarchy key is never
accepted for a candidate set.

A lane (edge, rc or stable) is the channel an image is built from. The
policy's channel section names one entry per enabled lane, and each entry maps
the lane's runtime roles to the packages that fill them. Edge is the default
and takes the -dev runtime; rc takes the clean omarchy and omarchy-settings of
its release. Any other lane is refused by name. A lane's inputs follow the x86
install contract: OMARCHY_MIRROR (the lane's channel on the Omarchy server),
OMARCHY_RUNTIME_PACKAGE and OMARCHY_SETTINGS_PACKAGE (what fills omarchy and
omarchy-settings) and OMARCHY_IMAGE_REF: edge itself, or the release tag
(omacom/omarchy's, v4.5.0-rc2) an rc build is for. That runtime's version must
be the tag's (omarchy 4.5.0rc2 for v4.5.0-rc2).

The channel's runtime may be named apart from its role: edge serves the
desktop as omarchy-dev and omarchy-settings-dev, which provide (and conflict
with) omarchy and omarchy-settings. The fill package must provide its role.

The frozen set has candidate_set.py's layout (import.json beside the archives
and their signatures, read-only), so the builder installs and inspects it the
same way, with "kind": "channel", "candidate_only": false (a public image: no
test image pin, the release profile), the database's sha256, the set's
manifest and its digest. A runtime package records its source commit when it
ships a source revision file; the -dev runtime ships none, and is identified
by its version and archive digest.

  channel_set.py lane --lane LANE [--release-tag TAG]
  channel_set.py label --lane LANE --release-tag TAG
  channel_set.py files --db FILE --lane LANE
  channel_set.py import --db FILE --db-sha256 HEX --lane LANE [--release-tag TAG] --packages DIR --output DIR
  channel_set.py describe --db FILE --lane LANE [--release-tag TAG] --packages DIR
  channel_set.py verify-installed --db FILE --db-sha256 HEX --records FILE --cache DIR
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
TRUST = HERE / 'candidate-trust'
HEX40 = re.compile(r'[0-9A-F]{40}')
HEX64 = re.compile(r'[0-9a-f]{64}')
LANES = ('edge', 'rc', 'stable')
DEFAULT_LANE = 'edge'
NUMBER = r'(0|[1-9][0-9]{0,3})'
RELEASE_TAGS = {
    # A final tag on rc builds the final runtime from rc before it is promoted to stable.
    'rc': re.compile(rf'v{NUMBER}\.{NUMBER}\.{NUMBER}(?:-rc([1-9][0-9]{{0,3}}))?'),
    'stable': re.compile(rf'v{NUMBER}\.{NUMBER}\.{NUMBER}'),
}
# What a pacman version and name look like in a repository database.
NAME = re.compile(r'[A-Za-z0-9@._+-]+')
VERSION = re.compile(r'([0-9]+:)?[A-Za-z0-9._+]+-[0-9]+(\.[0-9]+)?')


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


candidate_set = _load('candidate_set')
require = candidate_set.require
digest = candidate_set.digest


def load_policy(trust=TRUST, lane=DEFAULT_LANE):
    """The candidate policy and its channel section for LANE (the trust root, the lane's name and the
    runtime packages its policy entry maps), with the channel key checked against its pins."""
    policy = candidate_set.load_policy(trust)
    channel = policy.get('channel')
    require(isinstance(channel, dict), 'the trust policy names no channel trust root')
    key = channel.get('public_key', '')
    require(re.fullmatch(r'[A-Za-z0-9._-]+\.asc', key) is not None, 'invalid channel trust anchor')
    require(digest(trust / key) == channel.get('public_key_sha256'), 'channel trust anchor checksum mismatch')
    for field in ('primary_fingerprint', 'signing_fingerprint'):
        require(HEX40.fullmatch(channel.get(field, '')) is not None, 'invalid channel trust anchor fingerprint')
    require(channel['primary_fingerprint'] != policy['primary_fingerprint'].upper(),
            'the channel trust root is the candidate test key')
    lanes = channel.get('lanes')
    require(isinstance(lanes, dict) and lanes, 'the trust policy names no channel lane')
    for name, entry in lanes.items():
        require(name in LANES, f'the trust policy names an unknown lane {name}')
        require(isinstance(entry, dict) and 'runtime_packages' in entry
                and set(entry) <= {'runtime_packages', 'boot_loader'}, f'invalid {name} lane')
        require('boot_loader' not in entry or candidate_set.valid_boot_loader(entry['boot_loader']),
                f'invalid {name} lane boot loader')
        roles = entry['runtime_packages']
        require(isinstance(roles, dict) and set(roles) == set(policy['runtime_packages'])
                and all(isinstance(n, str) and NAME.fullmatch(n) for n in roles.values())
                and len(set(roles.values())) == len(roles), f'invalid {name} lane runtime packages')
    require(lane in LANES, f'unknown lane {lane}: a lane is edge, rc or stable')
    require(lane in lanes, f'lane {lane} is not enabled')
    root = {key: value for key, value in channel.items() if key != 'lanes'}
    return policy, dict(root, lane=lane, runtime_packages=lanes[lane]['runtime_packages'],
                        boot_loader=candidate_set.boot_loader(policy, lane))


def release_version(lane, tag):
    """The runtime pkgver LANE's release TAG ships (4.5.0rc2 for v4.5.0-rc2); edge has no release."""
    if lane == 'edge':
        require(tag is None, 'the edge lane takes no release tag')
        return None
    require(tag is not None, f'the {lane} lane needs a release tag')
    match = RELEASE_TAGS[lane].fullmatch(tag)
    require(match is not None, f"{tag} is not {'an' if lane == 'rc' else 'a'} {lane} release tag")
    version = '.'.join(match.groups()[:3])
    candidate = match.group(4) if lane == 'rc' else None
    return f'{version}rc{candidate}' if candidate else version


def release_image_label(lane, tag):
    """TAG as an image name carries it. A final tag on the rc lane takes a bare -rc (4.5.0-rc), so its
    image is never named as the stable image of that tag, which a catalog-stable would take."""
    label = tag.removeprefix('v')
    return f'{label}-rc' if lane == 'rc' and '-rc' not in label else label


def lane_inputs(lane, release_tag=None, trust=TRUST):
    """The install-contract inputs LANE bakes into an image built for RELEASE_TAG."""
    _, channel = load_policy(trust, lane)
    release_version(lane, release_tag)
    return {
        'OMARCHY_MIRROR': lane,
        'OMARCHY_RUNTIME_PACKAGE': channel['runtime_packages']['omarchy'],
        'OMARCHY_SETTINGS_PACKAGE': channel['runtime_packages']['omarchy-settings'],
        'OMARCHY_IMAGE_REF': release_tag or lane,
    }


def desc_fields(text):
    fields, key = {}, None
    for line in text.splitlines():
        if line.startswith('%') and line.endswith('%') and len(line) > 2:
            key = line.strip('%')
            require(key not in fields, f'a database entry names %{key}% twice')
            fields[key] = []
        elif line and key:
            fields[key].append(line)
    return fields


def read_database(path):
    """name -> desc fields for every package the repository database at PATH lists."""
    entries = {}
    with tempfile.TemporaryDirectory(prefix='channel-db-') as scratch:
        listing = subprocess.run(['bsdtar', '-tf', str(path)], capture_output=True, text=True)
        require(listing.returncode == 0, 'the channel database is not a repository database')
        for member in listing.stdout.splitlines():
            parts = member.rstrip('/').split('/')
            require(len(parts) in (1, 2) and all(p not in ('', '.', '..') for p in parts)
                    and (len(parts) == 1 or parts[1] in ('desc', 'files')),
                    f'unexpected member in the channel database: {member}')
        subprocess.run(['bsdtar', '-xf', str(path), '-C', scratch], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for desc in sorted(Path(scratch).glob('*/desc')):
            fields = desc_fields(desc.read_text())
            single = {}
            for key in ('NAME', 'VERSION', 'FILENAME', 'SHA256SUM', 'CSIZE', 'ARCH'):
                values = fields.get(key, [])
                require(len(values) == 1, f'{desc.parent.name} has no single %{key}%')
                single[key] = values[0]
            require(NAME.fullmatch(single['NAME']) and VERSION.fullmatch(single['VERSION'])
                    and desc.parent.name == f"{single['NAME']}-{single['VERSION']}"
                    and HEX64.fullmatch(single['SHA256SUM']) and single['CSIZE'].isdigit()
                    and candidate_set.ARCHIVE.fullmatch(single['FILENAME']),
                    f'malformed channel database entry {desc.parent.name}')
            require(single['NAME'] not in entries, f"the channel database lists {single['NAME']} twice")
            entries[single['NAME']] = {**fields, **{k: [v] for k, v in single.items()}}
    return entries


def provided(entry):
    """The names ENTRY provides, without version constraints."""
    return {re.split(r'[<>=]', value, maxsplit=1)[0] for value in entry.get('PROVIDES', [])}


def resolve(entries, policy, channel):
    """The set's packages in the database ENTRIES: (role or name, group, entry). The lane's boot
    loader installs Stage 2; a Stage 1 source it names apart (group "stage1") is carried for its
    raw Stage 1 alone and never installed."""
    chosen = []
    loader = channel.get('boot_loader', candidate_set.DEFAULT_BOOT_LOADER)
    for role, name in channel['runtime_packages'].items():
        entry = entries.get(name)
        require(entry is not None, f'the channel database does not list {name}')
        require(role == name or role in provided(entry), f'{name} does not provide {role}')
        chosen.append((role, 'runtime', entry))
    for name in candidate_set.boot_packages(policy, loader) + policy.get('support_packages', []):
        entry = entries.get(name)
        require(entry is not None, f'the channel database does not list {name}')
        chosen.append((name, 'support' if name in policy.get('support_packages', []) else 'boot', entry))
    source = candidate_set.stage1_only(loader)
    if source is not None:
        entry = entries.get(source)
        require(entry is not None, f'the channel database does not list {source}, the Stage 1 source')
        chosen.append((source, 'stage1', entry))
    names = [entry['NAME'][0] for _, group, entry in chosen if group != 'stage1']
    require(not set(names) & set(candidate_set.refused_packages(policy, loader)), 'refused package in the set')
    for _, group, entry in chosen:
        require(entry['ARCH'][0] in (('aarch64', 'any') if group == 'support' else ('aarch64',)), f"{entry['NAME'][0]} is not an aarch64 package")
    return chosen


# The kernel from which edge boots through aura (omacom/omarchy-pkgs#3025): aura
# needs it and conflicts with every older one, and m1n1-aurora with it is not
# qualified for edge images. Until this builder installs aura, an edge image never
# pairs the two; it stops instead, and the last edge image keeps serving installs.
# Compared by major.minor alone, so 7.3rc1, 7.3.aurora1 and 7.3.0 all count as 7.3.
AURA_KERNEL = '7.3'


def upstream_version(version):
    """VERSION's pkgver alone: no epoch, so a rollback re-pinned above a newer kernel by an
    epoch bump is compared by what it ships; no pkgrel."""
    return re.sub(r'^[0-9]+:', '', version).rsplit('-', 1)[0]


def kernel_series(version):
    """VERSION's kernel major.minor as integers, from its pkgver, or None when it names none."""
    match = re.match(r'([0-9]+)\.([0-9]+)', upstream_version(version))
    return (int(match[1]), int(match[2])) if match else None


def refuse_untested_boot_pair(chosen, lane):
    """An edge set must not pair linux-aurora AURA_KERNEL or newer with m1n1-aurora. rc and
    stable are left alone: they change only through promotion, which holds the 7.3 set back."""
    if lane != 'edge':
        return
    versions = {entry['NAME'][0]: entry['VERSION'][0] for _, group, entry in chosen if group == 'boot'}
    kernel = versions.get('linux-aurora')
    if 'm1n1-aurora' not in versions or kernel is None:
        return
    # A pkgver with no major.minor is refused too: it cannot be shown to be older.
    series = kernel_series(kernel)
    require(series is not None and series < kernel_series(AURA_KERNEL),
            f'edge pairs linux-aurora {kernel} with m1n1-aurora; m1n1-aurora is not qualified for edge '
            f'images with linux-aurora {AURA_KERNEL} or newer (that kernel boots through aura, '
            'omacom/omarchy-pkgs#3025)')


def manifest(chosen, lane, database_sha256, channel):
    return {
        'schema': 1,
        'kind': 'channel',
        'channel': lane,
        'repository': channel['repository'],
        'database_sha256': database_sha256,
        'signer': channel['primary_fingerprint'],
        'packages': sorted(({'name': e['NAME'][0], 'version': e['VERSION'][0], 'filename': e['FILENAME'][0],
                             'sha256': e['SHA256SUM'][0], 'size': int(e['CSIZE'][0]), 'group': group,
                             **({'role': role} if group == 'runtime' else {})}
                            for role, group, e in chosen), key=lambda p: p['name']),
    }


def keyring(home, trust, channel):
    home.chmod(0o700)
    subprocess.run(['gpg', '--homedir', str(home), '--batch', '--dearmor', '--output', str(home / 'trusted.gpg'),
                    str(trust / channel['public_key'])], check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE)


def verify_archive(home, path, entry, channel):
    """PATH is the archive ENTRY lists, signed by the channel key alone."""
    name = entry['NAME'][0]
    require(path.stat().st_size == int(entry['CSIZE'][0]) and digest(path) == entry['SHA256SUM'][0],
            f'{path.name} is not the archive the channel database lists')
    signature = Path(str(path) + '.sig')
    require(signature.is_file() and not signature.is_symlink(), f'unsigned package: {path.name}')
    candidate_set.verify_signature(home, path, channel)
    fields = candidate_set.pkginfo(path)
    require(fields.get('pkgname') == [name] and fields.get('pkgver') == entry['VERSION']
            and fields.get('arch') == entry['ARCH'], f'package metadata mismatch: {name}')
    return fields


def files(database, lane, trust=TRUST):
    policy, channel = load_policy(trust, lane)
    return [e['FILENAME'][0] for _, _, e in resolve(read_database(database), policy, channel)]


def snapshot(database, database_sha256, lane, packages, destination, trust=TRUST, release_tag=None):
    """Verifies the set the pinned DATABASE names from the archives in PACKAGES and freezes it in DESTINATION."""
    require(HEX64.fullmatch(database_sha256) is not None, 'invalid database hash')
    require(packages.is_dir() and not packages.is_symlink(), 'unsafe package directory')
    policy, channel = load_policy(trust, lane)
    release = release_version(lane, release_tag)
    destination.mkdir(mode=0o700)  # Exclusive: a failed import is never reused.
    candidate_set.copy_file(database.parent, database.name, destination)
    frozen = destination / database.name
    require(digest(frozen) == database_sha256, 'the channel database changed since it was pinned')
    chosen = resolve(read_database(frozen), policy, channel)
    frozen.unlink()
    refuse_untested_boot_pair(chosen, lane)
    for role, _, entry in chosen:
        version = entry['VERSION'][0]
        require(release is None or role not in ('omarchy', 'omarchy-settings')
                or re.sub(r'^[0-9]+:', '', version).rsplit('-', 1)[0] == release,
                f"{entry['NAME'][0]} {version} is not the release {release_tag}")
    record = manifest(chosen, lane, database_sha256, channel)
    text = json.dumps(record, indent=2, sort_keys=True) + '\n'
    (destination / 'manifest.json').write_text(text)
    payloads, fields_of, commits = {}, {}, {}
    with tempfile.TemporaryDirectory(prefix='channel-trust-') as temporary:
        home = Path(temporary)
        keyring(home, trust, channel)
        for role, group, entry in chosen:
            filename = entry['FILENAME'][0]
            candidate_set.copy_file(packages, filename, destination)
            # A missing signature is refused below, by name.
            if (packages / (filename + '.sig')).exists():
                candidate_set.copy_file(packages, filename + '.sig', destination)
            path = destination / filename
            fields_of[role] = verify_archive(home, path, entry, channel)
            payloads[role] = candidate_set.payload_paths(path)
            minimum = policy['minimum_versions'].get(entry['NAME'][0])
            require(minimum is None or candidate_set.vercmp(entry['VERSION'][0], minimum) >= 0,
                    f"{entry['NAME'][0]} {entry['VERSION'][0]} is below the minimum {minimum}")
            if group == 'runtime':
                revision_file = candidate_set.REVISION_FILES[role]
                if revision_file in payloads[role]:
                    revision = candidate_set.member(path, revision_file).decode().strip()
                    require(candidate_set.HEX40.fullmatch(revision) is not None,
                            f'{entry["NAME"][0]} carries a malformed source revision')
                    commits[role] = revision
    candidate_set.verify_payloads(payloads, policy, channel['boot_loader'])
    names = {role: entry['NAME'][0] for role, _, entry in chosen}
    versions = {role: entry['VERSION'][0] for role, _, entry in chosen}
    require(versions['omarchy'] == versions['omarchy-settings'], 'mixed desktop versions')
    settings = names['omarchy-settings']
    require(any(re.split(r'[<>=]', d, maxsplit=1)[0] == settings
                and d in (settings, f"{settings}={versions['omarchy-settings']}",
                          f"{settings}={versions['omarchy-settings'].rsplit('-', 1)[0]}")
                for d in fields_of['omarchy'].get('depend', [])), 'the runtime does not depend on its settings package')
    _, apple = candidate_set.apple_list(destination / next(e['FILENAME'][0] for r, _, e in chosen if r == 'omarchy'))
    require({'omarchy-mac', 'omarchy-mac-boot'} <= {line.strip() for line in apple.splitlines()},
            'the Apple package list lacks the add-on or boot package')
    packages_record = [{'name': p['name'], 'version': p['version'], 'filename': p['filename'], 'sha256': p['sha256'],
                        'group': p['group'], 'origin': 'channel',
                        **({'role': p['role']} if 'role' in p else {}),
                        **({'source_commit': commits[p['role']]} if p.get('role') in commits else {})}
                       for p in record['packages']]
    summary = {
        'schema': 1,
        'kind': 'channel',
        'set': f'omarchy-{lane}-{database_sha256[:12]}',
        'candidate_only': False,
        'channel': lane,
        'source_commit': None,
        'signer': channel['primary_fingerprint'],
        'database_sha256': database_sha256,
        'manifest_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'set_sha256': candidate_set.set_digest(record),
        'boot_loader': channel['boot_loader'],
        'packages': packages_record,
    }
    (destination / 'import.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    for path in destination.iterdir():
        path.chmod(0o444)
    destination.chmod(0o555)
    return summary


def describe(database, lane, packages, trust=TRUST, release_tag=None):
    """Verifies the set in a scratch copy and prints the values an inputs record pins."""
    with tempfile.TemporaryDirectory(prefix='channel-describe-') as scratch:
        summary = snapshot(database, digest(database), lane, packages, Path(scratch) / 'set', trust, release_tag)
        for path in (Path(scratch) / 'set').iterdir():
            path.chmod(0o644)
        (Path(scratch) / 'set').chmod(0o755)
    return summary


def verify_installed(database, database_sha256, records, cache, trust=TRUST):
    """Every package RECORDS (package_record.py's lines) traces to the channel repository is the
    pinned DATABASE's archive in CACHE, signed by the channel key alone. Returns how many."""
    policy, channel = load_policy(trust)
    require(digest(database) == database_sha256, 'the channel database changed since it was pinned')
    entries = read_database(database)
    count = 0
    with tempfile.TemporaryDirectory(prefix='channel-trust-') as temporary:
        home = Path(temporary)
        keyring(home, trust, channel)
        for line in records.read_text().splitlines():
            name, version, repository, filename, sha256 = line.split('|')
            if repository != channel['repository']:
                continue
            entry = entries.get(name)
            require(entry is not None and entry['VERSION'] == [version] and entry['FILENAME'] == [filename]
                    and entry['SHA256SUM'] == [sha256], f'{name} {version} is not the pinned channel database\'s')
            path = cache / filename
            require(path.is_file() and not path.is_symlink(), f'{filename} is not in the package cache')
            verify_archive(home, path, entry, channel)
            count += 1
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    laned = commands.add_parser('lane')
    labelled = commands.add_parser('label')
    listed = commands.add_parser('files')
    imported = commands.add_parser('import')
    described = commands.add_parser('describe')
    installed = commands.add_parser('verify-installed')
    for command in (listed, imported, described, installed):
        command.add_argument('--db', type=Path, required=True)
    for command in (laned, labelled, listed, imported, described):
        command.add_argument('--lane', required=True)
    for command in (laned, labelled, imported, described):
        command.add_argument('--release-tag')
    for command in (imported, described):
        command.add_argument('--packages', type=Path, required=True)
    for command in (imported, installed):
        command.add_argument('--db-sha256', required=True)
    imported.add_argument('--output', type=Path, required=True)
    installed.add_argument('--records', type=Path, required=True)
    installed.add_argument('--cache', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'lane':
            print(''.join(f'{key}={value}\n' for key, value in lane_inputs(args.lane, args.release_tag).items()), end='')
            return 0
        if args.command == 'label':
            release_version(args.lane, args.release_tag)
            print(release_image_label(args.lane, args.release_tag))
            return 0
        if args.command == 'files':
            print('\n'.join(files(args.db, args.lane)))
            return 0
        if args.command == 'verify-installed':
            count = verify_installed(args.db, args.db_sha256, args.records, args.cache)
            print(f'verified={count}')
            return 0
        if args.command == 'import':
            summary = snapshot(args.db, args.db_sha256, args.lane, args.packages, args.output,
                               release_tag=args.release_tag)
        else:
            summary = describe(args.db, args.lane, args.packages, release_tag=args.release_tag)
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f'channel_set: {error}', file=sys.stderr)
        return 1
    for key in ('set', 'signer', 'database_sha256', 'manifest_sha256', 'set_sha256'):
        print(f'{key}={summary[key]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
