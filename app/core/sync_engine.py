"""
Memory Engine — Sync Engine
SQLite → PostgreSQL distilled sync.

Only syncs high-value data:
- Portraits (user profiles)
- Top tags (weight > threshold)
- Insights (generated summaries)
- Events log (for monitoring)
- Sync status tracking
"""

import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, Text, DateTime, JSON, Index
from sqlalchemy.orm import declarative_base, sessionmaker
from contextlib import contextmanager

CentralBase = declarative_base()


# ═══ Central PostgreSQL Schema (Distilled Only) ═══════════

class CentralPortrait(CentralBase):
    """User portrait synced from SQLite."""
    __tablename__ = "me_portraits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, unique=True)

    top_topics = Column(JSON, default=[])
    top_entities = Column(JSON, default=[])
    top_intents = Column(JSON, default=[])
    top_emotions = Column(JSON, default=[])
    top_modules = Column(JSON, default=[])
    strong_links = Column(JSON, default=[])

    system_context = Column(Text)
    total_episodes = Column(Integer, default=0)

    synced_at = Column(DateTime, default=datetime.utcnow)


class CentralTopTag(CentralBase):
    """High-weight tags synced from SQLite."""
    __tablename__ = "me_top_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)
    tag_name = Column(String(200), nullable=False)
    dimension = Column(String(50), nullable=False)
    weight = Column(Float, default=0.0)
    occurrences = Column(Integer, default=0)
    last_seen_at = Column(DateTime)

    synced_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_central_tag_user_weight", "user_id", "weight"),
    )


class CentralInsight(CentralBase):
    """Insights synced from SQLite."""
    __tablename__ = "me_insights"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(100), nullable=False, index=True)
    episode_id = Column(String(36))

    summary = Column(Text)
    changes = Column(JSON, default=[])
    consistent = Column(Boolean)

    created_at = Column(DateTime)
    synced_at = Column(DateTime, default=datetime.utcnow)


class CentralEvent(CentralBase):
    """Event log synced from SQLite."""
    __tablename__ = "me_events_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)
    severity = Column(String(20))
    message = Column(Text)
    payload = Column(JSON)

    created_at = Column(DateTime)
    synced_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_central_event_type_time", "user_id", "event_type"),
    )


class SyncStatus(CentralBase):
    """Tracks last sync time per user."""
    __tablename__ = "me_sync_status"

    user_id = Column(String(100), primary_key=True)
    last_sync_at = Column(DateTime)
    last_episode_id = Column(String(36))
    episodes_synced = Column(Integer, default=0)
    tags_synced = Column(Integer, default=0)
    insights_synced = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    last_error = Column(Text)


# ═══ Sync Engine ══════════════════════════════════════════

