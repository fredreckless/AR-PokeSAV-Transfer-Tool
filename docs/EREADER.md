# Mystery Event over the link cable (the e-Reader route)

Roadmap Stage 3. This describes, in our own words, how Ruby and Sapphire (and Japanese Emerald)
receive a Mystery Event, as read from pret/pokeruby (`src/link.c`, `src/mystery_event_menu.c`,
`src/mystery_event_script.c`). The DUO's GBA will stand in for the Card e-Reader+ on that link.

## The link: the GBA's four-player mode

- **Mode.** SIOCNT in multi-player mode at 115,200 bit/s. Each transfer moves one 16-bit word
  from every console at once: each loads `SIOMLT_SEND`, the master starts the transfer, and
  afterwards every console sees all the words in `SIOMULTI0..3` (player 0 = the master).
- **Who is master.** That's decided by the cable. The console on the end whose SI is grounded is
  player 0. On a Nintendo AGB-005 cable, that's the purple plug. Game Boy and Game Boy Color
  cables won't do, because multi-player mode needs the SD line.
- **Pacing** (corrected after lab session 24). A slave only reacts, but it needs the master to
  pace exactly like this:
  - **One command a frame.** During the handshake, the master starts one transfer per frame
    (VBlank). Once connected, it runs one cycle of nine transfers per frame: a checksum slot,
    then eight words.
    - The game sends at most one command per frame, and takes in one received command per frame
      (`LinkMain1` → `DequeueRecvCmds`), with room for 50 waiting.
  - **Within a cycle, 0.75 ms between transfers.** Each next transfer starts 0.75 ms after the
    previous one **finished**: timer 3, 197 ticks of 64 cycles, started at the end of the
    master's serial interrupt. That gives the slave's interrupt time to load its next word.
  - **What went wrong in session 24.** The DUO was master, starting transfers 12 scanlines after
    the previous *start*: only ~0.44 ms after the end, with cycles back to back. 45% of commands
    had checksum mismatches.
  - **The fix.** `ereader.c` now waits 13 scanlines (~0.83 ms) after each transfer ends, and
    starts one cycle per frame.
  - **The simulator** now gives the slave game a 0.6 ms reaction time and a five-scanline
    transfer, and checks the one-cycle-per-frame rule.

## The game's link layer (link.c)

- **The handshake.**
  - Everyone sends a handshake word: the master 0x8FFF, slaves 0xB9A0 (plus a small id).
  - The link counts as up once player 0 says 0x8FFF and the number of players agrees twice in a
    row.
- **Commands.**
  - After the handshake, data moves in commands of 8 words (`CMD_LENGTH`), one word per
    transfer, then a ninth transfer carrying a checksum: the sum of the words received.
  - A command whose words are all zero means "nothing".
- **The commands that matter here:**
  - `0x2222, link type`: "send me your link player". Each side answers by sending its link-player
    block.
  - `0xBBBB, size, sender + 128`: a block is about to follow.
  - `0x8888, 7 words`: 14 bytes of the current block. Repeated until `size` bytes have arrived.
    Blocks over 256 bytes go into a larger shared buffer.
- **The link-player block** is the same 60-byte form we already send over the wireless link:
  - "GameFreak inc." (14 bytes), then 2 bytes of padding;
  - a 28-byte `struct LinkPlayer`, laid out in Ruby as:

    | Offset | Field |
    |---|---|
    | 0x00 | version + 0x4000 (u16) |
    | 0x02 | (u16) |
    | 0x04 | trainer ID (u32) |
    | 0x08 | name (11 bytes) |
    | 0x13 | gender |
    | 0x14 | link type (u32) |
    | 0x18 | id (u16) |
    | 0x1A | language (u16) |

  - "GameFreak inc." again.

### Framing, exactly

- **The cycle.** Every console keeps a send index and a receive index, both 0 when the link comes
  up. A command takes nine transfers, in a repeating cycle:
  1. **The checksum slot.** What arrives is everyone's checksum for the previous command; each
     console compares it with its own.
  2. **Then words 0 to 7.** Each received word is stored, and added to a running checksum: the sum
     of every player's words in that command, modulo 0x10000.
