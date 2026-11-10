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
    # AI jobs one worker runs at once (threads; ingest never waits for them)
    ai_concurrency: int = 1
    # scanned PDFs: OCR at most this many pages (text layers are read in full)
    ai_ocr_pdf_pages: int = 5
    # faces and people (opt-in): queue `faces` jobs for images. The ml
    # service needs KIST_FACES=1 too (it only loads the face models then)
    faces: bool = False
    # the worker recomputes suggested collections this often (0: only on
    # demand, GET /api/collections/suggested?refresh=1)
    collections_interval_hours: float = 24.0

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
