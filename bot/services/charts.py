from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


# ── style ────────────────────────────────────────────────
GREEN = "#00d4aa"
RED = "#ff6b6b"
TEAL = "#4ecdc4"
YELLOW = "#ffd93d"
BG = "#1e1e2e"
TEXT_CLR = "#cdd6f4"


def _apply_style() -> None:
    plt.style.use("dark_background")
    plt.rcParams.update({
        "figure.facecolor": BG,
        "axes.facecolor": BG,
        "text.color": TEXT_CLR,
        "axes.labelcolor": TEXT_CLR,
        "xtick.color": TEXT_CLR,
        "ytick.color": TEXT_CLR,
    })


def _fmt(val: float) -> str:
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val / 1_000:.0f}k"
    return f"{val:.0f}"


# ── week chart ───────────────────────────────────────────

def create_week_chart(records: list[dict], week_number: int, budget: float) -> BytesIO:
    _apply_style()

    daily: dict[str, float] = defaultdict(float)
    for r in records:
        dt = r["created_at"]
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        day_label = dt.strftime("%d.%m")
        daily[day_label] += r["amount"]

    days = list(daily.keys())
    amounts = list(daily.values())
    cumulative = list(np.cumsum(amounts))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), gridspec_kw={"hspace": 0.35})
    total = sum(amounts)

    # ── subplot 1: bar chart by day ──
    colors = [RED if a > (budget / 7 if budget else float("inf")) else GREEN for a in amounts]
    bars = ax1.bar(days, amounts, color=colors, edgecolor="none", width=0.6)
    for bar, val in zip(bars, amounts):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                 _fmt(val), ha="center", va="bottom", fontsize=9, color=TEXT_CLR)
    ax1.set_title(f"Неделя {week_number} — по дням  (итого {_fmt(total)})", fontsize=13, pad=10)
    ax1.set_ylabel("Руб.")
    ax1.tick_params(axis="x", rotation=45)
    ax1.grid(axis="y", alpha=0.15)

    # ── subplot 2: cumulative vs budget ──
    x = list(range(len(days)))
    ax2.plot(x, cumulative, marker="o", color=TEAL, linewidth=2, markersize=5, zorder=3)
    if budget > 0:
        ax2.axhline(budget, color=YELLOW, linestyle="--", linewidth=1.5, label=f"Бюджет {_fmt(budget)}")
        cum_arr = np.array(cumulative)
        bgt_arr = np.full_like(cum_arr, budget)
        ax2.fill_between(x, cum_arr, bgt_arr, where=cum_arr <= bgt_arr,
                         interpolate=True, color=GREEN, alpha=0.18)
        ax2.fill_between(x, cum_arr, bgt_arr, where=cum_arr > bgt_arr,
                         interpolate=True, color=RED, alpha=0.25)
        ax2.legend(loc="upper left", fontsize=9)

    ax2.set_xticks(x)
    ax2.set_xticklabels(days, rotation=45)
    ax2.set_title("Накопительный расход", fontsize=13, pad=10)
    ax2.set_ylabel("Руб.")
    ax2.grid(axis="y", alpha=0.15)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    return buf


# ── year chart ───────────────────────────────────────────

def create_year_chart(records: list[dict], year: int, budget: float) -> BytesIO:
    _apply_style()

    monthly: dict[int, float] = defaultdict(float)
    weekly: dict[int, float] = defaultdict(float)

    for r in records:
        dt = r["created_at"]
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        monthly[dt.month] += r["amount"]
        weekly[r["custom_week"]] += r["amount"]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9), gridspec_kw={"hspace": 0.35})

    # ── subplot 1: monthly bars ──
    month_names = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
                   "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
    m_keys = sorted(monthly.keys())
    m_labels = [month_names[m - 1] for m in m_keys]
    m_vals = [monthly[m] for m in m_keys]

    bars = ax1.bar(m_labels, m_vals, color=TEAL, edgecolor="none", width=0.6)
    for bar, val in zip(bars, m_vals):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                 _fmt(val), ha="center", va="bottom", fontsize=9, color=TEXT_CLR)
    ax1.set_title(f"{year} — расходы по месяцам", fontsize=13, pad=10)
    ax1.set_ylabel("Руб.")
    ax1.grid(axis="y", alpha=0.15)

    # ── subplot 2: weekly line + budget + moving avg ──
    w_keys = sorted(weekly.keys())
    w_vals = [weekly[w] for w in w_keys]

    ax2.plot(w_keys, w_vals, marker="o", color=TEAL, linewidth=1.5, markersize=4, label="Расходы", zorder=3)

    if budget > 0:
        ax2.axhline(budget, color=YELLOW, linestyle="--", linewidth=1.5, label=f"Бюджет {_fmt(budget)}")
        w_arr = np.array(w_vals, dtype=float)
        bgt = np.full_like(w_arr, budget)
        ax2.fill_between(w_keys, w_arr, bgt, where=w_arr > bgt,
                         interpolate=True, color=RED, alpha=0.2)
        ax2.fill_between(w_keys, w_arr, bgt, where=w_arr <= bgt,
                         interpolate=True, color=GREEN, alpha=0.15)

    # moving average (4 weeks)
    if len(w_vals) >= 4:
        ma = np.convolve(w_vals, np.ones(4) / 4, mode="valid")
        ma_x = w_keys[3:]
        ax2.plot(ma_x, ma, color=YELLOW, linewidth=2, linestyle="-", alpha=0.7, label="Тренд (4 нед.)")

    ax2.set_title(f"{year} — расходы по неделям", fontsize=13, pad=10)
    ax2.set_xlabel("Неделя")
    ax2.set_ylabel("Руб.")
    ax2.grid(axis="y", alpha=0.15)
    ax2.legend(loc="upper left", fontsize=9)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    return buf
