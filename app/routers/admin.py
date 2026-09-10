"""
Memory Engine — Admin Router
User management, sync control, global stats.
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import func
from app.core.database import list_users, user_db, create_user_db, delete_user_db
from app.core.sync_engine import SyncEngine
from app.models.schema import Episode, Tag, Event, Insight, Dispatch

router = APIRouter(prefix="/api/admin", tags=["admin"])

sync_engine = SyncEngine()


@router.get("/users")
async def list_all_users():
    """List all users with basic stats."""
    users = list_users()
    result = []
    for uid in users:
        with user_db(uid) as db:
            episode_count = db.query(func.count(Episode.id)).scalar() or 0
            tag_count = db.query(func.count(Tag.id)).scalar() or 0
            top_tag = db.query(Tag).order_by(Tag.weight.desc()).first()
            result.append({
                "user_id": uid,
                "episodes": episode_count,
                "tags": tag_count,
                "top_tag": {"name": top_tag.name, "weight": top_tag.weight} if top_tag else None,
            })
    return result


@router.post("/users/{user_id}")
async def create_user(user_id: str):
    """Create a new user database."""
    path = create_user_db(user_id)
    return {"user_id": user_id, "db_path": path, "status": "created"}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str):
    """Delete a user database (GDPR)."""
    if user_id not in list_users():
        raise HTTPException(status_code=404, detail="User not found")
    delete_user_db(user_id)
    return {"user_id": user_id, "status": "deleted"}


@router.post("/sync/{user_id}")
async def sync_user(user_id: str):
    """Sync one user's data to central PostgreSQL."""
    if user_id not in list_users():
        raise HTTPException(status_code=404, detail="User not found")

    from app.core.database import get_user_session
    session = get_user_session(user_id)
    try:
        stats = sync_engine.sync_user(user_id, session)
    finally:
        session.close()

    return {"user_id": user_id, "sync": stats}


@router.post("/sync-all")
async def sync_all():
    """Sync all users to central PostgreSQL."""
    from app.core.database import get_user_session
    results = sync_engine.sync_all_users(list_users(), get_user_session)
    return {"results": results, "total": len(results)}


@router.get("/stats")
async def global_stats():
    """Global statistics across all users."""
    users = list_users()
    total_episodes = 0
    total_tags = 0
    total_events = 0
    total_insights = 0

    for uid in users:
        with user_db(uid) as db:
            total_episodes += db.query(func.count(Episode.id)).scalar() or 0
            total_tags += db.query(func.count(Tag.id)).scalar() or 0
            total_events += db.query(func.count(Event.id)).scalar() or 0
            total_insights += db.query(func.count(Insight.id)).scalar() or 0

    return {
        "users": len(users),
        "total_episodes": total_episodes,
        "total_tags": total_tags,
        "total_events": total_events,
        "total_insights": total_insights,
    }
