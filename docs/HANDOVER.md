# Hand-over (start here in a fresh chat)

Written overnight on 29–30 October 2026, after lab sessions 21–25 and the work that followed
them. Last commit: see `git log -1` on `main` (`v42-local` is kept identical).

## In one paragraph

The project gives a Datel Action Replay MAX DUO (a GBA cartridge with USB) a second life. One
custom firmware, **Reckless v4.2**, serves two Python tools:
- **`maxduo_saves.py`** backs up GBA saves. It's finished; leave it alone.
- **`duo_events.py`** (renamed from `duo_wireless.py` overnight) runs small GBA programs on the
  DUO's GBA so that it plays the hardware a distribution used. A Pokémon game on a second GBA
  then receives the event through its own menus and saves it itself.

Mystery Gift works on real hardware for **Japanese Emerald** and **English LeafGreen**. The
**e-Reader route** (Mystery Event, Ruby/Sapphire) works end to end on hardware: English Ruby
received the wrapped Eon Ticket and Norman gave it (lab session 26, 30 October).

**The project's goal** (agreed 30 October; see docs/ROADMAP.md): support every Gen 3 Project
Pokémon file meant for a GBA Pokémon game, by its authentic route, wherever that leaves the game
to save the event itself. Routes that write the save directly are the user's decision.

## Ground rules (from the user; keep them)

- **Pushing:** standing permission to push. Keep `main` and `v42-local` identical, and push both
  together. Any *other* GitHub change (settings, releases, issues) needs asking first. That
  includes the release on the to-do list.
- **Tiered testing** (agreed 30 October; `python tests/check.py`):
  - `quick` (about 50 s) after each small change while working;
  - `changed` (the tests and planted bugs for the files changed since `origin/main`) before
    calling a piece of work done;
  - `full` (every test, plus the planted bugs for what changed) **before pushing a code change**;
    `full --all-bugs` when the tests themselves or `mutate4.py` change;
  - nothing for documentation-only changes (`*.md`, `lab/`, `docs/`).
- **No Mystery Gift hardware runs for now:** the user's GBA link cable is fitted for the e-Reader
  route and is very tight to remove. Plan hardware tests around the e-Reader cable.
- **Never write cartridge saves** from our code. Leave the DUO's button, the AT switch and the
  clock alone. Never send flash erase/program commands (0x80/0xA0).
- **The user installs firmware themselves** (typing UPDATE). Nothing tonight needs new firmware.
- **Never commit** Datel firmware, Nintendo data (`.wc3`, `.me3`, ROM contents) or personal data.
  That includes the player's trainer ID and name from traces; leave those out of lab notes.
- **Tone:** warm, natural British English; concise. Ask for just the end of each trace.
- **The user's PC:** Windows; Python 3.10 (Thonny); an `ActionReplay` folder in their user folder.
  - Files reach it through the desktop bridge when the chat is linked.
  - **After copying a file there, read it back and compare SHA-1s**: a copy once silently reused
    an earlier upload. Use a fresh staged file name each time.

## What works, and where

| | Status | Lab notes |
|---|---|---|
| Mystery Gift, Japanese Emerald | Old Sea Map and MysticTicket received; serial 0x7F7F chosen automatically | sessions 16–22 |
| Mystery Gift, English LeafGreen | Aurora Ticket (full-size card) received first time; serial 0x7F7D | session 23 |
| Wonder News | simulator only (needs a real news file) | — |
| e-Reader → Ruby | Eon Ticket (wrapped `.me3`) and the PUMKIN BERRY (wrapped `.ecb`) received, 0 mismatches, both collected from Norman | sessions 24–27 |

Lab notes: `lab/sessions/sessionNN/README.md`; index in `lab/README.md`.

## What to do next

- **The user chooses what to work on next.** Don't start new features unasked; the ideas
  discussed on 30 October (Enigma Berries from `.ecb`, ribbons, e-Card trainers, a trade
  distributor) are in the roadmap's goal table.
- **Enigma Berries work on hardware** (session 27: the PUMKIN BERRY from Norman). The user's
  berry files are in `EventsGallery\Released\Gen 3\ENG\e-Card Berries`.
- **Fixed overnight (30–31 October):** the false "the DUO is player 0" (session 27). `ereader.c`
  now takes its role from SIOCNT's player ID after each transfer.
