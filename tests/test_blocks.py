"""Within-ride A/B tests.

Fixtures here are synthetic by policy: no real ride ever goes into this repository.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from wattif.metrics.blocks import (
    Block,
    _neighbour_deltas,
    assign_arms,
    compare,
    drop_warmup,
    rides_needed,
    split_by_laps,
    split_by_time,
    t_critical,
)
from wattif.metrics.ride import RideMetrics

ARMS = ("hoods", "drops")


def metrics(efficiency: float, hr: float = 150.0) -> RideMetrics:
    """A RideMetrics whose efficiency is exactly what the test asked for."""
    return RideMetrics(
        activity_id="x",
        moving_s=600.0,
        distance_m=4000.0,
        raw_kmh=24.0,
        equivalent_kmh=efficiency * hr,
        mean_power_w=170.0,
        mean_hr=hr,
        mean_headwind_ms=0.0,
        ascent_m=10.0,
        weather=True,
    )


def blocks_from(values: list[float], activity_id: str = "ride1") -> list[Block]:
    arms = assign_arms(len(values), ARMS)
    return [
        Block(activity_id=activity_id, index=i, arm=arms[i], metrics=metrics(v))
        for i, v in enumerate(values)
    ]


def stream(seconds: int, start: datetime | None = None) -> pl.DataFrame:
    begin = start or datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    return pl.DataFrame(
        {
            "timestamp": [begin + timedelta(seconds=s) for s in range(seconds)],
            "speed_ms": [7.0] * seconds,
            "distance_m": [7.0 * s for s in range(seconds)],
            "heart_rate": [150.0] * seconds,
            "lap": [s // 300 for s in range(seconds)],
        }
    )


def test_arms_alternate_from_the_pattern():
    assert assign_arms(5, ARMS) == ["hoods", "drops", "hoods", "drops", "hoods"]
    assert assign_arms(4, ARMS, "ABBA") == ["hoods", "drops", "drops", "hoods"]


def test_an_empty_pattern_is_rejected():
    with pytest.raises(ValueError):
        assign_arms(3, ARMS, "??")


def test_time_split_cuts_at_the_requested_length():
    blocks = split_by_time(stream(900), every_s=300)
    assert len(blocks) == 3
    assert all(b.height == 300 for b in blocks)


def test_warmup_is_removed_from_the_front():
    trimmed = drop_warmup(stream(300), warmup_s=90)
    assert trimmed.height == 210
    assert trimmed["timestamp"][0].second == 30


def test_lap_split_can_filter_on_the_trigger():
    laps = pl.DataFrame(
        {
            "lap": [0, 1, 2],
            "trigger": ["manual", "distance", "manual"],
        }
    )
    records = stream(900)
    assert len(split_by_laps(records, laps, triggers=("manual",))) == 2
    assert len(split_by_laps(records, laps, triggers=None)) == 3


def test_neighbour_comparison_cancels_a_linear_drift():
    # Efficiency falls by 0.01 per block through the ride, and arm B is worth +0.02.
    values = []
    for i in range(7):
        drift = 0.20 - 0.01 * i
        values.append(drift + (0.02 if i % 2 else 0.0))

    deltas = _neighbour_deltas(blocks_from(values), ARMS)
    assert len(deltas) == 5
    assert all(d == pytest.approx(0.02) for d in deltas)


def test_the_naive_difference_is_biased_by_that_drift():
    # An even block count leaves the drift uncancelled in a plain arm-vs-arm mean. An odd
    # one happens to cancel it, which is exactly the sort of accident not to depend on.
    values = [0.20 - 0.01 * i + (0.02 if i % 2 else 0.0) for i in range(6)]
    result = compare(blocks_from(values), ARMS)

    assert result.delta == pytest.approx(0.02)  # the truth
    naive = result.naive_delta_pct / 100 * result.mean_efficiency[ARMS[0]]
    assert naive == pytest.approx(0.01)  # half of it, eaten by the drift


def test_a_block_is_never_paired_across_a_gap():
    values = [0.20, 0.22, 0.20, 0.22]
    blocks = blocks_from(values)
    # Drop the middle block: the survivors are no longer consecutive.
    gapped = [blocks[0], blocks[1], blocks[3]]
    assert _neighbour_deltas(gapped, ARMS) == []


def test_pooling_across_rides_reports_an_interval():
    first = blocks_from([0.20, 0.22, 0.20, 0.22, 0.20], "ride1")
    second = blocks_from([0.19, 0.215, 0.19, 0.215, 0.19], "ride2")
    result = compare(first + second, ARMS)
    assert result.n_rides == 2
    assert result.n_blocks == 10
    assert result.delta == pytest.approx(0.0225)  # the mean of 0.020 and 0.025
    low, high = result.ci_pct
    assert low < result.delta_pct < high


def test_a_single_ride_yields_no_interval():
    result = compare(blocks_from([0.20, 0.22, 0.20]), ARMS)
    assert result.n_rides == 1
    assert result.ci_pct[0] != result.ci_pct[0]  # NaN


def test_no_pairs_is_reported_rather_than_guessed():
    only_one = blocks_from([0.20])
    result = compare(only_one, ARMS)
    assert result.n_pairs == 0
    assert result.delta != result.delta  # NaN


def test_paired_design_needs_far_fewer_rides():
    assert rides_needed(cv_pct=4.72, effect_pct=5.0) < rides_needed(
        cv_pct=4.72, effect_pct=5.0, paired=False
    )
    assert rides_needed(cv_pct=2.0, effect_pct=0.0) == 0


def test_small_samples_get_a_wider_critical_value():
    assert t_critical(2) > t_critical(20) > t_critical(500)
