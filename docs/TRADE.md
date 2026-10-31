# The Cable Club trade, as the DUO plays it (Stage 9, step 3)

**Status (31 October 2026): working in the simulator, both cable roles; hardware next.**
`python duo_events.py trade --pk3 FILE` runs `firmware/programs/trade.c` against the game. The model
of the game's side is `tests/trade_game_model.py`; four tests and nine planted bugs cover it.

Decided on 31 October: every Gen 3 Pokémon goes by trade (docs/PK3_EVENTS.md). The DUO's GBA
plays the second player in a Pokémon Center's Cable Club **Trade Center**, over the GBA link
cable, and offers the event Pokémon; the game checks it, shows it, asks the player, trades and
**saves it itself**. Read from pokeruby (`cable_club.c`, `overworld.c`, `trade.c`, `link.c`,
`data/scripts/cable_club.inc`, the Trade Center's map and layout). Emerald and FireRed/LeafGreen
follow; their trade code is close but needs its own reading.

## The link underneath: what the e-Reader route already does

The Cable Club uses the same link layer as Mystery Event (docs/EREADER.md): four-player mode at
115,200 bit/s, the 0xB9A0/0x8FFF handshake, commands of 8 words plus a checksum slot, one command
cycle per frame, `0x2222` to ask for link players, `0xBBBB size id+128` then `0x8888` + 7 words a
command for a block, `0x5FFF` to close. `ereader.c` already does all of that, copies the game's
link type and language into its own link-player block, and follows the game closing and reopening
the link. **Unlike Mystery Event, a trade works with the DUO on either end of the cable.**

New commands the trade needs (link.c `ProcessRecvCmds`, `BuildSendCmd`):

| Command | Meaning | The DUO |
|---|---|---|
| `0xCAFE key` | one player's key for this frame, in the room (below) | sends one every frame |
| `0xCCCC n` | the master asks everyone for a block from their send buffer: 200, 200, 100, 220 or 40 bytes for n = 0-4 | answers with its own block of that size; as master, asks |
| `0x2FFE` | "ready": a barrier the games wait at, e.g. around saving | answers when the game says it |
| `0x5FFF` | close | as now |

(German Ruby/Sapphire answer `0xCCCC` a little differently, `deUnkValue2`; to check.)

## 1. The room: walking to the table

- The player talks to the Cable Club attendant and chooses TRADE CENTER: link type **0x1111**,
  link players exchanged, then both games warp into the Trade Center (`MAP_TRADE_CENTER`, warp 0).
- Each player appears on the door row: player *i* at (5 + *i*, 8). The room (layout
  `TradeCenter`, 12 x 9) is open floor from y = 3 to 7, with the table at (5,5)-(6,5). The seats
  are coordinate triggers beside it: **(4,5)** for player 0 and **(7,5)** for player 1.
- The room runs in lockstep: every frame each game sends `0xCAFE` with a key code and moves
  every player's character from everyone's keys (overworld.c): **17** nothing, **18** down,
  **19** up, **20** left, **21** right, **24** START, **25** A (26-29 are the games' own
  states).
- **The DUO walks to its seat** with a fixed list of keys: player 1 goes up, up, right, up, onto
  (7,5) (player 0: up, up, left, up). Stepping onto the seat runs the seat script (`special
  sub_8083B5C`), which waits for the other player; once both are seated, the games close this
  link and go to the trade screen. The exact timing of a link player's steps is to be confirmed
  in the model.

## 2. The trade screen: a fresh link

- The trade screen (`trade.c`, `sub_8047EC0`) opens a **new link, type 0x1122**: handshake, link
  players again (the master waits ~30 frames, then asks), then the parties.
- **The parties**, each a block the master asks for with `0xCCCC 1`: party slots 0-1 (200 bytes),
  2-3, 4-5; then the mail, `0xCCCC 3` (6 x 36 + 4 = 220 bytes); then the gift ribbons, `0xCCCC 4`
  (11 bytes used of 40). Each Pokémon is the game's 100-byte party form: encrypted, blocks
  shuffled, with the party stats after. `duo_events.pk3_game_form` already makes the first 80;
  the party's 20 (status, level, HP and stats) need working out from the species' base stats.
- **The DUO's party**: the event Pokémon, alone or with filler. What the game sends is its whole
  party; the DUO keeps it only to know what it receives.

## 3. The trade menu

20-byte messages (`Trade_SendData`: a block of `linkData[10]`), first word the code:

| Code | From | Meaning |
|---|---|---|
| `0xAABB, n` | the slave | "I've chosen my Pokémon n" |
| `0xEEAA` | the slave | "I've chosen CANCEL" |
| `0xDDDD, n` | the master | both have chosen: show "trade X for Y?" |
| `0xBBBB` / `0xBBCC` | the slave | yes / no to the trade |
| `0xCCDD` | the master | both said yes: trade |
| `0xDDEE` | the master | someone said no: back to choosing |
| `0xEEBB` / `0xEECC` | the master | both cancelled: leave / one cancelled |

The master decides; the slave proposes. As the slave (the cable as fitted now), the DUO sends
`0xAABB` with its event Pokémon's slot, then `0xBBBB` when asked; as master it will send the
decisions itself.

## 4. The trade and the save

The trade animation, then a save that both games make together, waiting for each other at
`0x2FFE` barriers. After it, the trade screen exchanges parties again (for another trade); the
player chooses CANCEL to leave, and the link closes.

**What the DUO receives** in return, the player's Pokémon, is kept by the tool as a `.pk3` on
the PC, so nothing is lost. The player is told up front that a trade is one for one, and to offer
a spare.

## The game's own rules (to model before building)

The game refuses some trades (`trade.c` checks after the parties arrive): eggs and species a game
can't receive yet (FireRed/LeafGreen need the National Dex for non-Kanto Pokémon), a Shedinja
nickname fix-up, and more. The tool should know them and say so before a trade, not leave it to
fail.

## What was built

- **`trade.c`** (7.4 KB with its buffers): ereader.c's link engine, then the room's walk and key
  22, block requests answered (and, as master, made 40 then 12 frames apart so the game has filled
  its buffer), the menu messages, 0xABCD/0xDCBA, barriers answered, the link closes. After the
  trade it swaps its own copy too, so the parties exchanged on the way out are right. It never
  sends lp_field_2 = 0x8000.
- **The tool:** checks the `.pk3`, builds the party form (game-encrypted box data plus the party
  fields; see below), names the partner after the Pokémon's own trainer, follows each stage, and
  keeps what the game gave as `received/<date> <species>.pk3` beside the event file.
- **Party fields.** Ruby keeps the level, HP and stats exactly as sent, so the tool works them out
  as `pokemon.c` does (base stats and growth rate from pokeemerald's `species_info.h`, the
  experience tables, IVs, EVs, nature). Against the gallery's 48 released party files that carry
  stats, all match; 5 unreleased debug ones differ by a point or two, as the games only refresh
  stats at a level-up. A file that carries its own stats keeps them.
- **The model** plays Ruby through all four links, in either role, and refuses what Ruby would:
  wrong link types, a party with gaps, a Pokémon failing its checksum or with no level or HP, a
  partner that never sits down or never answers a barrier.

## Still to check on hardware

- The walk's timing (40 frames after the room's first key, then a step every 20 frames).
- That nothing else happens between the attendant and the room that needs an answer.
- Emerald and FireRed/LeafGreen: their own trade code (not yet read).