- **Stages 6 and 7 done in the simulator (overnight):** e-Card `.raw` scans and Japanese
  Ruby/Sapphire. **Next hardware test:** a trainer card, e.g.
  `python duo_events.py event --event "EventsGallery\Released\Gen 3\ENG\e-Card .raws\Ruby & Sapphire\Trainers\08-A001 RS - Cool Trainer Devin (USA).raw"`,
  then the old man's house in Mossdeep City. Any e-Reader trainer already in the save is replaced.
- **Copy to the PC:** the PC was asleep at the end of the overnight session, so its
  `duo_events.py` and `HANDOVER.md` are older than `main`. Copy them across (and check SHA-1s)
  before the next hardware test.
- **31 October:** a planted bug that matches its code more than once, or no longer at all, now
  reports BROKEN instead of stopping the run. The bad-program test gained a case with intact code
  and a wrong stored CRC, so "run skips the program's CRC check" is caught every time (damaged
  code could crash the emulator first).
- **Also overnight:** the `finishing` test's `--keep-going` check now runs 45 s (12 s let a
  planted bug through once under load).
- **Stage 9, step 1 done (31 October):** `pk3-info`, `.ect` trainers, and docs/PK3_EVENTS.md.
  Only the Mystery Mew was a trade; nearly every other released event wrote the save directly.
  **The user decides** next: the trade route for all (labelled as the nearest), or the original
  multiboot route (our code writing saves). Don't build either before that.
- **For the user to decide:** the Berry Glitch Fixing Program cards are refused (an e-Reader
  program that writes the save itself), and so would be any card that turns on the clock reset
  (`enableresetrtc`; none of the gallery's does).
- Not yet checked on hardware: the Eon Ticket's Record Mixing gift; Mystery Gift through the new
  guided start-up (waiting: the cable stays fitted for the e-Reader route).
- **The user's folder:** `tidy_folder.bat` (30 October) moves the older files into `old\` when run;
  it leaves `EventsGallery`, `EventsGallery-master`, `Datel`, `saves` and the tools alone.
- The PC's copies of `duo_events.py`, `duo_wireless.py`, `maxduo_saves.py` and this file matched
  `main` (SHA-1) on 30 October; this file has changed since, so copy it across again.

## The `.me3` discovery (session 25; details in docs/EREADER.md)

- **What a `.me3` is.** A community `.me3` holds what Ruby **stores** after an event: the RAM
  script block (byte-sum checksum, magic 0x33, map group, map number, person, script) and the
  Record Mixing gift. It is **not** the Mystery Event script the e-Reader sent.
- **Why the raw file fails.** Ruby runs the received buffer as a script. 0xF0 isn't a command,
  so the engine stops with status 0, "loaded OK". The menu shows its success message and saves,
  having stored nothing.
- **What the tool does now.** It wraps a `.me3` in `checkcompat` (English/German from the file
  name; Ruby and Sapphire), `checksum`, `initramscript`, `setrecordmixinggift` and `end`.
  - `tests/mevent_model.py` is a model of Ruby's engine, written from pokeruby. It confirms
    that the game then stores exactly the file's bytes.
  - It did so locally for the real Eon Ticket file too: 1,012 of 1,012 bytes. That file isn't
    in the repository.
- **Ruby's MYSTERY EVENT menu only carries on as the link master** (`gLinkStatus & 0x20`; it
  closes the link if connected and not master). If the DUO comes up as player 0, the tool says
  to swap the cable's ends.

## What changed overnight (all pushed, if the session finished)

- **`duo_events.py`** replaces `duo_wireless.py`:
  - **Commands:** `gift --card`, `news --news`, `event --event`, plus `adapter-test`, `probe`
    and `find-serial`. Options include `--seconds`, `--keep-going`, `--serial`,
    `--sweep-serial`, `--check-search` and `--lab`, with the global `--wait`.
  - **`duo_wireless.py` is now a stub.** Old flags are translated (`translate_legacy`), with a
    note giving the new command.
