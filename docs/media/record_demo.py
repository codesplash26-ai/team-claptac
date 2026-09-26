"""Record docs/media/atlas-demo.gif from a running ATLAS (for the README).

    docker compose down -v && docker compose up -d --build     # a clean start
    pip install playwright pillow
    python docs/media/record_demo.py --url http://localhost:8000

Uses the Microsoft Edge or Chrome already installed (no browser download). Drives the real console:
the overview, a real flood drill, the 3D map tour, a human approval and the alert in Sinhala.
"""

from __future__ import annotations

import argparse
import io
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "atlas-demo.gif"
WIDTH = 960


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--channel", default="msedge", help="msedge or chrome")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    base = args.url.rstrip("/") + "/manager/"
    frames: list[tuple[Image.Image, int]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel, headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        def snap(hold_ms: int = 700) -> None:
            shot = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
            shot = shot.resize((WIDTH, round(shot.height * WIDTH / shot.width)), Image.LANCZOS)
            frames.append((shot, hold_ms))

        def go(hash_: str, wait_s: float = 2.0) -> None:
            page.goto(base + "#/" + hash_)
            time.sleep(wait_s)

        # 1. All quiet
        go("overview")
        snap(2200)

        # 2. Start the real Cyclone Ditwah flood drill
        go("practice")
        page.select_option("select", "60")
        snap(1500)
        page.locator(".drill", has_text="Cyclone Ditwah floods").get_by_role("button", name="Start this drill").click()
        time.sleep(1)
        snap(1200)

        # 3. The incident opens by itself
        go("overview", 1)
        for _ in range(12):
            snap(600)
            time.sleep(1.2)
        snap(1800)

        # 4. The map: guided tour over 3D terrain
        go("map", 7)
        snap(1800)
        page.get_by_role("button", name="▶ Tour").click()
        for _ in range(26):
            time.sleep(1.0)
            snap(500)

        # 5. A person approves the evacuation alert
        go("decisions")
        page.get_by_placeholder("e.g. District Officer Perera").fill("District Officer")
        snap(2200)
        page.get_by_role("button", name="✓ Approve").first.click()
        time.sleep(3)
        snap(1500)

        # 6. The alert, in Sinhala
        go("alerts")
        page.get_by_role("button", name="සිංහල").click()
        time.sleep(1)
        snap(3000)
        browser.close()

    images = [f.quantize(colors=160, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG) for f, _ in frames]
    images[0].save(
        args.out,
        save_all=True,
        append_images=images[1:],
        duration=[d for _, d in frames],
        loop=0,
        optimize=True,
        disposal=1,
    )
    size = Path(args.out).stat().st_size / 1e6
    print(f"{len(frames)} frames, {size:.1f} MB -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
