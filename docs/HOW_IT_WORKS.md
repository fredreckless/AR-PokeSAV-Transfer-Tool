# How it works

This is the plain-English tour. The details live in [HARDWARE.md](HARDWARE.md), [PROTOCOL.md](PROTOCOL.md) and [FINDINGS.md](FINDINGS.md).

## What the MAX DUO really is

The DUO is not a computer in its own right. When you switch the GBA on, the **GBA itself** runs Datel's program, which is stored in the DUO's flash chip as if it were a game. The DUO adds three things around that:

- **A switch** (an Actel logic chip). It decides whether the GBA sees the DUO's own program or the game cartridge plugged into its slot. Writing a value to one address flips it.
- **A USB chip** (a Cypress microcontroller). It passes 8-byte messages between the PC and the GBA, one at a time.
- **Its own flash memory** (512 KB). This holds Datel's program, the cheat code database and the DS saves it stores.

```
PC ══ USB ══ Cypress USB chip ══ one-byte link, taking turns ══╗
                                                               ║
GBA ══ cartridge bus ══ Actel switch ══ DUO flash (Datel's program, codes, DS saves)
                              └── your GBA game (ROM + save chip)
```

## Why Datel's software couldn't back up GBA saves

Datel's "read the cartridge" commands are for **DS** cards. Nothing in the firmware reads a GBA game's save, and the PC drivers no longer work on modern Windows anyway. So two things were needed:

1. **A PC tool that speaks the DUO's USB language without Datel's driver.** `maxduo_saves.py` uses Windows' generic WinUSB driver, set up once with Zadig. Datel's protocol was worked out from their PC program: 8-byte messages that start with `CBW`, with replies that start with `SBW`.
2. **New firmware commands that read the GBA save.** These are added to Datel's own firmware, in the unused space inside it. Everything Datel wrote stays where it was, and the update goes through Datel's normal update process.

## Reading a save, step by step

The trick is that the DUO's program has to switch *itself* out of view to reach the game. So the reading code is copied into the GBA's own RAM first:

1. The PC uploads a tiny program, checked with a CRC, into the GBA's RAM.
2. That program flips the switch so the GBA sees the game cartridge instead of the DUO.
3. **It reads one byte of the game's ROM.** This is the "wake read". The save chip ignores everything until the game's ROM has been touched after a switch. Finding this took several failed versions, and as far as we know it isn't documented anywhere else.
4. It reads the save **one byte at a time**, because the GBA's save bus is only 8 bits wide, and computes a CRC-32 of what it read.
5. It flips the switch back to the DUO, and always checks that the DUO's program is visible again before carrying on.
6. The DUO sends the data to the PC, compressed in 4 KB blocks, each with its own CRC. The PC checks every block, then compares the whole bank against the GBA's own CRC. If anything disagrees, the bank is read again. If it disagrees twice, the tool refuses to save anything.

A 128 KB save is read as two 64 KB banks. During a backup, switching between the banks is the only command sent to the save chip, and it's the same one the game itself uses.

## Why it takes about 50 seconds

The USB chip can only hold one 8-byte message, and the two sides must take turns: PC, then GBA, then PC. Each turn takes about 9 ms, so every 8 bytes of data costs about 18 ms. Compression helps a lot: a nearly empty save shrinks to about 5% and copies in about 25 seconds, while a well-played one shrinks to about 14% and takes about 50.

Version 3 tried to skip the turn-taking for speed, and it stalled on real hardware. That failure is how we learned the rule. It's now built into the simulator, so every change is tested against it.

## The lab

To learn all this safely, the firmware has a small "lab". The PC sends a short script, which the GBA runs from its RAM. Its operations are limited to:

- read;
- search;
- checksum;
- wait;
- a few permitted writes: the DUO's switch, the GBA's memory timing, and harmless save-chip commands.

Erase and write commands are refused, both by the PC tool and again on the GBA. The [lab/](../lab/) folder records every experiment run on the real DUO.

## Testing without risking anything

Before any change reaches real hardware, it runs in a **simulator**. This is Datel's actual firmware running in an ARM emulator, wrapped in a model of everything measured on the real DUO:

- the turn-taking;
- the sleeping save chip;
- the 8-bit save bus;
- the header overlay;
- start-up and restart behaviour;
- USB delays.

The test suite also corrupts packets at every possible position, pulls the PC away mid-transfer, and checks that nothing ever writes to a save. Then `mutate4.py` deliberately plants known bugs, each of which must make a test fail. That checks the tests themselves.
