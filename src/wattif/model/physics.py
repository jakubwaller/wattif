"""A power model for a bicycle, and its inverse.

Absolute watts from this are not trustworthy without a power meter: CdA and Crr are
estimates, and an error in either scales the whole answer. What *is* trustworthy is the
comparison. Running the same ride twice with one variable changed, or normalising two
rides onto the same conditions, cancels most of that error because it appears identically
on both sides.
"""

from __future__ import annotations

import math

G = 9.80665
R_SPECIFIC_AIR = 287.058  # J/(kg K), dry air


def air_density(temperature_c: float, pressure_hpa: float) -> float:
    """Density from the ideal gas law. ~1.225 kg/m^3 at 15 C and 1013 hPa."""
    return (pressure_hpa * 100.0) / (R_SPECIFIC_AIR * (temperature_c + 273.15))


def power_at_wheel(
    speed_ms: float,
    *,
    gradient: float = 0.0,
    headwind_ms: float = 0.0,
    accel_ms2: float = 0.0,
    mass_kg: float,
    cda: float,
    crr: float,
    rho: float = 1.225,
) -> float:
    """Watts needed at the wheel to move at `speed_ms` under these conditions.

    Drag acts on air speed, which is ground speed plus any headwind, but the work is done
    against the ground, so the drag *force* is squared in air speed while power multiplies
    by ground speed. Conflating the two is the usual way this calculation goes wrong.
    """
    if speed_ms <= 0:
        return 0.0

    air_speed = speed_ms + headwind_ms
    # A tailwind faster than the rider still produces drag, pushing rather than resisting.
    drag_force = 0.5 * rho * cda * air_speed * abs(air_speed)

    angle = math.atan(gradient)
    rolling_force = crr * mass_kg * G * math.cos(angle)
    gravity_force = mass_kg * G * math.sin(angle)
    inertial_force = mass_kg * accel_ms2

    return (drag_force + rolling_force + gravity_force + inertial_force) * speed_ms


def speed_for_power(
    power_w: float,
    *,
    mass_kg: float,
    cda: float,
    crr: float,
    rho: float = 1.225,
    gradient: float = 0.0,
    headwind_ms: float = 0.0,
    tolerance: float = 1e-4,
) -> float:
    """Invert the model: the speed this many watts buys under these conditions.

    Bisection rather than a closed form, because the cubic gains extra terms as soon as
    wind or gradient are involved and the numeric answer is exact enough at 0.1 mm/s.
    """
    if power_w <= 0:
        return 0.0

    lo, hi = 0.0, 30.0
    while power_at_wheel(hi, gradient=gradient, headwind_ms=headwind_ms, mass_kg=mass_kg,
                         cda=cda, crr=crr, rho=rho) < power_w and hi < 100.0:
        hi *= 2

    while hi - lo > tolerance:
        mid = (lo + hi) / 2
        needed = power_at_wheel(mid, gradient=gradient, headwind_ms=headwind_ms,
                                mass_kg=mass_kg, cda=cda, crr=crr, rho=rho)
        if needed < power_w:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def equivalent_flat_still_air_speed(
    power_w: float, *, mass_kg: float, cda: float, crr: float, rho: float = 1.225
) -> float:
    """The speed this effort would have produced on flat ground in still air.

    This is the number that makes two rides comparable. A tailwind day and a hilly day
    both collapse onto the same axis, so what is left of the difference is the rider.
    """
    return speed_for_power(power_w, mass_kg=mass_kg, cda=cda, crr=crr, rho=rho)
