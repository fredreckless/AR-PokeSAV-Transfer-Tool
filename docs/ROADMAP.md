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
| `.ecb` e-Card berries (18) | e-Reader Mystery Event (`setenigmaberry`) | English: working on hardware, the PUMKIN BERRY (lab session 27); Japanese: simulator done (Stage 7) |
| e-Card `.raw` scans (R/S trainers and promos; Emerald and FRLG trainers, Japanese) | Decode the dot codes and do what the e-Reader did | Ruby/Sapphire: all 186 cards decode and pass the model, English and Japanese (Stages 6–7); hardware next. Emerald's Trainer Hill and the FireRed/LeafGreen cards use another protocol (Stage 8). The Berry Glitch Fixing Program is refused (it writes the save) |
| `.pk3` Pokémon (3,344) | Varies by event: see docs/PK3_EVENTS.md | Catalogued (Stage 9, step 1): only the Mystery Mew was a trade; most others wrote the save directly. **The user's decision** on those comes next |
| `.ect` e-Reader trainers (166) | e-Reader Mystery Event (`addtrainer`) | Simulator done: each matches its card's trainer byte for byte |

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

## Stage 4: Enigma Berries (done on hardware: the PUMKIN BERRY, session 27)

- A `.ecb` is the saved `EnigmaBerry` record (1,328 bytes; docs/EREADER.md). The tool checks it
  (checksum with the pointers as 0, growth time, yield) and wraps it: `checkcompat`, `checksum`,
  `setenigmaberry`, `end`.
- The model of Ruby's engine (`tests/mevent_model.py`) now carries out `setenigmaberry` and the
  game's validity check. Two tests and three planted bugs.
- Checked locally with the six English e-Card berries: each stored exactly, in Ruby and Sapphire.
- **On hardware (session 27):** the PUMKIN BERRY went across with 0 mismatches, and Norman gave
  it. He won't give one while the bag or PC holds an e-Reader berry already.
- **Found on the way:** the "player 0" report can be wrong (session 27). To fix: decide the role
  from SIOCNT's player ID after each transfer, not from SI.

## Stage 5: Wonder Cards for every game (waiting)

Check all 68 `.wc3` files with the tool's own checks, then hardware runs on the cartridges to
hand. **Waiting:** the user's cable stays fitted for the e-Reader route for now.

## Stage 6: e-Card scans (simulator done, 31 October 2026; hardware next)

- `duo_events.py` reads `.raw` strips: blocks, interleaved Reed-Solomon codewords (parity
  checked), strip headers and titles, strips joined, vpk0 unpacked; then it finds the card's own
  Mystery Event script and sends it as the e-Reader did (docs/EREADER.md).
- All 186 Ruby/Sapphire cards in the gallery decode (berries, Battle-e trainers, the US Eon
  Ticket, the Japanese Decoration Present); the model accepts each in its own language, every
  trainer passes the game's checksum, and the berries and the Eon Ticket match the community's
  saved files byte for byte.
- The model now carries out `addtrainer` (the game's own check) and records `runscript`.
- Two tests (with made-up cards built by `tests/ecard_fixtures.py`) and five planted bugs.
- **On hardware:** send a trainer card, e.g. `08-A001 RS - Cool Trainer Devin (USA).raw`, then
  visit the old man's house in Mossdeep City.

## Stage 7: Japanese Ruby and Sapphire (simulator done, 31 October 2026)

- The Japanese language mask is **0x0001**, read from Nintendo's own Japanese cards. Japanese
  `.me3` and `.ecb` files are wrapped with it; Japanese cards go as they are.
- The tool warns at link-up when the event's language isn't the game's.
- The guided start-up gives the Japanese menu (ふしぎな できごと). The Japanese unlock phrase is
  still unconfirmed.
- **Needs** a Japanese Ruby or Sapphire for a hardware run.

## Stage 8: The other e-Reader protocols (Japanese; not started)

Emerald's Trainer Hill (`ereader_screen.c`) and the FireRed/LeafGreen e-Cards: a different link
protocol, so a new GBA program.

## Stage 9: Pokémon (`.pk3`; step 1 done, 31 October 2026)

1. **Done: how each event was given out** (docs/PK3_EVENTS.md). `duo_events.py pk3-info` reads
   both PKHeX's decrypted form and the game's own. Of 3,344 files: 435 by trade (the Mystery
   Mew), about 2,700 by routes that wrote the save (multiboot cartridges such as Aura Mew, event
   machines such as PCNY's, GameCube discs), 167 never distributed, 17 unknown.
2. **Decided (31 October): the trade route for all.** Authentic for the Mystery Mew; for the
   rest, labelled as the nearest route that leaves the game in charge. Never-distributed
   Pokémon are offered the same way, marked as such. No multiboot save writer.
3. **Next: the trade distributor** (docs/TRADE.md has the protocol, read from pokeruby).
   A trade distributor in the Cable Club (the Mystery Mew's authentic route, and the nearest for
   the rest, if chosen): the e-Reader route's link layer, then the trade steps from `trade.c`.
   Whatever the game sends in return is kept as a `.pk3` and can be traded back.

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
