"""
Memory Engine — Weight Engine
Ebbinghaus forgetting curve + frequency + consistency = tag weight.

Combines:
  - Mem0's hash dedup approach
  - Zep's bi-temporal tracking
  - Our Ebbinghaus-based weight formula
"""

import math
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.schema import Tag, EventTag, TagLink, Episode, UserPortrait


# ═══ Core Weight Formula ══════════════════════════════════

def ebbinghaus_retention(days_since_last: float, stability: float) -> float:
    """
    R = e^(-t/S)
    
    R = retention (how well we "remember" this tag)
    t = days since last occurrence
    S = stability (increases with each occurrence)
    
    After 1st occurrence (S=1):  R(1day) = 0.37, R(7days) = 0.0009
    After 5th occurrence (S=5):  R(1day) = 0.82, R(7days) = 0.25
    After 20th occurrence (S=20): R(1day) = 0.95, R(7days) = 0.70
    """
    if stability <= 0:
        stability = 1.0
    return math.exp(-days_since_last / stability)


def calculate_weight(
    total_occurrences: int,
    total_episodes: int,
    last_seen_at: Optional[datetime],
    first_seen_at: Optional[datetime],
    stability: float,
    now: Optional[datetime] = None,
) -> float:
    """
    Final weight = frequency × retention × consistency
    
    frequency:   occurrences / total_episodes (how often)
    retention:   ebbinghaus retention (how recent)
    consistency: spread of occurrences over time (not bursty)
    """
    if now is None:
        now = datetime.utcnow()
    if not last_seen_at:
        return 0.0

    # Frequency: what % of episodes mention this tag
    freq = min(total_occurrences / max(total_episodes, 1), 1.0)

    # Retention: Ebbinghaus decay
    days_since = max((now - last_seen_at).total_seconds() / 86400, 0)
    retention = ebbinghaus_retention(days_since, stability)

    # Consistency: how spread out are the occurrences
    if first_seen_at and last_seen_at:
        span_days = max((last_seen_at - first_seen_at).total_seconds() / 86400, 1)
        # More occurrences over more days = consistent
        # Burst: 10 occurrences in 1 day = consistency ~0.1
        # Steady: 10 occurrences over 100 days = consistency ~1.0
        raw_consistency = total_occurrences / span_days
        consistency = min(raw_consistency, 1.0)
    else:
        consistency = 0.5

    return round(freq * retention * consistency, 6)


def update_stability(current_stability: float, increment: float = 1.0) -> float:
    """
    Each occurrence increases stability.
    Stability grows logarithmically (diminishing returns).
    
    S_new = S_old + (1 / log2(S_old + 2))
    
    This means:
    - Early occurrences boost stability a lot
    - Later occurrences still help, but less
    """
    if current_stability <= 0:
        return 1.0
    boost = 1.0 / math.log2(current_stability + 2)
    return current_stability + boost


# ═══ Association Strength ═════════════════════════════════

def jaccard_similarity(count_a: int, count_b: int, count_both: int) -> float:
    """Jaccard index: |A ∩ B| / |A ∪ B|"""
    union = count_a + count_b - count_both
    if union <= 0:
        return 0.0
    return round(count_both / union, 6)


# ═══ Batch Update ═════════════════════════════════════════

def recalculate_all_weights(db: Session, user_id: str):
    """
    Recalculate weights for all tags and associations of a user.
    Run after each episode or as a background job.
    """
    total_episodes = db.query(func.count(Episode.id)).filter(
        Episode.user_id == user_id
    ).scalar() or 0

    # Update tag weights
    tags = db.query(Tag).filter(Tag.user_id == user_id).all()
    for tag in tags:
        tag.weight = calculate_weight(
            total_occurrences=tag.total_occurrences,
            total_episodes=total_episodes,
            last_seen_at=tag.last_seen_at,
            first_seen_at=tag.first_seen_at,
            stability=tag.stability,
        )

    # Update association strengths
    links = db.query(TagLink).filter(
        TagLink.user_id == user_id,
        TagLink.expired_at.is_(None),  # Only active links
    ).all()
    for link in links:
        tag_a = db.query(Tag).get(link.tag_a_id)
        tag_b = db.query(Tag).get(link.tag_b_id)
        if tag_a and tag_b:
            link.strength = jaccard_similarity(
                tag_a.total_occurrences,
                tag_b.total_occurrences,
                link.co_occurrence_count,
            )

    db.commit()


# ═══ Portrait Generation ══════════════════════════════════

def generate_portrait(db: Session, user_id: str) -> UserPortrait:
    """Compute user portrait from tag weights and associations."""
    dimensions = ["topic", "entity", "intent", "emotion", "module", "location"]
    portrait_data = {}

    for dim in dimensions:
        top = db.query(Tag).filter(
            Tag.user_id == user_id,
            Tag.dimension == dim,
            Tag.weight > 0.01,  # Minimum threshold
        ).order_by(Tag.weight.desc()).limit(10).all()
        portrait_data[dim] = [
            {"name": t.name, "weight": round(t.weight, 4), "occurrences": t.total_occurrences}
            for t in top
        ]

    # Strong associations
    links = db.query(TagLink).filter(
        TagLink.user_id == user_id,
        TagLink.strength > 0.3,
        TagLink.expired_at.is_(None),
    ).order_by(TagLink.strength.desc()).limit(20).all()

    strong_links = []
    for link in links:
        tag_a = db.query(Tag).get(link.tag_a_id)
        tag_b = db.query(Tag).get(link.tag_b_id)
        if tag_a and tag_b:
            strong_links.append({
                "a": tag_a.name, "a_dim": tag_a.dimension,
                "b": tag_b.name, "b_dim": tag_b.dimension,
                "strength": round(link.strength, 4),
                "count": link.co_occurrence_count,
            })

    # Session stats
    stats = db.query(
        func.count(Episode.id),
    ).filter(Episode.user_id == user_id).first()
    total = stats[0] or 0

    # Build system context
    topics = [t["name"] for t in portrait_data.get("topic", [])[:5]]
    entities = [t["name"] for t in portrait_data.get("entity", [])[:5]]
    intents = [t["name"] for t in portrait_data.get("intent", [])[:5]]
    emotions = [t["name"] for t in portrait_data.get("emotion", [])[:3]]

    context_parts = [f"User profile (from {total} conversations):"]
    if topics:
        context_parts.append(f"- Topics: {', '.join(topics)}")
    if entities:
        context_parts.append(f"- Key entities: {', '.join(entities)}")
    if intents:
        context_parts.append(f"- Common intents: {', '.join(intents)}")
    if emotions:
        context_parts.append(f"- Emotional patterns: {', '.join(emotions)}")
    if strong_links:
        pairs = [f"{l['a']}↔{l['b']}" for l in strong_links[:5]]
        context_parts.append(f"- Strong associations: {', '.join(pairs)}")

    system_context = "\n".join(context_parts)

    # Upsert
    portrait = db.query(UserPortrait).filter(UserPortrait.user_id == user_id).first()
    if not portrait:
        portrait = UserPortrait(user_id=user_id)
        db.add(portrait)

    portrait.top_topics = portrait_data.get("topic", [])
    portrait.top_entities = portrait_data.get("entity", [])
    portrait.top_intents = portrait_data.get("intent", [])
    portrait.top_emotions = portrait_data.get("emotion", [])
    portrait.top_modules = portrait_data.get("module", [])
    portrait.top_locations = portrait_data.get("location", [])
    portrait.strong_links = strong_links
    portrait.total_episodes = total
    portrait.system_context = system_context

    db.commit()
    return portrait
