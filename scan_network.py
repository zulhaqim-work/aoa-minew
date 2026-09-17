"""
Finds MG10-B (or any NanoLink-speaking) base station(s) on the local subnet
by actually probing the NanoLink command port (1337) with a real protocol
request, rather than just checking for an open port -- a response that
decodes as a valid NanoLink frame confirms it's really a Minew station.

Usage: python scan_network.py [subnet-prefix]
  e.g. python scan_network.py 192.168.0    (scans 192.168.0.1-254)
If no prefix is given, it's guessed from this machine's own IP.
"""

import sys
import socket
import cbor2

from nanolink import CMD_PORT, local_ip_towards


def guess_subnet_prefix() -> str:
    # Ask the OS which local IP it would use to reach the public internet;
    # its /24 is a reasonable guess for "the LAN we're on".
    ip = local_ip_towards("8.8.8.8")
    return ".".join(ip.split(".")[:3])


def build_probe(seq: int = 0) -> bytes:
    # Device Information | Firmware information (group 257, cmd 0), read request.
    frame = {"hdr": [4, 0, seq, 257, 0], "pld": {}}
    return cbor2.dumps(frame)


def scan(prefix: str, port: int = CMD_PORT, wait_s: float = 2.0):
    probe = build_probe()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(wait_s)

    print(f"Probing {prefix}.1-254 on UDP port {port} ...")
    for i in range(1, 255):
        ip = f"{prefix}.{i}"
        try:
            sock.sendto(probe, (ip, port))
        except OSError:
            pass

    found = {}
    while True:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            break
        except OSError:
            # Windows surfaces ICMP "port unreachable" replies (from all the
            # non-listening IPs we just probed) as a ConnectionResetError on
            # this socket -- harmless, just means that IP wasn't a station.
            continue
        try:
            frame = cbor2.loads(data)
            pld = frame["pld"]
            if "main" in pld or "mcuboot" in pld:
                found[addr[0]] = pld
        except Exception:
            continue

    return found


if __name__ == "__main__":
    prefix = sys.argv[1] if len(sys.argv) > 1 else guess_subnet_prefix()
    results = scan(prefix)

    if not results:
        print("No NanoLink devices responded.")
    else:
        print(f"\nFound {len(results)} device(s):")
        for ip, info in results.items():
            print(f"  {ip}: {info}")
