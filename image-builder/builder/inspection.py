"""Inspect a finished Apple image root against the candidate set it was built from.

Every boot component the Mac runs is compared with the bytes the signed set
carries, read from the frozen archives rather than from the image: m1n1's
stage 2 on the ESP (m1n1, the Aurora device trees in C order, U-Boot), the
installed boot payloads, the Limine loader, menu and UKI, the initramfs the
UKI embeds, the hooks that keep them current, the installed versions against
the set and its minimums, and the markers a fresh image's first boot needs.
A missing or mismatched component fails the inspection.
"""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import zlib

HERE = Path(__file__).resolve().parent
KERNEL = "linux-aurora"
PLATFORM = "apple-silicon"
LIMINE_STEP = "install/hardware/apple/limine-boot.sh"
# The m1n1 options update-m1n1 copies from /etc/m1n1.conf into stage 2.
M1N1_OPTION = re.compile(r"(chosen\.[^=]*|display|mitigations)=.*")


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


candidate_set = _load("candidate_set", "candidate_set.py")
limine = _load("apple_limine", "apple_limine.py")
installed_system = _load("verify_installed_system", "verify_installed_system.py")


class InspectionError(RuntimeError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise InspectionError(message)


class Candidates:
    """The frozen, imported candidate set: import.json and its archives."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.summary = json.loads((directory / "import.json").read_text())
        self.packages = {p["name"]: p for p in self.summary["packages"]}
        self._listings: dict[str, list[str]] = {}

    def archive(self, name: str) -> Path:
        return self.directory / self.packages[name]["filename"]

    def member(self, name: str, path: str) -> bytes:
        return subprocess.check_output(["bsdtar", "-xOf", str(self.archive(name)), path])

    def listing(self, name: str) -> list[str]:
        if name not in self._listings:
            output = subprocess.check_output(["bsdtar", "-tf", str(self.archive(name))], text=True)
            self._listings[name] = [p.removeprefix("./") for p in output.splitlines() if not p.endswith("/")]
        return self._listings[name]

    def kernel_release(self) -> str:
        kernels = [p for p in self.listing(KERNEL) if PurePosixPath(p).match("usr/lib/modules/*/vmlinuz")]
        require(len(kernels) == 1, "the candidate kernel carries no single vmlinuz")
        return PurePosixPath(kernels[0]).parts[3]

    def dtbs(self) -> list[str]:
        release = self.kernel_release()
        names = [p for p in self.listing(KERNEL) if PurePosixPath(p).match(f"usr/lib/modules/{release}/dtbs/*.dtb")]
        # update-m1n1 concatenates its glob in C collation: bytewise.
        return sorted(names, key=lambda path: path.encode())


def installed_versions(root: Path) -> dict[str, str]:
    versions = {}
    for entry in (root / "var/lib/pacman/local").iterdir():
        desc = entry / "desc"
        if desc.is_file():
            lines = desc.read_text().splitlines()
            versions[lines[lines.index("%NAME%") + 1]] = lines[lines.index("%VERSION%") + 1]
    return versions


def check_candidate_versions(root: Path, candidates: Candidates, report: dict) -> str:
    versions = installed_versions(root)
    report["installed_versions"] = {name: versions.get(name) for name in sorted(candidates.packages)}
    for name, package in candidates.packages.items():
        require(versions.get(name) == package["version"],
                f"{name} is {versions.get(name) or 'not installed'}, the set has {package['version']}")
    return f"all {len(candidates.packages)} candidate packages installed at the set's versions"


def check_minimum_versions(root: Path, policy: dict) -> str:
    versions = installed_versions(root)
    details = []
    for name, minimum in sorted(policy["minimum_versions"].items()):
        installed = versions.get(name)
        require(installed is not None, f"{name} is not installed")
        require(candidate_set.vercmp(installed, minimum) >= 0, f"{name} {installed} is below the minimum {minimum}")
        details.append(f"{name} {installed} >= {minimum}")
    return "; ".join(details)


def check_refused(root: Path, policy: dict) -> str:
    present = sorted(set(policy["refused_packages"]) & set(installed_versions(root)))
    require(not present, "refused packages are installed: " + ", ".join(present))
    return "none of " + ", ".join(policy["refused_packages"]) + " installed"


def check_installed_payloads(root: Path, candidates: Candidates) -> str:
    release = candidates.kernel_release()
    expected = {
        "usr/lib/asahi-boot/m1n1.bin": "m1n1-aurora",
        "usr/lib/asahi-boot/u-boot-nodtb.bin": "uboot-asahi",
        f"usr/lib/modules/{release}/vmlinuz": KERNEL,
    }
    expected.update({path: KERNEL for path in candidates.dtbs()})
    for path, package in expected.items():
        installed = root / path
        require(installed.is_file() and not installed.is_symlink(), f"/{path} is missing")
        require(installed.read_bytes() == candidates.member(package, path), f"/{path} is not {package}'s from the set")
    return f"m1n1, U-Boot, the kernel and {len(candidates.dtbs())} device trees match the set's archives"


def m1n1_stage2(root: Path, candidates: Candidates, report: dict) -> str:
    """m1n1/boot.bin is the set's m1n1, then every Aurora device tree in C
    order, then the set's U-Boot gzipped, then m1n1 options only."""
    data = limine.regular(root / "boot/efi/m1n1/boot.bin")
    m1n1 = candidates.member("m1n1-aurora", "usr/lib/asahi-boot/m1n1.bin")
    require(data.startswith(m1n1), "m1n1/boot.bin does not start with the set's m1n1")
    offset = len(m1n1)
    names = candidates.dtbs()
    for path in names:
        dtb = candidates.member(KERNEL, path)
        require(data[offset:offset + len(dtb)] == dtb,
                f"m1n1/boot.bin does not carry {PurePosixPath(path).name} from the set's kernel at its place")
        offset += len(dtb)
    require(data[offset:offset + 4] != b"\xd0\x0d\xfe\xed", "m1n1/boot.bin carries a device tree the set's kernel lacks")
    uboot, rest = limine.gunzip_prefix(data[offset:])
    require(uboot == candidates.member("uboot-asahi", "usr/lib/asahi-boot/u-boot-nodtb.bin"),
            "m1n1/boot.bin's U-Boot is not the set's")
    options = m1n1_options(root)
    require(rest == options, "m1n1/boot.bin does not end with exactly the image's m1n1 options")
    report["m1n1_stage2"] = {"device_trees": len(names), "bytes": len(data),
                             "options": options.decode(errors="replace").splitlines()}
    return f"m1n1 + {len(names)} device trees (C order) + U-Boot, all from the set"


def m1n1_options(root: Path) -> bytes:
    """What update-m1n1 appends from the image's /etc/m1n1.conf."""
    config = root / "etc/m1n1.conf"
    if not config.exists() and not config.is_symlink():
        return b""
    lines = [line.strip() for line in limine.regular(config).decode().splitlines()]
    return "".join(line + "\n" for line in lines if M1N1_OPTION.fullmatch(line)).encode()


def extract(candidates: Candidates, name: str, into: Path) -> Path:
    destination = into / name
    destination.mkdir()
    subprocess.run(["bsdtar", "-xf", str(candidates.archive(name)), "-C", str(destination),
                    "--exclude", ".PKGINFO", "--exclude", ".BUILDINFO", "--exclude", ".MTREE",
                    "--exclude", ".INSTALL", "--exclude", ".CHANGELOG"], check=True)
    return destination


def check_candidate_files(root: Path, candidates: Candidates, report: dict) -> str:
    """Every file every set package carries is in the image with the set's bytes."""
    counts, changed = 0, []
    with tempfile.TemporaryDirectory(prefix="inspect-") as scratch:
        for name in sorted(candidates.packages):
            unpacked = extract(candidates, name, Path(scratch))
            for directory, _, files in os.walk(unpacked):
                for file in files:
                    source = Path(directory, file)
                    if source.is_symlink():
                        continue
                    relative = source.relative_to(unpacked)
                    installed = root / relative
                    counts += 1
                    if (installed.is_symlink() or not installed.is_file()
                            or installed.stat().st_size != source.stat().st_size
                            or installed.read_bytes() != source.read_bytes()):
                        changed.append(f"/{relative} ({name})")
            subprocess.run(["rm", "-rf", str(unpacked)], check=True)
    report["candidate_files"] = counts
    require(not changed, f"{len(changed)} files differ from the set: " + ", ".join(sorted(changed)[:12]))
    return f"all {counts} files of the {len(candidates.packages)} set packages carry the set's bytes"


def mtree_digests(root: Path, package: str) -> dict[str, str]:
    """The sha256 the local pacman database recorded for each of PACKAGE's files at install."""
    entries = [e for e in (root / "var/lib/pacman/local").iterdir() if (e / "desc").is_file()
               and e.name.rsplit("-", 2)[0] == package]
    require(len(entries) == 1, f"{package} is not installed once")
    digests = {}
    for line in gzip.decompress((entries[0] / "mtree").read_bytes()).decode().splitlines():
        if not line.startswith("./") or "sha256digest=" not in line:
            continue
        path = line.split(" ", 1)[0][2:]
        digests[limine.unescape_mtree(path)] = re.search(r"sha256digest=([0-9a-f]{64})", line)[1]
    return digests


def check_supported_models(root: Path, candidates: Candidates, report: dict) -> str:
    """Every Mac U-Boot supports has its Aurora device tree in stage 2."""
    kernel_dtbs = {PurePosixPath(p).name for p in candidates.dtbs()}
    models = sorted(PurePosixPath(p).name for p in candidates.listing("uboot-asahi")
                    if PurePosixPath(p).match("usr/lib/asahi-boot/dtb/*.dtb"))
    require(models, "the set's U-Boot names no Mac")
    missing = [name for name in models if name not in kernel_dtbs]
    require(not missing, "the Aurora kernel lacks device trees for " + ", ".join(missing))
    report["supported_models"] = [name.removesuffix(".dtb") for name in models]
    return f"{len(models)} Macs: " + " ".join(name.removesuffix(".dtb") for name in models)


def check_limine(root: Path, candidates: Candidates, report: dict) -> str:
    sections = limine.validate_artifacts(root, KERNEL)
    release = candidates.kernel_release()
    require(sections[".linux"] == candidates.member(KERNEL, f"usr/lib/modules/{release}/vmlinuz"),
            "the UKI's kernel is not the set's")
    report["uki"] = {"release": release, "initramfs_bytes": len(sections[".initrd"]),
                     "cmdline": sections[".cmdline"].rstrip(b"\0 \n").decode(errors="replace")}
    return f"Limine at BOOTAA64.EFI boots omarchy_{KERNEL}.efi ({release}) with the set's kernel"


def check_embedded_initramfs(root: Path, report: dict) -> str:
    sections = limine.pe_sections(root / f"boot/efi/EFI/Linux/omarchy_{KERNEL}.efi")
    count = limine.validate_initramfs(sections.get(".initrd") or b"")
    report["initramfs_members"] = count
    return f"{count} members, including the encryption and vendor firmware units and their activation links"


def check_maintenance(root: Path) -> str:
    owners = limine.local_owners(root)
    limine.validate_maintenance(root, owners)
    require(owners.get("usr/share/limine/BOOTAA64.EFI") == "limine", "BOOTAA64.EFI is not the limine package's")
    # limine is not in the set: its loader must be the bytes its pinned archive
    # installed (PROVENANCE traces that archive to the pinned database).
    recorded = mtree_digests(root, "limine").get("usr/share/limine/BOOTAA64.EFI")
    loader = hashlib.sha256(limine.regular(root / "usr/share/limine/BOOTAA64.EFI")).hexdigest()
    require(recorded == loader, "the installed Limine loader is not the one its package installed")
    return "the UKI rebuild, Limine redeploy and activation gate hooks come from their packages; Limine is its package's"


# The image's owner of its root-owned state; fixtures built unprivileged set their own.
OWNER_UID = 0


def root_owned(path: Path, mode: int) -> None:
    status = path.lstat()
    require(not stat.S_ISLNK(status.st_mode), f"{path} is a symlink")
    require(status.st_uid == OWNER_UID and status.st_gid == (0 if OWNER_UID == 0 else status.st_gid),
            f"{path} is not owned by root")
    require(stat.S_IMODE(status.st_mode) == mode, f"{path} is not mode {mode:o}")


def check_image_target(root: Path) -> str:
    directory = root / "var/lib/omarchy/image"
    root_owned(directory, 0o755)
    path = directory / "target"
    require(path.is_file(), "/var/lib/omarchy/image/target is missing")
    root_owned(path, 0o644)
    require(path.read_text() == f"format=1\nplatform={PLATFORM}\n", f"/var/lib/omarchy/image/target does not name {PLATFORM}")
    return f"/var/lib/omarchy/image/target names {PLATFORM}, root-owned"


def empty_marker(path: Path, what: str) -> None:
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == 0, f"{what} is not armed")
    require(stat.S_IMODE(path.stat().st_mode) == 0o644, f"{what} is not mode 0644")


