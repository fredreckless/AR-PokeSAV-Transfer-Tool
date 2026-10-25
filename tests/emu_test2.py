"""Tests for patch version 2 (selectable mapping + switch-back guard)."""
import sys, threading, random, time
import paths
import emu_test as E, maxduo_saves as M
from unicorn.arm_const import UC_ARM_REG_PC
E.FW = open(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".bin") else paths.build_file("fw_patched2.bin"), "rb").read()

def run(name, fn, faulty=False):
    rng = random.Random(2)
    save = bytes(rng.getrandbits(8) for _ in range(0x20000))
    sim = E.Sim(save); sim.ignore_restore = faulty
    t = threading.Thread(target=sim.run, daemon=True); t.start()
    duo = M.MaxDuo(E.EmuTransport(sim)); M.SAFE_COMMANDS.add(0x03)
    try:
        res = fn(duo, sim, save)
    except Exception as e:
        res = f"{type(e).__name__}: {e}"
    if not faulty:
        sim.done.set(); t.join(10)
    ok = res is True and not sim.bad
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" -> {res} {sim.bad}"))
    return ok

def default_map(duo, sim, save):
    assert duo.download(0x03, 2) == save
    assert sim.map_writes == [0x0007, 0x0120], sim.map_writes
    assert not sim.mapped_game and sim.bank == 0
    return True

def chosen_map(duo, sim, save):
    assert duo.download(0x03, 2 | (0x0001 << 8)) == save
    assert sim.map_writes == [0x0001, 0x0120], sim.map_writes
    return True

def old_map_still_possible(duo, sim, save):
    assert duo.download(0x03, 2 | (0x0101 << 8)) == save
    assert sim.map_writes == [0x0101, 0x0120]
    return True

def guard_holds(duo, sim, save):
    # hardware refuses to switch back: firmware must stop in RAM, never run game code
    import queue
    duo.t.sim.h2d.put(b"CBW\x03" + (2 | 7 << 8).to_bytes(4, "little"))
    time.sleep(8)
    pc = sim.mu.reg_read(UC_ARM_REG_PC) if False else None
    assert sim.d2h.empty(), "device replied despite failed switch-back"
    assert not sim.bad, sim.bad
    sim.mu.emu_stop()
    return True

ok = all([
    run("default mapping is 0x0007, save read, DUO restored", default_map),
    run("PC can choose Datel's plain launch value 0x0001", chosen_map),
    run("version 1's 0x0101 still selectable", old_map_still_possible),
    run("guard: faulty switch-back -> waits in RAM, never runs game code", guard_holds, faulty=True),
])
print("ALL PASSED" if ok else "SOME FAILED")
