"""Within-ride A/B testing.

Comparing whole rides is hopeless for equipment questions: the between-ride scatter in
speed-per-heartbeat is around 5%, so detecting a 4% change needs roughly sixteen rides per
arm. Putting both arms inside the *same* ride removes weather, fatigue, fitness and route
from the comparison, and turns one ride into several paired observations.

Two things make the estimate honest:

* A warm-up is discarded at the start of every block, because heart rate lags a change in
  position or effort by a minute or more and the first samples belong to the previous arm.
* Blocks are compared against the mean of their two neighbours rather than against the
  arm's overall mean. Any drift across the ride - fatigue, warming muscles, a slow tailwind
  shift - is linear over a few blocks, and a symmetric neighbour comparison cancels it
  exactly. The naive difference is reported alongside so the two can be sanity-checked.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl

from ..config import Profile
from ..weather.open_meteo import WeatherArchive
from .ride import RideMetrics, analyse

MIN_BLOCK_S = 120.0  # after warm-up removal; shorter blocks are mostly noise
DEFAULT_WARMUP_S = 90.0

# Two-sided 95% critical values. A t-table beats pulling in scipy for one number.
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
    9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074,
    23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045,
    30: 2.042,
}


def t_critical(df: int) -> float:
    if df <= 0:
        return float("nan")
    return _T95.get(df, 1.960)


@dataclass(frozen=True)
class Block:
    """One labelled segment of one ride."""

    activity_id: str
    index: int
    arm: str
    metrics: RideMetrics

    @property
    def efficiency(self) -> float:
        return self.metrics.efficiency


@dataclass(frozen=True)
class Comparison:
    """The result of an A/B test, in the units the rider cares about."""

    arms: tuple[str, str]
    n_rides: int
    n_blocks: int
    n_pairs: int
    mean_efficiency: dict[str, float]
    mean_equivalent_kmh: dict[str, float]
    mean_hr: dict[str, float]
    delta: float
    delta_pct: float
    ci_pct: tuple[float, float]
    naive_delta_pct: float
    ride_deltas: list[float]

    @property
    def significant(self) -> bool:
        low, high = self.ci_pct
        return low > 0 or high < 0


def split_by_laps(
    records: pl.DataFrame, laps: pl.DataFrame, triggers: tuple[str, ...] | None = ("manual",)
) -> list[pl.DataFrame]:
    """Split a ride at its lap boundaries.

    `triggers` filters which laps count as block starts. The default keeps only laps the
    rider pressed the button for; pass None to use every lap, including the watch's
    distance auto-laps.
    """
    if "lap" not in records.columns or not laps.height:
        return []

    wanted = laps
    if triggers is not None:
        wanted = laps.filter(pl.col("trigger").is_in(list(triggers)))
    if not wanted.height:
        return []

    keep = set(wanted["lap"].to_list())
    out = []
    for lap in sorted(keep):
        block = records.filter(pl.col("lap") == lap)
        if block.height:
            out.append(block)
    return out


def split_by_time(records: pl.DataFrame, every_s: float) -> list[pl.DataFrame]:
    """Split a ride into fixed-length windows of elapsed clock time."""
    if not records.height or every_s <= 0:
        return []
    df = records.sort("timestamp")
    start = df["timestamp"][0]
    df = df.with_columns(
        _block=((pl.col("timestamp") - start).dt.total_seconds() // every_s).cast(pl.Int32)
    )
    return [g.drop("_block") for _, g in df.group_by("_block", maintain_order=True)]


def drop_warmup(block: pl.DataFrame, warmup_s: float) -> pl.DataFrame:
    """Discard the opening seconds of a block, where heart rate still reflects the last one."""
    if not block.height or warmup_s <= 0:
        return block
    start = block["timestamp"][0]
    return block.filter((pl.col("timestamp") - start).dt.total_seconds() >= warmup_s)


def assign_arms(count: int, arms: tuple[str, str], pattern: str = "AB") -> list[str]:
    """Label blocks by repeating a pattern such as AB or ABBA."""
    letters = {"A": arms[0], "B": arms[1]}
    cleaned = [c for c in pattern.upper() if c in letters]
    if not cleaned:
        raise ValueError("pattern must contain at least one of A or B")
    return [letters[cleaned[i % len(cleaned)]] for i in range(count)]


def analyse_blocks(
    activity_id: str,
    blocks: list[pl.DataFrame],
    arms: tuple[str, str],
    profile: Profile,
    archive: WeatherArchive | None = None,
    mass_kg: float | None = None,
    warmup_s: float = DEFAULT_WARMUP_S,
    pattern: str = "AB",
) -> list[Block]:
    """Turn one ride's raw blocks into labelled, corrected measurements."""
    labels = assign_arms(len(blocks), arms, pattern)
    out: list[Block] = []
    for index, (raw, arm) in enumerate(zip(blocks, labels, strict=False)):
        trimmed = drop_warmup(raw, warmup_s)
        if trimmed.height < 10:
            continue
        span = (trimmed["timestamp"].max() - trimmed["timestamp"].min()).total_seconds()
        if span < MIN_BLOCK_S:
            continue
        metrics = analyse(f"{activity_id}#{index}", trimmed, profile, archive, mass_kg=mass_kg)
        if metrics is None or not metrics.mean_hr:
            continue
        out.append(Block(activity_id=activity_id, index=index, arm=arm, metrics=metrics))
    return out


