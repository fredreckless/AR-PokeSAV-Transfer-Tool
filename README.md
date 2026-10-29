# ActionReplayMaxDuo-Pokemon-Toolkit

**Back up your Game Boy Advance Pokémon saves to a Windows 10/11 PC, and receive Mystery Gift Wonder Cards, using a Datel Action Replay MAX DUO.**

The MAX DUO was only ever able to back up DS saves, and Datel's drivers stopped working long ago. This project gives it a second life. One small firmware update for the DUO works with two Python tools:

- **`maxduo_saves.py`** copies a GBA cartridge's save to your PC as a normal `.sav` file, which you can open in PKHeX or an emulator. It's tested on Pokémon Ruby, Sapphire, FireRed and LeafGreen. A full 128 KB save takes about 50 seconds and is double-checked: the GBA and the PC each compute a checksum, and they must agree.
- **`duo_wireless.py`** turns the DUO's GBA into a **GBA Wireless Adapter with a Wonder Card distributor in range**, so a Pokémon game on a second GBA can receive a Mystery Gift Wonder Card from your own `.wc3` file. It has delivered the 2005 Old Sea Map card to a Japanese Pokémon Emerald on real hardware ([below](#mystery-gift-wonder-cards)).

> Not affiliated with or endorsed by Datel or Nintendo. "Action Replay" is a trademark of Datel. Use at your own risk; see [Is it safe?](#is-it-safe)

---

## Quick start

### You will need

- An **Action Replay MAX DUO** (the silver GBA/DS model, USB ID `05FD:DAAE`) and its USB cable.
- A **Game Boy Advance** and your game.
- A **Windows 10 or 11 PC** with [Python 3.10 or newer](https://www.python.org/downloads/). Tick "Add Python to PATH" when you install it.
- **Datel's official 1.06 firmware update file for the MAX DUO.** It isn't included here, because it is Datel's copyrighted software. The build step checks you have the right file, which has CRC-32 `1767E827`.

### 1. Download this project and install its helpers

Download the project with **Code → Download ZIP** on GitHub and unzip it. Then open a command prompt in that folder and run:

```
pip install -r requirements.txt
```

### 2. Let Windows talk to the DUO (one time only)

1. Plug in the DUO and switch the GBA on.
2. Open [Zadig](https://zadig.akeo.ie/) and choose **Options → List All Devices**.
3. Pick the device with USB ID **05FD DAAE**.
4. Choose **WinUSB** and click **Install Driver**.

### 3. Build and install the new firmware (one time only)

Build the firmware from Datel's file:

```
python firmware/build_firmware.py PATH\TO\DATELS_1.06_UPDATE_FILE
```

It should say `matches the released v4.1`. It also builds **`duo_1.06_reckless_v4.2.bin`**, update CRC `DD59ED8A`. That's v4.1 plus the ability to run this project's programs from the GBA's RAM, and it's the one firmware for both tools. Then put the DUO in **USB SERVER MODE** from its menu and install it:

```
python maxduo_saves.py --update firmware/build/duo_1.06_reckless_v4.2.bin
```

(If you only ever want save backups, v4.1 works too. Everything in this README also works on v4.2.)

Type `UPDATE` when asked, then wait about 30 seconds for it to finish. **Don't unplug anything while it's writing.** Finally, switch the GBA off and on again.

After the update, the DUO looks just as before, except the USB screen says **RECKLESS V4 - READY** and System Test shows **V1.06 RM4**.

### 4. Back up a save

1. With the GBA **off**, put your game in the DUO's slot.
2. Run:

   ```
   python maxduo_saves.py --gba-save
   ```

3. Switch the GBA on when the tool asks. It starts USB server mode by itself; or you can choose USB SERVER MODE from the menu first.

Your save appears in `saves\<game name>\` with the date in its name, for example:

```
Saved: saves\BPGE POKEMON LEAF\2026-10-25 064412 May #33.sav
  Trainer May (ID 12345), play time 9h 10m 42s, save #33, 14/14 sections valid
```

Back up as often as you like: an identical save is never stored twice. The tool also warns you if the trainer on the cartridge has changed since your last backup.

**Always switch the GBA off before swapping cartridges.**

---

## Is it safe?

- **Your game's save is only ever read.** The tool can only send USB commands on an allow-list. The GBA-side code refuses the commands that start an erase or a write on a save chip, and the PC tool checks the same rules before sending anything.
- **Datel's own delete and overwrite commands are blocked outright,** including DeleteFile (0x1C). Another GBA Action Replay tool sends that command at start-up, and on a MAX DUO it would delete a stored file. More on that in [the protocol notes](docs/PROTOCOL.md#a-warning-about-other-tools).
- **The wireless tool reads and writes nothing on either cartridge.** When you receive a Wonder Card, the game saves it itself, as at a real distribution. That's why backing up first is worth it.
- **The firmware update is the one step that writes,** and only to the DUO itself, never to your games. It asks you to type `UPDATE` first, and it uses Datel's own update process. You can go back to Datel's official firmware the same way.
- **Every change is tested before real hardware.** That means about 95 automated tests against a simulator of the DUO, plus "planted bugs" that check the tests themselves catch mistakes. Then it is confirmed on a real DUO: 21 saves in a row came out byte-for-byte identical.

## Other things it can do

| Command | What it does |
|---|---|
| `--gba-save` | Back up the inserted GBA game's save |
| `--cart-report` | Cartridge health check: header, ROM size, a whole-ROM checksum to compare with known good dumps, read stability, and the save type |
| `--restart` | Restart the DUO from the PC, straight back into USB server mode |
| `--list`, no options | List or export the **DS** saves stored on the DUO, as Datel's software did |
| `--lab script FILE.json` | Run a small read-only experiment on the GBA (see [lab/](lab/)) |
| `--help` | Everything else |


## Mystery Gift: Wonder Cards

`duo_wireless.py` is the second tool. It needs firmware v4.2, and `maxduo_saves.py` in the same folder (it shares the USB code). The DUO's GBA plays a GBA Wireless Adapter over a link cable, with a Wonder Card distributor "in range". The game on the second GBA goes through Nintendo's real Mystery Gift exchange and saves the card itself.

**What works so far.**
- **Japanese Pokémon Emerald (BPEJ), on real hardware.** The game finds the distributor, connects, receives the Wonder Card and its script, saves them and disconnects cleanly. For example, the Old Sea Map card has been delivered, and the item collected from the Pokémon Center.
- **The Japanese game's distributor serial is 0x7F7F**, not the 0x7F7D in the English decomp. `--sweep-serial` found it.
- **Other games and languages** (English and European Emerald, FireRed and LeafGreen) use the same Mystery Gift protocol. The distributor reads the game's language and version from its first broadcast, and picks the serial itself: 0x7F7F for Japanese Emerald, and 0x7F7D (from the decomps) for the rest. These work in the simulator, including the larger international cards, but haven't been tried on real hardware yet. If a game doesn't find the distributor, `--sweep-serial` finds its serial in about six minutes, and `--serial` then uses it. **Testers welcome.**

**You will need:**
- Two GBAs: the DUO's, and one for the game.
- A cross-wired Game Boy / Game Boy Color link cable. A Nintendo MGB-010 has been used. GBA link cables aren't cross-wired, so they won't work here.
- Your own `.wc3` file for your game and language. None is included.

**Receiving a card:**
1. **Back up your save first** with `maxduo_saves.py`. The game keeps one Wonder Card, and a new one replaces the old one, together with its script. Collect any item from the old card first.
2. Put the game in the second GBA, connect the cable, and switch the DUO's GBA on to USB SERVER MODE.
3. Run `python duo_wireless.py --trace --wonder-card "YOUR CARD.wc3"`. It should report "Wonder Card loaded". It also checks the card's and script's checksums, as the game would, and warns if the card's language doesn't match the game's.
4. On the game, choose **Mystery Gift → Wonder Cards → Wireless** (ふしぎな おくりもの → ふしぎな カード → じゅしん スポット). Japanese Emerald also has a separate Mystery Event (ふしぎな できごと) menu, which waits for an e-Reader; it isn't this one.
5. When the game says the card has been saved and the link has ended, press Ctrl+C on the PC. Then collect the item, as the card describes: in Emerald, upstairs in any Pokémon Center.

| Command | What it does |
|---|---|
| `python duo_wireless.py --trace --wonder-card FILE.wc3` | Offers the Wonder Card from your `.wc3` file over Mystery Gift, and lists every adapter command the game sends ([docs/DISTRIBUTOR.md](docs/DISTRIBUTOR.md)). Nothing from the file is kept |
| `python duo_wireless.py --trace --wonder-news FILE` | Offers Wonder News instead (your own file: the bare news, or the save's form with its CRC in front). On the game: Mystery Gift → Wonder News → Wireless. If it already has the same news, it keeps its own and says so |
| `python duo_wireless.py --trace` | The same, with no card: the distributor tells the game it has nothing to send |
| `python duo_wireless.py --trace --sweep-serial` | Offers every distributor serial number in turn until the game connects to one (about 6 minutes). Use it for a game or language not tried yet |
| `python duo_wireless.py --trace --serial 7F7D ...` | Use this distributor serial rather than the one chosen for the game |
| `python duo_wireless.py --trace --lab ...` | The lab build: also reports flick tuning, a link-port scope and the game's first data commands |
| `python duo_wireless.py --trace --check-search` | Link check: the game restarts its search after every answer only if our answers arrive intact |
| `python duo_wireless.py --find-serial [--deep]` | Read-only search of the cartridge in the DUO for the distributor serial. `--deep` reads the ROM into memory (never to disk) |
| `python duo_wireless.py --test` | Detection only: answers a Pokémon game's Wireless Adapter check ([docs/WIRELESS.md](docs/WIRELESS.md)) |
| `python duo_wireless.py --probe` | Lab: the 2 MHz answer-encoding probe |

The DUO writes nothing to either cartridge. The only write is the game saving the card it received, as it would at a real distribution.

## If something goes wrong

| Message or problem | Try this |
|---|---|
| "pyusb is missing" | `pip install -r requirements.txt` |
| The DUO isn't found | Re-do step 2 (Zadig), check the cable, and check the DUO's switch is on |
| "The Action Replay stopped answering" | Check the batteries, make sure the screen shows USB SERVER MODE, or run `--restart` |
| "The header's checksum doesn't match… reading it again" | Harmless: the tool re-reads it. If it keeps happening, clean the cartridge contacts |
| "bank N gave different data twice – not saving it" | Nothing was saved, and nothing was changed. Clean the contacts, reseat the cartridge with the GBA off, and try again |

---

## For tinkerers

This repository holds the whole project, not just the finished tool.

| Folder | What's inside |
|---|---|
| `maxduo_saves.py`, `duo_wireless.py`, `lab_runner.py` | The save tool, the Mystery Gift tool, and a runner for queued read-only lab jobs (handy from Thonny) |
| [`firmware/`](firmware/) | The build scripts that patch Datel's firmware, step by step from v1 to v4.2. They contain all the GBA (ARM7 Thumb) assembly we wrote. `programs/adapter.c` is the Wireless Adapter and distributor, in C. `history/` has the original v3, whose transfers stalled on real hardware |
| [`tests/`](tests/) | The simulator (Datel's real firmware running in an ARM emulator, wrapped in a model of the DUO's hardware), the test suite, and the planted-bug checker. `history/` holds the v3 and v3.1 suites |
| [`lab/`](lab/) | Every experiment run on the real DUO, session by session, with the scripts, the complete output log and the conclusions |
| [`docs/`](docs/) | How it works, the hardware (with board photos), the USB protocol, the findings and the project history |
| `tools/reverse-engineering/` | The small scripts used to study Datel's firmware: Ghidra export, disassembly and cross-references |

### Building and testing

```
pip install -r requirements.txt
python firmware/build_firmware.py PATH\TO\DATELS_1.06_UPDATE_FILE --all
python tests/run_each.py      # about 95 tests, each in its own process; around 15 minutes
python tests/mutate4.py       # plants 57 known bugs; each must be caught
```

The adapter program (`firmware/programs/adapter.c`) is built with clang and lld (LLVM 18). You only need them to rebuild it: `duo_wireless.py` carries a copy (refresh it with `python firmware/embed_programs.py`), which a test checks against a fresh build.

The firmware build is reproducible: the same input always gives the same v4.1, update CRC `A5303402`, and v4.2, update CRC `DD59ED8A`. The tests use synthetic saves and ROMs, so they need no game data.

### Read more

- [How it works](docs/HOW_IT_WORKS.md): the plain-English version
- [Hardware](docs/HARDWARE.md): the board, chips, photos and memory map
- [USB protocol](docs/PROTOCOL.md): Datel's commands, our additions, and a comparison with other Action Replay tools
- [Findings](docs/FINDINGS.md): what the lab measured
- [History](docs/HISTORY.md): how it got here, including what went wrong
- [Wireless](docs/WIRELESS.md): the Wireless Adapter protocol, as recreated here
- [Distributor](docs/DISTRIBUTOR.md): the Wonder Card distributor, stage by stage
- [Lab sessions 16–20](lab/README.md): from the first wireless answers to the Old Sea Map arriving
- [Roadmap](docs/ROADMAP.md): what's next for the events tool. Testers with English or European Emerald, FireRed or LeafGreen are very welcome

## Credits

Developed by fredreckless, working with Claude (Anthropic). Thanks to earlier Action Replay researchers whose work pointed the way: Parasyte (Kodewerx) and [kirschju/gameshark-gba-tooling](https://github.com/kirschju/gameshark-gba-tooling). Also thanks to GBATEK, devkitPro and the Unicorn, Keystone and Capstone projects.

## Licence

MIT, for the code and documents in this repository; see [LICENSE](LICENSE). Datel's firmware remains Datel's, and is never included.
