from __future__ import annotations

from dataclasses import dataclass
import fnmatch
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import uuid
import zipfile


class SyncError(RuntimeError):
    """Raised when server files cannot be synchronized."""


@dataclass(frozen=True)
class SyncResult:
    source: Path
    destination: Path
    backend: str


class Syncer:
    MAX_ARCHIVE_ENTRIES = 200_000
    MAX_UNCOMPRESSED_BYTES = 100 * 1024**3

    def __init__(self, ignore_patterns: list[str] | None = None, prefer_robocopy: bool | None = None):
        self.ignore_patterns = ignore_patterns or []
        self.prefer_robocopy = platform.system() == "Windows" if prefer_robocopy is None else prefer_robocopy

    def mirror(self, source: Path, destination: Path) -> SyncResult:
        if not source.exists() or not source.is_dir():
            raise SyncError(f"Source directory does not exist: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.prefer_robocopy:
            self._robocopy(source, destination)
            return SyncResult(source, destination, "robocopy")
        self._python_mirror(source, destination)
        return SyncResult(source, destination, "python")

    def extract_archive(self, source: Path, destination: Path) -> SyncResult:
        if not source.exists() or not source.is_file():
            raise SyncError(f"Archive does not exist: {source}")
        if destination.is_symlink():
            raise SyncError(f"Refusing to replace symlink destination: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        backup = destination.with_name(f".{destination.name}.backup-{uuid.uuid4().hex}")
        replaced_old = False
        try:
            with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
                temp_dir = Path(tmp)
                with zipfile.ZipFile(source) as archive:
                    self._validate_archive(archive, destination.parent)
                    self._safe_extract(archive, temp_dir)
                if destination.exists():
                    destination.replace(backup)
                    replaced_old = True
                try:
                    temp_dir.replace(destination)
                except Exception:
                    if replaced_old and backup.exists() and not destination.exists():
                        backup.replace(destination)
                    raise
            if backup.exists():
                try:
                    shutil.rmtree(backup)
                except OSError:
                    pass
        except (SyncError, OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            if replaced_old and backup.exists() and not destination.exists():
                try:
                    backup.replace(destination)
                except OSError:
                    pass
            if isinstance(exc, SyncError):
                raise
            raise SyncError(f"Cannot extract archive {source}: {exc}") from exc
        return SyncResult(source, destination, "zip")

    def create_archive(self, source: Path, destination: Path, compression_level: int = 1) -> SyncResult:
        if not source.exists() or not source.is_dir():
            raise SyncError(f"Source directory does not exist: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_file = destination.with_name(destination.name + ".tmp")
        compression = zipfile.ZIP_STORED if compression_level == 0 else zipfile.ZIP_DEFLATED
        zip_options: dict[str, object] = {"compression": compression}
        if compression != zipfile.ZIP_STORED:
            zip_options["compresslevel"] = compression_level
        try:
            if temp_file.exists():
                temp_file.unlink()
            files_written = 0
            with zipfile.ZipFile(temp_file, "w", **zip_options) as archive:
                for item in source.rglob("*"):
                    relative = item.relative_to(source)
                    if self._ignored(relative):
                        continue
                    if item.is_symlink():
                        raise SyncError(f"Refusing to archive symbolic link: {item}")
                    if item.is_file():
                        archive.write(item, relative.as_posix())
                        files_written += 1
            if files_written == 0:
                raise SyncError(f"Refusing to create an empty server archive from {source}")
            with zipfile.ZipFile(temp_file) as archive:
                bad_member = archive.testzip()
                if bad_member is not None:
                    raise SyncError(f"Archive validation failed at {bad_member}")
            temp_file.replace(destination)
        except (SyncError, OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            if temp_file.exists():
                temp_file.unlink()
            if isinstance(exc, SyncError):
                raise
            raise SyncError(f"Cannot create archive {destination}: {exc}") from exc
        return SyncResult(source, destination, "zip")

    def _robocopy(self, source: Path, destination: Path) -> None:
        command = ["robocopy", str(source), str(destination), "/MIR"]
        for pattern in self.ignore_patterns:
            command.extend(["/XF", pattern])
        completed = subprocess.run(command, check=False)
        if completed.returncode > 7:
            raise SyncError(f"robocopy failed with exit code {completed.returncode}")

    def _python_mirror(self, source: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        self._copy_tree(source, destination, source)
        self._delete_extra(source, destination, source, destination)

    def _copy_tree(self, source: Path, destination: Path, root_source: Path) -> None:
        for item in source.iterdir():
            relative = item.relative_to(root_source)
            if self._ignored(relative):
                continue
            target = destination / item.name
            if item.is_dir():
                if target.exists() and not target.is_dir():
                    target.unlink()
                target.mkdir(parents=True, exist_ok=True)
                self._copy_tree(item, target, root_source)
            elif item.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.is_dir():
                    shutil.rmtree(target)
                shutil.copy2(item, target)

    def _delete_extra(self, source: Path, destination: Path, root_source: Path, root_destination: Path) -> None:
        for item in list(destination.iterdir()):
            relative = item.relative_to(root_destination)
            if self._ignored(relative):
                continue
            source_item = source / item.name
            if not source_item.exists():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            elif item.is_dir() != source_item.is_dir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            elif item.is_dir() and source_item.is_dir():
                self._delete_extra(source_item, item, root_source, root_destination)

    def _ignored(self, relative: Path) -> bool:
        value = relative.as_posix()
        return any(
            fnmatch.fnmatch(value, pattern)
            or fnmatch.fnmatch(relative.name, pattern)
            or (pattern.endswith("/**") and (value == pattern[:-3] or value.startswith(pattern[:-3] + "/")))
            for pattern in self.ignore_patterns
        )

    def _safe_extract(self, archive: zipfile.ZipFile, destination: Path) -> None:
        root = destination.resolve()
        for member in archive.infolist():
            normalized = member.filename.replace("\\", "/")
            if not normalized or "\x00" in normalized or normalized.startswith(("/", "\\")):
                raise SyncError(f"Unsafe archive member: {member.filename}")
            if Path(normalized).drive or any(part == ".." for part in normalized.split("/")):
                raise SyncError(f"Unsafe archive member: {member.filename}")
            unix_mode = member.external_attr >> 16
            if unix_mode & 0o170000 == 0o120000:
                raise SyncError(f"Symbolic links are not allowed in archives: {member.filename}")
            target = (destination / member.filename).resolve()
            if root not in (target, *target.parents):
                raise SyncError(f"Unsafe archive member: {member.filename}")
        archive.extractall(destination)

    def _validate_archive(self, archive: zipfile.ZipFile, destination_parent: Path) -> None:
        members = archive.infolist()
        if not members:
            raise SyncError("Archive is empty")
        if len(members) > self.MAX_ARCHIVE_ENTRIES:
            raise SyncError(f"Archive has too many entries ({len(members):,})")
        normalized_names = [member.filename.replace("\\", "/").casefold().rstrip("/") for member in members]
        if len(normalized_names) != len(set(normalized_names)):
            raise SyncError("Archive contains duplicate file paths")
        total_size = sum(member.file_size for member in members)
        if total_size > self.MAX_UNCOMPRESSED_BYTES:
            raise SyncError(f"Archive expands beyond the safety limit ({total_size:,} bytes)")
        free_space = shutil.disk_usage(destination_parent).free
        if total_size > free_space:
            raise SyncError(
                f"Not enough free disk space: archive needs {total_size:,} bytes, {free_space:,} available"
            )
