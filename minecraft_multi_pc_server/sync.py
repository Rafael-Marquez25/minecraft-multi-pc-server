from __future__ import annotations

from dataclasses import dataclass
import fnmatch
from pathlib import Path
import platform
import shutil
import subprocess


class SyncError(RuntimeError):
    """Raised when server files cannot be synchronized."""


@dataclass(frozen=True)
class SyncResult:
    source: Path
    destination: Path
    backend: str


class Syncer:
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
        return any(fnmatch.fnmatch(value, pattern) or fnmatch.fnmatch(relative.name, pattern) for pattern in self.ignore_patterns)
