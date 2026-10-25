# Reverse-engineering helpers

These are small scripts used to study Datel's firmware offline. They need your own copy of the firmware image, `firmware/build/fw.bin`, which is made by `build_firmware.py`. Neither the image nor anything decompiled from it is included here.

- `DumpFw.java`, `SeedThumb.java`: Ghidra scripts. They seed Thumb disassembly from the entry point, then export every function's decompiled C (`fw.c`) and the string cross-references.
- `tdis.py START END`: disassembles a Thumb range with Capstone.
- `fq.py`, `refs.py`, `pool.py`: find functions in `fw.c`, find references to an address, and resolve literal pools.
- `ar_dump_dsi_early_attempt.py`: the project's first, mistaken start. It's a read-only dumper for the Action Replay DSi's protocol, kept for the record.

Set `DUO_FW` and `DUO_FW_C` to point at your files if they live elsewhere.
