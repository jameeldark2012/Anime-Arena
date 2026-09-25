"""Seed Clare's character profile into the database.

Usage:
    python -m scripts.ops.seed_clare_profile
"""
from __future__ import annotations

import asyncio

from database.database import init_database
from services.content.character_profile_service import get_profile_by_name, set_profile
from database.models.character import Character
from tortoise import Tortoise

CLARE_PROFILE = """
## Overview
Clare is the primary protagonist of Claymore and rank No. 47 of the Organization. Unlike standard Claymores created by fusing 1/2 Yoma flesh into humans, Clare voluntarily took in the flesh and blood of former Claymore Teresa "of the Faint Smile," making her a 1/4 Yoma hybrid.

## Appearance
**Human Childhood:** Thin and frail with long hime-cut light brown hair, green eyes, and heavy scarring from Yoma abuse.

**Claymore Form:** Slender and wispy build disguising her supernatural physical strength. Short pageboy-cut light blonde/ash hair, silver eyes, a vertical stigmata scar running down her chest. Wears the Organization's standard silver armor over a gray bodysuit and wields an unbreakable two-handed broadsword. After losing her right arm to Ophelia, she receives the severed right arm of former No. 3 Irene and wears a single black sleeve over the right arm to cover the seam.

**Partially Awakened Form:** Eyes turn golden with slit pupils. Legs deform into hock-jointed horse-like structures for high speed. Left arm expands into a massive claw. Blade projections grow from her right arm.

## Personality
- **Revenge-Driven:** Stoic and cold on the surface. Her sole driving purpose is to avenge Teresa by hunting and killing the Awakened Being Priscilla.
- **Deep Compassion:** Intensely protective of others despite her cold exterior. Her strong emotional nature causes her to rush into battle recklessly when those she cares for are threatened.
- **Tactical & Resilient:** Highly adaptive under pressure with immense mental fortitude and combat intelligence to outsmart stronger foes.

## Powers and Abilities

### Physical Capabilities
- Superhuman strength, speed, durability, and stamina.
- **Low-Mid Regeneration:** Can reattach severed limbs and rapidly heal non-fatal wounds (partial regeneration). Cannot fully regenerate from catastrophic damage without awakening.

### Yoki Sensing & Suppression
- **Acute Yoki Sensing (inherited from Teresa):** Reads minute Yoki flows in enemies, predicting physical attacks seconds before they occur by reading muscle movements.
- **Yoki Suppression:** Can completely hide her Yoki signature while remaining combat-ready and keeping her sensing active.

### Sword & Combat Techniques
- **Quicksword (High-Speed Sword):** Inherited alongside Irene's right arm. Channels 100% Yoki into her right arm while keeping her body calm, delivering dozens of virtually invisible high-speed slashes.
- **Windcutter:** Learned from Flora's technique. Rapid precise sword cuts using pure physical muscle control — releases no Yoki, bypasses enemies trained to sense energy signatures.
- **Rafaela's Martial Arts:** Absorbed Rafaela's memories and combat skills, gaining powerful kick-based martial arts and spiritual synchronization techniques.

### Transformations
- **Partially Awakened Limbs:** Selectively unleashes Yoki in her limbs for immense speed and striking power while maintaining her human mind.
- **Full Awakening (Teresa Manifestation):** By tapping into Rafaela's Soul Link, Clare summons the consciousness and form of Teresa from within her body, unleashing Teresa's peak strength and Yoki output. **This transformation is irreversible in a fight — once fully awakened, she cannot return to base form.**

## Combat Rules
- Partial awakening (partial limb transformation) is also irreversible once used in a fight — Clare cannot revert to her base form.
- Flash steps are short repositioning bursts — a single flash step does NOT constitute escaping an Absolute or higher area attack. Multiple large flash steps or teleportation-equivalent movement is required.
- Partial Regeneration heals non-fatal wounds only — it cannot serve as a defense against Absolute or Over-Absolute attacks.
- Windcutter releases no Yoki — opponents relying solely on Yoki sensing cannot read or react to it.
- Quicksword requires Irene's right arm — the black-sleeved arm. It is usable at any point in the fight.
- Clare has no ranged shield, no teleportation, no area negation. All her defenses are physical: sword guards, flash steps, jumps, or partial awakening-enhanced limbs.
""".strip()


async def main() -> None:
    await init_database()

    char = await Character.get_or_none(character_name__iexact="Clare")
    if char is None:
        print("[ERROR] Character 'Clare' not found in the database. Make sure she is imported from the dataset.")
        await Tortoise.close_connections()
        return

    updated = await set_profile(char.character_id, CLARE_PROFILE)
    if updated:
        print(f"[OK] Clare's profile set (character_id={char.character_id}).")
    else:
        print("[ERROR] Failed to update Clare's profile.")

    await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
