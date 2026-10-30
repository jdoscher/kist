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
    # sent when downloading URLs; some sites (Wikimedia) require contact info
    user_agent: str = ""
    thumb_sizes: tuple[int, int] = (256, 1024)

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
