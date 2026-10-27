# Pretending to be a Wireless Adapter (work in progress)

**Goal:** deliver Generation 3 Mystery Gift Wonder Cards over a link cable. The first target is the Japanese
**Old Sea Map**, which takes Emerald players to Faraway Island and Mew. The DUO's GBA plays the part of the
Game Boy Advance Wireless Adapter *and* of the distributor on the other side of the "wireless" link. The
Pokémon game on a second GBA then receives and saves the gift itself, exactly as it did at the 2005
events.

**Status:**
- Step 1 (detection) passes on real hardware: a Japanese Emerald accepted the DUO as a Wireless Adapter (lab session 10).
- Step 2 (the command layer) passes in the simulator. On real hardware, commands stall at 2 MHz (lab session 11); a probe is ready to find out why.

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

## Step 2: the command layer (done in the simulator)

The second program, `adapter` (`firmware/programs/adapter.c`), plays a whole Wireless Adapter with
nobody else in range. It is written in C and built with clang for the GBA's ARM7TDMI. It does four
things:

- **The ID exchange**, as in step 1. It repeats the exchange whenever the game resets the adapter,
  which librfu does before every session and before putting the adapter to sleep.
- **Commands.** The game sends `0x9966LLCC` plus `LL` data words, and the adapter answers each with
  `0x80000000`. Then it acknowledges with `0x9966LL(CC+0x80)` plus its response. The game clocks every
  word at 2 MHz. Between words, both sides signal "ready" on their SO lines. On the DUO that's SIOCNT
  bit 3 out and bit 2 in, ported from `librfu_intr.c`.
- **The clock swap.** After `0x25`, `0x27`, `0x35` or `0x37`, the game becomes the clock slave, and the
  adapter takes its turn. With no-one in range, there's only ever one thing to say: `0x99660027`,
  "nothing happened". It's sent after the number of frames the game chose in its Setup command
  (32 for Pokémon), with the inverted handshake.
- **The answers.**
  - Searches find nobody.
  - Status reports "idle".
  - Unknown commands are refused politely with `0x996601EE`, as the real adapter does.

Every command the game sends is kept in a trace. The PC reads it and prints it as it happens:

```
python maxduo_saves.py --adapter-trace        # needs firmware v4.2; Ctrl+C or 10 minutes to stop
```

**Testing in the simulator.** `tests/rfu_game_model.py` is the game's side of the link, ported from
librfu:
- the master and slave word handlers;
- the timeouts and the two retries;
- the ID exchange.

The simulator now models both SO lines, either GBA being clock master, and a word being lost when
the receiving side wasn't ready, as on hardware. The tests play:

- the whole Mystery Gift search, as Emerald drives it with nobody in range;
- a command abandoned half-way and started again;
- a silent cable.

All three pass with no retries. Four planted bugs are each caught:
- skipping the handshake;
- a wrong acknowledgement;
- never taking the adapter's turn;
- no recovery from an abandoned command.

### What to look for on real hardware

1. Enable Mystery Gift in the game first. Answer the Poké Mart questionnaire with the phrase. One
   source (WikiDex) gives the Japanese phrase as **ともだち いっぱい ふしぎ つうしん**; English games use
   LINK TOGETHER WITH ALL. Then save the game. That's the game's own save, done by the player; the
   tool never writes saves.
2. Run `--adapter-trace`, then choose **Mystery Gift → Wonder Cards → Wireless Communication**.
3. A healthy trace is:
   1. "ID exchange completed";
   2. Hello, setup and broadcast data;
   3. "start searching";
   4. "search results?" repeated about once a frame.

   The game should keep showing its searching message. Press B to cancel: the trace should end with
   another ID exchange and "sleep (0x3D)". The game says the search was cancelled and returns to its
   menu, with no link error.

### What real hardware showed (lab session 11)

The ID exchange works, but the first command never completes. The game clocks commands at
**2 MHz**, eight times faster than the ID exchange. Its words reach the DUO intact, but it rejects the
DUO's answer three times and gives up.

A GBA clock slave answering over a link cable seems too slow at 2 MHz: gba-link-connection warns
against GBA-to-GBA 2 Mbps except with very short wires. When the simulator adds a one-bit lag to the
DUO's 2 MHz answers, it reproduces the session exactly.

### The 2 MHz probe

```
python maxduo_saves.py --adapter-probe
```

This works like `--adapter-trace`, but it answers each attempt of a command a different way:
- **a one-bit lag:** load `W << 1`, with SO at W's top bit;
- **only the first bit late:** load `W`, with SO at its top bit;
- **as designed.**

The game only moves on to the ready handshake when it got exactly `0x80000000`, so the table at
the end shows which way works. For words after a handshake, which must be loaded "ready" (SO low),
the probe briefly flicks SO low and back high before the game's first clock edge. Whether that
gets the acknowledgement through is counted too. The simulator can't judge this part, because it
only sees the lines between instruction blocks.

## Next steps

1. **Run the 2 MHz probe on real hardware.** Then, depending on the result, try a fix in the
   program, a very short hand-made cable, or a Raspberry Pi Pico as the adapter (as GBA-Global-Link
   does).
2. **A distributor in range.** Answer "search results?" with one host whose broadcast data matches
   what Emerald's Mystery Gift looks for. Then handle Connect, and the data commands with real clock
   swaps ("data ready"), carrying librfu's own packet framing.
3. **The distributor:** the host side of Mystery Gift, from the decompilation's `mystery_gift_server.c`.
   It sends the genuine Wonder Card and its script (`E - Item Old Sea Map (JPN).wc3` from Project
   Pokémon's EventsGallery, supplied by the user, never stored in this repository).
4. **Delivery:** back up the save before, receive, then back up after and compare.
