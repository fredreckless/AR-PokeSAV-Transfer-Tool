# Session 26: the Eon Ticket, received and collected (30 October 2026, afternoon)

**Set-up.**
- The overnight tool (`duo_events.py`, Reckless v4.2), with `.me3` files wrapped.
- English Ruby, on the cable's master end (as in session 25, runs 2–4); the save backed up first.
- One run: `python duo_events.py event --event "RS - Item Eon Ticket (ENG) (UK).me3"`, with the
  guided start-up.

**What the trace showed.**
- The file was recognised as a saved Mystery Event: a script for person 1 in Petalburg City Gym,
  and a Record Mixing gift of item 275 (Eon Ticket). It was sent wrapped, as a 342-byte script
  for English games.
- Handshaking at 4.2 s; **link up at 26.2 s, the DUO as player 1** (the game was master).
- The game's link player (link type 0x5501, English) at 26.5 s; ours sent straight after.
- "**sent the Mystery Event**" at 27.2 s, and "**the game closed the link**" at 27.8 s.
- The tool finished by itself and gave the directions to Norman.
- 4,498 transfers, **0 checksum mismatches**; the DUO's longest look away from the link while
  linked was 4 scanlines (about 292 µs).

**In the game.** Ruby showed its message and saved. In Petalburg City Gym, **Norman gave the
Eon Ticket.**

**What it means.**
- The e-Reader route works end to end on real hardware: link, transfer, the game running the
  script, saving, and the RAM script firing.
- It confirms session 25's reading: the `.me3` is the game's saved form, and wrapping it in
  `checkcompat`, `checksum`, `initramscript`, `setrecordmixinggift` and `end` makes Ruby store
  it as the e-Reader's event would have.
- The guided start-up and the automatic finish worked first time on hardware.

**Not yet checked.** The Record Mixing gift (the ticket spreading to another game).