- **The guided start-up** (the user's design):
  - Both GBAs off, and the cable for that command.
  - The DUO's GBA on, the game's off.
  - **Progress bars** while the program uploads ("Setting up the DUO's GBA") and the file loads.
  - "Ready. Now switch on the game's GBA, then:" followed by each game's menu path.
    - Japanese Emerald's path comes with romaji, and the おくりもの/もらいもの sign: もらいもの
      means it was switched on too early.
    - The unlock phrases and places are given too.
  - `uprint` never fails on a console that can't show kana.
- **It finishes by itself:**
  - Mystery Gift: after a disconnect (0x30) with server step 6.
  - e-Reader: when the game closes the link after the event; it then says where to go in the
    game.
  - `--keep-going` turns this off.
- **The card/game check.** It warns if a card's file name (Project Pokémon's "E - ", "FL - ",
  "RS - ") names another game than the one the trace detects. The cards themselves don't show
  their game reliably: only the RAM scripts' `setvaddress` bases differed in the three files
  looked at (Emerald 0x0200025x, FRLG 0x020131xx). That's not used.
- **`ereader.c`:**
  - **In-flight flag.** It fixes a race: as master, a transfer could start before the previous
    one had been handled, if it finished between the IF and SIOCNT reads.
  - **Restarts.** It follows the game starting over after a close (three handshake words in a
    row), so two events can go in one session.
  - **Diagnostics for the PC:** mismatch stage counts, the first four mismatches, the longest
    gap, and events sent.
  - **Master pacing** (from session 24): one handshake transfer per frame, a two-frame pause at
    link up, one command cycle per frame, and 13 scanlines from each transfer's end. Only the
    slave role matters for Ruby, but this is kept and tested.
- **The simulator is stricter:**
  - five-scanline transfers;
  - a slave game that needs 0.6 ms to load its next word (it misses transfers started sooner);
  - the game takes in one command per frame, with room for 50;
  - Ruby's master-only menu rule (`menu_rules`).
- **Tests:**
  - missing Datel builds (`paths.MissingBuild`), or an `fw.bin` that isn't Datel's untouched
    firmware, now **skip** instead of failing;
  - new tests: `.me3` files, the master advice, two events per session, connect retry, upload
    retry;
  - **Final run:** 106 tests, 97 passed and 6 skipped (no Datel builds). Three timed out in the
    simulator under heavy load, and passed when re-run alone. 79 planted bugs: the 72 that can
    run here (`prog`, `ereader`, `pc`) were all caught.
- **Docs:** README (new commands, per-game menus, unlock phrases, cable notes), docs/EREADER.md
  (menu statuses, script commands, the `.me3` layout and wrapping), ROADMAP, and lab notes for
  sessions 21–25.

## Still on the to-do list

- **A GitHub release** (a zip of the scripts plus short instructions): **ask the user first.**
- **The lab build (`adapter_lab`) has about 20 bytes spare.** `-Oz` isn't an option, because it
  pulls in `__aeabi_uidiv`, which we can't link. Before the next lab feature, trim `SCOPE_N`,
  `CAP_CMDS`/`CAP_WORDS` or `W_MAX` in `adapter.c`.
- **Wonder News on hardware** needs a real news file. Japanese news is assumed to be 202 bytes.
- **More games on hardware:** English/European Emerald, FireRed, other languages. Use
  `gift --sweep-serial` if a game never finds the distributor.
- **Japanese Emerald's Mystery Event (ふしぎな できごと):**
  - The menu appears only when unlocked at the Petalburg Poké Mart questionnaire (the phrase
    「ふしぎ できごと わくわく ドキドキ」 is unverified), and only when no adapter answers.
  - The Japanese language masks for `checkcompat` aren't known, so Japanese `.me3` files are
    refused for now.
  - Its もらいもの entry is something else: the Trainer Hill e-Reader (`ereader_screen.c`), a
    different protocol.
- **Unexplained:** straight after a successful run, a program upload sometimes stalled (sessions
  16, 21). The retry handles it, and handing the link port back (RCNT/SIOCNT) may have cured it.
  Session 22 was clean.

## Code facts

- **Repository:** `fredreckless/ActionReplayMaxDuo-Pokemon-Toolkit`. The working clone was at
  `/home/claude/v42-wt` (branch `v42-local`). Push with
  `git push origin v42-local && git push origin v42-local:main`.
