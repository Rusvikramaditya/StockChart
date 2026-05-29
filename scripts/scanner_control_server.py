"""Local control dashboard for launching scanner runs from a browser."""

from __future__ import annotations

import argparse
import json
import mimetypes
import subprocess
import sys
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "output"
CHARTS_DIR = OUTPUT_DIR / "charts"
DOCS_DIR = BASE_DIR / "docs"
STOCKSCANNER_ENV_PATH = BASE_DIR.parent / "StockScanner" / ".env"
UNIVERSES = ("nifty500", "all_nse_equity", "small_mid_liquid", "watchlist", "recent_listings")
MODES = ("safe", "fetch_no_telegram", "live_with_telegram")
DHAN_ENV_KEYS = ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "DHAN_PIN", "DHAN_TOTP_SECRET")
MAX_LIMIT = 5000
MAX_WORKERS = 16
RUN_LOCK = threading.Lock()


def build_scan_command(form: dict[str, list[str]], *, now: datetime | None = None) -> tuple[list[str], Path]:
    """Build a whitelisted scanner command from form data."""
    universe = _field(form, "universe", "nifty500")
    if universe not in UNIVERSES:
        raise ValueError(f"Unsupported universe: {universe}")

    mode = _field(form, "mode", "safe")
    if mode not in MODES:
        raise ValueError(f"Unsupported run mode: {mode}")

    workers = _int_field(form, "workers", default=8, minimum=1, maximum=MAX_WORKERS)
    limit_value = _optional_int_field(form, "limit", minimum=1, maximum=MAX_LIMIT)
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"control_{timestamp}_{universe}.html"

    command = [
        sys.executable,
        "scanner.py",
        "--universe",
        universe,
        "--workers",
        str(workers),
        "--output",
        str(output_path),
    ]
    if limit_value is not None:
        command.extend(["--limit", str(limit_value)])
    if _truthy(form, "min_liquidity"):
        command.append("--min-liquidity")
    # Default behavior: backfill missing/stale OHLCV before the scan. The form
    # checkbox is an opt-OUT so users can skip backfill when they know data is
    # already current (e.g., right after a manual setup/10 run).
    if _truthy(form, "skip_backfill"):
        command.append("--no-fetch-missing")

    if mode == "safe":
        command.extend(["--skip-fetch", "--dry-run", "--no-telegram"])
    elif mode == "fetch_no_telegram":
        command.append("--no-telegram")

    return command, output_path


