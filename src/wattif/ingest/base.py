"""The source interface.

Garmin has no personal API. Their developer programme rejects individuals, so every
automated route is a reverse-engineered one that Garmin tolerates rather than supports —
the library that used to do this, garth, was killed outright in March 2026 when Garmin
changed the login flow.

So sources are deliberately thin and interchangeable. A source's only job is to put FIT
files in a directory. When the convenient one breaks, the manual export still works and
nothing downstream notices.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Source(Protocol):
    """Anything that can deliver FIT files onto local disk."""

    name: str

    def fetch(self, dest: Path, limit: int | None = None) -> list[Path]:
        """Put FIT files in `dest` and return the paths that are new."""
        ...
