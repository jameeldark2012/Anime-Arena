"""Aizen's CharacterRules instance.

Clip folder: E:\\D2\\Fighting\\Aizen\\
The clip_root is intentionally not hardcoded here — pass it at construction time
so the rules work regardless of where the clips are mounted.
"""
from __future__ import annotations

from pathlib import Path

from services.ai.core.character_rules import Ability, CharacterRules

# ---------------------------------------------------------------------------
# Folder → action type + tier mapping
# ---------------------------------------------------------------------------
# Keys are the exact subfolder names under the Aizen clip root.
AZEN_CATEGORY_MAP: dict[str, dict] = {
    "Normal Attack":      {"action_type": "attack",  "tier": "Normal"},
    "Medium Attack":      {"action_type": "attack",  "tier": "Medium"},
    "Absolute Attack":    {"action_type": "attack",  "tier": "Absolute"},
    "Normal Defenses":    {"action_type": "defense", "tier": "Normal"},
    "Medium Defense":     {"action_type": "defense", "tier": "Medium"},
    "Flash Step":         {"action_type": "defense", "tier": "Medium"},
    "Predictions":        {"action_type": "custom",  "tier": None},
    "RP":                 {"action_type": "custom",  "tier": None},
    "Intros":             {"action_type": "custom",  "tier": None},
}


def build_aizen_rules(clip_root: str | Path) -> CharacterRules:
    """Build and return Aizen's CharacterRules for a given clip root path."""
    return CharacterRules(
        name="Aizen",
        series="Bleach",
        clip_root=Path(clip_root).expanduser().resolve(),

        personality=(
            "Aizen is calm, calculating, and supremely confident. He speaks with quiet authority, "
            "often using sophisticated vocabulary and philosophical reflections. He views himself "
            "as a creator of new worlds and sees his opponents as stepping stones to his divine ambition. "
            "He never panics, even in defeat, because he always has a plan. When he speaks in battle, "
            "it is with cool detachment, often analyzing his opponent's weaknesses or explaining the "
            "futility of their resistance. Keep dialogue intelligent, cool, and slightly condescending— "
            "he knows he is superior and lets his opponent know it.\n\n"
            "Psychological responses:\n"
            "- Taunts about weakness or failure: Aizen calmly points out their inherent flaws or "
            "lack of understanding, treating them as a specimen to be studied.\n"
            "- Taunts about his ambition or betrayal: He embraces these labels, framing them as "
            "necessary steps toward creating a better world without the Soul King's tyranny.\n"
            "- Mocking his abilities: Aizen responds by demonstrating even greater power, revealing "
            "that what was shown was merely a fraction of his potential.\n"
            "- Being called a monster: He accepts this label, seeing it as proof that his evolution "
            "beyond human and Soul Reaper constraints is complete."
        ),

        abilities=[
            Ability(
                name="Kyōka Shigetsu (Mirror Ice Moonlight)",
                description=(
                    "Aizen's Zanpakutō's Shikai ability. It allows him to control the opponent's five "
                    "senses completely, trapping them in an illusion so perfect that even after the "
                    "trap ends, the effects remain. It is considered the most perfect illusion technique."
                ),
            ),
            Ability(
                name="Hakui (Pious Veil)",
                description=(
                    "Aizen's Bankai ability. It suppresses his spiritual pressure to the point that "
                    "even the most powerful Sense users cannot detect it. This makes him undetectable "
                    "to spiritual sensing techniques."
                ),
            ),
            Ability(
                name="Hogyoku Fusion Stage 1",
                description=(
                    "Aizen's first Hogyoku fusion state. He looks almost identical to his normal form, "
                    "but the Hogyoku is visible inside his chest. Grants exceptional partial regenerative "
                    "abilities and significantly increased strength and durability."
                ),
            ),

            Ability(
                            name="Hogyoku Fusion Stage 2",
                            description=(
                                "Its similar to the first stage but he grows a white thingy over his chest to his shoulder he gains more regenrative abilities, partial ones"
                            ),
                        ),
            Ability(
                name="Hogyoku Fusion Stage 3",
                description=(
                    "Aizen is completely covered from head to toe in white canvas-like material, "
                    "resembling a phantom. His face is hidden. Strength, speed, regeneration, "
                    "durability, and Spiritual Pressure all increase dramatically in this stage."
                ),
            ),
            Ability(
                name="Hogyoku Fusion Stage 4",
                description=(
                    "Aizen breaks out of the Chrysalis form. His face is visible again, and his hair "
                    "has grown significantly longer. Durability and Spiritual Pressure are enhanced."
                ),
            ),
            Ability(
                name="Hogyoku Fusion Stage 5",
                description=(
                    "Aizen sprouts large, bat-like wings, loses his irises (leaving only purple eyes), "
                    "and receives a diamond-shaped object embedded in his forehead. Gains the power "
                    "of teleportation. All stats are significantly enhanced."
                ),
            ),
            Ability(
                name="Hogyoku Fusion Stage 6",
                description=(
                    "Aizen's wings twist upwards, and the third eye opens on his face."
                ),
            ),
            Ability(
                name="Final Fusion (Hogyoku Fusion Stage 7)",
                description=(
                    "Aizen's ultimate form. He becomes a monstrous Hollow-like being, retaining very "
                    "little of his original human appearance. His face is completely black and demonic, "
                    "wings are adorned with Hollow-like skulls and eyes, left hand is demonic black, "
                    "and possesses extreme regeneration."
                ),
            ),
            Ability(
                name="Teleportation",
                description=(
                    "Aizen gained the ability to teleport at will after reaching Hogyoku Fusion Stage 5. "
                    "He can move instantly across vast distances."
                ),
            ),
        ],

        tier_capabilities={
            "Normal": (
                "Standard sword attacks and standard defense like frontal guards"
                
            ),
            "Medium": (
                "Kurohetsugi not fully chanted and some bakuduns like rakuuhu, flash steps ,aura-charged techniques and also partial regen if form is satisfied ."
            ),
            "Absolute": (
                "Large-scale Reiatsu release, higher Hadu, "
                "teleportation. Absolute-tier attacks should only be selected if a clip explicitly supports it."
            ),
            "Over-Absolute": (
                "The character doesn't have any"
            ),
        },

        defense_notes=(
            "Aizen has multiple defensive options: "
            "flash steps for repositioning, teleportation (Stage 5+), and overwhelming Spiritual Pressure."
            "He CAN use blocking techniques, flash steps, teleportation to defend"
        ),

        combat_rules=[
            "Aizen's forms are determined by visual appearance in the clip - NOT assumptions or filenames.",
            "Aizen has 8 visual forms in progression:",
            "1. Shinigami form (base state, looks human).",
            "2. Hogyoku fusion - Hogyoku visible in chest (still looks human).",
            "3. White canvas over chest/shoulders (Chrysalis).",
            "4. Masked form - full suit, merged with Hogyoku.",
            "5. Mask falls, he grows long hair.",
            "6. He grows wings.",
            "7. Wings twist up slightly, and the third eye opens on his face.",
            "8. Final Fusion - Monster looking bastard, face completely black.",
            "Once a transformation is shown visually in a clip, all subsequent actions must be consistent "
            "with that form. A clip's visual appearance determines the form.",
            "Teleportation is ONLY available after reaching Hogyoku Fusion Stage 5.",
        ],

        category_to_action_type=AZEN_CATEGORY_MAP,
    )
