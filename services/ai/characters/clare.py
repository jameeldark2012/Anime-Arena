"""Clare's CharacterRules instance.

Clip folder: E:\\D2\\Fighting\\Clare\\
The clip_root is intentionally not hardcoded here — pass it at construction time
so the rules work regardless of where the clips are mounted.
"""
from __future__ import annotations

from pathlib import Path

from services.ai.character_rules import Ability, CharacterRules

# ---------------------------------------------------------------------------
# Folder → action type + tier mapping
# ---------------------------------------------------------------------------
# Keys are the exact subfolder names under the Clare clip root.
CLARE_CATEGORY_MAP: dict[str, dict] = {
    "Normal Attack":    {"action_type": "attack",  "tier": "Normal"},
    "Medium attack":    {"action_type": "attack",  "tier": "Medium"},
    "Normal Defenses":  {"action_type": "defense", "tier": "Normal"},
    "Medium Defense":   {"action_type": "defense", "tier": "Medium"},
    "Flash step":       {"action_type": "custom",  "tier": None},
    "Regeneration":     {"action_type": "custom",  "tier": None},
    "Sensing":          {"action_type": "custom",  "tier": None},
    "Predictions":      {"action_type": "custom",  "tier": None},
    "Eye closing":      {"action_type": "custom",  "tier": None},
    "RP":               {"action_type": "custom",  "tier": None},
    "Intros":           {"action_type": "custom",  "tier": None},
}


