# Roadmap: the events tool (`duo_events.py`)

Each stage builds on the last. The cheapest and most useful steps come first. Every stage goes
through the simulator (with a planted bug for each new behaviour) before it touches real hardware.

## Stage 0: Make room and tidy up (done, 29 October 2026)

- **Free up memory.** Move the lab instrumentation (flick tables, link scope, command capture,
  timing calibration) into a separate lab build, so the everyday distributor has room to grow.
  Before this, 64 bytes were left below `0x0203AE00`.
- **Settings from the PC.** The serial number and the host's language come from the PC (or the
  game; see Stage 1) rather than being fixed in the code.
- **Check cards before sending them.** When loading a `.wc3`, check the card's and the script's
  CRC-16 (the game's own `CalcCRC16WithTable`), the script's magic and empty map fields, and the
  card size against the language. Warn about anything odd.
- **Small fixes.**
  - Ignore the harmless echoed acknowledgements ("unknown command 0xBD / 0x96"). Done: a word is
    ignored only when it is exactly our own last acknowledgement, so a genuinely unknown command
    is still refused.
  - The "second visit" to the Mystery Gift menu: the game had been started before the adapter
    was ready. On opening ふしぎな おくりもの, it found no adapter and offered the e-Reader instead
    (corrected in lab session 22). The fix is the start-up order: see the hand-over's to-do list.

Results:
- The everyday adapter build is 10,792 bytes, with about 960 bytes spare.
- The lab build (`--lab`) is 11,660 bytes. It keeps the instruments but leaves out the serial
  sweep.

## Stage 1: Wonder Cards for every Gen 3 game (simulator done; Japanese Emerald confirmed on hardware)

- **Recognise the game.** The adapter reads the version and language from the game's own
  broadcast data (command 0x16), then picks the distributor serial and card size itself.
- **A table of serials**, with a `--serial` override:

  | Game | Serial | Status |
  |---|---|---|
  | Japanese Emerald | 0x7F7F | confirmed on hardware (lab session 19); chosen automatically (session 21) |
  | English LeafGreen | 0x7F7D | confirmed on hardware (lab session 23) |
  | Others | 0x7F7D | from the English decomps; confirm with `--sweep-serial` |

  Each confirmed result goes into the table.
- **FireRed and LeafGreen.** Their Mystery Gift is the same protocol (pret/pokefirered), and
  `Rfu_GetWonderDistributorPlayerData` is identical. In the simulator, an English FireRed now
  receives a full-size (0x14C) card from serial 0x7F7D with an English host.
- **The host's language follows the game,** both in its broadcast data and in its link-player
  block. The trace names the game, for example "broadcast data (0x16) … - Japanese Emerald".
- **Hardware.** Japanese Emerald: game recognised, serial chosen and two cards received back to
  back without `--serial` (lab session 21). English LeafGreen: the full-size Aurora Ticket card, first time, serial 0x7F7D (lab session 23).
  Still to do, a short session per cartridge: English and European Emerald, FireRed, and
  LeafGreen in other languages. **Testers welcome.**

## Stage 2: Wonder News (simulator done, 29 October 2026; hardware to come)

The same Mystery Gift steps, but the server tells the game to save news rather than a card.
After receiving news, the delivery man gives berries.

- `news --news FILE` (formerly `--wonder-news`): the distributor advertises news (hasNews, `ACTIVITY_WONDER_NEWS`).
  - It sends the news under `MG_LINKID_NEWS`, and reads the game's answer: FALSE if the game saved
    it, TRUE if it already had the same news.
  - It then ends with "news received" or "you already had it", as the decomp's server does.
- **Sizes.**
  - English news is 444 bytes (pokeemerald).
  - **Japanese news is taken to be 202 bytes.** That's unconfirmed: the Japanese Old Sea Map card
    showed that Japanese text fields are 18 bytes, against 40 in English. A real Japanese news
    item will settle it.
- **File checks.** Files can be the bare news, or the save's form with the CRC-16 in front. The
  CRC is checked, and news ID 0 is reported, since the game treats it as no news.
- **The lab build** leaves Wonder News out, to stay inside the memory limit.

**Needs:** a real Wonder News file for the game and language, and one hardware run.

## Stage 3: Mystery Event for Ruby and Sapphire (the e-Reader route; link and block transfer on hardware, sessions 24–25; the first real gift is next)

Done in the simulator:
- `firmware/programs/ereader.c` (4 KB) plays the e-Reader in either cable role.
- `tests/link_game_model.py` is a Ruby written from the behaviour in docs/EREADER.md.
- The simulator models the four-player link mode (`attach_mp_game`).
- Three tests (game as master, game as slave, dry run) and seven planted bugs.

On hardware (sessions 24–25):
- With the game as master, link players were exchanged with 0 mismatches, and a 1,012-byte
  block went across.
- Ruby's menu needs the game to be master; the tool now says to swap the ends otherwise.
- The `.me3` file is the game's *saved* form: sent raw, the game stores nothing. The tool now
  wraps it in a Mystery Event script that makes the game store exactly its contents, checked
  with a model of Ruby's engine (`tests/mevent_model.py`).

Still to come: send the wrapped Eon Ticket, and collect it from Norman in Petalburg City Gym.

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

## Tidying done on 30 October

- **One events tool**, `duo_events.py`, with `gift`, `news` and `event` commands.
  `duo_wireless.py` is a stub that translates the old options.
- **A guided start-up.** Both GBAs off; the DUO's on; progress bars; "Ready", then the game, with
  each game's menu and unlock phrase.
- **It finishes by itself** when the game has the gift.
- **It warns** when a card's file name names another game.
- **Missing Datel builds** skip tests rather than failing them.

## Throughout

- The DUO never writes to cartridges. Back up before any gift.
- No Nintendo files (ROMs, `.wc3` files, distribution software) or personal data in the
  repository.
