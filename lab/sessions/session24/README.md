# Session 24: the e-Reader dry run with Ruby (29 October 2026, about 21:55)

**Set-up.**
- `--ereader`: no event file, link players only.
- English Ruby, Nintendo GBA link cable (AGB-005), MYSTERY EVENT.

**What happened.**
- **The first attempt failed with a USB timeout** before the tool said anything ("The Action
  Replay stopped answering"). The second attempt connected normally.
- **The trace:**
  - "waiting for the game", then "handshaking" at 12 s;
  - "**link up (the DUO is player 0)**" at 28.4 s. That was the first time the e-Reader route's
    handshake had completed on hardware.
  - Nothing more: no link player from the game.
  - At Ctrl+C: 61,823 transfers, **3,112 checksum mismatches**. That's about 45% of the ~6,900
    command cycles.
- **The DUO was player 0, the master.** The plan was to start with Ruby as master. Which plug
  went where is still to be checked.

**Why: our master was paced too fast.** Compared with Ruby's own master (pokeruby `link.c`):

| | Ruby as master | The DUO as master (session 24) |
|---|---|---|
| Handshake transfers | one per frame (VBlank) | one every 12 scanlines |
| Command cycles (9 transfers) | one per frame | back to back |
| From a transfer's end to the next start | 0.75 ms (timer 3, started at the end of the serial interrupt) | ~0.44 ms (12 scanlines from the *start*) |

- **Too little time for Ruby's interrupt.** A slave game loads its next word in its serial
  interrupt. If the next transfer starts before that, Ruby misses a transfer, and from then on
  the two sides frame commands differently. Each sees the other's checksums as data, and the
  mismatches feed themselves. That fits 45% with mostly empty commands.
- **Too many commands for Ruby to take in.** Ruby also takes in only one received command per
  frame (room for 50). Even with the timing right, back-to-back cycles would overflow it during
  a long event.
- **Why the simulator missed it.** Its game answered instantly and took any number of commands.

**Changes made after this session.**
- **`ereader.c`, as master:**
  - one handshake transfer per frame;
  - two frames' pause at link up;
  - one command cycle per frame;
  - 13 scanlines (~0.83 ms) from each transfer's end to the next start.
- **The simulator is now strict.**
  - Changes: a transfer takes five scanlines; a slave game needs 0.6 ms after each transfer;
    a transfer started sooner carries its stale word and the game misses it.
  - Checks: the game takes one command per frame, with room for 50; the slave test asserts one
    cycle per frame and no missed transfers.
  - Planted bugs bring back session 24's gap, and back-to-back cycles. Both are caught.
- **The tool:** if the first hello or firmware check times out, it waits ~16 s and tries once
  more.

**Next.**
- Repeat the dry run. If the DUO is master again, it should now reach "got the game's link
  player".
- Check which plug each GBA is on. The notes say the purple plug is player 0.

The DUO wrote nothing to either cartridge.
