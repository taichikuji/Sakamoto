from typing import TYPE_CHECKING

from discord import Embed, Interaction, app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import Sakamoto


def command_usage(command: app_commands.Command) -> str:
    """Return slash-command usage from Discord's registered parameters."""
    parameters = [
        f"<{parameter.name}>" if parameter.required else f"[{parameter.name}]"
        for parameter in command.parameters.values()
    ]
    return " ".join((f"/{command.qualified_name}", *parameters))


def parameter_details(command: app_commands.Command) -> str:
    """Return parameter descriptions and defaults from command metadata."""
    details = []
    for parameter in command.parameters.values():
        description = parameter.description or "No description provided."
        if not parameter.required and parameter.default is not None:
            description += f" Default: `{parameter.default}`."
        required = "required" if parameter.required else "optional"
        details.append(f"`{parameter.name}` ({required}) — {description}")
    return "\n".join(details)


def normalize_command_name(command_name: str) -> str:
    """Normalize input such as `/radio   search` for command lookup."""
    return " ".join(command_name.strip().removeprefix("/").lower().split())


class HelpCog(commands.Cog):
    """Cog that displays usage from registered application-command metadata."""

    def __init__(self, bot: "Sakamoto"):
        self.bot = bot

    @app_commands.command(
        name="help",
        description="Shows a list of available commands or details about a specific command.",
    )
    @app_commands.describe(command_name="Command or subcommand to describe, such as `play`.")
    async def show_help(self, interaction: Interaction, command_name: str | None = None):
        """Show the command overview or a named command's usage."""
        if command_name is None:
            embed = Embed(
                title="Help",
                description="Use `/help <command>` for usage and parameter details.",
                color=self.bot.color,
            )
            for command in self.bot.tree.get_commands():
                value = command.description
                if isinstance(command, app_commands.Group):
                    value = "\n".join(
                        (
                            command.description,
                            *(
                                f"`{command_usage(subcommand)}` — {subcommand.description}"
                                for subcommand in command.commands
                            ),
                        )
                    )
                else:
                    value += f"\nUsage: `{command_usage(command)}`"
                embed.add_field(name=f"/{command.name}", value=value, inline=False)
            await interaction.response.send_message(embed=embed)
            return

        normalized_name = normalize_command_name(command_name)
        parts = normalized_name.split()
        command = self.bot.tree.get_command(parts[0]) if parts else None
        for part in parts[1:]:
            command = command.get_command(part) if isinstance(command, app_commands.Group) else None
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
                value="\n".join(
                    f"`{command_usage(subcommand)}` — {subcommand.description}"
                    for subcommand in command.commands
                ),
                inline=False,
            )
        else:
            embed = Embed(
                title=f"Help: /{command.qualified_name}",
                description=command.description,
                color=self.bot.color,
            )
            embed.add_field(name="Usage", value=f"`{command_usage(command)}`", inline=False)
            if details := parameter_details(command):
                embed.add_field(name="Parameters", value=details, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: "Sakamoto"):
    """Add the HelpCog to the bot."""
    await bot.add_cog(HelpCog(bot))
