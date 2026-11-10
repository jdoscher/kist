"""Record Kist's silent demo loop (README / docs site) from a running instance.

Read-only: it scrolls, opens an item and searches by colour; nothing is
added, tagged, moved or deleted. Run from the repository root:

    KIST_URL=https://kist.example.com KIST_TOKEN=... \
      uv run --no-project --with playwright python docs/demo/record.py

Writes docs/assets/demo/raw.webm, then (with ffmpeg on PATH or $FFMPEG)
docs/assets/demo/demo.gif (README and docs site; kept under 5 MB) and an
H.264 demo.mp4 OUTSIDE the repository, for upload to the GitHub release:
$KIST_DEMO_MP4, by default ../kist-release-assets/demo.mp4 next to the
clone. The MP4 is never committed. Re-record on each release so the loop
shows the current UI. Point it at the demo library from docs/demo/seed.py:
whatever is in the grid ends up in the video.

The script, scene by scene (about 18 s):
  1. the grid in the dark theme                      1.5 s
  2. tiles grow with the size slider, the grid scrolls 4.0 s
  3. click an item: the info panel with its palette   2.5 s
  4. open it full screen, close it                    2.5 s
  5. press a palette colour: search by colour         3.5 s
  6. open a board                                     2.5 s
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "assets" / "demo"
SIZE = dict(width=1280, height=800)
SAFE = os.environ.get("KIST_SHOT_FILTER", "")  # e.g. any=Architecture,Maps
ITEM_INDEX = int(os.environ.get("KIST_DEMO_ITEM", "0"))  # which tile to open
BOARD = os.environ.get("KIST_DEMO_BOARD", "")  # board name to open last (sidebar)
MP4 = Path(os.environ.get("KIST_DEMO_MP4") or ROOT.parent / "kist-release-assets" / "demo.mp4")


def record() -> Path:
    url = os.environ["KIST_URL"].rstrip("/")
    token = os.environ["KIST_TOKEN"]
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_video"
    shutil.rmtree(tmp, ignore_errors=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport=SIZE, color_scheme="dark", record_video_dir=str(tmp), record_video_size=SIZE)
        ctx.add_init_script(
            f"try{{localStorage.setItem('kist_token',{token!r});localStorage.setItem('kist.theme','dark');"
            "localStorage.setItem('kist.setup','{\"dismissed\":true}')}catch(e){}"
        )
        if BOARD:
            # open the board once in a throwaway tab first, so its images are
            # cached and the recorded tab doesn't show them loading
            warm = ctx.new_page()
            warm.goto(url + "/all", wait_until="networkidle")
            board = warm.get_by_text(BOARD, exact=True).first
            if board.count():
                board.click()
                warm.wait_for_load_state("networkidle")
                warm.wait_for_function(
                    "() => [...document.images].length > 3 && [...document.images].every(i => i.complete)", timeout=15000
                )
            warm.close()
        page = ctx.new_page()
        page.goto(url + "/all" + (f"?{SAFE}" if SAFE else ""), wait_until="networkidle")
        page.wait_for_timeout(1200)  # 1
        # 2: bigger tiles, then a slow scroll down and back
        slider = page.locator("input[type=range]").first
        if slider.count():
            box = slider.bounding_box()
            if box:
                y = box["y"] + box["height"] / 2
                page.mouse.move(box["x"] + box["width"] * 0.35, y)
                page.mouse.down()
                for i in range(12):
                    page.mouse.move(box["x"] + box["width"] * (0.35 + 0.02 * i), y)
                    page.wait_for_timeout(60)
                page.mouse.up()
        page.wait_for_timeout(600)
        page.mouse.move(SIZE["width"] / 2, SIZE["height"] / 2)
        for _ in range(10):
            page.mouse.wheel(0, 100)
            page.wait_for_timeout(70)
        page.wait_for_timeout(400)
        for _ in range(10):
            page.mouse.wheel(0, -100)
            page.wait_for_timeout(60)
        page.wait_for_timeout(500)
        # 3: open an item in the info panel
        tile = page.locator(".tile").nth(ITEM_INDEX)
        tile.click()
        page.wait_for_timeout(2200)
        # 4: full screen and back
        tile.dblclick()
        page.wait_for_timeout(2000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
        # 5: search by colour. On a library that holds only the demo set, press
        # the palette swatch itself. With SAFE set, don't: a swatch starts a
        # LIBRARY-WIDE colour search (it drops SAFE), so take the swatch's
        # colour and run the search inside SAFE as an in-app navigation
        # (pushState + popstate, the way Back/Forward move), not a reload.
        swatch = page.locator(".color-palette button").nth(int(os.environ.get("KIST_DEMO_SWATCH", "1")))
        hexv = os.environ.get("KIST_DEMO_COLOR", "#3a6f8f")
        if swatch.count():
            m = (swatch.get_attribute("title") or "") + " " + (swatch.get_attribute("aria-label") or "")
            found = [w for w in m.replace(",", " ").split() if w.startswith("#") and len(w) == 7]
            hexv = found[0] if found else hexv
            swatch.hover()
            page.wait_for_timeout(500)
        if swatch.count() and not SAFE:
            swatch.click()
        else:
            target = "/all?" + "&".join(x for x in (SAFE, "color=" + hexv.replace("#", "%23")) if x)
            page.evaluate("t => { history.pushState(null, '', t); dispatchEvent(new PopStateEvent('popstate')) }", target)
        page.wait_for_timeout(3000)
        # 6: a board (by name, from the sidebar), else back to the grid
        # (sidebar rows are buttons; BOARDS comes before FOLDERS, so .first)
        link = page.get_by_text(BOARD, exact=True).first if BOARD else None
        if link is not None and link.count():
            link.click()
            page.wait_for_timeout(2500)
        else:
            page.go_back()
            page.wait_for_timeout(1500)
        video = page.video
        ctx.close()
        browser.close()
        raw = OUT / "raw.webm"
        Path(video.path()).replace(raw)
    shutil.rmtree(tmp, ignore_errors=True)
    return raw


def encode(raw: Path) -> None:
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ff:
        print("no ffmpeg: kept", raw)
        return
    mp4, gif = MP4, OUT / "demo.gif"
    mp4.parent.mkdir(parents=True, exist_ok=True)
    # drop the blank first frames while the page loads
    subprocess.run(
        [ff, "-y", "-loglevel", "error", "-ss", "0.6", "-i", str(raw), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-crf", "28", "-preset", "slow", "-movflags", "+faststart", "-vf", "scale=1280:-2", str(mp4)],
        check=True,
    )
    pal = OUT / "_palette.png"
    vf = "fps=10,scale=640:-1:flags=lanczos"
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(mp4), "-vf", f"{vf},palettegen=max_colors=128:stats_mode=diff", str(pal)], check=True)
    subprocess.run(
        [ff, "-y", "-loglevel", "error", "-i", str(mp4), "-i", str(pal), "-lavfi",
         f"{vf}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", "-loop", "0", str(gif)],
        check=True,
    )
    pal.unlink()
    if not os.environ.get("KIST_DEMO_KEEP_RAW"):
        raw.unlink()  # 10 MB of WebM nobody needs in the repo
    for f in (mp4, gif):
        print(f, f"{f.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    encode(record())
