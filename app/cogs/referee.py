from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.match_manager_service import match_manager, MatchState
from core.config import settings

REFEREE_ROLE_NAME = "Referee"


# ---------------------------------------------------------------------------
# Role guard
# ---------------------------------------------------------------------------

def is_referee(interaction: discord.Interaction) -> bool:
    """Return True if the invoking member has the Referee role."""
    if not isinstance(interaction.user, discord.Member):
        return False
    return any(r.name == REFEREE_ROLE_NAME for r in interaction.user.roles)


def referee_only() -> app_commands.check:
    async def predicate(interaction: discord.Interaction) -> bool:
        if not is_referee(interaction):
            await interaction.response.send_message(
                "🚫 This command is restricted to referees only.", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_match(interaction: discord.Interaction) -> tuple[MatchState | None, str | None]:
    """Resolve match from the current channel. Returns (match, error)."""
    match_state = match_manager.get_match(interaction.channel_id)
    if not match_state:
        return None, "No active match found in this channel."
    return match_state, None


def _hp_bar(hp: int, max_hp: int = 4) -> str:
    return f"{'❤️' * hp}{'🖤' * (max_hp - hp)} ({hp}/{max_hp})"


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class RefereeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /ref_view ─────────────────────────────────────────────────────────────

    @app_commands.command(
        name="ref_view",
        description="[Referee] View the full current state of the match in this channel.",
    )
    @referee_only()
    async def ref_view(self, interaction: discord.Interaction) -> None:
        match_state, err = _get_match(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        pending_atk = match_state.pending_attack
        pending_str = (
            f"**{pending_atk['tier']}** from <@{match_state.pending_attacker_id}>"
            if pending_atk
            else "None"
        )

        current_actions = match_state.current_turn_actions
        actions_str = (
            " → ".join(
                a["action_type"].upper() + (f" ({a['tier']})" if a.get("tier") else "")
                for a in current_actions
            )
            if current_actions
            else "*(none yet this turn)*"
        )

        history_count = len(match_state.state_history)

        embed = discord.Embed(
            title=f"🧑‍⚖️ Referee View — Match {match_state.match_id}",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Status",
            value=(
                f"{'⏸️ **PAUSED**' if match_state.is_paused else '▶️ Active'} — "
                f"Turn **{match_state.current_turn}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="HP",
            value=(
                f"<@{match_state.player1_id}>: {_hp_bar(match_state.player1_hp)}\n"
                f"<@{match_state.player2_id}>: {_hp_bar(match_state.player2_hp)}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Active Player",
            value=f"<@{match_state.current_player_id}>",
            inline=True,
        )
        embed.add_field(
            name="Pending Attack",
            value=pending_str,
            inline=True,
        )
        embed.add_field(
            name="Current Turn Actions (in progress)",
            value=actions_str,
            inline=False,
        )
        embed.add_field(
            name="State History",
            value=(
                f"{history_count} snapshot(s) stored. "
                f"Valid rollback indices: 0 – {history_count - 1}."
            ),
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /ref_set_hp ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="ref_set_hp",
        description="[Referee] Manually set a player's HP.",
    )
    @app_commands.describe(
        player="The player whose HP to correct",
        hp="New HP value (0–4)",
    )
    @referee_only()
    async def ref_set_hp(
        self,
        interaction: discord.Interaction,
        player: discord.Member,
        hp: app_commands.Range[int, 0, 4],
    ) -> None:
        match_state, err = _get_match(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        if player.id not in (match_state.player1_id, match_state.player2_id):
            await interaction.response.send_message(
                "That player is not in this match.", ephemeral=True
            )
            return

        if player.id == match_state.player1_id:
            old_hp = match_state.player1_hp
            match_state.player1_hp = hp
        else:
            old_hp = match_state.player2_hp
            match_state.player2_hp = hp

        await interaction.response.send_message(
            f"🧑‍⚖️ Referee correction: <@{player.id}>'s HP changed from "
            f"**{old_hp}** → **{hp}**."
        )

    # ── /ref_declare_winner ───────────────────────────────────────────────────

    @app_commands.command(
        name="ref_declare_winner",
        description="[Referee] Declare a winner and end the match.",
    )
    @app_commands.describe(winner="The player to declare as winner")
    @referee_only()
    async def ref_declare_winner(
        self,
        interaction: discord.Interaction,
        winner: discord.Member,
    ) -> None:
        match_state, err = _get_match(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        if winner.id not in (match_state.player1_id, match_state.player2_id):
            await interaction.response.send_message(
                "That player is not in this match.", ephemeral=True
            )
            return

        loser_id = (
            match_state.player2_id
            if winner.id == match_state.player1_id
            else match_state.player1_id
        )

        match_state.status = "finished"
        match_manager.remove_match(match_state.match_id)

        await interaction.response.send_message(
            f"🧑‍⚖️ **Referee Decision:** <@{winner.id}> is declared the winner!\n"
            f"<@{loser_id}> loses. Match is now closed."
        )

    # ── /ref_resume ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="ref_resume",
        description="[Referee] Resume a paused match.",
    )
    @referee_only()
    async def ref_resume(self, interaction: discord.Interaction) -> None:
        match_state, err = _get_match(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        if not match_state.is_paused:
            await interaction.response.send_message(
                "This match is not currently paused.", ephemeral=True
            )
            return

        match_state.is_paused = False
        match_state.has_objection = False

        await interaction.response.send_message(
            f"▶️ **Match resumed by referee <@{interaction.user.id}>.**\n"
            f"<@{match_state.current_player_id}> — it's your turn!"
        )

    # ── /ref_rollback ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="ref_rollback",
        description="[Referee] Roll back the match to a previous turn snapshot.",
    )
    @app_commands.describe(
        snapshot_index="The snapshot index to restore (use /ref_view to see valid indices)",
    )
    @referee_only()
    async def ref_rollback(
        self,
        interaction: discord.Interaction,
        snapshot_index: int,
    ) -> None:
        match_state, err = _get_match(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        max_index = len(match_state.state_history) - 1
        if snapshot_index < 0 or snapshot_index > max_index:
            await interaction.response.send_message(
                f"Invalid snapshot index. Valid range: 0 – {max_index}.\n"
                f"Use `/ref_view` to see the available snapshots.",
                ephemeral=True,
            )
            return

        success = match_state.restore_snapshot(snapshot_index)
        if not success:
            await interaction.response.send_message(
                "Failed to restore snapshot. Please try again.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"⏪ **Referee rolled back match to snapshot {snapshot_index}** "
            f"(Turn {match_state.current_turn}).\n"
            f"HP restored — <@{match_state.player1_id}>: **{match_state.player1_hp}** HP | "
            f"<@{match_state.player2_id}>: **{match_state.player2_hp}** HP.\n"
            f"▶️ <@{match_state.current_player_id}> — it's your turn!"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RefereeCog(bot))
