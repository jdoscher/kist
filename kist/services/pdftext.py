"""A PDF's text layer, and page images for OCR when it has none.

Runs in a child process with the preview time limit (like the preview
decoders): pdfium parses files from anywhere, and a hostile or huge PDF
must not take the worker down or hold it forever.
"""

import json
import subprocess
import sys
from pathlib import Path

# a text layer shorter than this (after trimming) is treated as none: scans
# often carry a stray page number or producer string
MIN_TEXT_CHARS = 8
MAX_TEXT_CHARS = 1_000_000
RENDER_LONG_SIDE = 2000


def extract(original: Path, max_pages: int, out_dir: Path) -> dict:
    """{"text": str, "pages": n, "images": [png paths]}. Images are rendered
    (first `max_pages` pages) only when the text layer is empty."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(original))
    try:
        parts: list[str] = []
        total = 0
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                tp = page.get_textpage()
                try:
                    t = tp.get_text_range() or ""
                finally:
                    tp.close()
            finally:
                page.close()
            t = t.replace("\r\n", "\n").replace("\r", "\n").strip()
            if t:
                parts.append(t)
                total += len(t)
            if total >= MAX_TEXT_CHARS:
                break
        text = "\n\n".join(parts)[:MAX_TEXT_CHARS]
        images: list[str] = []
        if len(text.strip()) < MIN_TEXT_CHARS:
            text = ""
            for i in range(min(len(pdf), max_pages)):
                page = pdf[i]
                try:
                    w, h = page.get_size()
                    scale = RENDER_LONG_SIDE / max(w, h, 1)
                    img = page.render(scale=scale).to_pil()
                finally:
                    page.close()
                p = out_dir / f"page-{i + 1}.png"
                img.convert("RGB").save(p, "PNG")
                images.append(str(p))
        return {"text": text, "pages": len(pdf), "images": images}
    finally:
        pdf.close()


def extract_isolated(original: Path, max_pages: int, out_dir: Path, timeout: float) -> dict:
    try:
        out = subprocess.run(
            [sys.executable, "-m", "kist.services.pdftext", str(original), str(max_pages),
             str(out_dir)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("reading the PDF's text timed out") from None
    if out.returncode != 0:
        tail = [ln for ln in out.stderr.splitlines() if ln.strip()][-1:] or ["unknown error"]
        raise RuntimeError(f"reading the PDF's text failed: {tail[0][:300]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def main(argv: list[str]) -> int:
    original, max_pages, out_dir = argv
    print(json.dumps(extract(Path(original), int(max_pages), Path(out_dir))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
