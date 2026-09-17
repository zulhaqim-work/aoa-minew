"""
Loads config.json: every physical/network detail about the AoA deployment
lives there, not in code, so adding a second (or third...) base station is
just adding an entry to the "stations" list.

config.json fields:

  local.data_port        UDP port on THIS machine that all stations are told
                          to push their notification data to (shared by all
                          stations -- packets are told apart by source IP).

  stations[].id           Friendly name, used only for display.
  stations[].ip           The station's IP (used to send it commands).
  stations[].cmd_port     NanoLink command port on the station (1337 default).
  stations[].position_m   [x, y] position of this station in your chosen
                           room coordinate system, in meters. Station 1 is
                           conventionally placed at [0, 0]; every other
                           station's position must be measured/estimated
                           relative to that same origin for multi-station
                           triangulation to make sense later.
  stations[].height_m     How high this station is mounted above the floor,
                           in meters (used for the floor-projection math).
  stations[].azimuth_offset_deg
                           Calibration offset: how many degrees to rotate
                           this station's raw azimuth so that "0 degrees"
                           lines up with your room's chosen reference
                           direction (e.g. one of the walls). Leave at 0
                           until you've done the calibration walk described
                           in the project notes -- uncalibrated, angles are
                           internally consistent but not tied to a known
                           compass direction.
  stations[].aoa_mode     0 = MW_CTE_5.1 tags, 1 = MW_CTE_4.2 tags (default).

  beacons.default_height_m
                          Assumed height (meters) of a beacon above the
                          floor, used for the floor-projection math when no
                          per-beacon override is given. Only meaningful if
                          your beacons stay at roughly this height.
  beacons.height_overrides_m
                          {"AA:BB:CC:DD:EE:FF": 1.4, ...} per-beacon height
                          overrides, keyed by the beacon's MAC address (as
                          printed by listen_data.py/visualize.py).

  smoothing.window        How many recent angle readings to median-filter
                           per beacon before displaying/plotting it.
  smoothing.tag_timeout_s How many seconds of silence before a beacon is
                           dropped from the table/plot.
"""

import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load(path: str = _CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    cfg.setdefault("local", {}).setdefault("data_port", 8833)

    for station in cfg.get("stations", []):
        station.setdefault("cmd_port", 1337)
        station.setdefault("position_m", [0.0, 0.0])
        station.setdefault("height_m", 3.0)
        station.setdefault("azimuth_offset_deg", 0.0)
        station.setdefault("aoa_mode", 1)

    beacons = cfg.setdefault("beacons", {})
    beacons.setdefault("default_height_m", 1.0)
    beacons.setdefault("height_overrides_m", {})

    smoothing = cfg.setdefault("smoothing", {})
    smoothing.setdefault("window", 7)
    smoothing.setdefault("tag_timeout_s", 5.0)

    return cfg


def stations_by_ip(cfg: dict) -> dict:
    return {s["ip"]: s for s in cfg["stations"]}


def beacon_height_m(cfg: dict, tag_mac: str) -> float:
    overrides = cfg["beacons"]["height_overrides_m"]
    return overrides.get(tag_mac, cfg["beacons"]["default_height_m"])
