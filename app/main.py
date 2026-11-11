from contextlib import asynccontextmanager

from app.logging_config import setup_logging

# Called before any other app.* import below - this needs to run as early
# as possible so nothing below has a chance to log through an unconfigured
# root logger first, same placement as the equivalent call already at the
# top of app/worker.py.
setup_logging()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.api.routes import (  # noqa: E402
    admin,
    agent,
    auth,
    billing,
    crm,
    focus,
    integrations,
    knowledge,
    life_balance,
    life_goals,
    namaz,
    projects,
    reminders,
    tasks,
)
from app.embeddings.factory import get_embedding_provider  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Same reasoning as app/worker.py's own startup preload: without this,
    # the sentence-transformers model (a few seconds, CPU-bound) loaded
    # lazily on whichever request first touched /knowledge - an
    # unpredictable stall for whichever user happened to trigger it, with
    # no loading indicator on the frontend for that specific case. This
    # process and worker.py already each hold their own full copy of the
    # model regardless (they're separate processes - see docs/
    # ARCHITECTURE.md), so preloading here doesn't add memory, it just
    # moves the existing cost to process startup where it's expected.
    get_embedding_provider()
    yield


app = FastAPI(title="AI Assistant Core", lifespan=lifespan)

# Bearer-token auth (no cookies), so a wide-open CORS policy carries no CSRF
# risk - simplest option while the Mini App frontend's dev/prod origins are
# still moving around.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(tasks.router)
app.include_router(projects.router)
app.include_router(focus.router)
app.include_router(agent.router)
app.include_router(integrations.router)
app.include_router(life_goals.router)
app.include_router(reminders.router)
app.include_router(namaz.router)
app.include_router(life_balance.router)
app.include_router(knowledge.router)
app.include_router(crm.router)
app.include_router(billing.router)
app.include_router(admin.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
