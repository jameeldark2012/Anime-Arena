from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING

from boss.boss_script import BossScript, pick_random_clip

if TYPE_CHECKING:
    from boss.boss_state import BossState


class ZekeScript(BossScript):
    """AI script for Zeke (Beast Titan).

    Behaviour:
    - Plays a random intro clip when the fight starts.
    - Never defends — always tanks hits.
    - Attack tier weights shift heavier at low HP (he gets desperate).
    - Reacts with an RP clip when he takes a big hit.
    - Taunts with an RP clip after landing a big hit on the player.
    """

    # ── Match lifecycle ────────────────────────────────────────────────

    def on_match_start(self, state: BossState) -> Path | None:
        """Play a random intro clip when the fight begins."""
        return pick_random_clip(state.boss_config, "intros")

    # ── Defense ───────────────────────────────────────────────────────

    def should_defend(self, state: BossState) -> bool:
        """Zeke never defends."""
        return False

    # ── Attack ────────────────────────────────────────────────────────

    def pick_tier(self, state: BossState) -> str:
        """Tier weights shift toward heavier attacks as Zeke's HP drops."""
        hp = state.boss_hp
        config = state.boss_config

        if hp <= 3:
            # Desperate — goes for big hits
            weights = {"Normal": 1, "Medium": 2, "Absolute": 4, "Over-Absolute": 2}
        elif hp <= 6:
            # Bloodied — slightly more aggressive
            weights = {"Normal": 2, "Medium": 4, "Absolute": 3, "Over-Absolute": 1}
        else:
            # Fresh — uses config defaults
            weights = config.attack_weights

        tiers = list(weights.keys())
        w = [weights[t] for t in tiers]

        # Prefer tiers that actually have clips
        tiers_with_clips = [t for t in tiers if config.get_clips(t)]
        if tiers_with_clips:
            w_with_clips = [weights[t] for t in tiers_with_clips]
            return random.choices(tiers_with_clips, weights=w_with_clips, k=1)[0]

        return random.choices(tiers, weights=w, k=1)[0]

    # ── Event reactions ───────────────────────────────────────────────

    def on_boss_hit(self, state: BossState, damage: int) -> Path | None:
        """React with an RP clip when taking a medium or bigger hit."""
        if damage >= 2:
            return pick_random_clip(state.boss_config, "RP")
        return None

    def on_player_hit(self, state: BossState, damage: int) -> Path | None:
        """Taunt with an RP clip after landing a significant hit."""
        if damage >= 2:
            return pick_random_clip(state.boss_config, "RP")
        return None
