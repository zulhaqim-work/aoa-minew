"""
One-shot setup for every AoA base station listed in config.json:
  1. Point each at this machine's IP + the shared local data port.
  2. Set its AoA tag protocol mode (per-station, from config.json).
  3. Disable installation-angle-detection lock if it's on (it blocks scan).
  4. Turn scanning on.
  5. Read everything back to confirm.

To add another station later: add an entry to config.json's "stations"
list. Nothing in this file needs to change.
"""

from nanolink import NanoLinkClient, local_ip_towards
import config as cfgmod

CFG = cfgmod.load()


def configure_one(station: dict, local_port: int):
    ip = station["ip"]
    cmd_port = station.get("cmd_port", 1337)
    label = station.get("id", ip)
    my_ip = local_ip_towards(ip)

    print(f"\n=== {label} ({ip}) ===")
    print(f"This machine's IP toward it: {my_ip}")

    client = NanoLinkClient(ip, cmd_port)

    fw = client.get_firmware_info()
    print("Firmware info:", fw["pld"])

    print(f"Setting destination server to {my_ip}:{local_port} ...")
    client.set_destination(my_ip, local_port)
    print("  ->", client.get_destination()["pld"])

    mode = station.get("aoa_mode", 1)
    print(f"Setting AoA positioning mode to {mode} ...")
    client.set_aoa_mode(mode)
    print("  ->", client.get_aoa_mode()["pld"])

    angle_state = client.get_installation_angle_detection()["pld"]
    if angle_state.get("en"):
        print("Installation angle detection is ON (blocks scanning) — disabling ...")
        client.set_installation_angle_detection(False)
        print("  ->", client.get_installation_angle_detection()["pld"])

    print("Enabling scan (default channels/params) ...")
    client.set_scan(enable=True)
    print("  ->", client.get_scan()["pld"])

    client.close()


def main():
    local_port = CFG["local"]["data_port"]
    for station in CFG["stations"]:
        configure_one(station, local_port)
    print(f"\nDone. All configured stations should now stream Notification "
          f"packets to UDP port {local_port} on this machine.")


if __name__ == "__main__":
    main()
