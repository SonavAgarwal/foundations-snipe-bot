from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from foundations_bot.store import FamilyGraphSeries


def render_two_week_graph(
    graph_series: list[FamilyGraphSeries], start_date: date, end_date: date
) -> BytesIO:
    dates = []
    cursor = start_date
    while cursor <= end_date:
        dates.append(cursor)
        cursor += timedelta(days=1)

    figure, axis = plt.subplots(figsize=(11, 6))

    for series in graph_series:
        running_total = 0
        y_values = []
        for current_date in dates:
            running_total += series.daily_points.get(current_date, 0)
            y_values.append(running_total)
        axis.plot(dates, y_values, marker="o", linewidth=2, label=series.family_name)

    axis.set_title("Family Points Over The Last Two Weeks")
    axis.set_xlabel("Date")
    axis.set_ylabel("Cumulative Points")
    axis.grid(alpha=0.3)
    axis.legend(loc="upper left", frameon=False)
    figure.autofmt_xdate(rotation=30)
    figure.tight_layout()

    buffer = BytesIO()
    figure.savefig(buffer, format="png", dpi=180, bbox_inches="tight")
    buffer.seek(0)
    plt.close(figure)
    return buffer
