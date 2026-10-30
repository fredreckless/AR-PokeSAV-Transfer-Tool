# ActionReplayMaxDuo-Pokemon-Toolkit

**Back up your Game Boy Advance Pokémon saves to a Windows 10/11 PC, and receive Mystery Gift and Mystery Event distributions, using a Datel Action Replay MAX DUO.**

The MAX DUO was only ever able to back up DS saves, and Datel's drivers stopped working long ago. This project gives it a second life. One small firmware update for the DUO works with two Python tools:

- **`maxduo_saves.py`** copies a GBA cartridge's save to your PC as a normal `.sav` file, which you can open in PKHeX or an emulator. It's tested on Pokémon Ruby, Sapphire, FireRed and LeafGreen. A full 128 KB save takes about 50 seconds and is double-checked: the GBA and the PC each compute a checksum, and they must agree.
- **`duo_events.py`** has the DUO's GBA play the hardware a distribution used, so a Pokémon game on a second GBA receives the event through its own menus and saves it itself:
  - **Mystery Gift**, playing a GBA Wireless Adapter with a Wonder Card distributor in range. On real hardware it has delivered the 2005 Old Sea Map to a Japanese Emerald and the Aurora Ticket to an English LeafGreen ([below](#mystery-gift-wonder-cards-and-wonder-news)).
  - **Mystery Event**, playing the Card e-Reader+ for Ruby and Sapphire. It works end to end on real hardware: English Ruby received the Eon Ticket (lab session 26) ([below](#mystery-event-the-e-reader-route)).

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
- **Every change is tested before real hardware.** That means about 106 automated tests against a simulator of the DUO, plus "planted bugs" that check the tests themselves catch mistakes. Then it is confirmed on a real DUO: 21 saves in a row came out byte-for-byte identical.

## Other things it can do

| Command | What it does |
|---|---|
| `--gba-save` | Back up the inserted GBA game's save |
| `--cart-report` | Cartridge health check: header, ROM size, a whole-ROM checksum to compare with known good dumps, read stability, and the save type |
| `--restart` | Restart the DUO from the PC, straight back into USB server mode |
| `--list`, no options | List or export the **DS** saves stored on the DUO, as Datel's software did |
| `--lab script FILE.json` | Run a small read-only experiment on the GBA (see [lab/](lab/)) |
| `--help` | Everything else |


## Mystery Gift: Wonder Cards and Wonder News

`duo_events.py` is the second tool. It needs firmware v4.2, and `maxduo_saves.py` in the same folder (it shares the USB code). For Mystery Gift, the DUO's GBA plays a GBA Wireless Adapter over a link cable, with a Wonder Card distributor "in range". The game on the second GBA goes through Nintendo's real Mystery Gift exchange and saves the card itself.

**What works on real hardware.**

| Game | Distributor serial | Result |
|---|---|---|
| Japanese Emerald | 0x7F7F (found with `--sweep-serial`) | Old Sea Map (item collected) and MysticTicket received |
| English LeafGreen | 0x7F7D (from the decomps) | The full-size Aurora Ticket card received, first time |

The tool reads the game's language and version from its first broadcast and chooses the serial itself. English and European Emerald, FireRed, and other languages use the same protocol and work in the simulator, but haven't been tried on hardware yet. If a game never finds the distributor, `gift --sweep-serial` finds its serial in about six minutes, and `gift --serial XXXX` then uses it. **Testers welcome.**

**You will need:**
- Two GBAs: the DUO's, and one for the game.
- A cross-wired Game Boy / Game Boy Color link cable. A Nintendo MGB-010 has been used. GBA link cables aren't cross-wired, so they won't work here.
- Your own `.wc3` file for your game and language. None is included. Project Pokémon's file names start with the game ("E - " Emerald, "FL - " FireRed/LeafGreen); the tool warns if that doesn't match the game it finds.
- **Mystery Gift unlocked in the game.** In FireRed, LeafGreen and Emerald, enter **LINK TOGETHER WITH ALL** at the questionnaire in a Poké Mart, then save and restart: MYSTERY GIFT appears on the title menu.

**Receiving a card:**
1. **Back up your save first** with `maxduo_saves.py`. The game keeps one Wonder Card and one script: a new card replaces both, so collect the item from the old one first.
2. **Both GBAs off.** Put the game in the second GBA and connect the cable.
3. **Run** `python duo_events.py gift --card "YOUR CARD.wc3"` and switch on the DUO's GBA when it asks. Leave the game's GBA off.
4. **Wait for "Ready".** Progress bars show the DUO's GBA being set up and the card loading. (If the DUO misses part of the upload, the tool waits about 16 s and sends it again.) The card's and script's checksums are checked, as the game would.
5. **Now switch on the game** and follow the menu the tool shows:
   - FireRed, LeafGreen, English or European Emerald: **MYSTERY GIFT → WONDER CARDS → WIRELESS COMMUNICATION**.
   - Japanese Emerald: **ふしぎな おくりもの → ふしぎな カード → じゅしん スポット → よみこむ**, then B back to the title menu. The title menu shows ふしぎな おくりもの only when the game finds the adapter. If it shows ふしぎな もらいもの (the e-Reader), the game was switched on too early: switch it off and on again.
6. **The tool stops by itself** once the game has the card and has closed the link (`--keep-going` keeps it listening). Then collect the item as the card describes: in Emerald, upstairs in any Pokémon Center.

| Command | What it does |
|---|---|
| `python duo_events.py gift --card FILE.wc3` | Offers the Wonder Card from your `.wc3` file over Mystery Gift, and lists every adapter command the game sends ([docs/DISTRIBUTOR.md](docs/DISTRIBUTOR.md)). Nothing from the file is kept |
| `python duo_events.py news --news FILE` | Offers Wonder News instead (your own file: the bare news, or the save's form with its CRC in front). On the game: Mystery Gift → Wonder News → Wireless. If it already has the same news, it keeps its own and says so |
| `python duo_events.py gift` | No card: the distributor tells the game it has nothing to send |
| `python duo_events.py gift --sweep-serial` | Offers every distributor serial number in turn until the game connects to one (about 6 minutes). For a game or language not tried yet |
| `python duo_events.py gift --serial 7F7D --card ...` | Use this distributor serial rather than the one chosen for the game |
| `python duo_events.py gift --lab ...` | The lab build: also reports flick tuning, a link-port scope and the game's first data commands |
| `python duo_events.py gift --check-search` | Link check: the game restarts its search after every answer only if our answers arrive intact |
| `python duo_events.py find-serial [--deep]` | Read-only search of the cartridge in the DUO for the distributor serial. `--deep` reads the ROM into memory (never to disk) |
| `python duo_events.py adapter-test` | Detection only: answers a Pokémon game's Wireless Adapter check ([docs/WIRELESS.md](docs/WIRELESS.md)) |
| `python duo_events.py probe` | Lab: the 2 MHz answer-encoding probe |

`--seconds N` sets how long to run (default 600), and `--wait N` how long to wait for the DUO's GBA. The old `duo_wireless.py` options still work: `duo_wireless.py` is now a small stub that passes them on and shows the new command.

The DUO writes nothing to either cartridge. The only write is the game saving what it received, as it would at a real distribution.

## Mystery Event: the e-Reader route

Ruby and Sapphire received their events through **Mystery Event**, from the Card e-Reader+ over an ordinary GBA link cable. The DUO's GBA can play the e-Reader:

```
python duo_events.py event --event "YOUR EVENT.me3"
python duo_events.py event --event "YOUR BERRY.ecb"     (an e-Reader Enigma Berry)
python duo_events.py event                      (a dry run: link players only)
```

**You will need:**
- a **GBA** link cable. Game Boy and Game Boy Color cables lack the SD line this mode needs. Unofficial cables often have two identical ends.
- your own event file for your game and language: a Mystery Event script, a saved event (`.me3`), or a saved Enigma Berry (`.ecb`), as Project Pokémon has them. These are what the game *stores*, not what the e-Reader sent, so the tool wraps each in the script that makes the game store exactly that ([docs/EREADER.md](docs/EREADER.md)). English and German files only, for now.
- **Mystery Event unlocked.** Tell the man by the PC in **Petalburg City's Pokémon Center** "**MYSTERY EVENT IS EXCITING**" (after beating Norman), then save and restart: MYSTERY EVENT appears on the title menu.

**Which end of the cable.** The game must be the link's **master**: Ruby's MYSTERY EVENT menu only carries on as master. Which end makes a GBA master depends on the cable (on Nintendo's own, the small purple plug). You don't need to know in advance: if the DUO comes up as player 0, the tool says so, and you switch both GBAs off and swap the ends.

**Sending an event:**
1. **Back up your save first.** The game saves after the event.
2. **Both GBAs off**, cable connected. Run the command and switch on the DUO's GBA when asked.
3. **Wait for "Ready"**, then switch on the game, choose **MYSTERY EVENT**, and press A when it asks.
4. The tool stops by itself once the game has the event and has closed the link. The game then runs it, saves, and shows its message. Press A, and the tool tells you where to go: for the Eon Ticket or an Enigma Berry, to your Dad in Petalburg City Gym (a berry comes after you've beaten him).

The DUO copies the game's link type and language into its own link-player details, so the game's checks agree. If the game opens the link again (for another event), the DUO starts over with it (with `--keep-going`).

**Status:** English Ruby received the Eon Ticket on real hardware, and Norman gave it (lab session 26). The PUMKIN BERRY, an e-Reader Enigma Berry, followed (lab session 27). See [docs/EREADER.md](docs/EREADER.md).

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
| `maxduo_saves.py`, `duo_events.py`, `lab_runner.py` | The save tool, the events tool (Mystery Gift and Mystery Event; `duo_wireless.py` is a stub for its old options), and a runner for queued read-only lab jobs (handy from Thonny) |
| [`firmware/`](firmware/) | The build scripts that patch Datel's firmware, step by step from v1 to v4.2. They contain all the GBA (ARM7 Thumb) assembly we wrote. `programs/adapter.c` is the Wireless Adapter and distributor, and `programs/ereader.c` the e-Reader, in C. `history/` has the original v3, whose transfers stalled on real hardware |
| [`tests/`](tests/) | The simulator (Datel's real firmware running in an ARM emulator, wrapped in a model of the DUO's hardware), the test suite, and the planted-bug checker. `history/` holds the v3 and v3.1 suites |
| [`lab/`](lab/) | Every experiment run on the real DUO, session by session, with the scripts, the complete output log and the conclusions |
| [`docs/`](docs/) | How it works, the hardware (with board photos), the USB protocol, the findings and the project history |
| `tools/reverse-engineering/` | The small scripts used to study Datel's firmware: Ghidra export, disassembly and cross-references |

### Building and testing

```
pip install -r requirements.txt
python firmware/build_firmware.py PATH\TO\DATELS_1.06_UPDATE_FILE --all
python tests/run_each.py      # about 106 tests, each in its own process; around 10-15 minutes
python tests/mutate4.py       # plants known bugs; each must be caught
```

The GBA programs (`firmware/programs/*.c`) are built with clang and lld (LLVM 18). You only need them to rebuild them: `duo_events.py` carries copies (refresh it with `python firmware/embed_programs.py`), which a test checks against a fresh build.

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
- [e-Reader](docs/EREADER.md): Mystery Event over the link cable, as the DUO plays it
- [Roadmap](docs/ROADMAP.md): what's next for the events tool. Testers with English or European Emerald, FireRed or LeafGreen are very welcome

## Credits

Developed by fredreckless, working with Claude (Anthropic). Thanks to earlier Action Replay researchers whose work pointed the way: Parasyte (Kodewerx) and [kirschju/gameshark-gba-tooling](https://github.com/kirschju/gameshark-gba-tooling). Also thanks to GBATEK, devkitPro and the Unicorn, Keystone and Capstone projects.

## Licence

MIT, for the code and documents in this repository; see [LICENSE](LICENSE). Datel's firmware remains Datel's, and is never included.