- **What gets loaded when.** After word 7 arrives, the console loads its checksum for the next
  transfer, and both indexes return to 0.
- **The first command.** The first transfer after a successful handshake is a checksum slot, and
  isn't checked.
- **Empty commands.** A command whose words are all zero is "nothing" and isn't queued.

### Starting and closing

- **Starting, when the game is master.**
  - Until the player presses A, the master sends the *slave* handshake word too. Its player count
    still works, so the menu can say whether two players are connected.
  - Pressing A makes it send 0x8FFF once, which brings the link up.
  - Then it sends `0x2222` to start the link-player exchange.
- **Starting, when the game is a slave.** Pressing A does nothing. The master (the DUO) would
  have to send 0x8FFF and `0x2222` itself. **But Ruby's Mystery Event menu never gets that far
  as a slave:** see below.
- **Closing.** Each side sends `0x5FFF`. A console closes the link once it has seen `0x5FFF`
  from every player. So the DUO must answer the game's `0x5FFF` with its own.

## What the Mystery Event menu does (Ruby)

1. Sets the link type to 0x5501, opens the link, and shows "link standby".
2. Waits until it is **the link's master** with at least two players: `gLinkStatus & 0x20`
   (`LINK_STAT_MASTER`) and a player count of two or more. Only then does it say "press A".
   - And every frame: once connected (`gLinkStatus & 0x40`), **if it isn't master, it closes the
     link and shows an error.**
   - So **the game must be on the cable's master end**, and the e-Reader is the slave. That is
     why session 24's runs with the DUO as master never got further. If the DUO comes up as
     player 0, the tool now says to swap the cable's ends.
3. When the player presses A, the master sends `0x2222`. Each side then sends its link-player
   block.
4. **Checks.**
   - Every link player must carry the same link type (0x5501), or the exchange counts as failed.
   - The two players' **languages must match**.
5. Waits for one more block from the other side: **the Mystery Event script itself**, received
   into the shared buffer (`gSharedMem`, 0x7D4 bytes).
6. Closes the link and runs the script from the start of that buffer
   (`RunMysteryEventScript(gSharedMem)`). Then it clears the buffer.
7. **Saves unless the status says otherwise:**

   | Status | Message | Save |
   |---|---|---|
   | 0, "loaded OK" (the starting status) | the game's own success message | yes |
   | 1, load error (a failed checksum or CRC command) | "loading error" | no |
   | 2, success (set by gift commands, or `setstatus`) | the script's own message | yes |
   | 3, failure (`checkcompat` failed, a full party) | "can't be used" | no |

8. Shows the message, waits for A, and resets the game.

## The Mystery Event script

From pokeruby's `mystery_event_script.c` and `script.c`.

- **The engine.** It runs commands until one *yields*, then carries on only while the script is
  marked valid. A byte that isn't a command (17 of them, 0–16) stops it at once.
- **The commands used here.** Arguments are little-endian and read byte by byte, so no alignment
  is needed.

  | No. | Command | Arguments | Notes |
  |---|---|---|---|
  | 1 | `checkcompat` | base (u32), languages (u16), languages (u32), flags (u16), versions (u32) | Sets the script's base address for pointers. Valid only if both language fields have the game's bit (English 1<<1, German 1<<2), flags has 0x4, and versions has Ruby 1<<7 or Sapphire 1<<8. Otherwise status 3. Yields |
  | 2 | `end` | | |
  | 3 | `setmsg` | status (u8), text (pointer) | Shows the text if the status matches (0xFF: any) |
  | 4 | `setstatus` | status (u8) | |
  | 6 | `initramscript` | map group, map number, person (u8 each), start, end (pointers) | `InitRamScript`: stores the script for that person, with magic 0x33 and a byte-sum checksum |
  | 11 | `setrecordmixinggift` | count, chance (u8 each), item (u16) | The item spreads by Record Mixing; zero fields clear it |
  | 15 | `checksum` | sum (u32), start, end | Byte sum. On mismatch: invalid, status 1. Yields |
  | 16 | `crc` | CRC-16 (u32), start, end | The game's CRC-16. On mismatch: invalid, status 1. Yields |

  - **Pointers** are `word - base + (where the buffer is)`, with base from `checkcompat`.

