"""
Memory Engine — FastAPI Application
Per-user SQLite → Central PostgreSQL pipeline.
With Open WebUI and AIOS integrations.
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.database import list_users
from app.core.llm_provider import get_llm
from app.routers.memory import router as memory_router
from app.routers.admin import router as admin_router
from app.integrations.openwebui import generate_openwebui_plugin

app = FastAPI(
    title="Memory Engine (ME)",
    description="Tag weight-based user profiling for AI agents. Per-user SQLite with central PostgreSQL sync.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
        "llm": get_llm().stats,
        "endpoints": {
            "user": {
                "POST /api/memory/add": "Add conversation → full pipeline",
                "GET /api/memory/portrait/{user_id}": "User portrait",
                "POST /api/memory/search": "Search memories",
                "GET /api/memory/tags/{user_id}": "Tags by weight",
                "GET /api/memory/links/{user_id}": "Tag associations",
                "GET /api/memory/events/{user_id}": "Event log",
                "GET /api/memory/insights/{user_id}": "Generated insights",
                "GET /api/memory/context/{user_id}": "LLM system context",
            },
            "admin": {
                "GET /api/admin/users": "List all users",
                "POST /api/admin/users/{user_id}": "Create user",
                "DELETE /api/admin/users/{user_id}": "Delete user (GDPR)",
                "POST /api/admin/sync/{user_id}": "Sync to central DB",
                "POST /api/admin/sync-all": "Sync all users",
                "GET /api/admin/stats": "Global statistics",
            },
            "integrations": {
                "GET /integrations/openwebui/plugin": "Get Open WebUI plugin code",
                "GET /integrations/aios/context/{user_id}": "Context for Note Agent",
            },
        },
    }


@app.get("/integrations/openwebui/plugin")
async def openwebui_plugin():
    """Get the Open WebUI plugin code."""
    me_url = os.getenv("ME_URL", "http://localhost:8001")
    return {"code": generate_openwebui_plugin(me_url), "instructions": "Paste into Open WebUI → Admin → Functions"}


@app.get("/integrations/aios/context/{user_id}")
async def aios_context(user_id: str):
    """Get context for AIOS Note Agent injection."""
    from app.integrations.aios import AIOSBridge
    bridge = AIOSBridge()
    context = await bridge.get_context_for_agent(user_id)
    await bridge.close()
    return {"user_id": user_id, "context": context}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "users": len(list_users()),
        "llm_providers": get_llm().stats.get("providers", []),
    }
