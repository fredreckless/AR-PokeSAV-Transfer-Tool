# Pretending to be a Wireless Adapter (work in progress)

**Goal:** deliver Generation 3 Mystery Gift Wonder Cards over a link cable. The first target is the Japanese
**Old Sea Map**, which takes Emerald players to Faraway Island and Mew. The DUO's GBA plays the part of the
Game Boy Advance Wireless Adapter *and* of the distributor on the other side of the "wireless" link. The
Pokémon game on a second GBA then receives and saves the gift itself, exactly as it did at the 2005
events.

**Status:** step 1 (detection) is built and passes in the simulator. It hasn't been tried on hardware yet.

## Why the adapter, and why it can be faked

- In Emerald, FireRed and LeafGreen, **Mystery Gift only works through the Wireless Adapter**.
  - The main menu offers it only when an adapter answers.
  - Japanese Emerald's e-Reader option accepts only Trainer Hill data.
  - Its Mystery Events (link cable) never received Wonder Cards. (Source: the Emerald decompilation,
    `main_menu.c`, `ereader_screen.c`.)
- The adapter is a small computer on the link port. It speaks a documented protocol: Normal-mode SPI with
  32-bit words, `0x9966…` command packets, and clock-direction swaps (see the
  [gba-link-connection notes](https://github.com/afska/gba-link-connection/blob/master/docs/wireless_adapter.md)).
  A Raspberry Pi Pico has already been used to emulate it
  ([GBA-Global-Link](https://github.com/zaksabeast/GBA-Global-Link)). Here, another GBA does the job.

## Step 1: detection (done in the simulator)

When the main menu opens, the game calls `IsWirelessAdapterConnected()`. That resets the adapter and runs
librfu's `Sio32ID` exchange:
- Normal 32-bit mode, with the **game as the clock master at 256 kHz**, roughly 2 ms between attempts.
- The words spell **NINTENDO** and end with the adapter's ID, **0x8001**. Nothing more is needed for the
  game to believe an adapter is present.

Nintendo's library implements *both* roles of this exchange. The DUO's program `adapter_detect`
(`firmware/programs.py`) is the adapter-side role, ported from it. In the simulator, against a model of
the game's side (`tests/sio32id_model.py`, from the same library), the words exchanged are exactly those
captured from a real adapter:

| Game sends | Adapter (DUO) answers |
|---|---|
| `FFFF494E` | `494EFFFF` |
| `B6B1494E` | `494EB6B1` |
| `B6B1544E` | `544EB6B1` |
| … | … |
| `B0BB8001` | `8001B0BB` |

```
python maxduo_saves.py --adapter-test        # needs firmware v4.2
```

The tool uploads the program, reports what the game is doing as it happens, and prints PASS when the game
has accepted the adapter ID. Nothing is written to either cartridge.

Notes for the real test:
- **Cable.** The adapter conversation uses the crossed data lines, SO→SI and SI→SO. **A GBA link cable
  (AGB-005) doesn't carry them both ways:** one plug has no SO, because GBA multiplayer uses SD. A Game Boy
  Color link cable (CGB-003) fits the GBA's port and is crossed.
- **Menu.** The game *shows* Mystery Gift only once it's switched on with the questionnaire phrase at a
  Poké Mart. The detection runs either way, and the tool sees the result from the DUO's side.
- **After detection,** the game sends one adapter command ("stop mode"), which this first program doesn't
  answer. The game ignores the result of that command, so the menu carries on normally.

## Next steps

1. **Real-hardware detection test**, with a GBC cable and the Japanese Emerald.
2. **The adapter command layer:**
   - the `0x9966` packets and the SO/SI ready handshake;
   - clock inversion after `0x25`/`0x27`;
   - Hello, Setup, SystemStatus, BroadcastRead…, Connect, SendData and ReceiveData.
3. **The distributor:** the host side of Mystery Gift, from the decompilation's `mystery_gift_server.c`.
   It sends the genuine Wonder Card and its script (`E - Item Old Sea Map (JPN).wc3` from Project
   Pokémon's EventsGallery, supplied by the user, never stored in this repository).
4. **Delivery:** back up the save before, receive, then back up after and compare.
