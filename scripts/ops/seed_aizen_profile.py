"""Seed Aizen Sosuke's character profile into the database.

Usage:
    python -m scripts.ops.seed_aizen_profile
"""
from __future__ import annotations

import asyncio

from database.database import init_database
from services.content.character_profile_service import set_profile
from database.models.character import Character
from tortoise import Tortoise

AIZEN_PROFILE = """
## Overview
Sosuke Aizen is the central antagonist of Bleach during the Soul Society, Arrancar, and Fake Karakura Town arcs. Originally the respected Captain of Division 5 in the Gotei 13, he orchestrated a centuries-long conspiracy to betray the Soul Society, steal the Hōgyoku, and overthrow the Soul King.

## Appearance
**Captain Era:** Scholarly and polite — soft brown hair, square-framed glasses, warm brown eyes, standard Soul Reaper robes under his Division 5 Captain haori.

**Hueco Mundo / Espada Leader Era:** No glasses, hair slicked back with a single sharp strand falling over his face. Cold piercing gaze. White Arrancar-style robes with a broad black sash.

**Hōgyoku Transformations (one-way escalations):**
- **Chrysalis Form:** Covered head-to-toe in a smooth seamless white shell.
- **Post-Chrysalis Form:** Long brown hair, purple glowing eyes, white outfit fused directly into his skin.
- **Butterfly Form:** Three pairs of butterfly wings bearing skull faces, a third eye on his forehead, the Hōgyoku embedded in his chest.
- **Monster Form:** Dark grey hollow-like head with vertical face slits, blackened skin, dragon/hollow heads on his six wing tips.

**Muken / TYBW Era:** Bound to an iron containment chair with dark gray spiritual restraints and an eye patch covering his right eye.

## Personality
- **Deceptive & Charismatic:** Masters the persona of a soft-spoken compassionate mentor to hide his true motives.
- **Cold & Ruthless Manipulator:** Views everyone — allies and enemies alike — as disposable pawns. Operates with absolute detachment.
- **God Complex:** Driven by a refusal to submit to a passive ruler. Believes he alone possesses the wisdom and power to stand at the top.
- **Underlying Isolation:** Behind his arrogance lies a subconscious loneliness from being stronger than everyone around him.

## Powers and Abilities

### Genius Intellect
- Plans events decades in advance. Manipulates the lives of Ichigo, the Gotei 13, and the Espada without detection.
- Master scientist who independently created his own Hōgyoku.

### Core Soul Reaper Combat
- **Immense Reiatsu (Spiritual Pressure):** Transcendent levels capable of crushing Captain-level fighters to their knees and paralyzing Arrancar.
- **Master Swordsmanship (Zanjutsu):** Fights top-tier Captains with one hand, parries Bankai attacks with minimal effort.
- **Master Flash Step (Hohō):** Blinding speed capable of striking down multiple Captain-level opponents before they can react.
- **Master Hand-to-Hand (Hakuda):** Stops high-caliber weapons bare-handed, delivers destructive physical strikes.
- **Kidō Master:** Casts high-level offense (Hadō) and defense (Bakudō) spells up to level 90+ without incantation, including:
  - **Hadō #90: Kurohitsugi (Black Coffin)** — A torrent of gravitational force and dark blades. Full incantation = full power; half incantation = reduced effect.
  - **Hadō #99: Goryūtenlokudō** — Summons energy dragons that crush the landscape and siphon Reiatsu.

### Zanpakutō: Kyōka Suigetsu (Mirror Flower, Water Moon)
- **Shikai Release Command:** "Shatter" (Kowakero).
- **Kanzen Saimin (Complete Hypnosis):** Controls all five senses of anyone who **witnesses the Shikai release**. The effect is permanent — once a target has seen the release, they are susceptible forever. He can reactivate and alter their perception at will.
- **Counter:** If an opponent closes their eyes at the moment of Shikai activation (before the release completes), they are not affected.
- **TYBW Fused Form:** After fusing with his Zanpakutō, Complete Hypnosis activates through Reiatsu contact alone — no need to see the blade. Anyone who looks at him or contacts his Reiatsu falls under illusion.

### Hōgyoku Fusion
- **Absolute Immortality & Regeneration:** Completely immortal. Regenerates instantly from fatal cuts, total bisection, and complete disintegration. Cannot be killed — only sealed.
- **Adaptive Evolution:** Continuously evolves in power, form, and speed when pushed to his limits.
- **Energy Projection:** Fires massive destructive energy blasts (Fragor) from his mutated wings.
- **Teleportation:** Teleports instantly across long distances.

## Combat Rules
- **Kyōka Suigetsu requires witnessing the release:** An opponent who closes their eyes before the release completes, or who has never seen it, is immune to the illusion.
- **Complete Hypnosis is permanent after first exposure:** Does not need to be reactivated each turn — once an opponent has seen the release, they are under its effect for the rest of the fight unless they found a counter.
- **TYBW fused form bypasses the eye-closing counter:** In this form the illusion spreads through Reiatsu contact, not visual exposure.
- **Kurohitsugi at full power requires full incantation.** Half-incantation or casual casting produces a weaker version — treat as Medium tier rather than Absolute.
- **Hōgyoku transformations are one-way escalations** — Aizen cannot revert to an earlier form once transformed.
- **Immortality under Hōgyoku fusion:** Over-Absolute attacks do not kill Aizen — they force a sealing scenario. Referees should rule this as a match condition rather than a standard KO.
- **Flash steps are long-range:** Aizen's Hohō operates at Captain-class+ speed — a single flash step covers far greater distance than most characters.
""".strip()


async def main() -> None:
    await init_database()

    # Aizen's exact name in the database is "Sousuke Aizen"
    char = await Character.get_or_none(character_name__iexact="Sousuke Aizen")
    if char is None:
        # Fallback to case-insensitive contains
        char = await Character.get_or_none(character_name__icontains="Sousuke Aizen")
    if char is None:
        char = await Character.get_or_none(character_name__icontains="Aizen")

    if char is None:
        print("[ERROR] Character 'Aizen' not found in the database. Make sure he is imported from the dataset.")
        await Tortoise.close_connections()
        return

    updated = await set_profile(char.character_id, AIZEN_PROFILE)
    if updated:
        print(f"[OK] Aizen's profile set (character_id={char.character_id}, name='{char.character_name}').")
    else:
        print("[ERROR] Failed to update Aizen's profile.")

    await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
