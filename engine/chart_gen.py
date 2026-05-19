"""Annotated chart generation for Phase 5A."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd
from matplotlib.patches import PathPatch, Polygon, Rectangle
from matplotlib.path import Path as MplPath

from config import settings


LOOKBACK_BARS = 120
CHART_DPI = 100
CHART_SIZE = (12, 8)

BG = "#0a0a0a"
PANEL = "#141414"
GRID = "#2a2a2a"
TEXT = "#f5f5f5"
MUTED = "#a3a3a3"
ACCENT = "#ff4800"
GREEN = "#22c55e"
RED = "#ef4444"
BLUE = "#38bdf8"
YELLOW = "#facc15"


def generate_pattern_chart(
    df: pd.DataFrame | dict[str, Any],
    symbol: str,
    pattern_result: Any,
    *,
    all_patterns: Iterable[Any] | None = None,
    pivot: float | None = None,
    target: float | None = None,
    stop_loss: float | None = None,
    conviction: int | float | None = None,
    rsi_value: float | None = None,
    output_dir: str | Path | None = None,
    lookback_bars: int = LOOKBACK_BARS,
    chart_date: str | None = None,
) -> Path:
    """Generate a 1200x800 annotated candlestick PNG for one pattern hit."""
    source = _prepare_ohlcv_frame(df)
    if len(source) < 2:
        raise ValueError("At least two OHLCV rows are required to generate a chart")

    pattern_name = str(_field(pattern_result, "pattern", "Pattern"))
    pivot = _first_number(pivot, _field(pattern_result, "pivot"))
    target = _first_number(target, _field(pattern_result, "target"))
    stop_loss = _first_number(stop_loss, _field(pattern_result, "stop_loss"))
    conviction = _first_number(conviction, _field(pattern_result, "score"), _field(pattern_result, "confidence"))

    enriched = _with_indicators(source, rsi_value=rsi_value)
    plot_df = enriched.tail(max(2, int(lookback_bars))).copy()
    visible_start = len(enriched) - len(plot_df)

    fig, axes = _plot_base_chart(plot_df, symbol, pattern_name, conviction, all_patterns)
    price_ax = axes["price"]
    rsi_ax = axes["rsi"]
    _style_axes(fig, axes.values())
    _set_price_limits(price_ax, plot_df, [pivot, target, stop_loss])
    _draw_moving_average_legend(price_ax)
    _draw_key_levels(price_ax, len(plot_df), pivot, target, stop_loss)
    _draw_pattern_annotation(price_ax, plot_df, enriched, visible_start, pattern_result, pivot)
    _style_rsi_panel(rsi_ax, plot_df["RSI14"])

    output_path = _chart_path(output_dir, symbol, pattern_name, plot_df, chart_date)
    fig.subplots_adjust(left=0.06, right=0.88, top=0.90, bottom=0.08, hspace=0.10)
    fig.savefig(
        output_path,
        dpi=CHART_DPI,
        facecolor=BG,
        edgecolor=BG,
    )
    plt.close(fig)
    return output_path


def draw_cup_handle(
    ax,
    frame: pd.DataFrame,
    *,
    left_rim_x: int | None,
    trough_x: int | None,
    right_rim_x: int | None,
    handle_start_x: int | None,
    pivot: float | None,
) -> None:
    """Draw cup curve, rim, and handle labels."""
    if _valid_x(left_rim_x, frame) and _valid_x(trough_x, frame) and _valid_x(right_rim_x, frame):
        left_y = _price(frame, left_rim_x, "High")
        trough_y = _price(frame, trough_x, "Low")
        right_y = _price(frame, right_rim_x, "High")
        path = MplPath(
            [(left_rim_x, left_y), (trough_x, trough_y), (right_rim_x, right_y)],
            [MplPath.MOVETO, MplPath.CURVE3, MplPath.CURVE3],
        )
        ax.add_patch(PathPatch(path, edgecolor=ACCENT, facecolor="none", lw=2.0, alpha=0.95))
        _label(ax, trough_x, trough_y, "CUP", color=ACCENT, y_offset=-18)

    if pivot is not None:
        ax.hlines(pivot, 0, len(frame) - 1, colors=ACCENT, linestyles=":", linewidth=1.2, alpha=0.75)
        _label(ax, len(frame) - 1, pivot, "RIM", color=ACCENT, x_offset=8)

    if _valid_x(handle_start_x, frame):
        end_x = len(frame) - 1
        high = float(frame["High"].iloc[handle_start_x : end_x + 1].max())
        low = float(frame["Low"].iloc[handle_start_x : end_x + 1].min())
        ax.add_patch(
            Rectangle(
                (handle_start_x, low),
                max(1, end_x - handle_start_x),
                max(high - low, 0.01),
                facecolor=ACCENT,
                edgecolor=ACCENT,
                alpha=0.10,
                linewidth=1.0,
            )
        )
        _label(ax, handle_start_x, high, "HANDLE", color=ACCENT, y_offset=14)


def draw_ascending_triangle(
    ax,
    frame: pd.DataFrame,
    *,
    resistance: float | None,
    touch_xs: list[int],
    low_xs: list[int],
) -> None:
    """Draw flat resistance, rising support, and triangle fill."""
    if resistance is None:
        return
    left_x = min(touch_xs or [0])
    ax.hlines(resistance, left_x, len(frame) - 1, colors=ACCENT, linewidth=1.8)
    _label(
        ax,
        len(frame) - 1,
        resistance,
        f"RESISTANCE Rs.{_fmt(resistance)} ({len(touch_xs)} touches)",
        color=ACCENT,
        x_offset=8,
    )
    for x in touch_xs:
        if _valid_x(x, frame):
            ax.scatter([x], [_price(frame, x, "High")], color=ACCENT, s=22, zorder=5)

    support_points = [(x, _price(frame, x, "Low")) for x in low_xs if _valid_x(x, frame)]
    if len(support_points) >= 2:
        x1, y1 = support_points[0]
        x2, y2 = support_points[-1]
        ax.plot([x1, x2], [y1, y2], color=YELLOW, linewidth=1.8)
        ax.add_patch(
            Polygon(
                [(x1, resistance), (x2, resistance), (x2, y2), (x1, y1)],
                closed=True,
                facecolor=ACCENT,
                edgecolor="none",
                alpha=0.08,
            )
        )
        _label(ax, x2, y2, "RISING SUPPORT", color=YELLOW, y_offset=-18)


def draw_bull_flag(
    ax,
    frame: pd.DataFrame,
    *,
    pole_start_x: int | None,
    pole_end_x: int | None,
    flag_len: int | None,
    pole_pct: float | None,
    pullback_pct: float | None,
) -> None:
    """Draw flag pole and consolidation channel."""
    if _valid_x(pole_start_x, frame) and _valid_x(pole_end_x, frame):
        y1 = _price(frame, pole_start_x, "Close")
        y2 = _price(frame, pole_end_x, "Close")
        ax.plot([pole_start_x, pole_end_x], [y1, y2], color=GREEN, linewidth=2.4)
        _label(ax, pole_end_x, y2, f"POLE +{_fmt(pole_pct)}%", color=GREEN, y_offset=16)

    if flag_len:
        start_x = max(0, len(frame) - int(flag_len))
        end_x = len(frame) - 1
        if end_x > start_x:
            high_start = _price(frame, start_x, "High")
            high_end = _price(frame, end_x, "High")
            low_start = _price(frame, start_x, "Low")
            low_end = _price(frame, end_x, "Low")
            ax.plot([start_x, end_x], [high_start, high_end], color=ACCENT, linewidth=1.8)
            ax.plot([start_x, end_x], [low_start, low_end], color=ACCENT, linewidth=1.8)
            ax.add_patch(
                Polygon(
                    [(start_x, high_start), (end_x, high_end), (end_x, low_end), (start_x, low_start)],
                    closed=True,
                    facecolor=ACCENT,
                    edgecolor="none",
                    alpha=0.10,
                )
            )
            _label(ax, start_x, high_start, f"FLAG pullback {_fmt(pullback_pct)}%", color=ACCENT, y_offset=14)


def draw_vcp(
    ax,
    frame: pd.DataFrame,
    *,
    pattern_start_x: int,
    contractions_pct: list[float],
    pivot: float | None,
) -> None:
    """Draw contraction zones and pivot tightness label."""
    if not contractions_pct:
        return
    start_x = max(0, pattern_start_x)
    segments = np.array_split(np.arange(start_x, len(frame)), len(contractions_pct))
    swing_points = []
    for idx, segment in enumerate(segments):
        if len(segment) == 0:
            continue
        seg = frame.iloc[segment]
        high_x = int(segment[int(np.argmax(seg["High"].to_numpy()))])
        low_x = int(segment[int(np.argmin(seg["Low"].to_numpy()))])
        high_y = _price(frame, high_x, "High")
        low_y = _price(frame, low_x, "Low")
        ax.plot([high_x, low_x], [high_y, low_y], color=ACCENT, linewidth=1.6)
        _label(ax, low_x, low_y, f"C{idx + 1}: {_fmt(contractions_pct[idx])}%", color=ACCENT, y_offset=-16)
        swing_points.extend([(high_x, high_y), (low_x, low_y)])
    if len(swing_points) >= 2:
        ax.plot([x for x, _ in swing_points], [y for _, y in swing_points], color=YELLOW, linewidth=1.0, alpha=0.65)

    if pivot is not None:
        zone_start = max(0, len(frame) - 30)
        zone_height = max(pivot * 0.02, 0.01)
        ax.add_patch(
            Rectangle(
                (zone_start, pivot - zone_height / 2),
                len(frame) - zone_start - 1,
                zone_height,
                facecolor=YELLOW,
                edgecolor=YELLOW,
                alpha=0.10,
            )
        )
        _label(ax, len(frame) - 1, pivot, "PIVOT ZONE", color=YELLOW, x_offset=8)


def draw_inv_hs(
    ax,
    frame: pd.DataFrame,
    *,
    left_shoulder_x: int | None,
    head_x: int | None,
    right_shoulder_x: int | None,
    neckline: float | None,
) -> None:
    """Draw inverse head-and-shoulders troughs and neckline."""
    points = [
        ("LS", left_shoulder_x),
        ("HEAD", head_x),
        ("RS", right_shoulder_x),
    ]
    plotted = []
    for label, x in points:
        if _valid_x(x, frame):
            y = _price(frame, x, "Low")
            ax.scatter([x], [y], color=ACCENT, s=30, zorder=5)
            _label(ax, x, y, label, color=ACCENT, y_offset=-18)
            plotted.append((x, y))
    if len(plotted) >= 2:
        ax.plot([x for x, _ in plotted], [y for _, y in plotted], color=ACCENT, linewidth=1.8)
    if neckline is not None:
        ax.hlines(neckline, 0, len(frame) - 1, colors=YELLOW, linewidth=1.8)
        _label(ax, len(frame) - 1, neckline, f"NECKLINE Rs.{_fmt(neckline)}", color=YELLOW, x_offset=8)


def draw_supertrend(
    ax,
    frame: pd.DataFrame,
    *,
    supertrend_values: np.ndarray,
    flip_x: int | None,
) -> None:
    """Draw supertrend support line and bullish flip marker."""
    if len(supertrend_values) == len(frame):
        ax.plot(np.arange(len(frame)), supertrend_values, color=GREEN, linewidth=1.8, alpha=0.95)
    if _valid_x(flip_x, frame):
        y = _price(frame, flip_x, "Low")
        ax.annotate(
            "BULLISH FLIP",
            xy=(flip_x, y),
            xytext=(flip_x, y * 0.97),
            color=GREEN,
            fontsize=8,
            fontweight="bold",
            ha="center",
            arrowprops={"arrowstyle": "->", "color": GREEN, "lw": 1.4},
            bbox={"boxstyle": "round,pad=0.25", "fc": PANEL, "ec": GREEN, "alpha": 0.95},
        )


def draw_multiyear_breakout(
    ax,
    frame: pd.DataFrame,
    *,
    resistance_level: float | None,
    touch_xs: list[int],
    years: float | None,
) -> None:
    """Draw multiyear resistance and breakout arrow."""
    if resistance_level is None:
        return
    ax.hlines(resistance_level, 0, len(frame) - 1, colors=ACCENT, linewidth=1.8)
    years_text = f"held {_fmt(years)} years" if years else "multiyear"
    _label(
        ax,
        len(frame) - 1,
        resistance_level,
        f"RESISTANCE Rs.{_fmt(resistance_level)} ({years_text})",
        color=ACCENT,
        x_offset=8,
    )
    for x in touch_xs:
        if _valid_x(x, frame):
            ax.scatter([x], [resistance_level], color=ACCENT, s=22, zorder=5)
    close = float(frame["Close"].iloc[-1])
    if close >= resistance_level:
        ax.annotate(
            "BREAKOUT",
            xy=(len(frame) - 1, close),
            xytext=(max(0, len(frame) - 18), close * 1.03),
            color=GREEN,
            fontsize=8,
            fontweight="bold",
            arrowprops={"arrowstyle": "->", "color": GREEN, "lw": 1.4},
            bbox={"boxstyle": "round,pad=0.25", "fc": PANEL, "ec": GREEN, "alpha": 0.95},
        )


def _plot_base_chart(
    frame: pd.DataFrame,
    symbol: str,
    pattern_name: str,
    conviction: float | None,
    all_patterns: Iterable[Any] | None,
) -> tuple[plt.Figure, dict[str, Any]]:
    addplots = []
    for column, color, width in [("MA50", BLUE, 1.1), ("MA150", YELLOW, 1.0), ("MA200", RED, 1.0)]:
        if frame[column].notna().any():
            addplots.append(mpf.make_addplot(frame[column], panel=0, color=color, width=width))
    addplots.append(mpf.make_addplot(frame["RSI14"], panel=2, color=ACCENT, width=1.2, ylabel="RSI"))

    market_colors = mpf.make_marketcolors(
        up=GREEN,
        down=RED,
        edge="inherit",
        wick={"up": GREEN, "down": RED},
        volume={"up": GREEN, "down": RED},
    )
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=market_colors,
        facecolor=BG,
        figcolor=BG,
        gridcolor=GRID,
        gridstyle="-",
        rc={
            "axes.edgecolor": GRID,
            "axes.labelcolor": TEXT,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "font.family": "DejaVu Sans",
            "savefig.facecolor": BG,
            "savefig.edgecolor": BG,
        },
    )
    fig, axlist = mpf.plot(
        frame[["Open", "High", "Low", "Close", "Volume"]],
        type="candle",
        volume=True,
        addplot=addplots,
        panel_ratios=(5, 1.25, 1.25),
        style=style,
        figsize=CHART_SIZE,
        returnfig=True,
        datetime_format="%d %b",
        xrotation=0,
        warn_too_much_data=len(frame) + 1,
    )
    axes = {"price": axlist[0], "volume": axlist[2], "rsi": axlist[4] if len(axlist) > 4 else axlist[-1]}
    title = _title(symbol, pattern_name, conviction, all_patterns)
    fig.suptitle(title, color=TEXT, fontsize=15, fontweight="bold", y=0.965)
    return fig, axes


def _draw_pattern_annotation(
    ax,
    plot_df: pd.DataFrame,
    source: pd.DataFrame,
    visible_start: int,
    pattern_result: Any,
    pivot: float | None,
) -> None:
    extra = dict(_field(pattern_result, "extra", {}) or {})
    pattern_name = str(_field(pattern_result, "pattern", "")).lower()
    pattern_bars = int(_first_number(_field(pattern_result, "bars_in_pattern"), len(source)) or len(source))
    total = len(source)

    if "cup" in pattern_name:
        draw_cup_handle(
            ax,
            plot_df,
            left_rim_x=_relative_x(extra.get("left_rim_idx"), total, pattern_bars, visible_start, len(plot_df)),
            trough_x=_relative_x(extra.get("trough_idx"), total, pattern_bars, visible_start, len(plot_df)),
            right_rim_x=_relative_x(extra.get("right_rim_idx"), total, pattern_bars, visible_start, len(plot_df)),
            handle_start_x=_relative_x(extra.get("handle_start_idx"), total, pattern_bars, visible_start, len(plot_df)),
            pivot=pivot,
        )
    elif "ascending triangle" in pattern_name:
        touch_xs = _relative_xs(extra.get("touch_indices", []), total, pattern_bars, visible_start, len(plot_df))
        low_xs = _relative_xs(extra.get("low_indices", []), total, pattern_bars, visible_start, len(plot_df))
        draw_ascending_triangle(ax, plot_df, resistance=pivot, touch_xs=touch_xs, low_xs=low_xs)
    elif "bull flag" in pattern_name:
        draw_bull_flag(
            ax,
            plot_df,
            pole_start_x=_absolute_x(extra.get("pole_start_idx"), visible_start, len(plot_df)),
            pole_end_x=_absolute_x(extra.get("pole_end_idx"), visible_start, len(plot_df)),
            flag_len=_int_or_none(extra.get("flag_len")),
            pole_pct=_first_number(extra.get("pole_pct")),
            pullback_pct=_first_number(extra.get("pullback_pct")),
        )
    elif pattern_name == "vcp" or "volatility contraction" in pattern_name:
        draw_vcp(
            ax,
            plot_df,
            pattern_start_x=max(0, len(source) - pattern_bars - visible_start),
            contractions_pct=[float(item) for item in extra.get("contractions_pct", [])],
            pivot=pivot,
        )
    elif "head" in pattern_name and "shoulder" in pattern_name:
        draw_inv_hs(
            ax,
            plot_df,
            left_shoulder_x=_relative_x(extra.get("left_shoulder_idx"), total, pattern_bars, visible_start, len(plot_df)),
            head_x=_relative_x(extra.get("head_idx"), total, pattern_bars, visible_start, len(plot_df)),
            right_shoulder_x=_relative_x(extra.get("right_shoulder_idx"), total, pattern_bars, visible_start, len(plot_df)),
            neckline=_first_number(extra.get("neckline"), pivot),
        )
    elif "supertrend" in pattern_name:
        supertrend = _supertrend_line(source)
        visible = supertrend[-len(plot_df) :] if len(supertrend) >= len(plot_df) else np.array([], dtype=float)
        draw_supertrend(
            ax,
            plot_df,
            supertrend_values=visible,
            flip_x=_absolute_x(extra.get("flip_idx"), visible_start, len(plot_df)),
        )
    elif "multi-year" in pattern_name or "multiyear" in pattern_name:
        touch_xs = _relative_xs(extra.get("resistance_touch_indices", []), total, pattern_bars, visible_start, len(plot_df))
        draw_multiyear_breakout(
            ax,
            plot_df,
            resistance_level=pivot,
            touch_xs=touch_xs,
            years=_first_number(extra.get("years")),
        )
    else:
        if pivot is not None:
            _label(ax, len(plot_df) - 1, pivot, str(_field(pattern_result, "pattern", "PATTERN")), color=ACCENT, x_offset=8)


def _prepare_ohlcv_frame(data: pd.DataFrame | dict[str, Any]) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        frame = data.copy()
    elif isinstance(data, dict):
        frame = pd.DataFrame(data)
    else:
        raise TypeError("df must be a pandas DataFrame or OHLCV dictionary")

    if frame.empty:
        raise ValueError("Cannot generate chart from an empty OHLCV frame")

    columns = {str(column).lower(): column for column in frame.columns}
    date_col = columns.get("date") or columns.get("week")
    if date_col is not None:
        index = pd.to_datetime(frame[date_col], errors="coerce")
    else:
        index = pd.to_datetime(frame.index, errors="coerce")

    rename = {}
    for source, target in [("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close"), ("volume", "Volume")]:
        if source not in columns:
            raise ValueError(f"Missing OHLCV column: {source}")
        rename[columns[source]] = target

    frame = frame.rename(columns=rename)
    frame.index = pd.DatetimeIndex(index)
    frame = frame[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    frame["Volume"] = frame["Volume"].fillna(0.0)
    frame = frame[~frame.index.isna()].sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    if frame.empty:
        raise ValueError("No valid OHLCV rows remain after cleaning")
    return frame


def _with_indicators(frame: pd.DataFrame, *, rsi_value: float | None) -> pd.DataFrame:
    enriched = frame.copy()
    enriched["MA50"] = enriched["Close"].rolling(50, min_periods=50).mean()
    enriched["MA150"] = enriched["Close"].rolling(150, min_periods=150).mean()
    enriched["MA200"] = enriched["Close"].rolling(200, min_periods=200).mean()
    enriched["RSI14"] = _rsi(enriched["Close"])
    supplied_rsi = _first_number(rsi_value)
    if supplied_rsi is not None:
        enriched.iloc[-1, enriched.columns.get_loc("RSI14")] = supplied_rsi
    enriched["RSI14"] = enriched["RSI14"].fillna(50.0).clip(0.0, 100.0)
    return enriched


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    avg_gain = gains.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.mask((avg_loss == 0.0) & (avg_gain > 0.0), 100.0)
    rsi = rsi.mask((avg_gain == 0.0) & (avg_loss > 0.0), 0.0)
    rsi = rsi.mask((avg_gain == 0.0) & (avg_loss == 0.0), 50.0)
    return rsi.fillna(50.0)


def _supertrend_line(frame: pd.DataFrame) -> np.ndarray:
    period = int(settings.SUPERTREND["atr_period"])
    multiplier = float(settings.SUPERTREND["multiplier"])
    high = frame["High"].to_numpy(dtype=float)
    low = frame["Low"].to_numpy(dtype=float)
    close = frame["Close"].to_numpy(dtype=float)
    if len(close) < period:
        return np.array([], dtype=float)

    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    true_range = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    atr = np.zeros_like(close, dtype=float)
    atr[:period] = np.mean(true_range[:period])
    for idx in range(period, len(close)):
        atr[idx] = (atr[idx - 1] * (period - 1) + true_range[idx]) / period

    hl2 = (high + low) / 2.0
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr
    final_upper = upper.copy()
    final_lower = lower.copy()
    direction = np.ones(len(close), dtype=int)
    line = np.zeros(len(close), dtype=float)
    for idx in range(1, len(close)):
        final_upper[idx] = upper[idx] if upper[idx] < final_upper[idx - 1] or close[idx - 1] > final_upper[idx - 1] else final_upper[idx - 1]
        final_lower[idx] = lower[idx] if lower[idx] > final_lower[idx - 1] or close[idx - 1] < final_lower[idx - 1] else final_lower[idx - 1]
        if close[idx] > final_upper[idx - 1]:
            direction[idx] = 1
        elif close[idx] < final_lower[idx - 1]:
            direction[idx] = -1
        else:
            direction[idx] = direction[idx - 1]
        line[idx] = final_lower[idx] if direction[idx] == 1 else final_upper[idx]
    line[0] = final_lower[0]
    return line


def _draw_key_levels(
    ax,
    bars: int,
    pivot: float | None,
    target: float | None,
    stop_loss: float | None,
) -> None:
    for value, label, color in [
        (pivot, "ENTRY", ACCENT),
        (target, "TARGET", GREEN),
        (stop_loss, "STOP", RED),
    ]:
        if value is None:
            continue
        ax.hlines(value, -0.5, bars - 0.5, colors=color, linestyles="--", linewidth=1.2, alpha=0.9)
        _label(ax, bars - 1, value, f"{label} Rs.{_fmt(value)}", color=color, x_offset=8)


def _draw_moving_average_legend(ax) -> None:
    ax.text(
        0.01,
        0.98,
        "MA50  MA150  MA200",
        transform=ax.transAxes,
        ha="left",
        va="top",
        color=TEXT,
        fontsize=8,
        bbox={"boxstyle": "round,pad=0.25", "fc": PANEL, "ec": GRID, "alpha": 0.9},
    )
    ax.text(0.018, 0.935, "blue", transform=ax.transAxes, ha="left", va="top", color=BLUE, fontsize=7)
    ax.text(0.066, 0.935, "yellow", transform=ax.transAxes, ha="left", va="top", color=YELLOW, fontsize=7)
    ax.text(0.128, 0.935, "red", transform=ax.transAxes, ha="left", va="top", color=RED, fontsize=7)


def _style_rsi_panel(ax, rsi: pd.Series) -> None:
    ax.axhline(70, color=RED, linewidth=0.8, linestyle="--", alpha=0.70)
    ax.axhline(30, color=GREEN, linewidth=0.8, linestyle="--", alpha=0.70)
    ax.set_ylim(0, 100)
    latest = float(rsi.iloc[-1])
    _label(ax, len(rsi) - 1, latest, f"RSI {_fmt(latest)}", color=ACCENT, x_offset=8)


def _set_price_limits(ax, frame: pd.DataFrame, levels: list[float | None]) -> None:
    values = [float(frame["Low"].min()), float(frame["High"].max())]
    values.extend(float(item) for item in levels if item is not None and np.isfinite(item))
    low = min(values)
    high = max(values)
    span = max(high - low, abs(high) * 0.02, 1.0)
    ax.set_ylim(low - span * 0.10, high + span * 0.12)
    ax.set_xlim(-1, len(frame) + 13)


def _style_axes(fig: plt.Figure, axes: Iterable[Any]) -> None:
    fig.patch.set_facecolor(BG)
    for ax in axes:
        ax.set_facecolor(BG)
        ax.tick_params(colors=MUTED, labelsize=8)
        ax.yaxis.label.set_color(MUTED)
        ax.xaxis.label.set_color(MUTED)
        for spine in ax.spines.values():
            spine.set_color(GRID)


def _chart_path(
    output_dir: str | Path | None,
    symbol: str,
    pattern_name: str,
    frame: pd.DataFrame,
    chart_date: str | None,
) -> Path:
    out_dir = Path(output_dir) if output_dir is not None else settings.CHARTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    if chart_date is None:
        latest = frame.index[-1]
        chart_date = latest.strftime("%Y%m%d") if not pd.isna(latest) else date.today().strftime("%Y%m%d")
    filename = f"{_slug(symbol)}_{_slug(pattern_name)}_{_slug(chart_date)}.png"
    return out_dir / filename


def _title(
    symbol: str,
    pattern_name: str,
    conviction: float | None,
    all_patterns: Iterable[Any] | None,
) -> str:
    score = "N/A" if conviction is None else f"{int(round(conviction))}/100"
    patterns = list(all_patterns or [])
    stack = f" | {len(patterns)} patterns" if len(patterns) > 1 else ""
    return f"{symbol.upper()} - {pattern_name.upper()} - {score}{stack}"


def _relative_x(
    idx: Any,
    total_bars: int,
    pattern_bars: int,
    visible_start: int,
    plot_bars: int,
) -> int | None:
    number = _int_or_none(idx)
    if number is None:
        return None
    raw_idx = total_bars - pattern_bars + number
    chart_x = raw_idx - visible_start
    return chart_x if 0 <= chart_x < plot_bars else None


def _relative_xs(
    values: Iterable[Any],
    total_bars: int,
    pattern_bars: int,
    visible_start: int,
    plot_bars: int,
) -> list[int]:
    xs = [_relative_x(value, total_bars, pattern_bars, visible_start, plot_bars) for value in values]
    return [int(x) for x in xs if x is not None]


def _absolute_x(idx: Any, visible_start: int, plot_bars: int) -> int | None:
    number = _int_or_none(idx)
    if number is None:
        return None
    chart_x = number - visible_start
    return chart_x if 0 <= chart_x < plot_bars else None


def _label(
    ax,
    x: int,
    y: float,
    text: str,
    *,
    color: str,
    x_offset: int = 0,
    y_offset: int = 0,
) -> None:
    ax.annotate(
        text,
        xy=(x, y),
        xytext=(x_offset, y_offset),
        textcoords="offset points",
        color=color,
        fontsize=8,
        fontweight="bold",
        ha="left" if x_offset >= 0 else "right",
        va="center",
        clip_on=False,
        bbox={"boxstyle": "round,pad=0.25", "fc": PANEL, "ec": color, "alpha": 0.92, "lw": 0.8},
    )


def _price(frame: pd.DataFrame, x: int, column: str) -> float:
    return float(frame[column].iloc[int(x)])


def _valid_x(x: int | None, frame: pd.DataFrame) -> bool:
    return x is not None and 0 <= int(x) < len(frame)


def _field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _first_number(*values: Any) -> float | None:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(number):
            return number
    return None


def _int_or_none(value: Any) -> int | None:
    number = _first_number(value)
    return None if number is None else int(number)


def _fmt(value: Any) -> str:
    number = _first_number(value)
    if number is None:
        return "N/A"
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip()).strip("_").lower()
    return text or "chart"
