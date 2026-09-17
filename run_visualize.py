"""
Single entry point: configures every AoA base station in config.json, then
opens the live floor-plan window.

Usage:
    python run_visualize.py

Close the plot window (or Ctrl+C in the terminal) to stop.
"""

import configure_station
import visualize


def main():
    configure_station.main()
    print()
    visualize.main()


if __name__ == "__main__":
    main()
