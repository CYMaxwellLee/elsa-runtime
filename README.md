# elsa-runtime

Core runtime implementation for Elsa System multi-agent AI assistant.

**Status:** Phase 0 — Foundation infra complete, building Tier 1 components

| Tracking | File |
|----------|------|
| Implementation progress | [PROGRESS.md](PROGRESS.md) |
| Session-by-session log | [SESSION-LOG.md](SESSION-LOG.md) |
| Design & architecture | [Elsa-System repo](https://github.com/CYMaxwellLee/Elsa-System) |
| Long-term backlog | [Elsa-System/meta/BACKLOG.md](https://github.com/CYMaxwellLee/Elsa-System/blob/main/meta/BACKLOG.md) |

## What's Working

- **ExecutionLogger** (`data/execution_log.py`) — append-only JSONL, stdlib-only, monthly rotation + gzip
- **Pydantic schemas** (`src/elsa_runtime/schemas/`) — TaskCard, Insight, Skill, Cost, Agent, Federation
- **Gate stubs** (`src/elsa_runtime/gates/`) — Gate 1/2 interface defined

## Installation

```bash
pip install -e ".[dev]"
```

## Testing

```bash
pytest                                    # unit tests
python3.11 data/execution_log.py          # ExecutionLogger smoke test
```

## Repo Structure

```
elsa-runtime/
├── data/               # Production data utilities (ExecutionLogger)
├── src/elsa_runtime/
│   ├── schemas/        # Pydantic models (interface contracts)
│   ├── gates/          # Gate 1/2 validation
│   ├── routing/        # Task + model routing
│   ├── knowledge/      # InsightStore, KG, skill bank
│   ├── cost/           # Token tracking, batch scheduling
│   ├── runtime/        # Agent runtime abstraction
│   └── federation/     # Multi-agent protocol
├── tests/
├── PROGRESS.md         # Implementation tracker
└── SESSION-LOG.md      # Development history
```

## Related Repos

| Repo | Purpose |
|------|---------|
| [Elsa-System](https://github.com/CYMaxwellLee/Elsa-System) | Design docs, architecture, agent personalities |
| elsa-runtime (this) | Implementation code |
| elsa-deploy (future) | Deployment configs (Phase 2+) |
