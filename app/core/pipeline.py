"""
Memory Engine — Pipeline Engine
State machine: created → extracted → dispatched → archived → insighted → verified

Each state transition either succeeds or moves to 'inconsistent' for retry.
"""

import json
import uuid
from datetime import datetime
from typing import Optional, Dict, List, Callable, Awaitable
from sqlalchemy.orm import Session

from app.models.schema import (
    Episode, Tag, EventTag, TagLink, Event, Dispatch, Insight, Archive,
    PIPELINE_STATES
)
from app.core.weight_engine import (
    calculate_weight, update_stability, jaccard_similarity,
    recalculate_all_weights, generate_portrait
)
from app.core.llm_provider import get_llm


# ═══ Pipeline Orchestrator ════════════════════════════════

class Pipeline:
    """
    Drives an episode through the full pipeline:
    extract → dispatch → archive → insight → verify
    """

    def __init__(self, llm_call: Callable[[str], Awaitable[str]] = None):
        self.llm = llm_call or (lambda prompt: get_llm().call(prompt))

    async def run(self, db: Session, episode_id: str, user_id: str):
        """Run the full pipeline for an episode."""
        episode = db.query(Episode).get(episode_id)
        if not episode:
            return

        try:
            # Step 1: Extract tags
            await self._extract(db, episode, user_id)

            # Step 2: Dispatch to modules
            await self._dispatch(db, episode, user_id)

            # Step 3: Archive
            await self._archive(db, episode, user_id)

            # Step 4: Generate insight
            await self._insight(db, episode, user_id)

            # Step 5: Verify consistency
            await self._verify(db, episode, user_id)

            # Mark complete
            episode.pipeline_status = 'verified'
            episode.processed_at = datetime.utcnow()
            db.commit()

        except Exception as e:
            episode.pipeline_status = 'inconsistent'
            episode.pipeline_error = str(e)
            episode.pipeline_retries += 1
            db.commit()
            self._create_event(db, episode, user_id,
                event_type='pipeline_error',
                severity='warning',
                message=f'Pipeline error at {episode.pipeline_status}: {str(e)[:200]}',
                payload={'error': str(e), 'status': episode.pipeline_status}
            )


    # ─── Step 1: Extract Tags ─────────────────────────────

    async def _extract(self, db: Session, episode: Episode, user_id: str):
        episode.pipeline_status = 'extracting'
        db.commit()

        # Get existing tags for dedup context
        existing = db.query(Tag).filter(Tag.user_id == user_id).order_by(
            Tag.weight.desc()
        ).limit(50).all()

        # Build extraction prompt
        from app.services.extraction import (
            build_existing_tags_context, build_conversation_text,
            process_extracted_tags
        )

        prompt = EXTRACTION_PROMPT.format(
            existing_tags=build_existing_tags_context(existing),
            conversation_text=build_conversation_text(episode.messages),
        )

        # Call LLM
        response = await self.llm(prompt)

        # Parse
        try:
            extracted = json.loads(response)
        except json.JSONDecodeError:
            import re
            match = re.search(r'\[.*\]', response, re.DOTALL)
            extracted = json.loads(match.group()) if match else []

        # Process tags
        events, links = process_extracted_tags(db, user_id, episode.id, extracted)

        # Recalculate weights
        old_weights = {t.id: t.weight for t in db.query(Tag).filter(Tag.user_id == user_id).all()}
        recalculate_all_weights(db, user_id)

        # Detect significant weight changes
        for tag in db.query(Tag).filter(Tag.user_id == user_id).all():
            old = old_weights.get(tag.id, 0)
            if abs(tag.weight - old) > 0.1:
                self._create_event(db, episode, user_id,
                    event_type='tag_weight_changed',
                    severity='info' if tag.weight > old else 'warning',
                    message=f'Tag "{tag.name}" ({tag.dimension}) weight: {old:.3f} → {tag.weight:.3f}',
                    payload={'tag': tag.name, 'dimension': tag.dimension,
                             'old_weight': old, 'new_weight': tag.weight}
                )

        # Generate episode summary
        episode.summary = await self._generate_summary(episode.messages)
        episode.pipeline_status = 'extracted'


    # ─── Step 2: Star Schema Dispatch ─────────────────────

    async def _dispatch(self, db: Session, episode: Episode, user_id: str):
        episode.pipeline_status = 'dispatching'
        db.commit()

        # Get tags for this episode
        event_tags = db.query(EventTag).filter(
            EventTag.episode_id == episode.id
        ).all()

        tag_names = {}
        for et in event_tags:
            tag = db.query(Tag).get(et.tag_id)
            if tag:
                tag_names[tag.name] = tag.dimension

        # Apply dispatch rules
        dispatches = self._apply_dispatch_rules(tag_names, episode)

        # Dedup dispatches by module+action
        seen = set()
        for disp in dispatches:
            key = f"{disp['module']}:{disp['action']}"
            if key in seen:
                continue
            seen.add(key)

            dispatch = Dispatch(
                episode_id=episode.id,
                user_id=user_id,
                target_module=disp['module'],
                action=disp['action'],
                payload=disp['payload'],
            )
            db.add(dispatch)

            self._create_event(db, episode, user_id,
                event_type='dispatch_complete',
                severity='info',
                message=f"Dispatched to {disp['module']}: {disp['action']}",
                payload=disp
            )

        episode.pipeline_status = 'dispatched'

    def _apply_dispatch_rules(self, tags: Dict[str, str], episode: Episode) -> list:
        """Apply tag→module mapping rules. Dedup by module."""
        dispatches = []
        entities = [n for n, d in tags.items() if d == 'entity']
        topics = [n for n, d in tags.items() if d == 'topic']
        intents = [n for n, d in tags.items() if d == 'intent']

        # Entities → one CRM dispatch with all entities
        if entities:
            dispatches.append({
                'module': 'crm.contacts',
                'action': 'create',
                'payload': {'names': entities, 'source': 'memory_engine'}
            })

        # Topic + Intent → Module (one per module)
        if 'accounting' in topics or 'invoice' in topics or 'invoicing' in topics:
            dispatches.append({
                'module': 'accounting.invoices',
                'action': 'create',
                'payload': {'source': 'memory_engine', 'episode_id': episode.id}
            })

        if 'payroll' in topics or 'epf' in topics:
            dispatches.append({
                'module': 'payroll.payslips',
                'action': 'create',
                'payload': {'source': 'memory_engine'}
            })

        if 'hiring' in topics or 'employee' in topics:
            dispatches.append({
                'module': 'hr.employees',
                'action': 'create',
                'payload': {'source': 'memory_engine'}
            })

        # Always: notes
        dispatches.append({
            'module': 'notes.notes',
            'action': 'create',
            'payload': {
                'title': episode.title,
                'summary': episode.summary,
                'source': 'memory_engine',
                'tags': list(tags.keys()),
            }
        })

        return dispatches


    # ─── Step 3: Archive ──────────────────────────────────

    async def _archive(self, db: Session, episode: Episode, user_id: str):
        episode.pipeline_status = 'archiving'
        db.commit()

        # Compress conversation
        key_excerpts = []
        for msg in episode.messages:
            if msg.get('role') == 'user' and len(msg.get('content', '')) > 20:
                key_excerpts.append(msg['content'][:200])

        # Get current tags snapshot
        event_tags = db.query(EventTag).filter(EventTag.episode_id == episode.id).all()
        tags_snapshot = []
        for et in event_tags:
            tag = db.query(Tag).get(et.tag_id)
            if tag:
                tags_snapshot.append({
                    'name': tag.name, 'dimension': tag.dimension,
                    'weight': tag.weight, 'confidence': et.confidence
                })

        # Get dispatch snapshot
        dispatches = db.query(Dispatch).filter(Dispatch.episode_id == episode.id).all()
        dispatch_snapshot = [
            {'module': d.target_module, 'action': d.action, 'target_id': d.target_id}
            for d in dispatches
        ]

        # Calculate compression
        original_tokens = len(json.dumps(episode.messages))
        archive_summary = episode.summary or episode.title

        archive = Archive(
            id=str(uuid.uuid4()),
            episode_id=episode.id,
            user_id=user_id,
            title=episode.title,
            summary=archive_summary,
            key_excerpts=key_excerpts[:10],
            tags_snapshot=tags_snapshot,
            dispatch_snapshot=dispatch_snapshot,
            original_token_count=original_tokens,
            compressed_token_count=len(archive_summary or ''),
            compression_ratio=len(archive_summary or '') / max(original_tokens, 1),
        )
        db.add(archive)

        # Mark episode as archived
        episode.archived = True
        episode.archive_summary = archive_summary
        episode.pipeline_status = 'archived'


    # ─── Step 4: Generate Insight ─────────────────────────

    async def _insight(self, db: Session, episode: Episode, user_id: str):
        episode.pipeline_status = 'insighting'
        db.commit()

        # Get top tags
        top_tags = db.query(Tag).filter(
            Tag.user_id == user_id, Tag.weight > 0.05
        ).order_by(Tag.weight.desc()).limit(10).all()

        # Get recent events
        recent_events = db.query(Event).filter(
            Event.user_id == user_id
        ).order_by(Event.created_at.desc()).limit(5).all()

        # Build insight prompt
        prompt = INSIGHT_PROMPT.format(
            top_tags=', '.join([f"{t.name}({t.dimension}:{t.weight:.2f})" for t in top_tags]),
            recent_events='\n'.join([f"- {e.message}" for e in recent_events]),
            conversation_summary=episode.summary or episode.title,
        )

        response = await self.llm(prompt)

        # Parse JSON (handle markdown-wrapped responses)
        result = self._parse_json(response)
        if not result:
            result = {'summary': response[:500], 'changes': [], 'consistent': True}

        insight = Insight(
            id=str(uuid.uuid4()),
            user_id=user_id,
            episode_id=episode.id,
            summary=result.get('summary', ''),
            changes=result.get('changes', []),
            recommendations=result.get('recommendations', []),
        )
        db.add(insight)

        self._create_event(db, episode, user_id,
            event_type='insight_ready',
            severity='info',
            message=insight.summary[:200],
            payload={'insight_id': insight.id}
        )

        episode.pipeline_status = 'insighted'


    # ─── Step 5: Verify Consistency ───────────────────────

    async def _verify(self, db: Session, episode: Episode, user_id: str):
        episode.pipeline_status = 'verifying'
        db.commit()

        insight = db.query(Insight).filter(
            Insight.episode_id == episode.id
        ).order_by(Insight.created_at.desc()).first()

        if not insight:
            return

        # Get tags for comparison
        event_tags = db.query(EventTag).filter(EventTag.episode_id == episode.id).all()
        tag_list = []
        for et in event_tags:
            tag = db.query(Tag).get(et.tag_id)
            if tag:
                tag_list.append(f"{tag.name}({tag.dimension})")

        # Build verification prompt
        prompt = VERIFY_PROMPT.format(
            insight=insight.summary,
            tags=', '.join(tag_list),
            conversation_excerpt=episode.summary or str(episode.messages[:3]),
        )

        response = await self.llm(prompt)

        result = self._parse_json(response)
        if not result:
            result = {'match': True, 'issues': []}

        insight.consistent = result.get('match', True)
        insight.inconsistency_notes = json.dumps(result.get('issues', []))
        insight.verification_prompt = prompt[:500]
        insight.verification_response = response[:500]

        if not insight.consistent:
            self._create_event(db, episode, user_id,
                event_type='verification_failed',
                severity='action_required',
                message=f"Insight inconsistency: {result.get('issues', ['Unknown'])[0][:200]}",
                payload={'insight_id': insight.id, 'issues': result.get('issues', [])}
            )
            raise Exception(f"Verification failed: {result.get('issues', [])}")

        episode.pipeline_status = 'verified'


    # ─── Helpers ──────────────────────────────────────────

    def _create_event(self, db, episode, user_id, event_type, severity, message, payload=None):
        event = Event(
            episode_id=episode.id if episode else None,
            user_id=user_id,
            event_type=event_type,
            severity=severity,
            message=message,
            payload=payload or {},
        )
        db.add(event)

    async def _generate_summary(self, messages):
        if not messages:
            return ""
        text = '\n'.join([f"[{m.get('role','?')}]: {m.get('content','')[:100]}" for m in messages[:6]])
        try:
            response = await self.llm(f"Summarize this conversation in one sentence:\n{text}")
            return response[:300]
        except:
            return messages[0].get('content', '')[:200]

    def _parse_json(self, text: str):
        """Parse JSON from LLM response, handling markdown code blocks."""
        import re
        # Direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Extract from markdown code blocks
        for pattern in [r'```json\s*(.*?)\s*```', r'```\s*(.*?)\s*```', r'(\{.*\})', r'(\[.*\])']:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
        return None


# ═══ Prompts ══════════════════════════════════════════════

EXTRACTION_PROMPT = """Extract semantic tags from this conversation. 6 dimensions: topic, entity, intent, emotion, module, location.

EXISTING TAGS:
{existing_tags}

CONVERSATION:
{conversation_text}

Output JSON array:
[{{"name": "tag_name", "dimension": "topic", "confidence": 0.9, "excerpt": "relevant text", "existing_id": null}}]
"""

INSIGHT_PROMPT = """Generate a one-sentence insight from this user's data.

Top tags: {top_tags}
Recent events: {recent_events}
Conversation: {conversation_summary}

Output JSON:
{{"summary": "one sentence insight", "changes": [{{"tag": "name", "change": "description"}}], "consistent": true}}
"""

VERIFY_PROMPT = """Does this insight match the conversation?

Insight: {insight}
Tags extracted: {tags}
Conversation: {conversation_excerpt}

Output JSON:
{{"match": true/false, "issues": ["issue1"]}}
"""
