"""Child-process entry point for preview generation (see
thumbs.generate_previews_isolated). Prints the probe as one JSON line."""

import json
import logging
import sys
from pathlib import Path

from . import thumbs


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING)
    if argv and argv[0] == "--screen":
        original, item_dir, ext = argv[1:]
        thumbs.screen_copy_from(Path(original), Path(item_dir), ext)
        return 0
    if argv and argv[0] == "--thumbs-batch":
        # perf P3 backfill (services/thumb_backfill.py): one JSON line
        # [source, item_dir] per item on stdin, one {"i", "error"} line out
        for i, line in enumerate(sys.stdin.read().splitlines()):
            source, item_dir = json.loads(line)
            try:
                thumbs.make_thumbs_from(Path(source), Path(item_dir), mark=False)
                error = None
            except Exception as exc:
                error = str(exc)[-500:] or type(exc).__name__
            print(json.dumps({"i": i, "error": error}), flush=True)
        return 0
    original, item_dir, mime, ext = argv[:4]
    options = json.loads(argv[4]) if len(argv) > 4 else None
    probe = thumbs.generate_previews(Path(original), Path(item_dir), mime, ext, options)
    print(json.dumps({
        "width": probe.width, "height": probe.height, "duration_s": probe.duration_s,
        "media": probe.media,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
