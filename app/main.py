"""
Memory Engine — FastAPI Application
"""

from fastapi import FastAPI
from app.core.database import init_db
from app.routers.memory import router as memory_router

app = FastAPI(
    title="Memory Engine (ME)",
    description="Tag weight-based user profiling for AI agents",
    version="0.1.0",
)

app.include_router(memory_router)


@app.on_event("startup")
async def startup():
    init_db()


@app.get("/")
async def root():
    return {
        "name": "Memory Engine",
        "version": "0.1.0",
        "description": "Tag weight-based user profiling for AI agents",
        "endpoints": {
            "POST /api/memory/add": "Add conversation and extract tags",
            "GET /api/memory/portrait/{user_id}": "Get user portrait",
            "POST /api/memory/search": "Search memories",
            "GET /api/memory/tags/{user_id}": "List tags by weight",
            "GET /api/memory/links/{user_id}": "List tag associations",
            "GET /api/memory/context/{user_id}": "Get LLM system context",
        },
    }