class SyncEngine:
    """
    Syncs distilled data from per-user SQLite to central PostgreSQL.
    Only syncs high-value data (weight > threshold).
    """

    def __init__(self, central_db_url: str = None, weight_threshold: float = 0.1):
        self.central_url = central_db_url or os.getenv(
            "ME_CENTRAL_DB", "postgresql://localhost/memory_engine_central"
        )
        self.weight_threshold = weight_threshold
        self._central_engine = None
        self._CentralSession = None

    @property
    def central_engine(self):
        if self._central_engine is None:
            self._central_engine = create_engine(self.central_url, echo=False)
            CentralBase.metadata.create_all(bind=self._central_engine)
            self._CentralSession = sessionmaker(bind=self._central_engine)
        return self._central_engine

    @contextmanager
    def central_session(self):
        session = self._CentralSession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def sync_user(self, user_id: str, sqlite_session) -> dict:
        """
        Sync one user's distilled data from SQLite to PostgreSQL.
        Returns sync stats.
        """
        stats = {'portraits': 0, 'tags': 0, 'insights': 0, 'events': 0, 'errors': 0}

        with self.central_session() as central:
            # Get last sync time
            status = central.query(SyncStatus).get(user_id)
            since = status.last_sync_at if status else datetime(2020, 1, 1)

            # 1. Sync portrait
            try:
                self._sync_portrait(central, sqlite_session, user_id)
                stats['portraits'] = 1
            except Exception as e:
                stats['errors'] += 1

            # 2. Sync top tags (weight > threshold)
            try:
                count = self._sync_top_tags(central, sqlite_session, user_id, since)
                stats['tags'] = count
            except Exception as e:
                stats['errors'] += 1

            # 3. Sync insights
            try:
                count = self._sync_insights(central, sqlite_session, user_id, since)
                stats['insights'] = count
            except Exception as e:
                stats['errors'] += 1

            # 4. Sync events
            try:
                count = self._sync_events(central, sqlite_session, user_id, since)
                stats['events'] = count
            except Exception as e:
                stats['errors'] += 1

            # Update sync status
            if status:
                status.last_sync_at = datetime.utcnow()
                status.episodes_synced += stats['portraits']
                status.tags_synced += stats['tags']
                status.insights_synced += stats['insights']
                status.errors += stats['errors']
            else:
                central.add(SyncStatus(
                    user_id=user_id,
                    last_sync_at=datetime.utcnow(),
                    episodes_synced=stats['portraits'],
                    tags_synced=stats['tags'],
                    insights_synced=stats['insights'],
                    errors=stats['errors'],
                ))

        return stats

    def _sync_portrait(self, central, sqlite, user_id):
        """Sync user portrait."""
        from app.models.schema import UserPortrait
        portrait = sqlite.query(UserPortrait).filter(UserPortrait.user_id == user_id).first()
        if not portrait:
            return

        existing = central.query(CentralPortrait).filter(
            CentralPortrait.user_id == user_id
        ).first()

        if existing:
            existing.top_topics = portrait.top_topics
            existing.top_entities = portrait.top_entities
            existing.top_intents = portrait.top_intents
            existing.top_emotions = portrait.top_emotions
            existing.top_modules = portrait.top_modules
            existing.strong_links = portrait.strong_links
            existing.system_context = portrait.system_context
            existing.total_episodes = portrait.total_episodes
            existing.synced_at = datetime.utcnow()
        else:
            central.add(CentralPortrait(
                user_id=user_id,
                top_topics=portrait.top_topics,
                top_entities=portrait.top_entities,
                top_intents=portrait.top_intents,
                top_emotions=portrait.top_emotions,
                top_modules=portrait.top_modules,
                strong_links=portrait.strong_links,
                system_context=portrait.system_context,
                total_episodes=portrait.total_episodes,
            ))

    def _sync_top_tags(self, central, sqlite, user_id, since) -> int:
        """Sync tags with weight above threshold."""
        from app.models.schema import Tag
        tags = sqlite.query(Tag).filter(
            Tag.user_id == user_id,
            Tag.weight >= self.weight_threshold,
            Tag.last_seen_at >= since,
        ).all()

        count = 0
        for tag in tags:
            existing = central.query(CentralTopTag).filter(
                CentralTopTag.user_id == user_id,
                CentralTopTag.tag_name == tag.name,
            ).first()

            if existing:
                existing.weight = tag.weight
                existing.occurrences = tag.total_occurrences
                existing.last_seen_at = tag.last_seen_at
                existing.synced_at = datetime.utcnow()
            else:
                central.add(CentralTopTag(
                    user_id=user_id,
                    tag_name=tag.name,
                    dimension=tag.dimension,
                    weight=tag.weight,
                    occurrences=tag.total_occurrences,
                    last_seen_at=tag.last_seen_at,
                ))
            count += 1

        return count

    def _sync_insights(self, central, sqlite, user_id, since) -> int:
        """Sync new insights."""
        from app.models.schema import Insight
        insights = sqlite.query(Insight).filter(
            Insight.user_id == user_id,
            Insight.created_at >= since,
            Insight.synced_to_central == False,
        ).all()

        count = 0
        for insight in insights:
            if not central.query(CentralInsight).get(insight.id):
                central.add(CentralInsight(
                    id=insight.id,
                    user_id=user_id,
                    episode_id=insight.episode_id,
                    summary=insight.summary,
                    changes=insight.changes,
                    consistent=insight.consistent,
                    created_at=insight.created_at,
                ))
                insight.synced_to_central = True
                count += 1

        return count

    def _sync_events(self, central, sqlite, user_id, since) -> int:
        """Sync new events."""
        from app.models.schema import Event
        events = sqlite.query(Event).filter(
            Event.user_id == user_id,
            Event.created_at >= since,
            Event.dispatched_to_central == False,
        ).all()

        count = 0
        for event in events:
            central.add(CentralEvent(
                user_id=user_id,
                event_type=event.event_type,
                severity=event.severity,
                message=event.message,
                payload=event.payload,
                created_at=event.created_at,
            ))
            event.dispatched_to_central = True
            count += 1

        return count

    def sync_all_users(self, user_ids: list, session_factory) -> dict:
        """Sync all users. Called by background job."""
        results = {}
        for user_id in user_ids:
            try:
                session = session_factory(user_id)
                results[user_id] = self.sync_user(user_id, session)
                session.close()
            except Exception as e:
                results[user_id] = {'error': str(e)}
        return results
