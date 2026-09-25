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

    async def prepare_intro(self, state: BossState) -> None:
        """Optional async hook for preparing an intro before it is posted."""
        return

    def take_turn_intro(self, state: BossState) -> Path | None:
        """Return an intro selected as part of a boss turn, if any."""
        return None

    # ------------------------------------------------------------------
    # Scripted turn plan (preferred over independent hooks)
    # ------------------------------------------------------------------

    async def prepare_turn(self, state: BossState) -> None:
        """Optional async hook called before plan_turn or the hook-based path.

        Use this in subclasses that need to do async work (e.g. an AI API call)
        before deciding what to do this turn. Store results on self or state.
        Default: no-op.
        """
        return

    def plan_turn(self, state: BossState) -> tuple[Path | None, str, Path | None, Path | None]:
        """Return a complete turn plan as (pre_clip, tier, attack_clip, post_clip).

        - pre_clip:    RP/flavour clip to post BEFORE the attack. None = skip.
        - tier:        Attack tier string ("Normal", "Medium", "Absolute", …).
        - attack_clip: Specific attack clip Path. None = pick randomly from tier folder.
        - post_clip:   RP/flavour clip to post AFTER the embed. None = skip.

        Override this in a subclass to give a boss a fully scripted, deterministic
        turn sequence. When overridden, boss_ai.py will use this instead of the
        individual hooks (pick_tier, on_turn_start, on_turn_end, etc.).

        Default: returns (None, pick_tier(state), None, None) — falls back to the
        existing probabilistic hook system so base-class bosses still work.
        """
        return None, self.pick_tier(state), None, None

    def plan_actions(self, state: BossState) -> list[dict] | None:
        """Return ordered multi-action plans when a script supports them."""
        return None

    def uses_plan_turn(self) -> bool:
        """Return True if this script overrides plan_turn.

        boss_ai.py checks this to decide whether to use the scripted path
        or the original independent-hook path.
        Default: False (base class does not override plan_turn).
        """
        return type(self).plan_turn is not BossScript.plan_turn

    # ------------------------------------------------------------------
    # Turn hooks (used when plan_turn is NOT overridden)
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

    def try_respawn(self, state: BossState) -> Path | None:
        """Called when the boss's HP reaches 0, before the match-over is declared.

        Return a Path to a respawn clip to trigger a one-time resurrection.
        The engine will reset the boss HP to the value returned by respawn_hp()
        and continue the fight. Return None to let the boss die normally.

        Default: no respawn.
        """
        return None

    def respawn_hp(self, state: BossState) -> int:
        """How much HP the boss is restored to after a respawn.

        Only called when try_respawn() returns a clip path.
        Default: 4.
        """
        return 4

    def on_victory(self, state: BossState) -> Path | None:
        """Called when the boss wins (human player's HP reaches 0).

        Return a Path to a victory clip, or None to skip.
        """
        return None

    def on_defeat(self, state: BossState) -> Path | None:
        """Called when the boss is defeated (boss HP reaches 0 and no respawn).

        Return a Path to a defeat clip, or None to skip.
        """
        return None
