"""
Memory Engine — SQLite Schema (Per-User Database)
Complete pipeline: extraction → dispatch → archive → insight → verify
"""

from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, DateTime,
    ForeignKey, JSON, Index, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# ═══ Pipeline States ══════════════════════════════════════

PIPELINE_STATES = [
    'created',        # Episode just saved
    'extracting',     # LLM extracting tags
    'extracted',      # Tags extracted, weights updated
    'dispatching',    # Star schema dispatch in progress
    'dispatched',     # Data written to AIOS modules
    'archiving',      # Compressing original conversation
    'archived',       # Original stored in archive
    'insighting',     # Generating insight
    'insighted',      # Insight generated
    'verifying',      # LLM checking consistency
    'verified',       # Insight matches distilled data ✓
    'inconsistent',   # Mismatch detected ✗ → re-extract
]


# ═══ Layer 1: Episodes (Conversations) ════════════════════

class Episode(Base):
    """Raw conversation with full pipeline state tracking."""
    __tablename__ = "me_episodes"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(100), nullable=False, index=True)
    title = Column(String(500))
    messages = Column(JSON, default=[])
    summary = Column(Text)
    token_count = Column(Integer, default=0)

    # Pipeline state
    pipeline_status = Column(String(30), default='created', index=True)
    pipeline_error = Column(Text)
    pipeline_retries = Column(Integer, default=0)
    processed_at = Column(DateTime)

    # Archive
    archived = Column(Boolean, default=False)
    archive_summary = Column(Text)      # Compressed version of conversation

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tags = relationship("EventTag", back_populates="episode", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="episode", cascade="all, delete-orphan")
    dispatches = relationship("Dispatch", back_populates="episode", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_episode_user_status", "user_id", "pipeline_status"),
        Index("idx_episode_user_time", "user_id", "created_at"),
    )


# ═══ Layer 2: Tags (Multi-Dimensional) ═══════════════════

class Tag(Base):
    """Semantic tag with Ebbinghaus weight tracking."""
    __tablename__ = "me_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    dimension = Column(String(50), nullable=False, index=True)
    # topic | entity | intent | emotion | module | location
    description = Column(Text)

    # Weight components (Ebbinghaus)
    total_occurrences = Column(Integer, default=0)
    last_seen_at = Column(DateTime)
    first_seen_at = Column(DateTime)
    stability = Column(Float, default=1.0)    # S in R = e^(-t/S)
    weight = Column(Float, default=0.0)       # Final computed weight
    weight_prev = Column(Float, default=0.0)  # Previous weight (for change detection)

    # For vector search (future)
    embedding = Column(JSON)

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
    """Links tag to episode — each occurrence is an event record."""
    __tablename__ = "me_event_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_id = Column(String(36), ForeignKey("me_episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    tag_id = Column(Integer, ForeignKey("me_tags.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(100), nullable=False, index=True)

    excerpt = Column(Text)
    confidence = Column(Float, default=1.0)
    position = Column(Integer)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    episode = relationship("Episode", back_populates="tags")
    tag = relationship("Tag", back_populates="events")

    __table_args__ = (
        Index("idx_event_tag_time", "tag_id", "created_at"),
        Index("idx_event_user_tag", "user_id", "tag_id", "created_at"),
    )


class TagLink(Base):
    """Co-occurrence with bi-temporal tracking (from Zep)."""
    __tablename__ = "me_tag_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)
    tag_a_id = Column(Integer, ForeignKey("me_tags.id", ondelete="CASCADE"), nullable=False)
    tag_b_id = Column(Integer, ForeignKey("me_tags.id", ondelete="CASCADE"), nullable=False)

    co_occurrence_count = Column(Integer, default=1)
    strength = Column(Float, default=0.0)

    # Bi-temporal
    valid_at = Column(DateTime)
    invalid_at = Column(DateTime)
    expired_at = Column(DateTime)

    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)

    tag_a = relationship("Tag", foreign_keys=[tag_a_id], back_populates="links_from")
    tag_b = relationship("Tag", foreign_keys=[tag_b_id], back_populates="links_to")

    __table_args__ = (
        UniqueConstraint("tag_a_id", "tag_b_id", name="uq_tag_pair"),
        Index("idx_link_active", "user_id", "expired_at"),
    )


# ═══ Layer 3: Events (Event Prompts Queue) ════════════════

class Event(Base):
    """
    Event prompt queue — what changed and why.
    These drive the pipeline: each event triggers the next step.
    """
    __tablename__ = "me_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_id = Column(String(36), ForeignKey("me_episodes.id", ondelete="CASCADE"), index=True)
    user_id = Column(String(100), nullable=False, index=True)

    event_type = Column(String(50), nullable=False, index=True)
    # tag_weight_changed | new_entity | new_association | contradiction
    # dispatch_complete | insight_ready | verification_failed

    severity = Column(String(20), default='info')
    # info | warning | action_required

    message = Column(Text, nullable=False)  # Human-readable prompt
    payload = Column(JSON, default={})      # Structured data

    # Sync status
    dispatched_to_central = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    episode = relationship("Episode", back_populates="events")

    __table_args__ = (
        Index("idx_event_type_time", "user_id", "event_type", "created_at"),
    )


# ═══ Layer 4: Dispatches (Star Schema Routing) ════════════

class Dispatch(Base):
    """
    Tracks what was written to which AIOS module.
    Star schema: one episode → many module dispatches.
    """
    __tablename__ = "me_dispatches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_id = Column(String(36), ForeignKey("me_episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(100), nullable=False, index=True)

    target_module = Column(String(100), nullable=False)
    # crm.contacts | crm.companies | accounting.invoices |
    # hr.employees | tasks.tasks | notes.notes | helpdesk.tickets

    target_id = Column(String(100))       # ID in target module
    action = Column(String(20))           # create | update | archive
    payload = Column(JSON)                # Data written

    # Verification
    verified = Column(Boolean, default=False)
    verified_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)

    episode = relationship("Episode", back_populates="dispatches")

    __table_args__ = (
        Index("idx_dispatch_module", "user_id", "target_module"),
        Index("idx_dispatch_episode", "episode_id"),
    )