def check_first_boot(root: Path, report: dict) -> str:
    state = root / "var/lib/omarchy"
    empty_marker(state / "mac-first-boot/pending", "first boot")
    empty_marker(state / "provisioning/pending", "owner provisioning")
    gate = state / "limine.enabled"
    require(gate.is_file() and not gate.is_symlink(), "the Limine gate is missing")
    first_boot = limine.regular(root / "usr/lib/omarchy/mac-first-boot/omarchy-mac-first-boot").decode()
    steps = state / "mac-first-boot/deferred-steps"
    if f"'{LIMINE_STEP}'" in first_boot:
        require(steps.is_file() and not steps.is_symlink() and steps.read_text() == LIMINE_STEP + "\n",
                "deferred-steps is not the boot package's fresh-image contract")
        contract = "deferred-steps names the Limine leaf"
    else:
        require(not steps.exists(), "deferred-steps is written for a first boot that does not read it")
        contract = "no deferred-steps contract"
    for unit in ("omarchy-mac-first-boot.service", "omarchy-provision-owner.service"):
        require((root / "etc/systemd/system/multi-user.target.wants" / unit).is_symlink(), f"{unit} is not enabled")
    queue = state / "image/deferred-steps"
    if (root / "usr/bin/omarchy-provision-hardware").exists():
        require(queue.is_file() and (root / "etc/systemd/system/multi-user.target.wants/"
                                     "omarchy-provision-hardware.service").is_symlink(),
                "the runtime defers hardware setup but first boot has no queue to run")
        report["hardware_setup"] = "deferred"
    else:
        require(not queue.exists(), "a hardware queue exists that this runtime never runs")
        report["hardware_setup"] = "build"
    return f"first boot and owner provisioning armed, Limine gate set, {contract}, hardware setup {report['hardware_setup']}"


