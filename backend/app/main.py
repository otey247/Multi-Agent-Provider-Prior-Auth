import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.observability import setup_observability
from app.routers import review, decision, agents

# Configure logging for the app namespace
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("app").setLevel(logging.DEBUG)

# Enable Azure Application Insights observability
setup_observability()

app = FastAPI(
    title="Prior Authorization Review API",
    description="Prior auth review powered by Azure OpenAI gpt-5.4 via Microsoft Agent Framework",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.FRONTEND_ORIGIN,
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(review.router, prefix="/api")
app.include_router(decision.router, prefix="/api")
app.include_router(agents.router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}
