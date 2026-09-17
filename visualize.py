"""
Live floor-plan view: shows every configured AoA base station (fixed
triangle markers, positioned from config.json) and every currently-tracked
beacon (moving dots, in room-coordinate meters). Run configure_station.py
first (or use run_visualize.py, which does both).
"""

import matplotlib.pyplot as plt
import matplotlib.animation as animation

import tag_tracker as tt


def main():
    tt.load_all_scan_configs()
    tt.start_receiver_thread()

    stations = tt.CFG["stations"]
    station_xs = [s.get("position_m", [0.0, 0.0])[0] for s in stations]
    station_ys = [s.get("position_m", [0.0, 0.0])[1] for s in stations]
    max_h = max((s.get("height_m", 3.0) for s in stations), default=3.0)
    span = max(1.5 * max_h, 5.0)  # doc's recommended coverage radius = 1.5x height

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.set_aspect("equal")
    ax.set_xlabel("meters (x)")
    ax.set_ylabel("meters (y)")
    ax.set_title("AoA live floor plan")
    ax.grid(True, linestyle="--", alpha=0.4)

    ax.scatter(station_xs, station_ys, marker="^", s=250, c="black", zorder=3, label="AoA station")
    for s in stations:
        sx, sy = s.get("position_m", [0.0, 0.0])
        ax.annotate(s.get("id", s["ip"]), (sx, sy), textcoords="offset points",
                    xytext=(8, 8), fontsize=9, fontweight="bold")

    tag_scatter = ax.scatter([], [], marker="o", s=120, c="tab:red", zorder=4, label="beacon")
    ax.legend(loc="upper right")

    ax.set_xlim(min(station_xs, default=0) - span, max(station_xs, default=0) + span)
    ax.set_ylim(min(station_ys, default=0) - span, max(station_ys, default=0) + span)

    _labels: list = []

    def update(_frame):
        for lbl in _labels:
            lbl.remove()
        _labels.clear()

        tags = tt.get_snapshot()
        points = [(info["x"], info["y"]) for info in tags.values() if info["x"] is not None]
        tag_scatter.set_offsets(points if points else [[float("nan"), float("nan")]])

        for mac, info in tags.items():
            if info["x"] is None:
                continue
            lbl = ax.annotate(f"{mac[-5:]}\n{info['dist']:.1f}m",
                               (info["x"], info["y"]), textcoords="offset points",
                               xytext=(6, 6), fontsize=8)
            _labels.append(lbl)

        return (tag_scatter, *_labels)

    _ani = animation.FuncAnimation(fig, update, interval=300, cache_frame_data=False)
    plt.show()


if __name__ == "__main__":
    main()
