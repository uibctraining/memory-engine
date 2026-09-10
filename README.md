# Memory Engine (ME)

**Tag Weight-Based User Profiling for AI Agents**

Combines Mem0's efficient memory extraction with Zep's temporal knowledge graph, plus a unique multi-dimensional tag weighting system.

## Architecture

```
Conversation → LLM Extract Tags → Event Record → Weight Calc → User Portrait
                    ↓                  ↓              ↓
              6 Dimensions      SessionTag      Ebbinghaus Decay
              topic/entity/     (timestamp +     weight = f(freq)
              intent/emotion/    excerpt +        × recency_decay
              module/location   confidence)       × consistency
```

## What Makes ME Different

| Feature | Mem0 | Zep | **Memory Engine** |
|---|---|---|---|
| Memory unit | Fact | Entity/Edge | **Tag (6 dimensions)** |
| Time model | Timestamp only | Bi-temporal | **Ebbinghaus decay** |
| Weight | None | Mention count | **freq × decay × consistency** |
| User portrait | None | Community summary | **Computed tag graph** |
| Dedup | Hash + LLM | LLM + cosine | **Hash + cosine + weight merge** |
| Storage | Vector DB | Graph DB | **SQLAlchemy (SQLite/PG)** |
| Retrieval | Semantic + BM25 | BM25 + BFS + RRF | **Semantic + weight + association** |

## Quick Start

```bash
cd memory-engine
pip install -r requirements.txt
python -m app.main
```

## API

```python
from memory_engine import MemoryEngine

me = MemoryEngine(db_url="sqlite:///memory.db")

# Add conversation
me.add("user_123", messages=[
    {"role": "user", "content": "帮我做发票给Sarah"},
    {"role": "assistant", "content": "好的，我来创建发票。"}
])

# Get user portrait
portrait = me.get_portrait("user_123")
# → {"top_topics": [...], "top_entities": [...], "system_context": "..."}

# Search memories
results = me.search("user_123", "Sarah的发票")
# → [{"tag": "sarah_chen", "weight": 0.85, "excerpt": "..."}]
```

## License

MIT
