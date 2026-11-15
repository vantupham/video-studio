"""Keep only the DLLs an executable actually loads; delete the rest of a bundled folder.

Used by ``fetch-tesseract.ps1``: the UB-Mannheim Tesseract build ships the DLLs of its training
tools too (pango, cairo, ICU, glib, ...). Walks the PE import tables (normal + delay-load)
starting from the given executables and removes every ``*.dll`` in the folder that is not
reachable. Standard library only.

    python prune_dlls.py <folder> tesseract.exe [--dry-run]
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path


def _rva_to_offset(sections: list[tuple[int, int, int, int]], rva: int) -> int | None:
    for va, vsize, raw_ptr, raw_size in sections:
        if va <= rva < va + max(vsize, raw_size):
            return raw_ptr + (rva - va)
    return None


def _cstr(data: bytes, off: int) -> str:
    end = data.index(b"\0", off)
    return data[off:end].decode("ascii", "replace")


def pe_imports(path: Path) -> set[str]:
    """Lower-cased names of the DLLs imported (and delay-imported) by a PE file."""
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise ValueError(f"{path.name}: not a PE file")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError(f"{path.name}: bad PE signature")
    n_sections, = struct.unpack_from("<H", data, pe + 6)
    opt_size, = struct.unpack_from("<H", data, pe + 20)
    opt = pe + 24
    magic, = struct.unpack_from("<H", data, opt)
    dirs = opt + (112 if magic == 0x20B else 96)
    sections = []
    for i in range(n_sections):
        sh = opt + opt_size + 40 * i
        vsize, va, raw_size, raw_ptr = struct.unpack_from("<IIII", data, sh + 8)
        sections.append((va, vsize, raw_ptr, raw_size))
    names: set[str] = set()
    # data directory 1 = import table (20-byte descriptors, name RVA at +12),
    # 13 = delay-load import table (32-byte descriptors, name RVA at +4)
    for index, size, name_at in ((1, 20, 12), (13, 32, 4)):
        rva, _ = struct.unpack_from("<II", data, dirs + 8 * index)
        off = _rva_to_offset(sections, rva) if rva else None
        while off is not None and off + size <= len(data):
            desc = data[off:off + size]
            if not any(desc):
                break
            name_rva, = struct.unpack_from("<I", desc, name_at)
            name_off = _rva_to_offset(sections, name_rva)
            if name_off is None:
                break
            names.add(_cstr(data, name_off).lower())
            off += size
    return names


def closure(folder: Path, roots: list[str]) -> set[str]:
    present = {p.name.lower(): p for p in folder.glob("*.dll")}
    keep: set[str] = set()
    todo = [folder / r for r in roots]
    while todo:
        current = todo.pop()
        for name in pe_imports(current):
            if name in present and name not in keep:
                keep.add(name)
                todo.append(present[name])
    return keep


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) < 2:
        print(__doc__)
        return 2
    folder, roots = Path(args[0]), args[1:]
    keep = closure(folder, roots)
    kept = removed = 0
    for dll in sorted(folder.glob("*.dll")):
        size = dll.stat().st_size
        if dll.name.lower() in keep:
            kept += size
            continue
        removed += size
        print(f"  - {dll.name} ({size / 1e6:.1f} MB)")
        if not dry:
            dll.unlink()
    print(f"kept {len(keep)} DLLs ({kept / 1e6:.1f} MB), removed {removed / 1e6:.1f} MB"
          f"{' (dry run)' if dry else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
