"""
Memory Engine — Open WebUI Bridge
Intercepts Open WebUI conversations → feeds to Memory Engine.

Open WebUI uses a middleware/hooks system.
This bridge hooks into the chat flow to extract tags and inject user context.
"""

import json
import asyncio
from typing import List, Dict, Optional
from datetime import datetime


class OpenWebUIBridge:
    """
    Bridge between Open WebUI and Memory Engine.
    
    Flow:
    1. User sends message in Open WebUI
    2. Bridge intercepts → sends to ME for tag extraction
    3. ME returns user portrait context
    4. Context injected into LLM system prompt
    5. LLM responds with awareness of user history
    6. Response also sent to ME for extraction
    """

    def __init__(self, me_base_url: str = "http://localhost:8001"):
        self.me_url = me_base_url
        self._http = None

    @property
    def http(self):
        if self._http is None:
            import httpx
            self._http = httpx.AsyncClient(timeout=30)
        return self._http

    async def get_user_context(self, user_id: str) -> str:
        """
        Get pre-computed user context for system prompt injection.
        Called BEFORE LLM processes the message.
        """
        try:
            resp = await self.http.get(f"{self.me_url}/api/memory/context/{user_id}")
            if resp.status_code == 200:
                return resp.json().get("context", "")
        except Exception:
            pass
        return ""

    async def get_user_portrait(self, user_id: str) -> dict:
        """Get full user portrait."""
        try:
            resp = await self.http.get(f"{self.me_url}/api/memory/portrait/{user_id}")
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return {}

    async def record_conversation(self, user_id: str, messages: List[Dict], title: str = None):
        """
        Send completed conversation to ME for extraction.
        Called AFTER the conversation ends.
        """
        try:
            resp = await self.http.post(f"{self.me_url}/api/memory/add", json={
                "user_id": user_id,
                "messages": messages,
                "title": title,
            })
            return resp.json() if resp.status_code == 200 else None
        except Exception:
            return None

    async def search_memories(self, user_id: str, query: str, limit: int = 5) -> list:
        """Search user's memories for context."""
        try:
            resp = await self.http.post(f"{self.me_url}/api/memory/search", json={
                "user_id": user_id,
                "query": query,
                "limit": limit,
            })
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return []

    async def close(self):
        if self._http:
            await self._http.aclose()


# ═══ Open WebUI Plugin Integration ════════════════════════

OPENWEBUI_PLUGIN_CODE = """
# Open WebUI Function Plugin — Memory Engine Integration
# Place in Open WebUI → Admin → Functions

from pydantic import BaseModel, Field
from typing import Optional
import requests

class Pipe:
    class Valves(BaseModel):
        ME_URL: str = Field(default="http://localhost:8001", description="Memory Engine URL")
        ENABLED: bool = Field(default=True, description="Enable memory extraction")

    def __init__(self):
        self.valves = self.Valves()

    def pipe(self, body: dict, __user__: dict) -> dict:
        \"\"\"Inject user context before LLM call.\"\"\"
        if not self.valves.ENABLED:
            return body

        user_id = __user__.get("id", "default")
        messages = body.get("messages", [])

        # Get user context from Memory Engine
        try:
            resp = requests.get(f"{self.valves.ME_URL}/api/memory/context/{user_id}", timeout=5)
            if resp.status_code == 200:
                context = resp.json().get("context", "")
                if context:
                    # Inject as system message
                    system_msg = {
                        "role": "system",
                        "content": f"[User Memory Context]\\n{context}"
                    }
                    # Insert after existing system message or at start
                    insert_idx = 0
                    for i, msg in enumerate(messages):
                        if msg.get("role") == "system":
                            insert_idx = i + 1
                            break
                    messages.insert(insert_idx, system_msg)
                    body["messages"] = messages
        except Exception:
            pass

        # After response, record conversation
        # (This is done asynchronously via a background task)

        return body
"""


# ═══ Open WebUI Middleware Hook ════════════════════════════

OPENWEBUI_MIDDLEWARE = """
# Open WebUI Middleware — Memory Engine
# Add to Open WebUI backend middleware

import asyncio
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

class MemoryEngineMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, me_url: str = "http://localhost:8001"):
        super().__init__(app)
        self.me_url = me_url

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Intercept chat completion responses
        if request.url.path.endswith("/chat/completions") and request.method == "POST":
            # Extract user_id from auth
            user_id = getattr(request.state, "user_id", "anonymous")

            # Record conversation in background
            asyncio.create_task(self._record(user_id, request))

        return response

    async def _record(self, user_id: str, request: Request):
        try:
            import httpx
            body = await request.body()
            data = json.loads(body)
            messages = data.get("messages", [])

            async with httpx.AsyncClient() as client:
                await client.post(f"{self.me_url}/api/memory/add", json={
                    "user_id": user_id,
                    "messages": messages,
                }, timeout=30)
        except Exception:
            pass
"""


def generate_openwebui_plugin(me_url: str = "http://localhost:8001") -> str:
    """Generate the Open WebUI plugin code with configured ME URL."""
    return OPENWEBUI_PLUGIN_CODE.replace("http://localhost:8001", me_url)
