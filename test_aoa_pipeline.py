"""
Hardware-independent validation of the AoA math: generates synthetic I/Q
samples for a KNOWN azimuth/elevation (by construction -- computing the
exact phase each antenna in the real 4x4 array would see for a plane wave
from that direction), feeds them through the same estimate_aoa() pipeline
used on real data, and checks the recovered angle matches what we put in.

This is the test we couldn't do against the real station: with live data we
can see the pipeline produce *plausible-looking, clustering* numbers, but
we can't independently know the true angle to check correctness against.
Here we control ground truth exactly.

Run: python test_aoa_pipeline.py
"""

import math
import struct
import cmath

from antenna_array import ANTENNA_POSITIONS
from aoa_estimate import estimate_aoa, project_to_floor, SPEED_OF_LIGHT

FAILURES = []


def check(name: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def generate_synthetic_iq(az_deg, el_deg, freq_mhz, ants_sequence, tspace_us,
                           cfo_slope_rad_per_us=0.0, amplitude=1000.0,
                           ref_guard_us=12.0, n_cycles=2):
    """Builds iq bytes exactly as a real MG10-B scan record would encode
    them, for a plane wave arriving from (az_deg, el_deg) under this
    project's own direction-cosine convention (u=sin(az), v=sin(el))."""
    wavelength_m = SPEED_OF_LIGHT / (freq_mhz * 1e6)
    u = math.sin(math.radians(az_deg))
    v = math.sin(math.radians(el_deg))

    def ideal_phase(ant_id):
        x, y = ANTENNA_POSITIONS[ant_id]
        return (2 * math.pi / wavelength_m) * (x * u + y * v)

    ref_count = round(ref_guard_us / tspace_us)
    ref_antenna = ants_sequence[1]
    switch_seq = ants_sequence[2:]

    samples = []
    t = 0.0
    for _ in range(ref_count):
        phase = ideal_phase(ref_antenna) + cfo_slope_rad_per_us * t
        samples.append(amplitude * cmath.exp(1j * phase))
        t += tspace_us
    for i in range(len(switch_seq) * n_cycles):
        ant = switch_seq[i % len(switch_seq)]
        phase = ideal_phase(ant) + cfo_slope_rad_per_us * t
        samples.append(amplitude * cmath.exp(1j * phase))
        t += tspace_us

    return b"".join(
        struct.pack("<hh", int(round(s.real)), int(round(s.imag))) for s in samples
    )


def test_angle_recovery():
    ants_sequence = [1, 1] + list(range(1, 33))
    tspace_us = 2.0
    freq_mhz = 2480

    cases = [
        (0.0, 0.0),
        (20.0, -10.0),
        (-35.0, 15.0),
        (10.0, 40.0),
        (-45.0, -20.0),
    ]
    for az_true, el_true in cases:
        iq_raw = generate_synthetic_iq(az_true, el_true, freq_mhz, ants_sequence, tspace_us)
        result = estimate_aoa(iq_raw, freq_mhz, ants_sequence, tspace_us)
        az, el = result["azimuth_deg"], result["elevation_deg"]
        check(f"angle recovery az={az_true} el={el_true}",
              az is not None and el is not None
              and abs(az - az_true) < 0.5 and abs(el - el_true) < 0.5,
              f"got az={az}, el={el}")


def test_cfo_drift_rejection():
    ants_sequence = [1, 1] + list(range(1, 33))
    tspace_us = 2.0
    freq_mhz = 2480
    az_true, el_true = 20.0, -10.0

    for slope in (0.0, 0.02, -0.05, 0.1):
        iq_raw = generate_synthetic_iq(az_true, el_true, freq_mhz, ants_sequence,
                                        tspace_us, cfo_slope_rad_per_us=slope)
        result = estimate_aoa(iq_raw, freq_mhz, ants_sequence, tspace_us)
        az, el = result["azimuth_deg"], result["elevation_deg"]
        check(f"CFO drift rejection (slope={slope} rad/us)",
              az is not None and el is not None
              and abs(az - az_true) < 0.5 and abs(el - el_true) < 0.5,
              f"got az={az}, el={el}")


def test_project_to_floor():
    x, y, r = project_to_floor(0.0, 0.0, 2.0)
    check("project_to_floor: straight down", abs(x) < 1e-9 and abs(y) < 1e-9 and abs(r - 2.0) < 1e-9,
          f"got x={x}, y={y}, r={r}")

    x, y, r = project_to_floor(45.0, 0.0, 2.0)
    # u = sin(45) = v2/2, w = cos(45) = v2/2 -> r = 2 / (v2/2) = 2*v2 ~= 2.828, x = r*u = 2.0
    check("project_to_floor: 45deg azimuth", abs(x - 2.0) < 1e-6 and abs(y) < 1e-9,
          f"got x={x}, y={y}, r={r}")

    try:
        project_to_floor(0.0, 0.0, -1.0)
        check("project_to_floor: rejects non-positive drop", False)
    except ValueError:
        check("project_to_floor: rejects non-positive drop", True)


def test_multi_station_room_transform():
    import tag_tracker

    station = {"position_m": [5.0, 5.0], "azimuth_offset_deg": 90.0}
    rx, ry = tag_tracker._room_position(station, 1.0, 0.0)
    # rotating local (1, 0) by +90deg -> (0, 1), then translate by (5, 5) -> (5, 6)
    check("room transform: 90deg rotation + translation",
          abs(rx - 5.0) < 1e-9 and abs(ry - 6.0) < 1e-9,
          f"got rx={rx}, ry={ry}")

    station0 = {"position_m": [0.0, 0.0], "azimuth_offset_deg": 0.0}
    rx0, ry0 = tag_tracker._room_position(station0, 3.5, -2.0)
    check("room transform: identity (single station at origin)",
          abs(rx0 - 3.5) < 1e-9 and abs(ry0 - (-2.0)) < 1e-9,
          f"got rx={rx0}, ry={ry0}")


if __name__ == "__main__":
    test_angle_recovery()
    test_cfo_drift_rejection()
    test_project_to_floor()
    test_multi_station_room_transform()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        raise SystemExit(1)
    print("All checks passed.")
