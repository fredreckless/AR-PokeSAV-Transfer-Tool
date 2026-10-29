# Session 25: the e-Reader route on hardware - link players and an event block (29 October 2026, about 23:00)

**Set-up.**
- Session 24's tool: master pacing like Ruby's.
- English Ruby; an unofficial GBA link cable with two identical black ends; MYSTERY EVENT.
- Four runs:
  1. `--ereader` (dry run), the cable as in session 24.
  2. `--ereader` again, with the ends swapped.
  3. `--mystery-event "RS - Item Eon Ticket (ENG) (UK).me3"`, ends still swapped.
  4. The same again.

**What the traces showed.**
- **Run 1 (the DUO as player 0, the master).**
  - The link came up at 52.8 s, but no link player came from the game.
  - 4,543 transfers, 44 checksum mismatches: far fewer than session 24's 3,112, after the pacing
    fix.
- **Run 2 (the DUO as player 1; Ruby was master).**
  - Link up at 23.2 s.
  - "**got the game's link player: link type 0x5501, English**", then "**sent our link
    player**".
  - 47,189 transfers, **0 mismatches**. That's the first complete link-player exchange on
    hardware.
- **Runs 3 and 4.**
  - "Mystery Event loaded: … (1012 bytes)", link up, link players, then "**sent the Mystery
    Event**" and "**the game closed the link**", all within about 1.5 s.
  - Mismatches: 36 (run 3, which kept going for a long time afterwards) and 1 (run 4).

**What it means.** Two findings came from reading pokeruby against these runs.

1. **Ruby's MYSTERY EVENT menu only carries on as the link master**
   (`mystery_event_menu.c`):
   - it waits for `gLinkStatus & LINK_STAT_MASTER` before "press A";
   - once connected, it closes the link with an error if it isn't master.

   That explains runs 1 (and session 24) versus run 2: **the game must be on the cable's master
   end.** On this cable that's the end the DUO had in sessions 24 and 25 run 1.
2. **The `.me3` file isn't what the e-Reader sent.** It's the game's saved form: the RAM script
   block and the Record Mixing gift (see docs/EREADER.md).
   - Ruby runs the received buffer as a Mystery Event script. 0xF0 isn't a command, so the engine
     stops at once with status 0.
   - The menu then shows its success message and saves, **having stored nothing**.
   - So runs 3 and 4 proved the link carries a 1,012-byte block. But the game can't have the
     Eon Ticket from them. (The user still has to report what the game showed; a success message
     is expected.)

- **The mismatches after the close.** They were very likely the game opening the link again,
  whose handshake words were counted as data. Run 3 kept going long afterwards.

**Changes made after this session** (30 October, overnight).
- **`.me3` files are wrapped.** The tool now sends a script that makes the game store exactly
  the file's contents: `checkcompat`, `checksum`, `initramscript`, `setrecordmixinggift`, `end`.
  - A model of Ruby's script engine (`tests/mevent_model.py`) confirms that.
  - Locally it does for the real Eon Ticket file too: it reproduces all 1,012 bytes.
- **The tool says to swap the cable's ends** if the DUO comes up as master.
- **`ereader.c`:**
  - an in-flight flag fixes a start race that, as master, could start a transfer before the last
    one was handled;
  - it follows the game starting over after a close, so two events can go in one session;
  - it records mismatches (stage, words) and its own longest look away from the link.

**Next.** Send the wrapped Eon Ticket, the game on the master end:
`python duo_events.py event --event "RS - Item Eon Ticket (ENG) (UK).me3"`. Expect the game's
success message. Then go to Petalburg City Gym and talk to Norman.

The DUO wrote nothing to either cartridge. The game saved after runs 3 and 4 (with nothing new
stored).
