"""
Memory Engine — Main API Router
FastAPI endpoints for the memory engine.
"""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.schema import Episode, Tag, EventTag, TagLink, UserPortrait
from app.core.weight_engine import recalculate_all_weights, generate_portrait
from app.services.extraction import extract_tags, process_extracted_tags

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

class PortraitResponse(BaseModel):
    user_id: str
    top_topics: list
    top_entities: list
    top_intents: list
    top_emotions: list
    top_modules: list
    system_context: str
    total_episodes: int

class TagResponse(BaseModel):
    id: int
    name: str
    dimension: str
    weight: float
    total_occurrences: int
    last_seen_at: Optional[datetime]


# ═══ Endpoints ════════════════════════════════════════════

@router.post("/add")
async def add_conversation(req: AddRequest, db: Session = Depends()):
    """
    Add a conversation and extract tags.
    This is the main entry point — call after each conversation.
    """
    # 1. Save episode
    episode = Episode(
        id=str(__import__('uuid').uuid4()),
        user_id=req.user_id,
        title=req.title or _generate_title(req.messages),
        messages=[m.dict() for m in req.messages],
        created_at=datetime.utcnow(),
    )
    db.add(episode)
    db.flush()

    # 2. Extract tags via LLM
    extracted = await extract_tags(
        db, req.user_id, [m.dict() for m in req.messages], _llm_call
    )

    # 3. Process tags (create/update tags, events, associations)
    events, links = process_extracted_tags(
        db, req.user_id, episode.id, extracted
    )

    # 4. Recalculate weights
    recalculate_all_weights(db, req.user_id)

    return {
        "episode_id": episode.id,
        "tags_extracted": len(events),
        "new_links": len(links),
    }


@router.get("/portrait/{user_id}", response_model=PortraitResponse)
async def get_portrait(user_id: str, db: Session = Depends()):
    """Get computed user portrait."""
    portrait = generate_portrait(db, user_id)
    return portrait


@router.post("/search")
async def search_memories(req: SearchRequest, db: Session = Depends()):
    """Search tags by query, optionally filtered by dimension."""
    query = db.query(Tag).filter(Tag.user_id == req.user_id)

    if req.dimension:
        query = query.filter(Tag.dimension == req.dimension)

    # Simple text match (vector search comes later)
    query = query.filter(Tag.name.contains(req.query.lower()))
    tags = query.order_by(Tag.weight.desc()).limit(req.limit).all()

    return [
        {
            "id": t.id,
            "name": t.name,
            "dimension": t.dimension,
            "weight": t.weight,
            "occurrences": t.total_occurrences,
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
    db: Session = Depends(),
):
    """List all tags for a user, sorted by weight."""
    query = db.query(Tag).filter(
        Tag.user_id == user_id,
        Tag.weight >= min_weight,
    )
    if dimension:
        query = query.filter(Tag.dimension == dimension)

    tags = query.order_by(Tag.weight.desc()).limit(limit).all()

    return [
        TagResponse(
            id=t.id, name=t.name, dimension=t.dimension,
            weight=t.weight, total_occurrences=t.total_occurrences,
            last_seen_at=t.last_seen_at,
        )
        for t in tags
    ]


@router.get("/links/{user_id}")
async def list_links(
    user_id: str,
    min_strength: float = 0.1,
    limit: int = 50,
    db: Session = Depends(),
):
    """List tag associations for a user."""
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
                "strength": link.strength,
                "count": link.co_occurrence_count,
            })

    return result


@router.get("/context/{user_id}")
async def get_system_context(user_id: str, db: Session = Depends()):
    """Get pre-computed system context for LLM injection."""
    portrait = db.query(UserPortrait).filter(
        UserPortrait.user_id == user_id
    ).first()

    if not portrait:
        portrait = generate_portrait(db, user_id)

    return {"context": portrait.system_context or "New user — no profile yet."}


# ═══ Helpers ══════════════════════════════════════════════

def _generate_title(messages: List[Message]) -> str:
    """Generate a title from the first user message."""
    for msg in messages:
        if msg.role == "user":
            content = msg.content[:80]
            return content + ("..." if len(msg.content) > 80 else "")
    return "Untitled conversation"


async def _llm_call(prompt: str) -> str:
    """
    Placeholder LLM call — replace with your actual LLM integration.
    Should call DeepSeek or your local model.
    """
    # TODO: integrate with AIOS LLM routing
    import httpx
    # Example with DeepSeek:
    # async with httpx.AsyncClient() as client:
    #     resp = await client.post("http://localhost:8000/v1/chat/completions", ...)
    #     return resp.json()["choices"][0]["message"]["content"]
    return "[]"  # Placeholder
