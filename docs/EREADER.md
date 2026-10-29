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
- **Starting, when the game is a slave.** Pressing A does nothing. The master (the DUO) must send
  0x8FFF and `0x2222` itself.
- **Closing.** Each side sends `0x5FFF`. A console closes the link once it has seen `0x5FFF`
  from every player. So the DUO must answer the game's `0x5FFF` with its own.

## What the Mystery Event menu does (Ruby)

1. Sets the link type to 0x5501, opens the link, and shows "link standby".
2. Waits for the link to be up, with exactly two players.
3. When the player presses A, the master sends `0x2222`. Each side then sends its link-player
   block.
4. **Checks.**
   - Every link player must carry the same link type (0x5501), or the exchange counts as failed.
   - The two players' **languages must match**.
5. Waits for one more block from the other side: **the Mystery Event script itself**, received
   into the shared buffer.
6. Closes the link, runs the script, and saves if it succeeded.

## The Mystery Event script

- **What it is.** Bytecode for the game's Mystery Event script engine.
- **What it checks.** It carries its own checks: a compatibility check of language and version
  masks, and a checksum or CRC over its own data. A script that fails them says "This Mystery
  Event can't be used", and nothing is saved.
- **Our part.** The link only has to deliver the bytes intact.

## What the DUO will do

- **Role.** Be the second player. It can be either the master or a slave; we'll support both,
  following the cable.
- **Handshake.** Answer it.
- **On `0x2222`.** Send its link-player block, with link type 0x5501 and the game's language
  (read from the game's own block). Then send the event script, loaded from the PC, as one block.
- **Plain mode.** With no event loaded, send a link-player block only. The game then times out
  harmlessly: a safe dry run.

## Open questions, for the hardware session

- Which end of the AGB-005 the DUO should take. The plan is to support both.
- The file format of community Mystery Event dumps. We'll accept the raw script, and check its
  size and its own checksum command where we can.
