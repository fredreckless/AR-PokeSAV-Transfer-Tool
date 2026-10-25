# Archived tests (v3 and v3.1)

These are the test scripts from the v3 / v3.1 stage of the project, kept as they were for the
record (paths adapted to this repository, and the real save they used replaced by a synthetic
one). They are not maintained: `tests/test_v4.py` is the current suite and covers everything
these did.

- `test_v3.py`, `mutate31.py` - the v3.1 suite and its planted bugs
- `hwsim_v31.py` - the simulator as it was for v3.1
- `repro_old.py`, `smoke_v3.py`, `trace31.py`, `check_both.py`, `test_sim_fidelity.py` -
  one-off checks: reproducing the v3 stall, comparing simulator and hardware results
- `maxduo_saves_v3_original.py`, `maxduo_saves_v3.1.py` - earlier versions of the PC tool
  (the first is used by `test_v4.py` to replay the original v3 failure)
