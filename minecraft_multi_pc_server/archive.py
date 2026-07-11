from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re


class ArchiveError(RuntimeError):
    """Raised when a dated archive cannot be selected or created."""


def timestamp_from_archive_name(name: str) -> datetime | None:
    patterns = (
        r"(\d{8})[-_](\d{6})",
        r"(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})",
    )
    for pattern in patterns:
        match = re.search(pattern, name)
        if not match:
            continue
        try:
            if len(match.groups()) == 2:
                return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
            return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
        except ValueError:
            continue
    return None


def resolve_latest_archive(archive_dir: Path) -> Path:
    if not archive_dir.exists() or not archive_dir.is_dir():
        raise ArchiveError(f"Archive directory does not exist: {archive_dir}")
    archives = sorted(path for path in archive_dir.glob("*.zip") if path.is_file())
    if not archives:
        raise ArchiveError(f"No ZIP archives found in {archive_dir}. Create the first ZIP before running.")

    dated = [(timestamp_from_archive_name(path.name), path) for path in archives]
    dated = [(timestamp, path) for timestamp, path in dated if timestamp is not None]
    if dated:
        return max(dated, key=lambda item: (item[0], item[1].name))[1]
    return max(archives, key=lambda path: (path.stat().st_mtime, path.name))


def ordered_archives_newest_first(archive_dir: Path) -> list[Path]:
    archives = [path for path in archive_dir.glob("*.zip") if path.is_file()]
    return sorted(archives, key=_archive_order_key, reverse=True)


def prune_old_archives(archive_dir: Path, keep: int = 3, protected: Path | None = None) -> list[Path]:
    if keep < 1:
        raise ArchiveError("Must keep at least one archive")
    ordered = ordered_archives_newest_first(archive_dir)
    if protected is not None and protected in ordered:
        ordered.remove(protected)
        kept = [protected, *ordered[: keep - 1]]
    else:
        kept = ordered[:keep]
    removed: list[Path] = []
    for archive in ordered:
        if archive in kept:
            continue
        archive.unlink()
        removed.append(archive)
    return removed


def build_dated_archive_path(archive_dir: Path, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = archive_dir / f"server-{timestamp}.zip"
    if not base.exists():
        return base
    suffix = 2
    while True:
        candidate = archive_dir / f"server-{timestamp}-{suffix}.zip"
        if not candidate.exists():
            return candidate
        suffix += 1


def _archive_order_key(path: Path) -> tuple[datetime, str]:
    timestamp = timestamp_from_archive_name(path.name)
    if timestamp is None:
        timestamp = datetime.fromtimestamp(path.stat().st_mtime)
    return timestamp, path.name
