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
    original, item_dir, mime, ext = argv
    probe = thumbs.generate_previews(Path(original), Path(item_dir), mime, ext)
    print(json.dumps({
        "width": probe.width, "height": probe.height, "duration_s": probe.duration_s,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
