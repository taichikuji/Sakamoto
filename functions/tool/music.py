from asyncio import get_running_loop
from collections import deque
from random import shuffle
from typing import TYPE_CHECKING

from discord import Embed, Interaction, Member, VoiceState, app_commands
from discord.ext import commands
from yt_dlp import YoutubeDL

from ._audio_engine import get_audio_engine

if TYPE_CHECKING:
    from main import Sakamoto


class MusicCog(commands.Cog):
    """Cog for music playback and shared audio controls."""

    def __init__(self, bot: "Sakamoto"):
        self.bot = bot
        self.engine = get_audio_engine(bot)
        self.ydl_opts = {
            "format": "bestaudio/best",
            "default_search": "ytsearch",
            "noplaylist": True,
            "quiet": True,
            "nocheckcertificate": True,
            "no_warnings": True,
            "source_address": "0.0.0.0",
        }

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.guild_id is not None:
            return True
        await interaction.response.send_message(":x: Could not determine guild ID.", ephemeral=True)
        return False

    async def play_query_autocomplete(self, _interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        query = current.strip()
        if len(query) < 2:
            return []

        try:
            info = await get_running_loop().run_in_executor(None, self.search_source_autocomplete, query)
        except Exception:
            return []

        entries = info.get("entries") if isinstance(info, dict) else None
        if not entries:
            return []

        choices: list[app_commands.Choice[str]] = []
        seen_values: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "Unknown Title").strip()
            value = str(entry.get("webpage_url") or "").strip()
            fallback = str(entry.get("url") or "").strip()
            value = value or (fallback if "://" in fallback else f"https://www.youtube.com/watch?v={fallback}" if fallback else title)
            if len(value) > 100:
                value = title[:100].strip()
            if not value or value in seen_values:
                continue
            seen_values.add(value)
            choices.append(app_commands.Choice(name=title[:100] or "Unknown Title", value=value))
            if len(choices) == 5:
                break
        return choices

    @app_commands.command(name="play", description="Play a song or audio. Provide a search term or URL.")
    @app_commands.autocomplete(query=play_query_autocomplete)
    async def play(self, interaction: Interaction, query: str):
        if not query:
            await interaction.response.send_message(":x: You must provide a search term or URL.", ephemeral=True)
            return
        assert (guild_id := interaction.guild_id) is not None
        if not isinstance(user := interaction.user, Member):
            await interaction.response.send_message(":x: This command can only be used in a server.", ephemeral=True)
            return
        if not user.voice or not user.voice.channel:
            await interaction.response.send_message(":x: You need to be in a voice channel to use this command.", ephemeral=True)
            return

        await interaction.response.defer()
        channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            await interaction.followup.send(":x: This command must be used in a text channel.", ephemeral=True)
            return

        existing_vc = self.engine.voice_clients.get(guild_id)
        was_connected = existing_vc is not None and existing_vc.is_connected()
        if await self.engine.get_or_connect_voice_client(guild_id, user.voice.channel, interaction) is None:
            return

        try:
            info = await get_running_loop().run_in_executor(None, self.search_source, query)
            if not info:
                raise ValueError("Failed to extract information.")
            stream_url = info.get("url")
            if not (webpage_url := info.get("webpage_url")):
                raise ValueError("No webpage URL found.")
        except Exception as e:
            if not was_connected:
                await self.engine.disconnect_and_cleanup(guild_id)
            await interaction.followup.send(f":x: Failed to retrieve video. Error: {e}", ephemeral=True)
            return

        self.engine.command_channels[guild_id] = channel
        title = info.get("title", "Unknown Title")
        duration = info.get("duration_string", "N/A")
        await self.engine.enqueue_or_play(
            guild_id,
            source_url=webpage_url,
            title=title,
            duration=duration,
            stream_url=stream_url,
            followup=interaction.followup.send,
            refresh_stream=self.refresh_stream_url,
        )

    def search_source(self, query: str):
        with YoutubeDL(self.ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
        if info and "entries" in info:
            if not info["entries"]:
                raise ValueError("No results found.")
            return info["entries"][0]
        return info

    def search_source_autocomplete(self, query: str):
        opts = dict(self.ydl_opts)
        opts["extract_flat"] = True
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(f"ytsearch5:{query}", download=False)

    async def refresh_stream_url(self, source_url: str) -> str | None:
        info = await get_running_loop().run_in_executor(None, self.search_source, source_url)
        return info.get("url")

    def cog_unload(self):
        self.engine.unload()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: Member, before: VoiceState, after: VoiceState):
        await self.engine.handle_voice_state_update(member, before, after)

    @app_commands.command(name="stop", description="Stop the currently playing audio and disconnect.")
    async def stop(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if await self.engine.ensure_user_in_same_voice_channel(interaction, guild_id) is None:
            return
        await self.engine.disconnect_and_cleanup(guild_id)
        await interaction.response.send_message(":stop_button: Stopped and disconnected.")

    @app_commands.command(name="pause", description="Pause the currently playing audio.")
    async def pause(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if (vc := await self.engine.ensure_user_in_same_voice_channel(interaction, guild_id)) is None:
            return
        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message(":pause_button: Playback paused.")
        else:
            await interaction.response.send_message(":x: Nothing is currently playing.", ephemeral=True)

    @app_commands.command(name="resume", description="Resume paused audio.")
    async def resume(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if (vc := await self.engine.ensure_user_in_same_voice_channel(interaction, guild_id)) is None:
            return
        if vc.is_paused():
            vc.resume()
            await interaction.response.send_message(":arrow_forward: Playback resumed.")
        else:
            await interaction.response.send_message(":x: Playback is not paused.", ephemeral=True)

    @app_commands.command(name="queue", description="Show the current music queue. Displays up to 10 items and indicates if there are more.")
    async def queue(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None

        max_display = 10
        queue_items = []
        if guild_id in self.engine.currently_playing:
            _, title, duration = self.engine.currently_playing[guild_id]
            queue_items.append(f"**Now Playing:** {title} [{duration}]")

        queue = self.engine.queues.get(guild_id, ())
        for i, item in enumerate(list(queue)[:max_display]):
            queue_items.append(f"{i+1}. {item.title} [{item.duration}]")

        if len(queue) > max_display:
            queue_items.append(f"\n...and {len(queue) - max_display} more.")

        if not queue_items:
            await interaction.response.send_message(":x: The music queue is currently empty.")
        else:
            embed = Embed(
                title=":notes: Music Queue",
                description="\n".join(queue_items),
                color=self.bot.color,
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="skip", description="Skip the current song.")
    async def skip(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if (vc := await self.engine.ensure_user_in_same_voice_channel(interaction, guild_id)) is None:
            return
        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await interaction.response.send_message(":track_next: Skipped.")
        else:
            await interaction.response.send_message(":x: Nothing is currently playing.", ephemeral=True)

    @app_commands.command(name="shuffle", description="Shuffle the current music queue.")
    async def shuffle(self, interaction: Interaction):
        assert (guild_id := interaction.guild_id) is not None
        if guild_id in self.engine.queues and self.engine.queues[guild_id]:
            shuffled = list(self.engine.queues[guild_id])
            shuffle(shuffled)
            self.engine.queues[guild_id] = deque(shuffled)
            await interaction.response.send_message(":twisted_rightwards_arrows: Queue shuffled.")
        else:
            await interaction.response.send_message(":x: The music queue is currently empty.", ephemeral=True)


async def setup(bot: "Sakamoto"):
    """Add the MusicCog to the bot."""
    await bot.add_cog(MusicCog(bot))
