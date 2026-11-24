from asyncio import Lock
from itertools import batched
from typing import TYPE_CHECKING

from discord import Embed, HTTPException, Interaction, Message, app_commands, ui
from discord.ext import commands

if TYPE_CHECKING:
    from main import Sakamoto


def command_usage(command: app_commands.Command) -> str:
    """Return slash-command usage from Discord's registered parameters."""
    parameters = [
        f"<{parameter.name}>" if parameter.required else f"[{parameter.name}]"
        for parameter in command.parameters
    ]
    return " ".join((f"/{command.qualified_name}", *parameters))


def parameter_details(command: app_commands.Command) -> str:
    """Return parameter descriptions and defaults from command metadata."""
    details = []
    for parameter in command.parameters:
        description = parameter.description or "No description provided."
        if not parameter.required and parameter.default is not None:
            description += f" Default: `{parameter.default}`."
        required = "required" if parameter.required else "optional"
        details.append(f"`{parameter.name}` ({required}) — {description}")
    return "\n".join(details)


def normalize_command_name(command_name: str) -> str:
    """Normalize input such as `/radio   search` for command lookup."""
    return " ".join(command_name.strip().removeprefix("/").lower().split())


def subcommand_details(group: app_commands.Group) -> str:
    return "\n".join(
        f"`{command_usage(command)}` — {command.description}"
        for command in group.walk_commands()
        if isinstance(command, app_commands.Command)
    )


class HelpPages(ui.View):
    def __init__(self, pages: list[Embed], requester_id: int):
        super().__init__()
        self.pages = pages
        self.requester_id = requester_id
        self.index = 0
        self.page_lock = Lock()
        self.message: Message | None = None

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Run `/help` to open your own pages.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        async with self.page_lock:
            for item in self.children:
                if isinstance(item, ui.Button):
                    item.disabled = True
            if self.message is not None:
                try:
                    await self.message.edit(view=self)
                except HTTPException:
                    pass

    async def turn_page(self, interaction: Interaction, step: int) -> None:
        async with self.page_lock:
            if interaction.message is not None:
                # Edit the channel message after the original slash token expires.
                self.message = interaction.message
            last = len(self.pages) - 1
            self.index = max(0, min(last, self.index + step))
            self.previous.disabled = self.index == 0
            self.next.disabled = self.index == last
            await interaction.response.edit_message(
                embed=self.pages[self.index], view=self
            )

    @ui.button(label="Previous", disabled=True)
    async def previous(self, interaction: Interaction, _button: ui.Button) -> None:
        await self.turn_page(interaction, -1)

    @ui.button(label="Next")
    async def next(self, interaction: Interaction, _button: ui.Button) -> None:
        await self.turn_page(interaction, 1)


class HelpCog(commands.Cog):
    """Cog that displays usage from registered application-command metadata."""

    def __init__(self, bot: Sakamoto):
        self.bot = bot

    async def command_name_autocomplete(
        self, _interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Suggest commands and subcommands matching the current input."""
        query = normalize_command_name(current)
        return [
            app_commands.Choice(
                name=f"/{command.qualified_name}", value=command.qualified_name
            )
            for command in self.bot.tree.walk_commands()
            if query in command.qualified_name.casefold()
        ][:25]

    @app_commands.command(
        name="help",
        description="Shows a list of available commands or details about a specific command.",
    )
    @app_commands.describe(
        command_name="Command or subcommand to describe, such as `play`."
    )
    @app_commands.autocomplete(command_name=command_name_autocomplete)
    async def show_help(
        self, interaction: Interaction, command_name: str | None = None
    ):
        """Show the command overview or a named command's usage."""
        if command_name is None:
            batches = list(batched(self.bot.tree.get_commands(), 5)) or [()]
            pages = []
            for index, batch in enumerate(batches, start=1):
                embed = Embed(
                    title="Help",
                    description="Use `/help <command>` for usage and parameter details.",
                    color=self.bot.color,
                )
                for command in batch:
                    value = command.description
                    if isinstance(command, app_commands.Group):
                        value += f"\n{subcommand_details(command)}"
                    else:
                        value += f"\nUsage: `{command_usage(command)}`"
                    embed.add_field(name=f"/{command.name}", value=value, inline=False)
                if len(batches) > 1:
                    embed.set_footer(text=f"Page {index} of {len(batches)}")
                pages.append(embed)
            if len(pages) > 1:
                view = HelpPages(pages, interaction.user.id)
                response = await interaction.response.send_message(
                    embed=pages[0], view=view
                )
                if isinstance(response.resource, Message):
                    view.message = response.resource
            else:
                await interaction.response.send_message(embed=pages[0])
            return

        normalized_name = normalize_command_name(command_name)
        parts = normalized_name.split()
        command = self.bot.tree.get_command(parts[0]) if parts else None
        for part in parts[1:]:
            command = (
                command.get_command(part)
                if isinstance(command, app_commands.Group)
                else None
            )
            if command is None:
                break

        if command is None:
            await interaction.response.send_message(
                f":x: Command `{command_name}` not found.", ephemeral=True
            )
            return

        if isinstance(command, app_commands.Group):
            embed = Embed(
                title=f"Help: /{command.qualified_name}",
                description=command.description,
                color=self.bot.color,
            )
            embed.add_field(
                name="Subcommands",
                value=subcommand_details(command),
                inline=False,
            )
        else:
            embed = Embed(
                title=f"Help: /{command.qualified_name}",
                description=command.description,
                color=self.bot.color,
            )
            embed.add_field(
                name="Usage", value=f"`{command_usage(command)}`", inline=False
            )
            if details := parameter_details(command):
                embed.add_field(name="Parameters", value=details, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: Sakamoto):
    """Add the HelpCog to the bot."""
    await bot.add_cog(HelpCog(bot))
