# Roadmap: the events tool (`duo_events.py`)

Each stage builds on the last. The cheapest and most useful steps come first. Every stage goes
through the simulator (with a planted bug for each new behaviour) before it touches real hardware.

## The goal (agreed 30 October 2026)

> Support every Gen 3 Project Pokémon file meant for a GBA Pokémon game, by the route it
> originally came by, wherever that route leaves the game to save the event itself. Where it
> doesn't, offer the nearest route that does, and say so.

The files are those in Project Pokémon's events gallery (`projectpokemon/EventsGallery`,
`Released/Gen 3` and `Unreleased/Gen 3`). They stay on the user's PC and never enter this
repository.

| Files | Authentic route | Where we are |
|---|---|---|
| `.wc3` Wonder Cards (68) | Mystery Gift, as the distributions sent them | Japanese Emerald and English LeafGreen on hardware; other games and languages next (Stage 1) |
| Wonder News | The same route | Simulator only; the gallery has no Gen 3 news files yet (Stage 2) |
| `.me3` Mystery Events (13, all the Eon Ticket) | e-Reader to Ruby/Sapphire | Working on hardware: English Ruby received the Eon Ticket (lab session 26) |
| `.ecb` e-Card berries (18) | e-Reader Mystery Event (`setenigmaberry`) | English: simulator done, hardware next (Stage 4); Japanese waits for the language masks |
| e-Card `.raw` scans (R/S trainers and promos; Emerald and FRLG trainers, Japanese) | Decode the dot codes and do what the e-Reader did | Not started. Ruby/Sapphire cards fit the Stage 3 route; Emerald's Trainer Hill uses its own protocol (`ereader_screen.c`); Japanese cards need the `checkcompat` language masks |
| `.pk3` Pokémon (about 1,000) | Varies by event; check each one | Not started |

**Out of scope:** files meant for other games or for the GameCube (the Channel, Colosseum and
Pinball e-Cards; Pokémon from GameCube bonus discs).

**Routes that write the save directly.** Some original routes (many Pokémon distributions, as
far as we know, and the Berry Glitch Fixing Program) sent a program over the cable that wrote
the save itself, without the game's menus. That conflicts with the rule that our code never
writes cartridge saves. **The user decides** how to handle these; until then, the nearest route
that leaves the game in charge (for Pokémon, a trade through the game's own Trade Centre) is the
one to plan for, labelled as such.

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

## Stage 3: Mystery Event for Ruby and Sapphire (the e-Reader route; done on hardware: the Eon Ticket, session 26)

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

On hardware (session 26): the wrapped Eon Ticket went across with 0 mismatches, Ruby saved it,
and Norman gave the ticket in Petalburg City Gym.

Still to come: the Record Mixing gift on hardware, Sapphire, and other languages.

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

## Stage 4: Enigma Berries (simulator done, 30 October 2026; hardware next)

- A `.ecb` is the saved `EnigmaBerry` record (1,328 bytes; docs/EREADER.md). The tool checks it
  (checksum with the pointers as 0, growth time, yield) and wraps it: `checkcompat`, `checksum`,
  `setenigmaberry`, `end`.
- The model of Ruby's engine (`tests/mevent_model.py`) now carries out `setenigmaberry` and the
  game's validity check. Two tests and three planted bugs.
- Checked locally with the six English e-Card berries: each stored exactly, in Ruby and Sapphire.
- **On hardware:** send one, then talk to Norman after his battle. He won't give one while the bag
  or PC holds an e-Reader berry already.

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
