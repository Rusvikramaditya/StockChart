"""Daily NSE pattern scanner pipeline."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import importlib.util
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from config import settings
from engine import dashboard, dhan_client, momentum_radar, signal_tracker, storage, telegram, universe
from engine.chart_gen import generate_pattern_chart
from engine.chart_payload import build_chart_payload
from engine.data_loader import DataLoader
from engine.dedup import deduplicate_results
from engine.eod_catchup import catch_up_daily_eod, local_data_status
from engine.explainer import attach_explanation
from engine.scorer import score_pattern
from engine.thesis_chart import export_thesis_chart_png
from filters.market_regime import compute_market_regime
from engine.sector_leaderboard import compute_leaderboard, _load_sector_map
from filters.sector_rs import compute_sector_rs_cache
from patterns.base import PatternResult


STAGE_ORDER = ("verify", "fetch_missing", "fetch", "pre_compute", "detect", "filter_and_score", "output")

# Cross-process lock file. Held during the fetch_missing + fetch stages so
# two scanners cannot write to the SQLite DB concurrently. Stale locks are
# detected via PID-presence check.
_FETCH_LOCK_PATH = settings.DATA_DIR / "scanner_fetch.lock"


@contextlib.contextmanager
def _fetch_lock(timeout_seconds: int = 600):
    """Acquire a filesystem lock to serialize Dhan fetches across scanner runs.

    Best-effort, portable across Windows and POSIX. Stale locks (PID no
    longer running) are reclaimed automatically.
    """
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout_seconds
    while True:
        try:
            fd = os.open(str(_FETCH_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.close(fd)
            break
        except FileExistsError:
            if _is_stale_lock():
                try:
                    _FETCH_LOCK_PATH.unlink()
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                raise PipelineError(
                    f"Could not acquire fetch lock at {_FETCH_LOCK_PATH} within {timeout_seconds}s"
                )
            time.sleep(2.0)
    try:
        yield
    finally:
        try:
            _FETCH_LOCK_PATH.unlink()
        except OSError:
            pass


def _is_stale_lock() -> bool:
    """True if the lock file references a PID that is no longer running."""
    try:
        pid_text = _FETCH_LOCK_PATH.read_text(encoding="utf-8").strip()
        pid = int(pid_text) if pid_text else 0
    except (OSError, ValueError):
        return True
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
        return False  # process exists
    except ProcessLookupError:
        return True  # gone
    except PermissionError:
        return False  # exists but inaccessible; treat as live
    except OSError:
        return True  # any other error - safer to reclaim


@dataclass
class PipelineContext:
    universe_name: str = "nifty500"
    universe_path: Path | None = None
    scan_timeframe: str = "daily"
    scan_date: date = field(default_factory=date.today)
    dry_run: bool = False
    skip_fetch: bool = False
    fetch_missing: bool = True
    stage: str | None = None
    workers: int = settings.PROCESS_WORKERS
    stock_timeout_seconds: int = settings.STOCK_TIMEOUT_SECONDS
    fetch_all_data: bool = False
    check_rebalance: bool = False
    refresh_universe: bool = False
    min_liquidity: bool = False
    limit: int | None = None
    output_path: Path | None = None
    send_telegram: bool = True
    loader: DataLoader | None = None

    selected_profile: pd.DataFrame | None = None
    symbols: list[str] = field(default_factory=list)
    liquidity_profile: dict[str, dict] = field(default_factory=dict)
    market_regime: dict = field(default_factory=dict)
    sector_rs_cache: dict = field(default_factory=dict)
    sector_leaderboard: dict = field(default_factory=dict)
    daily_arrays: dict[str, dict] = field(default_factory=dict)
    weekly_arrays: dict[str, dict] = field(default_factory=dict)
    raw_hits: list[dict] = field(default_factory=list)
    scored_results: list[dict] = field(default_factory=list)
    momentum_radar: list[dict] = field(default_factory=list)
    signal_tracker: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    stage_timings: dict[str, float] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    alerts_sent: int = 0
    dashboard_path: Path | None = None


class PipelineError(RuntimeError):
    """Raised when a critical pipeline stage fails."""


def stage(name: str, *, critical: bool = True) -> Callable:
    """Record stage timings and normalize stage errors."""

    def decorate(func: Callable) -> Callable:
        def wrapped(self: "Pipeline", *args, **kwargs):
            started = time.perf_counter()
            try:
                return func(self, *args, **kwargs)
            except Exception as exc:
                self._record_error(name, "-", str(exc), critical=critical)
                if critical:
                    raise
                return None
            finally:
                self.ctx.stage_timings[name] = round(time.perf_counter() - started, 3)

        wrapped.__name__ = func.__name__
        wrapped.__doc__ = func.__doc__
        return wrapped

    return decorate


class Pipeline:
    def __init__(self, ctx: PipelineContext):
        self.ctx = ctx

    def _log_data(self, message: str) -> None:
        print(f"[data] {message}", flush=True)

    def run(self) -> PipelineContext:
        if self.ctx.stage and self.ctx.stage not in STAGE_ORDER:
            raise PipelineError(f"Unsupported stage '{self.ctx.stage}'. Supported: {', '.join(STAGE_ORDER)}")

        for name in STAGE_ORDER:
            getattr(self, name)()
            if self.ctx.stage == name:
                break
        return self.ctx

    @stage("verify")
    def verify(self) -> None:
        self.ctx.loader = self.ctx.loader or DataLoader()
        storage.ensure_schema(self.ctx.loader.conn)
        profile = self.ctx.loader.get_universe_profile(self.ctx.universe_name)
        if self.ctx.limit is not None:
            profile = profile.head(max(0, int(self.ctx.limit))).copy()
        if profile.empty:
            raise PipelineError(f"Universe '{self.ctx.universe_name}' selected no symbols")

        self.ctx.selected_profile = profile.reset_index(drop=True)
        self.ctx.symbols = self.ctx.selected_profile["symbol"].astype(str).str.upper().tolist()
        self.ctx.universe_path = self.ctx.universe_path or universe.PROFILE_PATHS.get(
            universe.normalise_profile_name(self.ctx.universe_name)
        )
        self._verify_output_path()
        verify_error_count = len(self.ctx.errors)
        self._verify_data_coverage()
        new_verify_errors = len(self.ctx.errors) - verify_error_count
        if new_verify_errors > 1:
            raise PipelineError(f"Verification failed with {new_verify_errors} non-critical data issue(s)")
        self.ctx.stats["symbols_selected"] = len(self.ctx.symbols)

    @stage("fetch_missing", critical=False)
    def fetch_missing(self) -> None:
        """Catch up completed EOD candles before live/today fetch."""
        if self.ctx.dry_run or not self.ctx.fetch_missing:
            reason = "dry run" if self.ctx.dry_run else "disabled by --no-fetch-missing"
            self._log_data(f"EOD catch-up skipped: {reason}.")
            if self.ctx.loader is not None:
                self._record_local_data_status(reason="catch-up skipped")
            self.ctx.stats["fetch_missing"] = "skipped"
            return
        if self.ctx.selected_profile is None:
            raise PipelineError("verify must run before fetch_missing")
        assert self.ctx.loader is not None
        self._log_data(
            f"EOD catch-up start: universe={self.ctx.universe_name}, symbols={len(self.ctx.selected_profile)}."
        )
        with _fetch_lock():
            summary = catch_up_daily_eod(
                self.ctx.loader.conn,
                self.ctx.selected_profile,
                logger=self._log_data,
            )
        self.ctx.stats["data_status"] = summary.to_dict()
        self.ctx.stats["fetch_missing"] = {
            "source": "eod_catchup",
            "missing_days": len(summary.missing_days),
            "caught_up_days": len(summary.caught_up_days),
            "rows_written": summary.rows_written,
            "source_counts": summary.source_counts,
            "data_as_of": summary.data_as_of,
        }
        for warning in summary.warnings:
            self._record_error("fetch_missing", "-", warning, critical=False)
        if summary.rows_written:
            self._run_weekly_incremental(full=True)

    @stage("fetch")
    def fetch(self) -> None:
        if self.ctx.dry_run or self.ctx.skip_fetch:
            reason = "dry run" if self.ctx.dry_run else "disabled by --skip-fetch"
            self._log_data(f"Dhan live fetch skipped: {reason}.")
            self.ctx.stats["fetch"] = "skipped"
            return
        if self.ctx.selected_profile is None:
            raise PipelineError("verify must run before fetch")
        assert self.ctx.loader is not None
        self._log_data(
            f"Dhan live fetch start: universe={self.ctx.universe_name}, symbols={len(self.ctx.selected_profile)}."
        )
        with _fetch_lock():
            try:
                rows = self.ctx.loader.fetch_todays_candles(
                    self.ctx.selected_profile,
                    universe_name=self.ctx.universe_name,
                )
            except dhan_client.DhanDataNotSubscribedError as exc:
                self._log_data("Dhan live fetch unavailable: Data APIs not subscribed; using EOD/local data.")
                self.ctx.stats["fetch"] = {
                    "source": "dhan",
                    "status": "not_subscribed",
                    "fallback": "eod_catchup",
                }
                self._record_error(
                    "fetch",
                    "-",
                    "Dhan market data is not subscribed; using EOD/local data instead.",
                    critical=False,
                )
                self._record_local_data_status(reason="Dhan not subscribed fallback")
                return
            except dhan_client.DhanRateLimitError as exc:
                self._log_data(f"Dhan live fetch blocked by rate limit: {exc}")
                raise
            except dhan_client.DhanError as exc:
                self._log_data(f"Dhan live fetch failed: {exc}")
                raise
            self.ctx.stats["rows_fetched"] = rows
            self.ctx.stats["fetch"] = {"source": "dhan", "rows_written": rows}
            self._log_data(f"Dhan live fetch finished: rows_written={rows}.")
            self._record_local_data_status(reason="after Dhan live fetch")
            if rows:
                data_status = dict(self.ctx.stats.get("data_status") or {})
                source_counts = dict(data_status.get("source_counts") or {})
                source_counts["dhan"] = source_counts.get("dhan", 0) + rows
                data_status["source_counts"] = source_counts
                rows_written = int(data_status.get("rows_written") or 0)
                data_status["rows_written"] = rows_written + rows
                self.ctx.stats["data_status"] = data_status
            _refresh_index_today(self.ctx.loader.conn)
            if self.ctx.scan_date.weekday() == 4 or self.ctx.scan_timeframe in {"weekly", "all"}:
                self._run_weekly_incremental()

    @stage("pre_compute")
    def pre_compute(self) -> None:
        if not self.ctx.symbols:
            raise PipelineError("verify must run before pre_compute")
        assert self.ctx.loader is not None
        self._exclude_stale_symbols_from_scan()
        sector_map = _load_sector_map()
        sector_symbols = {str(s).upper() for s in sector_map.keys()}
        breadth_universe = sorted(set(self.ctx.symbols) | sector_symbols)
        try:
            breadth_stats = self.ctx.loader.get_recent_close_stats(
                breadth_universe, ma_periods=(50, 200)
            )
        except Exception as exc:  # batch breadth is an optimization; fall back to per-call
            self._record_error("pre_compute", "-", f"breadth_stats: {exc}", critical=False)
            breadth_stats = {}
        self.ctx.market_regime = compute_market_regime(
            self.ctx.loader, self.ctx.symbols, breadth_stats=breadth_stats or None
        )
        self.ctx.sector_rs_cache = compute_sector_rs_cache(self.ctx.loader, self.ctx.symbols)
        try:
            self.ctx.sector_leaderboard = compute_leaderboard(
                self.ctx.loader,
                sector_map=sector_map,
                breadth_stats=breadth_stats or None,
            )
        except Exception as exc:  # leaderboard is auxiliary; don't fail the scan
            self._record_error("pre_compute", "-", f"sector_leaderboard: {exc}", critical=False)
            self.ctx.sector_leaderboard = {}
        self.ctx.liquidity_profile = _liquidity_map(self.ctx.selected_profile)
        self.ctx.stats["market_regime"] = self.ctx.market_regime.get("verdict", "UNKNOWN")

    @stage("detect")
    def detect(self) -> None:
        assert self.ctx.loader is not None
        prepared = self._load_detector_inputs(self.ctx.symbols)
        self.ctx.daily_arrays = {symbol: daily for symbol, daily, _weekly in prepared}
        self.ctx.weekly_arrays = {symbol: weekly for symbol, _daily, weekly in prepared}
        if not prepared:
            self.ctx.raw_hits = []
            self.ctx.stats["detect"] = "no_symbols_with_daily_data"
            return

        if int(self.ctx.workers) <= 1:
            results = [
                _detect_symbol(symbol, daily, weekly, self.ctx.universe_name, self.ctx.scan_timeframe)
                for symbol, daily, weekly in prepared
            ]
        else:
            results = self._detect_parallel(prepared)

        self.ctx.raw_hits = []
        for item in results:
            symbol = str(item.get("symbol", "")).upper()
            for error in item.get("errors", []):
                self._record_error("detect", symbol, error, critical=False)
            for hit in item.get("hits", []):
                self.ctx.raw_hits.append({"symbol": symbol, "pattern_result": hit})
        self.ctx.stats["raw_hits"] = len(self.ctx.raw_hits)

    @stage("filter_and_score")
    def filter_and_score(self) -> None:
        assert self.ctx.loader is not None
        scored_results: list[dict] = []
        company_names = _company_name_map(self.ctx.selected_profile)

        for raw in self.ctx.raw_hits:
            symbol = str(raw["symbol"]).upper()
            pattern = raw["pattern_result"]
            daily = self.ctx.daily_arrays.get(symbol) or self.ctx.loader.get_stock_daily_arrays(symbol)
            weekly = self.ctx.weekly_arrays.get(symbol) or self.ctx.loader.get_stock_weekly_arrays(symbol)
            try:
                scored = score_pattern(
                    symbol,
                    pattern,
                    daily,
                    weekly,
                    self.ctx.market_regime,
                    self.ctx.sector_rs_cache,
                )
                scored["cmp"] = _latest_close(daily)
                scored["signal_date"] = _latest_date(daily)
                scored["company_name"] = company_names.get(symbol, symbol)
                _apply_liquidity(scored, self.ctx.liquidity_profile.get(symbol), self.ctx.min_liquidity)
                self._attach_chart_payload(scored)
                scored_results.append(attach_explanation(scored))
            except Exception as exc:
                self._record_error("filter_and_score", symbol, str(exc), critical=False)

        self.ctx.scored_results = deduplicate_results(scored_results)
        self.ctx.stats["scored_results"] = len(self.ctx.scored_results)
        self.ctx.momentum_radar = self._build_momentum_radar(company_names)
        self.ctx.stats["momentum_radar"] = len(self.ctx.momentum_radar)

    @stage("output")
    def output(self) -> None:
        generated_at = datetime.now()
        if self.ctx.send_telegram and not self.ctx.dry_run:
            self.ctx.alerts_sent = self._send_alerts()
            summary_sent = telegram.send_daily_summary(
                self.ctx.market_regime,
                self.ctx.scored_results,
                stocks_scanned=len(self.ctx.symbols),
                total_alerts=self.ctx.alerts_sent,
                data_status=self.ctx.stats.get("data_status"),
            )
            self.ctx.stats["telegram_summary_sent"] = summary_sent
            if not summary_sent:
                self._record_error(
                    "telegram",
                    "-",
                    "Daily summary was not sent; check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID",
                    critical=False,
                )

        self._record_current_signals(generated_at)
        self.ctx.signal_tracker = self._build_signal_tracker(generated_at)
        self.ctx.stats["signal_tracker"] = len(self.ctx.signal_tracker)

        output_context = {
            "generated_at": generated_at,
            "duration_seconds": sum(self.ctx.stage_timings.values()),
            "stocks_scanned": len(self.ctx.symbols),
            "scan_timeframe": self.ctx.scan_timeframe,
            "market_regime": self.ctx.market_regime,
            "sector_rs": self.ctx.sector_rs_cache,
            "sector_leaderboard": self.ctx.sector_leaderboard,
            "results": self.ctx.scored_results,
            "momentum_radar": self.ctx.momentum_radar,
            "signal_tracker": self.ctx.signal_tracker,
            "errors": self.ctx.errors,
            "alerts_sent": self.ctx.alerts_sent,
            "stats": self.ctx.stats,
            "data_status": self.ctx.stats.get("data_status"),
        }
        self.ctx.dashboard_path = dashboard.write_dashboard(output_context, self.ctx.output_path)
        self.ctx.stats["dashboard_path"] = str(self.ctx.dashboard_path)

    def close(self) -> None:
        if self.ctx.loader is not None:
            self.ctx.loader.close()

    def _build_momentum_radar(self, company_names: dict[str, str]) -> list[dict]:
        assert self.ctx.loader is not None
        actionable_symbols = {
            str(item.get("symbol", "")).upper()
            for item in self.ctx.scored_results
            if str(item.get("tier", "")).upper() != "SKIP" and bool(item.get("tradable", True))
        }
        skip_reason_by_symbol: dict[str, str] = {}
        for item in self.ctx.scored_results:
            symbol = str(item.get("symbol", "")).upper()
            reason = item.get("skip_reason")
            if symbol and reason and symbol not in skip_reason_by_symbol:
                skip_reason_by_symbol[symbol] = str(reason)

        rows: list[dict] = []
        for symbol in self.ctx.symbols:
            symbol = str(symbol).upper()
            if symbol in actionable_symbols:
                continue
            daily = self.ctx.daily_arrays.get(symbol)
            weekly = self.ctx.weekly_arrays.get(symbol)
            if daily is None:
                daily = self.ctx.loader.get_stock_daily_arrays(symbol)
            if weekly is None:
                weekly = self.ctx.loader.get_stock_weekly_arrays(symbol)
            radar = momentum_radar.evaluate(
                symbol,
                daily,
                weekly,
                company_name=company_names.get(symbol, symbol),
                skip_reason=skip_reason_by_symbol.get(symbol),
            )
            if radar is not None:
                rows.append(radar)

        rows.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("symbol") or "")))
        limit = int(settings.MOMENTUM_RADAR.get("max_results", 30))
        return rows[:limit]

    def _verify_output_path(self) -> None:
        target = self.ctx.output_path or settings.OUTPUT_DIR / "_scanner_write_probe.tmp"
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        probe = path if path.name == "_scanner_write_probe.tmp" else path.parent / "_scanner_write_probe.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)

    def _verify_data_coverage(self) -> None:
        assert self.ctx.loader is not None
        symbols = self.ctx.symbols
        daily_count = _covered_symbol_count(self.ctx.loader.conn, "ohlcv_daily", symbols)
        weekly_count = _covered_symbol_count(self.ctx.loader.conn, "ohlcv_weekly", symbols)
        if daily_count == 0:
            raise PipelineError(f"No daily OHLCV rows found for selected universe '{self.ctx.universe_name}'")
        if weekly_count == 0:
            self._record_error("verify", "-", "No weekly OHLCV rows found for selected universe", critical=False)

        index_count = self.ctx.loader.conn.execute(
            "SELECT COUNT(*) FROM index_daily WHERE index_name = 'NIFTY 50' AND close > 0"
        ).fetchone()[0]
        if int(index_count) == 0:
            self._record_error("verify", "-", "NIFTY 50 index history missing; regime will be UNKNOWN", critical=False)

        self.ctx.stats["daily_coverage"] = f"{daily_count}/{len(symbols)}"
        self.ctx.stats["weekly_coverage"] = f"{weekly_count}/{len(symbols)}"

    def _load_detector_inputs(self, symbols: Iterable[str]) -> list[tuple[str, dict, dict]]:
        assert self.ctx.loader is not None
        prepared = []
        for symbol in symbols:
            try:
                daily = self.ctx.loader.get_stock_daily_arrays(symbol)
                weekly = self.ctx.loader.get_stock_weekly_arrays(symbol)
                if len(daily.get("close", [])) == 0:
                    self._record_error("detect", symbol, "missing daily OHLCV rows", critical=False)
                    continue
                prepared.append((symbol, daily, weekly))
            except Exception as exc:
                self._record_error("detect", symbol, str(exc), critical=False)
        return prepared

    def _detect_parallel(self, prepared: list[tuple[str, dict, dict]]) -> list[dict]:
        workers = max(1, int(self.ctx.workers))
        timeout = max(1, int(self.ctx.stock_timeout_seconds))
        batch_timeout = timeout * max(1, math.ceil(len(prepared) / workers))
        results: list[dict] = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
            future_to_symbol = {
                executor.submit(
                    _detect_symbol,
                    symbol,
                    daily,
                    weekly,
                    self.ctx.universe_name,
                    self.ctx.scan_timeframe,
                ): symbol
                for symbol, daily, weekly in prepared
            }
            processed: set[concurrent.futures.Future] = set()
            try:
                completed = concurrent.futures.as_completed(future_to_symbol, timeout=batch_timeout)
                for future in completed:
                    processed.add(future)
                    symbol = future_to_symbol[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        results.append({"symbol": symbol, "hits": [], "errors": [str(exc)]})
            except concurrent.futures.TimeoutError:
                done = {future for future in future_to_symbol if future.done() and future not in processed}
                for future in done:
                    symbol = future_to_symbol[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        results.append({"symbol": symbol, "hits": [], "errors": [str(exc)]})
                for future, symbol in future_to_symbol.items():
                    if future not in done and future not in processed:
                        future.cancel()
                        results.append({"symbol": symbol, "hits": [], "errors": [f"detector timeout after {timeout}s"]})
        return results

    def _attach_chart_payload(self, scored: dict) -> None:
        assert self.ctx.loader is not None
        symbol = str(scored["symbol"]).upper()
        pattern = scored.get("pattern_result")
        if pattern is None:
            return
        timeframe = str(getattr(pattern, "timeframe", scored.get("timeframe", "daily"))).lower()
        frame = self.ctx.loader.get_stock_weekly(symbol) if timeframe == "weekly" else self.ctx.loader.get_stock_daily(symbol)
        try:
            scored["chart_payload"] = build_chart_payload(
                frame,
                symbol,
                pattern,
                company_name=scored.get("company_name"),
                entry_price=scored.get("entry_price"),
                timeframe=timeframe.title(),
            )
        except Exception as exc:
            self._record_error("chart_payload", symbol, str(exc), critical=False)

    def _send_alerts(self) -> int:
        sent = 0
        max_alerts = int(getattr(settings, "TELEGRAM_MAX_ALERTS", 0) or 0)
        data_status = self.ctx.stats.get("data_status") or {}
        data_as_of = str(data_status.get("data_as_of") or "")
        target_date = str(data_status.get("target_date") or data_as_of)
        for scored in self.ctx.scored_results:
            if max_alerts > 0 and sent >= max_alerts:
                break
            if not telegram.should_send_alert(scored):
                continue
            if not _liquidity_allows_alert(scored):
                continue
            signal_date = str(scored.get("signal_date") or data_as_of)
            if target_date and data_as_of and data_as_of < target_date:
                self.ctx.stats["alerts_suppressed_stale_data"] = (
                    self.ctx.stats.get("alerts_suppressed_stale_data", 0) + 1
                )
                continue
            if data_as_of and signal_date != data_as_of:
                self.ctx.stats["alerts_suppressed_stale_symbol"] = (
                    self.ctx.stats.get("alerts_suppressed_stale_symbol", 0) + 1
                )
                continue
            if self._alert_already_sent(scored, signal_date):
                self.ctx.stats["alerts_suppressed_duplicate"] = (
                    self.ctx.stats.get("alerts_suppressed_duplicate", 0) + 1
                )
                continue
            chart_path = self._alert_chart_path(scored)
            ok = False
            if chart_path and Path(str(chart_path)).exists():
                ok = telegram.send_chart_alert(scored, chart_path)
            if not ok:
                ok = telegram.send_alert(telegram.format_alert(scored))
            if ok:
                self._record_alert_sent(scored, signal_date)
                sent += 1
        return sent

    def _alert_chart_path(self, scored: dict) -> Path | None:
        if scored.get("chart_payload"):
            try:
                exported = export_thesis_chart_png(scored, output_dir=settings.CHARTS_DIR)
                scored["chart_html_path"] = str(exported["html_path"])
                scored["chart_screenshot_path"] = str(exported["png_path"])
                scored["chart_screenshot_stats"] = exported["stats"]
                return Path(exported["png_path"])
            except Exception as exc:
                self._record_error("chart_screenshot", scored.get("symbol", "-"), str(exc), critical=False)

        existing = scored.get("chart_path")
        if existing and Path(str(existing)).exists():
            return Path(str(existing))

        assert self.ctx.loader is not None
        pattern_result = scored.get("pattern_result")
        if pattern_result is None:
            return None
        symbol = str(scored.get("symbol", "")).upper()
        try:
            frame = self.ctx.loader.get_stock_daily(symbol)
            fallback = generate_pattern_chart(
                frame,
                symbol,
                pattern_result,
                all_patterns=scored.get("all_patterns"),
                pivot=scored.get("pivot"),
                target=scored.get("target"),
                stop_loss=scored.get("stop_loss"),
                conviction=scored.get("score"),
                output_dir=settings.CHARTS_DIR,
            )
            scored["chart_path"] = str(fallback)
            return fallback
        except Exception as exc:
            self._record_error("chart_fallback", symbol, str(exc), critical=False)
            return None

    def _run_weekly_incremental(self, *, full: bool = False) -> None:
        assert self.ctx.loader is not None
        try:
            stats = _generate_weekly_incremental(self.ctx.loader.conn, full=full)
            key = "weekly_rebuild" if full else "weekly_incremental"
            self.ctx.stats[key] = stats
        except Exception as exc:
            self._record_error("weekly_incremental", "-", str(exc), critical=False)

    def _record_local_data_status(self, *, reason: str = "local status check") -> None:
        if self.ctx.loader is None:
            return
        summary = local_data_status(self.ctx.loader.conn, self.ctx.symbols)
        sources = ", ".join(
            f"{source}={count}" for source, count in sorted(summary.source_counts.items())
        ) or "none"
        self._log_data(
            f"Local DB status ({reason}): target={summary.target_date}, "
            f"data_as_of={summary.data_as_of or 'none'}, min_data_as_of={summary.min_data_as_of or 'none'}, "
            f"current={summary.symbols_current}, stale={summary.symbols_stale}, sources={sources}."
        )
        current = dict(self.ctx.stats.get("data_status") or {})
        updated = summary.to_dict()
        if current:
            source_counts = dict(current.get("source_counts") or {})
            for source, count in updated.get("source_counts", {}).items():
                source_counts[source] = max(int(source_counts.get(source, 0) or 0), int(count or 0))
            updated["source_counts"] = source_counts
            updated["warnings"] = list(dict.fromkeys([*current.get("warnings", []), *updated.get("warnings", [])]))
            updated["rows_written"] = int(current.get("rows_written") or 0)
            updated["missing_days"] = current.get("missing_days", [])
            updated["missing_days_count"] = int(current.get("missing_days_count") or 0)
            updated["caught_up_days"] = current.get("caught_up_days", [])
            updated["caught_up_days_count"] = int(current.get("caught_up_days_count") or 0)
        self.ctx.stats["data_status"] = updated

    def _exclude_stale_symbols_from_scan(self) -> None:
        data_status = self.ctx.stats.get("data_status") or {}
        stale_symbols = {
            str(symbol).strip().upper()
            for symbol in data_status.get("stale_symbols", [])
            if str(symbol).strip()
        }
        if not stale_symbols:
            return
        before = len(self.ctx.symbols)
        self.ctx.symbols = [symbol for symbol in self.ctx.symbols if symbol not in stale_symbols]
        skipped = before - len(self.ctx.symbols)
        if skipped <= 0:
            return
        self.ctx.stats["symbols_skipped_stale"] = skipped
        self.ctx.stats["stale_symbols_skipped"] = sorted(stale_symbols)[:20]
        if self.ctx.selected_profile is not None and "symbol" in self.ctx.selected_profile.columns:
            profile = self.ctx.selected_profile.copy()
            symbols = profile["symbol"].astype(str).str.strip().str.upper()
            self.ctx.selected_profile = profile.loc[~symbols.isin(stale_symbols)].reset_index(drop=True)
        target = str(data_status.get("target_date") or "latest completed EOD")
        self._log_data(f"Skipping {skipped} stale symbol(s) before detection; no official EOD row for {target}.")
        self._record_error(
            "data_status",
            "-",
            f"Skipped {skipped} stale symbol(s) before detection; no official EOD row for {target}.",
            critical=False,
        )
        if not self.ctx.symbols:
            raise PipelineError(f"No current symbols left to scan after stale-data filter for {target}.")

    def _alert_already_sent(self, scored: dict, signal_date: str) -> bool:
        conn = getattr(self.ctx.loader, "conn", None) if self.ctx.loader is not None else None
        if conn is None or not signal_date:
            return False
        storage.ensure_schema(conn)
        return storage.alert_was_sent(
            conn,
            str(scored.get("symbol") or ""),
            str(scored.get("pattern") or "Pattern"),
            signal_date,
        )

    def _record_alert_sent(self, scored: dict, signal_date: str) -> None:
        conn = getattr(self.ctx.loader, "conn", None) if self.ctx.loader is not None else None
        if conn is None or not signal_date:
            return
        storage.ensure_schema(conn)
        storage.record_alert_sent(
            conn,
            str(scored.get("symbol") or ""),
            str(scored.get("pattern") or "Pattern"),
            signal_date,
            datetime.now().isoformat(timespec="seconds"),
        )

    def _record_current_signals(self, generated_at: datetime) -> None:
        conn = getattr(self.ctx.loader, "conn", None) if self.ctx.loader is not None else None
        if conn is None:
            return
        if self.ctx.dry_run:
            self.ctx.stats["signals_recorded"] = 0
            return
        storage.ensure_schema(conn)
        data_status = self.ctx.stats.get("data_status") or {}
        recorded = signal_tracker.record_current_signals(
            conn,
            self.ctx.scored_results,
            generated_at=generated_at,
            data_as_of=data_status.get("data_as_of"),
        )
        self.ctx.stats["signals_recorded"] = recorded

    def _build_signal_tracker(self, generated_at: datetime) -> list[dict]:
        conn = getattr(self.ctx.loader, "conn", None) if self.ctx.loader is not None else None
        if conn is None:
            return []
        storage.ensure_schema(conn)
        data_status = self.ctx.stats.get("data_status") or {}
        return signal_tracker.build_tracker(
            conn,
            self.ctx.scored_results,
            generated_at=generated_at,
            data_as_of=data_status.get("data_as_of"),
        )

    def _record_error(self, stage_name: str, symbol: str, message: str, *, critical: bool) -> None:
        self.ctx.errors.append(
            {
                "stage": stage_name,
                "symbol": symbol,
                "message": message,
                "critical": critical,
            }
        )


def _detect_symbol(
    symbol: str,
    daily: dict,
    weekly: dict | None = None,
    universe_name: str = "nifty500",
    scan_timeframe: str = "daily",
) -> dict:
    from patterns import get_detectors_for_universe

    hits: list[PatternResult] = []
    errors: list[str] = []
    for detector in get_detectors_for_universe(
        universe_name,
        symbol=symbol,
        scan_timeframe=scan_timeframe,
    ):
        try:
            found = detector(daily, weekly or {})
            if found:
                hits.extend(found)
        except Exception as exc:
            errors.append(f"{detector.__module__}.{detector.__name__}: {exc}")
    return {"symbol": symbol, "hits": hits, "errors": errors}


def _covered_symbol_count(conn, table_name: str, symbols: list[str]) -> int:
    if table_name not in {"ohlcv_daily", "ohlcv_weekly"}:
        raise ValueError(f"Unsupported OHLCV table: {table_name}")
    covered: set[str] = set()
    for chunk in _symbol_chunks([str(symbol).upper() for symbol in symbols], 800):
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT DISTINCT symbol FROM {table_name} WHERE close > 0 AND symbol IN ({placeholders})",
            chunk,
        ).fetchall()
        covered.update(str(row[0]).upper() for row in rows)
    return len(covered)


def _symbol_chunks(symbols: list[str], size: int) -> Iterable[list[str]]:
    for idx in range(0, len(symbols), size):
        yield symbols[idx : idx + size]


def _liquidity_map(profile: pd.DataFrame | None) -> dict[str, dict]:
    if profile is None or profile.empty or "liquidity_pass" not in profile.columns:
        return {}
    mapped: dict[str, dict] = {}
    for row in profile.fillna("").to_dict("records"):
        symbol = str(row.get("symbol", "")).upper()
        if not symbol:
            continue
        mapped[symbol] = {
            "liquidity_pass": _truthy(row.get("liquidity_pass")),
            "risk_tier": str(row.get("risk_tier", "")),
            "liquidity_reason": str(row.get("liquidity_reason", "")),
            "avg_volume_50d": row.get("avg_volume_50d"),
            "avg_traded_value_50d": row.get("avg_traded_value_50d"),
        }
    return mapped


def _apply_liquidity(scored: dict, profile: dict | None, min_liquidity: bool) -> None:
    if not profile:
        scored["liquidity_pass"] = None
        return
    scored.update(profile)
    if min_liquidity and not bool(profile.get("liquidity_pass")):
        scored["tradable"] = False
        scored["skip_reason"] = "LIQUIDITY_FAIL:" + str(profile.get("liquidity_reason", ""))


def _liquidity_allows_alert(scored: dict) -> bool:
    value = scored.get("liquidity_pass")
    if value is None:
        return True
    return bool(value)


def _company_name_map(profile: pd.DataFrame | None) -> dict[str, str]:
    if profile is None or profile.empty or "company_name" not in profile.columns:
        return {}
    return {
        str(row.symbol).upper(): str(row.company_name or row.symbol)
        for row in profile[["symbol", "company_name"]].itertuples(index=False)
    }


def _latest_close(daily: dict) -> float | None:
    close = daily.get("close")
    if close is None or len(close) == 0:
        return None
    return round(float(close[-1]), 2)


def _latest_date(daily: dict) -> str:
    dates = daily.get("date")
    if dates is None or len(dates) == 0:
        return ""
    return str(pd.to_datetime(dates[-1]).date())


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _is_bear_regime(regime: dict) -> bool:
    return str(regime.get("verdict", "")).upper() == "BEAR"


def _refresh_index_today(conn) -> None:
    """Fetch today's NIFTY 50 and sector index closes into index_daily."""
    from engine import dhan_client, storage

    today = date.today().isoformat()
    index_names = ["NIFTY 50", *settings.SECTOR_INDICES]
    try:
        imaster = dhan_client.index_master()
    except Exception:
        return
    for index_name in index_names:
        try:
            security_id = dhan_client.resolve_index_security_id(index_name, imaster)
            if not security_id:
                continue
            frame = dhan_client.fetch_historical_sync(
                security_id=security_id,
                exchange_segment="IDX_I",
                instrument="INDEX",
                from_date=today,
                to_date=today,
            )
            if not frame.empty:
                storage.upsert_index_rows(conn, index_name, frame)
        except Exception:
            pass


