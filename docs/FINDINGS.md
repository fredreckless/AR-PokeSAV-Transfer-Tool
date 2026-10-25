# What we learned about the MAX DUO

Everything here was measured on one real DUO in October 2026, using
read-only lab scripts (`maxduo_saves.py --lab script ...`) and photos of the board.
The scripts and the complete output are in [lab/](../lab/); the chips are described in
[HARDWARE.md](HARDWARE.md).

## The board

| Part | What it is |
|---|---|
| SST 39VF400A | 512 KB flash (x16, 4 KB sectors, ID BF/2780): Datel's program and stored data |
| Actel eX256 (TQ100) | Antifuse FPGA (12k gates, 2.5 V core). Swaps the DUO's program and the game cartridge in and out of the GBA's address space |
| Cypress CY7C63743 | enCoRe low-speed USB controller (8 KB OTP, 256 B RAM, 8-byte endpoints). Powered from USB, so it holds packets with the console off |

## Address map (as the GBA sees it)

- The DUO's 512 KB flash appears twice below 1 MB, with open bus above.
- 0x08030000–0x0803BFFF holds Datel's storage ("DATEL NDS1 V1.0") and directory records.
- 0x08040000–0x0807FFFF holds the Action Replay code database (256 KB).
- **The Actel overlays the header area 0x08000020–0x0800009F** in every mode and at every access width. It reads as zeros, except for its mapping register at 0x9C, which reads F0 0F.
- The mapping register (0x0800009C) chooses what the GBA sees: 0x0120 is the DUO program, and 0x0101 / 0x0007 / 0x0001 show the game cartridge.

## Behaviour that shaped the firmware

- **The USB link strictly alternates.** Every transfer goes PC packet, then GBA packet, then PC packet. v3 sent packets back to back and stalled on real hardware ("LAB: upload timed out"); v3.1 fixed this by alternating strictly.
- **The save chip sleeps after every swap-in.** It only answers once any game ROM read has happened. Any single read wakes it, at any address and width.
- **The save area is an 8-bit bus.** 16- and 32-bit reads return the byte repeated, so the save checksum must read byte by byte. v4's first real save found this.
- **The DUO has no Nintendo logo.** It borrows the game's at power-on, so with no cartridge the screen shows "GAME BOY" and stops. A BIOS hard reset would therefore freeze. `--restart` uses the soft reset, which skips the logo check.
- **Datel's start-up code checks once for a waiting hello** and, if one is there, goes straight to USB server mode. Reckless v4.1 waits for the PC's hello before restarting, so a restart always lands in server mode (13 of 13 on real hardware).
- **Game ROM timing.** Two-cycle first-access timing corrupted reads on a Ruby cartridge but not on LeafGreen, so the tool uses the games' own timing.

## Speed

- A run costs about 2.6 s fixed, plus about 18 ms for every 8-byte packet actually sent: one GBA packet and one PC reply, roughly 9 ms each.
- Data is run-length compressed, so speed depends on the save. A near-empty save bank takes about 10 s, while a 9-hour Pokémon save takes about 24 s per 64 KB bank (about 48 s in all).
- Searching a ROM for its save-library tag runs at about 1 s per MB. Games the tool knows skip the search.
- 21 consecutive saves of the same cartridge were byte-identical, with no re-reads or re-sent blocks.

## Cartridge baselines

| Game | Code | Revision | Save library tag at |
|---|---|---|---|
| Ruby | AXVE | 2 | 0x086B07AC |
| Sapphire | AXPE | 1 | 0x086B080C |
| FireRed | BPRE | 1 | 0x086FBF74 |
| LeafGreen | BPGE | 1 | 0x086FB860 |

All four use 16 MB ROM chips with data below 8 MB, FF padding above, and open bus from 16 MB.
LeafGreen's corrected whole-ROM CRC-32 (DAFFECEC) matches the No-Intro value we had on record (worth verifying against the current database).

## Open questions

- Twice, the first run after a start-up returned its first 3–5 output bytes garbled. It hasn't been seen since in more than 30 attempts. The tool now reads a 32-byte guard first, reports any garbling, and re-reads a header whose checksum fails.
- Is the ~9 ms per transfer set by the DUO's USB chip or by Windows' polling? Reading the USB descriptors would tell.
