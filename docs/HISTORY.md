# How it got here

The whole project took five days, 21–25 October 2026. Here's the story, including the parts that went wrong, because those taught the most.

## 21 October: a driver that wouldn't install

The MAX DUO's Datel drivers are 32-bit and unsigned, so Windows 11 won't load them. Rather than write a kernel driver, we attached the DUO to Windows' generic **WinUSB** driver with Zadig, and talked to it from Python.

- Datel's protocol came from their 2005 PC program: 8-byte `CBW` / `SBW` messages.
- The first version of `maxduo_saves.py` listed and exported the **DS** saves stored on the DUO, as Datel's software had.
- One early dead end is kept in `tools/reverse-engineering/ar_dump_dsi_early_attempt.py`. It's a dumper written for the Action Replay DSi's protocol, before we knew which device we had.

## Reading Datel's firmware

Datel's firmware turned out to have **no way to read a GBA save at all**. Its "cartridge" commands are for DS cards. Reading the firmware offline (Ghidra, Capstone; see `tools/reverse-engineering/`) showed:
- the command table;
- Datel's send and receive routines;
- the mapping register that swaps the game in;
- a large empty padding area inside the image.

That padding is where all of this project's code lives.

## v1 and v2: reading nothing

- **v1** added command 0x03: swap the game in, read 0x0E000000, swap back, and send the data using Datel's own send routine. It ran without errors, and returned nothing but FF.
- **v2** let the PC choose the mapping value, defaulting to Datel's own in-game value. It also added a guard: if the DUO's program doesn't come back after a swap, the routine waits safely in RAM instead of crashing into the game. It still returned nothing useful.
- The cosmetic "Reckless Modding" edition recoloured the menus at this point. v4 later restored Datel's original look.

## The lab edition, and the wake read (22 October)

Guessing wasn't working, so the next firmware added a **lab**. The PC uploads a small CRC-checked script interpreter, which runs read-only experiments on the GBA. Within an hour on 22 October:

- reading the save straight after a swap gave **FF, every time**;
- reading **the game's header first** gave the real save.

The save chip ignores everything until the game's ROM has been read once after each swap. This is now called the **wake read**, and it doesn't seem to be documented anywhere else. That morning the first complete save came off the cartridge through the lab.

## v3: fast, and stalled

v3 made backups fast and automatic:
- compressed 4 KB blocks with CRCs;
- fast uploads;
- save-type detection from the game's own save-library tag, rather than by sending commands to the chip.

It passed every simulator test, and on the real DUO it stopped with **"LAB: upload timed out"**.

Re-reading Datel's code explained why. The link between the GBA and the USB chip is a single byte-wide channel, and every one of Datel's transfers **strictly takes turns**. v3 sent packets back to back, and the second one never arrived. The simulator hadn't known that rule, so it couldn't catch the mistake.

The rule went into the simulator first, which reproduced the stall exactly (`tests/test_v4.py`, "reproduction"). Only then was it fixed.

## v3.1: the first fast save (24 October)

v3.1 kept everything from v3 but takes turns strictly. On the real DUO it read a 128 KB Pokémon Ruby save in 58 seconds, checked by both sides. The same evening, the PCB photos identified the chips ([HARDWARE.md](HARDWARE.md)), and lab sessions 1–4 measured where the time went, the flash layout, the header overlay and the memory timing.

## v4: restart, a known-games list, the cartridge report (24 October)

v4 added several things:
- a **restart** command (a BIOS soft reset, because the DUO has no logo of its own for a hard reset to check);
- Datel's original look;
- a resident interpreter, so uploads are smaller;
- a list of known games, which skips the 33-second search;
- `--cart-report`;
- smarter backups.

Two problems surfaced:
- **A restart race, caught by the simulator.** Once the simulator modelled real USB delay, v4's restart sometimes beat the PC's hello and landed in the menu. v4.1 waits until the hello has arrived before resetting.
- **The 8-bit save bus, caught by real hardware.** v4 checksummed the save with fast 32-bit reads. The save bus is 8 bits wide, so each 32-bit read returns one byte repeated, and the checksums never matched. Nothing wrong was saved: the tool refused, exactly as designed. v4.1 checksums the save byte by byte. The simulator now models the 8-bit bus, and a planted bug checks that the tests notice.

## v4.1 (25 October)

v4.1 was confirmed on the real DUO:
- 13 of 13 restarts landed in server mode;
- 21 consecutive saves were byte-identical;
- the cartridge checksum for LeafGreen matched the No-Intro value we had on record.

One question remains open: twice, the first save after a start-up showed a garbled title. It hasn't been seen since, and the tool now guards against it ([FINDINGS.md](FINDINGS.md#open-questions)).

## Lessons worth passing on

- **Measure, model, test, correct the model.** The simulator only knows what has been observed. When the hardware disagrees, reproduce the failure in the simulator before fixing it.
- **Test the tests.** `mutate4.py` plants each bug we've ever met and checks it's caught.
- **Make failure safe.** Every real-hardware failure in this project ended in a refusal or a timeout, never in a damaged save.
