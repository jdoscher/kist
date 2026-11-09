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
    thumb_sizes: tuple[int, int] = (256, 1024)
    # a job a worker claimed longer ago than this is treated as abandoned (the
    # worker died) and queued again; keep it above download + preview limits
    job_lease_seconds: int = 900
    # the address people open Kist at (e.g. https://kist.example.com); MCP
    # results then carry absolute web_url links. Empty: links are site-relative
    public_url: str = ""

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
    def import_dirs(self) -> list[Path]:
        """Directories the MCP add_item(file_path=...) tool may read from."""
        return [self.inbox_dir, self.library_dir / "import"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
