from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from boss.boss_script import BossScript

if TYPE_CHECKING:
    from boss.boss_state import BossState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Turn plan type
# ---------------------------------------------------------------------------

class TurnPlan(NamedTuple):
    pre:    str | None   # RP subfolder filename, relative to clips_dir
    tier:   str          # "Normal" | "Medium" | "Absolute" | "Over-Absolute"
    attack: str          # attack clip filename, relative to attacks/<tier_folder>/
    post:   str | None   # RP subfolder filename, relative to clips_dir


# ---------------------------------------------------------------------------
# The full 29-turn script
# ---------------------------------------------------------------------------
# Each entry: TurnPlan(pre_rp, tier, attack_filename, post_rp)
# pre/post are bare filenames inside their respective folders.
# Attack filenames are inside attacks/<tier>/.

_SCRIPT: list[TurnPlan] = [
    # ── ACT 1: Arrogant and lazy (turns 1-7) ─────────────────────────────────
    TurnPlan(
        pre="Watches you.mp4",
        tier="Normal",
        attack="Normal attack 1 calls on titans.mp4",
        post=None,
    ),
    TurnPlan(
        pre="Scratches its ear and smiles sinseterly.mp4",
        tier="Normal",
        attack="Normal attack 2 turns to right then swipe fangs.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Normal",
        attack="Normal attack 3 human form calles on titans on u.mp4",
        post="Hands in air after rocks, gameuu setuu, im good at.mp4",
    ),
    TurnPlan(
        pre=None,
        tier="Normal",
        attack="Normal attack 4 throws human flesh at u above in the sky.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Normal",
        attack="Normal attack 5 throws flesh behind him.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Normal",
        attack="Normal attack 6 throws flesh above himself.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Normal",
        attack="Normal attack 7 throws some rocks.mp4",
        post="Mocks after throwing rocks on city, went too high .mp4",
    ),

    # ── ACT 2: Annoyed, escalating (turns 8-18) ──────────────────────────────
    TurnPlan(
        pre="Sits down next to you and looks scary beast form.mp4",
        tier="Medium",
        attack="Medium attack 1 throw horse.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Medium",
        attack="Medium attack 2 throw big rock.mp4",
        post=None,
    ),
    TurnPlan(
        pre="What would throwing these things do gets angry.mp4",
        tier="Medium",
        attack="Medium attack 3 smashes ground and sends titans.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Medium",
        attack="Medium attack 4 what are you acheving with screaming!!!! gets pissed and throws rocks.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Medium",
        attack="Medium attack 5 throws things devasting area.mp4",
        post="He says poor things with sympathy.mp4",
    ),
    TurnPlan(
        pre=None,
        tier="Medium",
        attack="Medium attack 6 throws rocks good damage besat for.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Medium",
        attack="Medium attack 7 thows rocks 90 degrees convered on front.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Medium",
        attack="Medium attack 8 human form call on titans RAINING.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Medium",
        attack="Medium attack 9 throws rock at sky above.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Medium",
        attack="Medium attack 10 throws rocks from above to below .mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Medium",
        attack="Medium attack 11 rocks from above to below.mp4",
        post=None,
    ),

    # ── ACT 3: Done playing, going for the kill (turns 19-29) ────────────────
    TurnPlan(
        pre="Lets end it here, i want to be over with this huma.mp4",
        tier="Absolute",
        attack="Absolute 1 summons titans destroys city.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Absolute",
        attack="Absolute 2 throws barrel destroys city.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Absolute",
        attack="Absolute 3 throws big shattered rocks On cityy.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Absolute",
        attack="Absolute 4 big rocks on city.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Absolute",
        attack="Absolute 4 big rocks on city, mocks (perfect gamu).mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Absolute",
        attack="Absolute 5 rocks throw.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Absolute",
        attack="Absolute 6 first crushes stonr with its hand gets pissed then is surprised about being pissed so he tries to have fun throws massive rocks.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Absolute",
        attack="Absolute 7 human form summon titans from above nukes city.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Absolute",
        attack="Absolute 8 throws rocks high area damage to ships.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Absolute",
        attack="Absolute 8 gets up from ground call titans city destroyed also says What a shame.mp4",
        post=None,
    ),
    TurnPlan(
        pre=None,
        tier="Medium",
        attack="Medium attack 12 big rocks throw.mp4",
        post=None,
    ),
]

