"""
Memory Engine — Tag Extraction Service
LLM-powered multi-dimensional tag extraction from conversations.

Combines:
  - Mem0's single-pass ADD-only extraction
  - Zep's structured Pydantic output
  - Our 6-dimension tag taxonomy
"""

import json
import hashlib
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session

from app.models.schema import Tag, EventTag, TagLink, Episode


# ═══ Tag Dimensions ═══════════════════════════════════════

TAG_DIMENSIONS = {
    "topic": "Subject matter discussed (accounting, tax, hiring, pricing)",
    "entity": "Named things (people, companies, products, locations)",
    "intent": "What user wanted to do (create_invoice, check_status, learn)",
    "emotion": "User's apparent mood (frustrated, curious, urgent, satisfied)",
    "module": "Which system module was relevant (finance, crm, operations)",
    "location": "Geographic reference (malaysia, singapore, kuala_lumpur)",
}


# ═══ LLM Extraction Prompt ═══════════════════════════════

EXTRACTION_PROMPT = """You are a semantic tag extractor for a business workspace AI.

Analyze this conversation and extract tags in 6 dimensions.

RULES:
1. Extract 3-10 tags per conversation
2. Each tag MUST have a dimension from: topic, entity, intent, emotion, module, location
3. Tags must be lowercase, underscore_separated
4. Be specific: "sarah_chen" not "person", "digital_scale" not "company"
5. Include confidence (0.0-1.0) and the relevant excerpt
6. If the same concept appears multiple times, extract it once with highest confidence
7. Detect the language and extract in the SAME language

EXISTING TAGS (avoid duplicates, reference these IDs):
{existing_tags}

CONVERSATION:
{conversation_text}

OUTPUT FORMAT (JSON array):
[
  {{"name": "tag_name", "dimension": "topic", "confidence": 0.95, "excerpt": "the relevant sentence", "existing_id": null}},
  {{"name": "sarah_chen", "dimension": "entity", "confidence": 0.90, "excerpt": "发给客户Sarah", "existing_id": 3}}
]

If a tag matches an existing tag, set existing_id to its ID.
If no match, set existing_id to null.
"""


def build_existing_tags_context(tags: List[Tag]) -> str:
    """Format existing tags for the LLM prompt."""
    if not tags:
        return "None yet — this is a new user."
    
    lines = []
    for t in tags[:50]:  # Limit to 50 to save tokens
        lines.append(f"  ID {t.id}: {t.name} ({t.dimension}, weight={t.weight:.3f})")
    return "\n".join(lines)


def build_conversation_text(messages: List[Dict]) -> str:
    """Format messages for the LLM prompt."""
    lines = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


# ═══ Extraction Pipeline ══════════════════════════════════

def hash_tag(name: str, dimension: str) -> str:
    """MD5 hash for fast dedup (from Mem0)."""
    return hashlib.md5(f"{name}:{dimension}".encode()).hexdigest()


async def extract_tags(
    db: Session,
    user_id: str,
    messages: List[Dict],
    llm_call,  # Async function: llm_call(prompt) -> str
) -> List[Dict]:
    """
    Extract tags from a conversation.
    
    Returns list of dicts: [{name, dimension, confidence, excerpt, existing_id}]
    """
    # Get existing tags for dedup context
    existing = db.query(Tag).filter(Tag.user_id == user_id).order_by(
        Tag.weight.desc()
    ).all()
    
    prompt = EXTRACTION_PROMPT.format(
        existing_tags=build_existing_tags_context(existing),
        conversation_text=build_conversation_text(messages),
    )
    
    # Call LLM
    response = await llm_call(prompt)
    
    # Parse JSON response
    try:
        extracted = json.loads(response)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        import re
        match = re.search(r'\[.*\]', response, re.DOTALL)
        if match:
            extracted = json.loads(match.group())
        else:
            return []
    
    return extracted


def process_extracted_tags(
    db: Session,
    user_id: str,
    episode_id: str,
    extracted: List[Dict],
) -> Tuple[List[EventTag], List[TagLink]]:
    """
    Process extracted tags: create/update tags, create event records,
    update associations.
    
    Returns: (event_tags, new_links)
    """
    now = datetime.utcnow()
    event_tags = []
    tag_ids_this_session = []
    
    for item in extracted:
        name = item["name"].lower().strip()
        dimension = item["dimension"]
        confidence = item.get("confidence", 0.8)
        excerpt = item.get("excerpt", "")
        existing_id = item.get("existing_id")
        
        # Find or create tag
        if existing_id:
            tag = db.query(Tag).get(existing_id)
        else:
            tag = db.query(Tag).filter(
                Tag.user_id == user_id,
                Tag.name == name,
                Tag.dimension == dimension,
            ).first()
        
        if tag:
            # Update existing tag
            tag.total_occurrences += 1
            tag.last_seen_at = now
            tag.stability = update_stability(tag.stability)
        else:
            # Create new tag
            tag = Tag(
                user_id=user_id,
                name=name,
                dimension=dimension,
                description=item.get("description"),
                total_occurrences=1,
                last_seen_at=now,
                first_seen_at=now,
                stability=1.0,
            )
            db.add(tag)
            db.flush()  # Get ID
        
        tag_ids_this_session.append(tag.id)
        
        # Create event record
        event = EventTag(
            episode_id=episode_id,
            tag_id=tag.id,
            user_id=user_id,
            excerpt=excerpt,
            confidence=confidence,
        )
        db.add(event)
        event_tags.append(event)
    
    # Update associations (co-occurrence)
    new_links = []
    for i, tag_a_id in enumerate(tag_ids_this_session):
        for tag_b_id in tag_ids_this_session[i+1:]:
            if tag_a_id == tag_b_id:
                continue
            
            # Ensure consistent ordering
            a_id = min(tag_a_id, tag_b_id)
            b_id = max(tag_a_id, tag_b_id)
            
            link = db.query(TagLink).filter(
                TagLink.tag_a_id == a_id,
                TagLink.tag_b_id == b_id,
            ).first()
            
            if link:
                link.co_occurrence_count += 1
                link.last_seen_at = now
                if link.expired_at:
                    # Reactivate expired link
                    link.expired_at = None
                    link.valid_at = now
            else:
                link = TagLink(
                    user_id=user_id,
                    tag_a_id=a_id,
                    tag_b_id=b_id,
                    co_occurrence_count=1,
                    valid_at=now,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                db.add(link)
                new_links.append(link)
    
    db.commit()
    return event_tags, new_links


def update_stability(current: float, increment: float = 1.0) -> float:
    """Each occurrence increases stability with diminishing returns."""
    if current <= 0:
        return 1.0
    import math
    boost = 1.0 / math.log2(current + 2)
    return current + boost
