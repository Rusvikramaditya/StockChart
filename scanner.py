"""Daily NSE pattern scanner pipeline."""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from config import settings
from engine import dashboard, storage, telegram, universe
from engine.chart_gen import generate_pattern_chart
from engine.chart_payload import build_chart_payload
from engine.data_loader import DataLoader
from engine.dedup import deduplicate_results
from engine.explainer import attach_explanation
from engine.scorer import score_pattern
from engine.thesis_chart import export_thesis_chart_png
from filters.market_regime import compute_market_regime
from filters.sector_rs import compute_sector_rs_cache
from patterns.base import PatternResult


STAGE_ORDER = ("verify", "fetch", "pre_compute", "detect", "filter_and_score", "output")


@dataclass
class PipelineContext:
    universe_name: str = "nifty500"
    universe_path: Path | None = None
    scan_date: date = field(default_factory=date.today)
    dry_run: bool = False
    skip_fetch: bool = False
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
    daily_arrays: dict[str, dict] = field(default_factory=dict)
    weekly_arrays: dict[str, dict] = field(default_factory=dict)
    raw_hits: list[dict] = field(default_factory=list)
    scored_results: list[dict] = field(default_factory=list)
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

    @stage("fetch")
    def fetch(self) -> None:
        if self.ctx.dry_run or self.ctx.skip_fetch:
            self.ctx.stats["fetch"] = "skipped"
            return
        if self.ctx.selected_profile is None:
            raise PipelineError("verify must run before fetch")
        assert self.ctx.loader is not None
        rows = self.ctx.loader.fetch_todays_candles(
            self.ctx.selected_profile,
            universe_name=self.ctx.universe_name,
        )
        self.ctx.stats["rows_fetched"] = rows
        if self.ctx.scan_date.weekday() == 4:
            self._run_weekly_incremental()

    @stage("pre_compute")
    def pre_compute(self) -> None:
        if not self.ctx.symbols:
            raise PipelineError("verify must run before pre_compute")
        assert self.ctx.loader is not None
        self.ctx.market_regime = compute_market_regime(self.ctx.loader, self.ctx.symbols)
        self.ctx.sector_rs_cache = compute_sector_rs_cache(self.ctx.loader, self.ctx.symbols)
        self.ctx.liquidity_profile = _liquidity_map(self.ctx.selected_profile)
        self.ctx.stats["bear_early_exit"] = _is_bear_regime(self.ctx.market_regime)

    @stage("detect")
    def detect(self) -> None:
        if _is_bear_regime(self.ctx.market_regime):
            self.ctx.raw_hits = []
            self.ctx.stats["detect"] = "skipped_bear_regime"
            return

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
                _detect_symbol(symbol, daily, weekly, self.ctx.universe_name)
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
                scored["company_name"] = company_names.get(symbol, symbol)
                _apply_liquidity(scored, self.ctx.liquidity_profile.get(symbol), self.ctx.min_liquidity)
                self._attach_chart_payload(scored)
                scored_results.append(attach_explanation(scored))
            except Exception as exc:
                self._record_error("filter_and_score", symbol, str(exc), critical=False)

        self.ctx.scored_results = deduplicate_results(scored_results)
        self.ctx.stats["scored_results"] = len(self.ctx.scored_results)

    @stage("output")
    def output(self) -> None:
        if self.ctx.send_telegram and not self.ctx.dry_run:
            self.ctx.alerts_sent = self._send_alerts()
            summary_sent = telegram.send_daily_summary(
                self.ctx.market_regime,
                self.ctx.scored_results,
                stocks_scanned=len(self.ctx.symbols),
                total_alerts=self.ctx.alerts_sent,
            )
            self.ctx.stats["telegram_summary_sent"] = summary_sent
            if not summary_sent:
                self._record_error(
                    "telegram",
                    "-",
                    "Daily summary was not sent; check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID",
                    critical=False,
                )

        output_context = {
            "generated_at": datetime.now(),
            "duration_seconds": sum(self.ctx.stage_timings.values()),
            "stocks_scanned": len(self.ctx.symbols),
            "market_regime": self.ctx.market_regime,
            "sector_rs": self.ctx.sector_rs_cache,
            "results": self.ctx.scored_results,
            "errors": self.ctx.errors,
            "alerts_sent": self.ctx.alerts_sent,
        }
        self.ctx.dashboard_path = dashboard.write_dashboard(output_context, self.ctx.output_path)
        self.ctx.stats["dashboard_path"] = str(self.ctx.dashboard_path)

    def close(self) -> None:
        if self.ctx.loader is not None:
            self.ctx.loader.close()

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
                executor.submit(_detect_symbol, symbol, daily, weekly, self.ctx.universe_name): symbol
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
                timeframe=timeframe.title(),
            )
        except Exception as exc:
            self._record_error("chart_payload", symbol, str(exc), critical=False)

    def _send_alerts(self) -> int:
        sent = 0
        for scored in self.ctx.scored_results:
            if not telegram.should_send_alert(scored):
                continue
            if not _liquidity_allows_alert(scored):
                continue
            chart_path = self._alert_chart_path(scored)
            ok = False
            if chart_path and Path(str(chart_path)).exists():
                ok = telegram.send_chart_alert(scored, chart_path)
            if not ok:
                ok = telegram.send_alert(telegram.format_alert(scored))
            sent += int(ok)
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

    def _run_weekly_incremental(self) -> None:
        assert self.ctx.loader is not None
        try:
            stats = _generate_weekly_incremental(self.ctx.loader.conn)
            self.ctx.stats["weekly_incremental"] = stats
        except Exception as exc:
            self._record_error("weekly_incremental", "-", str(exc), critical=False)

    def _record_error(self, stage_name: str, symbol: str, message: str, *, critical: bool) -> None:
        self.ctx.errors.append(
            {
                "stage": stage_name,
                "symbol": symbol,
                "message": message,
                "critical": critical,
            }
        )


def _detect_symbol(symbol: str, daily: dict, weekly: dict | None = None, universe_name: str = "nifty500") -> dict:
    from patterns import get_detectors_for_universe

    hits: list[PatternResult] = []
    errors: list[str] = []
    for detector in get_detectors_for_universe(universe_name):
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


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _is_bear_regime(regime: dict) -> bool:
    return str(regime.get("verdict", "")).upper() == "BEAR"


def _generate_weekly_incremental(conn) -> dict[str, int]:
    module = _load_setup_module("03_generate_weekly.py", "pattern_finder_generate_weekly")
    return module.generate_weekly_incremental(conn, full=False)


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the NSE pattern scanner pipeline.")
    parser.add_argument("--universe", default="nifty500", help="Universe profile to scan.")
    parser.add_argument("--skip-fetch", action="store_true", help="Do not fetch today's OHLC from Dhan.")
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
    ctx = PipelineContext(
        universe_name=universe_name,
        dry_run=dry_run,
        skip_fetch=bool(args.skip_fetch or dry_run),
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
    print(f"Symbols selected: {len(ctx.symbols)}")
    print(f"Pattern hits: {len(ctx.raw_hits)}")
    print(f"Scored results: {len(ctx.scored_results)}")
    print(f"Errors: {len(ctx.errors)}")
    if ctx.dashboard_path:
        print(f"Dashboard: {ctx.dashboard_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
