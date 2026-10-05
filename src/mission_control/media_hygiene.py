from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mov", ".mkv", ".avi", ".wmv", ".m4v", ".webm",
    ".mpeg", ".mpg", ".3gp", ".flv", ".mts", ".m2ts",
})
AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus"})
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff", ".gif"})

# .ts is intentionally excluded: Codestra treats TypeScript source as source code,
# never as personal video/media, even though MPEG transport streams may also use .ts.
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS

EXCLUDED_PARTS = frozenset({
    ".git", "node_modules", ".venv", "venv", "env", ".tox", "__pycache__",
    "site-packages", "github", "codestra-development-hub", "appdata",
    "windows", "program files", "program files (x86)", "programdata",
})

DEFAULT_ARCHIVE_THRESHOLD_BYTES = 250 * 1024 * 1024


@dataclass(frozen=True)
class MediaFile:
    path: Path
    size: int
    mtime: float
    kind: str
    sha256: str | None = None


@dataclass(frozen=True)
class DuplicateGroup:
    sha256: str
    canonical: MediaFile
    redundant: tuple[MediaFile, ...]


def _parts_lower(path: Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in path.parts)


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_PARTS for part in _parts_lower(path))


def classify_media(path: Path) -> str | None:
    if is_excluded(path):
        return None
    suffix = path.suffix.casefold()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    return None


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_media(roots: Iterable[Path]) -> list[MediaFile]:
    found: list[MediaFile] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            kind = classify_media(path)
            if kind is None:
                continue
            stat = path.stat()
            found.append(MediaFile(path=path, size=stat.st_size, mtime=stat.st_mtime, kind=kind))
    return found


def with_hashes(files: Iterable[MediaFile]) -> list[MediaFile]:
    by_size: dict[int, list[MediaFile]] = {}
    for item in files:
        by_size.setdefault(item.size, []).append(item)

    result: list[MediaFile] = []
    for same_size in by_size.values():
        if len(same_size) == 1:
            result.extend(same_size)
            continue
        for item in same_size:
            result.append(
                MediaFile(
                    path=item.path,
                    size=item.size,
                    mtime=item.mtime,
                    kind=item.kind,
                    sha256=sha256_file(item.path),
                )
            )
    return result


def _canonical_rank(item: MediaFile) -> tuple[int, int, str]:
    parts = _parts_lower(item.path)
    in_downloads = "downloads" in parts
    # Prefer non-Downloads, then shallower paths, then stable lexical order.
    return (1 if in_downloads else 0, len(parts), str(item.path).casefold())


def exact_duplicate_groups(files: Iterable[MediaFile]) -> list[DuplicateGroup]:
    hashed = with_hashes(files)
    buckets: dict[tuple[int, str], list[MediaFile]] = {}
    for item in hashed:
        if item.sha256 is None:
            continue
        buckets.setdefault((item.size, item.sha256), []).append(item)

    groups: list[DuplicateGroup] = []
    for (_, digest), members in buckets.items():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=_canonical_rank)
        groups.append(
            DuplicateGroup(
                sha256=digest,
                canonical=ordered[0],
                redundant=tuple(ordered[1:]),
            )
        )
    return sorted(groups, key=lambda group: str(group.canonical.path).casefold())


def redundant_paths(groups: Iterable[DuplicateGroup]) -> set[Path]:
    return {item.path for group in groups for item in group.redundant}


def archive_target(
    item: MediaFile,
    archive_root: Path,
    *,
    threshold_bytes: int = DEFAULT_ARCHIVE_THRESHOLD_BYTES,
) -> Path | None:
    if item.kind != "video" or item.size <= threshold_bytes:
        return None
    if item.path.drive.casefold() != "c:":
        return None
    stamp = datetime.fromtimestamp(item.mtime, tz=timezone.utc)
    return archive_root / f"{stamp.year:04d}" / f"{stamp.month:02d}" / item.path.name


def plan_archive(
    files: Iterable[MediaFile],
    groups: Iterable[DuplicateGroup],
    archive_root: Path,
    *,
    threshold_bytes: int = DEFAULT_ARCHIVE_THRESHOLD_BYTES,
) -> list[tuple[Path, Path]]:
    redundant = redundant_paths(groups)
    planned: list[tuple[Path, Path]] = []
    for item in files:
        if item.path in redundant:
            continue
        target = archive_target(item, archive_root, threshold_bytes=threshold_bytes)
        if target is not None:
            planned.append((item.path, target))
    return sorted(planned, key=lambda pair: str(pair[0]).casefold())


def duplicate_bytes(groups: Iterable[DuplicateGroup]) -> int:
    return sum(item.size for group in groups for item in group.redundant)
