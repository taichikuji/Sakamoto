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

Pending Temporary Rejoin Bans persist in SQLite so their cleanup survives a bot restart.

## Maintainer preferences

- `/load`, `/unload`, and `/reload` are core operational features. They allow a malfunctioning extension to be replaced without restarting the bot.
- `/shutdown` is also intentional: it stops the process so Docker can restart a malfunctioning bot.
- Persist unfinished actions only when process death would otherwise lose required cleanup, as with Temporary Rejoin Bans. Cover failure classes broadly; add scenario-specific machinery only after a real need appears.
- An optional integration that requires an API token must fail its own extension setup when the token is absent, while allowing the rest of the bot to load normally.
- Caches that reduce third-party requests are operational safeguards, not disposable optimizations. Keep freshness policies separate where the data differs: ordinary AniList searches may be cached for a week, while the weekly schedule must refresh much sooner.
- Preserve request coalescing and autocomplete cache seeding when they prevent duplicate API calls. Music source caching should remain bounded and respect each signed stream URL's expiry to limit YouTube extraction traffic.
- Prefer the smallest working implementation, existing code, standard-library or platform features, and no new dependency unless it provides a demonstrated benefit. Do not remove an intentional feature merely because it adds code.
- Use a regular Discord bot until actual guild scale warrants sharding; do not enable sharding only as a precaution.

## Music runtime behavior

- yt-dlp resolves media outside the event loop; only compact playback metadata is cached, with a 256-entry cap and automatic expiry.
- FFmpeg runs as a child process during playback and exits when playback stops; disconnect cleanup removes the guild's Playback Session and clears its queue, current track, and command channel.
- Repeated extraction and playback can leave the process at a higher RSS plateau after live objects are released because Python and glibc may retain freed pages. Stable elevated RSS alone does not establish a live-object leak.

### YouTube JavaScript runtime fallback

A 2026-09-11 Docker benchmark on Linux ARM64 used yt-dlp 2026.8.19 and the bot's normal search and audio-format settings. Each variant ran in three fresh containers against `charlie kirk slowed 1 hour`; every run selected the same format 251 Opus stream and passed an FFmpeg read probe.

| Variant | Median extraction | Approximate image increase | Result |
| --- | ---: | ---: | --- |
| No EJS or runtime | 4.444 s | baseline | Pass |
| EJS 0.8.0 only | 3.449 s | 0.06 MB | Pass; EJS cannot run |
| QuickJS 2025-04-26 + EJS | 4.492 s | 1.03 MB | Pass |
| Deno 2.9.4 + EJS | 3.711 s | 44.36 MB | Pass |
| Node 24.21.0 + EJS | 3.307 s | 45.95 MB | Pass |
| Bun 1.3.14 + EJS | 5.722 s | 37.37 MB | Pass; deprecated by yt-dlp |

The default YouTube client did not invoke its challenge solver, so small timing differences are network noise rather than evidence that one runtime is faster. A forced `web` client failed both without a runtime and with QuickJS because of SABR/PO-token restrictions; adding a JavaScript runtime does not fix that failure class.

Keep the image runtime-free while normal extraction and playback pass. If a real URL fails specifically because JavaScript challenge solving is unavailable, retest that URL with current versions and add matching `yt-dlp-ejs` plus QuickJS first because its image cost is smallest. Try Deno next if QuickJS fails or is too slow. Node has no demonstrated advantage here, and Bun should not be selected while deprecated.

## Resolved ambiguities

- “Lobby” means Generator Channel (trigger) or Temporary Lobby (generated), never both.
- “Kick” means Voice Votekick for voice-only removal; use Discord server kick for the server action.
