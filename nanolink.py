"""
Minimal NanoLink (Minew AoA MG10-B) protocol helper.

NanoLink frames are CBOR maps of the form:
    {"hdr": [version, option_code, seq, group_id, cmd_id], "pld": {...}}

- version is always 4
- option_code: 0=read req, 1=read resp, 2=write req, 3=write resp, 8=notification
- seq: wraps 0..255, must match between request/response
- group_id / cmd_id: see the MG10-B manual, section 4.3

Commands are sent over UDP to the base station's command port (1337).
The base station pushes unsolicited "Notification" frames (option_code=8)
containing scanned tag data to whatever destination IP/port you configure
with the "Destination IP" command (group 4106, cmd 1).
"""

import socket
import cbor2

CMD_PORT = 1337  # base station command reception port (default; overridable per station in config.json)

GROUP_OS = 0
GROUP_DEVICE_INFO = 257
GROUP_APPLICATION = 265
GROUP_UI = 267
GROUP_AOA = 4106  # LE Direction Finding Station

# AoA (group 4106) command IDs
CMD_LOCAL_IP = 0
CMD_DEST_IP = 1
CMD_SCAN = 2
CMD_SENSOR_NOTIFY = 3
CMD_AOA_MODE = 4
CMD_INSTALL_ANGLE = 5

OPT_READ_REQ = 0
OPT_READ_RESP = 1
OPT_WRITE_REQ = 2
OPT_WRITE_RESP = 3
OPT_NOTIFICATION = 8


class NanoLinkClient:
    def __init__(self, station_ip: str, cmd_port: int = CMD_PORT, timeout: float = 2.0):
        self.station_ip = station_ip
        self.cmd_port = cmd_port
        self.timeout = timeout
        self._seq = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq = (self._seq + 1) % 256
        return seq

    def _send(self, option_code: int, group: int, cmd: int, payload: dict) -> dict:
        seq = self._next_seq()
        frame = {"hdr": [4, option_code, seq, group, cmd], "pld": payload}
        raw = cbor2.dumps(frame)
        self.sock.sendto(raw, (self.station_ip, self.cmd_port))

        data, _addr = self.sock.recvfrom(4096)
        resp = cbor2.loads(data)
        hdr = resp["hdr"]
        if hdr[2] != seq:
            raise RuntimeError(f"sequence mismatch: sent {seq}, got {hdr[2]}")
        if "err" in resp["pld"]:
            raise RuntimeError(f"device error: {resp['pld']['err']}")
        return resp

    def read(self, group: int, cmd: int) -> dict:
        return self._send(OPT_READ_REQ, group, cmd, {})

    def write(self, group: int, cmd: int, payload: dict) -> dict:
        return self._send(OPT_WRITE_REQ, group, cmd, payload)

    # ---- convenience wrappers for the AoA group -------------------------

    def set_destination(self, ip: str, port: int):
        """Point the base station's data reporting at (ip, port)."""
        octets = bytes(int(x) for x in ip.split("."))
        payload = {"ipv4": cbor2.CBORTag(52, octets), "port": port}
        return self.write(GROUP_AOA, CMD_DEST_IP, payload)

    def get_destination(self):
        return self.read(GROUP_AOA, CMD_DEST_IP)

    def set_aoa_mode(self, mode: int):
        """0 = MW_CTE_5.1 (BLE5.1), 1 = MW_CTE_4.2 (BLE4.2, default), 2 = private."""
        return self.write(GROUP_AOA, CMD_AOA_MODE, {"mode": mode})

    def get_aoa_mode(self):
        return self.read(GROUP_AOA, CMD_AOA_MODE)

    def set_installation_angle_detection(self, enable: bool):
        """The station boots into this leveling-check routine and IGNORES
        scan commands until it's disabled (see manual section 2 / 4.3.4.1)."""
        return self.write(GROUP_AOA, CMD_INSTALL_ANGLE, {"en": enable})

    def get_installation_angle_detection(self):
        return self.read(GROUP_AOA, CMD_INSTALL_ANGLE)

    def set_scan(self, enable: bool, channels=None, filter_rssi=None, filter_mac_regex=None):
        payload = {"en": enable}
        if channels is not None:
            payload["param"] = {"ch": channels}
        filters = []
        if filter_rssi is not None:
            filters.append({"rssi": filter_rssi})
        if filter_mac_regex is not None:
            filters.append({"re_mac": filter_mac_regex})
        if filters:
            payload["filter"] = filters
        return self.write(GROUP_AOA, CMD_SCAN, payload)

    def get_scan(self):
        return self.read(GROUP_AOA, CMD_SCAN)

    def factory_reset(self):
        return self.write(GROUP_DEVICE_INFO, 2, {})

    def system_reset(self):
        return self.write(GROUP_OS, 5, {"force": True})

    def get_firmware_info(self):
        return self.read(GROUP_DEVICE_INFO, 0)

    def close(self):
        self.sock.close()


def local_ip_towards(host: str) -> str:
    """Best-effort: figure out which local NIC IP the OS would use to reach `host`."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 1))
        return s.getsockname()[0]
    finally:
        s.close()
