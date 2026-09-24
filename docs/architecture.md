# Project architecture

This project is already organized into a few useful layers:

- app/cogs: interaction layer and Discord-facing UI
- services: orchestration and business logic
- database/models: persisted state
- boss: combat and boss behavior rules
- core: shared configuration and helpers

The future direction is to make the separation explicit as the project grows:

## Target grouping

### Interface / platform
- Discord bot entry points
- commands, embeds, forum threads, interactions

### Game domain
- combat rules
- turn progression
- match state
- boss rules
- win/loss logic

### AI / strategy
- boss decision making
- memory/context planning
- action selection
- agent orchestration

### Persistence
- players
- characters
- matches
- rankings
- historical state

### Progression / ranking
- points
- leaderboard
- ranks
- seasons
- match history

### Media / analysis
- clip storage
- video processing
- collection tooling
- search/analysis

This file is intentionally a structural scaffold only. Existing logic remains in place and is not moved yet.
