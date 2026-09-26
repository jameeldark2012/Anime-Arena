"""Prompt builder — assembles the full AI decision prompt from all parts.

Sections (in order):
  1. Role block         — who the AI is and what it's doing
  2. Game rules block   — hardcoded, never changes
  3. Character rules    — rendered from CharacterRules
  4. Opponent profile   — the opponent's character profile text
  5. Match state        — current HP, turn, who attacked last and at what tier
  6. Opponent actions   — what the opponent declared + AI descriptions of their clips
  7. Available actions  — the clip pool with descriptions, grouped by category
  8. Output schema      — exact JSON the AI must return
"""
from __future__ import annotations

from services.ai.character_rules import CharacterRules
from services.ai.clip_catalog import ClipCatalog, ClipEntry

# ---------------------------------------------------------------------------
# Hardcoded game rules — these do not change
# ---------------------------------------------------------------------------
_GAME_RULES = """
## Game Rules (read carefully — these are absolute)

You are playing a 1v1 turn-based video game on Discord where each player submits real anime video clips as their actions.

### HP System
- Each player starts at 4 HP.
- Normal attack deals 1 damage if undefended.
- Medium attack deals 2 damage if undefended.
- Absolute attack deals 3 damage if undefended.
- Over-Absolute attack deals 4 damage (instant KO) if undefended.
- Your primary objective is to win the fight: preserve Clare's life, create openings, and reduce the opponent's HP.
- Use a slow-burn escalation: early turns should establish presence, observe the opponent, exchange restrained Normal-tier actions, and use fitting RP or setup clips. Do not jump into a climax, full transformation, ultimate attack, or endgame escalation on turn 2 without an established prerequisite or an immediate survival necessity ,However, if your hp is getting low you could rush to stronger moves and transformations.

### Turn Structure
- Players alternate turns. On your turn you may submit one or more actions, then end your turn.
- You may attack ONCE per turn maximum.
- You may submit defense, custom/RP clips in any combination alongside your attack.
- A single action can combine movement and attack (e.g. flash step into a slash = one action).

### Defense Obligation (critical rule)
- If your opponent attacked on their previous turn, your FIRST action this turn MUST be a defense.
- If your opponent did not attack on their previous turn, do NOT waste a defense action. Defense is only for blocking or avoiding a clearly identified incoming attack or threat.
- If you do not defend first, you take full damage from their attack regardless of what else you do.
- The defense must make logical sense and be of equal or greater tier to the incoming attack:
  - Against Normal attack: any Normal defense, flash step, jump, or higher-tier defense works.
  - Against Medium attack: a Medium-tier defense (aura shield, strong evasion) or higher. A single flashstep or equivalent, partial regeneration .
  - Against Absolute attack: the defense must be equivalent in scale. A large-area teleport, multiple large flash steps, full awakening, or equivalent power. Partial regeneration does NOT count but full regeneration does.
  - Against Over-Absolute: must leave the entire affected region or use reality-scale protection. Three flash steps are not enough.

### Tier Definitions
- **Normal:** A standard attack — sword swing, punch, small projectile. No city-scale effect.
- **Medium:** A strong attack that could destroy a building or is a charged/aura-enhanced technique.
- **Absolute:** A devastating attack that could destroy an entire city or large area.
- **Over-Absolute:** A ridiculously powerful attack — planet-scale or universe-scale, per character rules.

### Logic Rule
All actions must be physically and logically consistent with your character's abilities. You cannot guard a sword with your bare hands if your character has no enhanced durability. You cannot use an ability you have not established yet if your character's rules require setup first.
Treat every prerequisite as a hard gate. If a clip requires partial awakening, full awakening, Yoki release, or another established ability and that state is not listed as established, do not select the clip even if its tier or description looks attractive.
You also cannot revert back the transformation if you fully transform unless you have a clip that does that, once you transform you can only use the clips that are in that transofrmation.
""".strip()

