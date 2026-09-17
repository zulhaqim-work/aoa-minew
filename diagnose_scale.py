"""
Scale-check diagnostic: uses KNOWN beacon-to-beacon spacing (which you can
measure with a tape measure, independent of the station) to check whether
our angle/distance math has a systematic scale error -- without needing the
azimuth rotation calibration done first, because relative distances between
points are rotation-invariant.

For each pair of simultaneously-tracked beacons, reconstructs their full 3D
position relative to the station (using az/el/dist) and computes the
straight-line distance between them, then compares it to what you measured.

Usage:
    python diagnose_scale.py <mac1>:<known_m> <mac2>:<known_m> ...
    (known_m entries are CUMULATIVE distance along the line from the first
    beacon listed, matching how you'd naturally measure "A to B, B to C")

Example (this project's actual setup):
    python diagnose_scale.py C2:03:03:00:4F:19:0 C2:03:03:00:4F:1A:4 C2:03:03:00:4F:1B:8
"""

import sys
import time
import math
import statistics
from collections import defaultdict

import tag_tracker as tt


def to_xyz(az_deg, el_deg, dist_m):
    u = math.sin(math.radians(az_deg))
    v = math.sin(math.radians(el_deg))
    w_sq = max(0.0, 1.0 - u * u - v * v)
    w = math.sqrt(w_sq)
    return dist_m * u, dist_m * v, dist_m * w


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    targets = {}  # mac -> known cumulative distance along the line
    for arg in sys.argv[1:]:
        mac, known = arg.rsplit(":", 1)
        # MACs contain colons too, so mac is everything before the LAST colon
        targets[mac.upper()] = float(known)

    print(f"Tracking {len(targets)} beacons for 8s: {list(targets)}")
    tt.load_all_scan_configs()
    sock = tt.open_socket(timeout=0.3)

    samples = defaultdict(list)  # mac -> list of (az, el, dist)
    deadline = time.time() + 8.0
    while time.time() < deadline:
        tt.receive_once(sock)
        snap = tt.get_snapshot()
        for mac in targets:
            if mac in snap and snap[mac]["dist"] is not None:
                samples[mac].append((snap[mac]["az"], snap[mac]["el"], snap[mac]["dist"]))

    positions = {}
    for mac, known in targets.items():
        if not samples[mac]:
            print(f"  [!] never saw {mac} -- skipping")
            continue
        az = statistics.median(s[0] for s in samples[mac])
        el = statistics.median(s[1] for s in samples[mac])
        dist = statistics.median(s[2] for s in samples[mac])
        positions[mac] = (to_xyz(az, el, dist), known, dist, az, el)
        print(f"  {mac}: az={az:+.1f} el={el:+.1f} station_dist={dist:.2f}m "
              f"(known cumulative {known:.2f}m)")

    macs = list(positions.keys())
    print("\nPairwise beacon-to-beacon distances (measured vs computed):")
    ratios = []
    for i in range(len(macs)):
        for j in range(i + 1, len(macs)):
            m1, m2 = macs[i], macs[j]
            (x1, y1, z1), k1, *_ = positions[m1]
            (x2, y2, z2), k2, *_ = positions[m2]
            computed = math.dist((x1, y1, z1), (x2, y2, z2))
            known = abs(k2 - k1)
            ratio = computed / known if known else float("nan")
            ratios.append(ratio)
            print(f"  {m1} <-> {m2}: known={known:.2f}m  computed={computed:.2f}m  "
                  f"ratio={ratio:.2f}x")

    if ratios:
        avg_ratio = statistics.mean(ratios)
        print(f"\nAverage computed/known ratio: {avg_ratio:.2f}x")
        if abs(avg_ratio - 1.0) < 0.1:
            print("-> Close to 1.0: no significant scale error. Discrepancies are likely noise/offset, not a geometry-model bug.")
        else:
            print(f"-> Consistently off by ~{avg_ratio:.2f}x: suggests a systematic scale error "
                  "in the antenna geometry model or CTE timing assumption (aoa_estimate.py).")


if __name__ == "__main__":
    main()
