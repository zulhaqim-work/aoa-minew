"""
Live-refreshing RAW (unsmoothed) table: same clear-and-redraw style as
listen_data.py, but each row is the MOST RECENT single reading for that
beacon -- no median filtering, no history -- so you can watch it jump
around exactly as the hardware reports it, update to update.

Usage:
    python listen_raw.py                  # all beacons
    python listen_raw.py <mac> [<mac>...] # only these beacons
"""

import os
import sys
import time
import socket
import cbor2

import tag_tracker as tt
from aoa_estimate import estimate_aoa, project_to_floor

REFRESH_INTERVAL_S = 0.3
STALE_AFTER_S = 5.0

_last_raw: dict[str, dict] = {}  # tag_mac -> latest single reading, no smoothing


def handle_packet(data: bytes, source_ip: str, filter_macs):
    try:
        frame = cbor2.loads(data)
    except Exception:
        return
    if frame["hdr"][1] != 8:
        return

    scan_cfg = tt._station_scan_config.get(source_ip)
    if scan_cfg is None:
        return
    station = tt.STATIONS_BY_IP.get(source_ip, {"height_m": 3.0})

    for scan_instance in frame["pld"].get("data", {}).get("scan", []):
        for rec in scan_instance.get("records", []):
            iq_raw = rec.get("iq")
            if not iq_raw:
                continue
            tag_mac = tt.mac_str(rec["a"])
            if filter_macs and tag_mac not in filter_macs:
                continue

            try:
                aoa = estimate_aoa(iq_raw, rec.get("f"), scan_cfg["ants"], scan_cfg["tspace"])
                az, el = aoa["azimuth_deg"], aoa["elevation_deg"]
            except Exception:
                continue
            if az is None or el is None:
                continue

            beacon_h = tt.cfgmod.beacon_height_m(tt.CFG, tag_mac)
            vertical_drop = station.get("height_m", 3.0) - beacon_h
            try:
                _x, _y, dist = project_to_floor(az, el, vertical_drop)
            except ValueError:
                dist = None

            _last_raw[tag_mac] = {
                "az": az, "el": el, "dist": dist, "rssi": rec.get("s"),
                "freq": rec.get("f"), "station_ip": source_ip, "last_seen": time.time(),
            }


def render_table():
    now = time.time()
    for mac in list(_last_raw):
        if now - _last_raw[mac]["last_seen"] > STALE_AFTER_S:
            del _last_raw[mac]

    os.system("")  # enable ANSI escape processing on legacy Windows consoles
    print("\033[H\033[J", end="")
    print("AoA RAW live view (unsmoothed) - Ctrl+C to stop")
    print(f"{'TAG MAC':<20} {'AZ(deg)':>9} {'EL(deg)':>9} {'DIST(m)':>8} {'RSSI':>6} {'CH(MHz)':>8} {'STATION':<15}")
    print("-" * 82)
    if not _last_raw:
        print("(no beacons seen yet)")
        return
    for mac, info in sorted(_last_raw.items()):
        rssi = f"{info['rssi']:.0f}" if info["rssi"] is not None else "?"
        dist = f"{info['dist']:.2f}" if info["dist"] is not None else "?"
        print(f"{mac:<20} {info['az']:>+9.1f} {info['el']:>+9.1f} {dist:>8} "
              f"{rssi:>6} {info['freq']:>8} {info['station_ip']:<15}")


def main():
    filter_macs = {m.upper() for m in sys.argv[1:]} or None

    tt.load_all_scan_configs()
    sock = tt.open_socket(timeout=REFRESH_INTERVAL_S)

    while True:
        try:
            data, addr = sock.recvfrom(65535)
            handle_packet(data, addr[0], filter_macs)
        except socket.timeout:
            pass
        except Exception:
            pass
        render_table()


if __name__ == "__main__":
    main()