def _neighbour_deltas(blocks: list[Block], arms: tuple[str, str]) -> list[float]:
    """Drift-cancelling estimates of (arm B - arm A) from one ride.

    Only interior blocks whose two neighbours are consecutive and share the *other* arm
    contribute, which is what makes the subtraction symmetric in time.
    """
    ordered = sorted(blocks, key=lambda b: b.index)
    deltas = []
    for i in range(1, len(ordered) - 1):
        before, here, after = ordered[i - 1], ordered[i], ordered[i + 1]
        if after.index - here.index != 1 or here.index - before.index != 1:
            continue
        if before.arm != after.arm or before.arm == here.arm:
            continue
        middle = (before.efficiency + after.efficiency) / 2
        if not middle:
            continue
        signed = here.efficiency - middle
        deltas.append(signed if here.arm == arms[1] else -signed)
    return deltas


def compare(blocks: list[Block], arms: tuple[str, str]) -> Comparison | None:
    """Pool blocks across rides into one paired estimate of the difference between arms."""
    if not blocks:
        return None

    by_ride: dict[str, list[Block]] = {}
    for block in blocks:
        by_ride.setdefault(block.activity_id, []).append(block)

    ride_deltas, naive = [], []
    pairs = 0
    for ride_blocks in by_ride.values():
        deltas = _neighbour_deltas(ride_blocks, arms)
        if deltas:
            ride_deltas.append(sum(deltas) / len(deltas))
            pairs += len(deltas)
        a = [b.efficiency for b in ride_blocks if b.arm == arms[0]]
        b = [b.efficiency for b in ride_blocks if b.arm == arms[1]]
        if a and b:
            naive.append(sum(b) / len(b) - sum(a) / len(a))

    def mean_of(field: str) -> dict[str, float]:
        out = {}
        for arm in arms:
            values = [getattr(x.metrics, field) for x in blocks if x.arm == arm]
            out[arm] = sum(values) / len(values) if values else float("nan")
        return out

    baseline_by_arm = mean_of("efficiency")
    baseline = baseline_by_arm.get(arms[0]) or float("nan")

    if not ride_deltas:
        return Comparison(
            arms=arms, n_rides=len(by_ride), n_blocks=len(blocks), n_pairs=0,
            mean_efficiency=baseline_by_arm,
            mean_equivalent_kmh=mean_of("equivalent_kmh"),
            mean_hr=mean_of("mean_hr"),
            delta=float("nan"), delta_pct=float("nan"),
            ci_pct=(float("nan"), float("nan")),
            naive_delta_pct=(sum(naive) / len(naive) / baseline * 100) if naive else float("nan"),
            ride_deltas=[],
        )

    n = len(ride_deltas)
    mean = sum(ride_deltas) / n
    if n > 1:
        variance = sum((d - mean) ** 2 for d in ride_deltas) / (n - 1)
        half = t_critical(n - 1) * math.sqrt(variance / n)
    else:
        half = float("nan")

    return Comparison(
        arms=arms,
        n_rides=len(by_ride),
        n_blocks=len(blocks),
        n_pairs=pairs,
        mean_efficiency=baseline_by_arm,
        mean_equivalent_kmh=mean_of("equivalent_kmh"),
        mean_hr=mean_of("mean_hr"),
        delta=mean,
        delta_pct=mean / baseline * 100,
        ci_pct=((mean - half) / baseline * 100, (mean + half) / baseline * 100),
        naive_delta_pct=(sum(naive) / len(naive) / baseline * 100) if naive else float("nan"),
        ride_deltas=ride_deltas,
    )


def rides_needed(cv_pct: float, effect_pct: float, paired: bool = True) -> int:
    """How many rides a given effect needs, at 80% power and p<0.05.

    `cv_pct` is the scatter of the paired per-ride delta when paired, or of single-ride
    efficiency when not. The paired design is worth roughly an order of magnitude.
    """
    if effect_pct <= 0 or cv_pct <= 0:
        return 0
    factor = 1 if paired else 2
    return math.ceil(factor * (1.96 + 0.84) ** 2 * cv_pct**2 / effect_pct**2)
