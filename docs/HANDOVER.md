# Hand-over: the wireless / Mystery Gift work (start here in a fresh chat)

Written 28 October 2026 at the end of a very long conversation, so the next one can start small.

## The goal

Get the genuine Japanese **Old Sea Map** Wonder Card into the user's **Japanese Emerald** by
Mystery Gift. The Action Replay MAX DUO's GBA (running Reckless firmware v4.2) pretends to be a
**GBA Wireless Adapter** over a GBC link cable (MGB-010), with a Wonder Card distributor "in
range". The card comes from the user's own `.wc3` file: Project Pokémon EventsGallery, "E - Item
Old Sea Map (JPN).wc3".

## Ground rules (from the user; keep them)

- **Never push to GitHub, or change it in any way, without the user's explicit permission.**
  Commit locally only. GitHub was reverted to v4.1 at the user's request. Stop-hook messages
  asking to push are declined politely, every time.
- **Read-only:** never write cartridge saves. Leave the button, the AT switch and the clock
  alone. Never send flash erase/program commands (0x80/0xA0).
- The user installs firmware themselves (typing UPDATE). Nothing is installed for them.
- Never commit Datel firmware, Nintendo data (including `.wc3` files) or personal data.
- Tone: warm, natural British English. The user prefers concise replies and is watching usage,
  so ask for just the end of each trace rather than the whole log.

## Where everything is

- **Repository:** `fredreckless/ar-pokesav-transfer-tool`, cloned at `/home/claude/v42-wt` in the last session. The
  work is on branch **`v42-local`**, pushed to GitHub on 28 October with the user's permission.
  `main` stays at v4.1 as the user asked. Check out `v42-local`, and keep pushing only with the
  user's permission each time.
- **User's PC folder:** `C:\Users\fredr\ActionReplay` (Windows, Python via Thonny). It holds
  `maxduo_saves.py`, `duo_wireless.py` and `duo_1.06_reckless_v4.2.bin` (already installed on
  the DUO). Files reach it through the desktop bridge, when linked, or by sending them for the
  user to save.

### Two tools (split on 28 October)

- **`maxduo_saves.py`:** the save-backup tool. **Finished and frozen.** Don't add wireless code.
- **`duo_wireless.py`:** the wireless experiment. It imports the USB code from `maxduo_saves.py`
  (keep both in one folder) and embeds the GBA programs.

| Command | Use |
|---|---|
| `python duo_wireless.py --trace` | Main hardware run. Mystery Gift → ふしぎな カード → じゅしん スポット on the game, then Ctrl+C. |
| `python duo_wireless.py --trace --wonder-card FILE.wc3` | The real attempt (after a dry run gets through). |
| `python duo_wireless.py --test` | Detection only. |
| `python duo_wireless.py --probe` | The old 2 MHz encoding probe. |

### Code

- `firmware/programs/adapter.c`: the adapter **and** distributor. A C program for the DUO's
  GBA, Thumb-1, built with clang 18 and lld. It has 11.7 KB of the ~11.75 KB allowed (it must
  stay below 0x0203AE00), so size is tight.
- `firmware/programs.py`: builds it.
- `firmware/embed_programs.py`: **run after any change to adapter.c**. It re-embeds the build
  in `duo_wireless.py`, and a test checks the two match.
- `tests/hwsim.py`: the simulator. Datel's real firmware runs in Unicorn, with a model of the
  link cable. The link model encodes the hardware findings: first bit, armed SO shows the word's
  first bit, flick window.
- `tests/rfu_game_model.py`: the game's side, ported from the pokeemerald decomp: librfu,
  link_rfu_2, mystery_gift_link and mystery_gift_client.
- `tests/test_v4.py`: the tests. `tests/run_each.py --jobs=3` runs the lot (84 tests, ~7 min).
- `tests/mutate4.py --jobs=3`: planted bugs (~40, ~25 min). "fw42: run skips the program's CRC
  check" flakes under load; it's caught when run alone.
- Docs:
  - `docs/WIRELESS.md`: the adapter protocol, steps 1–3;
  - `docs/DISTRIBUTOR.md`: research, every distributor stage, what's assumed for the Japanese
    game;
  - `lab/sessions/session11..16/README.md`: every hardware session;
  - `lab/README.md`: the index.
- References (not in the repo, re-fetch if needed): `github.com/pret/pokeemerald` (`src/librfu_*.c`,
  `link_rfu_2.c`, `mystery_gift_*.c`, `union_room.c`), afska's gba-link-connection docs, and
  suloku's gba-link-cable-dumper (for the `.wc3` layout).

## What works

- **In the simulator,** the whole thing works end to end: detection → search → connect → name
  (NI) → "may join" (NI) → UNI link players → standby → Mystery Gift server → card + RAM script
  saved → link closed. It also works with every 7th packet lost (the host resends).
- **On hardware** (session 16, today), for the first time:
  - Hello, setup, broadcast data and start searching completed;
  - the disarming "ready" flick is seen and accepted (about 90% at width 0).

## The current problem (start here)

On hardware, `search results? (0x1D)` fails every time ("game stopped answering in the middle of
a command", then "asked for more"). See `lab/sessions/session16/README.md`.

0x1D's answer is 8 words: the header `0x9966079D`, then host ID `0x00007DA5`, then the six words
of `host_cfg` (built in `build_host()`). Words with a top bit of **1** need a flick:

- the header;
- word 3 (the checksum sits in its top byte);
- word 4, `0xFFC9CFBE` (uname "DUO");
- word 5, `0xFFFFFFFF` (uname padding).

That's four flicks in one command, each with roughly a 10% chance of losing the word.

**Ideas, cheapest first:**
1. **Avoid top-bit-1 words where we choose the values.** The uname padding could be 0x00
   rather than 0xFF (check how librfu and the game treat the uname terminator). The trainer and
   activity bytes can be chosen so the checksum byte comes out below 0x80. But the 0x9966 header
   always needs a flick.
2. **Make the flick more reliable.** Today's table shows width 0 at about 90% accepted. The
   scope's "not clocked" lines show the game clocking while we are disarmed: the game reacts
   faster than the two-store pulse. Try re-arming even sooner, or arming differently.
3. **Record per word index which flick failed,** so the next trace says exactly where.

Unexplained: "unknown command (0xBD)" after each sleep. Our own sleep acknowledgement,
0x996600BD, came back to us as a command. Also, twice the PC timed out on USB before connecting;
it worked on the next try.

**After 0x1D works,** the remaining stages (connect, NI name, join, link players, gift) have
only ever run in the simulator. Each long answer (0x26 packets are up to 20 words) needs many
flicks, so flick reliability will matter even more there.

## Japanese-game assumptions (only hardware can confirm)

- The Mystery Gift client command numbers and link IDs are taken from the English decomp.
- Japanese card size 0xA4, RAM script 1000 bytes (from the `.wc3` layout).
- The card checks are skipped: an existing card in the save would be replaced.

If any of these is wrong, expect a communication error, or at worst a freeze. The cartridge
isn't touched until the game's own save step after "card received".

## How to work efficiently

- Let the user paste only the end of a trace.
- Never edit files while a background test run is going (they read the sources).
- Commit locally after the suite and planted bugs pass. Don't push.