def _generate_weekly_incremental(conn, *, full: bool = False) -> dict[str, int]:
    module = _load_setup_module("03_generate_weekly.py", "pattern_finder_generate_weekly")
    return module.generate_weekly_incremental(conn, full=full)


def _run_rebalance_check() -> dict:
    module = _load_setup_module("07_rebalance_check.py", "pattern_finder_rebalance_check")
    return module.check_rebalance(apply_history=True)


def _run_refresh_universe() -> dict:
    build_module = _load_setup_module("08_build_universe.py", "pattern_finder_build_universe")
    profiles_module = _load_setup_module("09_build_profiles.py", "pattern_finder_build_profiles")
    result = build_module.run(force_master_refresh=True)
    profiles_module.run_watchlist()
    try:
        liquidity_result = profiles_module.run_small_mid_liquid()
    except Exception as exc:
        liquidity_result = {"status": "failed", "error": str(exc)}
    return {"universe": result, "small_mid_liquid": liquidity_result}


def _load_setup_module(filename: str, module_name: str):
    path = settings.BASE_DIR / "setup" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load setup module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _maybe_print_rebalance_reminder(today: date, universe_name: str) -> None:
    if universe.normalise_profile_name(universe_name) != "nifty500":
        return
    if today.month in {3, 6, 9, 12}:
        print(
            "Reminder: this is a quarterly Nifty 500 review month. "
            "Run python scanner.py --check-rebalance when you want to refresh the profile."
        )


