# Hand-over (start here in a fresh chat)

Updated 28 October 2026, after the Old Sea Map Wonder Card was received on real hardware.

## Where things stand

- **One firmware, two tools.** Reckless v4.2 (`duo_1.06_reckless_v4.2.bin`, update CRC `DD59ED8A`)
  serves both:
  - `maxduo_saves.py`: GBA save backups. Finished and frozen; don't add wireless code.
  - `duo_wireless.py`: Mystery Gift. The DUO's GBA plays a GBA Wireless Adapter with a Wonder
    Card distributor in range. It imports the USB code from `maxduo_saves.py` (keep both in one
    folder) and embeds the GBA program `firmware/programs/adapter.c`.
- **Working on real hardware:** Japanese Emerald (BPEJ) receives a Wonder Card and its RAM script
  over the whole link: search, connect, NI name, join, link players, Mystery Gift server, close.
  Lab sessions 16–20 tell the story. Session 21: the game and serial are now recognised
  automatically, and two cards were received back to back.
  Session 23: **English LeafGreen** received the full-size (0x14C) Aurora Ticket card first time,
  serial 0x7F7D, with an English host.
- **Repository:** `fredreckless/ActionReplayMaxDuo-Pokemon-Toolkit` (renamed from
  `ar-pokesav-transfer-tool`; GitHub redirects). `main` holds everything.
- **User's PC folder:** an `ActionReplay` folder in their Windows user folder, Windows, Python 3.10 via Thonny. Files reach
  it through the desktop bridge when linked. Re-read any file after copying it there: a commit
  once silently reused an earlier upload.

## Ground rules (from the user; keep them)

- **Pushing (updated 29 October):** the user has given standing permission to push. Keep `main`
  and `v42-local` in step, pushing both together after the suite and planted bugs pass. Any other
  GitHub change (settings, releases, issues) still needs asking first.
- Never write cartridge saves from our code. Leave the button, the AT switch and the clock alone.
  Never send flash erase/program commands (0x80/0xA0).
- The user installs firmware themselves (typing UPDATE).
- Never commit Datel firmware, Nintendo data (including `.wc3` files and ROM contents) or personal
  data. That includes the player's trainer ID and name from traces.
- Warm, natural British English. Concise replies. Ask for just the end of each trace.

## What we learnt (hardware)

- **Flicks.** At 2 MHz, words after a ready handshake whose top bit is 1 need the "ready" flick:
  disarm briefly, then re-arm with SO high. About 99.9% are accepted at width 0.
- **Top-bit-0 words.** They must go low unarmed and then arm (`SIOCNT = 0x1000; 0x1080`).
  Arming straight from resting high leaves SO high, and the game never clocks (session 17).
- **Answer data.** The distributor's 0x1D answer is chosen so that only its header needs a flick.
  The name is "DUO" plus a space, with 0x00 padding; the trainer ID is 0x5678.
- **Japanese Emerald's distributor serial is 0x7F7F.** The English decomp's 0x7F7D isn't in the
  ROM. `--sweep-serial` found it (session 19).
- **The MGB-010 cable** runs at 4× its rated speed and is cross-wired. It's adequate: the data
  words arrive intact (`--check-search`, session 18).
- **"unknown command (0xBD)/(0x96)"** is the game clocking once more after sleep: it's our own
  last acknowledgement, echoed back. Harmless.
- **Back-to-back runs.** Straight after a successful run, the next program upload can stall: an
  upload answer is held up on its way to the PC and turns up later (session 21; also session 16).
  `run_program` now waits for the DUO to give up (~16 s), says hello again and re-sends. The cause
  is unknown.
- **One card at a time.** The game keeps one Wonder Card and one RAM script. A new card replaces
  both, so collect each item from the delivery man before receiving the next card.
- **The "first visit" problem** (corrected in session 22): the game was started too early.
  Japanese Emerald looks for a Wireless Adapter as ふしぎな おくりもの opens. If the DUO isn't
  running the adapter yet, the game offers the e-Reader instead. Backing out and going in again
  works once the adapter is "Ready". So: start the game only after "Ready".

## Code facts

- **Memory limit.** Each build of `adapter.c` must end, variables included, below `0x0203AE00`.
  Above it are the firmware's log messages (MSG_AT), and from `0x0203AF00` its transfer state
  (VARS). `programs.py` enforces this. There's no hardware divide, so no `%` or `/` on variables.
- **Three builds of `adapter.c`:**
  - `adapter`: everyday, 10,792 bytes, about 960 spare;
  - `adapter_lab` (`-DLAB`, `--lab`): adds the flick tables, scope, capture and calibration, but
    leaves out the sweep; 11,660 bytes;
  - `adapter_probe` (`-DPROBE`, `--probe`): the 2 MHz probe.

  `mutate4.py` rebuilds and swaps both the everyday and the lab images for each planted bug.
