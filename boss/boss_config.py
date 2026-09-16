from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from boss.boss_script import BossScript

# Sentinel player ID used for ALL bosses in the Player table.
# Must match the row seeded by scripts/seed_boss.py.
BOSS_PLAYER_ID = -1

# Root directory for all boss clip assets.
BOSS_CLIPS_ROOT = Path(__file__).parent.parent / "assets" / "boss_clips"

# Mapping from tier name → subfolder name under each boss's clip directory.
TIER_FOLDER: dict[str, str] = {
    "Normal": "normal",
    "Medium": "medium",
    "Absolute": "absolute",
    "Over-Absolute": "over_absolute",
}


@dataclass
class BossConfig:
    """Configuration for a single boss.

    Attributes
    ----------
    slug:
        Internal identifier used to look up this boss (e.g. "zeke").
    display_name:
        Human-readable name shown in Discord messages.
    hp:
        Starting HP for this boss. Can be any positive integer.
    character_id:
        The ``character_id`` of the Character row in the DB that represents
        this boss. Seeded by scripts/seed_boss.py.
    never_defends:
        When True the boss AI will never submit a defense action, it only
        attacks. The combat engine handles this naturally (no action = full
        damage received). Per-boss so future bosses can defend if desired.
    attack_weights:
        Probability weights for tier selection when the boss attacks.
        Keys must match TIER_FOLDER keys. Values are relative weights
        (they do NOT need to sum to 1 — random.choices handles normalisation).
        Example: {"Normal": 4, "Medium": 3, "Absolute": 2, "Over-Absolute": 1}
    """

    slug: str
    display_name: str
    hp: int
    character_id: int
    never_defends: bool = True
    attack_weights: dict[str, int] = field(
        default_factory=lambda: {
            "Normal": 4,
            "Medium": 3,
            "Absolute": 2,
            "Over-Absolute": 1,
        }
    )
    script_class: type[BossScript] | None = None

    def get_script(self) -> BossScript:
        """Return an instance of this boss's script, falling back to the base class."""
        from boss.boss_script import BossScript as _Base
        cls = self.script_class if self.script_class is not None else _Base
        return cls()

    @property
    def clips_dir(self) -> Path:
        """Root clip directory for this boss (e.g. assets/boss_clips/zeke/)."""
        return BOSS_CLIPS_ROOT / self.slug

    def get_clips(self, tier: str) -> list[Path]:
        """Return all available clip files for a given tier.

        Returns an empty list if the folder doesn't exist or has no video files.
        """
        folder = self.clips_dir / "attacks" / TIER_FOLDER.get(tier, tier.lower())
        if not folder.exists():
            return []
        return [
            p for p in folder.iterdir()
            if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
        ]


# ---------------------------------------------------------------------------
# Registered bosses
# ---------------------------------------------------------------------------
# Add new bosses here. The character_id must match an existing row in your DB.
# Run scripts/seed_boss.py once to create the Player(-1) row and link the character.

from boss.scripts.zeke import ZekeScript

BOSSES: dict[str, BossConfig] = {
    "zeke": BossConfig(
        slug="zeke",
        display_name="Zeke",
        hp=10,
        character_id=142314,
        never_defends=True,
        attack_weights={
            "Normal": 4,
            "Medium": 3,
            "Absolute": 2,
            "Over-Absolute": 1,
        },
        script_class=ZekeScript,
    ),
}
