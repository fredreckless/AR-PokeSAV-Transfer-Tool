# Simulator and tests

- **`hwsim.py`** runs Datel's real firmware, as built by `firmware/build_firmware.py`, in the Unicorn ARM emulator. Around it is a model of everything measured on the real DUO:
  - the strictly alternating USB link, with optional delay and corruption;
  - the save chip that sleeps until the ROM is read;
  - Flash, SRAM and EEPROM cartridges;
  - the 8-bit save bus;
  - the Actel header overlay and the DUO's mirrored flash;
  - power-on and restart through Datel's real start-up code.

  Any write that could change a save is recorded as a violation and fails the test.
- **`test_v4.py`** is the test suite, about 60 tests. Run `python run_each.py` to put each test in its own process: the emulator library can crash after very many simulated DUOs in one process.
- **`mutate4.py`** plants 12 known bugs, one at a time, from v4's restart race to the 32-bit save checksum, and checks a test catches each one.
- **`emu_*.py`** are the earlier emulator tests (v1, v2, the lab, and firmware updates), still run by the suite.
- **`fixtures.py`** builds synthetic Pokémon Gen 3 saves, so no real save is needed. **`logo_correction.py`** derives the checksum corrections for the DUO's header overlay.
- **`history/`** holds the v3 / v3.1 suites, kept for the record.

Build first with `python firmware/build_firmware.py PATH_TO_DATEL_1.06_UPDATE --all`.