_ROLEPLAY_RULES = """
## Roleplay and Character Behavior

Combat is important, but this is also an in-character anime scene. Do not reduce every turn to an attack calculation.

- Stay in character at all times. Use the personality, history, emotional triggers, abilities, and limitations in the character rules above.
- React to the opponent's taunts, dialogue, gestures, psychological pressure, and visible behavior when there is something meaningful to answer.
- Use the opponent's dialogue and video analysis as scene context, not only as combat data.
- When appropriate, choose a custom or RP clip to answer the opponent, establish mood, threaten, observe, reposition, show emotion, or advance the scene.
- If an available custom/RP video directly fits the opponent's taunt or scene, selecting that video is a strong plus. Dialogue can accompany it, but spoken dialogue alone should not be treated as the best possible roleplay response when a fitting clip exists.
- When the opponent clearly speaks or delivers a meaningful taunt, claim, threat, or manipulation, do not ignore it. Directly acknowledge or answer that specific input through in-character dialogue, a custom/RP clip, or both but prefer to use an appropaite rp if it exists.
- Treat meaningful statements from the named opponent as deliberate character behavior or psychological manipulation when the scene supports that reading. Answer the specific claim in the AI character's own personality instead of responding as if no dialogue occurred.
- This trigger is situational, not constant: if the opponent is silent or the line has no meaningful connection to the scene, combat or silent observation is fine.
- An attack is allowed once per turn, but it is not mandatory in an absolute sense. However, when no mandatory defense prevents it and a legal attack is tactically reasonable, Clare should normally attack because she is trying to win.
- Keep dialogue specific to the current exchange. Do not invent dialogue that contradicts the selected clip.
- Review the dedicated forbidden dialogue list below before writing. Exact repeats, lightly reworded repeats, and generic recycled phrases are invalid. Create a genuinely new response tied to the opponent's current words, or remain silent only when no response is needed.
- Let roleplay shape the action, but do not let roleplay replace a viable attack by default. A meaningful taunt can be answered with dialogue or a custom/RP clip alongside an attack.
Combat legality remains absolute: mandatory defenses come first, an attack can occur at most once, and all selected clips must obey the character's abilities and transformation rules.
""".strip()

# ---------------------------------------------------------------------------
# Output schema instruction — hardcoded
# ---------------------------------------------------------------------------
_OUTPUT_SCHEMA = """
## Required Output

Respond with valid JSON only. No markdown, no commentary outside the JSON.

```json
{
  "reasoning": "Your internal thought process — what you considered and why you chose these actions. 2-4 sentences.",
  "actions": [
    {
      "action_type": "attack | defense | custom",
      "tier": "Normal | Medium | Absolute | Over-Absolute | null",
      "clip_filename": "exact filename from the available clips list",
      "reasoning": "one sentence — why this specific clip for this action"
    }
  ],
    "dialogue": "Optional short in-character line Clare says out loud. Null if silent.",
    "opponent_analysis": [
        "One concise tactical analysis for each attached opponent video, in attachment order."
    ],
    "intro_clip_filename": "Optional exact filename from the Intros category, or null."
}
```

Rules for the output:
- `tier` is required for attack and defense actions. Set to null for custom/RP actions.
- `clip_filename` must exactly match a filename from the available clips list below.
- If opponent attacked last turn, your first action in the array MUST be a defense.
- List actions in the order they should be submitted (defense first if required).
- Keep `dialogue` in-character.
- Dialogue must be new for this match. Never repeat or lightly rephrase any line in `## Clare's Previous Dialogue (do not repeat)`.
- Use `custom` actions and RP clips when they are the best in-character response, but normally include one legal attack when Clare can safely and meaningfully advance toward victory.
- When the opponent roleplays or speaks, answer the specific scene or emotional beat when meaningful, while keeping any combat action legal.
- If the opponent's analyzed video contains meaningful spoken dialogue, respond in character when a response is appropriate, but do not force dialogue or an RP clip when the scene does not call for one.
- If a fitting RP/custom clip naturally answers a meaningful taunt or spoken line, consider including it. Otherwise, a concise in-character dialogue response or a focused combat action is valid.
- On early turns, prefer restrained Normal-tier attacks, observation, setup, or grounded RP over transformation-dependent Medium attacks. Escalate only when the match history establishes it or survival requires it.
- When opponent videos are attached, return one `opponent_analysis` entry per video before choosing actions.
- Identify the opponent by their known character name and explain what their video means tactically.
- If an optional intro is requested, keep it outside the actions array and return its filename in `intro_clip_filename`.
""".strip()


# ---------------------------------------------------------------------------
# Builder function
# ---------------------------------------------------------------------------

