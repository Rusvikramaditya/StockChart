"""Phase 4 contract tests for Telegram alerts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from engine import telegram


class FakeResponse:
    def __init__(self, ok: bool = True, payload: dict | None = None):
        self.ok = ok
        self._payload = payload if payload is not None else {"ok": ok}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ConsumingSession(FakeSession):
    def __init__(self, responses):
        super().__init__(responses)
        self.positions = []

    def post(self, url, **kwargs):
        photo = kwargs["files"]["photo"]
        self.positions.append(photo.tell())
        photo.read()
        return super().post(url, **kwargs)


def sample_scored(score: int = 82) -> dict:
    if score >= 90:
        tier = "HIGHEST"
    elif score >= 70:
        tier = "HIGH"
    elif score >= 50:
        tier = "MEDIUM"
    else:
        tier = "SKIP"
    return {
        "symbol": "TEST",
        "pattern": "Ascending Triangle",
        "status": "BREAKING OUT",
        "pivot": 100.0,
        "target": 120.0,
        "stop_loss": 94.0,
        "score": score,
        "tier": tier,
        "tradable": True,
        "filters": {
            "stage2": {"passed": True, "status": "PASS", "details": {"close": 101.5}},
            "volume": {"passed": True, "status": "PASS", "details": {"breakout_volume_ratio": 1.8}},
            "sector_rs": {"passed": True, "status": "LEADING"},
            "market_regime": {"score": 3, "verdict": "CONFIRMED UPTREND"},
            "rsi": {"value": 64.0, "status": "HEALTHY"},
        },
    }


class TelegramPhase4Test(unittest.TestCase):
    def test_format_alert_contains_trade_and_filter_context(self):
        message = telegram.format_alert(sample_scored())

        self.assertIn("<b>TEST</b>", message)
        self.assertIn("Ascending Triangle", message)
        self.assertIn("Entry Rs.100", message)
        self.assertIn("Target Rs.120", message)
        self.assertIn("Stop Rs.94", message)
        self.assertIn("Conviction: <b>82/100</b>", message)
        self.assertIn("Stage2 \u2705", message)
        self.assertIn("Regime CONFIRMED UPTREND", message)

    def test_should_send_alert_enforces_threshold_and_tradable(self):
        self.assertTrue(telegram.should_send_alert(sample_scored(70)))
        self.assertFalse(telegram.should_send_alert(sample_scored(69)))

        skipped = sample_scored(90)
        skipped["tradable"] = False
        self.assertFalse(telegram.should_send_alert(skipped))

    def test_send_alert_uses_html_parse_mode_and_retries(self):
        session = FakeSession(
            [
                requests.Timeout("timeout"),
                FakeResponse(ok=False, payload={"ok": False}),
                FakeResponse(ok=True, payload={"ok": True}),
            ]
        )
        with patch("engine.telegram.time.sleep") as sleep:
            sent = telegram.send_alert(
                "hello",
                token="token",
                chat_id="chat",
                retry_count=2,
                sleep_seconds=0,
                session=session,
            )

        self.assertTrue(sent)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(sleep.call_count, 2)
        payload = session.calls[-1][1]["data"]
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertTrue(payload["disable_web_page_preview"])

    def test_send_alert_returns_false_when_config_missing(self):
        session = FakeSession([FakeResponse()])

        self.assertFalse(telegram.send_alert("hello", token=" ", chat_id=" ", session=session))
        self.assertEqual(session.calls, [])

    def test_send_chart_alert_posts_photo_and_trims_caption(self):
        session = FakeSession([FakeResponse(ok=True, payload={"ok": True})])
        with tempfile.TemporaryDirectory() as tmp:
            chart_path = Path(tmp) / "chart.png"
            chart_path.write_bytes(b"png")
            sent = telegram.send_chart_alert(
                sample_scored(),
                chart_path,
                token="token",
                chat_id="chat",
                caption="x" * 2000,
                session=session,
            )

        self.assertTrue(sent)
        url, kwargs = session.calls[0]
        self.assertTrue(url.endswith("/sendPhoto"))
        self.assertIn("photo", kwargs["files"])
        self.assertLessEqual(len(kwargs["data"]["caption"]), telegram.CAPTION_LIMIT)
        self.assertEqual(kwargs["data"]["parse_mode"], "HTML")

    def test_send_chart_alert_rewinds_photo_between_retries(self):
        session = ConsumingSession(
            [
                FakeResponse(ok=False, payload={"ok": False}),
                FakeResponse(ok=True, payload={"ok": True}),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            chart_path = Path(tmp) / "chart.png"
            chart_path.write_bytes(b"png-bytes")
            with patch("engine.telegram.time.sleep"):
                sent = telegram.send_chart_alert(
                    sample_scored(),
                    chart_path,
                    token="token",
                    chat_id="chat",
                    retry_count=1,
                    sleep_seconds=0,
                    session=session,
                )

        self.assertTrue(sent)
        self.assertEqual(session.positions, [0, 0])

    def test_daily_summary_counts_alerts_and_tiers(self):
        message = telegram.format_daily_summary(
            {"verdict": "CONFIRMED UPTREND", "score": 3},
            [sample_scored(90), sample_scored(70), sample_scored(55), sample_scored(20)],
            stocks_scanned=500,
        )

        self.assertIn("Market regime: <b>CONFIRMED UPTREND</b> (3/4)", message)
        self.assertIn("Stocks scanned: 500", message)
        self.assertIn("Pattern hits: 4", message)
        self.assertIn("HIGHEST 1", message)
        self.assertIn("HIGH 1", message)
        self.assertIn("MEDIUM 1", message)
        self.assertIn("SKIP 1", message)
        self.assertIn("Telegram alerts: 2", message)


if __name__ == "__main__":
    unittest.main()
