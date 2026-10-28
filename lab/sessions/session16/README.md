# Session 16: the disarming flick (28 October 2026, afternoon)

**Set-up.**
- `--adapter-trace` with the session-15 change: the ready flick disarms for a moment.
- No Wonder Card loaded.
- Japanese Emerald, MGB-010 cable, Mystery Gift → じゅしん スポット.
- Two runs. Between them, two attempts timed out on USB before connecting.

**What the trace showed.**
- **The flick works.** These are the first answers after a ready handshake that the game has
  ever taken:

  | Width | Flicks | Clocked | Accepted | Refused |
  |---|---|---|---|---|
  | 0 (two stores, ~0.3 µs) | 19 and 23 | all | 17 and 19 | 2 and 4 |
  | 1 | 2 | 2 | 1 | 1 |

- **A whole command sequence completed for the first time:**
  - Hello (0x10);
  - setup (0x17) 003C0420;
  - broadcast data (0x16), 6 words;
  - start searching (0x1C).
- **It then stops at `search results? (0x1D)`**, the first command with a long answer (7 words:
  host ID, then the distributor's broadcast data). Each time it goes "the game stopped answering
  in the middle of a command", then "asked for more of an answer than the adapter sent". The game
  retries twice, gives up, and puts the adapter to sleep.
- **"unknown command (0xBD)"** appears after each sleep (0x3D). That's the low byte of our
  sleep acknowledgement, 0x996600BD, which came back to us as if it were a command. Not
  explained yet.
- **The scope.** Clocked words show the game going low within one sample. Each "not clocked"
  line shows the game's SO toggling (`-----__-___-____-_-___---___…`) with no word arriving: the
  game is clocking while we are disarmed. So some flicks do lose words, even at width 0.

**Next (for the fresh chat).**
- Find which of 0x1D's seven answer words fails. Words with a top bit of 0 are armed plainly;
  top-bit-1 words need the flick.
- Possible causes:
  - Every flick in a long answer is another chance to lose a word. Even at width 0, about 1 in
    10 is refused.
  - The game may react to a flick faster mid-answer than at its start.
- Possible fixes:
  - A shorter or differently placed disarm.
  - Choosing broadcast data and IDs whose words start with 0. That avoids flicks wherever we
    choose the values, for example the host ID (already 0x7DA5) and the padding.
  - Recording, per word index, which flick lost the word.

Nothing was written to either cartridge.
