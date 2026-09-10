"""
Memory Engine — Core Data Models
Combines Mem0's flat memory + Zep's temporal edges + our tag weight system.
"""

from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, DateTime,
    ForeignKey, JSON, Index, UniqueConstraint, func
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# ═══ Layer 1: Episodes (from Zep) ═════════════════════════

class Episode(Base):
    """Raw conversation session — lossless source of truth."""
    __tablename__ = "me_episodes"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(100), nullable=False, index=True)
    title = Column(String(500))
    messages = Column(JSON, default=[])       # Full message history
    summary = Column(Text)                     # LLM-generated summary
    token_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tags = relationship("EventTag", back_populates="episode", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_episode_user_time", "user_id", "created_at"),
    )


# ═══ Layer 2: Tags (our innovation) ═══════════════════════

class Tag(Base):
    """
    A multi-dimensional semantic tag.
    6 dimensions: topic, entity, intent, emotion, module, location
    """
    __tablename__ = "me_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    dimension = Column(String(50), nullable=False, index=True)
    # topic | entity | intent | emotion | module | location
    description = Column(Text)

    # Weight components
    total_occurrences = Column(Integer, default=0)
    last_seen_at = Column(DateTime)
    first_seen_at = Column(DateTime)
    stability = Column(Float, default=1.0)    # S in Ebbinghaus: R = e^(-t/S)
    weight = Column(Float, default=0.0)       # Computed final weight

    # For vector search
    embedding = Column(JSON)                  # Float array

    created_at = Column(DateTime, default=datetime.utcnow)

    events = relationship("EventTag", back_populates="tag")
    links_from = relationship("TagLink", foreign_keys="TagLink.tag_a_id", back_populates="tag_a")
    links_to = relationship("TagLink", foreign_keys="TagLink.tag_b_id", back_populates="tag_b")

    __table_args__ = (
        UniqueConstraint("user_id", "name", "dimension", name="uq_user_tag_dim"),
        Index("idx_tag_weight", "user_id", "weight"),
        Index("idx_tag_dim_weight", "user_id", "dimension", "weight"),
    )


class EventTag(Base):
    """
    Links a tag to an episode. Each link = one event record.
    This is the fundamental unit of observation.
    """
    __tablename__ = "me_event_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_id = Column(String(36), ForeignKey("me_episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    tag_id = Column(Integer, ForeignKey("me_tags.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(100), nullable=False, index=True)

    # Context
    excerpt = Column(Text)          # The sentence where tag appeared
    confidence = Column(Float, default=1.0)
    position = Column(Integer)      # Message index in conversation

    # Dispatch to modules
    dispatched = Column(Boolean, default=False)
    dispatch_target = Column(String(100))   # e.g. "crm.contacts"
    dispatch_id = Column(String(100))       # ID in target module

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    episode = relationship("Episode", back_populates="tags")
    tag = relationship("Tag", back_populates="events")

    __table_args__ = (
        Index("idx_event_tag_time", "tag_id", "created_at"),
        Index("idx_event_user_tag", "user_id", "tag_id", "created_at"),
    )


# ═══ Layer 3: Tag Associations (from Zep edges) ═══════════

class TagLink(Base):
    """
    Co-occurrence relationship between tags.
    Bi-temporal: valid_at = when relationship started,
                 invalid_at = when it ended (null = still active).
    """
    __tablename__ = "me_tag_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)
    tag_a_id = Column(Integer, ForeignKey("me_tags.id", ondelete="CASCADE"), nullable=False)
    tag_b_id = Column(Integer, ForeignKey("me_tags.id", ondelete="CASCADE"), nullable=False)

    # Stats
    co_occurrence_count = Column(Integer, default=1)
    strength = Column(Float, default=0.0)    # Jaccard similarity

    # Bi-temporal (from Zep)
    valid_at = Column(DateTime)              # When relationship became true
    invalid_at = Column(DateTime)            # When relationship stopped being true
    expired_at = Column(DateTime)            # When system marked it inactive

    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)

    tag_a = relationship("Tag", foreign_keys=[tag_a_id], back_populates="links_from")
    tag_b = relationship("Tag", foreign_keys=[tag_b_id], back_populates="links_to")

    __table_args__ = (
        UniqueConstraint("tag_a_id", "tag_b_id", name="uq_tag_pair"),
        Index("idx_link_strength", "user_id", "strength"),
        Index("idx_link_active", "user_id", "expired_at"),
    )


# ═══ Layer 4: User Portrait (computed) ════════════════════

class UserPortrait(Base):
    """Computed user profile — updated periodically from tag weights."""
    __tablename__ = "me_portraits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, unique=True)

    # Top tags by dimension
    top_topics = Column(JSON, default=[])
    top_entities = Column(JSON, default=[])
    top_intents = Column(JSON, default=[])
    top_emotions = Column(JSON, default=[])
    top_modules = Column(JSON, default=[])
    top_locations = Column(JSON, default=[])

    # Strong associations
    strong_links = Column(JSON, default=[])  # [{a, b, strength, count}]

    # Derived
    occupation_guess = Column(String(200))
    language = Column(String(50))
    expertise = Column(JSON, default=[])

    # Pre-computed context for LLM
    system_context = Column(Text)

    # Stats
    total_episodes = Column(Integer, default=0)
    total_messages = Column(Integer, default=0)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
