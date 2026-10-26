# The MAX DUO's USB protocol

This was worked out from Datel's 2005 PC program (NintendoDSMaxDrive.exe) and from Datel's firmware, read offline. Nothing was learned by sending unknown commands to the device.

## Packets

- USB ID **05FD:DAAE**, low-speed. Interrupt OUT endpoint **0x02** and IN endpoint **0x81**.
- **Every message is exactly 8 bytes.**
  - PC to DUO: `"CBW"`, a command byte, then a 32-bit little-endian argument.
  - DUO to PC: `"SBW"`, a code byte, then a 32-bit value.
- **The two sides strictly take turns:** one PC packet, then one GBA packet, then one PC packet. Datel's own transfers all work this way, and the USB chip has room for only one 8-byte packet each way. A second PC packet in a row is never delivered (the PC's write times out), and a second GBA packet in a row hangs the GBA. Version 3 of this project broke the rule and stalled ([HISTORY.md](HISTORY.md)).
- A packet sent while the GBA is off waits in the USB chip. Datel's start-up code checks for a waiting hello and goes straight to USB SERVER MODE.

## Datel's commands (firmware 1.06)

| Command | What it does | Our tool (allow-list) |
|---|---|---|
| 0x02 | Hello; replies 0x03 and the version (0x104) | used |
| 0x11 | Send the directory of stored files | used |
| 0x13 | Send stored file N | used |
| 0x26 | Send stored-file information | not used |
| 0x16 / 0x18 / 0x19 / 0x1F | Checksum, start sending, acknowledge, CRC wrong | used |
| 0x1A / 0x12 / 0x24 | DS card: save size, read, page | allowed (these are for **DS** cards, not GBA) |
| 0x22 | Replies with a fixed value (0x40000) | asked once, by `lab_runner.py` |
| 0x0C / 0x0F / 0x20 | Firmware update: receive, checksum, "is the file good" | `--update` only, after you type UPDATE |
| **0x1C** | **DeleteFile: deletes stored file N** | **refused** |
| 0x0A / 0x0B / 0x25 | Receive a file: writes to the DUO's memory or a DS card | **refused** |
| 0x27 | DS card status: sends commands to a DS card's save chip | **refused** |

Saves exported from the DUO's own storage arrive compressed:
- a 0xA8-byte header;
- then chunks of [big-endian size, big-endian compressed size, escape byte, data];
- where `escape, n, v` means "byte v repeated n+1 times".

## Commands added by this project

All new code lives in empty padding inside Datel's image. These commands occupy slots that Datel's firmware answers with "unknown command".

| Command | Added in | What it does |
|---|---|---|
| 0x03 | v1 | Read the GBA save directly (legacy path, later fixed with the wake read) |
| 0x04 / 0x05 / 0x06 | lab edition | Set the upload address, write a word, and run a staged program, sending its output with Datel's send routine (slow) |
| 0x1E | v3 | Upload n bytes to the staging area. Each packet is answered with 0x1F; the last is answered with the CRC-32 of everything received |
| 0x07 | v3 | Run the staged program (log the PC's messages, run, stream the output; see below) |
| 0x08 / 0x1D | v3 | Stream support: resend and pacing (in the firmware; the current tool doesn't need them) |
| 0x09 | v3 | Version: replies `RK31` (v3.1), `RK40` (v4/v4.1) or `RK42` (v4.2) |
| 0x21 | v4 | Restart: reply first, then a BIOS soft reset; in v4.1 it first waits for the PC's hello to arrive |
| 0x23 | v4.2 | Run a program from RAM (see below) |

### Staged programs

A staged program is made up of:
1. the `RKL1` magic;
2. its length;
3. the CRC-32 of the code;
4. Thumb code.

The firmware refuses to run it unless all three checks match. The code is always the project's own small **interpreter**, followed by a list of operations:

| Operation | Notes |
|---|---|
| `r8`, `r16`, `r32` | Read memory |
| `w16` | Only the mapping register 0x0800009C and WAITCNT 0x04000204 |
| `w8` | Only the save-chip command addresses 0x0E005555 / 0x0E002AAA / 0x0E000000, and **never the values 0x80 or 0xA0**, which begin an erase or a program |
| `delay`, `wait16` | Timing |
| `sample16`, `sample8` | Watch an address over time |
| `search` | Find up to four 32-bit patterns |
| `crc` | Byte-wise CRC-32 |
| `crct` | Table CRC-32, 32-bit reads: for ROM only. The save bus is 8-bit, so the tool refuses `crct` there |

Any other write stops the script with `ERR!`. The PC tool checks the same rules before uploading anything. After a run, the firmware always switches the DUO's program back into view and checks it's there. If it isn't, it waits safely in RAM rather than running whatever is mapped.

In v4 the interpreter stays in the GBA's RAM between runs, so the PC only re-sends the header and the operations. The firmware's CRC check then rejects the run if the resident copy has gone, and the tool sends it again in full.

### Programs (v4.2)

The PC uploads an image to the staging area with 0x04 and 0x1E. The image is made up of:
1. the `RKR1` magic;
2. the code length;
3. the CRC-32 of the code;
4. the entry offset (bit 0 = Thumb);
5. the code, linked at 0x02038010.

Command 0x23 checks all of it, replies `0x23 'RUN!'` (or 0x3F if anything is wrong), and calls the code. In
r0 it passes a table of Datel's routines: USB send, receive and available; idle; screen log; packet reply;
CRC. While a program runs, it answers PC packets one at a time:
- `0x2A` poll gets an answer `0x2A value`;
- `0x2B` stop gets an answer `0x2B value`, and then the program returns.

The DUO then shows "Program finished" and is back in USB server mode. The tool only runs the programs
built from `firmware/programs.py`; the tests check that its embedded copies match a fresh build.

### Streams

The output is cut into 4 KB blocks. Each block is run-length compressed (escape byte 0xE7, then `E7 n v` = v repeated n+1 times), or sent raw if that is smaller. The exchange goes:

```
GBA: 0x30 START, total length                     PC: CONT
GBA: block header (index, length|raw flag, CRC-32) PC: CONT
GBA: data packet                                   PC: CONT   (repeated)
...                                                PC: ACK block  (or NAK = send it again)
GBA: next block header ...
GBA: 0x32 END, CRC-32 of everything                (the PC compares it with what it received)
```

Every block is checked on arrival, and a bad one is sent again. The tests corrupt one packet at every possible position in a stream, and the data always recovers. Each packet exchange costs about 18 ms, so speed depends on how well the data compresses ([FINDINGS.md](FINDINGS.md#speed)).

## A warning about other tools

[kirschju/gameshark-gba-tooling](https://github.com/kirschju/gameshark-gba-tooling) is an open-source tool for the **standalone** GBA Action Replay / GameShark. Its device shares the DUO's USB ID, endpoints, 8-byte packets, `CBW` prefix and turn-taking, and even uses the same "world" value 0x101 to show the game. **But its command numbers mean different things on a DUO.** Its first command at start-up is 0x1C with argument 0. On a GameShark that means "get version"; on a MAX DUO it means **"delete stored file 0"**. Don't point it at a MAX DUO. This project's tool refuses 0x1C outright, and a test checks that a full run never sends it.
