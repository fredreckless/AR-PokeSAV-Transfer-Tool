# AR-PokeSAV-Transfer-Tool

**Back up your Game Boy Advance Pokémon saves to a Windows 10/11 PC, using a Datel Action Replay MAX DUO.**

The MAX DUO was only ever able to back up DS saves, and Datel's drivers stopped working long ago. This project adds the missing piece. It's a small, read-only firmware update for the DUO, plus a Python tool, that together copy a GBA cartridge's save to your PC as a normal `.sav` file. You can open it in PKHeX or an emulator.

Tested on Pokémon Ruby, Sapphire, FireRed and LeafGreen. A full 128 KB save takes about 50 seconds and is double-checked: the GBA and the PC each compute a checksum, and they must agree.

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

It should say `matches the released v4.1`. It also builds `duo_1.06_reckless_v4.2.bin`, which is v4.1 plus the ability to run this project's programs from the GBA's RAM. You only need v4.2 for the experimental `--adapter-test`. Then put the DUO in **USB SERVER MODE** from its menu and install the firmware:

```
python maxduo_saves.py --update firmware/build/duo_1.06_reckless_v4.1.bin
```

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
- **The firmware update is the one step that writes,** and only to the DUO itself, never to your games. It asks you to type `UPDATE` first, and it uses Datel's own update process. You can go back to Datel's official firmware the same way.
- **Every change is tested before real hardware.** That means about 60 automated tests against a simulator of the DUO, plus "planted bugs" that check the tests themselves catch mistakes. Then it is confirmed on a real DUO: 21 saves in a row came out byte-for-byte identical.

## Other things it can do

| Command | What it does |
|---|---|
| `--gba-save` | Back up the inserted GBA game's save |
| `--cart-report` | Cartridge health check: header, ROM size, a whole-ROM checksum to compare with known good dumps, read stability, and the save type |
| `--restart` | Restart the DUO from the PC, straight back into USB server mode |
| `--list`, no options | List or export the **DS** saves stored on the DUO, as Datel's software did |
| `--lab script FILE.json` | Run a small read-only experiment on the GBA (see [lab/](lab/)) |
| `--adapter-test` | *Experimental, firmware v4.2:* the DUO's GBA answers a Pokémon game's Wireless Adapter check on a second GBA, over a GBC link cable. It's the first step towards recreating Mystery Gift events; see [docs/WIRELESS.md](docs/WIRELESS.md) |
| `--adapter-trace` | *Experimental, firmware v4.2:* the DUO's GBA plays a whole Wireless Adapter (with nobody in range) and lists every command the game sends it |
| `--help` | Everything else |

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
| `maxduo_saves.py`, `lab_runner.py` | The PC tool, and a runner for queued read-only lab jobs (handy from Thonny) |
| [`firmware/`](firmware/) | The build scripts that patch Datel's firmware, step by step from v1 to v4.1. They contain all the GBA (ARM7 Thumb) assembly we wrote. `history/` has the original v3, whose transfers stalled on real hardware |
| [`tests/`](tests/) | The simulator (Datel's real firmware running in an ARM emulator, wrapped in a model of the DUO's hardware), the test suite, and the planted-bug checker. `history/` holds the v3 and v3.1 suites |
| [`lab/`](lab/) | Every experiment run on the real DUO, session by session, with the scripts, the complete output log and the conclusions |
| [`docs/`](docs/) | How it works, the hardware (with board photos), the USB protocol, the findings and the project history |
| `tools/reverse-engineering/` | The small scripts used to study Datel's firmware: Ghidra export, disassembly and cross-references |

### Building and testing

```
pip install -r requirements.txt
python firmware/build_firmware.py PATH\TO\DATELS_1.06_UPDATE_FILE --all
python tests/run_each.py      # about 75 tests, each in its own process; around 15 minutes
python tests/mutate4.py       # plants 21 known bugs; each must be caught
```

The adapter program (`firmware/programs/adapter.c`) is built with clang and lld (LLVM 18). You only need them to rebuild it: the tool carries a copy, which a test checks against a fresh build.

The firmware build is reproducible: the same input always gives the same v4.1, update CRC `A5303402`, and v4.2, update CRC `DD59ED8A`. The tests use synthetic saves and ROMs, so they need no game data.

### Read more

- [How it works](docs/HOW_IT_WORKS.md): the plain-English version
- [Hardware](docs/HARDWARE.md): the board, chips, photos and memory map
- [USB protocol](docs/PROTOCOL.md): Datel's commands, our additions, and a comparison with other Action Replay tools
- [Findings](docs/FINDINGS.md): what the lab measured
- [History](docs/HISTORY.md): how it got here, including what went wrong
- [Wireless](docs/WIRELESS.md): work in progress on recreating Mystery Gift distributions

## Credits

Developed by fredreckless, working with Claude (Anthropic). Thanks to earlier Action Replay researchers whose work pointed the way: Parasyte (Kodewerx) and [kirschju/gameshark-gba-tooling](https://github.com/kirschju/gameshark-gba-tooling). Also thanks to GBATEK, devkitPro and the Unicorn, Keystone and Capstone projects.

## Licence

MIT, for the code and documents in this repository; see [LICENSE](LICENSE). Datel's firmware remains Datel's, and is never included.
