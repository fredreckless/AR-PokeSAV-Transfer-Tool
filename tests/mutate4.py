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
FW42_MUTANTS = {
    "run skips the program's CRC check": ("    ldr  r1, [r6, #8]\n    cmp  r0, r1\n    bne  run_bad\n", "    ldr  r1, [r6, #8]\n", "refused"),
    "run never replies before starting the program": ("    movs r0, #{CMD_RUN:#x}\n    ldr  r1, lit_ack\n    bl   {v4.AT['send_sbw']:#x}\n", "", "adapter test: answers"),
    "run accepts an entry point outside the code": ("    cmp  r1, r4\n    bhs  run_bad\n", "", "refused"),
}
PROG_MUTANTS = {      # firmware/programs/adapter.c: rebuilt and embedded in a copy of the tool
    "adapter skips the ready handshake": ("    if (!wait_si(1, HANDSHAKE_LINES)) {\n#ifndef PROBE\n        judge_flick(0);", "    if (0) {\n#ifndef PROBE\n        judge_flick(0);", "wireless adapter"),
    "adapter acknowledges without the 0x80": ("((cmd + 0x80) & 0xFF)", "(cmd & 0xFF)", "wireless adapter: Emerald"),
    "adapter never takes its turn after a wait": ("frames * LINES_PER_FRAME)\n            break;", "frames * LINES_PER_FRAME * 100000)\n            break;", "wireless adapter: Emerald"),
    "adapter rests SO low before a command (no first-bit fix)": ("    SIODATA32 = IDLE_WORD;\n    SIOCNT = CNT_32BIT | CNT_START | CNT_SO_HIGH;\n}", "    SIODATA32 = IDLE_WORD;\n    SIOCNT = CNT_32BIT | CNT_START;\n}", "first-bit behaviour"),
    "adapter flicks again without waiting out the word": ("#define FLICK_QUIET 40", "#define FLICK_QUIET 1", "first-bit behaviour"),
    "adapter's flick never gets wider": ("            flick_w++;                                        /* not noticed: wider */", "            ;", "tunes its own width"),
    "adapter's flick never gets narrower": ("            flick_w = last_flick ? last_flick - 1 : 0;       /* clocked but refused: narrower */", "            ;", "tunes its own width"),
    "distributor's broadcast checksum is wrong": ("    b[15] = ~sum;", "    b[15] = sum;", "distributor"),
    "distributor doesn't say it has a card": ("               | 1 << 5                  /* hasCard */", "               | 0 << 5                  /* hasCard */", "distributor"),
    "host never acknowledges the game's chunks": ("    if (ni.state & NI_BUSY) {\n        u32 f", "    if (0) {\n        u32 f", "NI channel"),
    "host acknowledges with the wrong window number": ("ni.n[ni.phase] << 11 | 1u << 18", "((ni.n[ni.phase] + 1) & 3) << 11 | 1u << 18", "NI channel"),
    "host never moves on after the game acknowledges": ("        nis.acked[phase] = 1;", "        ;", "may join"),
    "host sends the wrong join status": ("static const u8 join_group_ok = 5;", "static const u8 join_group_ok = 6;", "may join"),
    "parent doesn't echo the game's commands": ("        memcpy(out + bytes + 3 + 14, rx_cmd, 14);                    /* row 1: the game's, echoed */\n", "", "Mystery Gift delivers"),
    "parent gives the game the wrong player number": ("tx_cmd[2] = 0x0001;", "tx_cmd[2] = 0x0002;", "Mystery Gift delivers"),
    "parent ignores the game's standby": ("    case CMD_READY_EXIT: standby_reply = 1;", "    case CMD_READY_EXIT: standby_reply = 0;", "Mystery Gift delivers"),
    "parent never agrees to close the link": ("    case CMD_READY_CLOSE: close_reply = 1;", "    case CMD_READY_CLOSE: close_reply = 0;", "nothing to send"),
    "parent keeps the game's sequence number": ("            child_uni[0] &= 0x1F;", "            ;", "Mystery Gift delivers"),
    "parent swaps the bytes in block chunks": ("tx_cmd[1 + j] = lo | hi << 8;", "tx_cmd[1 + j] = hi | lo << 8;", "Mystery Gift delivers"),
    "server's header CRC is off by one bit": ("            crc = ~crc;", "            crc = ~crc ^ 1;", "Mystery Gift delivers"),
    "server tells the game to save the card as Wonder News": ("CLI_SAVE_CARD = 10,", "CLI_SAVE_CARD = 9,", "Mystery Gift delivers"),
    "host moves on even when the game missed a packet": ("        if (hf_uni && !uni_acked && n_resent < 0xFFFF)", "        if (0)", "packets lost"),
    "adapter has no watchdog mid-command": ("        if (phase != PH_COMMAND && phase != PH_ID && now() - last_word_at > WATCHDOG_LINES) {", "        if (0) {", "wireless adapter: the game gives up"),
}

