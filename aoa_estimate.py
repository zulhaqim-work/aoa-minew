"""
Angle-of-arrival estimation from MG10-B scan-record IQ data.

Pipeline per Bluetooth 5.1 CTE direction finding, matched to what the MG10-B
manual actually exposes (section 3 "Antenna information" + 4.3.4.5.1 "Scan"):

  1. Decode the raw `iq` bytes into complex samples (I + jQ).
  2. The first samples were captured on a fixed *reference* antenna (the
     station's antenna dwelling there long enough for the CFO-induced phase
     drift to be estimated and removed). The remaining samples cycle through
     the configured antenna switch pattern (`ants`), one antenna per
     `tspace` microseconds.
  3. Correct every remaining sample's phase for the CFO drift measured in
     step 2, then coherently average repeated visits to the same antenna
     (the switch pattern usually loops more than once within the CTE).
  4. Fit the resulting per-antenna phase as a linear function of antenna
     position along each array axis (rows -> azimuth, columns -> elevation)
     and convert the phase gradient to an angle via the standard
     phase-interferometry formula: dphi/dx = (2*pi/lambda) * sin(theta).

CAVEAT — CTE timing assumption: the manual documents `nof8us` (total CTE
length) and `tspace` (sample interval) but does NOT document the exact
guard/reference period length used by this firmware (the Bluetooth 5.1 core
spec nominally uses 4us guard + 8us reference, but vendors sometimes deviate).
REF_GUARD_US below is a reasonable default (12us) inferred from that spec
and cross-checked loosely against the observed ~82-sample records at
tspace=2us over a 160us CTE, but it has NOT been empirically validated
against a physical known-bearing test. If computed angles look biased or
noisy, this is the first constant to recalibrate — see `calibrate.py`.
"""

from __future__ import annotations

import math
import struct
import numpy as np

from antenna_array import positions_for_polarization

SPEED_OF_LIGHT = 299_792_458.0
REF_GUARD_US = 12.0  # see CAVEAT above


def decode_iq(raw: bytes) -> np.ndarray:
    """iq bytes -> complex128 array, one sample per (I, Q) int16 LE pair."""
    n = len(raw) // 4
    pairs = struct.iter_unpack("<hh", raw[: n * 4])
    return np.array([complex(i, q) for i, q in pairs], dtype=np.complex128)


def _cfo_correct_and_group(iq: np.ndarray, ants_sequence: list[int], tspace_us: float,
                            ref_guard_us: float = REF_GUARD_US):
    """Returns (per-antenna averaged complex phasor dict, debug info dict)."""
    ref_count = max(1, round(ref_guard_us / tspace_us))
    ref_count = min(ref_count, max(1, len(iq) - 1))

    ref_samples = iq[:ref_count]
    t_ref = np.arange(ref_count) * tspace_us
    ref_phase = np.unwrap(np.angle(ref_samples))
    if ref_count >= 2:
        slope, _intercept = np.polyfit(t_ref, ref_phase, 1)
    else:
        slope = 0.0

    switch_seq = list(ants_sequence[2:]) or list(ants_sequence)
    remaining = iq[ref_count:]
    t_all = ref_count * tspace_us + np.arange(len(remaining)) * tspace_us
    corrected = remaining * np.exp(-1j * slope * t_all)

    sums: dict[int, complex] = {}
    counts: dict[int, int] = {}
    for i, val in enumerate(corrected):
        ant = switch_seq[i % len(switch_seq)]
        sums[ant] = sums.get(ant, 0j) + val
        counts[ant] = counts.get(ant, 0) + 1
    avg = {ant: sums[ant] / counts[ant] for ant in sums}

    debug = {"ref_count": ref_count, "cfo_slope_rad_per_us": slope,
              "n_switching_samples": len(remaining)}
    return avg, debug


def _axis_angle_deg(phasors: dict[int, complex], positions: dict[int, tuple],
                      wavelength_m: float, axis: str) -> float | None:
    """Group elements sharing the same 'other' coordinate (rows -> vary x for
    azimuth, columns -> vary y for elevation), unwrap phase along that line,
    fit the phase gradient, convert to an angle. Averages over all
    available rows/columns."""
    groups: dict[float, list[tuple[float, complex]]] = {}
    for ant, (x, y) in positions.items():
        if ant not in phasors:
            continue
        coord, other = (x, y) if axis == "x" else (y, x)
        groups.setdefault(round(other, 6), []).append((coord, phasors[ant]))

    angles = []
    for pts in groups.values():
        if len(pts) < 2:
            continue
        pts.sort(key=lambda p: p[0])
        coords = np.array([p[0] for p in pts])
        phases = np.unwrap(np.array([np.angle(p[1]) for p in pts]))
        k, _c = np.polyfit(coords, phases, 1)  # phase ~= k*coord + c
        sin_theta = np.clip(k * wavelength_m / (2 * np.pi), -1.0, 1.0)
        angles.append(np.degrees(np.arcsin(sin_theta)))

    return float(np.mean(angles)) if angles else None


def estimate_aoa(iq_raw: bytes, freq_mhz: float, ants_sequence: list[int],
                  tspace_us: float, polarization: str = "odd",
                  ref_guard_us: float = REF_GUARD_US) -> dict:
    """Full pipeline: raw iq bytes -> {'azimuth_deg', 'elevation_deg', ...}."""
    iq = decode_iq(iq_raw)
    phasors, debug = _cfo_correct_and_group(iq, ants_sequence, tspace_us, ref_guard_us)
    positions = positions_for_polarization(polarization)
    wavelength_m = SPEED_OF_LIGHT / (freq_mhz * 1e6)

    azimuth = _axis_angle_deg(phasors, positions, wavelength_m, axis="x")
    elevation = _axis_angle_deg(phasors, positions, wavelength_m, axis="y")

    return {
        "azimuth_deg": azimuth,
        "elevation_deg": elevation,
        "n_antennas_resolved": len(phasors),
        **debug,
    }


def project_to_floor(azimuth_deg: float, elevation_deg: float, vertical_drop_m: float):
    """Project a station-relative bearing onto the horizontal plane
    `vertical_drop_m` below the station (station_height - beacon_height),
    assuming the array's boresight points straight down (ceiling mount).

    azimuth_deg/elevation_deg here are treated as direction cosines along
    the array's own x/y axes (u = sin(az), v = sin(el)); this matches how
    _axis_angle_deg fits them, independent of whatever real-world compass
    direction the array's x-axis happens to point (see antenna_array.py's
    orientation caveat -- that calibration is applied separately, on top of
    this function's output, via a station's position/azimuth_offset).

    Returns (x_m, y_m, straight_line_distance_m) in the station's own local
    floor-plane axes. Raises ValueError if vertical_drop_m <= 0 (station
    must be above the beacon) or if az/el put the beacon behind the array
    (u^2 + v^2 >= 1, i.e. more than 90 degrees off boresight on some axis).
    """
    if vertical_drop_m <= 0:
        raise ValueError("vertical_drop_m must be positive (station above beacon)")

    u = math.sin(math.radians(azimuth_deg))
    v = math.sin(math.radians(elevation_deg))
    w_sq = 1.0 - u * u - v * v
    if w_sq <= 1e-6:
        raise ValueError("azimuth/elevation too far off boresight to project")
    w = math.sqrt(w_sq)

    r = vertical_drop_m / w  # straight-line station->beacon distance
    return r * u, r * v, r