def recent_reports(limit: int = 8) -> list[dict[str, str]]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(OUTPUT_DIR.glob("*.html"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]
    return [
        {
            "name": item.name,
            "href": f"/output/{item.name}",
            "modified": datetime.fromtimestamp(item.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }
        for item in files
    ]


def recent_charts(limit: int = 10) -> list[dict[str, str]]:
    if not CHARTS_DIR.exists():
        return []
    files: list[Path] = []
    for suffix in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        files.extend(CHARTS_DIR.rglob(suffix))
    files = sorted((item for item in files if item.is_file()), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]
    charts = []
    for item in files:
        rel = str(item.relative_to(OUTPUT_DIR)).replace("\\", "/")
        charts.append(
            {
                "name": rel,
                "href": f"/output/{rel}",
                "modified": datetime.fromtimestamp(item.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return charts


def summarize_run_failure(returncode: int, stdout: str, stderr: str) -> str:
    """Return a concise operator-facing failure message."""
    combined = f"{stdout}\n{stderr}".lower()
    if "dhan" in combined and ("429" in combined or "too many requests" in combined):
        return (
            "Dhan rate-limited the live fetch. Wait before starting another live run, or use Safe dry run / skip live "
            "fetch for broad universes."
        )
    if "dhan" in combined and ("401" in combined or "authentication failed" in combined or "token invalid" in combined):
        return (
            "Dhan authentication failed. This run used live fetch mode, so the scanner called Dhan and Dhan rejected "
            "the current Client ID/token. Switch Run mode to Safe dry run, or update DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in .env."
        )
    if "universe profile" in combined and "missing" in combined:
        return "The selected universe profile is missing. Pick another universe or rebuild the profile before scanning."
    if returncode != 0:
        return "Scanner failed. Check the technical log below for the exact traceback."
    return ""


def read_env_map(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def sync_dhan_env_from_stockscanner(
    *,
    source_path: Path = STOCKSCANNER_ENV_PATH,
    target_path: Path = BASE_DIR / ".env",
) -> list[str]:
    """Copy only Dhan auth keys from the sibling StockScanner .env."""
    source = read_env_map(source_path)
    missing = [key for key in DHAN_ENV_KEYS if not source.get(key)]
    if missing:
        raise ValueError(f"Missing StockScanner Dhan key(s): {', '.join(missing)}")

    lines = target_path.read_text(encoding="utf-8-sig").splitlines() if target_path.exists() else []
    for key in DHAN_ENV_KEYS:
        replacement = f"{key}={source[key]}"
        for index, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[index] = replacement
                break
        else:
            lines.append(replacement)

    target_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list(DHAN_ENV_KEYS)


def upsert_env_values(target_path: Path, values: dict[str, str]) -> None:
    lines = target_path.read_text(encoding="utf-8-sig").splitlines() if target_path.exists() else []
    for key, value in values.items():
        replacement = f"{key}={value}"
        for index, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[index] = replacement
                break
        else:
            lines.append(replacement)
    target_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_dhan_auth_check() -> dict[str, object]:
    """Run Dhan auth verification in a fresh process so .env changes are loaded."""
    code = r"""
import json
import requests
import pyotp
from config import settings
from engine import dhan_client

missing = [key for key, value in {
    "DHAN_CLIENT_ID": settings.DHAN_CLIENT_ID,
    "DHAN_PIN": settings.DHAN_PIN,
    "DHAN_TOTP_SECRET": settings.DHAN_TOTP_SECRET,
}.items() if not value]
if missing:
    print("FAIL: Missing " + ", ".join(missing))
    raise SystemExit(1)

otp = pyotp.TOTP(settings.DHAN_TOTP_SECRET).now()
url = (
    "https://auth.dhan.co/app/generateAccessToken"
    f"?dhanClientId={settings.DHAN_CLIENT_ID}&pin={settings.DHAN_PIN}&totp={otp}"
)
try:
    response = requests.post(url, timeout=30)
except Exception as exc:
    print("FAIL: Dhan auth request failed: " + exc.__class__.__name__ + ": " + str(exc))
    raise SystemExit(1)

body = response.text[:500]
try:
    payload = response.json()
except Exception:
    payload = {}

token = str(payload.get("accessToken") or "").strip()
if response.status_code == 200 and token:
    dhan_client._write_session_token_cache(token)
    print("OK: Dhan auth token refreshed and cached.")
    raise SystemExit(0)

print(f"FAIL: Dhan auth HTTP {response.status_code}: {body}")
raise SystemExit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "message": output or "No output from Dhan auth check.",
    }


def run_telegram_check() -> dict[str, object]:
    env = read_env_map(BASE_DIR / ".env")
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = env.get("TELEGRAM_CHAT_ID", "").strip()
    username = env.get("TELEGRAM_BOT_USERNAME", "").strip()
    if not token:
        return {"ok": False, "message": "TELEGRAM_BOT_TOKEN is missing in .env."}

    base = f"https://api.telegram.org/bot{token}"
    try:
        me = requests.get(base + "/getMe", timeout=20)
        payload = me.json()
    except Exception as exc:
        return {"ok": False, "message": f"Telegram getMe failed: {exc.__class__.__name__}: {exc}"}
    if not payload.get("ok"):
        return {"ok": False, "message": "Telegram rejected the bot token."}

    bot = payload.get("result", {}) or {}
    bot_username = "@" + str(bot.get("username") or username or "").lstrip("@")
    if not chat_id:
        return {
            "ok": False,
            "message": (
                f"Bot token works for {bot_username}, but TELEGRAM_CHAT_ID is empty. "
                "Open the bot in Telegram, press Start/send any message, then provide the numeric chat ID or rerun getUpdates."
            ),
        }

    try:
        chat = requests.get(base + "/getChat", params={"chat_id": chat_id}, timeout=20).json()
    except Exception as exc:
        return {"ok": False, "message": f"Telegram getChat failed: {exc.__class__.__name__}: {exc}"}
    if not chat.get("ok"):
        return {
            "ok": False,
            "message": f"Bot token works for {bot_username}, but Telegram rejected TELEGRAM_CHAT_ID.",
        }
    target = chat.get("result", {}).get("title") or chat.get("result", {}).get("username") or chat_id
    return {"ok": True, "message": f"Telegram bot and chat target verified. Bot: {bot_username}. Target: {target}."}


def resolve_telegram_chat_id() -> dict[str, object]:
    env = read_env_map(BASE_DIR / ".env")
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {"ok": False, "message": "TELEGRAM_BOT_TOKEN is missing in .env."}

    base = f"https://api.telegram.org/bot{token}"
    try:
        payload = requests.get(base + "/getUpdates", timeout=20).json()
    except Exception as exc:
        return {"ok": False, "message": f"Telegram getUpdates failed: {exc.__class__.__name__}: {exc}"}
    if not payload.get("ok"):
        return {"ok": False, "message": "Telegram getUpdates failed for this bot token."}

    chats = []
    for item in payload.get("result", []):
        msg = item.get("message") or item.get("channel_post") or item.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            chats.append(chat)
    if not chats:
        return {
            "ok": False,
            "message": "No Telegram chats found yet. Open @ChanakyaChartBot in Telegram, press Start/send any message, then click this again.",
        }

    chat = chats[-1]
    chat_id = str(chat["id"])
    upsert_env_values(BASE_DIR / ".env", {"TELEGRAM_CHAT_ID": chat_id})
    label = chat.get("title") or chat.get("username") or chat.get("first_name") or chat_id
    return {"ok": True, "message": f"Saved TELEGRAM_CHAT_ID for latest chat: {label}."}


def _field(form: dict[str, list[str]], key: str, default: str = "") -> str:
    values = form.get(key)
    if not values:
        return default
    return str(values[0]).strip() or default


def _truthy(form: dict[str, list[str]], key: str) -> bool:
    return _field(form, key).lower() in {"1", "true", "yes", "on"}


def _int_field(form: dict[str, list[str]], key: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = _field(form, key, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _optional_int_field(form: dict[str, list[str]], key: str, *, minimum: int, maximum: int) -> int | None:
    raw = _field(form, key)
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


class ControlHandler(BaseHTTPRequestHandler):
    server_version = "PatternFinderControl/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(_render_index())
            return
        if parsed.path == "/api/reports":
            self._send_json({"reports": recent_reports()})
            return
        if parsed.path == "/api/charts":
            self._send_json({"charts": recent_charts()})
            return
        if parsed.path.startswith("/output/"):
            self._serve_output(parsed.path)
            return
        if parsed.path.startswith("/docs/"):
            self._serve_docs(parsed.path)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/run":
            self._run_scanner()
            return
        if parsed.path == "/api/verify":
            self._run_verify()
            return
        if parsed.path == "/api/import-dhan":
            self._import_dhan()
            return
        if parsed.path == "/api/verify-dhan":
            self._verify_dhan()
            return
        if parsed.path == "/api/verify-telegram":
            self._verify_telegram()
            return
        if parsed.path == "/api/resolve-telegram-chat":
            self._resolve_telegram_chat()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), format % args))

    def _read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        return parse_qs(body, keep_blank_values=True)

    def _run_scanner(self) -> None:
        try:
            command, output_path = build_scan_command(self._read_form())
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if not RUN_LOCK.acquire(blocking=False):
            self._send_json({"ok": False, "error": "A scanner run is already in progress."}, status=HTTPStatus.CONFLICT)
            return

        try:
            started = datetime.now()
            result = subprocess.run(
                command,
                cwd=BASE_DIR,
                text=True,
                capture_output=True,
                timeout=3600,
                check=False,
            )
            elapsed = round((datetime.now() - started).total_seconds(), 1)
        finally:
            RUN_LOCK.release()

        failure_summary = summarize_run_failure(result.returncode, result.stdout, result.stderr)
        self._send_json(
            {
                "ok": result.returncode == 0,
                "returncode": result.returncode,
                "elapsed": elapsed,
                "error": failure_summary,
                "command": " ".join(str(item) for item in command),
                "stdout": result.stdout,
                "stderr": result.stderr,
                "dashboard": f"/output/{output_path.name}" if output_path.exists() else "",
                "reports": recent_reports(),
                "charts": recent_charts(),
            },
            status=HTTPStatus.OK if result.returncode == 0 else HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    def _run_verify(self) -> None:
        if not RUN_LOCK.acquire(blocking=False):
            self._send_json({"ok": False, "error": "A scanner run is already in progress."}, status=HTTPStatus.CONFLICT)
            return
        try:
            result = subprocess.run(
                [sys.executable, "setup\\06_verify_data.py"],
                cwd=BASE_DIR,
                text=True,
                capture_output=True,
                timeout=600,
                check=False,
            )
        finally:
            RUN_LOCK.release()
        self._send_json(
            {
                "ok": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            status=HTTPStatus.OK if result.returncode == 0 else HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    def _import_dhan(self) -> None:
        try:
            keys = sync_dhan_env_from_stockscanner()
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json(
            {
                "ok": True,
                "message": "Imported Dhan keys from StockScanner .env: " + ", ".join(keys),
            }
        )

    def _verify_dhan(self) -> None:
        result = run_dhan_auth_check()
        self._send_json(
            result,
            status=HTTPStatus.OK if result["ok"] else HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    def _verify_telegram(self) -> None:
        result = run_telegram_check()
        self._send_json(
            result,
            status=HTTPStatus.OK if result["ok"] else HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    def _resolve_telegram_chat(self) -> None:
        result = resolve_telegram_chat_id()
        self._send_json(
            result,
            status=HTTPStatus.OK if result["ok"] else HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    def _serve_output(self, path: str) -> None:
        name = unquote(path.removeprefix("/output/"))
        target = (OUTPUT_DIR / name).resolve()
        if OUTPUT_DIR.resolve() not in target.parents or not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_docs(self, path: str) -> None:
        name = unquote(path.removeprefix("/docs/"))
        target = (DOCS_DIR / name).resolve()
        if DOCS_DIR.resolve() not in target.parents or not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, html: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _render_index() -> str:
    report_links = "\n".join(
        f'<a class="report" href="{item["href"]}" target="_blank"><strong>{item["name"]}</strong><span>{item["modified"]}</span></a>'
        for item in recent_reports()
    )
    if not report_links:
        report_links = '<div class="empty">No dashboards have been generated yet.</div>'
    chart_links = "\n".join(
        f'<a class="report" href="{item["href"]}" target="_blank"><strong>{item["name"]}</strong><span>{item["modified"]}</span></a>'
        for item in recent_charts()
    )
    if not chart_links:
        chart_links = '<div class="empty">No generated chart PNGs yet. They appear only after a scan produces scored pattern hits.</div>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pattern Finder Control</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #080808;
      --panel: #151515;
      --panel-2: #101010;
      --line: #303030;
      --line-soft: #232323;
      --text: #f2f2f2;
      --muted: #a8a8a8;
      --accent: #ff4800;
      --green: #22c55e;
      --red: #ef4444;
      --yellow: #facc15;
      --shadow: rgba(0, 0, 0, 0.55);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Syne, "Segoe UI", Inter, Arial, sans-serif;
      letter-spacing: 0;
    }}
    button, input, select {{ font: inherit; letter-spacing: 0; }}
    .shell {{ width: min(1180px, 100%); margin: 0 auto; padding: 28px 20px 40px; }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: flex-start;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--line);
    }}
    .eyebrow {{ margin: 0 0 8px; color: var(--accent); font-size: 12px; font-weight: 800; text-transform: uppercase; }}
    h1 {{ margin: 0; font-size: 34px; line-height: 1.08; }}
    .subline {{ margin: 10px 0 0; color: var(--muted); max-width: 680px; }}
    .badge {{
      min-width: 230px;
      border: 1px solid var(--line);
      border-left: 4px solid var(--accent);
      border-radius: 8px;
      padding: 12px 14px;
      background: var(--panel);
      box-shadow: 0 18px 44px var(--shadow);
    }}
    .badge span {{ display: block; color: var(--muted); font-size: 12px; }}
    .badge strong {{ display: block; margin-top: 4px; font-size: 17px; }}
    .grid {{ display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(300px, 0.85fr); gap: 16px; margin-top: 20px; }}
    .side-stack {{ display: grid; gap: 16px; align-content: start; }}
    .panel {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 18px 44px var(--shadow);
      overflow: visible;
    }}
    .panel-head {{ padding: 15px 16px; border-bottom: 1px solid var(--line-soft); }}
    .panel-head h2 {{ margin: 0; font-size: 18px; }}
    form {{ display: grid; gap: 14px; padding: 16px; }}
    .form-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    label {{ display: grid; gap: 7px; color: var(--muted); font-size: 12px; font-weight: 800; text-transform: uppercase; }}
    .field-label {{ display: flex; align-items: center; gap: 7px; }}
    .field-note {{ color: var(--muted); font-size: 12px; font-weight: 600; line-height: 1.35; text-transform: none; }}
    .help {{
      position: relative;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      border: 1px solid var(--line);
      border-radius: 50%;
      background: var(--panel-2);
      color: var(--accent);
      font-size: 12px;
      font-weight: 900;
      cursor: help;
      text-transform: none;
      outline: 0;
    }}
    .help::after {{
      content: attr(data-tip);
      position: absolute;
      left: 50%;
      bottom: calc(100% + 10px);
      z-index: 40;
      width: min(320px, calc(100vw - 40px));
      padding: 10px 11px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #050505;
      color: var(--text);
      box-shadow: 0 16px 40px var(--shadow);
      font-size: 12px;
      font-weight: 700;
      line-height: 1.35;
      opacity: 0;
      pointer-events: none;
      text-align: left;
      transform: translate(-50%, 4px);
      transition: opacity 0.12s ease, transform 0.12s ease;
      white-space: normal;
    }}
    .help:hover::after,
    .help:focus::after {{ opacity: 1; transform: translate(-50%, 0); }}
    input, select {{
      width: 100%;
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px 10px;
      background: var(--panel-2);
      color: var(--text);
      outline: 0;
    }}
    input:focus, select:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px rgba(255, 72, 0, 0.16); }}
    .checks {{ display: flex; flex-wrap: wrap; gap: 10px; }}
    .check {{
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 38px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-2);
      color: var(--text);
      font-size: 13px;
      font-weight: 700;
      text-transform: none;
    }}
    .check input {{ width: auto; min-height: auto; }}
    .mode-warning {{
      display: none;
      margin-top: 8px;
      border: 1px solid rgba(250, 204, 21, 0.42);
      border-radius: 8px;
      padding: 10px 11px;
      background: rgba(250, 204, 21, 0.08);
      color: #f8e28a;
      font-size: 12px;
      font-weight: 700;
      line-height: 1.35;
      text-transform: none;
    }}
    .mode-warning.show {{ display: block; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; }}
    button {{
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px 13px;
      background: var(--panel-2);
      color: var(--text);
      cursor: pointer;
    }}
    button.primary {{ background: #1e120d; border-color: var(--accent); }}
    button:hover {{ border-color: var(--accent); }}
    button:disabled {{ color: #6f6f6f; cursor: wait; }}
    .status {{ padding: 16px; display: grid; gap: 10px; }}
    .status-line {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: var(--panel-2);
      color: var(--muted);
    }}
    .status-line.ok {{ color: var(--green); }}
    .status-line.fail {{ color: var(--red); }}
    pre {{
      min-height: 260px;
      max-height: 520px;
      overflow: auto;
      margin: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #050505;
      color: #d7d7d7;
      font-family: "JetBrains Mono", Consolas, monospace;
      font-size: 12px;
      white-space: pre-wrap;
    }}
    .reports {{ display: grid; gap: 8px; padding: 16px; }}
    .info-list {{ display: grid; gap: 10px; padding: 16px; }}
    .info-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px;
      background: var(--panel-2);
    }}
    .info-card strong {{ display: block; margin-bottom: 5px; color: var(--text); }}
    .info-card span {{ display: block; color: var(--muted); font-size: 13px; line-height: 1.4; }}
    .info-card .mono {{ display: inline; }}
    .mono {{ font-family: "JetBrains Mono", Consolas, monospace; }}
    .report {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      min-width: 0;
      padding: 11px;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--text);
      background: var(--panel-2);
      text-decoration: none;
    }}
    .report strong {{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .report span {{ flex: 0 0 auto; color: var(--muted); font-size: 12px; }}
    .empty {{ color: var(--muted); }}
    .dashboard-link {{ color: var(--green); font-weight: 800; }}
    @media (max-width: 820px) {{
      .topbar, .grid {{ grid-template-columns: 1fr; flex-direction: column; }}
      .form-grid {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 26px; }}
      .badge {{ width: 100%; }}
      .report {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">Local scanner control</p>
        <h1>Pattern Finder Control Dashboard</h1>
        <p class="subline">Select scanner parameters, run a safe local scan, then open the generated HTML dashboard from the same page.</p>
      </div>
      <div class="badge">
        <span>Default mode</span>
        <strong>Safe dry run</strong>
        <span>No Dhan fetch. No Telegram send.</span>
      </div>
    </header>

    <section class="grid">
      <div class="panel">
        <div class="panel-head"><h2>Run Scanner</h2></div>
        <form id="scanForm">
          <div class="form-grid">
            <label>
              <span class="field-label">Universe <span class="help" tabindex="0" data-tip="Which stock list to scan. Nifty 500 is stable and faster. All NSE equity is broad and slower. Watchlist scans only your configured names.">?</span></span>
              <select name="universe">
                <option value="nifty500">Nifty 500</option>
                <option value="all_nse_equity">All NSE equity</option>
                <option value="small_mid_liquid">Small/Mid liquid</option>
                <option value="watchlist">Watchlist</option>
                <option value="recent_listings">Recent listings</option>
              </select>
              <span class="field-note">Controls how many symbols enter the scanner.</span>
            </label>
            <label>
              <span class="field-label">Run mode <span class="help" tabindex="0" data-tip="Safe dry run uses existing local data and sends no Telegram. Live fetch updates today's data. Telegram mode can send real alerts if credentials are configured.">?</span></span>
              <select name="mode" id="runMode">
                <option value="safe">Safe dry run</option>
                <option value="fetch_no_telegram">Live fetch, no Telegram</option>
                <option value="live_with_telegram">Live fetch + Telegram</option>
              </select>
              <span class="field-note">Use safe dry run first unless you want live data fetch.</span>
              <span class="mode-warning" id="modeWarning">Live fetch calls Dhan. If DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN is expired or wrong, this will fail with HTTP 401. Use Safe dry run to scan existing local data.</span>
            </label>
            <label>
              <span class="field-label">Limit <span class="help" tabindex="0" data-tip="Optional cap on selected symbols. Use 1-25 to test quickly. Leave blank to scan the full selected universe.">?</span></span>
              <input name="limit" type="number" min="1" max="{MAX_LIMIT}" placeholder="Blank = full universe">
              <span class="field-note">Blank means full universe.</span>
            </label>
            <label>
              <span class="field-label">Workers <span class="help" tabindex="0" data-tip="Parallel scanner processes. Higher is faster but uses more CPU. 8 is the normal local default.">?</span></span>
              <input name="workers" type="number" min="1" max="{MAX_WORKERS}" value="8">
              <span class="field-note">CPU parallelism for detection.</span>
            </label>
          </div>
          <div class="checks">
            <label class="check"><input type="checkbox" name="min_liquidity"> <span>Require liquidity pass</span> <span class="help" tabindex="0" data-tip="Requires the scanner liquidity profile to pass before a setup is treated as tradable. It does not create or force patterns.">?</span></label>
            <label class="check"><input type="checkbox" name="skip_backfill"> <span>Skip historical backfill</span> <span class="help" tabindex="0" data-tip="By default the scanner backfills missing or stale daily OHLCV from Dhan before the scan, so detectors always see fresh data. Tick this only when you've already run the historical fetch script manually and want to save API quota.">?</span></label>
          </div>
          <div class="actions">
            <button type="submit" class="primary" id="runButton" title="Run scanner.py with the selected parameters.">Run scanner</button>
            <button type="button" id="verifyButton" title="Run setup\\06_verify_data.py and show the coverage table.">Verify data</button>
            <button type="button" id="importDhanButton" title="Copy Dhan auth keys from A:\\VibeCoding\\ProductIdeas\\StockScanner\\.env without displaying secrets.">Import StockScanner Dhan</button>
            <button type="button" id="verifyDhanButton" title="Verify Dhan PIN/TOTP auth and cache a refreshed token if Dhan accepts it.">Verify Dhan auth</button>
            <button type="button" id="verifyTelegramButton" title="Verify Telegram bot token and configured chat target without displaying secrets.">Verify Telegram</button>
            <button type="button" id="resolveTelegramButton" title="After you press Start in Telegram, save the latest chat ID from bot updates.">Resolve Telegram Chat ID</button>
          </div>
        </form>
        <div class="status">
          <div class="status-line" id="statusLine">Ready.</div>
          <pre id="logBox">Scanner output will appear here.</pre>
        </div>
      </div>

      <aside class="side-stack">
        <div class="panel">
          <div class="panel-head"><h2>Recent Dashboards</h2></div>
          <div class="reports" id="reports">{report_links}</div>
        </div>

        <div class="panel">
          <div class="panel-head"><h2>Where Charts Appear</h2></div>
          <div class="info-list">
            <div class="info-card">
              <strong>Generated dashboard</strong>
              <span>Open a recent dashboard. If the scan finds scored pattern hits, charts appear inside each result card under <span class="mono">Open thesis</span>.</span>
            </div>
            <div class="info-card">
              <strong>Chart PNG files</strong>
              <span>Per-stock chart screenshots are saved under <span class="mono">output\\charts</span> only when there are scored pattern hits. Zero-hit scans will not create chart PNGs.</span>
            </div>
            <a class="report" href="/docs/CHART_APPROVAL_GALLERY.html" target="_blank"><strong>Chart approval gallery</strong><span>Real detector samples</span></a>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head"><h2>Recent Chart PNGs</h2></div>
          <div class="reports" id="charts">{chart_links}</div>
        </div>
      </aside>
    </section>
  </main>

  <script>
    const form = document.getElementById("scanForm");
    const runButton = document.getElementById("runButton");
    const verifyButton = document.getElementById("verifyButton");
    const importDhanButton = document.getElementById("importDhanButton");
    const verifyDhanButton = document.getElementById("verifyDhanButton");
    const verifyTelegramButton = document.getElementById("verifyTelegramButton");
    const resolveTelegramButton = document.getElementById("resolveTelegramButton");
    const runMode = document.getElementById("runMode");
    const modeWarning = document.getElementById("modeWarning");
    const statusLine = document.getElementById("statusLine");
    const logBox = document.getElementById("logBox");
    const reports = document.getElementById("reports");
    const charts = document.getElementById("charts");

    function setBusy(isBusy) {{
      runButton.disabled = isBusy;
      verifyButton.disabled = isBusy;
      importDhanButton.disabled = isBusy;
      verifyDhanButton.disabled = isBusy;
      verifyTelegramButton.disabled = isBusy;
      resolveTelegramButton.disabled = isBusy;
    }}

    function setStatus(text, cls = "") {{
      statusLine.className = "status-line " + cls;
      statusLine.innerHTML = text;
    }}

    function updateModeWarning() {{
      if (!runMode || !modeWarning) return;
      modeWarning.classList.toggle("show", runMode.value !== "safe");
    }}

    function renderReports(items) {{
      if (!items || !items.length) {{
        reports.innerHTML = '<div class="empty">No dashboards have been generated yet.</div>';
        return;
      }}
      reports.innerHTML = items.map((item) =>
        `<a class="report" href="${{item.href}}" target="_blank"><strong>${{item.name}}</strong><span>${{item.modified}}</span></a>`
      ).join("");
    }}

    function renderCharts(items) {{
      if (!items || !items.length) {{
        charts.innerHTML = '<div class="empty">No generated chart PNGs yet. They appear only after a scan produces scored pattern hits.</div>';
        return;
      }}
      charts.innerHTML = items.map((item) =>
        `<a class="report" href="${{item.href}}" target="_blank"><strong>${{item.name}}</strong><span>${{item.modified}}</span></a>`
      ).join("");
    }}

    async function refreshReports() {{
      const response = await fetch("/api/reports");
      const payload = await response.json();
      renderReports(payload.reports);
    }}

    async function refreshCharts() {{
      const response = await fetch("/api/charts");
      const payload = await response.json();
      renderCharts(payload.charts);
    }}

    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      setBusy(true);
      setStatus("Running scanner...");
      logBox.textContent = "Waiting for scanner output...";
      try {{
        const response = await fetch("/api/run", {{ method: "POST", body: new URLSearchParams(new FormData(form)) }});
        const payload = await response.json();
        logBox.textContent = [
          payload.error ? "Summary:\\n" + payload.error + "\\n" : "",
          payload.command || "",
          "",
          payload.stdout || "",
          payload.stderr ? "\\nTechnical stderr:\\n" + payload.stderr : ""
        ].join("\\n");
        if (payload.ok && payload.dashboard) {{
          setStatus(`Done in ${{payload.elapsed}}s. <a class="dashboard-link" href="${{payload.dashboard}}" target="_blank">Open generated dashboard</a>`, "ok");
        }} else {{
          setStatus(payload.error || "Scanner failed. Check the log below.", "fail");
        }}
        renderReports(payload.reports);
        renderCharts(payload.charts);
      }} catch (error) {{
        setStatus("Control server request failed.", "fail");
        logBox.textContent = String(error);
      }} finally {{
        setBusy(false);
      }}
    }});

    verifyButton.addEventListener("click", async () => {{
      setBusy(true);
      setStatus("Verifying local data...");
      logBox.textContent = "Waiting for verification output...";
      try {{
        const response = await fetch("/api/verify", {{ method: "POST" }});
        const payload = await response.json();
        logBox.textContent = (payload.stdout || "") + (payload.stderr ? "\\nSTDERR:\\n" + payload.stderr : "");
        setStatus(payload.ok ? "Data verification passed." : "Data verification failed.", payload.ok ? "ok" : "fail");
      }} catch (error) {{
        setStatus("Verify request failed.", "fail");
        logBox.textContent = String(error);
      }} finally {{
        setBusy(false);
      }}
    }});

    importDhanButton.addEventListener("click", async () => {{
      setBusy(true);
      setStatus("Importing Dhan keys from StockScanner...");
      logBox.textContent = "Secrets will not be displayed.";
      try {{
        const response = await fetch("/api/import-dhan", {{ method: "POST" }});
        const payload = await response.json();
        if (payload.ok) {{
          setStatus("Dhan keys imported from StockScanner .env.", "ok");
          logBox.textContent = payload.message + "\\nNext: click Verify Dhan auth.";
        }} else {{
          setStatus(payload.error || "Dhan key import failed.", "fail");
          logBox.textContent = payload.error || "";
        }}
      }} catch (error) {{
        setStatus("Dhan key import request failed.", "fail");
        logBox.textContent = String(error);
      }} finally {{
        setBusy(false);
      }}
    }});

    verifyDhanButton.addEventListener("click", async () => {{
      setBusy(true);
      setStatus("Verifying Dhan auth...");
      logBox.textContent = "Checking DHAN_CLIENT_ID + DHAN_PIN + DHAN_TOTP_SECRET. No secrets will be displayed.";
      try {{
        const response = await fetch("/api/verify-dhan", {{ method: "POST" }});
        const payload = await response.json();
        setStatus(payload.ok ? "Dhan auth verified and token cached." : "Dhan auth failed.", payload.ok ? "ok" : "fail");
        logBox.textContent = payload.message || "";
      }} catch (error) {{
        setStatus("Dhan auth verify request failed.", "fail");
        logBox.textContent = String(error);
      }} finally {{
        setBusy(false);
      }}
    }});

    verifyTelegramButton.addEventListener("click", async () => {{
      setBusy(true);
      setStatus("Verifying Telegram...");
      logBox.textContent = "Checking bot token and TELEGRAM_CHAT_ID. Token will not be displayed.";
      try {{
        const response = await fetch("/api/verify-telegram", {{ method: "POST" }});
        const payload = await response.json();
        setStatus(payload.ok ? "Telegram verified." : "Telegram needs chat target setup.", payload.ok ? "ok" : "fail");
        logBox.textContent = payload.message || "";
      }} catch (error) {{
        setStatus("Telegram verify request failed.", "fail");
        logBox.textContent = String(error);
      }} finally {{
        setBusy(false);
      }}
    }});

    resolveTelegramButton.addEventListener("click", async () => {{
      setBusy(true);
      setStatus("Resolving Telegram chat ID...");
      logBox.textContent = "Open @ChanakyaChartBot in Telegram and press Start/send a message before using this.";
      try {{
        const response = await fetch("/api/resolve-telegram-chat", {{ method: "POST" }});
        const payload = await response.json();
        setStatus(payload.ok ? "Telegram chat ID saved." : "Telegram chat ID not found yet.", payload.ok ? "ok" : "fail");
        logBox.textContent = payload.message || "";
      }} catch (error) {{
        setStatus("Telegram chat resolve request failed.", "fail");
        logBox.textContent = String(error);
      }} finally {{
        setBusy(false);
      }}
    }});

    if (runMode) runMode.addEventListener("change", updateModeWarning);
    updateModeWarning();
    refreshReports();
    refreshCharts();
  </script>
</body>
</html>"""


def run_server(host: str, port: int) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), ControlHandler)
    print(f"Pattern Finder Control Dashboard: http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Pattern Finder control dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
