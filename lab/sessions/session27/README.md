# Session 27: the first Enigma Berry (30 October 2026, evening)

**Set-up.**
- `duo_events.py` with `.ecb` support (commit 9b66c7e), Reckless v4.2.
- English Ruby; the GBA link cable as in session 26 (not moved since); the save backed up first.
- One run: `python duo_events.py event --event "...\08-K001 RS - Pumkin Berry (USA).ecb"`.

**What the trace showed.**
- The file was recognised as a saved Enigma Berry, the PUMKIN BERRY (sour 40), and sent as a
  1,364-byte Mystery Event script for English games.
- Handshaking at 7.5 s; link up at 26.2 s, **reported as "the DUO is player 0"**, with the
  warning to swap the cable's ends.
- The game's link player (0x5501, English) and ours at 26.6 s; "sent the Mystery Event" and "the
  game closed the link" at 28.1 s. The tool finished by itself.
- 5,310 transfers, **0 checksum mismatches**; longest look away 4 scanlines.

**In the game.** Ruby showed its message and saved. In Petalburg City Gym, **Norman gave the
PUMKIN BERRY.**

**What it means.**
- `.ecb` files work end to end on real hardware: the second file type of the goal.
- **The "player 0" report was wrong.** Ruby's menu only carries on as the link master, and it did;
  the data went across with 0 mismatches, and the cable hadn't moved since session 26 ("player 1").
  The program decides its role from SIOCNT's SI bit on every pass of its loop, and the tool reads
  that bit once, at link-up. In multi-player mode the other GBA drives SI during a transfer, so a
  read at the wrong moment can look like "master". The dependable signal is the player ID the GBA
  writes into SIOCNT (bits 4-5) after each transfer. **Not yet fixed** (it needs a change to
  `ereader.c`); the warning can be ignored when the event goes through.
