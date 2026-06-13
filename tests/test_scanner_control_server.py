"""Contract tests for the local scanner control dashboard."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import scripts.scanner_control_server as control
from scripts.scanner_control_server import build_scan_command


NOW = datetime(2026, 5, 20, 17, 45, 0)


def test_control_server_script_runs_when_invoked_by_path():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "scanner_control_server.py"

    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        cwd=script_path.parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "Run the local Pattern Finder control dashboard" in result.stdout


def test_safe_mode_builds_dry_run_command():
    command, output_path = build_scan_command(
        {
            "universe": ["nifty500"],
            "mode": ["safe"],
            "limit": ["25"],
            "workers": ["8"],
            "min_liquidity": ["on"],
        },
        now=NOW,
    )

    assert "scanner.py" in command
    assert command[command.index("--universe") + 1] == "nifty500"
    assert command[command.index("--scan-timeframe") + 1] == "daily"
    assert command[command.index("--limit") + 1] == "25"
    assert command[command.index("--workers") + 1] == "8"
    assert "--min-liquidity" in command
    assert "--skip-fetch" in command
    assert "--dry-run" in command
    assert "--no-telegram" in command
    assert output_path.name == "control_20260520_174500_nifty500.html"


def test_fetch_no_telegram_mode_keeps_fetch_enabled():
    command, _output_path = build_scan_command(
        {
            "universe": ["nifty500"],
            "mode": ["fetch_no_telegram"],
            "limit": [""],
            "workers": ["4"],
        },
        now=NOW,
    )

    assert "--skip-fetch" not in command
    assert "--dry-run" not in command
    assert "--no-telegram" in command
    assert "--limit" not in command


def test_full_all_nse_all_timeframes_skips_dhan_but_keeps_eod_catchup():
    command, _output_path = build_scan_command(
        {
            "universe": ["all_nse_equity"],
            "mode": ["fetch_no_telegram"],
            "scan_timeframe": ["all"],
            "limit": [""],
            "workers": ["4"],
        },
        now=NOW,
    )

    assert command[command.index("--universe") + 1] == "all_nse_equity"
    assert command[command.index("--scan-timeframe") + 1] == "all"
    assert "--limit" not in command
    assert "--skip-fetch" in command
    assert "--no-fetch-missing" not in command
    assert "--dry-run" not in command
    assert "--no-telegram" in command


def test_limited_all_nse_all_timeframes_keeps_requested_live_fetch():
    command, _output_path = build_scan_command(
        {
            "universe": ["all_nse_equity"],
            "mode": ["fetch_no_telegram"],
            "scan_timeframe": ["all"],
            "limit": ["25"],
            "workers": ["4"],
        },
        now=NOW,
    )

    assert command[command.index("--universe") + 1] == "all_nse_equity"
    assert command[command.index("--scan-timeframe") + 1] == "all"
    assert command[command.index("--limit") + 1] == "25"
    assert "--skip-fetch" not in command
    assert "--no-fetch-missing" not in command
    assert "--dry-run" not in command
    assert "--no-telegram" in command


def test_live_with_telegram_mode_does_not_force_safety_flags():
    command, _output_path = build_scan_command(
        {
            "universe": ["watchlist"],
            "mode": ["live_with_telegram"],
            "workers": ["2"],
        },
        now=NOW,
    )

    assert "--skip-fetch" not in command
    assert "--dry-run" not in command
    assert "--no-telegram" not in command


def test_weekly_scan_timeframe_is_whitelisted_and_names_output():
    command, output_path = build_scan_command(
        {
            "universe": ["watchlist"],
            "mode": ["safe"],
            "workers": ["2"],
            "scan_timeframe": ["weekly"],
        },
        now=NOW,
    )

    assert command[command.index("--scan-timeframe") + 1] == "weekly"
    assert output_path.name == "control_20260520_174500_watchlist_weekly.html"


@pytest.mark.parametrize(
    "form",
    [
        {"universe": ["bad"], "mode": ["safe"], "workers": ["8"]},
        {"universe": ["nifty500"], "mode": ["bad"], "workers": ["8"]},
        {"universe": ["nifty500"], "mode": ["safe"], "workers": ["0"]},
        {"universe": ["nifty500"], "mode": ["safe"], "workers": ["99"]},
        {"universe": ["nifty500"], "mode": ["safe"], "workers": ["8"], "scan_timeframe": ["monthly"]},
        {"universe": ["nifty500"], "mode": ["safe"], "workers": ["8"], "limit": ["0"]},
    ],
)
def test_rejects_unsafe_or_out_of_range_inputs(form):
    with pytest.raises(ValueError):
        build_scan_command(form, now=NOW)


def test_control_page_explains_fields_and_chart_locations():
    html = control._render_index()

    assert "Where Charts Appear" in html
    assert "Recent Chart PNGs" in html
    assert "output\\charts" in html
    assert "data-tip=" in html
    assert "Live fetch may call Dhan" in html
    assert "Skip EOD catch-up" in html
    assert "Import StockScanner Dhan" in html
    assert "Verify Dhan auth" in html
    assert "Verify Telegram" in html
    assert "Resolve Telegram Chat ID" in html
    assert "Past Suggestions" in html
    assert "Which stock list to scan" in html
    assert "Scan timeframe" in html
    assert "Full All NSE + Daily + weekly skips Dhan live fetch" in html
    assert "Chart approval gallery" in html


def test_recent_charts_lists_output_chart_files(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    charts_dir = output_dir / "charts"
    charts_dir.mkdir(parents=True)
    chart_path = charts_dir / "SAMPLE_chart.png"
    chart_path.write_bytes(b"png")
    monkeypatch.setattr(control, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(control, "CHARTS_DIR", charts_dir)

    charts = control.recent_charts()

    assert charts == [
        {
            "name": "charts/SAMPLE_chart.png",
            "href": "/output/charts/SAMPLE_chart.png",
            "modified": charts[0]["modified"],
        }
    ]


def test_dhan_auth_failure_is_summarized_for_operator():
    stderr = (
        "engine.dhan_client.DhanError: Dhan batch OHLC HTTP 401: "
        '{"data":{"808":"Authentication Failed - Client ID or Token invalid"},"status":"failed"}'
    )

    message = control.summarize_run_failure(1, "", stderr)

    assert "Dhan authentication failed" in message
    assert "Safe dry run" in message
    assert "DHAN_CLIENT_ID" in message
    assert "DHAN_ACCESS_TOKEN" in message


def test_dhan_subscription_failure_is_summarized_separately():
    stderr = (
        "engine.dhan_client.DhanDataNotSubscribedError: Dhan batch OHLC HTTP 401: "
        '{"data":{"806":"Data APIs not Subscribed"},"status":"failed"}'
    )

    message = control.summarize_run_failure(1, "", stderr)

    assert "market-data APIs are not subscribed" in message
    assert "bhavcopy/yfinance" in message
    assert "authentication failed" not in message.lower()


def test_dhan_rate_limit_failure_is_summarized_for_operator():
    stderr = (
        "engine.dhan_client.DhanError: Dhan batch OHLC HTTP 429: "
        '{"data":{"805":"Too many requests. Further requests may result in the user being blocked."},"status":"failed"}'
    )

    message = control.summarize_run_failure(1, "", stderr)

    assert "Dhan rate-limited" in message
    assert "Wait" in message
    assert "Safe dry run" in message


def test_sync_dhan_env_from_stockscanner_copies_only_dhan_keys(tmp_path):
    source = tmp_path / "stock.env"
    target = tmp_path / "pattern.env"
    source.write_text(
        "\n".join(
            [
                "DHAN_CLIENT_ID=cid",
                "DHAN_ACCESS_TOKEN=token",
                "DHAN_PIN=pin",
                "DHAN_TOTP_SECRET=secret",
                "GEMINI_API_KEY=do_not_copy",
            ]
        ),
        encoding="utf-8",
    )
    target.write_text("TELEGRAM_BOT_TOKEN=telegram\nDHAN_CLIENT_ID=old\n", encoding="utf-8")

    copied = control.sync_dhan_env_from_stockscanner(source_path=source, target_path=target)
    values = control.read_env_map(target)

    assert copied == list(control.DHAN_ENV_KEYS)
    assert values["DHAN_CLIENT_ID"] == "cid"
    assert values["DHAN_ACCESS_TOKEN"] == "token"
    assert values["DHAN_PIN"] == "pin"
    assert values["DHAN_TOTP_SECRET"] == "secret"
    assert values["TELEGRAM_BOT_TOKEN"] == "telegram"
    assert "GEMINI_API_KEY" not in values


def test_telegram_check_reports_missing_chat_id(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_BOT_TOKEN=token\nTELEGRAM_BOT_USERNAME=@ChanakyaChartBot\nTELEGRAM_CHAT_ID=\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(control, "BASE_DIR", tmp_path)

    class Response:
        def json(self):
            return {"ok": True, "result": {"username": "ChanakyaChartBot"}}

    monkeypatch.setattr(control.requests, "get", lambda *args, **kwargs: Response())

    result = control.run_telegram_check()

    assert result["ok"] is False
    assert "Bot token works" in result["message"]
    assert "TELEGRAM_CHAT_ID is empty" in result["message"]


def test_resolve_telegram_chat_id_saves_latest_chat(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("TELEGRAM_BOT_TOKEN=token\nTELEGRAM_CHAT_ID=\n", encoding="utf-8")
    monkeypatch.setattr(control, "BASE_DIR", tmp_path)

    class Response:
        def json(self):
            return {
                "ok": True,
                "result": [
                    {"message": {"chat": {"id": 111, "first_name": "Old"}}},
                    {"message": {"chat": {"id": 222, "first_name": "Latest"}}},
                ],
            }

    monkeypatch.setattr(control.requests, "get", lambda *args, **kwargs: Response())

    result = control.resolve_telegram_chat_id()
    values = control.read_env_map(env_path)

    assert result["ok"] is True
    assert values["TELEGRAM_CHAT_ID"] == "222"
