#!/usr/bin/env python3
"""Stage the real m1n1-macho Mach-O bundle for the m1n1-aurora package.

This is a producer boundary, not a boot-image builder. The m1n1-macho
checkout must already have produced ``build/m1n1.macho`` and
``build/m1n1-sptm-stage2.pkg``. The command refuses missing, empty,
symlinked, or malformed inputs and never creates a substitute payload.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import struct
import tempfile


MACHO_RELATIVE = Path("build/m1n1.macho")
STAGE2_RELATIVE = Path("build/m1n1-sptm-stage2.pkg")
MACHO_MAGIC = b"\xcf\xfa\xed\xfe"  # MH_MAGIC_64, little-endian arm64 Mach-O
STAGE2_MAGIC = b"M1N1SPT2"  # tools/sptm_stage2_pack.py
STAGE2_HEADER = struct.Struct("<8s10Q")
PACKAGE_DIRECTORY = Path("usr/share/m1n1-aurora/macho")


class ProducerError(ValueError):
    """Raised when an authenticated producer input is unsafe or invalid."""


def _regular_bytes(path: Path, label: str) -> bytes:
    if path.is_symlink():
        raise ProducerError(f"{label} must not be a symlink: {path}")
    if not path.exists() or not path.is_file():
        raise ProducerError(f"{label} is missing: {path}")
    data = path.read_bytes()
    if not data:
        raise ProducerError(f"{label} is empty: {path}")
    return data


def _validate_inputs(source: Path) -> dict[str, bytes]:
    if source.is_symlink() or not source.is_dir():
        raise ProducerError(f"m1n1-macho source is not a real directory: {source}")
    macho = _regular_bytes(source / MACHO_RELATIVE, "m1n1 Mach-O")
    if len(macho) < 32 or macho[:4] != MACHO_MAGIC:
        raise ProducerError("m1n1 Mach-O has an invalid 64-bit arm64 magic")
    # MH_CPU_TYPE_ARM64 | CPU_ARCH_ABI64_32 is not accepted here: m1n1's
    # startup object is an arm64e Mach-O (cputype 0x100000c).
    if struct.unpack_from("<I", macho, 4)[0] != 0x100000C:
        raise ProducerError("m1n1 Mach-O is not an arm64e object")
    stage2 = _regular_bytes(source / STAGE2_RELATIVE, "m1n1 SPTM Stage 2 package")
    if len(stage2) < STAGE2_HEADER.size or not stage2.startswith(STAGE2_MAGIC):
        raise ProducerError("m1n1 SPTM Stage 2 package has an invalid magic")
    fields = STAGE2_HEADER.unpack_from(stage2)
    if fields[1] != 1 or fields[-1] != len(stage2):
        raise ProducerError("m1n1 SPTM Stage 2 package has an invalid header")
    return {"aura-boot.macho": macho, "aura-stage2.pkg": stage2}


def _safe_directory(root: Path, relative: Path) -> Path:
    """Create a package directory while refusing symlinked path components."""
    root = root.absolute()
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ProducerError(f"package staging path is not a real directory: {root}")
    # Existing host ancestors such as macOS's /var may themselves be
    # symlinks. Resolve those before creating our package-owned components;
    # the explicit output-root check above still refuses a symlink argument.
    root = root.resolve()
    if not root.exists():
        root.mkdir(parents=True)
    current = root
    for component in relative.parts:
        current /= component
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ProducerError(f"package staging path is not a real directory: {current}")
        if not current.exists():
            current.mkdir()
    return current


def _atomic_write(path: Path, data: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ProducerError(f"package member is not a regular file: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _check_destinations(destination: Path, names: list[str]) -> None:
    for name in names:
        path = destination / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ProducerError(f"package member is not a regular file: {path}")


def produce(source: Path, output: Path, *, nv2: bool = False) -> dict[str, int]:
    """Copy validated outputs into an m1n1-aurora package staging root."""
    members = _validate_inputs(Path(source))
    members["nv-config"] = f"x1n1.nv2={'1' if nv2 else '0'}\n".encode("ascii")
    destination = _safe_directory(Path(output), PACKAGE_DIRECTORY)
    _check_destinations(destination, list(members))
    for name, data in members.items():
        _atomic_write(destination / name, data)
    return {name: len(data) for name, data in members.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="m1n1-macho checkout (with build/ outputs)")
    parser.add_argument("output", type=Path, help="package staging root")
    parser.add_argument("--nv2", choices=("off", "on"), default="off",
                        help="write the explicit NV2 config (default: off)")
    args = parser.parse_args(argv)
    try:
        sizes = produce(args.source, args.output, nv2=args.nv2 == "on")
    except (OSError, ProducerError) as error:
        parser.error(str(error))
    for name, size in sizes.items():
        print(f"m1n1-aurora: {name}: {size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
