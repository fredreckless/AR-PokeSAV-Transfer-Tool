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
| B. **A distributor in range** | Search results with one host; after about 2 s, `connect (0x1F)` and polls | The game finds a distributor and tries to connect |
| C. Connected | `0x21` success, clock swap, child name accepted | "Awaiting response…" |
| D. Link players exchanged | Data commands both ways | "Wireless link established" |
| E. Card delivered | Server script runs to the end | The Old Sea Map Wonder Card is received |

Each stage gets a game-side model in the simulator, ported from the decomp, before it runs on
hardware. That's how the command layer was built.
