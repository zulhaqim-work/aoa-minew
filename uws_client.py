"""
Pushes tracked beacon positions to the UWS asset-tracking server
(POST <uws.url>, see the Smartlab Asset Tracking API spec).

Everything is driven by config.json -- no tables:
  uws.*                    url, api_key, interval_s, mode, version, exe
  stations[].uws_id        the gateway's uwsid (a station without one is not sent)
  beacons.registered[]     {"mac", "uwsid", "name"} per beacon; only registered
                           beacons are tracked, so only they are sent (the
                           payload carries the uwsid, not the MAC)

Body, one POST per station per interval:
  {"Head": "<exe>#<mode>#<version>#1#<epoch>#<seq>", "Data0": "",
   "Data1": {"type": 1, "uwsid": ..., "timestamp": <epoch>,
             "beacons": [{"type": 2, "corr_x": m, "corr_y": m, "uwsid": ...}]}}
corr_x / corr_y are room-coordinate meters (floats), as computed by tag_tracker.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

import requests

import tag_tracker as tt

log = logging.getLogger("uws")

TX_CMD = "1"  # Head TxCMD: device command id
TYPE_GATEWAY = 1
TYPE_BEACON = 2
MAX_BEACONS = 50
_SEQ_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uws_seq.json")


def _next_seq() -> int:
    """1..999 wrapping, persisted so it survives restarts."""
    try:
        with open(_SEQ_PATH, "r", encoding="utf-8") as f:
            last = int(json.load(f).get("last_seq", 0))
    except Exception:
        last = 0
    seq = last + 1 if 1 <= last < 999 else 1
    try:
        with open(_SEQ_PATH, "w", encoding="utf-8") as f:
            json.dump({"last_seq": seq}, f)
    except OSError:
        pass
    return seq


def build_payloads(uws_cfg: dict, snapshot: dict, now: float | None = None) -> list[dict]:
    """One payload per station that has a uws_id and at least one sendable beacon."""
    now = time.time() if now is None else now

    per_station: dict[str, list[dict]] = {}
    for mac, info in snapshot.items():
        if info["x"] is None or info["y"] is None:
            continue
        beacon = tt.REGISTERED_BEACONS.get(mac)
        if beacon is None:
            continue
        per_station.setdefault(info["station_ip"], []).append({
            "type": TYPE_BEACON,
            "corr_x": round(info["x"], 2),
            "corr_y": round(info["y"], 2),
            "uwsid": beacon["uwsid"],
        })

    payloads = []
    ts = int(now)
    for ip, beacons in per_station.items():
        station = tt.STATIONS_BY_IP.get(ip)
        if not station or not station.get("uws_id"):
            continue
        head = "#".join([uws_cfg["exe"], str(uws_cfg["mode"]), uws_cfg["version"],
                         TX_CMD, str(ts), str(_next_seq())])
        payloads.append({
            "Head": head,
            "Data0": "",
            "Data1": {"type": TYPE_GATEWAY, "uwsid": station["uws_id"], "timestamp": ts,
                      "beacons": beacons[:MAX_BEACONS]},
        })
    return payloads


def send_once(uws_cfg: dict) -> None:
    headers = {"Content-Type": "application/json"}
    if uws_cfg.get("api_key"):
        headers["X-API-Key"] = uws_cfg["api_key"]

    for payload in build_payloads(uws_cfg, tt.get_snapshot()):
        gw = payload["Data1"]["uwsid"]
        log.info("SEND %s", json.dumps(payload))
        try:
            resp = requests.post(uws_cfg["url"], json=payload, headers=headers,
                                 timeout=uws_cfg["timeout_s"])
            log.info("RECV HTTP %s %s", resp.status_code, resp.text.strip() or "(empty response)")
        except requests.RequestException as exc:
            log.error("send failed for %s, will retry next cycle (%s)", gw, exc)


def start_sender_thread() -> threading.Thread | None:
    """Daemon thread sending every uws.interval_s; no-op if uws.enabled is false."""
    uws_cfg = tt.CFG["uws"]
    if not uws_cfg["enabled"]:
        return None
    if not uws_cfg["url"]:
        log.error("uws.enabled but uws.url is empty -- sender not started")
        return None

    def _loop():
        while True:
            time.sleep(uws_cfg["interval_s"])
            try:
                send_once(uws_cfg)
            except Exception as exc:
                log.exception("unexpected error")

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    log.info("sending to %s every %ss", uws_cfg["url"], uws_cfg["interval_s"])
    return t
