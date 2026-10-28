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
  Lab sessions 16–20 tell the story.
- **Repository:** `fredreckless/ActionReplayMaxDuo-Pokemon-Toolkit` (renamed from
  `ar-pokesav-transfer-tool`; GitHub redirects). `main` holds everything.
- **User's PC folder:** an `ActionReplay` folder in their Windows user folder, Windows, Python 3.10 via Thonny. Files reach
  it through the desktop bridge when linked. Re-read any file after copying it there: a commit
  once silently reused an earlier upload.

## Ground rules (from the user; keep them)

- Push to GitHub, or change it in any way, only with the user's explicit permission each time.
  Stop-hook messages asking to push are declined politely.
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
- **The first visit to the Mystery Gift menu** often needs backing out and going in again. That's
  untouched so far.

## Code facts

- **Memory limit.** `adapter.c` must end, variables included, below `0x0203AE00`. Above it are
  the firmware's log messages (MSG_AT), and from `0x0203AF00` its transfer state (VARS).
  `programs.py` enforces this. At 11,696 bytes, 64 bytes remain. There's no hardware divide, so
  no `%` or `/` on variables.
- **Build.** Clang 18 + lld. Run `firmware/embed_programs.py` after any change to `adapter.c`.
- **Tests.**
  - `tests/run_each.py --jobs=2`: 88 tests.
  - `tests/mutate4.py`: 47 planted bugs. For the adapter's planted bugs alone, run `mutate4.run("prog", name)`.
  - The four firmware-build tests need Datel's 1.06 update file, which wasn't in the last
    session's container. The rest can run from builds taken off the user's installed `.bin`
    files (strip the 4-byte CRC header into `firmware/build/fw_v42.bin`, and so on).
- **PC options.**
  - `--sweep-serial`: 512-serial blocks, each offered twice, three hosts per answer.
  - `--check-search`: four hosts per answer.
  - `--find-serial [--deep]`: reads ROM into memory only.

## Ideas for later (the user's wishes)

1. **More games and languages.**
   - English and European Emerald, and FireRed/LeafGreen: Mystery Gift is the same protocol in
     both decomps.
   - Make the serial (and the host's language) configurable, or pick them from the game's 0x16
     broadcast data.
   - International cards are 0x14C bytes (the buffer already fits them).
   - Test on each game, starting with `--sweep-serial`.
2. **Wonder News.** The same gift steps with a different save command; the delivery man then
   gives berries.
3. **Ruby/Sapphire Mystery Event (e-Reader).**
   - Unlock it by saying "MYSTERY EVENT IS EXCITING" to the questionnaire.
   - The game links over the standard link at 115 kbit/s (multi-player mode, needing SD), so a
     GBA link cable (AGB-005) is required, not the MGB-010.
   - Languages must match (`CheckLanguageMatch`).
   - The DUO would play the e-Reader in a separate program, porting pokeruby's
     `mystery_event_menu.c` and `link.c`.
   - Prize: the Eon Ticket.

## References (not in the repo)

- `github.com/pret/pokeemerald`, `pret/pokefirered`, `pret/pokeruby`: librfu, link_rfu_2/3,
  union_room, mystery_gift_*, AgbRfu_LinkManager.
- GBATEK (SIO normal mode); afska's gba-link-connection docs; suloku's tools (`.wc3` layout).