## Community files: `.me3` is the *saved* form (lab session 25)

- **What a `.me3` holds** (1,012 bytes; checked against the Eon Ticket file and Ruby's code):

  | Offset | Size | What |
  |---|---|---|
  | 0 | 4 | the RAM script's checksum: the byte sum of the next 1,000 bytes |
  | 4 | 1 | magic 0x33 |
  | 5 | 3 | map group, map number, person (the Eon Ticket: 8, 1, 1 = Norman in Petalburg City Gym) |
  | 8 | 996 | the RAM script (a field script), zero-padded |
  | 1004 | 4 | the Record Mixing gift's checksum: the byte sum of its data |
  | 1008 | 4 | count, chance, item (u16) (the Eon Ticket: 1, 151, 275 = `ITEM_EON_TICKET`) |

  That's the game's `RamScript` and the start of its `RecordMixingGift`, exactly as saved.
- **Sent as it is**, the game reads 0xF0 as its first command, which isn't one. It stops at
  once with status 0, shows its success message and **saves, having stored nothing.** That's
  what sessions 24 and 25 did: the link worked, but the game can't have received the ticket.
- **So the tool wraps a `.me3`** in the script that stores exactly its contents:

  ```
  checkcompat  base 0x02000000, English (or German) in both fields, flags 0x4, Ruby | Sapphire
  checksum     byte sum of the RAM script, over the RAM script     (damage -> "loading error", no save)
  initramscript  map group, map number, person, the RAM script
  setrecordmixinggift  count, chance, item                        (if the file has a gift)
  end
  <the RAM script>
  ```

  - The status stays 0, so the game shows its own success message and saves.
  - The language comes from the file's name ("(ENG)", "(GER)"). Japanese files are refused: the
    Japanese masks aren't known.
  - A model of Ruby's engine (`tests/mevent_model.py`, written from the decomp) checks that the
    game would then store the file's own bytes, RAM script and gift alike. Locally, it does so
    for the real Eon Ticket file too, which isn't in the repository.
- **A file that already starts with `checkcompat`** is sent as it is, after reading its
  compatibility fields.

## What the DUO does

- **Role.** The second player. It follows the cable, master or slave, but only the slave role
  can work with Ruby's menu (above). As master, it paces exactly like Ruby's own master, which
  is kept for other uses and tests.
- **Handshake.** Answer it.
- **On `0x2222`.** Send its link-player block, with link type 0x5501 and the game's language
  (read from the game's own block). Then send the event script, loaded from the PC, as one block.
- **Plain mode.** With no event loaded, send a link-player block only. The game then times out
  harmlessly: a safe dry run.

## On hardware so far

- **Session 24.** With the DUO as master, the link came up but nothing more happened. That's
  explained by the menu's master rule. There were also checksum mismatches, partly from our
  pacing, which was too fast and is now fixed, and possibly from a start race, now fixed with an
  in-flight flag.
- **Session 25.** With the ends swapped, the game was master.
  - The link players were exchanged with 0 mismatches.
  - The event block went across, twice, and the game closed the link. But the block was the raw
    `.me3`, so the game stored nothing (above).
  - A few mismatches were counted after the close. They were very likely the game opening the
    link again, whose handshake words were counted as data. The program now follows the game
    starting over, and doesn't count those.
- **Diagnostics now reported at the end of a run:**
  - where any mismatches happened, by stage;
  - the first four mismatches, with their words;
  - the longest time the DUO looked away from the link.

## Open questions

- Ruby's success message and the Eon Ticket itself. Next session: send the wrapped `.me3`, then
  talk to Norman.
- Japanese Mystery Event scripts (Japanese Emerald's ふしぎな できごと): the Japanese language
  masks and the file formats.
