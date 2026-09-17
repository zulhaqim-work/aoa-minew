"""
Single entry point: configures the MG10-B base station (destination IP,
AoA mode, disables angle-detection lock, enables scan), then immediately
starts the live listener printing tag angle-of-arrival estimates.

Usage:
    python run_aoa.py

Stop with Ctrl+C.
"""

import configure_station
import listen_data


def main():
    configure_station.main()
    print()
    try:
        listen_data.main()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