def check_pacman_config(root: Path, channel: str) -> str:
    template = root / f"usr/share/omarchy/default/pacman/aarch64/pacman-{channel}.conf"
    mirrorlist = root / f"usr/share/omarchy/default/pacman/aarch64/mirrorlist-{channel}"
    require((root / "etc/pacman.conf").read_bytes() == limine.regular(template),
            f"/etc/pacman.conf is not the runtime's aarch64 {channel} configuration")
    require((root / "etc/pacman.d/mirrorlist").read_bytes() == limine.regular(mirrorlist),
            f"/etc/pacman.d/mirrorlist is not the runtime's aarch64 {channel} mirror list")
    return f"the runtime's aarch64 {channel} pacman.conf and mirror list"


def check_installed_system(root: Path, report: dict) -> str:
    verification = installed_system.verify(root)
    report["installed_system"] = verification.checks
    require(not verification.failed, "installed-system checks failed: " + ", ".join(verification.failed))
    return f"{len(verification.checks)} installed-system checks"


def check_factory(factory: Path, candidates: Candidates) -> str:
    sealed = factory / "var/lib/omarchy/factory-sealed"
    require(sealed.is_file() and not sealed.is_symlink(), "@factory is not sealed")
    body = sealed.read_text()
    require(body == f"format=2\ncandidate_set={candidates.summary['set']}\n"
                    f"candidate_source_commit={candidates.summary['source_commit']}\n",
            "@factory records another candidate set")
    for rel in ("var/lib/omarchy/mac-first-boot/pending", "var/lib/omarchy/mac-first-boot/deferred-steps",
                "var/lib/omarchy/provisioning/pending", "etc/pacman.d/gnupg", "boot/efi/omarchy/install.conf"):
        require(not (factory / rel).exists(), f"@factory carries /{rel}")
    for rel in ("var/lib/omarchy/image/target", "var/lib/omarchy/limine.enabled", "etc/default/limine"):
        require((factory / rel).is_file(), f"@factory lacks /{rel}")
    return "sealed for the set, without fresh-image or owner state, keeping the image target and Limine setup"


