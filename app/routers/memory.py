"""
Memory Engine — Memory Router
Per-user operations: add conversation, search, get portrait.
"""

import uuid
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func

from app.models.schema import Episode, Tag, EventTag, TagLink, Event, Insight
from app.core.database import user_db, create_user_db, list_users, get_user_session
from app.core.weight_engine import recalculate_all_weights, generate_portrait
from app.core.pipeline import Pipeline
from app.core.llm_provider import get_llm

router = APIRouter(prefix="/api/memory", tags=["memory"])


# ═══ Request/Response Models ══════════════════════════════

class Message(BaseModel):
    role: str
    content: str

class AddRequest(BaseModel):
    user_id: str
    messages: List[Message]
    title: Optional[str] = None

class SearchRequest(BaseModel):
    user_id: str
    query: str
    dimension: Optional[str] = None
    limit: int = 10


# ═══ LLM Call Placeholder ═════════════════════════════════

async def _llm_call(prompt: str) -> str:
    """Replace with actual LLM integration (DeepSeek, local model, etc.)"""
    import httpx
    # TODO: integrate with AIOS LLM routing
    return "[]"


# ═══ Endpoints ════════════════════════════════════════════

@router.post("/add")
async def add_conversation(req: AddRequest):
    """
    Add conversation → full pipeline:
    extract tags → dispatch to modules → archive → generate insight → verify
    """
    # Auto-create user db if not exists
    if req.user_id not in list_users():
        create_user_db(req.user_id)

    with user_db(req.user_id) as db:
        # Save episode
        episode = Episode(
            id=str(uuid.uuid4()),
            user_id=req.user_id,
            title=req.title or _generate_title(req.messages),
            messages=[m.dict() for m in req.messages],
            pipeline_status='created',
        )
        db.add(episode)
        db.flush()

        # Run full pipeline
        pipeline = Pipeline(_llm_call)
        await pipeline.run(db, episode.id, req.user_id)

        # Refresh episode
        db.refresh(episode)

        return {
            "episode_id": episode.id,
            "pipeline_status": episode.pipeline_status,
            "summary": episode.summary,
        }


@router.get("/portrait/{user_id}")
async def get_portrait(user_id: str):
    """Get computed user portrait."""
    if user_id not in list_users():
        raise HTTPException(status_code=404, detail="User not found")

    with user_db(user_id) as db:
        portrait = generate_portrait(db, user_id)
        return {
            "user_id": user_id,
            "top_topics": portrait.top_topics or [],
            "top_entities": portrait.top_entities or [],
            "top_intents": portrait.top_intents or [],
            "top_emotions": portrait.top_emotions or [],
            "top_modules": portrait.top_modules or [],
            "strong_links": portrait.strong_links or [],
            "system_context": portrait.system_context or "",
            "total_episodes": portrait.total_episodes or 0,
        }


@router.post("/search")
async def search_memories(req: SearchRequest):
    """Search tags by text, optionally filtered by dimension."""
    if req.user_id not in list_users():
        raise HTTPException(status_code=404, detail="User not found")

    with user_db(req.user_id) as db:
        query = db.query(Tag).filter(Tag.user_id == req.user_id)
        if req.dimension:
            query = query.filter(Tag.dimension == req.dimension)
        query = query.filter(Tag.name.contains(req.query.lower()))
        tags = query.order_by(Tag.weight.desc()).limit(req.limit).all()

        return [
            {
                "id": t.id, "name": t.name, "dimension": t.dimension,
                "weight": round(t.weight, 4), "occurrences": t.total_occurrences,
                "last_seen": t.last_seen_at.isoformat() if t.last_seen_at else None,
            }
            for t in tags
        ]


@router.get("/tags/{user_id}")
async def list_tags(
    user_id: str,
    dimension: Optional[str] = None,
    min_weight: float = 0.0,
    limit: int = 50,
):
    """List all tags for a user, sorted by weight."""
    if user_id not in list_users():
        raise HTTPException(status_code=404, detail="User not found")

    with user_db(user_id) as db:
        query = db.query(Tag).filter(
            Tag.user_id == user_id, Tag.weight >= min_weight
        )
        if dimension:
            query = query.filter(Tag.dimension == dimension)
        tags = query.order_by(Tag.weight.desc()).limit(limit).all()

        return [
            {
                "id": t.id, "name": t.name, "dimension": t.dimension,
                "weight": round(t.weight, 4), "occurrences": t.total_occurrences,
                "stability": round(t.stability, 2),
                "last_seen": t.last_seen_at.isoformat() if t.last_seen_at else None,
            }
            for t in tags
        ]


@router.get("/links/{user_id}")
async def list_links(user_id: str, min_strength: float = 0.1, limit: int = 50):
    """List tag associations."""
    if user_id not in list_users():
        raise HTTPException(status_code=404, detail="User not found")

    with user_db(user_id) as db:
        links = db.query(TagLink).filter(
            TagLink.user_id == user_id,
            TagLink.strength >= min_strength,
            TagLink.expired_at.is_(None),
        ).order_by(TagLink.strength.desc()).limit(limit).all()

        result = []
        for link in links:
            tag_a = db.query(Tag).get(link.tag_a_id)
            tag_b = db.query(Tag).get(link.tag_b_id)
            if tag_a and tag_b:
                result.append({
                    "a": tag_a.name, "a_dim": tag_a.dimension,
                    "b": tag_b.name, "b_dim": tag_b.dimension,
                    "strength": round(link.strength, 4),
                    "count": link.co_occurrence_count,
                })
        return result


@router.get("/events/{user_id}")
async def list_events(user_id: str, limit: int = 20):
    """List recent events for a user."""
    if user_id not in list_users():
        raise HTTPException(status_code=404, detail="User not found")

    with user_db(user_id) as db:
        events = db.query(Event).filter(
            Event.user_id == user_id
        ).order_by(Event.created_at.desc()).limit(limit).all()

        return [
            {
                "id": e.id, "type": e.event_type, "severity": e.severity,
                "message": e.message, "payload": e.payload,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]


@router.get("/insights/{user_id}")
async def list_insights(user_id: str, limit: int = 10):
    """List generated insights for a user."""
    if user_id not in list_users():
        raise HTTPException(status_code=404, detail="User not found")

    with user_db(user_id) as db:
        insights = db.query(Insight).filter(
            Insight.user_id == user_id
        ).order_by(Insight.created_at.desc()).limit(limit).all()

        return [
            {
                "id": i.id, "summary": i.summary,
                "changes": i.changes or [], "consistent": i.consistent,
                "inconsistency": i.inconsistency_notes,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in insights
        ]


@router.get("/context/{user_id}")
async def get_system_context(user_id: str):
    """Get pre-computed system context for LLM injection."""
    if user_id not in list_users():
        return {"context": "New user — no profile yet."}

    with user_db(user_id) as db:
        portrait = generate_portrait(db, user_id)
        return {"context": portrait.system_context or "New user — no profile yet."}


# ═══ Helpers ══════════════════════════════════════════════

def _generate_title(messages: List[Message]) -> str:
    for msg in messages:
        if msg.role == "user":
            content = msg.content[:80]
            return content + ("..." if len(msg.content) > 80 else "")
    return "Untitled conversation"
