"""
Headless UWS sender: configures the base station(s), tracks the registered
beacons, and every uws.interval_s pushes their positions to the UWS server,
logging each payload sent and each response received (console + app.log).

Usage:
    python app.py

Stop with Ctrl+C.
"""

import logging
import time

import configure_station
import tag_tracker as tt
import uws_client


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("app.log", encoding="utf-8")],
    )


def main():
    setup_logging()
    log = logging.getLogger("app")

    configure_station.main()
    tt.load_all_scan_configs()
    tt.start_receiver_thread()
    log.info("listening for base station data on UDP %s", tt.LOCAL_PORT)

    if uws_client.start_sender_thread() is None:
        log.error("UWS sending is disabled -- set uws.enabled in config.json")
        return

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