# ═══ Layer 5: Insights (Generated Summaries) ══════════════

class Insight(Base):
    """
    Auto-generated insight from distilled data.
    LLM verifies consistency with original conversation.
    """
    __tablename__ = "me_insights"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(100), nullable=False, index=True)
    episode_id = Column(String(36), ForeignKey("me_episodes.id"), index=True)

    # Generated content
    summary = Column(Text, nullable=False)        # One-line summary
    changes = Column(JSON, default=[])            # [{tag, old_weight, new_weight, reason}]
    recommendations = Column(JSON, default=[])    # Suggested actions

    # Verification
    consistent = Column(Boolean)                  # LLM verified match
    inconsistency_notes = Column(Text)            # What didn't match
    verification_prompt = Column(Text)            # The prompt used for verification
    verification_response = Column(Text)          # LLM's raw response

    # Sync
    synced_to_central = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("idx_insight_user_time", "user_id", "created_at"),
    )


# ═══ Layer 6: Archive (Compressed Conversations) ══════════

class Archive(Base):
    """
    Completed conversations compressed for storage.
    Original messages replaced with summary + key excerpts.
    """
    __tablename__ = "me_archive"

    id = Column(String(36), primary_key=True)
    episode_id = Column(String(36), ForeignKey("me_episodes.id"), nullable=False, index=True)
    user_id = Column(String(100), nullable=False, index=True)

    # Compressed content
    title = Column(String(500))
    summary = Column(Text)                        # LLM-generated summary
    key_excerpts = Column(JSON, default=[])       # Important sentences
    tags_snapshot = Column(JSON, default=[])       # Tags at time of archive
    dispatch_snapshot = Column(JSON, default=[])   # What was dispatched

    # Stats
    original_token_count = Column(Integer)
    compressed_token_count = Column(Integer)
    compression_ratio = Column(Float)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_archive_user", "user_id", "created_at"),
    )


# ═══ User Config ══════════════════════════════════════════

class UserConfig(Base):
    """Per-user configuration stored in their SQLite."""
    __tablename__ = "me_config"

    key = Column(String(100), primary_key=True)
    value = Column(JSON)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
