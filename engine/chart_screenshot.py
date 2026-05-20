"""Headless browser screenshot export for thesis chart HTML."""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_WIDTH = 1600
DEFAULT_HEIGHT = 1000
DEFAULT_MIN_COLORED_PIXELS = 100


def export_chart_screenshot(
    html_path: Path,
    output_path: Path,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    chrome_path: Path | None = None,
    min_colored_pixels: int = DEFAULT_MIN_COLORED_PIXELS,
) -> dict[str, int]:
    """Render ``html_path`` in headless Chromium and write a verified PNG."""

    html_path = Path(html_path)
    output_path = Path(output_path)
    if not html_path.exists():
        raise FileNotFoundError(f"chart HTML not found: {html_path}")
    executable_path = chrome_path or find_chrome_executable()
    if executable_path is None:
        raise RuntimeError("Chrome or Edge executable not found for chart screenshot export")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright is required for chart screenshot export") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(executable_path),
        )
        try:
            page = browser.new_page(
                viewport={"width": int(width), "height": int(height)},
                device_scale_factor=1,
            )
            page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            page.wait_for_selector('[data-chart-ready="1"]', timeout=10_000)
            page.wait_for_timeout(500)
            stats = _canvas_stats(page)
            if stats["colored_pixels"] < int(min_colored_pixels):
                raise RuntimeError(
                    "chart canvas appears blank: "
                    f"{stats['colored_pixels']} colored sampled pixels"
                )
            page.screenshot(path=str(output_path), full_page=True)
        finally:
            browser.close()

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError(f"screenshot was not written: {output_path}")
    stats["screenshot_bytes"] = output_path.stat().st_size
    return stats


def find_chrome_executable() -> Path | None:
    env_path = os.getenv("CHROME_PATH", "").strip()
    candidates = [
        Path(env_path) if env_path else None,
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def _canvas_stats(page) -> dict[str, int]:
    return page.evaluate(
        """
        () => {
          const canvases = Array.from(document.querySelectorAll('canvas'));
          let colored = 0;
          let sampled = 0;
          for (const canvas of canvases) {
            const ctx = canvas.getContext('2d');
            if (!ctx) continue;
            const w = canvas.width;
            const h = canvas.height;
            if (!w || !h) continue;
            const stepX = Math.max(1, Math.floor(w / 160));
            const stepY = Math.max(1, Math.floor(h / 100));
            const data = ctx.getImageData(0, 0, w, h).data;
            for (let y = 0; y < h; y += stepY) {
              for (let x = 0; x < w; x += stepX) {
                const idx = (y * w + x) * 4;
                const r = data[idx];
                const g = data[idx + 1];
                const b = data[idx + 2];
                const a = data[idx + 3];
                if (a === 0) continue;
                sampled += 1;
                if ((Math.max(r, g, b) - Math.min(r, g, b)) > 18 || r < 235 || g < 235 || b < 235) {
                  colored += 1;
                }
              }
            }
          }
          return { canvas_count: canvases.length, sampled_pixels: sampled, colored_pixels: colored };
        }
        """
    )