PC_MUTANTS = {
    "fast 32-bit checksum on the save": ('("r8", SAVE_BASE, 0x10000), ("crc", SAVE_BASE, 0x10000), ("w16", MAP_REG, 0x0120)]', '("r8", SAVE_BASE, 0x10000), ("crct", SAVE_BASE, 0x10000), ("w16", MAP_REG, 0x0120)]', "8-bit"),
    "no stale-packet clearing": ("        cleared = self.drain()\n", "        cleared = 0\n", "connect"),
    "partial upload drops interpreter bytes": ("            self._upload_fast(LAB_STAGE, prog[:16])\n", "            self._upload_fast(LAB_STAGE, prog[:12])\n", "resident"),
    "wrong logo constant": ("0x01000000: 0x62B5CBC1", "0x01000000: 0x62B5CBC0", "logo constants"),
    "duplicate backups stored": ("            return p, \"identical to an earlier backup - not stored again\", summary, previous\n", "            pass\n", "backups"),
    "trainer change not warned": ("    if previous and (previous[\"trainer\"], previous[\"id\"]) != (summary[\"trainer\"], summary[\"id\"]):", "    if False:", "backups"),
    "DeleteFile allowed": ("    0x1C: \"DeleteFile", "    0x1D: \"DeleteFile", "refusals"),
    "adapter test never stops the program": ("    finally:\n        word = program_poll(duo, 1)\n        st = program_stop(duo)\n", "    finally:\n        word = st = 0\n", "adapter test"),
    "adapter program answers with the wrong ID": ("ADAPTER_IMAGE", "ADAPTER_IMAGE_WRONG_ID", "adapter test: answers"),
    "Wonder Card's RAM script read from the wrong place": ("    script = data[4 + size + 0x50 + 4:4 + size + 0x50 + 4 + 1000]", "    script = data[4 + size + 0x50:4 + size + 0x50 + 1000]", "Mystery Gift delivers"),
    "known game table ignored": ("    known = KNOWN_SAVE_GAMES.get(info[\"code\"][:3])\n    if forced:", "    known = None\n    if forced:", "known games"),
}


import threading
BUILD_LOCK = threading.Lock()


