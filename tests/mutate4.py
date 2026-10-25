"""Planted bugs: each must make at least one test fail (a check on the tests themselves).
    python mutate4.py                 run them all (slow: each runs part of the suite)
    python mutate4.py fw  "<name>"    one firmware mutant, rebuilt from build_v4.py
    python mutate4.py pc  "<name>"    one tool mutant"""
import os, re, shutil, subprocess, sys
FW_MUTANTS = {
    "restart doesn't wait for the hello (v4's race)": ("    ldr  r7, lit_wait\nrs_wait:\n    bl   {AVAIL:#x}\n    cmp  r0, #0\n    bne  rs_go\n    bl   {IDLE:#x}\n    subs r7, #1\n    bne  rs_wait\nrs_go:\n", "", "ten in a row"),
    "restart uses hard reset": ("    svc  #0\n", "    svc  #0x26\n", "restart"),
    "restart resets before replying": ("    movs r0, #{CMD_RESTART:#x}\n    ldr  r1, lit_ack\n    bl   {a['send_sbw']:#x}\n", "", "restart"),
    "restart with interrupts on": ("    ldr  r0, lit_ime\n    movs r1, #0\n    strh r1, [r0]\n", "    movs r1, #0\n", "restart"),
}
PC_MUTANTS = {
    "fast 32-bit checksum on the save": ('("r8", SAVE_BASE, 0x10000), ("crc", SAVE_BASE, 0x10000), ("w16", MAP_REG, 0x0120)]', '("r8", SAVE_BASE, 0x10000), ("crct", SAVE_BASE, 0x10000), ("w16", MAP_REG, 0x0120)]', "8-bit"),
    "no stale-packet clearing": ("        cleared = self.drain()\n", "        cleared = 0\n", "connect"),
    "partial upload drops interpreter bytes": ("            self._upload_fast(LAB_STAGE, prog[:16])\n", "            self._upload_fast(LAB_STAGE, prog[:12])\n", "resident"),
    "wrong logo constant": ("0x01000000: 0x62B5CBC1", "0x01000000: 0x62B5CBC0", "logo constants"),
    "duplicate backups stored": ("            return p, \"identical to an earlier backup - not stored again\", summary, previous\n", "            pass\n", "backups"),
    "trainer change not warned": ("    if previous and (previous[\"trainer\"], previous[\"id\"]) != (summary[\"trainer\"], summary[\"id\"]):", "    if False:", "backups"),
    "DeleteFile allowed": ("    0x1C: \"DeleteFile", "    0x1D: \"DeleteFile", "refusals"),
    "known game table ignored": ("    known = KNOWN_SAVE_GAMES.get(info[\"code\"][:3])\n    if forced:", "    known = None\n    if forced:", "known games"),
}


def run(kind, name):
    import paths, tempfile
    work = tempfile.mkdtemp()
    if kind == "fw":
        old, new, pick = FW_MUTANTS[name]
        src = open(os.path.join(paths.FIRMWARE, "build_v4.py")).read()
        assert src.count(old) == 1, name
        open(os.path.join(work, "build_v4_mut.py"), "w").write(src.replace(old, new))
        shutil.copy(paths.build_file("fw.bin"), work)
        r = subprocess.run([sys.executable, "-c", "import build_v4_mut as B; B.build('fw_mut.bin')"], cwd=work,
                           capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=paths.FIRMWARE))
        assert r.returncode == 0, r.stderr[-800:]
        env = dict(os.environ, V3_FW=os.path.join(work, "fw_mut.bin"))
    else:
        old, new, pick = PC_MUTANTS[name]
        src = open(os.path.join(paths.ROOT, "maxduo_saves.py")).read()
        assert src.count(old) == 1, name
        mutated = src.replace(old, new)
        if "crct" in new:   # let the bug through the tool's own guard too, so only the tests can catch it
            mutated = mutated.replace('if name == "crct" and 0x0E000000 <= addr < 0x0F000000:', 'if False:')
        open(os.path.join(work, "maxduo_saves.py"), "w").write(mutated)
        env = dict(os.environ, MAXDUO_TOOL_DIR=work)
    r = subprocess.run([sys.executable, "test_v4.py", pick], cwd=paths.HERE, capture_output=True, text=True, env=env, timeout=1800)
    summary = [l for l in r.stdout.splitlines() if l.startswith("[") or "passed" in l]
    caught = any(l.startswith("[FAIL]") for l in summary)
    print(f"{'CAUGHT' if caught else 'MISSED'}  {kind}: {name}  ->  {summary[-1] if summary else r.stderr[-200:]}", flush=True)
    shutil.rmtree(work, ignore_errors=True)
    return caught


if __name__ == "__main__":
    if len(sys.argv) == 3:
        ok = run(sys.argv[1], sys.argv[2])
    else:
        ok = all([run("fw", n) for n in FW_MUTANTS] + [run("pc", n) for n in PC_MUTANTS])
        print("every planted bug was caught" if ok else "SOME PLANTED BUGS WERE MISSED")
    sys.exit(0 if ok else 1)
