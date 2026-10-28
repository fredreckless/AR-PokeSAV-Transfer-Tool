# Session 19: the serial sweep finds 0x7F7F, and the whole link runs (28 October 2026, about 21:30)

**Set-up.**
- `--trace --sweep-serial` (commit with the 512-serial blocks).
- Japanese Emerald, MGB-010 cable, Mystery Gift → じゅしん スポット.
- No Wonder Card loaded.

**What the trace showed.**
- `0x1D` was answered 21,803 times while the sweep went through the serials, with no
  interruptions. Flicks: 23,787 of 23,789 accepted.
- At 374 s: `stop searching (0x1E)`, then **`connect (0x1F) 00007F7F`**. The Japanese Emerald's
  distributor serial is **0x7F7F** (the English decomp's is 0x7F7D, which BPEJ doesn't contain).
- Then, **on real hardware for the first time, every stage that had only run in the simulator**:
  - connected (child ID 0x2C41);
  - the game's name arrived over NI (serial 0002, then the player's name);
  - "may join" over NI, acknowledged;
  - link players exchanged (the player's trainer ID and name, language 1);
  - Mystery Gift server steps 1 to 4 (no card loaded, so "nothing to send"), then ready to close,
    agreed, and the game disconnected (`0x30`).
  - 3 packets were sent again, and the link carried on.

**Change.** `SERIAL_WONDER_DISTRIBUTOR` is now 0x7F7F. The game model uses
`JP_DISTRIBUTOR_SERIAL`, and a planted bug brings 0x7F7D back.

**Next.** The real attempt: `--trace --wonder-card "E - Item Old Sea Map (JPN).wc3"`, after a
fresh backup of the save with `maxduo_saves.py`.

Nothing was written to either cartridge. The game saves only after it receives a card.