def run(kind, name):
    import paths, tempfile
    work = tempfile.mkdtemp()
    if kind == "fw42":
        old, new, pick = FW42_MUTANTS[name]
        src = open(os.path.join(paths.FIRMWARE, "build_v42.py")).read()
        assert src.count(old) == 1, name
        open(os.path.join(work, "build_v42_mut.py"), "w").write(src.replace(old, new))
        shutil.copy(paths.build_file("fw.bin"), work)
        r = subprocess.run([sys.executable, "-c", "import build_v42_mut as B; B.build('fw_mut.bin')"], cwd=work,
                           capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=paths.FIRMWARE))
        assert r.returncode == 0, r.stderr[-800:]
        env = dict(os.environ, V42_FW=os.path.join(work, "fw_mut.bin"))
    elif kind == "fw":
        old, new, pick = FW_MUTANTS[name]
        src = open(os.path.join(paths.FIRMWARE, "build_v4.py")).read()
        assert src.count(old) == 1, name
        open(os.path.join(work, "build_v4_mut.py"), "w").write(src.replace(old, new))
        shutil.copy(paths.build_file("fw.bin"), work)
        r = subprocess.run([sys.executable, "-c", "import build_v4_mut as B; B.build('fw_mut.bin')"], cwd=work,
                           capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=paths.FIRMWARE))
        assert r.returncode == 0, r.stderr[-800:]
        env = dict(os.environ, V3_FW=os.path.join(work, "fw_mut.bin"))
    elif kind == "prog":
        old, new, pick = PROG_MUTANTS[name]
        sys.path.insert(0, paths.FIRMWARE)
        import programs
        src_path = os.path.join(paths.FIRMWARE, "programs", "adapter.c")
        good_src = open(src_path).read()
        assert good_src.count(old) == 1, name
        with BUILD_LOCK:
            good = programs.build("adapter").hex()
        os.makedirs(os.path.join(work, "programs"))
        open(os.path.join(work, "programs", "adapter.c"), "w").write(good_src.replace(old, new))
        shutil.copy(os.path.join(paths.FIRMWARE, "programs", "link.ld"), os.path.join(work, "programs"))
        with BUILD_LOCK:
            saved = programs._HERE
            programs._HERE = work
            try:
                bad = programs.build("adapter").hex()
            finally:
                programs._HERE = saved
        assert good != bad
        tool = open(os.path.join(paths.ROOT, "maxduo_saves.py")).read()
        assert tool.count(good) == 1
        open(os.path.join(work, "maxduo_saves.py"), "w").write(tool.replace(good, bad))
        env = dict(os.environ, MAXDUO_TOOL_DIR=work)
    else:
        old, new, pick = PC_MUTANTS[name]
        src = open(os.path.join(paths.ROOT, "maxduo_saves.py")).read()
        if old == "ADAPTER_IMAGE":                     # rebuild the embedded program with a wrong ID (valid CRC)
            sys.path.insert(0, paths.FIRMWARE)
            import programs
            with BUILD_LOCK:
                good = programs.build("adapter_detect").hex()
                programs.PROGRAMS["adapter_detect"] = programs.ADAPTER_DETECT.replace(f"{programs.RFU_ID:#x}", "0x8002")
                try:
                    bad = programs.build("adapter_detect").hex()
                finally:
                    programs.PROGRAMS["adapter_detect"] = programs.ADAPTER_DETECT
            assert good != bad
            old, new = good, bad
        assert src.count(old) == 1, name
        mutated = src.replace(old, new)
        if "crct" in new:   # let the bug through the tool's own guard too, so only the tests can catch it
            mutated = mutated.replace('if name == "crct" and 0x0E000000 <= addr < 0x0F000000:', 'if False:')
        open(os.path.join(work, "maxduo_saves.py"), "w").write(mutated)
        env = dict(os.environ, MAXDUO_TOOL_DIR=work)
    for attempt in range(3):               # the emulator itself can segfault under load: run again
        r = subprocess.run([sys.executable, "test_v4.py", pick], cwd=paths.HERE, capture_output=True, text=True, env=env, timeout=1800)
        finished = any(" passed, " in l and " failed" in l for l in r.stdout.splitlines())
        if r.returncode >= 0 and finished:
            break                          # (no summary line means the run died part-way: try again)
    summary = [l for l in r.stdout.splitlines() if l.startswith("[") or "passed" in l]
    caught = any(l.startswith("[FAIL]") for l in summary)
    print(f"{'CAUGHT' if caught else 'MISSED'}  {kind}: {name}  ->  {summary[-1] if summary else r.stderr[-200:]}", flush=True)
    shutil.rmtree(work, ignore_errors=True)
    return caught


if __name__ == "__main__":
    if len(sys.argv) == 3 and not sys.argv[2].startswith("--"):
        ok = run(sys.argv[1], sys.argv[2])
    else:
        import concurrent.futures as cf
        jobs = next((int(a.split("=", 1)[1]) for a in sys.argv[1:] if a.startswith("--jobs=")),
                    (os.cpu_count() or 2) * 2)
        todo = ([("fw", n) for n in FW_MUTANTS] + [("fw42", n) for n in FW42_MUTANTS]
                + [("prog", n) for n in PROG_MUTANTS] + [("pc", n) for n in PC_MUTANTS])
        with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
            ok = all(list(pool.map(lambda kn: run(*kn), todo)))
        print("every planted bug was caught" if ok else "SOME PLANTED BUGS WERE MISSED")
    sys.exit(0 if ok else 1)
