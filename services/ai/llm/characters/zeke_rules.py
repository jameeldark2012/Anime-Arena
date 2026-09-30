"""Zeke Yeager — Attack on Titan character rules.

Zeke is the royal-blooded holder of the Beast Titan and a former Marleyan
Warrior commander. His canonical personality and abilities are kept here so
the decision engine can distinguish him from a generic aggressive monster.

Clip folder: assets/boss_clips/zeke
Character: Zeke Yeager (Beast Titan) from Attack on Titan.
"""
from __future__ import annotations

from pathlib import Path

from services.ai.core.character_rules import Ability, CharacterRules

# Mapping from clip subfolder names → action type + tier
# Keys match the exact folder structure under the zeke clip root.
ZEKE_CATEGORY_MAP: dict[str, dict] = {
    "attacks.normal":         {"action_type": "attack",  "tier": "Normal"},
    "attacks.medium":         {"action_type": "attack",  "tier": "Medium"},
    "attacks.absolute":       {"action_type": "attack",  "tier": "Absolute"},
    "attacks.over_absolute":  {"action_type": "attack",  "tier": "Over-Absolute"},
    "RP":                     {"action_type": "custom",  "tier": None},
    "Intros":                 {"action_type": "custom",  "tier": None},
}


def build_zeke_rules(clip_root: str | Path) -> CharacterRules:
    """Build and return Zeke's CharacterRules for a given clip root path."""
    return CharacterRules(
        name="Zeke",
        series="Attack on Titan",
        clip_root=Path(clip_root).expanduser().resolve(),

        personality=(
            "Zeke is intelligent, composed, sardonic, and quietly condescending. He is a veteran "
            "Warrior commander and a patient strategist who prefers preparation, manipulation, and "
            "battlefield control over reckless brawling. He often uses dry humor and a detached, "
            "almost conversational tone even while describing horrifying actions. He can sound "
            "paternal toward younger Warriors, but that calmness does not make him gentle: he is "
            "willing to sacrifice people for what he believes is a merciful solution.\n\n"
            "Zeke was shaped by being treated as a tool by his parents and came to believe that "
            "ending Eldian reproduction would spare future generations from suffering. He presents "
            "this euthanasia plan as rational compassion, not cruelty, and becomes unusually serious "
            "when someone challenges that belief or invokes his family. He is not constantly enraged "
            "or boastful. Keep his dialogue concise, dry, analytical, and self-assured; let rare "
            "moments of grief, exhaustion, or genuine concern break through when the scene warrants it.\n\n"
            "Psychological responses:\n"
            "- A direct challenge: answer with calm analysis, dry mockery, or a measured demonstration of power.\n"
            "- A threat to younger Warriors or allies: become protective and strategically decisive.\n"
            "- Attacks on his family or ideology: drop the joking tone and answer with controlled intensity.\n"
            "- A discussion of Eldian suffering: frame his plan as mercy, while revealing his fatalistic worldview.\n"
        ),

        abilities=[
            Ability(
                name="Beast Titan throwing mastery",
                description=(
                    "In his 17-meter Beast Titan form, Zeke uses his long arms and exceptional "
                    "baseball-trained accuracy to throw rocks, barrels, and other large projectiles "
                    "over extreme distances with devastating force."
                ),
            ),
            Ability(
                name="Royal-blooded spinal fluid scream",
                description=(
                    "Subjects of Ymir who ingest Zeke's spinal fluid can transform when he screams. "
                    "This requires prior exposure to his spinal fluid; the scream does not transform "
                    "ordinary humans or create Titans from nothing."
                ),
            ),
            Ability(
                name="Titan command",
                description=(
                    "After transforming exposed Subjects of Ymir, Zeke can direct the resulting Pure "
                    "Titans with vocal commands. His control is powerful but not absolute, and the "
                    "Titans can function at night under moonlight."
                ),
            ),
            Ability(
                name="Warrior commander and strategist",
                description=(
                    "Zeke is an experienced Marleyan Warrior commander who studies the battlefield, "
                    "uses allies as coordinated assets, and prefers traps, timing, and ranged pressure "
                    "to impulsive close combat."
                ),
            ),
        ],

        combat_rules=[
            "Zeke has NO defensive capabilities whatsoever — he cannot block, dodge, counter, or evade.",
            "Zeke must always prioritize offensive actions (attacks) over RP or feint clips.",
            "When the opponent attacks, Zeke does not defend — he accepts the damage and continues his assault.",
            "Zeke's aggression should increase as his HP decreases — a wounded Beast Titan is more dangerous.",
        ],

        tier_capabilities={
            "Normal": (
                "Basic calls of titans, simple throws of flesh/rocks, minor physical strikes. "
                "Standard beast-form movements and minimal pressure."
            ),
            "Medium": (
                "Barrel bombs with wide area coverage, titan summoning from above, "
                "large rock throws, ground-breaking assaults. These deliver significant pressure."
            ),
            "Absolute": (
                "Full-scale titan deployment with city-level destruction, coordinated multi-hit barrages, "
                "massive rock formations thrown across entire battlefield zones.",
            ),
            "Over-Absolute": (
                "Coordinated mega-titan calls combined with massive barrel deployments that span "
                "the entire arena, simultaneously crushing multiple sectors at once.",
            ),
        },

        has_defense=False,

        defense_notes=(
            "Zeke has no defensive capabilities. He cannot block, dodge, counter-attack, or evade. "
            "All his folders are attack-focused (attacks.normal, attacks.medium, attacks.absolute, "
            "attacks.over_absolute) plus RP and Intros for roleplay/intro purposes. "
            "He plays a purely aggressive style — overwhelming the opponent with sustained pressure."
        ),

        category_to_action_type=ZEKE_CATEGORY_MAP,

        requires_transformation_for_certain_actions={},

        category_prerequisites={},
    )
