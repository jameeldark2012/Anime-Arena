import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv("config.env")
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = 1547419026609672222  # replace with your actual server ID

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.tree.command(name="ping", description="Test command")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Beep test test test")

@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

bot.run(TOKEN)