def inspect(root: Path, candidates_dir: Path, channel: str, factory: Path | None = None,
            trust: Path = candidate_set.TRUST) -> dict:
    policy = candidate_set.load_policy(trust)
    candidates = Candidates(candidates_dir)
    report: dict = {
        "schema": 1,
        "kind": "apple-image-inspection",
        "set": candidates.summary["set"],
        "source_commit": candidates.summary["source_commit"],
        "signer": candidates.summary["signer"],
        "channel": channel,
        "checks": {},
    }
    checks = [
        ("candidate-versions", lambda: check_candidate_versions(root, candidates, report)),
        ("minimum-versions", lambda: check_minimum_versions(root, policy)),
        ("refused-packages", lambda: check_refused(root, policy)),
        ("installed-boot-payloads", lambda: check_installed_payloads(root, candidates)),
        ("candidate-files", lambda: check_candidate_files(root, candidates, report)),
        ("m1n1-stage2", lambda: m1n1_stage2(root, candidates, report)),
        ("aurora-device-trees", lambda: check_supported_models(root, candidates, report)),
        ("limine-uki", lambda: check_limine(root, candidates, report)),
        ("embedded-initramfs", lambda: check_embedded_initramfs(root, report)),
        ("boot-maintenance", lambda: check_maintenance(root)),
        ("image-target", lambda: check_image_target(root)),
        ("first-boot", lambda: check_first_boot(root, report)),
        ("pacman-config", lambda: check_pacman_config(root, channel)),
        ("installed-system", lambda: check_installed_system(root, report)),
    ]
    if factory is not None:
        checks.append(("factory", lambda: check_factory(factory, candidates)))
    for identifier, check in checks:
        try:
            detail = check()
            report["checks"][identifier] = {"result": "passed", "detail": detail}
        except (InspectionError, limine.ContractError, OSError, ValueError, KeyError, IndexError, TypeError,
                zlib.error, subprocess.CalledProcessError) as error:
            report["checks"][identifier] = {"result": "failed", "detail": str(error)}
    report["failed_checks"] = sorted(k for k, v in report["checks"].items() if v["result"] != "passed")
    report["result"] = "failed" if report["failed_checks"] else "passed"
    return report


def write_report(report: dict, path: Path) -> None:
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(text)
    os.replace(tmp, path)
