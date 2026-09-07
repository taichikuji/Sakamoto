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

\* Use “owner” only for the literal Discord server owner.

## Relationships

- A Generator Channel can create many Temporary Lobbies; each lobby has one initial Lobby Owner.
- A guild has zero or one active Playback Session, containing zero or more Queue Entries.
- A user has zero or one Steam Link.
- A successful Voice Votekick creates one Temporary Rejoin Ban for its target in that channel.

## Music runtime behavior

- yt-dlp resolves media outside the event loop; only compact playback metadata is cached, with a 256-entry cap and automatic expiry.
- FFmpeg runs as a child process during playback and exits when playback stops; disconnect cleanup removes the guild's Playback Session and clears its queue, current track, and command channel.
- Repeated extraction and playback can leave the process at a higher RSS plateau after live objects are released because Python and glibc may retain freed pages. Stable elevated RSS alone does not establish a live-object leak.

## Resolved ambiguities

- “Lobby” means Generator Channel (trigger) or Temporary Lobby (generated), never both.
- “Kick” means Voice Votekick for voice-only removal; use Discord server kick for the server action.
