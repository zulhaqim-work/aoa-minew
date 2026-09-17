"""
Shared beacon-tracking state, used by both listen_data.py (text table) and
visualize.py (floor-plan plot) so the receive/estimate/project logic isn't
duplicated between them.

Design for multiple base stations (only one is configured today, in
config.json, but this is what makes adding more just a config edit):

  - All stations are told (by configure_station.py) to push their
    Notification data to the SAME local UDP port on this machine.
  - Packets from different stations are told apart by their UDP *source IP*
    (not by anything inside the payload), matched against config.json.
  - Each station has its own position/height/azimuth_offset in config.json,
    used to project that station's raw azimuth/elevation into shared ROOM
    coordinates (meters). With one station at position [0, 0] and offset 0,
    room coordinates are identical to that station's own local coordinates.
  - Right now each beacon's position comes from whichever single station
    last heard it -- there's no cross-station triangulation/averaging yet.
    That would be the natural next step once a second station is added.
"""

from __future__ import annotations

import time
import math
import socket
import threading
import statistics
from collections import deque
import cbor2

import config as cfgmod
from nanolink import NanoLinkClient
from aoa_estimate import estimate_aoa, project_to_floor

CFG = cfgmod.load()
STATIONS_BY_IP = cfgmod.stations_by_ip(CFG)
LOCAL_PORT = CFG["local"]["data_port"]
SMOOTHING_WINDOW = CFG["smoothing"]["window"]
TAG_TIMEOUT_S = CFG["smoothing"]["tag_timeout_s"]

# ants/tspace per station IP, filled in by load_all_scan_configs()
_station_scan_config: dict[str, dict] = {}

_lock = threading.Lock()
_history: dict[str, deque] = {}  # tag_mac -> deque[(az, el, rssi)]
_tags: dict[str, dict] = {}      # tag_mac -> latest computed state


def unwrap(value):
    """Unwrap a CBOR semantic tag to its raw value (mac/ipv4 tags aren't
    auto-decoded by cbor2, only well-known ones like timestamps are)."""
    if isinstance(value, cbor2.CBORTag):
        return value.value
    return value


def mac_str(raw) -> str:
    b = unwrap(raw)
    return ":".join(f"{x:02X}" for x in b)


def load_all_scan_configs():
    """Query every configured station for its antenna switch pattern/sample
    spacing so estimate_aoa() knows how to interpret its IQ data."""
    for station in CFG["stations"]:
        ip = station["ip"]
        label = station.get("id", ip)
        try:
            client = NanoLinkClient(ip, station.get("cmd_port", 1337))
            scan = client.get_scan()["pld"]
            client.close()
            dfe = scan.get("dfe", {})
            if dfe.get("ants") and dfe.get("tspace"):
                _station_scan_config[ip] = {
                    "ants": list(dfe["ants"]),
                    "tspace": float(dfe["tspace"]),
                }
                print(f"[{label}] scan config loaded (tspace={dfe['tspace']}us)")
            else:
                print(f"[!] [{label}] scan config missing dfe/ants -- angle estimates disabled for this station")
        except Exception as exc:
            print(f"[!] [{label}] could not read scan config ({exc}) -- angle estimates disabled for this station")


def _room_position(station: dict, x_local: float, y_local: float):
    """Rotate+translate a station-local (x, y) floor offset into room
    coordinates using that station's position_m / azimuth_offset_deg."""
    offset = math.radians(station.get("azimuth_offset_deg", 0.0))
    cos_o, sin_o = math.cos(offset), math.sin(offset)
    rx = x_local * cos_o - y_local * sin_o
    ry = x_local * sin_o + y_local * cos_o
    sx, sy = station.get("position_m", [0.0, 0.0])
    return sx + rx, sy + ry


def handle_notification(source_ip: str, frame: dict):
    hdr = frame["hdr"]
    pld = frame["pld"]
    if hdr[1] != 8:  # only care about notifications here
        return

    station = STATIONS_BY_IP.get(source_ip)
    if station is None:
        # Traffic from a station not in config.json -- still track it,
        # anchored at the room origin, rather than silently dropping it.
        station = {"position_m": [0.0, 0.0], "height_m": 3.0, "azimuth_offset_deg": 0.0}

    scan_cfg = _station_scan_config.get(source_ip)
    if scan_cfg is None:
        return  # never learned this station's antenna pattern -- can't estimate an angle

    data = pld.get("data", {})
    now = time.time()

    for scan_instance in data.get("scan", []):
        for rec in scan_instance.get("records", []):
            iq_raw = rec.get("iq")
            if not iq_raw:
                continue
            try:
                aoa = estimate_aoa(iq_raw, rec.get("f"), scan_cfg["ants"], scan_cfg["tspace"])
                az, el = aoa["azimuth_deg"], aoa["elevation_deg"]
            except Exception:
                continue
            if az is None or el is None:
                continue

            tag_mac = mac_str(rec["a"])
            rssi = rec.get("s")
            freq = rec.get("f")

            with _lock:
                hist = _history.setdefault(tag_mac, deque(maxlen=SMOOTHING_WINDOW))
                hist.append((az, el, rssi))
                n = len(hist)
                s_az = statistics.median(a for a, _, _ in hist)
                s_el = statistics.median(e for _, e, _ in hist)
                rssi_vals = [r for _, _, r in hist if r is not None]
                s_rssi = statistics.median(rssi_vals) if rssi_vals else None

                beacon_h = cfgmod.beacon_height_m(CFG, tag_mac)
                vertical_drop = station.get("height_m", 3.0) - beacon_h
                try:
                    x_local, y_local, dist = project_to_floor(s_az, s_el, vertical_drop)
                    x_room, y_room = _room_position(station, x_local, y_local)
                except ValueError:
                    x_room = y_room = dist = None

                _tags[tag_mac] = {
                    "az": s_az, "el": s_el, "rssi": s_rssi, "freq": freq, "n": n,
                    "x": x_room, "y": y_room, "dist": dist,
                    "station_ip": source_ip, "last_seen": now,
                }


def get_snapshot() -> dict:
    """Thread-safe copy of current tag states, with stale tags dropped."""
    now = time.time()
    with _lock:
        for mac in list(_tags):
            if now - _tags[mac]["last_seen"] > TAG_TIMEOUT_S:
                del _tags[mac]
                _history.pop(mac, None)
        return {mac: dict(info) for mac, info in _tags.items()}


def receive_once(sock: socket.socket):
    """Try to receive+process one UDP packet; no-op on timeout/bad frame."""
    try:
        data, addr = sock.recvfrom(65535)
        frame = cbor2.loads(data)
        handle_notification(addr[0], frame)
    except socket.timeout:
        pass
    except Exception:
        pass


def open_socket(timeout: float = 0.3) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", LOCAL_PORT))
    sock.settimeout(timeout)
    return sock


def start_receiver_thread(timeout: float = 0.3) -> socket.socket:
    """Runs the receive loop in a daemon thread -- for callers (like
    visualize.py) that need their main thread free for a GUI event loop."""
    sock = open_socket(timeout)

    def _loop():
        while True:
            receive_once(sock)

    threading.Thread(target=_loop, daemon=True).start()
    return sock