def build_prompt(
    *,
    character_rules: CharacterRules,
    opponent_profile: str | None,
    my_hp: int,
    opponent_hp: int,
    turn_number: int,
    opponent_attacked_last_turn: bool,
    opponent_last_attack_tier: str | None,
    opponent_last_attack_descriptions: list[str],
    opponent_dialogue: list[str],
    established_abilities: dict[str, bool],
    turn_history: list[str],
    available_clips: ClipCatalog,
    opponent_character_name: str = "the opponent",
    previous_ai_dialogues: list[str] | None = None,
) -> str:
    """Assemble and return the full prompt string.

    Parameters
    ----------
    character_rules:
        The CharacterRules for the AI's character.
    opponent_profile:
        The opponent character's profile text from the DB (or None if unavailable).
    my_hp:
        Current HP of the AI player.
    opponent_hp:
        Current HP of the opponent.
    turn_number:
        Current turn number.
    opponent_attacked_last_turn:
        Whether the opponent submitted an attack on their previous turn.
    opponent_last_attack_tier:
        The declared tier of the opponent's last attack (e.g. "Normal"), or None.
    opponent_last_attack_descriptions:
        List of AI-generated descriptions of the opponent's clips this turn.
    turn_history:
        List of plain-text summaries of previous turns, oldest first.
    available_clips:
        The ClipCatalog for the AI's character.
    """
    sections: list[str] = []

    # 1. Role block
    sections.append(
        f"You are the AI mind controlling **{character_rules.name}** from {character_rules.series} "
        f"in a 1v1 anime battle game on Discord. "
        f"Your job is to decide {character_rules.name}'s actions this turn by choosing from the available video clips below. "
        f"You must stay in character and follow both the game rules and your character's specific combat rules exactly."
    )

    # 2. Game rules
    sections.append(_GAME_RULES)

    # 3. Character rules
    sections.append(character_rules.render_for_prompt())

    # 4. Roleplay behavior
    sections.append(_ROLEPLAY_RULES)

    # 5. Opponent profile
    sections.append(
        f"## Opponent Identity\nThe opponent in this match is **{opponent_character_name}**. "
        "Use this identity and the profile below when interpreting their appearance, dialogue, motives, and tactics."
    )
    if opponent_profile:
        sections.append("## Opponent Character Profile")
        sections.append(opponent_profile)
    else:
        sections.append("## Opponent Character Profile\nNo profile available. Use general combat logic.")

    if previous_ai_dialogues:
        sections.append(
            "## Clare's Previous Dialogue (do not repeat)\n"
            "These lines have already been used in this match. Exact repeats, close paraphrases, "
            "and the same sentence structure are forbidden:\n"
            + "\n".join(f"{index}. {line}" for index, line in enumerate(previous_ai_dialogues, 1))
        )

    # 6. Match state
    state_lines = [
        "## Current Match State",
        f"- Turn: {turn_number}",
        f"- Your HP: {my_hp}",
        f"- Opponent HP: {opponent_hp}",
    ]
    if opponent_attacked_last_turn and opponent_last_attack_tier:
        state_lines.append(
            f"- **Opponent attacked last turn at tier: {opponent_last_attack_tier}. "
            f"Your FIRST action this turn MUST be a defense of equal or greater tier.**"
        )
    elif not opponent_attacked_last_turn:
        state_lines.append("- Opponent did not attack last turn. No mandatory defense this turn.")
    sections.append("\n".join(state_lines))

    # 7. Opponent actions this turn
    if opponent_last_attack_descriptions:
        opp_lines = ["## Opponent's Clips This Turn (AI descriptions — use as context, not gospel)"]
        opp_lines.append(
            "Note: these descriptions are AI-generated and may not be 100% accurate. "
            "The declared tier is more reliable. If something seems off, you can flag it."
        )
        for i, desc in enumerate(opponent_last_attack_descriptions, 1):
            opp_lines.append(f"{i}. {desc}")
        sections.append("\n".join(opp_lines))

    # 8. Opponent dialogue/psychology
    if opponent_dialogue:
        dialogue_lines = ["## Opponent's Dialogue This Turn"]
        dialogue_lines.append(
            "The opponent said these things. Consider them when deciding your actions and dialogue."
        )
        for i, line in enumerate(opponent_dialogue, 1):
            dialogue_lines.append(f"{i}. \"{line}\"")
        sections.append("\n".join(dialogue_lines))

    # 9. Established abilities/combat state
    if established_abilities:
        ability_lines = ["## Established Abilities/Combat State"]
        ability_lines.append("These abilities have been shown and are available for use:")
        for ability, established in sorted(established_abilities.items()):
            if established:
                ability_lines.append(f"- ✓ {ability}")
            else:
                ability_lines.append(f"- ✗ {ability} (not yet shown)")
        
        # Add character-specific state
        if character_rules.requires_transformation_for_certain_actions:
            ability_lines.append("\n### Transformation Requirements")
            for ability, requirement in character_rules.requires_transformation_for_certain_actions.items():
                ability_lines.append(f"- {ability}: {requirement}")
        
        sections.append("\n".join(ability_lines))

    # 10. Turn history
    if turn_history:
        hist_lines = ["## Turn History (oldest first; compound context)"]
        hist_lines.append(
            "Each entry records both what the opponent did and how you answered, "
            "so the next decision is guided by cumulative runtime memory."
        )
        for entry in turn_history[-6:]:  # last 6 turns max — keep prompt concise
            hist_lines.append(f"- {entry}")
        sections.append("\n".join(hist_lines))

    # 11. Available clips
    sections.append(_build_available_clips_section(available_clips, character_rules, established_abilities))

    # 12. Output schema
    sections.append(_OUTPUT_SCHEMA)

    return "\n\n---\n\n".join(sections)


