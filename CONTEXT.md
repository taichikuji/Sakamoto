# Sakamoto Domain Context

Sakamoto is a voice-first Discord bot. Use the following terms consistently.

| Term | Meaning | Avoid |
| --- | --- | --- |
| **Generator Channel** | Voice channel that creates a member-owned Temporary Lobby when joined. | Spawn channel, auto-room |
| **Temporary Lobby** | Short-lived voice channel deleted when empty. | Private room, session room |
| **Lobby Owner** | Member who created a Temporary Lobby and manages it. | Host, operator |
| **Playback Session** | Per-guild music connection, current track, and queue. | Player instance, stream job |
| **Queue Entry** | Requested track waiting in a Playback Session. | Playlist item, ticket |
| **Voice Votekick** | Time-bounded vote by channel participants to remove a member from that channel. | Ban vote, timeout vote |
| **Temporary Rejoin Ban** | Short-lived channel permission block after a Voice Votekick. | Permanent ban, mute |
| **Server Moderator** | Member with elevated Discord permissions who configures or safeguards the bot. | Owner*, staff |
| **Steam Link** | Persisted mapping of a Discord user to a SteamID64. | Steam account cache, token |
| **Pipenv Environment** | Repository's canonical dependency and command environment. | Global pip, ad-hoc virtualenv |
| **Optional Integration** | Feature module that may be unavailable without harming core voice use. | Required module, core dependency |
| **Degraded Capability** | Non-core feature unavailable while core slash commands and voice workflows continue. | Outage, crash |

\* Use “owner” only for the literal Discord server owner.

## Relationships

- A Generator Channel can create many Temporary Lobbies; each lobby has one initial Lobby Owner.
- A guild has zero or one active Playback Session, containing zero or more Queue Entries.
- A user has zero or one Steam Link.
- A successful Voice Votekick creates one Temporary Rejoin Ban for its target in that channel.
- Optional Integration failures are Degraded Capabilities unless core workflows fail.
- Install dependencies and run commands through Pipenv (`pipenv sync`, `pipenv run ...`).

## Resolved ambiguities

- “Lobby” means Generator Channel (trigger) or Temporary Lobby (generated), never both.
- “Kick” means Voice Votekick for voice-only removal; use Discord server kick for the server action.
- Do not call optional-integration failure a “broken bot” unless core voice flows fail.
