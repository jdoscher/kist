# Releasing

1. Move the **Unreleased** notes in `CHANGELOG.md` under a new
   `## [X.Y.Z] - YYYY-MM-DD` heading.
2. Set the version in `kist/__init__.py`, `pyproject.toml`, and
   `web/package.json` (then `cd web && npm install --package-lock-only`).
3. Commit, wait for CI to pass on `main`, then tag and push the tag:

   ```bash
   git tag -a vX.Y.Z -m "Kist X.Y.Z"
   git push origin vX.Y.Z
   ```

The **Release** workflow builds the multi-arch image
(`ghcr.io/jdoscher/kist:X.Y.Z`, `:X.Y`, `:X`) and creates the GitHub release
from that version's changelog section. After the first release, make the GHCR
package public under **Packages → kist → Package settings**.

## Demo loop and screenshots

Re-shoot them on a throwaway demo instance seeded with the public-domain
library ([Demo library](demo/README.md)), never a real one. The GIF and the
screenshots are committed; `docs/demo/record.py` writes the MP4 outside the
repository (`../kist-release-assets/demo.mp4`, or `$KIST_DEMO_MP4`). Attach
that MP4 to the GitHub release by hand (**Edit release → Attach files**); it
is never committed. Check `docs/demo/CREDITS.md` still lists every image
the new pictures show.
