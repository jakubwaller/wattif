"""Physics and geometry tests. All pure functions, no network, no data."""

from __future__ import annotations

import pytest

from wattif.model.geo import bearing_deg, headwind_ms, scale_wind_to_rider
from wattif.model.physics import (
    air_density,
    equivalent_flat_still_air_speed,
    power_at_wheel,
    speed_for_power,
)

RIDER = dict(mass_kg=85.0, cda=0.40, crr=0.008)


def test_bearing_north():
    assert bearing_deg(53.55, 10.0, 53.56, 10.0) == pytest.approx(0.0, abs=0.1)


def test_bearing_east():
    assert bearing_deg(53.55, 10.0, 53.55, 10.01) == pytest.approx(90.0, abs=0.1)


def test_bearing_south():
    assert bearing_deg(53.55, 10.0, 53.54, 10.0) == pytest.approx(180.0, abs=0.1)


def test_headwind_straight_into_it():
    # Wind from the north, riding north: the whole wind opposes you.
    assert headwind_ms(5.0, 0.0, 0.0) == pytest.approx(5.0)


def test_tailwind_is_negative():
    assert headwind_ms(5.0, 0.0, 180.0) == pytest.approx(-5.0)


def test_crosswind_has_no_along_component():
    assert headwind_ms(5.0, 0.0, 90.0) == pytest.approx(0.0, abs=1e-9)


def test_wind_scaled_down_from_ten_metres():
    # Rider height sees roughly two thirds of the reported 10 m wind.
    assert scale_wind_to_rider(10.0) == pytest.approx(6.0, abs=0.6)


def test_air_density_at_standard_conditions():
    assert air_density(15.0, 1013.25) == pytest.approx(1.225, abs=0.005)


def test_warm_air_is_thinner():
    assert air_density(30.0, 1013.25) < air_density(0.0, 1013.25)


def test_no_speed_no_power():
    assert power_at_wheel(0.0, **RIDER) == 0.0


def test_power_rises_faster_than_speed():
    # Drag is cubic in power terms, so doubling speed costs far more than double.
    assert power_at_wheel(14.0, **RIDER) > 4 * power_at_wheel(7.0, **RIDER)


def test_climbing_costs_more_than_flat():
    assert power_at_wheel(7.0, gradient=0.05, **RIDER) > power_at_wheel(7.0, **RIDER)


def test_headwind_costs_more_than_still_air():
    assert power_at_wheel(7.0, headwind_ms=3.0, **RIDER) > power_at_wheel(7.0, **RIDER)


def test_tailwind_costs_less():
    assert power_at_wheel(7.0, headwind_ms=-3.0, **RIDER) < power_at_wheel(7.0, **RIDER)


def test_speed_for_power_inverts_the_model():
    watts = power_at_wheel(7.5, **RIDER)
    assert speed_for_power(watts, **RIDER) == pytest.approx(7.5, abs=1e-3)


def test_speed_for_power_inverts_with_wind_and_slope():
    watts = power_at_wheel(6.0, gradient=0.03, headwind_ms=2.0, **RIDER)
    recovered = speed_for_power(watts, gradient=0.03, headwind_ms=2.0, **RIDER)
    assert recovered == pytest.approx(6.0, abs=1e-3)


def test_zero_power_is_zero_speed():
    assert speed_for_power(0.0, **RIDER) == 0.0


def test_tailwind_ride_normalises_downward():
    # Riding 8 m/s with a 4 m/s tailwind is easier than 8 m/s in still air, so the
    # still-air equivalent of that effort must be slower than 8 m/s.
    watts = power_at_wheel(8.0, headwind_ms=-4.0, **RIDER)
    assert equivalent_flat_still_air_speed(watts, **RIDER) < 8.0


def test_headwind_ride_normalises_upward():
    watts = power_at_wheel(6.0, headwind_ms=4.0, **RIDER)
    assert equivalent_flat_still_air_speed(watts, **RIDER) > 6.0


def test_faster_tyres_buy_speed_at_equal_power():
    watts = power_at_wheel(7.0, mass_kg=85.0, cda=0.40, crr=0.008)
    slow = speed_for_power(watts, mass_kg=85.0, cda=0.40, crr=0.008)
    fast = speed_for_power(watts, mass_kg=85.0, cda=0.40, crr=0.004)
    assert fast > slow