def _validate_live_fetch_scope(universe_name: str, *, skip_fetch: bool, limit: int | None) -> None:
    if skip_fetch:
        return
    try:
        dhan_client.raise_if_rate_limited()
    except dhan_client.DhanRateLimitError as exc:
        raise PipelineError(str(exc)) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the NSE pattern scanner pipeline.")
    parser.add_argument("--universe", default="nifty500", help="Universe profile to scan.")
    parser.add_argument(
        "--scan-timeframe",
        choices=("daily", "weekly", "all"),
        default="daily",
        help="Detector set to run. Daily is the existing default; weekly adds weekly price-action detectors.",
    )
    parser.add_argument("--skip-fetch", action="store_true", help="Do not fetch today's OHLC from Dhan.")
    parser.add_argument(
        "--no-fetch-missing",
        action="store_true",
        help="Skip the pre-scan EOD catch-up stage. By default the scanner fills "
        "missing completed daily candles from bhavcopy/yfinance before scanning.",
    )
    parser.add_argument("--stage", choices=STAGE_ORDER, default=None, help="Run through this stage and stop.")
    parser.add_argument("--dry-run", action="store_true", help="No Dhan fetch and no Telegram sends.")
    parser.add_argument("--workers", type=int, default=settings.PROCESS_WORKERS)
    parser.add_argument("--fetch-all-data", action="store_true", help="Use all_nse_equity as the scan universe.")
    parser.add_argument("--check-rebalance", action="store_true", help="Run Nifty 500 rebalance check, then exit.")
    parser.add_argument("--refresh-universe", action="store_true", help="Refresh broad NSE universe/profile files, then exit.")
    parser.add_argument("--min-liquidity", action="store_true", help="Require liquidity_pass for tradable alerts.")
    parser.add_argument("--limit", type=int, default=None, help="Limit selected symbols after profile load.")
    parser.add_argument("--output", type=Path, default=None, help="Dashboard HTML output path.")
    parser.add_argument("--no-telegram", action="store_true", help="Disable Telegram sends.")
    parser.add_argument("--timeout", type=int, default=settings.STOCK_TIMEOUT_SECONDS, help="Detector timeout seconds.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.refresh_universe:
        _run_refresh_universe()
        return 0
    if args.check_rebalance:
        _run_rebalance_check()
        return 0

    universe_name = args.universe
    if args.fetch_all_data and universe_name == "nifty500":
        universe_name = "all_nse_equity"
    dry_run = bool(args.dry_run)
    _maybe_print_rebalance_reminder(date.today(), universe_name)
    skip_fetch = bool(args.skip_fetch or dry_run)
    try:
        _validate_live_fetch_scope(universe_name, skip_fetch=skip_fetch, limit=args.limit)
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    ctx = PipelineContext(
        universe_name=universe_name,
        scan_timeframe=args.scan_timeframe,
        dry_run=dry_run,
        skip_fetch=skip_fetch,
        fetch_missing=not bool(args.no_fetch_missing),
        stage=args.stage,
        workers=args.workers,
        stock_timeout_seconds=args.timeout,
        fetch_all_data=args.fetch_all_data,
        check_rebalance=args.check_rebalance,
        refresh_universe=args.refresh_universe,
        min_liquidity=args.min_liquidity,
        limit=args.limit,
        output_path=args.output,
        send_telegram=not args.no_telegram and not dry_run,
    )
    pipeline = Pipeline(ctx)
    try:
        pipeline.run()
    finally:
        pipeline.close()

    print(f"Universe: {ctx.universe_name}")
    print(f"Scan timeframe: {ctx.scan_timeframe}")
    print(f"Symbols selected: {len(ctx.symbols)}")
    print(f"Pattern hits: {len(ctx.raw_hits)}")
    print(f"Scored results: {len(ctx.scored_results)}")
    data_status = ctx.stats.get("data_status") or {}
    if data_status:
        print(f"Data as of: {data_status.get('data_as_of') or 'unknown'}")
        source_counts = data_status.get("source_counts") or {}
        if source_counts:
            print("Data sources: " + ", ".join(f"{key}={value}" for key, value in sorted(source_counts.items())))
        print(f"Rows updated: {data_status.get('rows_written', 0)}")
        print(
            "Catch-up days: "
            f"{data_status.get('caught_up_days_count', 0)}/"
            f"{data_status.get('missing_days_count', 0)}"
        )
    fetch_status = ctx.stats.get("fetch")
    if isinstance(fetch_status, dict):
        if fetch_status.get("status"):
            print(
                "Dhan fetch: "
                f"{fetch_status.get('status')}"
                + (f" -> {fetch_status.get('fallback')}" if fetch_status.get("fallback") else "")
            )
        elif "rows_written" in fetch_status:
            print(f"Dhan fetch rows: {fetch_status.get('rows_written', 0)}")
    elif fetch_status:
        print(f"Dhan fetch: {fetch_status}")
    print(f"Errors: {len(ctx.errors)}")
    if ctx.dashboard_path:
        print(f"Dashboard: {ctx.dashboard_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
