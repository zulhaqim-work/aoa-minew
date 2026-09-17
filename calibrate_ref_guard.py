"""
Empirically finds the correct REF_GUARD_US (CTE guard+reference period
length, in aoa_estimate.py) by sweeping candidate values against KNOWN
beacon-to-beacon spacing, instead of trusting the Bluetooth 5.1 spec
default the code currently guesses.

Captures raw IQ for the target beacons directly (bypassing tag_tracker's
fixed pipeline), then for each candidate ref_guard_us re-runs estimate_aoa
and checks which value makes the beacon-to-beacon distances match what you
actually measured with a tape measure.

Usage: same argument format as diagnose_scale.py
    python calibrate_ref_guard.py <mac1>:<known_m> <mac2>:<known_m> ...

Example:
    python calibrate_ref_guard.py C2:03:03:00:4F:19:0 C2:03:03:00:4F:1A:4 C2:03:03:00:4F:1B:8
"""

import sys
import time
import math
from collections import defaultdict

import cbor2

import tag_tracker as tt
from aoa_estimate import estimate_aoa, project_to_floor


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

    targets = {}
    for arg in sys.argv[1:]:
        mac, known = arg.rsplit(":", 1)
        targets[mac.upper()] = float(known)

    capture_s = 20.0
    print(f"Loading scan config and capturing raw IQ for {len(targets)} beacons ({capture_s:.0f}s) ...")
    tt.load_all_scan_configs()
    sock = tt.open_socket(timeout=0.3)

    # raw_by_mac[mac] = list of (iq_raw, freq, station_ip)
    raw_by_mac = defaultdict(list)
    deadline = time.time() + capture_s
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(65535)
        except Exception:
            continue
        try:
            frame = cbor2.loads(data)
        except Exception:
            continue
        if frame["hdr"][1] != 8:
            continue
        for scan_instance in frame["pld"].get("data", {}).get("scan", []):
            for rec in scan_instance.get("records", []):
                mac = tt.mac_str(rec["a"])
                if mac in targets and rec.get("iq"):
                    raw_by_mac[mac].append((rec["iq"], rec.get("f"), addr[0]))

    for mac in targets:
        print(f"  {mac}: {len(raw_by_mac[mac])} raw samples captured")
    usable = {mac: known for mac, known in targets.items() if raw_by_mac[mac]}
    if len(usable) < 2:
        print("Fewer than 2 beacons had any samples -- can't compare pairs, aborting.")
        sys.exit(1)
    if len(usable) < len(targets):
        missing = set(targets) - set(usable)
        print(f"  [!] proceeding without {missing} (never seen in this window)")
    targets = usable

    station = tt.CFG["stations"][0]
    vertical_drop = station.get("height_m", 3.0) - tt.CFG["beacons"]["default_height_m"]
    scan_cfg = tt._station_scan_config[station["ip"]]

    candidates = [i * 0.5 for i in range(0, 161)]  # 0.0 to 80.0us in 0.5us steps
    best = None
    all_scores = []

    for ref_guard_us in candidates:
        positions = {}
        for mac, known in targets.items():
            xs, ys, zs = [], [], []
            for iq_raw, freq, _ip in raw_by_mac[mac]:
                try:
                    aoa = estimate_aoa(iq_raw, freq, scan_cfg["ants"], scan_cfg["tspace"],
                                        ref_guard_us=ref_guard_us)
                    az, el = aoa["azimuth_deg"], aoa["elevation_deg"]
                    if az is None or el is None:
                        continue
                    _x, _y, dist = project_to_floor(az, el, vertical_drop)
                    x, y, z = to_xyz(az, el, dist)
                    xs.append(x); ys.append(y); zs.append(z)
                except ValueError:
                    continue
            if xs:
                positions[mac] = (sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs))

        if len(positions) < len(targets):
            continue

        macs = list(positions.keys())
        errs = []
        for i in range(len(macs)):
            for j in range(i + 1, len(macs)):
                m1, m2 = macs[i], macs[j]
                computed = math.dist(positions[m1], positions[m2])
                known = abs(targets[m2] - targets[m1])
                if known > 0:
                    errs.append(((computed - known) / known) ** 2)
        if not errs:
            continue
        score = sum(errs) / len(errs)
        all_scores.append((ref_guard_us, score))

        if best is None or score < best[1]:
            best = (ref_guard_us, score)

    if all_scores:
        print("\nTop 10 candidates by fit (ref_guard_us, mean relative-error^2):")
        for rg, sc in sorted(all_scores, key=lambda t: t[1])[:10]:
            print(f"  {rg:5.1f}us  ->  {sc:.4f}")

    if best is None:
        print("No candidate produced usable results.")
        sys.exit(1)

    best_guard, best_score = best
    print(f"\nBest-fit REF_GUARD_US: {best_guard:.1f} (mean relative-error^2 = {best_score:.4f})")
    print("Recomputing distances at that value:")

    positions = {}
    for mac, known in targets.items():
        xs, ys, zs, dists = [], [], [], []
        for iq_raw, freq, _ip in raw_by_mac[mac]:
            try:
                aoa = estimate_aoa(iq_raw, freq, scan_cfg["ants"], scan_cfg["tspace"], ref_guard_us=best_guard)
                az, el = aoa["azimuth_deg"], aoa["elevation_deg"]
                if az is None or el is None:
                    continue
                _x, _y, dist = project_to_floor(az, el, vertical_drop)
                x, y, z = to_xyz(az, el, dist)
                xs.append(x); ys.append(y); zs.append(z); dists.append(dist)
            except ValueError:
                continue
        positions[mac] = (sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs))
        print(f"  {mac}: station_dist={sum(dists)/len(dists):.2f}m")

    macs = list(positions.keys())
    for i in range(len(macs)):
        for j in range(i + 1, len(macs)):
            m1, m2 = macs[i], macs[j]
            computed = math.dist(positions[m1], positions[m2])
            known = abs(targets[m2] - targets[m1])
            print(f"  {m1} <-> {m2}: known={known:.2f}m computed={computed:.2f}m")

    print(f"\nIf this looks right, set REF_GUARD_US = {best_guard} in aoa_estimate.py")


if __name__ == "__main__":
    main()
