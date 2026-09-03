from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import canvas, runs


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AIVAR QA Agent API", lifespan=lifespan)

# The web port differs per worktree, so allow any localhost origin rather than
# hardcoding one and re-discovering CORS at 2am.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(canvas.router)
app.include_router(runs.router)

# Screenshots and diffs are written here and rendered straight into canvas
# widgets by URL -- no upload endpoint needed.
app.mount(
    "/artifacts",
    StaticFiles(directory=settings.artifacts_dir),
    name="artifacts",
)


@app.get("/health")
def health():
    """Also reports which worktree answered, so you know which stack you hit."""
    return {
        "status": "ok",
        "worktree": settings.worktree_name,
        "api_port": settings.api_port,
        "web_port": settings.web_port,
    }
