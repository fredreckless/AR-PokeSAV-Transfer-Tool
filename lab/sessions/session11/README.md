# Session 11: first real run of the adapter program (27 October 2026)

**Set-up.**
- Firmware v4.2 with the `adapter` program (`--adapter-trace`).
- Japanese Emerald with Mystery Gift newly enabled through the questionnaire.
- MGB-010 cable, small plug to small plug.
- The player went to the main menu, then ふしぎな おくりもの → じゅしん スポット, several times.

**What the trace showed.** Every time:
1. The ID exchange completes (as in session 10).
2. The game sends its first command, `0x3D`, which arrives intact.
3. The adapter's ready handshake never completes: the game never raises its SO line.
4. The game tries the command again, three attempts in all, as librfu does. Then it gives up.

There were no other commands.

**Interpretation.**
- The game clocks commands at **2 MHz**. It clocked the ID exchange at 256 kHz, and that works.
- Words from the game to the DUO arrive intact at 2 MHz.
- After the first word, librfu only goes on to the handshake if the adapter's answer was exactly
  `0x80000000`. A game that never raises SO therefore points to the DUO's answer arriving
  **wrong at 2 MHz**. The likeliest cause is a GBA clock slave answering too late for a 2 MHz clock
  over a link cable. gba-link-connection warns against GBA-to-GBA 2 Mbps without very short wires.
- The other possibility, the game not seeing the DUO's SO line, is less likely: the same lines
  worked in the ID exchange.

**Next.** A diagnostic program to tell a consistent one-bit lag (possibly workable) from random
errors (not workable over this cable).

Nothing was written to either cartridge.