- **Serial and language** come from the game's 0x16 broadcast:
  - Japanese Emerald → 0x7F7F;
  - anything else → 0x7F7D;
  - `--serial HEX` overrides (poll 27).

  The host's language follows the game. A word that equals our own last acknowledgement (an
  echo after sleep) is ignored.
- **Card checks** (`check_wc3`): the game's CRC-16 over the card and the RAM script, and the
  0x33 marker.
- **Wonder News** (`--wonder-news`; the everyday build only):
  - the gift's size word has bit 16 set;
  - the host offers hasNews and activity 22;
  - the server steps are: game data → save-news script → news (ID 23) → the game's answer
    (ID 19) → "received" or "had news" → ready to end.
  - Japanese news is 202 bytes (inferred from the Japanese card's 18-byte text fields; unconfirmed),
    and English news 444.
- **Build sizes now:** `adapter` 11,264 bytes (about 500 spare); `adapter_lab` 11,740 (20 spare). If a
  planted bug makes the lab build too big, `mutate4` tests the everyday build only.
- **Build.** Clang 18 + lld. Run `firmware/embed_programs.py` after any change to `adapter.c`.
- **Tests.**
  - `tests/run_each.py --jobs=2`: 99 tests.
  - `tests/mutate4.py`: 65 planted bugs. The kinds are `prog` (adapter.c), `ereader`
    (ereader.c), `pc`, `fw` and `fw42`. For the adapter's planted bugs alone, run `mutate4.run("prog", name)`.
  - The four firmware-build tests need Datel's 1.06 update file, which wasn't in the last
    session's container. The rest can run from builds taken off the user's installed `.bin`
    files (strip the 4-byte CRC header into `firmware/build/fw_v42.bin`, and so on).
- **PC options.**
  - `--sweep-serial`: 512-serial blocks, each offered twice, three hosts per answer.
  - `--check-search`: four hosts per answer.
  - `--find-serial [--deep]`: reads ROM into memory only.

## Ideas for later (the user's wishes)

1. **More games and languages** (simulator done; see docs/ROADMAP.md). English LeafGreen is confirmed
   (session 23). What's left is hardware: a short run per remaining cartridge, with `--sweep-serial` if the game doesn't find the distributor.
2. **Wonder News** (simulator done). What's left is a real news file and a hardware run.
3. **Ruby/Sapphire Mystery Event (e-Reader)**, simulator done. The program is `ereader.c`; the
   PC commands are `--mystery-event FILE` and `--ereader`; the notes are docs/EREADER.md. The user
   has an AGB-005. What's left is a hardware run and a real English event file. Plan for the
   first run:
   - try the game on the purple plug (the game as master) first;
   - if anything goes wrong, try the other end.

   Details:
   - Unlock it by saying "MYSTERY EVENT IS EXCITING" to the questionnaire.
   - The game links over the standard link at 115 kbit/s (multi-player mode, needing SD), so a
     GBA link cable (AGB-005) is required, not the MGB-010.
   - Languages must match (`CheckLanguageMatch`).
   - The DUO would play the e-Reader in a separate program, porting pokeruby's
     `mystery_event_menu.c` and `link.c`.
   - Prize: the Eon Ticket.
4. **Clearer start-up on the PC (to do; the user's design).** Guide a strict order:
   1. Before running anything: "Both GBAs should be switched off."
   2. Run the command. Then: "Switch on the DUO's GBA now (leave the game's GBA off)."
   3. A progress bar while the program uploads and the card loads: "Setting up the DUO's
      GBA... don't switch on the game's GBA yet."
   4. Only then: "Ready - now switch on the game's GBA and choose Mystery Gift."

   It's the same for the e-Reader. Why: starting the game early caused both the e-Reader
   message (the game found no adapter as Mystery Gift opened) and the stalled upload in
   session 21. Session 22, started in the right order, ran twice back to back without trouble.
5. **Japanese Emerald menus in the instructions (to do; the user's observations).**
   - **The title menu** has four entries: つづきから はじめる (Continue), さいしょから はじめる
     (New Game), the gift entry, and せっていを かえる (Options).
   - **The gift entry shows whether the adapter is ready.** It reads ふしぎな もらいもの when the
     game finds no Wireless Adapter. It reads **ふしぎな おくりもの** when the cable is connected
     and the DUO is "Ready". So おくりもの on the title screen is the sign to go ahead. If it
     says もらいもの, the game was started too early: restart it after "Ready".
   - **The route:** ふしぎな おくりもの → ふしぎな カード → じゅしん スポット → よみこむ (receive),
     then B back to the title menu.
   - **Update** the README, the tool's prompts (tie into item 4) and the docs. Drop the line
     about a separate ふしぎな できごと menu.

## References (not in the repo)

- `github.com/pret/pokeemerald`, `pret/pokefirered`, `pret/pokeruby`: librfu, link_rfu_2/3,
  union_room, mystery_gift_*, AgbRfu_LinkManager.
- GBATEK (SIO normal mode); afska's gba-link-connection docs; suloku's tools (`.wc3` layout).
