from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.match.match_manager_service import match_manager, MatchState
from boss.boss_manager import boss_manager
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
    """Resolve match from the current channel. Checks PvP matches first, then boss fights."""
    match_state = match_manager.get_match_for_interaction(interaction)
    if match_state:
        return match_state, None
    boss_state = boss_manager.get_fight(interaction.channel_id)
    if boss_state:
        return boss_state, None
    return None, "No active match found in this channel."


def _remove_match(match_state: MatchState) -> None:
    """Remove a match from whichever store (PvP or boss) holds it."""
    from boss.boss_state import BossState as _BossState
    if isinstance(match_state, _BossState):
        boss_manager.remove_fight(match_state.match_id)
    else:
        match_manager.remove_match(match_state.match_id)


def _hp_bar(hp: int, max_hp: int = 4) -> str:
    filled = min(hp, max_hp)
    return f"{'❤️' * filled}{'🖤' * (max_hp - filled)} ({hp}/{max_hp})"


def _describe_player(match_state: MatchState, player_id: int) -> str:
    """Format a player for referee messages, using the boss display name when relevant."""
    from boss.boss_config import BOSS_PLAYER_ID
    from boss.boss_state import BossState as _BossState

    if isinstance(match_state, _BossState) and player_id == BOSS_PLAYER_ID:
        return f"**{match_state.boss_config.display_name}** (Boss)"
    return f"<@{player_id}>"


def _get_max_hp(match_state, player_id: int) -> int:
    """Return the starting HP for a player, derived from snapshot 0."""
    from boss.boss_state import BossState as _BossState
    from boss.boss_config import BOSS_PLAYER_ID
    if isinstance(match_state, _BossState) and player_id == BOSS_PLAYER_ID:
        return match_state.boss_config.hp
    if match_state.state_history:
        snap = match_state.state_history[0]
        if player_id == match_state.player1_id:
            return snap["player1_hp"]
        return snap["player2_hp"]
    return 4


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
            f"**{pending_atk['tier']}** from {_describe_player(match_state, match_state.pending_attacker_id)}"
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
                f"{_describe_player(match_state, match_state.player1_id)}: {_hp_bar(match_state.player1_hp, _get_max_hp(match_state, match_state.player1_id))}\n"
                f"{_describe_player(match_state, match_state.player2_id)}: {_hp_bar(match_state.player2_hp, _get_max_hp(match_state, match_state.player2_id))}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Active Player",
            value=_describe_player(match_state, match_state.current_player_id),
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

        # For boss fights, route through the boss-aware handler so the
        # victory/defeat clip fires before the fight is torn down.
        from boss.boss_state import BossState as _BossState
        from app.cogs.boss_battle import _handle_match_over as _boss_match_over
        if isinstance(match_state, _BossState):
            await interaction.response.send_message(
                f"🧑‍⚖️ **Referee Decision:** <@{winner.id}> is declared the winner!\n"
                f"<@{loser_id}> loses. Match is now closed."
            )
            await _boss_match_over(match_state, interaction.channel, winner.id)
            return

        match_state.status = "finished"
        _remove_match(match_state)

        await interaction.response.send_message(
            f"🧑‍⚖️ **Referee Decision:** <@{winner.id}> is declared the winner!\n"
            f"<@{loser_id}> loses. Match is now closed."
        )

    # ── /ref_boss_wins ────────────────────────────────────────────────────────

    @app_commands.command(
        name="ref_boss_wins",
        description="[Referee] Declare the boss as winner and end the boss fight.",
    )
    @referee_only()
    async def ref_boss_wins(self, interaction: discord.Interaction) -> None:
        from boss.boss_state import BossState as _BossState

        match_state, err = _get_match(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        if not isinstance(match_state, _BossState):
            await interaction.response.send_message(
                "❌ This command can only be used in a boss fight channel.", ephemeral=True
            )
            return

        from boss.boss_config import BOSS_PLAYER_ID as _BOSS_ID
        from app.cogs.boss_battle import _handle_match_over as _boss_match_over

        await interaction.response.send_message(
            f"🧑‍⚖️ **Referee Decision:** **{match_state.boss_config.display_name}** is declared the winner!\n"
            f"<@{match_state.player1_id}> loses. Boss fight is now closed."
        )
        await _boss_match_over(match_state, interaction.channel, _BOSS_ID)

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
            f"{_describe_player(match_state, match_state.current_player_id)} — it's your turn!"
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
            f"HP restored — {_describe_player(match_state, match_state.player1_id)}: **{match_state.player1_hp}** HP | "
            f"{_describe_player(match_state, match_state.player2_id)}: **{match_state.player2_hp}** HP.\n"
            f"▶️ {_describe_player(match_state, match_state.current_player_id)} — it's your turn!"
        )

        # ── If this is a boss fight and the rollback landed on the boss's turn,
        #    trigger the boss AI automatically — otherwise the fight freezes.
        from boss.boss_state import BossState as _BossState
        from boss.boss_config import BOSS_PLAYER_ID
        if (
            isinstance(match_state, _BossState)
            and match_state.current_player_id == BOSS_PLAYER_ID
        ):
            boss_cog = interaction.client.get_cog("BossBattleCog")
            if boss_cog:
                await interaction.channel.send("⚙️ **The boss is responding…**")
                await boss_cog._run_boss_turn_and_post(match_state, interaction.channel)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RefereeCog(bot))
