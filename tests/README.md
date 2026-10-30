# Simulator and tests

- **`hwsim.py`** runs Datel's real firmware, as built by `firmware/build_firmware.py`, in the Unicorn ARM emulator. Around it is a model of everything measured on the real DUO:
  - the strictly alternating USB link, with optional delay and corruption;
  - the save chip that sleeps until the ROM is read;
  - Flash, SRAM and EEPROM cartridges;
  - the 8-bit save bus;
  - the Actel header overlay and the DUO's mirrored flash;
  - power-on and restart through Datel's real start-up code.
  - the GBA link cable's four-player mode, with optional adversarial timing (`race_window`): a transfer finishes at the very moment the DUO reads SIOCNT, so start races show up every time rather than by luck.

  Any write that could change a save is recorded as a violation and fails the test.
- **`check.py`** runs the tests in tiers: `quick` (a fast spread across every area, about a minute), `changed` (the tests and planted bugs for the files changed since `origin/main`), `full` (everything, plus the planted bugs for what changed) and `what` (just say what `changed` would run). The tables at its top say which tests and planted bugs each file needs.
- **`test_v4.py`** is the test suite, 106 tests. Run `python run_each.py` to put each test in its own process: the emulator library can crash after very many simulated DUOs in one process. Without the firmware built, the tests that need it are skipped.
- **`mutate4.py`** plants known bugs one at a time (in the firmware builds, the GBA programs and the PC tools) and checks a test catches each one. `python mutate4.py [KIND ...]` runs all of them or the kinds named (`fw`, `fw42`, `prog`, `ereader`, `pc`).
- **`emu_*.py`** are the earlier emulator tests (v1, v2, the lab, and firmware updates), still run by the suite.
- **`fixtures.py`** builds synthetic Pokémon Gen 3 saves, so no real save is needed. **`ecard_fixtures.py`** builds made-up e-Cards (a program, vpk0-compressed, cut into Reed-Solomon-coded strips), the reverse of the tool's card reader. **`logo_correction.py`** derives the checksum corrections for the DUO's header overlay.
- **`history/`** holds the v3 / v3.1 suites, kept for the record.

Build first with `python firmware/build_firmware.py PATH_TO_DATEL_1.06_UPDATE --all`.
