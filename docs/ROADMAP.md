# Roadmap: the events tool (`duo_wireless.py`)

Each stage builds on the last. The cheapest and most useful steps come first. Every stage goes
through the simulator (with a planted bug for each new behaviour) before it touches real hardware.

## Stage 0: Make room and tidy up

- **Free up memory.** Move the lab instrumentation (flick tables, link scope, command capture,
  timing calibration) into a separate lab build, so the everyday distributor has room to grow.
  Before this, 64 bytes were left below `0x0203AE00`.
- **Settings from the PC.** The serial number and the host's language come from the PC (or the
  game; see Stage 1) rather than being fixed in the code.
- **Check cards before sending them.** When loading a `.wc3`, check the card's and the script's
  CRC-16 (the game's own `CalcCRC16WithTable`), the script's magic and empty map fields, and the
  card size against the language. Warn about anything odd.
- **Small fixes.**
  - Ignore the harmless echoed acknowledgements ("unknown command 0xBD / 0x96").
  - Look into why the Mystery Gift menu sometimes needs a second visit.

## Stage 1: Wonder Cards for every Gen 3 game

- **Recognise the game.** The adapter reads the version and language from the game's own
  broadcast data (command 0x16), then picks the distributor serial and card size itself.
- **A table of serials**, with a `--serial` override:

  | Game | Serial | Status |
  |---|---|---|
  | Japanese Emerald | 0x7F7F | confirmed on hardware (lab session 19) |
  | Others | 0x7F7D | from the English decomps; confirm with `--sweep-serial` |

  Each confirmed result goes into the table.
- **FireRed and LeafGreen.** Their Mystery Gift is the same protocol (pret/pokefirered). Model
  them in the simulator, and test both card sizes (Japanese 0xA4, others 0x14C).
- **Hardware.** A short session per cartridge: English and European Emerald, and FireRed/LeafGreen
  in any language. **Testers welcome.**

## Stage 2: Wonder News

The same Mystery Gift steps, but the server tells the game to save news rather than a card.
After receiving news, the delivery man gives berries.

## Stage 3: Mystery Event for Ruby and Sapphire (the e-Reader route)

- **The DUO plays the e-Reader** on the standard link (two-player mode at 115 kbit/s), as a
  separate GBA program.
- **How Ruby receives an event** (pret/pokeruby):
  - the link-player exchange, where the languages must match;
  - one block of up to about 2 KB, holding a Mystery Event script;
  - the game runs the script, then saves.
- **The simulator** gains a model of two-player link mode and Ruby's Mystery Event menu.
- **Needs** a GBA link cable (AGB-005), because two-player mode uses the SD line, which Game Boy
  and Game Boy Color cables don't carry.
- **First goal:** the English Eon Ticket, from carefully checked data.

## Throughout

- The DUO never writes to cartridges. Back up before any gift.
- No Nintendo files (ROMs, `.wc3` files, distribution software) or personal data in the
  repository.