# After turn 29, loop from turn 19 (index 18) — permanent nuke mode.
_LOOP_FROM = 18

# Tier folder mapping (matches TIER_FOLDER in boss_config.py).
_TIER_FOLDER = {
    "Normal": "normal",
    "Medium": "medium",
    "Absolute": "absolute",
    "Over-Absolute": "over_absolute",
}


# ---------------------------------------------------------------------------
# ZekeScript
# ---------------------------------------------------------------------------

class ZekeScript(BossScript):
    """Fully deterministic script for Zeke Yeager (Beast Titan).

    Every boss turn is pre-planned. The turn counter advances by 1 each time
    plan_turn() is called. After turn 29 the script loops from turn 19 onward
    (permanent ACT 3 / nuke mode).

    Respawn fires once when boss HP hits 0 (Full rebirth → HP restored to 4).
    Victory / defeat clips play at match end.
    Intros are the only randomised element — a different one each fight.
    """

    def __init__(self) -> None:
        # 0-indexed position in _SCRIPT. Incremented each call to plan_turn().
        self._turn_index: int = 0
        self._has_respawned: bool = False

    # ------------------------------------------------------------------
    # Intro (only randomness in the script)
    # ------------------------------------------------------------------

    def on_match_start(self, state: BossState) -> Path | None:
        intros_dir = state.boss_config.clips_dir / "intros"
        if not intros_dir.exists():
            return None
        clips = [
            p for p in intros_dir.iterdir()
            if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
        ]
        return random.choice(clips) if clips else None

    # ------------------------------------------------------------------
    # Scripted turn plan
    # ------------------------------------------------------------------

    def plan_turn(self, state: BossState) -> tuple[Path | None, str, Path | None, Path | None]:
        """Return the pre-planned turn for the current turn index."""
        clips_dir = state.boss_config.clips_dir

        plan = _SCRIPT[self._turn_index]

        # Advance index, looping back into ACT 3 after the last turn.
        self._turn_index += 1
        if self._turn_index >= len(_SCRIPT):
            self._turn_index = _LOOP_FROM

        pre_path  = (clips_dir / "RP" / plan.pre) if plan.pre else None
        post_path = (clips_dir / "RP" / plan.post) if plan.post else None
        attack_path = clips_dir / "attacks" / _TIER_FOLDER[plan.tier] / plan.attack

        # Validate paths — fall back gracefully if a file is missing.
        if pre_path and not pre_path.exists():
            logger.warning("Zeke pre-clip missing: %s", pre_path)
            pre_path = None
        if post_path and not post_path.exists():
            logger.warning("Zeke post-clip missing: %s", post_path)
            post_path = None
        if not attack_path.exists():
            logger.warning("Zeke attack clip missing: %s — will pick randomly.", attack_path)
            attack_path = None

        return pre_path, plan.tier, attack_path, post_path

    # ------------------------------------------------------------------
    # Respawn
    # ------------------------------------------------------------------

    def try_respawn(self, state: BossState) -> Path | None:
        if self._has_respawned:
            return None
        path = state.boss_config.clips_dir / "defenses" / "Full rebirth (online-video-cutter.com).mp4"
        if not path.exists():
            return None
        self._has_respawned = True
        return path

    def respawn_hp(self, state: BossState) -> int:
        return 4

    # ------------------------------------------------------------------
    # Victory / defeat
    # ------------------------------------------------------------------

    def on_victory(self, state: BossState) -> Path | None:
        p = state.boss_config.clips_dir / "RP" / "I won beast form.mp4"
        return p if p.exists() else None

    def on_defeat(self, state: BossState) -> Path | None:
        p = state.boss_config.clips_dir / "RP" / "Falls from above and dies.mp4"
        return p if p.exists() else None
