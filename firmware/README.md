# Firmware build scripts

These scripts take **Datel's official MAX DUO 1.06 update** (CRC-32 `1767E827`), which you supply, and apply this project's changes to it. The result is never committed: `.gitignore` excludes `build/`.

```
python build_firmware.py PATH_TO_DATEL_1.06_UPDATE          # -> build/duo_1.06_reckless_v4.1.bin (CRC A5303402)
python build_firmware.py PATH_TO_DATEL_1.06_UPDATE --all    # also the older builds the tests use
```

Each stage builds on the one before. All the GBA code is ARM7TDMI Thumb assembly, kept as text inside these scripts and assembled with Keystone. `thumbcheck.py` then disassembles every routine with Capstone, to prove it uses only instructions the GBA's ARM7 understands.

| Script | Stage |
|---|---|
| `build_patch.py` | v1: command 0x03 reads the GBA save through Datel's own send routine |
| `build_patch2.py` | v2: the PC chooses the mapping; a guard checks that the DUO's program comes back |
| `build_reckless.py` | The cosmetic edition: text and colours. Data only, no code changes. v4 keeps Datel's look |
| `build_lab.py` | The lab: CRC-checked staged programs and the restricted script interpreter |
| `build_v3.py` | v3.1: streamed, compressed, checksummed transfers that strictly take turns |
| `build_v4.py` | v4.1: restart (0x21), Datel's original look, and the version reply `RK40` |
| `history/build_v3_original.py` | The first v3, whose back-to-back packets stalled on real hardware. Kept so the tests can replay that failure |
| `history/build_lab_v31.py` | The lab builder as it was for v3.1 |

The tests check that:
- outside Datel's empty padding area, only command-table slots and a few text pointers change;
- Datel's own command handlers are untouched;
- the build is byte-for-byte reproducible.
