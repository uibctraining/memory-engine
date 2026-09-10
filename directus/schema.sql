-- Memory Engine — Directus Schema Setup
-- Run this after Directus is installed and connected to PostgreSQL.
-- This creates the collections and relationships for the ME admin dashboard.

-- ═══ Collections (Tables) ════════════════════════════════

-- These tables already exist in PostgreSQL from sync_engine.py.
-- Directus auto-discovers them. We just need to configure the UI.

-- ═══ Directus Collection Metadata ════════════════════════

-- Mark ME tables as Directus collections
INSERT INTO directus_collections (collection, icon, note, color, singleton) VALUES
('me_portraits', 'person', 'User portraits synced from Memory Engine', '#5e6ad2', false),
('me_top_tags', 'label', 'High-weight tags for all users', '#22c55e', false),
('me_insights', 'lightbulb', 'Generated insights from conversations', '#e5a00d', false),
('me_events_log', 'event_note', 'Event log from all users', '#f97316', false),
('me_sync_status', 'sync', 'Sync status per user', '#62666d', false)
ON CONFLICT (collection) DO NOTHING;


-- ═══ Fields Configuration ════════════════════════════════

-- me_portraits fields
INSERT INTO directus_fields (collection, field, interface, display, display_options, sort) VALUES
('me_portraits', 'id', 'input', NULL, NULL, 1),
('me_portraits', 'user_id', 'input', 'labels', '{"format":"{{user_id}}"}', 2),
('me_portraits', 'top_topics', 'list', 'tags', NULL, 3),
('me_portraits', 'top_entities', 'list', 'tags', NULL, 4),
('me_portraits', 'top_intents', 'list', 'tags', NULL, 5),
('me_portraits', 'top_emotions', 'list', 'tags', NULL, 6),
('me_portraits', 'top_modules', 'list', 'tags', NULL, 7),
('me_portraits', 'strong_links', 'list', NULL, NULL, 8),
('me_portraits', 'system_context', 'input-multiline', NULL, NULL, 9),
('me_portraits', 'total_episodes', 'input', NULL, NULL, 10),
('me_portraits', 'synced_at', 'datetime', 'datetime', NULL, 11)
ON CONFLICT (collection, field) DO NOTHING;

-- me_top_tags fields
INSERT INTO directus_fields (collection, field, interface, display, display_options, sort) VALUES
('me_top_tags', 'id', 'input', NULL, NULL, 1),
('me_top_tags', 'user_id', 'input', NULL, NULL, 2),
('me_top_tags', 'tag_name', 'input', 'labels', '{"format":"{{tag_name}}"}', 3),
('me_top_tags', 'dimension', 'select-dropdown', 'labels', '{"choices":[{"text":"topic","value":"topic","color":"#5e6ad2"},{"text":"entity","value":"entity","color":"#22c55e"},{"text":"intent","value":"intent","color":"#e5a00d"},{"text":"emotion","value":"emotion","color":"#ef4444"},{"text":"module","value":"module","color":"#a855f7"},{"text":"location","value":"location","color":"#06b6d4"}]}', 4),
('me_top_tags', 'weight', 'input', NULL, NULL, 5),
('me_top_tags', 'occurrences', 'input', NULL, NULL, 6),
('me_top_tags', 'last_seen_at', 'datetime', 'datetime', NULL, 7),
('me_top_tags', 'synced_at', 'datetime', 'datetime', NULL, 8)
ON CONFLICT (collection, field) DO NOTHING;

-- me_insights fields
INSERT INTO directus_fields (collection, field, interface, display, display_options, sort) VALUES
('me_insights', 'id', 'input', NULL, NULL, 1),
('me_insights', 'user_id', 'input', NULL, NULL, 2),
('me_insights', 'episode_id', 'input', NULL, NULL, 3),
('me_insights', 'summary', 'input-multiline', NULL, NULL, 4),
('me_insights', 'changes', 'list', NULL, NULL, 5),
('me_insights', 'consistent', 'boolean', 'boolean', NULL, 6),
('me_insights', 'created_at', 'datetime', 'datetime', NULL, 7),
('me_insights', 'synced_at', 'datetime', 'datetime', NULL, 8)
ON CONFLICT (collection, field) DO NOTHING;

-- me_events_log fields
INSERT INTO directus_fields (collection, field, interface, display, display_options, sort) VALUES
('me_events_log', 'id', 'input', NULL, NULL, 1),
('me_events_log', 'user_id', 'input', NULL, NULL, 2),
('me_events_log', 'event_type', 'select-dropdown', 'labels', '{"choices":[{"text":"tag_weight_changed","value":"tag_weight_changed"},{"text":"new_entity","value":"new_entity"},{"text":"dispatch_complete","value":"dispatch_complete"},{"text":"insight_ready","value":"insight_ready"},{"text":"verification_failed","value":"verification_failed"},{"text":"pipeline_error","value":"pipeline_error"}]}', 3),
('me_events_log', 'severity', 'select-dropdown', 'labels', '{"choices":[{"text":"info","value":"info","color":"#5e6ad2"},{"text":"warning","value":"warning","color":"#e5a00d"},{"text":"action_required","value":"action_required","color":"#ef4444"}]}', 4),
('me_events_log', 'message', 'input-multiline', NULL, NULL, 5),
('me_events_log', 'payload', 'input-code', NULL, NULL, 6),
('me_events_log', 'created_at', 'datetime', 'datetime', NULL, 7),
('me_events_log', 'synced_at', 'datetime', 'datetime', NULL, 8)
ON CONFLICT (collection, field) DO NOTHING;


-- ═══ Insights Dashboard ══════════════════════════════════

-- Create dashboard
-- (Run via Directus API or UI — this is the config)

-- Dashboard: Memory Engine Analytics
-- Panels:
-- 1. Total Users (Metric) — COUNT me_portraits
-- 2. Total Tags (Metric) — COUNT me_top_tags
-- 3. Total Insights (Metric) — COUNT me_insights
-- 4. Tags by Dimension (Pie) — GROUP BY dimension
-- 5. Top Tags by Weight (List) — ORDER BY weight DESC LIMIT 20
-- 6. Recent Insights (List) — ORDER BY created_at DESC LIMIT 10
-- 7. Events by Type (Bar) — GROUP BY event_type
-- 8. Consistency Rate (Metric) — AVG consistent
