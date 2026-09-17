"""
Physical geometry of the MG10-B's 4x4 dual-polarized antenna array
(manual section 3, "Antenna information").

CAVEAT: the manual's antenna-numbering diagram (page 9) is a scanned image;
the exact left/right and pairing of ant IDs to grid cells below was
reconstructed from that image and cross-checked against the visible pair
groupings ("7/8", "6/5", "3/4", "1/2" per row, etc.) and the antenna
control-value table (ANT1..ANT32). The 50mm pitch and 4x4 shape are stated
directly in the manual; the *orientation* (which physical compass direction
is +x/+y) is NOT stated anywhere, since the manual gives no reference
marking for "front" of the array. That means:

  - Relative azimuth/elevation math below is correct.
  - The absolute compass bearing your +x axis corresponds to is unknown
    until you calibrate: point a tag at a known bearing (e.g. straight
    "front" of the housing) and check the sign/magnitude of the computed
    angle, then flip signs / swap axes here as needed.

Antenna numbering pattern derived from the manual's figure: rows 1-4 (top to
bottom) hold antennas 1-8, 9-16, 17-24, 25-32 respectively; within each row,
antenna numbers increase from right to left, with each physical element
carrying a pair of orthogonally-polarized antennas.
"""

PITCH_M = 0.050  # 50mm element spacing, both axes


def _ant_pair(row: int, col: int) -> tuple[int, int]:
    """row, col are 0-indexed, row 0 = top, col 0 = left.
    Returns (low_id, high_id) antenna numbers for that grid cell."""
    ant_low = 8 * row + 2 * (3 - col) + 1
    return ant_low, ant_low + 1


# ant_id -> (x_m, y_m), array centered at (0, 0)
ANTENNA_POSITIONS: dict[int, tuple[float, float]] = {}
for _row in range(4):
    for _col in range(4):
        _lo, _hi = _ant_pair(_row, _col)
        _x = (_col - 1.5) * PITCH_M
        _y = (1.5 - _row) * PITCH_M
        ANTENNA_POSITIONS[_lo] = (_x, _y)
        ANTENNA_POSITIONS[_hi] = (_x, _y)  # same element, other polarization


def positions_for_polarization(which: str = "odd") -> dict[int, tuple[float, float]]:
    """which: 'odd' (ant 1,3,5,...), 'even' (ant 2,4,6,...), or 'both'."""
    if which == "both":
        return dict(ANTENNA_POSITIONS)
    parity = 1 if which == "odd" else 0
    return {a: p for a, p in ANTENNA_POSITIONS.items() if a % 2 == parity}
