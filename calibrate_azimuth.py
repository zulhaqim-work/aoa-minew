"""
One-time azimuth calibration helper.

The station reports azimuth relative to its own antenna array's x-axis --
we don't know, and can't know from the datasheet, which real-world compass
direction that x-axis actually points. This script fixes that: place a
beacon at a spot whose TRUE bearing from the station you know (see below),
tell this script the beacon's MAC and that true bearing, and it computes
the azimuth_offset_deg correction and (optionally) writes it into
config.json for you.

Two ways to pick a "known true bearing" spot -- either works:
  1. Directly under the station -> true bearing is undefined/arbitrary
     (pick this only to sanity-check that elevation is also ~0 there, not
     for the rotation offset itself).
  2. Somewhere off to one side, along a direction you can measure or that
     matches a room reference (e.g. "directly toward the door" = 0 degrees,
     or "along the long wall to the right" = 90 degrees). This is the one
     to use for the actual offset calibration.

Usage:
    python calibrate_azimuth.py <beacon_mac> <true_azimuth_deg> [--apply] [--seconds N]

Example:
    Beacon physically placed 2m to the right of the point directly under
    the station, along the wall you're calling "90 degrees":

    python calibrate_azimuth.py C2:03:03:00:4F:1A 90 --apply
"""

import sys
import time
import argparse
import statistics

import tag_tracker as tt
import config as cfgmod


def normalize_deg(angle: float) -> float:
    """Wrap an angle into (-180, 180]."""
    return ((angle + 180) % 360) - 180


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mac", help="MAC of the beacon currently placed at the known bearing (as printed by listen_data.py)")
    parser.add_argument("true_azimuth_deg", type=float, help="The real-world azimuth (degrees) that position should correspond to")
    parser.add_argument("--station", default=None, help="Station id from config.json (default: first station)")
    parser.add_argument("--seconds", type=float, default=6.0, help="How long to listen and average readings (default 6s)")
    parser.add_argument("--apply", action="store_true", help="Write the computed offset into config.json")
    args = parser.parse_args()

    target_mac = args.mac.upper()
    stations = tt.CFG["stations"]
    station = next((s for s in stations if s.get("id") == args.station), stations[0]) if args.station else stations[0]

    print(f"Calibrating against station '{station.get('id', station['ip'])}' ({station['ip']})")
    print(f"Listening for beacon {target_mac} for {args.seconds:.0f}s -- keep it still at the known position ...")

    tt.load_all_scan_configs()
    sock = tt.open_socket(timeout=0.3)

    raw_samples = []  # (az, el) BEFORE this station's azimuth_offset_deg is applied
    saved_offset = station.get("azimuth_offset_deg", 0.0)
    station["azimuth_offset_deg"] = 0.0  # measure the raw, uncorrected angle

    deadline = time.time() + args.seconds
    while time.time() < deadline:
        tt.receive_once(sock)
        snap = tt.get_snapshot()
        if target_mac in snap:
            raw_samples.append((snap[target_mac]["az"], snap[target_mac]["el"]))

    station["azimuth_offset_deg"] = saved_offset  # restore

    if not raw_samples:
        print(f"\nNever saw beacon {target_mac}. Check the MAC (see listen_data.py's TAG MAC column) "
              "and that it's powered on near the station.")
        sys.exit(1)

    raw_az = statistics.median(a for a, _ in raw_samples)
    raw_el = statistics.median(e for _, e in raw_samples)
    offset = normalize_deg(args.true_azimuth_deg - raw_az)

    print(f"\n{len(raw_samples)} samples collected.")
    print(f"  Raw (uncorrected) azimuth: {raw_az:+.1f} deg")
    print(f"  Raw elevation:             {raw_el:+.1f} deg  (sanity-check this looks reasonable for the beacon's height)")
    print(f"  You said true azimuth should be: {args.true_azimuth_deg:+.1f} deg")
    print(f"  => azimuth_offset_deg to use: {offset:+.1f}")

    if args.apply:
        cfg_path = cfgmod._CONFIG_PATH
        raw_cfg = cfgmod.load(cfg_path)
        for s in raw_cfg["stations"]:
            if s["ip"] == station["ip"]:
                s["azimuth_offset_deg"] = round(offset, 1)
        import json
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(raw_cfg, f, indent=2)
            f.write("\n")
        print(f"\nWrote azimuth_offset_deg={offset:+.1f} to config.json for station '{station['ip']}'.")
    else:
        print("\nRe-run with --apply to write this into config.json, "
              "or edit stations[].azimuth_offset_deg by hand.")


if __name__ == "__main__":
    main()
