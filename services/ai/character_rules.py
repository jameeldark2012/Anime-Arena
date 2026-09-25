"""Character rules — defines a character's fighting ruleset for the AI.

CharacterRules is the structured object that gets rendered into the prompt.
It encodes personality, abilities, mandatory combat sequencing rules,
tier capabilities, and physical constraints.

Each character gets their own instance in services/ai/characters/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Tier = Literal["Normal", "Medium", "Absolute", "Over-Absolute"]


@dataclass
class Ability:
    """A named ability with a combat description."""
    name: str
    description: str


@dataclass
class CharacterRules:
    """Full ruleset for a single character.

    Parameters
    ----------
    name:
        Character's name as it appears in-game.
    series:
        Source anime/manga.
    clip_root:
        Path to the folder containing this character's clip library.
    personality:
        How this character speaks, behaves, and reacts in combat.
        Used to shape dialogue and RP choices.
    abilities:
        List of named abilities the AI can reference when choosing clips.
    combat_rules:
        Mandatory sequencing rules and physical constraints.
        These are hard rules the AI must not violate.
        Examples:
          - "Full awakening is irreversible — cannot revert once used"
          - "Flash steps do not count as escaping Absolute area attacks"
    tier_capabilities:
        Maps game tiers to what this character can realistically produce
        at each state. Used to validate tier choices.
        Example: {"Normal": "standard sword attacks", "Medium": "Quicksword or aura burst"}
    defense_notes:
        What defensive options this character physically has and lacks.
        The AI uses this to pick logically valid defenses.
    category_to_action_type:
        Maps clip folder names to game action types (attack/defense/custom).
        Also encodes the tier for attack/defense folders.
    """

    name: str
    series: str
    clip_root: Path
    personality: str
    abilities: list[Ability] = field(default_factory=list)
    combat_rules: list[str] = field(default_factory=list)
    tier_capabilities: dict[Tier, str] = field(default_factory=dict)
    defense_notes: str = ""
    category_to_action_type: dict[str, dict] = field(default_factory=dict)
    # e.g. {"Normal Attack": {"action_type": "attack", "tier": "Normal"},
    #        "Flash step":   {"action_type": "custom", "tier": None},
    #        "RP":           {"action_type": "custom", "tier": None}}

    # Maps ability names to transformation requirements
    # e.g., {"Quicksword": "Requires showing Quicksword ability first",
    #        "Medium attack final form": "Requires partial or full awakening"}
    requires_transformation_for_certain_actions: dict[str, str] = field(default_factory=dict)

    # Maps action categories to prerequisite abilities/state
    # e.g., {"Medium attack final form": ["partial_awakening_shown"]}
    category_prerequisites: dict[str, list[str]] = field(default_factory=dict)

    def render_for_prompt(self) -> str:
        """Render this ruleset as a concise prompt block."""
        lines: list[str] = []

        lines.append(f"## Character: {self.name} ({self.series})")
        lines.append("")

        lines.append("### Personality")
        lines.append(self.personality)
        lines.append("")

        if self.abilities:
            lines.append("### Abilities")
            for ability in self.abilities:
                lines.append(f"- **{ability.name}:** {ability.description}")
            lines.append("")

        if self.tier_capabilities:
            lines.append("### Tier Capabilities")
            for tier, description in self.tier_capabilities.items():
                lines.append(f"- **{tier}:** {description}")
            lines.append("")

        if self.defense_notes:
            lines.append("### Defense Capabilities")
            lines.append(self.defense_notes)
            lines.append("")

        if self.combat_rules:
            lines.append("### Mandatory Combat Rules")
            lines.append("These rules are absolute. Violating them is not allowed.")
            for rule in self.combat_rules:
                lines.append(f"- {rule}")
            lines.append("")

        return "\n".join(lines).strip()
