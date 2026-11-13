from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KIST_", extra="ignore")

    token: str = "change-me"
    database_url: str = "postgresql+psycopg://kist:kist@db:5432/kist"
    library_dir: Path = Path("/library")
    inbox_poll_seconds: float = 5.0
    worker_poll_seconds: float = 2.0
    max_download_mb: int = 512
    # "add from URL" refuses private/LAN/loopback addresses unless allowed:
    # url_allow_private=true allows them all; url_allow_hosts is a comma list
    # of host names and/or CIDR ranges allowed anyway (e.g. "nas.lan,10.0.5.0/24")
    url_allow_private: bool = False
    url_allow_hosts: str = ""
    # overall limit for one URL download (each read also times out after 30 s)
    download_timeout_seconds: int = 120
    # preview generation runs in a child process; it is killed after this long
    preview_timeout_seconds: int = 300
    # Strict-Transport-Security max-age; browsers only honour it over HTTPS.
    # 0 turns it off (e.g. when Kist is served over plain HTTP on purpose)
    hsts_seconds: int = 15552000
    # sent when downloading URLs; some sites (Wikimedia) require contact info
    user_agent: str = ""
    # a job a worker claimed longer ago than this is treated as abandoned (the
    # worker died) and queued again; keep it above download + preview limits
    job_lease_seconds: int = 900
    # the address people open Kist at (e.g. https://kist.example.com); MCP
    # results then carry absolute web_url links. Empty: links are site-relative
    public_url: str = ""
    # largest direct upload accepted (bytes); bigger ones get 413
    max_upload_bytes: int = 4 * 1024**3
    # DEBUG | INFO | WARNING | ERROR for the app and worker logs
    log_level: str = "INFO"
    # the commit the image was built from (Dockerfile build arg)
    git_commit: str = "unknown"
    # the worker runs `kist verify` this often (0 turns it off); deep health
    # reports the last result
    verify_interval_hours: float = 24.0
    # the worker permanently deletes items trashed longer than this (never
    # protected ones); 0 keeps the trash forever
    trash_retention_days: int = 30
    # `kist backup` sends the library to this restic repository (a path,
    # sftp:, rest:, s3:, b2: ...). The password comes from RESTIC_PASSWORD or
    # RESTIC_PASSWORD_FILE, restic's own variables
    backup_repo: str = ""
    # the worker runs `kist backup` this often (0: only by hand)
    backup_interval_hours: float = 0.0

    # ---- AI (the `ml` service; see services/ai.py) ----
    # queue embed/ocr jobs for new items (compose turns this on)
    ai_enabled: bool = False
    # the ml service the worker sends AI jobs to (compose: http://ml:8000).
    # Empty: the worker runs no AI jobs itself; `kist helper` can still claim them
    ml_url: str = ""
    # the embedding model; vectors from different models never mix
    ml_model: str = "ViT-B-32__openai"
    # one ml request's time limit (seconds)
    ml_timeout_seconds: float = 120.0
    # when backfill jobs may run, local time "HH:MM-HH:MM" (may wrap past
    # midnight); empty = any time. New items' jobs don't wait for it
    ai_window: str = ""
    # AI jobs one worker runs at once (threads; ingest never waits for them).
    # Two keep the ml service busy while the other thread reads files and
    # writes results
    ai_concurrency: int = 2
    # embed jobs one worker thread sends to the ml service in one request
    # (POST /embed/images, at most 32); 1 sends them one at a time
    ai_embed_batch: int = 16
    # scanned PDFs: OCR at most this many pages (text layers are read in full)
    ai_ocr_pdf_pages: int = 5
    # faces and people (opt-in): queue `faces` jobs for images. The ml
    # service needs KIST_FACES=1 too (it only loads the face models then)
    faces: bool = False
    # F7 cutouts: "Cut out subject" makes a transparent PNG as a new item
    # (services/cutouts.py). The ml service downloads the model the first
    # time someone asks for a cut-out (ISNet 179 MB; BiRefNet-lite 224 MB
    # for "fine edges"). False hides the action and refuses the endpoints
    cutouts: bool = True
    # the model a cut-out uses unless the request names one: "" lets the ml
    # service decide (its KIST_CUTOUT_MODEL, else isnet-general-use)
    cutout_model: str = ""
    # a cut-out's long side at most this many pixels (the original's own
    # size when smaller)
    cutout_max_side: int = 4096
    # the worker recomputes suggested collections this often (0: only on
    # demand, GET /api/collections/suggested?refresh=1)
    collections_interval_hours: float = 24.0

    # ---- captions (services/captions.py; off unless a provider is set) ----
    # ollama | openai | anthropic | gemini. The last three send each image
    # captioned to that company (bring your own key)
    caption_provider: str = ""
    # ollama: its address (e.g. http://ollama:11434); empty with ollama means
    # a `kist helper --kinds caption` on a Mac runs them. For openai, an
    # OpenAI-compatible server's /v1 address instead of api.openai.com
    caption_url: str = ""
    # e.g. qwen2.5vl:3b (ollama), gpt-4o-mini, claude-haiku-4-5, gemini-2.5-flash
    caption_model: str = ""
    caption_api_key: str = ""
    # the instruction sent with each image (empty: services/captions.DEFAULT_PROMPT)
    caption_prompt: str = ""
    caption_timeout_seconds: float = 180.0
    # caption every new item automatically (otherwise only on demand and by
    # `kist ai-backfill --kind caption`)
    caption_auto: bool = False

    # ---- M4: in, out and sharing ----
    # finished exports (library/exports/<id>.zip) are deleted after this long
    export_retention_days: int = 7
    # POST /api/items/{id}/snapshot with no body fetches page_url on the
    # server (through the SSRF guard). Off: only browser-sent snapshots
    snapshots: bool = False
    # the link-rot watch re-checks each item's page_url this often (days).
    # Off (0) by default since 2.0: once Kist has its copy, the page it came
    # from is the other site's business, and re-requesting it costs that
    # site requests (and gets rate-limited). Requests are spaced
    # KIST_LINK_CHECK_DELAY_SECONDS apart when it's on
    link_check_days: float = 0.0
    link_check_delay_seconds: float = 2.0
    # ask the Wayback Machine (archive.org, an outside service) for an
    # archived copy of sources that are gone. Off by default: it tells
    # archive.org which pages you kept
    wayback: bool = False
    # write items/<id>/original.xmp (rating, keywords, notes, provenance) for
    # images whenever they change, for Lightroom/darktable/digiKam
    xmp_sidecars: bool = True
    # failed share-link password tries allowed per link per 15 minutes
    share_password_attempts: int = 10

    @property
    def exports_dir(self) -> Path:
        return self.library_dir / "exports"

    # ---- media (Milestone 5; services/media.py, services/video.py) ----
    # threads one ffmpeg run may use (it also runs under `nice`)
    ffmpeg_threads: int = 2
    # hardware video encoding for proxies and clips: qsv | vaapi | none.
    # Needs /dev/dri passed into the worker container; software is the
    # fallback when the hardware encoder fails
    hwaccel: str = "none"
    vaapi_device: str = "/dev/dri/renderD128"
    # frames in a video's scrub sprite (scrub.webp)
    scrub_frames: int = 20
    # longest clip (seconds) for mp4/mp3/wav, and for animated gif/webp
    clip_max_seconds: float = 600.0
    gif_max_seconds: float = 60.0
    # web-safe proxies: long side at most this many pixels
    proxy_max_side: int = 1920
    # use exiftool (when installed) for camera metadata and RAW previews
    exiftool: bool = True
    # asset bundles (zip files): limits that keep a zip bomb harmless
    bundle_max_entries: int = 20000
    bundle_max_total_mb: int = 8192  # uncompressed, all entries together
    bundle_max_entry_mb: int = 1024  # one entry served or read
    # the web map: a tile URL template or a PMTiles file URL, and its credit
    # line. Empty: the web uses the self-hosted PMTiles file (docs/maps.md)
    map_tiles_url: str = ""
    map_attribution: str = ""

    @property
    def geo_dir(self) -> Path:
        """library/.kist/geo: the GeoNames extract `kist geo-download` writes."""
        return self.state_dir / "geo"

    @property
    def items_dir(self) -> Path:
        return self.library_dir / "items"

    @property
    def inbox_dir(self) -> Path:
        return self.library_dir / "inbox"

    @property
    def staging_dir(self) -> Path:
        return self.library_dir / ".staging"

    @property
    def state_dir(self) -> Path:
        """library/.kist: worker heartbeat, last verify result, repair notes."""
        return self.library_dir / ".kist"

    @property
    def import_dirs(self) -> list[Path]:
        """Directories the MCP add_item(file_path=...) tool may read from."""
        return [self.inbox_dir, self.library_dir / "import"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
