# Plan: a Mystery Gift distributor on the DUO

This is a working plan, written from the Emerald decompilation (pret/pokeemerald) and the notes of
others who have taken GBA wireless apart. It's for the step after the adapter's command layer
works on real hardware.

## What others have done (research, 27–28 October)

| Project | What it does | What we take from it |
|---|---|---|
| suloku's *GBA Link Cable Mystery Event Distributor* (a fork of FIX94's gba-link-cable-dumper, GameCube/Wii homebrew) | Uploads a small program to a GBA over the GameCube cable. The program **writes the event straight into the save**: Wonder Card, its checksum, and the RAM script, then fixes the section checksum. No game code runs. | Where Emerald keeps the card and script. Japanese Emerald stores a **0xA4-byte** Wonder Card (other languages 0x14C) plus a 1004-byte script block. A useful cross-check after a real delivery. It's not a distribution, though: it edits the save. |
| zaksabeast's *GBA-Global-Link* (Raspberry Pi Pico, Rust) | A Wireless Adapter emulator. It bit-bangs the link port, setting each bit as the clock falls and reading on the rise, which is fast enough at 2 MHz. Command handling runs in a web app. | Its ready handshake: low before each word; afterwards low, about 0.8 µs, high, then wait for the GBA to go high. A Pico is fast enough to set the first bit on the clock edge. A GBA clock slave isn't, which is why the DUO has to resort to the flick. |
| afska's gba-link-connection docs and LinkWireless | The fullest public description of the adapter protocol (commands, ready handshake, clock swap, data headers). Warns that GBA-to-GBA at 2 Mbps needs very short wires. | The command table and the data headers. |
| VBA-M (gbaLink.cpp) | Emulates the adapter between two emulators, including broadcast, connect and data. | A second opinion on response formats (0x1D/0x1E, 0x20/0x21, 0x26). |
| pret/pokeemerald | The game's own code: librfu, the link manager, link_rfu_2, link.c, and the Mystery Gift client and **server** (Emerald can share cards with a friend, so the host side is in there too). | Everything below. Following Emerald's own host code means a game that accepts a friend's card will accept ours. |

## What Emerald looks for

`Task_CardOrNewsOverWireless` (union_room.c) runs `InitializeRfuLinkManager_JoinGroup`, then:

1. **Search.** It sends `0x1C`, then `0x1D` once a frame. Each host in range comes back as 7 words:
   - `id | slot<<16`;
   - the host's game config: serial number (u16), *gname* (13 bytes), one checksum byte, and
     *uname* (8 bytes).

   librfu drops entries whose checksum doesn't match. The checksum is the complement of the sum of
   the first 8 gname bytes plus the 8 uname bytes.
2. **Filter.** The link manager keeps hosts whose serial number is accepted. Mystery Gift then
   insists on **serial 0x7F7D (`RFU_SERIAL_WONDER_DISTRIBUTOR`)**, and reads the gname as
   `struct RfuGameData`. For Wonder Cards, `compatibility.hasCard` must be set. After about 2
   seconds (120 frames) with the host listed, the game connects.
3. **Connect.** It sends `0x1F <host id>`, polls with `0x20`, then `0x21`. Each answer is
   `child id | slot<<16 | status<<24`, where status 0 means connected. Then comes `0x27`, a clock
   swap.
4. **Child name.** The child sends its game name to the host over librfu's NI (reliable) channel.
   The host must accept it within the name-accept period.
5. **link_rfu_2 traffic.** The host and child exchange small packets every frame on librfu's
   UNI/NI slots: the link player exchange, then block transfers.
6. **Mystery Gift.** `mystery_gift_link.c` moves the server's script, the card and the RAM script
   in blocks. The client (`mystery_gift_client.c`) checks and saves them. The Old Sea Map card
   carries a script that sets `FLAG_ENABLE_SHIP_FARAWAY_ISLAND` and `FLAG_RECEIVED_OLD_SEA_MAP`.

## How the work splits

- **The DUO's GBA (program in RAM)** answers the adapter commands in real time, and plays the host
  for steps 1 to 6. Steps 5 and 6 need a frame-by-frame host, which USB is far too slow to run from
  the PC (one packet takes about 18 ms).
- **The PC** reads the user's `.wc3`. It checks the size and layout, and that the card is
  Japanese for a Japanese game. It uploads the card, then follows progress through the trace.
