"""
Live-refreshing text table of every tracked beacon: angle, floor distance,
RSSI, and which station last heard it. All station/beacon geometry comes
from config.json (via tag_tracker.py) -- nothing here is hardcoded.

The base station(s) must already be configured to send data here -- run
configure_station.py first, or just run run_aoa.py which does both.
"""

import os

import tag_tracker as tt

REFRESH_INTERVAL_S = 0.3


def render_table(tags: dict):
    os.system("")  # enable ANSI escape processing on legacy Windows consoles
    print("\033[H\033[J", end="")  # move cursor home + clear screen
    print("AoA live view - Ctrl+C to stop")
    print(f"{'TAG MAC':<20} {'AZ(deg)':>9} {'EL(deg)':>9} {'DIST(m)':>8} "
          f"{'RSSI':>6} {'CH(MHz)':>8} {'N':>3} {'STATION':<15}")
    print("-" * 90)
    if not tags:
        print("(no beacons seen yet)")
        return
    for mac, info in sorted(tags.items()):
        rssi = f"{info['rssi']:.0f}" if info["rssi"] is not None else "?"
        dist = f"{info['dist']:.2f}" if info["dist"] is not None else "?"
        print(f"{mac:<20} {info['az']:>+9.1f} {info['el']:>+9.1f} {dist:>8} "
              f"{rssi:>6} {info['freq']:>8} {info['n']:>3} {info['station_ip']:<15}")


def main():
    tt.load_all_scan_configs()
    sock = tt.open_socket(REFRESH_INTERVAL_S)
    print(f"Listening for base station notifications on UDP 0.0.0.0:{tt.LOCAL_PORT} ...")

    while True:
        tt.receive_once(sock)
        render_table(tt.get_snapshot())


if __name__ == "__main__":
    main()
