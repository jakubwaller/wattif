"""Import from a Garmin or Strava bulk export.

This is the path that always works. Garmin Connect: Account Settings, Export Your Data.
Strava: Settings, My Account, Download or Delete Your Account, Request Your Archive.
Slower and manual, but it is sanctioned, it cannot be switched off without notice, and it
needs no credentials at all.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .fit import find_fit_files

FIT_SUFFIXES = (".fit", ".fit.gz")


class ExportArchive:
    """A bulk export, either a .zip or an already-unpacked directory."""

    name = "export"

    def __init__(self, source: Path):
        self.source = Path(source).expanduser()
        if not self.source.exists():
            raise FileNotFoundError(f"No such export: {self.source}")

    def fetch(self, dest: Path, limit: int | None = None) -> list[Path]:
        dest.mkdir(parents=True, exist_ok=True)
        found = self._extract_zip(dest) if zipfile.is_zipfile(self.source) else self._copy_dir(dest)
        return found[:limit] if limit else found

    def _extract_zip(self, dest: Path) -> list[Path]:
        written: list[Path] = []
        with zipfile.ZipFile(self.source) as archive:
            for member in archive.namelist():
                name = Path(member).name
                if not name.lower().endswith(FIT_SUFFIXES) or name.startswith("."):
                    continue
                target = dest / name
                if target.exists():
                    continue
                with archive.open(member) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
                written.append(target)
        return written

    def _copy_dir(self, dest: Path) -> list[Path]:
        if self.source.is_file():
            candidates = [self.source]
        else:
            candidates = find_fit_files(self.source)
        written = []
        for path in candidates:
            target = dest / path.name
            if target.exists():
                continue
            shutil.copy2(path, target)
            written.append(target)
        return written