def _build_available_clips_section(catalog: ClipCatalog, rules: CharacterRules, established_abilities: dict[str, bool]) -> str:
    """Build the available actions section listing clips by category with descriptions."""
    lines = ["## Available Clips (choose from these only)"]
    lines.append(
        "Each clip is listed as: `filename` — description. "
        "The category tells you what type of action it is."
    )
    lines.append("**IMPORTANT: Once a clip is used, it cannot be used again in this match.**")

    available_clips_by_category = catalog.get_available_clips_by_category()
    
    for category, clips in available_clips_by_category.items():
        action_info = rules.category_to_action_type.get(category, {})
        action_type = action_info.get("action_type", "custom")
        tier = action_info.get("tier")

        # Check if this category has prerequisites
        prerequisites = rules.category_prerequisites.get(category, [])
        missing_prereqs = [p for p in prerequisites if not established_abilities.get(p, False)]
        
        tier_str = f" [{tier}]" if tier else ""
        prereq_str = ""
        if missing_prereqs:
            prereq_str = f" ⚠️ REQUIRES: {', '.join(missing_prereqs)}"
        
        lines.append(f"\n### {category} (action_type: {action_type}{tier_str}){prereq_str}")

        if not clips:
            lines.append("*(No available clips in this category)*")
            continue

        # Filter out clips that require transformations not yet established
        available_in_category = []
        for clip in clips:
            # Check if clip filename suggests it's from a transformed state
            clip_lower = clip.filename.lower()
            requires_awakening = any(
                term in clip_lower for term in ["final form", "ff", "awakened", "partial", "transformed"]
            )
            
            # Check if transformation is required and established
            if requires_awakening:
                if not established_abilities.get("partial_awakening_shown", False) and not established_abilities.get("full_awakening_shown", False):
                    continue  # Skip clips requiring transformation if not transformed
            
            available_in_category.append(clip)

        if not available_in_category and missing_prereqs:
            lines.append(f"*(Category locked — missing prerequisites: {', '.join(missing_prereqs)})*")
            continue
        elif not available_in_category:
            lines.append("*(No available clips in this category)*")
            continue

        for clip in available_in_category:
            desc = clip.description or "(no description)"
            # Truncate very long descriptions to keep prompt concise
            if len(desc) > 200:
                desc = desc[:197] + "..."
            lines.append(f"- `{clip.filename}` — {desc}")

    # Add warning if using clips from transformed state before transformation is established
    if rules.requires_transformation_for_certain_actions:
        lines.append("\n### ⚠️ Transformation Rules")
        lines.append("The following abilities require transformation:")
        for ability, requirements in rules.requires_transformation_for_certain_actions.items():
            lines.append(f"- {ability}: {requirements}")

    return "\n".join(lines)