- **Programs** (`firmware/programs/*.c`, clang 18 + lld; `firmware/programs.py`):
  - `adapter` (everyday Mystery Gift), `adapter_lab` (`-DLAB`), `adapter_probe` (`-DPROBE`),
    `ereader`.
  - Everything, variables included, must end below `0x0203AE00` (`programs.py` asserts it). Above
    that are the firmware's messages, and from `0x0203AF00` its transfer state.
  - There's no hardware divide: no `/` or `%` on variables.
  - **After any change to a program, run `python firmware/embed_programs.py`.** That refreshes
    the copies embedded in `duo_events.py`, and a test checks them against a fresh build.
- **Sizes:** `adapter` 11,264 bytes, `adapter_lab` 11,740, `ereader` about 5.2 KB.
- **Program protocol** (v4.2 firmware):
  - `0x23` RUN runs the staged image (`RKR1` header, CRC-checked);
  - `0x2A` POLL with an argument (low byte selects; `idx << 8`);
  - `0x2B` STOP;
  - `0x2C` PUT loads a file a word at a time (poll 20 starts the load, poll 21 checks its CRC).
  - The link to the PC strictly alternates: one PC packet, then one GBA packet.
- **Mystery Gift facts:**
  - the serial is chosen from the game's 0x16 broadcast: Japanese Emerald 0x7F7F, others 0x7F7D;
  - card checks use the game's CRC-16 (init 0x1121, reflected polynomial 0x8408, inverted);
  - a JP card is 0xA4 bytes, EN 0x14C;
  - news is 202 bytes (JP, assumed) or 444 (EN);
  - polls 20/21 load, 22 the link state (`mg_step` in bits 4–7), 26 check/sweep, 27 serial,
    28 the news result.
- **e-Reader facts:**
  - SIO multi-player mode, 115,200 bit/s;
  - handshake 0x8FFF (master) / 0xB9A0 (slave);
  - commands of 8 words plus a checksum slot (nine transfers per cycle);
  - `0x2222` link players, `0xBBBB` + size + id block header, `0x8888` + 7 words per chunk,
    `0x5FFF` close;
  - link type 0x5501;
  - polls: 0 = state | master<<4 | sessions<<5 | transfers<<8; 1 = bad checksums; 2 = the game's
    link type and language; 3 = the game's player bytes; 4–6 = the mismatch log; 7 = mismatches
    by state; 8 = longest gap | events sent<<16.
- **Tests:**
  - `python tests/check.py quick|changed|full|what`: the tiers (see the ground rules). The
    workspace has 2 cores; the runners default to one job per core (3 made tests time out).
  - `python tests/run_each.py [FILTER]`: every test (or those matching) in its own process, about
    6.5 minutes for all 106 here.
  - `python tests/mutate4.py [KIND ...]`: planted bugs, all or by kind (`fw`, `fw42`, `prog`,
    `ereader`, `pc`). The `fw`/`fw42` kinds need Datel's untouched firmware (`fw.bin`).
  - Without the firmware built, tests that need it are reported as skipped, and the rest run.
  - The models live in `tests/`: `hwsim.py` (the DUO; the multi-player link model with timing),
    `rfu_game_model.py` (Mystery Gift client), `link_game_model.py` (Ruby's link layer and
    Mystery Event menu), `mevent_model.py` (Ruby's Mystery Event script engine).
- **Firmware builds:** reproducible from Datel's 1.06 update file
  (`firmware/build_firmware.py FILE --all`): v4.1 has update CRC `A5303402`, v4.2 `DD59ED8A`.
  **Each new chat's workspace starts without them:** stage Datel's update file from the user's
  PC (`ActionReplay\Datel\us_update_v1_06\us_update_v1_06\us_update_v1_06.bin`) and build
  first. With it, all 106 tests pass and none skip (30 October).

## References (not in the repo)

- `github.com/pret/pokeruby`: `mystery_event_menu.c`, `mystery_event_script.c`, `script.c`
  (`RunScriptCommand`, `InitRamScript`), `link.c`, `data/script_cmd_table.inc`,
  `include/macros/event.inc`, `include/constants/flags.h`. Raw files can be fetched with curl
  from raw.githubusercontent.com; the GitHub API is blocked here.
- `pret/pokeemerald`, `pret/pokefirered`: librfu, link_rfu, `mystery_gift_*`, `main_menu.c`,
  `ereader_screen.c`.
- GBATEK (SIO modes); suloku's tools (the `.wc3` layout); Project Pokémon's event gallery (the
  user's files).
