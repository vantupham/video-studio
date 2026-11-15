"""Multi-file sources: a document plus the files it references (Markdown images…),
uploaded as a .zip, a folder or several files. Relative paths are kept so links like
``![](images/a.png)`` resolve inside the project."""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

from videostudio.textutil import safe_filename

MAX_BUNDLE_BYTES = 200 * 1024 * 1024
MAX_BUNDLE_FILES = 2000
MAX_DEPTH = 12
# Preferred main document when a bundle contains several candidates.
MAIN_EXT_ORDER = (".md", ".markdown", ".mdown", ".mkd", ".docx", ".pdf", ".txt")
_SKIP = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}


class BundleError(ValueError):
    pass


def safe_rel_path(raw: str) -> PurePosixPath | None:
    """Sanitise a client-supplied relative path; ``None`` for anything suspicious."""
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or len(parts) > MAX_DEPTH or any(p == ".." for p in parts):
        return None
    if any(p.lower() in _SKIP for p in parts) or ":" in parts[0]:
        return None
    clean = [safe_filename(p) for p in parts]
    return PurePosixPath(*clean)


def _zip_name(info: zipfile.ZipInfo) -> str:
    # Without the UTF-8 flag Python decodes names as cp437; Windows Explorer and many
    # tools actually wrote UTF-8 (or the user's code page) bytes.
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437", errors="replace")
    for encoding in ("utf-8", "cp1258"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return info.filename


def strip_common_root(paths: list[PurePosixPath]) -> list[PurePosixPath]:
    """``docs/a.md, docs/img/x.png`` → ``a.md, img/x.png`` (folder uploads add the folder
    name to every path)."""
    if len(paths) < 1 or any(len(p.parts) < 2 for p in paths):
        return paths
    first = paths[0].parts[0]
    if all(p.parts[0] == first for p in paths):
        return [PurePosixPath(*p.parts[1:]) for p in paths]
    return paths


def extract_zip(data_path: Path, dest: Path) -> list[PurePosixPath]:
    try:
        archive = zipfile.ZipFile(data_path)
    except zipfile.BadZipFile as exc:
        raise BundleError("File .zip bị hỏng hoặc không phải .zip") from exc
    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        if len(infos) > MAX_BUNDLE_FILES:
            raise BundleError(f"File .zip có quá nhiều tệp (tối đa {MAX_BUNDLE_FILES})")
        total = sum(i.file_size for i in infos)
        if total > MAX_BUNDLE_BYTES:
            raise BundleError("Nội dung giải nén quá lớn (tối đa 200 MB)")
        entries: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        for info in infos:
            rel = safe_rel_path(_zip_name(info))
            if rel is not None:
                entries.append((info, rel))
        rels = strip_common_root([r for _, r in entries])
        written: list[PurePosixPath] = []
        for (info, _), rel in zip(entries, rels, strict=True):
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as out:
                remaining = info.file_size + 1
                while chunk := src.read(min(1 << 20, remaining)):
                    out.write(chunk)
                    remaining -= len(chunk)
                    if remaining <= 0:
                        raise BundleError("Tệp trong .zip lớn hơn khai báo")
            written.append(rel)
    return written


def pick_main(paths: list[PurePosixPath], wanted: str | None = None) -> PurePosixPath:
    if wanted:
        rel = safe_rel_path(wanted)
        for p in paths:
            if rel is not None and (p == rel or p.name == rel.name):
                return p
        raise BundleError(f"Không có tệp chính '{wanted}' trong các tệp đã tải lên")

    def rank(p: PurePosixPath) -> tuple[int, int, int, str]:
        ext = p.suffix.lower()
        order = MAIN_EXT_ORDER.index(ext) if ext in MAIN_EXT_ORDER else len(MAIN_EXT_ORDER)
        preferred = 0 if p.stem.lower() in ("index", "readme", "main") else 1
        return order, len(p.parts), preferred, str(p).lower()

    candidates = [p for p in paths if p.suffix.lower() in MAIN_EXT_ORDER]
    if not candidates:
        raise BundleError("Không tìm thấy tài liệu (.md, .docx, .pdf, .txt) trong các tệp")
    return min(candidates, key=rank)
