from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from delta.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Register tools via import side-effects
    import delta.tools.bash  # noqa: F401
    import delta.tools.read_file  # noqa: F401
    import delta.tools.write_file  # noqa: F401

    settings.session_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Delta starting — model={settings.default_model} sessions={settings.session_dir}")
    yield
    logger.info("Delta stopped")


app = FastAPI(title="Delta", version="0.0.1-alpha", lifespan=lifespan)

from delta.api.routes import router  # noqa: E402
app.include_router(router)
