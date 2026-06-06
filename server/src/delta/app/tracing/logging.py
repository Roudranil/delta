"""
File configures logging across the entire codebase

Sections:
- sinks
- function decorators
- fastapi helpers
- text sanitisation utilites
- setup
"""

from loguru import logger

from delta.app.config import AppSettings, app_settings

# -- sinks --


# -- setup --
def setup_logging(settings: AppSettings) -> None:
    """Configure logging across the entire app. Idempotence is desired. Call at startup

    Parameters
    ----------
    settings : AppSettings
        app settings instance
    """
    logger.remove()
