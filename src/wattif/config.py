"""Rider profile and on-disk layout.

Everything wattif writes lives under one root directory, which is gitignored by default.
Nothing here ever leaves the machine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ENV_HOME = "WATTIF_HOME"


def default_root() -> Path:
    """Where activity data lives. Override with WATTIF_HOME."""
    env = os.environ.get(ENV_HOME)
    return Path(env).expanduser() if env else Path.cwd() / "data"


@dataclass(frozen=True)
class Profile:
    """The rider and bike, as physical inputs to the model.

    The aerodynamic and rolling numbers are estimates and will be wrong in absolute terms.
    That is tolerable because the questions wattif answers are comparative: the same error
    appears on both sides of a what-if and largely cancels.
    """

    rider_kg: float
    bike_kg: float
    cda: float = 0.40
    crr: float = 0.0080
    drivetrain_loss: float = 0.03
    max_hr: int | None = None
    resting_hr: int | None = None

    @property
    def total_kg(self) -> float:
        return self.rider_kg + self.bike_kg

    @classmethod
    def load(cls, path: Path | None = None) -> Profile:
        candidates = [path] if path else [
            Path("config/profile.yaml"),
            Path.home() / ".wattif/profile.yaml",
        ]
        for candidate in candidates:
            if candidate and candidate.is_file():
                data = yaml.safe_load(candidate.read_text()) or {}
                known = {f for f in cls.__dataclass_fields__}
                return cls(**{k: v for k, v in data.items() if k in known and v is not None})
        raise FileNotFoundError(
            "No profile found. Copy config/profile.example.yaml to config/profile.yaml and edit it."
        )


@dataclass(frozen=True)
class Paths:
    """Directory layout under the data root."""

    root: Path

    @property
    def raw(self) -> Path:
        """Downloaded FIT files, exactly as the source gave them."""
        return self.root / "raw"

    @property
    def activities(self) -> Path:
        """One parquet of per-record streams per activity."""
        return self.root / "activities"

    @property
    def catalog(self) -> Path:
        """One row per activity: the summary table."""
        return self.root / "catalog.parquet"

    def ensure(self) -> Paths:
        self.raw.mkdir(parents=True, exist_ok=True)
        self.activities.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def default(cls) -> Paths:
        return cls(default_root()).ensure()
