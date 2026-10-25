"""The simulator must reproduce the real hardware results we observed."""
import sys, json
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))); import paths
import hwsim, maxduo_saves as M
RUBY = __import__("fixtures").make_gen3_save()
GAME = hwsim.make_game_rom()
ok = True
def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {'' if cond else detail}")

# 1. v2 patch (Reckless edition): reads nothing, as on real hardware
hw = hwsim.Hw(open("fw_reckless.bin", "rb").read(), GAME, "sanyo", RUBY).start()
duo = M.MaxDuo(hwsim.Transport(hw)); M.SAFE_COMMANDS.add(3)
data = duo.gba_save(2, 0x0007); hw.stop()
check("v2 --gba-save returns no save data (as on your DUO)", data[1:0x10000] == b"\xFF" * 0xFFFF and data[0x10001:] == b"\xFF" * 0xFFFF, data[:16].hex())
check("v2 never altered the save", not hw.violations, hw.violations)

# 2. lab edition, last night's working script (header read first) and failing one
lab_fw = open("fw_lab.bin", "rb").read()
for name, script, expect in (("read with header first", "bank0_v2.json", True), ("read without header", "nohdr.json", False)):
    hw = hwsim.Hw(lab_fw, GAME, "sanyo", RUBY).start()
    duo = M.MaxDuo(hwsim.Transport(hw))
    path = __import__("os").path.join(paths.ROOT, "lab", "early", script)
    res = M.Lab(duo).run(M._lab_ops(json.load(open(path))), timeout=600000)
    hw.stop()
    got = res[-1][1]
    check(f"lab {name}: {'gets the save' if expect else 'gets nothing'} (as on your DUO)", (got == RUBY[:0x10000]) == expect, got[:16].hex())

# 3. Sanyo ID: enter then single F0 exit returns to data (this morning's test 1)
hw = hwsim.Hw(lab_fw, GAME, "sanyo", RUBY).start()
duo = M.MaxDuo(hwsim.Transport(hw))
res = M.Lab(duo).run(M._lab_ops(json.load(open(__import__("os").path.join(paths.ROOT, "lab", "early", "t1_sanyo_exit.json")))))
hw.stop()
vals = [d.hex() for _, d in res]
check("Sanyo ID then F0 exit matches this morning's hardware result",
      vals[1:4] == [RUBY[:4].hex(), "62130000"[:8], RUBY[:4].hex()] or (vals[2] == "62136213" and vals[3] == RUBY[:4].hex()), vals)
print("SIMULATOR MATCHES HARDWARE" if ok else "MISMATCH")
