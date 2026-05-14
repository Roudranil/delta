from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DELTA_")

    default_model: str = "claude-3-5-sonnet-20241022"
    session_dir: Path = Path.home() / ".delta" / "sessions"
    max_iterations: int = 10
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
