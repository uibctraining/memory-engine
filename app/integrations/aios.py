"""
Memory Engine — AIOS Note Agent Bridge
Connects ME to AIOS: tags → notes, dispatches → modules, context → LLM.

This is the bridge between ME (logic layer) and AIOS (application layer).
"""

import json
import httpx
from typing import List, Dict, Optional
from datetime import datetime


class AIOSBridge:
    """
    Bridge between Memory Engine and AIOS.
    
    Data flow:
    ME tags → AIOS Notes (conclusion output)
    ME dispatches → AIOS Modules (structured data)
    ME portrait → AIOS Note Agent (system context)
    ME events → AIOS Insights (analytics)
    """

    def __init__(self, aios_url: str = "http://localhost:8000", api_key: str = ""):
        self.aios_url = aios_url.rstrip('/')
        self.api_key = api_key
        self._http = None

    @property
    def http(self):
        if self._http is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._http = httpx.AsyncClient(timeout=30, headers=headers)
        return self._http

    # ─── Write to AIOS Modules ────────────────────────────

    async def write_note(self, user_id: str, title: str, content: str, tags: list = None) -> dict:
        """Write a note to AIOS Notes module."""
        try:
            resp = await self.http.post(f"{self.aios_url}/api/v2/notes", json={
                "title": title,
                "content": content,
                "tags": tags or [],
                "source": "memory_engine",
                "notebook": "auto-extracted",
            })
            return resp.json() if resp.status_code == 200 else {"error": resp.text}
        except Exception as e:
            return {"error": str(e)}

    async def create_contact(self, user_id: str, name: str, email: str = "", company: str = "") -> dict:
        """Write a contact to AIOS CRM."""
        try:
            resp = await self.http.post(f"{self.aios_url}/api/crm/contacts", json={
                "name": name,
                "email": email,
                "company": company,
                "source": "memory_engine",
                "status": "lead",
            })
            return resp.json() if resp.status_code == 200 else {"error": resp.text}
        except Exception as e:
            return {"error": str(e)}

    async def create_task(self, user_id: str, title: str, content: str = "", priority: str = "medium") -> dict:
        """Write a task to AIOS Tasks module."""
        try:
            resp = await self.http.post(f"{self.aios_url}/api/v2/notes", json={
                "title": title,
                "content": content,
                "tags": ["auto-task"],
                "source": "memory_engine",
            })
            return resp.json() if resp.status_code == 200 else {"error": resp.text}
        except Exception as e:
            return {"error": str(e)}

    async def write_insight(self, user_id: str, summary: str, changes: list) -> dict:
        """Write an insight to AIOS Notes."""
        content = f"## Memory Engine Insight\n\n{summary}\n\n### Changes\n"
        for change in changes:
            content += f"- {change.get('tag', 'unknown')}: {change.get('change', 'no details')}\n"

        return await self.write_note(
            user_id=user_id,
            title=f"Insight — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
            content=content,
            tags=["insight", "auto-generated"],
        )

    # ─── Dispatch Handler ─────────────────────────────────

    async def execute_dispatch(self, user_id: str, dispatch: dict) -> dict:
        """
        Execute a dispatch from ME to the appropriate AIOS module.
        Star schema routing: tag → module mapping.
        """
        module = dispatch.get("target_module", "")
        action = dispatch.get("action", "create")
        payload = dispatch.get("payload", {})

        if module == "notes.notes":
            return await self.write_note(
                user_id=user_id,
                title=payload.get("title", "Auto-extracted"),
                content=payload.get("summary", ""),
                tags=payload.get("tags", []),
            )
        elif module == "crm.contacts":
            return await self.create_contact(
                user_id=user_id,
                name=payload.get("name", ""),
                company=payload.get("company", ""),
            )
        elif module.startswith("tasks"):
            return await self.create_task(
                user_id=user_id,
                title=payload.get("title", "Auto-task"),
                content=payload.get("content", ""),
            )
        else:
            return {"skipped": module, "reason": "no handler"}

    # ─── Context Provider ─────────────────────────────────

    async def get_context_for_agent(self, user_id: str) -> str:
        """
        Get ME context for AIOS Note Agent.
        Called by Note Agent before each LLM response.
        """
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"http://localhost:8001/api/memory/context/{user_id}")
                if resp.status_code == 200:
                    return resp.json().get("context", "")
        except Exception:
            pass
        return ""

    async def close(self):
        if self._http:
            await self._http.aclose()


# ═══ Dispatch Executor ════════════════════════════════════

async def execute_pending_dispatches(me_db_session, user_id: str, aios_bridge: AIOSBridge):
    """
    Execute all pending dispatches from a user's SQLite to AIOS modules.
    Called by sync engine.
    """
    from app.models.schema import Dispatch

    pending = me_db_session.query(Dispatch).filter(
        Dispatch.user_id == user_id,
        Dispatch.verified == False,
    ).all()

    results = []
    for dispatch in pending:
        result = await aios_bridge.execute_dispatch(user_id, {
            "target_module": dispatch.target_module,
            "action": dispatch.action,
            "payload": dispatch.payload,
        })
        dispatch.verified = True
        dispatch.verified_at = datetime.utcnow()
        dispatch.target_id = result.get("id")
        results.append(result)

    me_db_session.commit()
    return results
