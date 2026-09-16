from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from boss.boss_state import BossState
    from boss.boss_config import BossConfig


def pick_random_clip(config: BossConfig, subfolder: str) -> Path | None:
    """Utility: return a random clip Path from a named subfolder under the boss's
    clip directory, or None if the folder doesn't exist / is empty.

    Parameters
    ----------
    config:
        The BossConfig of the current boss (provides clips_dir).
    subfolder:
        Subfolder name relative to the boss's clip root, e.g. "intros", "RP",
        or "defenses/Normal defense".
    """
    folder = config.clips_dir / subfolder
    if not folder.exists():
        return None
    clips = [
        p for p in folder.iterdir()
        if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
    ]
    return random.choice(clips) if clips else None


class BossScript:
    """Base class for per-boss AI behaviour.

    Subclass this and override only the methods you care about.
    All methods receive the live BossState so scripts can react to HP,
    turn count, or any other game condition.

    The default implementations reproduce the original random-attack,
    never-defend behaviour so any boss without a custom script still works.
    """

    # ------------------------------------------------------------------
    # Match lifecycle hooks
    # ------------------------------------------------------------------

    def on_match_start(self, state: BossState) -> Path | None:
        """Called once when the boss fight is created.

        Return a Path to an intro clip to post before the first turn,
        or None to skip the intro entirely.
        """
        return None

    # ------------------------------------------------------------------
    # Turn hooks
    # ------------------------------------------------------------------

    def on_turn_start(self, state: BossState) -> Path | None:
        """Called at the beginning of the boss's turn, before attack logic.

        Return a Path to a flavour/RP clip to post before the attack clip,
        or None to skip.
        """
        return None

    def should_defend(self, state: BossState) -> bool:
        """Return True if the boss should submit a defense action this turn.

        Default: never defend (original behaviour).
        """
        return False

    def pick_defense_tier(self, state: BossState) -> str:
        """Choose the defense tier when should_defend() returns True.

        Only called when should_defend() is True.
        Default: "Normal".
        """
        return "Normal"

    def pick_defense_clip(self, state: BossState, tier: str) -> Path | None:
        """Choose a specific defense clip for the given tier.

        Return a Path, or None to let the engine pick randomly from the
        standard defense folder for that tier.
        """
        return None

    def should_attack(self, state: BossState) -> bool:
        """Return True if the boss should attack this turn.

        Default: always attack. Override to make the boss skip attacks
        under certain conditions (e.g. a stun mechanic).
        """
        return True

    def pick_tier(self, state: BossState) -> str:
        """Choose the attack tier for this turn.

        Default: weighted random using the boss config's attack_weights,
        restricted to tiers that actually have clips available.
        """
        config = state.boss_config
        tiers = list(config.attack_weights.keys())
        weights = [config.attack_weights[t] for t in tiers]

        tiers_with_clips = [t for t in tiers if config.get_clips(t)]
        if tiers_with_clips:
            weights_with_clips = [config.attack_weights[t] for t in tiers_with_clips]
            return random.choices(tiers_with_clips, weights=weights_with_clips, k=1)[0]

        # No clips in any folder — fall back to raw weights so the game advances.
        return random.choices(tiers, weights=weights, k=1)[0]

    def pick_attack_clip(self, state: BossState, tier: str) -> Path | None:
        """Choose a specific attack clip for the given tier.

        Return a Path, or None to let the engine pick randomly from the
        standard tier folder.
        """
        return None

    def on_turn_end(self, state: BossState) -> Path | None:
        """Called after the boss's turn resolves (after end_turn succeeds).

        Return a Path to a flavour/RP clip to post after the turn embed,
        or None to skip.
        """
        return None

    # ------------------------------------------------------------------
    # Event hooks
    # ------------------------------------------------------------------

    def on_boss_hit(self, state: BossState, damage: int) -> Path | None:
        """Called when the boss takes damage.

        Return a Path to a reaction clip, or None to skip.
        """
        return None

    def on_player_hit(self, state: BossState, damage: int) -> Path | None:
        """Called when the human player takes damage from the boss.

        Return a Path to a taunting/RP clip, or None to skip.
        """
        return None