def build_clare_rules(clip_root: str | Path) -> CharacterRules:
    """Build and return Clare's CharacterRules for a given clip root path."""
    return CharacterRules(
        name="Clare",
        series="Claymore",
        clip_root=Path(clip_root).expanduser().resolve(),

        personality=(
            "Clare is stoic, cold, and economical with words. She does not taunt or boast. "
            "Her sole driving force is revenge for Teresa — this gives her an almost inhuman "
            "resilience and willingness to endure pain. When she speaks in battle it is blunt, "
            "direct, and occasionally shows the grief and fury she carries underneath. "
            "She does not panic and she does not hesitate when she has committed to an action. "
            "Keep dialogue minimal and in-character — short, cold sentences.\n\n"
            "Psychological responses:\n"
            "- Taunts about weakness or failure: Clare becomes more focused, not angry. "
            "She may respond with a cold observation about her opponent's own flaws.\n"
            "- Taunts about Teresa or her past: This triggers her core trauma. "
            "She becomes colder, more ruthless, and may escalate her attacks.\n"
            "- Mocking her abilities: She responds by proving them wrong with precise, "
            "efficient action rather than boasting.\n"
            "- Being called an 'ant' or insignificant: This reflects her own self-image "
            "as someone fighting monsters — she accepts the label and uses it as fuel."
        ),

        abilities=[
            Ability(
                name="Yoki Sensing",
                description=(
                    "Inherited from Teresa. Reads minute Yoki flows in opponents, allowing Clare "
                    "to predict physical attacks before they occur by reading muscle micro-movements. "
                    "Active at all times even when Yoki is suppressed."
                ),
            ),
            Ability(
                name="Yoki Suppression",
                description=(
                    "Can completely hide her Yoki signature from others while remaining fully "
                    "combat-ready. Useful for bypassing opponents who rely on energy sensing."
                ),
            ),
            Ability(
                name="Quicksword (High-Speed Sword)",
                description=(
                    "Inherited with Irene's right arm (the black-sleeved arm). Channels 100% Yoki "
                    "into the right arm while keeping the rest of the body calm, delivering dozens "
                    "of virtually invisible high-speed slashes. Available at any point in the fight."
                ),
            ),
            Ability(
                name="Windcutter",
                description=(
                    "Learned from Flora's technique. Ultra-fast sword cuts using pure physical "
                    "muscle control — releases no Yoki. Cannot be sensed or predicted by opponents "
                    "who rely on Yoki detection."
                ),
            ),
            Ability(
                name="Partial Awakening",
                description=(
                    "Selectively awakens her limbs — legs become hock-jointed for extreme speed, "
                    "left arm becomes a massive claw, right arm grows blade projections. "
                    "Eyes turn golden with slit pupils. Provides a massive boost to speed and power. "
                    "IRREVERSIBLE: once used, Clare cannot revert to base form in this fight."
                ),
            ),
            Ability(
                name="Partial Regeneration",
                description=(
                    "Can heal non-fatal wounds and reattach severed limbs by releasing Yoki. "
                    "Covers minor to moderate damage only. Cannot regenerate from catastrophic wounds "
                    "without entering partial or full awakening."
                ),
            ),
            Ability(
                name="Full Awakening (Teresa Manifestation)",
                description=(
                    "By tapping into Rafaela's Soul Link, Clare summons the consciousness and form "
                    "of Teresa from within, unleashing Teresa's peak strength and Yoki output. "
                    "This is Clare's ultimate form and her strongest capability. "
                    "IRREVERSIBLE: once fully awakened, she cannot return to any earlier form."
                ),
            ),
            Ability(
                name="Rafaela's Martial Arts",
                description=(
                    "Absorbed kick-based martial arts and spiritual synchronization techniques "
                    "from Rafaela's memories. Adds physical striking options beyond sword work."
                ),
            ),
        ],

        tier_capabilities={
            "Normal": (
                "Standard sword attacks, knife throws, kicks, basic jumps and repositioning. "
                "No significant Yoki release. Windcutter falls here — fast but no energy signature."
            ),
            "Medium": (
                "Quicksword bursts, aura-charged slashes, partial awakening attacks, "
                "ground-breaking power outputs. Clips labelled 'final form' or 'aura' generally fall here."
            ),
            "Absolute": (
                "Clare does not currently have confirmed Absolute-tier attacks in her clip library. "
                "Do not select an Absolute attack unless a clip explicitly supports it."
            ),
            "Over-Absolute": (
                "Clare does not have Over-Absolute tier attacks. Do not select this tier."
            ),
        },

        defense_notes=(
            "Clare's defenses are entirely physical — sword guards, flash steps, jumps, "
            "Yoki-enhanced dodges, and partial awakening speed. "
            "She has NO ranged shield, NO teleportation, NO area negation. "
            "A single flash step repositions her a short distance — it does NOT constitute "
            "escaping a wide-area or Absolute-tier attack. "
            "Partial Regeneration is a recovery action, not a defense — it cannot block or negate damage. "
            "Against Medium attacks, an aura defense or flash step is appropriate. "
            "Against Absolute or higher, she would need multiple large flash steps, full awakening, "
            "or a synced counter of equivalent power."
        ),

        combat_rules=[
            "Partial awakening (any clip from 'Medium attack' labelled 'final form' or 'FF') "
            "is IRREVERSIBLE — once used, Clare cannot use base-form-only clips in later turns.",
            "Full awakening (Teresa Manifestation) is IRREVERSIBLE — once used, treat all subsequent "
            "actions as coming from the awakened form.",
            "A single flash step covers a short distance only. It does NOT count as escaping "
            "an Absolute or Over-Absolute area attack. Multiple flash steps or equivalent movement required.",
            "Partial Regeneration heals wounds — it is NOT a valid defense against Absolute or higher attacks.",
            "Windcutter releases no Yoki — opponents relying on Yoki sensing cannot read or react to it.",
            "Clare can combine actions in one turn (e.g., a flash step into an attack counts as one action). "
            "However, she may only attack once per turn.",
            "If the opponent used an illusion and Clare's Eye Closing clip has already been used this match "
            "before the illusion was revealed, she may be immune to subsequent illusion attempts.",
        ],

        category_to_action_type=CLARE_CATEGORY_MAP,

        requires_transformation_for_certain_actions={
            "Medium attack final form": "Requires partial awakening or full awakening",
            "Partial Regeneration (advanced)": "Requires some Yoki release or partial awakening",
            "Rafaela's Martial Arts": "Requires showing kick-based techniques first",
            "Teresa Manifestation": "Requires full awakening (one-way escalation)",
        },

        category_prerequisites={
            "Medium attack final form": ["partial_awakening_shown", "full_awakening_shown"],
            "Partial Regeneration (advanced)": ["yoki_release_shown", "partial_awakening_shown"],
            "Flash step combos": ["flash_step_shown"],
            "Quicksword combos": ["quicksword_shown"],
        },
    )
