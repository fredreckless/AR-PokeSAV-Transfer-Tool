# The lab: experiments on a real MAX DUO

Everything we know about the DUO's behaviour was measured here. Each experiment is a short JSON script of read-only operations: read, search, checksum, wait, and the few permitted writes described in [docs/PROTOCOL.md](../docs/PROTOCOL.md#staged-programs). The GBA runs the script from its own RAM. Experiments were queued in `lab_job.json` and run with `lab_runner.py` from Thonny, which refuses firmware updates and anything that isn't read-only.

```
python maxduo_saves.py --lab script lab/sessions/session7/s7_e1.json     # run one experiment
python lab_runner.py                                                     # run the queue in lab_job.json
```

- **`lab_runner_log.txt`** is the complete output from the real DUO, 22–25 October 2026. Trainer names, IDs and save contents have been redacted, and excerpts of Datel's stored data omitted. Nothing else is changed.
- **`RESULTS.txt`** is the test summary for v4.1, with the real-hardware confirmations.
- **`early/`** holds the first experiments, run with the lab edition firmware.
- **`sessions/`** holds one folder per session: the job list (`lab_job.json`) and its scripts.

## Sessions

| When | Session | Question | Answer |
|---|---|---|---|
| 22 Oct, 00:19 | early: probe, `exit_test`, `readonly_diag` | What does the save area look like through each mapping? Does the Sanyo chip's ID mode exit cleanly? | The chip is a Sanyo LE26FV10N1TS (62 13). Reads straight after a swap gave nothing useful |
| 22 Oct, 00:24 | early: `read_bank0` ×3, then `bank0_v2` | Why does reading the save return only FF? | Reading the save first gives FF every time (CRC DEAB7E4E). **Reading the game header first gives the real save.** This was the discovery of the wake read |
| 22 Oct, 11:27 | early: `tests12` (t1a–c, t2) | Does the wake persist? Which reads wake it? | Cold reads give FF; after any ROM read, data; after re-mapping, FF again. A 16-bit read works too. The first complete save backup was read the same morning |
| 22 Oct, 11:52 | early: `tests13` | Do all three mappings work with a wake read? Does Sanyo ID mode exit? | All three mappings work. ID mode exits correctly with F0 |
| 24 Oct, 20:32 | session 1 | Where do a save's 58 s go? How big is the DUO's flash? What's stored? Wake-read variants | The save-tag search took about 33 s (about 1 s per MB) and each bank 10.3 s. The flash is 512 KB, mirrored. Any single read of any width or address wakes the chip |
| 24 Oct, 20:40 | session 2a | A map of the DUO's flash, in 4 KB checksums | Firmware, leftovers, Datel's storage and the code database are laid out as in [FINDINGS](../docs/FINDINGS.md) |
| 24 Oct, 20:43 | session 2b | Hello sent with the GBA **off**, then switched on? | Answered 14 s later, straight into USB SERVER MODE. The USB chip holds the packet |
| 24 Oct, 20:58 | session 3 | Memory timing (WAITCNT) against speed and correctness; the end of the ROM; the header overlay; Datel's 0x22 | Faster timing gave only about 10% more speed, and 2-cycle access corrupted Ruby's reads. ROM data ends below 8 MB, then FF to 16 MB, then open bus. The Actel overlays 0x20–0x9F. 0x22 replies 0x40000 |
| 24 Oct, 21:08 | session 3b | Does the overlay depend on the mapping or read width? | No: it's the same under every mapping and width |
| 24 Oct, 21:20 | session 4 (×3) | Baselines for Sapphire, FireRed and LeafGreen | Header, save-library tag position and save checksums recorded ([FINDINGS](../docs/FINDINGS.md#cartridge-baselines)) |
| 24 Oct, 22:45 | session 5 | First real test of v4 | Restart worked (0.5 s). The fast save failed: its 32-bit checksum met the **8-bit save bus**. This led to v4.1 |
| 25 Oct, 06:33 | session 6 | v4.1: boot shortcut, fast save, ten restarts each followed by a lab run, cartridge report | All passed. The first save after start-up showed a garbled title, which is the open question |
| 25 Oct, 06:40 | session 7 | Hunting the garbled title (5 experiments ×2, plus a save) | Not reproduced; every header was clean |
| 25 Oct, 06:47 | session 8 | Restart then save, three times, with the new guard read | All clean |
| 25 Oct, 09:20 | session 9 | Cold start then save; speed against compressibility; a soak of 20 saves | The cold-start save was clean. Time is about 2.6 s plus about 18 ms per 8-byte packet. 21 identical saves |
| 27 Oct, 16:45 | session 10 | v4.2: does a real Japanese Emerald accept the DUO as a Wireless Adapter? (MGB-010 cable, small plug to small plug) | **PASS** after 111.8 s: 14 transfers, ID 0x8001 accepted. The game then sent its first adapter command, `9966003D` |
| 27 Oct, 21:15 | session 11 | First real run of the adapter program: Mystery Gift → じゅしん スポット | The ID exchange works, but the first command stalls: the 2 MHz answer from the DUO doesn't reach the game correctly ([details](sessions/session11/README.md)) |
| 27 Oct, 21:47 | session 12 | The 2 MHz probe | Only the **first bit** of each answer is late: holding SO at the top bit works 359/359, and the SO flick gets acknowledgements through ([details](sessions/session12/README.md)) |
| 27 Oct, 22:25 | session 13 | The first-bit fix | Every command's first word is accepted; the acknowledgement's ready flick re-armed the link mid-word ([details](sessions/session13/README.md)) |
| 28 Oct, 08:40 | session 15 | Overnight build (distributor) | 29,562 ready flicks up to 12 µs, none seen: armed, SO shows the word's first bit, not SIOCNT bit 3. The flick now disarms briefly ([details](sessions/session15/README.md)) |
| 28 Oct, 16:20 | session 16 | Disarming flick | Hello, setup, broadcast and start-search complete for the first time; stops at 0x1D (7-word answer) ([details](sessions/session16/README.md)) |

Experiments deliberately **not** done:
- two GBA packets in a row, which would hang the GBA;
- mapping values Datel never uses;
- chip-ID commands on anything that isn't Flash;
- anything touching the cartridges' clocks;
- any write to a save.
