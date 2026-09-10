"""
Memory Engine — FastAPI Application
Per-user SQLite → Central PostgreSQL pipeline.
"""

from fastapi import FastAPI
from app.core.database import list_users
from app.routers.memory import router as memory_router
from app.routers.admin import router as admin_router

app = FastAPI(
    title="Memory Engine (ME)",
    description="Tag weight-based user profiling for AI agents. Per-user SQLite with central PostgreSQL sync.",
    version="0.2.0",
)

app.include_router(memory_router)
app.include_router(admin_router)


@app.get("/")
async def root():
    return {
        "name": "Memory Engine",
        "version": "0.2.0",
        "architecture": "per-user SQLite → central PostgreSQL",
        "active_users": len(list_users()),
        "endpoints": {
            "user": {
                "POST /api/memory/add": "Add conversation → extract → dispatch → archive → insight → verify",
                "GET /api/memory/portrait/{user_id}": "Get user portrait",
                "POST /api/memory/search": "Search memories",
                "GET /api/memory/tags/{user_id}": "List tags by weight",
                "GET /api/memory/context/{user_id}": "Get LLM system context",
            },
            "admin": {
                "GET /api/admin/users": "List all users",
                "POST /api/admin/sync/{user_id}": "Sync user to central DB",
                "POST /api/admin/sync-all": "Sync all users",
                "GET /api/admin/stats": "Global statistics",
            },
        },
    }