- **Memory.** The program area is 12 KB. The card and script are about 1.3 KB. If the host code
  outgrows the area, the card goes in a second upload area found the same way as the first (lab
  session 2 mapped which memory Datel's firmware leaves alone).

## Stages, each testable on hardware

| Stage | The trace should show | Emerald should show |
|---|---|---|
| A. Command layer (now) | Hello, setup, broadcast, search polls | "Searching…", then a clean cancel |
| B. **A distributor in range** *(simulator: done)* | Search results with one host; after about 2 s, `connect (0x1F)` and polls | The game finds a distributor and tries to connect |
| C. Connected *(simulator: done)* | `0x21` success, clock swap, child name received | "Awaiting response…" |
| D1. Join accepted *(simulator: done)* | "told the game it may join" | "Wireless link established" |
| D2. Link players exchanged | link_rfu_2 per-frame packets both ways | (behind the scenes) |
| E. Card delivered | Server script runs to the end | The Old Sea Map Wonder Card is received |

Each stage gets a game-side model in the simulator, ported from the decomp, before it runs on
hardware. That's how the command layer was built.

## Notes for stage C: what goes over the air after connecting

From librfu_rfu.c, so stage C can be built against it:

- **Data commands.** The child sends with `0x24` or `0x25` ("send, then wait"). Its first data
  word is a header: `bytes << (8 + 5 × slot)`. Hosts use plain `bytes`. After `0x25`/`0x27`, the
  adapter has the clock. When the host has sent something, it tells the child with `0x99660028`
  (data ready). The child then reads it with `0x26`. The answer is a header word, whose bits 0–6
  are the bytes from the host, then the data itself.
- **LL frames.** The data is a run of *LL sub-frames*. Each starts with a little-endian header:
  - child frames have 2 header bytes: size (5 bits), phase, n, ack, slot state, "recv first";
  - host frames have 3: size (7 bits), the same fields wider, and the slot flags of the children
    it addresses.

  Slot state `LCOM_UNI` carries unreliable per-frame data. The others are the **NI** (reliable)
  window: four phases, each numbered and acknowledged, with a start frame that says how many
  bytes follow.
- **The child's name.** Straight after connecting, the child sends 26 bytes over NI, starting at
  its own `serialNo`: serial (2), gname (15 with padding), uname (9). That's
  `rfu_NI_CHILD_setSendGameName(slot, 0x0E)`, so 14-byte sub-frames. The host's link manager
  must receive it and acknowledge every phase in its own frames, within the name-accept period.
- **Ground truth.** The adapter program now records the first eight data commands the game
  sends, word for word, and the PC prints them at the end of a trace. The first real connection
  will show Emerald's own NI frames for its name. The host's NI receiver can then be checked
  against real bytes, not just against the model.

## Progress in the simulator (night of 27–28 October)

- **Stage B.** One host in range: serial 0x7F7D, RfuGameData (Japanese, Emerald, hasCard), user
  name "DUO", librfu's checksum. Connecting works.
- **Stage C.** The adapter speaks for the host once a frame (`0x28`) and serves the host's frames
  on `0x26`. The host receives the game's 26-byte name over NI and acknowledges every chunk.
- **Stage D1.** Once it has the name, the host sends link_rfu_2's status byte
  `RFU_STATUS_JOIN_GROUP_OK` (5) over NI, with sub-frame size 8 as in `link_rfu_2.c`. The game
  receives it.

The game's side of each stage is a port of librfu's own NI sender and receiver, in
`tests/rfu_game_model.py`. Both sides are written from the same reading of the decomp, so the real
check is hardware. The first connection will print the game's actual frames, word for word.

## Stage D2 onwards (next)

After joining, link_rfu_2 switches both sides to **UNI** (unreliable, every frame) packets of
`COMM_SLOT_LENGTH` bytes:
- The child sends `gSendCmd`: 8 u16s, the first carrying the command and a 3-bit sequence
  number in its top bits.
- The host sends `gRfu.recvCmds`: every player's commands.

The commands that matter for Mystery Gift:
- `RFUCMD_SEND_PLAYER_IDS(_NEW)` (0x7700/0x7800): who is who.
- `RFUCMD_SEND_BLOCK_INIT/BLOCK/BLOCK_REQ` (0x8800/0x8900/0xA100): link.c's block transfers. The
  link player records go this way, then everything `mystery_gift_link.c` sends.
- `RFUCMD_READY_CLOSE_LINK` (0x5F00) at the end.

Then `mystery_gift_server.c`'s script for a Wonder Card sends the card, its script and the RAM
script, and waits for the client's replies. The host side needs:
1. a UNI sender and receiver for slot 0, much simpler than NI;
2. a small link_rfu_2 parent: player ids, block send and receive, sequence numbers;
3. the server script with the user's `.wc3` (uploaded by the PC into a second memory area if the
   12 KB program area is too small).
