# ⚔️ Anime Arena

A Discord bot for 1v1 anime character battles — decided by real video clips, not stats or RNG.

Claim any anime character, then fight other players using actual footage as your moves. Every clip carries a hidden tier. Your opponent never sees what you played until the damage lands.

> 🚧 Currently in active beta.

**🔗 Join the server:** https://discord.gg/H6t3M2AJu

---

## 🎮 What is this?

Anime Arena is built on one idea: combat should be judged by real footage and logical consistency, not a stat sheet.

- **Character claims** — reserve any anime character; ownership is exclusive per server
- **Clip-based combat** — attacks and defenses are real video clips you upload, each with a hidden tier
- **Tiered damage** — Normal / Medium / Absolute / Over-Absolute, 4 HP per player
- **Hidden information** — your opponent only sees your clip and the resulting damage, never your tier
- **Logical combat** — abilities must be properly set up to work; defenses have to make physical sense
- **Human referees** — disputes get resolved by a real person, not an algorithm (for now)
- **Boss battles** — custom boss encounters use scripted logic and fight-specific behavior
- **Media prep tools** — convert files to Discord-safe H.264 and burn subtitles into anime clips for battle use
- **Roleplay rewards** — staying in character is planned to feed directly into the points/ranking system

If you were ever part of the old-school Facebook anime battle groups, this is that same idea, rebuilt for Discord.

---

## 📋 Commands

### Getting started
| Command | Description |
|---|---|
| `/reserve` | Reserve a character — search by anime, then by character name |
| `/my_character` | Check your currently reserved character |
| `/players` | See all players and their reserved characters |
| `/player` | Look up any player's currently reserved character by ID or mention |
| `/challenge @player` | Send a match request — 60s to accept/decline |

### In a match
| Command | Description |
|---|---|
| `/attack` | Declare an attack — pick a tier, upload a clip |
| `/defend` | Declare a defense — pick a tier, upload a clip |
| `/custom` | Submit a flavor/RP clip — no damage effect |
| `/end_turn` | Lock in your actions and pass the turn |
| `/surrender` | Forfeit the match |
| `/object` | Pause the match and call a referee |

### Referee-only
| Command | Description |
|---|---|
| `/ref_view` | Inspect the current match state and snapshot history |
| `/ref_set_hp @player` | Manually correct a player's HP (0–4) |
| `/ref_declare_winner @winner` | Declare a winner and end the match |
| `/ref_boss_wins` | Declare the boss as winner in a boss fight |
| `/ref_resume` | Resume a paused match |
| `/ref_rollback` | Rewind the match to a previous turn snapshot |

---

## 🛠️ Tech Stack

- **Python** + [discord.py](https://github.com/Rapptz/discord.py)
- **PostgreSQL** (hosted on [Neon](https://neon.tech)) via [Tortoise ORM](https://tortoise.github.io/)
- **FFmpeg** for video conversion and subtitle burn-in
- **TQDM** for progress output in batch media scripts
- Character/anime data sourced from the Kaggle dataset: [MyAnimeList Jikan Database](https://www.kaggle.com/datasets/andreuvallhernndez/myanimelist-jikan?select=characters.csv)

## 📁 Project Structure

```
Anime Arena/
├── app/
│   ├── cogs/          # Discord commands and match/boss/referee logic
│   ├── bot.py         # Bot bootstrap and extension loader
│   └── main.py        # Entry point for the application
├── boss/              # Boss system, configs, state, AI, and scripted behaviors
├── core/              # Shared config values and environment setup
├── database/
│   ├── models/        # Anime, Player, Character models
│   └── database.py    # DB setup and session handling
├── services/          # Game logic services and match helpers
├── scripts/
│   ├── batch_nvenc_burn.py   # Batch subtitle burning with NVENC
│   ├── convert_to_h264.py    # Recursive H.264 conversion for Discord-safe files
│   ├── db_tables_creation.py # DB table generation
│   └── ...
├── assets/
│   └── boss_clips/    # Boss attack/defense/intros media assets
├── data/              # CSV datasets for anime/character data
├── Video demonstrations/ # Example video records and clips
├── config.env         # Local environment config
├── requirements.txt    # Python requirements
├── README.md          # Project overview and usage notes
└── .gitignore
```

---

## 🚀 Setup

1. Clone the repo
2. Create a virtual environment and install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Create `config.env` in the project root:
   ```
   DATABASE_URL=your_postgres_connection_string
   DISCORD_BOT_TOKEN=your_bot_token
   ```
4. Run the table creation script:
   ```
   python -m scripts.db_tables_creation
   ```
5. Start the bot:
   ```
   python -m app.main
   ```

---

## 🎬 Media processing utilities

These are helper commands used for preparing anime files before they are used in battle clips.

### Burn subtitles into a folder of videos

```bash
python -m scripts.batch_nvenc_burn "\\Desktop-l967ko4\e2\Media\Bleach\Anime\18"
```

Use a dry run first to preview what would be processed:

```bash
python -m scripts.batch_nvenc_burn "E:\test" --dry-run
```

This script recursively scans the chosen folder and subfolders, finds subtitle tracks, and burns them into exported MP4 files using NVENC. It prefers English subtitle streams and supports both ASS/SSA and SRT subtitle types.

### Convert videos to H.264

```bash
python -m scripts.convert_to_h264 "\\Desktop-l967ko4\e2\Media\Bleach\Anime"
```

This recursively converts videos in the target folder and its subfolders to H.264-compatible MP4 output files.

> These media scripts are intended for local media prep and should be run from the project root with FFmpeg available on PATH.

---

## 🗺️ Roadmap

- [x] Character claim system with fuzzy search
- [x] Turn-based combat with tiered damage
- [x] Boss battle system with scripted enemy logic
- [x] Referee dispute/objection flow
- [x] Player lookup command for any registered user
- [x] Batch media conversion and subtitle burn-in tooling
- [ ] Points and ranking system
- [ ] Roleplay scoring
- [ ] Character challenge system (contest a claimed character)
- [ ] AI referee, trained to log and resolve rulings

---

## 🤝 Contributing

This project is in early beta — issues and PRs are welcome, but expect things to shift as the core systems (points, rankings, RP scoring) get built out.

## 📜 License

TBD
