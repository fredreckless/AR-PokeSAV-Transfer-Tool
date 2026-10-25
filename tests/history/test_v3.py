"""
Test suite for Reckless v3.1 firmware + PC tool, run against hwsim (which
reproduces every behaviour observed on the real DUO).

    python3 test_v3.py            run everything
    python3 test_v3.py stream     run tests whose name contains "stream"
"""
import gc, io, contextlib, glob, json, os, random, struct, subprocess, sys, tempfile, threading, time, traceback, types, zlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))); import paths
import hwsim, thumbcheck, maxduo_saves as M

FW = open(os.environ.get("V3_FW") or paths.build_file("fw_v3.bin"), "rb").read()
OFFICIAL = paths.read_build("fw.bin")
LABFW = paths.read_build("fw_lab.bin")
OLD_V3 = paths.read_build("fw_v3_original.bin")      # what your DUO runs now
RUBY = __import__("fixtures").make_gen3_save()
GAME = hwsim.make_game_rom()
SAVE = 0x0E000000
RESULTS = []
LIVE = []
M.SHORT_TIMEOUT = 800        # keep the real ratio: DUO waits ~15 s, PC ~5 s (scaled here)


def device(save_type="sanyo", save=None, game=None, faults=None, fw=FW):
    hw = hwsim.Hw(fw, game or GAME, save_type, save if save is not None else RUBY, faults).start()
    LIVE.append(hw)
    duo = M.MaxDuo(hwsim.Transport(hw))
    duo.hello()
    return hw, duo


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


def gba_save(duo, **kw):
    out = tempfile.mkdtemp()
    args = types.SimpleNamespace(out=out, map=kw.get("map"), save_type=kw.get("save_type"), slow=kw.get("slow", False))
    (path, data, info), text = quiet(M.gba_save_v3, duo, args)
    return path, data, info, text, out


def clean(hw):
    assert not hw.bad, f"simulator problems: {hw.bad}"
    assert not hw.violations, f"save-changing writes: {hw.violations}"
    assert hw.mapped is None, "left the game mapped"
    assert not hw.link_errors, f"link out of step: {hw.link_errors}"
    assert bytes(hw.save) == hw.original_save, "save contents changed"


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


def lab_read_ops(n, start=SAVE, wake=True, mapping=0x0101):
    ops = [("w16", M.MAP_REG, mapping), ("delay", 0, 0x100)]
    if wake:
        ops.append(("r16", 0x08000000, 2))
    return ops + [("r8", start, n), ("w16", M.MAP_REG, 0x0120)]


# ===================================================================== build
@test("build: v3 rebuilds byte-for-byte from source (reproducible)")
def _():
    out = subprocess.run([sys.executable, "build_v3.py"], cwd=paths.BUILD, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert open(paths.BUILD + "/fw_v3.bin", "rb").read() == FW


@test("build: outside Datel's padding, only command slots and text pointers change")
def _():
    allowed = {0x080074A8, 0x080074BC, 0x080074C4, 0x08007518,                           # cmds 2,7,9,1E
               0x080074AC, 0x080074B0, 0x080074B4, 0x080074B8,                           # cmds 3,4,5,6 (lab/v2)
               0x080073A0, 0x080073A4,                                                   # redirected call
               0x08000FA0, 0x08000FA4, 0x0800B238}                                        # colours, version label
    allowed |= {t + 4 * m for t in (0x0802BB70, 0x0802BE94, 0x0802C0B8, 0x0802C2DC) for m in (0, 36, 60)}
    colour_table = range(0x0800EADC, 0x0800EADC + 14 * 12)
    bad = sorted({hex(0x08000000 + i & ~3) for i in range(len(OFFICIAL)) if FW[i] != OFFICIAL[i]
                  and not 0x1E7C2 <= i < 0x1FD90 and (0x08000000 + i & ~3) not in allowed
                  and 0x08000000 + i not in colour_table})
    assert not bad, f"unexpected changes at {bad}"


@test("build: Datel's own command handlers are untouched")
def _():
    ours = {2, 3, 4, 5, 6, 7, 9, 0x1E}
    for c in range(2, 0x28):
        o = 0x74A8 + 4 * (c - 2)
        if c not in ours:
            assert FW[o:o + 4] == OFFICIAL[o:o + 4], f"command {c:#x} changed"


@test("build: every patch routine (v1, v2, lab, v3) is ARM7TDMI Thumb-1 only")
def _():
    import build_patch as v1, build_patch2 as v2, build_lab as L, build_v3 as V
    pieces = [("v2 wrapper", v2.ASM_PREP2.replace("RAMFN_ADDR", "0").replace("RAMFN_LEN", "0"), v1.PREP_AT),
              ("stub", v1.ASM_STUB, v1.STUB_AT), ("lab tail", L.ASM_LAB_TAIL, L.TAIL_AT),
              ("lab setaddr", L.ASM_SETADDR, L.SETADDR_AT), ("lab poke", L.ASM_POKE, L.POKE_AT),
              ("lab exec", L.ASM_EXEC, L.EXEC_AT),
              ("lab wrapper", L.ASM_LAB_PREP.replace("TRAMP_ADDR", "0").replace("TRAMP_LEN", "0"), L.LAB_PREP_AT),
              ("lab trampoline", L.ASM_LAB_TRAMP.replace("PROBE_AT", "0").replace("PROBE", "0"), L.LAB_TRAMP_AT),
              ("interpreter", L.ASM_LAB_INTERP, L.STAGE_LO + 12)]
    _, S = V.strings_blob()
    pieces += [(n, src, V.AT[n]) for n, src in V.sources(S).items()]
    for name, src, at in pieces:
        thumbcheck.checked_assemble(v1.assemble, src, at, name)
    # and the tool's embedded interpreter is exactly the checked build
    assert M.LAB_INTERP == L.interp_code()


@test("build: update file is self-consistent and recognised by the tool")
def _():
    crc, payload = M.check_update_file(paths.BUILD + "/duo_1.06_reckless_v3.1.bin")
    assert payload == FW and "v3.1" in M.KNOWN_FIRMWARE.get(crc, "")
    crc, _ = M.check_update_file(paths.BUILD + "/installed_v3.bin")
    assert "use v3.1" in M.KNOWN_FIRMWARE.get(crc, "")


@test("update: installing v3.1 over the official, the lab edition AND your installed v3, flash simulated")
def _():
    saved, sys.argv = sys.argv, sys.argv[:1]
    try:
        import emu_update_test as U
    finally:
        sys.argv = saved
    for current in (OFFICIAL, LABFW, OLD_V3):
        U.OFFICIAL = current
        ok, text = quiet(U.main, paths.BUILD + "/duo_1.06_reckless_v3.1.bin")
        assert ok, text


# ============================================================ basic behaviour
@test("info: v3.1 identifies itself; your installed v3 and the lab edition are told apart")
def _():
    for fw, kind, fast in ((FW, "v3.1", True), (OLD_V3, "v3", False), (LABFW, None, False)):
        hw, duo = device(fw=fw)
        assert (duo.reckless(), duo.is_v3()) == (kind, fast), (kind, duo.reckless())
        hw.stop(); clean(hw)


@test("legacy --gba-save (cmd 0x03) now works too, with all three mappings")
def _():
    for m in (0x0101, 0x0007, 0x0001):
        hw, duo = device()
        data = duo.gba_save(2, m)
        hw.stop(); clean(hw)
        assert data == RUBY, f"mapping {m:#x}"
        assert hw.bank == 0


@test("legacy lab path (cmds 4/5/6) still works on v3")
def _():
    hw, duo = device()
    lab = M.Lab(duo); lab.v3 = False
    res = lab.run(lab_read_ops(64))
    hw.stop(); clean(hw)
    assert res[-1][1] == RUBY[:64]


@test("full --gba-save of your Ruby save: identical file, no writes beyond bank switches")
def _():
    hw, duo = device()
    t0 = hw.sent_packets
    path, data, info, text, _ = gba_save(duo)
    hw.stop(); clean(hw)
    assert data == RUBY and zlib.crc32(open(path, "rb").read()) == 0x612DDAF0
    assert info["title"] == "POKEMON RUBY" and "FLASH1M_V103" in info["tags"]
    assert hw.save_writes == 8, f"{hw.save_writes} chip writes (expected 2 bank switches = 8)"


@test("screen log: expected messages, in order, all short enough for the screen")
def _():
    hw, duo = device()
    gba_save(duo)
    hw.stop()
    log = hw.log
    want = ["PC connected: RECKLESS V3.1", "GBA save: checking game", "Game: POKEMON RUBY", "Reading bank 0 (64 KB)",
            "Sent 64 KB", "Reading bank 1 (64 KB)", "Save copied to PC", "128 KB, CRC 612DDAF0"]
    pos = 0
    for w in want:
        assert w in log[pos:], f"missing or out of order: {w!r}\n{log}"
        pos = log.index(w, pos) + 1
    assert all(len(l) <= 27 for l in log), [l for l in log if len(l) > 27]


@test("speed: v3.1 needs far fewer USB transfers than v2 for the same save (both directions counted)")
def _():
    hw, duo = device()
    n0 = hw.sent_packets + hw.pc_packets
    gba_save(duo)
    v31 = hw.sent_packets + hw.pc_packets - n0
    hw.stop(); clean(hw)
    v2 = 2 * (0x20000 // 8) + 8                 # Datel-style: every 8 bytes = 1 packet + 1 ack
    print(f"      USB transfers: v2 {v2}, v3.1 {v31} -> about {v2 * 0.0087:.0f} s vs {v31 * 0.0087:.0f} s on your DUO")
    assert v31 * 8 < v2, v31


# ================================================================== streaming
PATTERNS = {
    "random": bytes(random.Random(1).getrandbits(8) for _ in range(0x8000)),
    "all FF": b"\xFF" * 0x8000,
    "all zero": bytes(0x8000),
    "all escape byte": bytes([M.RLE_ESC]) * 0x8000,
    "escape alternating": bytes([M.RLE_ESC, 0]) * 0x4000,
    "runs 255/256/257": (b"\x11" * 255 + b"\x22" * 256 + b"\x33" * 257 + b"\x44") * 42 + b"\x55" * (0x8000 - 769 * 42),
    "ascending": bytes(i & 0xFF for i in range(0x8000)),
    "Ruby first 32 KB": RUBY[:0x8000],
    "short runs of 3": (b"\x01\x01\x01\x02\x02\x02") * 5461 + b"\x00\x00",
}


@test("stream: 9 data patterns round-trip exactly; GBA compression == reference encoder")
def _():
    for name, pat in PATTERNS.items():
        assert len(pat) == 0x8000, name
        hw, duo = device("sram", save=pat, game=hwsim.make_game_rom(tag=b"SRAM_V113"))
        res = M.Lab(duo).run(lab_read_ops(0x8000))
        hw.stop(); clean(hw)
        assert res[-1][1] == pat, f"{name}: data differs"
        out = b"\x2e\x00" + pat + b"DONE"                  # what the GBA streamed
        for (b, raw, dlen), payload in zip(duo.stats["block_info"], duo.stats["payloads"]):
            ref = M.rle3_encode(out[b * 4096:(b + 1) * 4096])
            assert raw == (ref is None), f"{name} block {b}: raw={raw}, reference says {ref is None}"
            if not raw:
                assert payload == ref, f"{name} block {b}: GBA's compressed bytes differ from the reference"


@test("stream: odd output sizes (1, 4095, 4096, 4097, 5000, 65536 bytes)")
def _():
    for n in (1, 4095, 4096, 4097, 5000, 0x10000):
        hw, duo = device()
        res = M.Lab(duo).run(lab_read_ops(n))
        hw.stop(); clean(hw)
        assert res[-1][1] == RUBY[:n], n


@test("stream: random bit corruption is caught by block CRCs and re-sent")
def _():
    hw, duo = device(faults={"corrupt_p": 0.004, "seed": 11})
    res = M.Lab(duo).run(lab_read_ops(0x10000))
    hw.stop(); clean(hw)
    assert res[-1][1] == RUBY[:0x10000] and duo.stats["retries"] > 0, duo.stats["retries"]


@test("stream: a corrupted block header and a corrupted data packet are each NAKed and re-sent")
def _():
    hw, duo = device(faults={"corrupt_at": {1, 30, 200}})
    res = M.Lab(duo).run(lab_read_ops(0x10000))
    hw.stop(); clean(hw)
    assert res[-1][1] == RUBY[:0x10000] and duo.stats["retries"] >= 2, duo.stats["retries"]


@test("stream: one corrupted packet at EVERY position of a stream (header, data, last) always recovers")
def _():
    ops = lab_read_ops(0x3000)
    hw, duo = device()
    M.Lab(duo).run(ops)
    n = duo.stats["packets"]
    hw.stop(); clean(hw)
    harmless = []
    hw, duo = device()
    lab = M.Lab(duo)
    for pos in range(1, n + 1):                  # one DUO, a fresh fault position each run
        hw.faults["corrupt_at"] = {pos}
        hw.rng.seed(pos)
        res = lab.run(ops)
        assert res[-1][1] == RUBY[:0x3000], pos
        assert not hw.bad and not hw.link_errors, (pos, hw.bad, hw.link_errors)
        if not duo.stats["retries"]:
            harmless.append(pos)
    hw.faults.clear()
    hw.stop(); clean(hw)
    assert len(harmless) <= 4, harmless          # only flips in a block's discarded padding need no resend
    print(f"      all {n} positions gave exact data ({len(harmless)} flips landed in padding: {harmless})")


@test("stream: random corruption (1 packet in 2000) over 64 KB still gives exact data")
def _():
    hw, duo = device(faults={"corrupt_p": 0.0005, "seed": 5})
    res = M.Lab(duo).run(lab_read_ops(0x10000))
    hw.stop(); clean(hw)
    assert res[-1][1] == RUBY[:0x10000]
    print(f"      {duo.stats['retries']} blocks re-sent")


@test("stream: --slow uses the Datel-style lab transfers and still gets the identical save")
def _():
    hw, duo = device()
    path, data, info, text, _ = gba_save(duo, slow=True)
    hw.stop(); clean(hw)
    assert data == RUBY and any(l.startswith("SendFile") for l in hw.log)


@test("stream: if the PC vanishes mid-transfer the DUO times out and recovers")
def _():
    hw, duo = device()
    lab = M.Lab(duo)
    prog = M.lab_encode([("w16", M.WAITCNT, 3)] + lab_read_ops(0x10000))
    lab._upload_fast(M.LAB_STAGE, prog)
    duo.t.write(M.packet(M.CMD_EXEC_STREAM, 0))
    duo.t.read(5000)                           # start packet
    for _ in range(4):                         # answer a few packets, then stop answering
        duo.t.write(M.packet(M.ST_CONT, 0))
        duo.t.read(5000)
    time.sleep(hwsim.FRAME_S * 900 + 2.5)
    duo._drain()
    assert "PC stopped answering" in hw.log, hw.log[-5:]
    assert duo.hello() == 0x104               # back in the command loop
    hw.stop(); clean(hw)


@test("link: stray stream answers and retired v3 commands get Datel's 'unknown' reply - never silence")
def _():
    hw, duo = device()
    for cmd in (M.ST_ACK, 0x0F, M.ST_CONT, 0x08, 0x1D):
        duo.t.write(b"CBW" + bytes([cmd]) + bytes(4))           # raw: the tool itself won't send 0x0F/0x08/0x1D
        assert M.parse(duo.t.read(5000))[0] == 0x01, f"{cmd:#x}"
    assert duo.hello() == 0x104
    hw.stop(); clean(hw)


@test("reproduction: v3 firmware + v3 tool stall exactly as on your DUO ('LAB: upload timed out')")
def _():
    import importlib.util
    spec = importlib.util.spec_from_file_location("old_tool", __import__("os").path.join(paths.HERE, "history", "maxduo_saves_v3_original.py"))
    OLD = importlib.util.module_from_spec(spec); spec.loader.exec_module(OLD)
    OLD.SHORT_TIMEOUT = 800
    hw = hwsim.Hw(OLD_V3, GAME, "sanyo", RUBY).start(); LIVE.append(hw)
    duo = OLD.MaxDuo(hwsim.Transport(hw)); duo.hello()
    args = types.SimpleNamespace(out=tempfile.mkdtemp(), map=None, save_type=None, slow=False)
    try:
        quiet(OLD.gba_save_v3, duo, args)
        raise AssertionError("the old pair worked in the simulator - it should not")
    except Exception as e:
        assert _is_timeout_like(e), e
    time.sleep(hwsim.FRAME_S * 900 + 3)
    assert "LAB: upload timed out" in hw.log, hw.log[-4:]
    assert any("PC sent two packets in a row" in x for x in hw.link_errors)
    hw.stop()
    assert not hw.violations and bytes(hw.save) == hw.original_save


def _is_timeout_like(e):
    return "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower()


@test("fallback: the new tool reads your save through the INSTALLED v3 firmware (slow, proven path)")
def _():
    hw, duo = device(fw=OLD_V3)
    path, data, info, text, _ = gba_save(duo)
    hw.stop(); clean(hw)
    assert data == RUBY and "Reckless v3, whose fast transfers stall" in text
    assert hw.save_writes == 8


# ==================================================================== uploads
@test("upload: firmware refuses out-of-window, zero-length and overflowing uploads")
def _():
    hw, duo = device()
    assert duo.ask(M.LAB_SETADDR, 0x0203AFF8) == (4, 0x0203AFF8)
    assert duo.ask(M.CMD_UPLOAD, 16)[0] == 0x3F                    # would cross the window end
    assert duo.ask(M.CMD_UPLOAD, 0)[0] == 0x3F
    assert duo.ask(M.CMD_UPLOAD, 0xFFFFFFF8)[0] == 0x3F            # wrap-around attempt
    assert duo.ask(M.LAB_SETADDR, 0x02000000)[0] == 0x3F           # outside the window entirely
    assert "LAB: bad upload request" in hw.log
    hw.stop(); clean(hw)


@test("upload: a corrupted upload is detected by its CRC")
def _():
    hw, duo = device()
    lab = M.Lab(duo)
    duo.ask(M.LAB_SETADDR, M.LAB_STAGE)
    assert duo.ask(M.CMD_UPLOAD, 16) == (M.CMD_UPLOAD, 16)
    duo.t.write(b"ABCDEFGH")
    assert M.parse(duo.t.read(5000)) == (M.UP_ACK, 8)                # every packet answered
    duo.t.write(b"ABCDEFGX")
    code, crc = M.parse(duo.t.read(5000))
    assert code == M.CMD_UPLOAD and crc != zlib.crc32(b"ABCDEFGHABCDEFGH")
    hw.stop(); clean(hw)


@test("upload: an incomplete upload times out cleanly, without replying out of turn")
def _():
    hw, duo = device()
    duo.ask(M.LAB_SETADDR, M.LAB_STAGE)
    assert duo.ask(M.CMD_UPLOAD, 64) == (M.CMD_UPLOAD, 64)
    duo.t.write(b"12345678")
    assert M.parse(duo.t.read(5000)) == (M.UP_ACK, 8)
    time.sleep(hwsim.FRAME_S * 900 + 2.5)                       # PC goes quiet; the DUO gives up
    assert "LAB: upload timed out" in hw.log, hw.log[-3:]
    assert duo.hello() == 0x104
    hw.stop(); clean(hw)


@test("messages: sanitised, truncated, at most three, and never repeated")
def _():
    hw, duo = device()
    lab = M.Lab(duo)
    lab.run(lab_read_ops(4), messages=["ok line", "tab\there", "x" * 40, "fourth"])
    n = len(hw.log)
    lab.run(lab_read_ops(4))
    hw.stop(); clean(hw)
    assert "ok line" in hw.log and "tab?here" in hw.log and "x" * 26 in hw.log
    assert "x" * 27 not in hw.log and "fourth" not in hw.log
    assert hw.log[n:].count("ok line") == 0


# ============================================================ safety / types
def save_case(tag, chip, save=None, save_type=None, expect=None, writes=0):
    game = hwsim.make_game_rom(tag=tag) if tag is not None else hwsim.make_game_rom(tag=None)
    hw, duo = device(chip, save=save, game=game)
    try:
        path, data, info, text, out = gba_save(duo, save_type=save_type)
        err = None
    except M.DuoError as e:
        err, data, out = str(e), None, None
    hw.stop(); clean(hw)
    assert hw.save_writes == writes, f"{hw.save_writes} chip writes, expected {writes}"
    return err, data, hw


@test("types: FLASH1M on Sanyo and Macronix chips -> 128 KB with bank switch")
def _():
    for chip in ("sanyo", "macronix"):
        err, data, hw = save_case(b"FLASH1M_V103", chip, writes=8)
        assert err is None and data == RUBY, chip


@test("types: 64 KB Flash (FLASH512_V / FLASH_V) -> 64 KB, no chip commands at all")
def _():
    for tag in (b"FLASH512_V131", b"FLASH_V126"):
        err, data, hw = save_case(tag, "flash512", save=RUBY[:0x10000])
        assert err is None and data == RUBY[:0x10000], tag


@test("types: SRAM (SRAM_V / SRAM_F_V) -> 32 KB, no writes at all")
def _():
    for tag in (b"SRAM_V113", b"SRAM_F_V100"):
        err, data, hw = save_case(tag, "sram", save=RUBY[:0x8000])
        assert err is None and data == RUBY[:0x8000], tag


@test("types: EEPROM, no tag, and conflicting tags are refused without touching the chip")
def _():
    err, _, _ = save_case(b"EEPROM_V124", "eeprom")
    assert err and "EEPROM" in err
    err, _, _ = save_case(None, "sram", save=RUBY[:0x8000])
    assert err and "tag" in err
    game = hwsim.make_game_rom(tag=b"FLASH1M_V103")
    game = game[:0x50000] + b"SRAM_V113" + game[0x50009:]
    hw, duo = device("sanyo", game=game)
    try:
        gba_save(duo); raise AssertionError("conflicting tags accepted")
    except M.DuoError as e:
        assert "several" in str(e)
    hw.stop(); clean(hw)
    assert hw.save_writes == 0


@test("types: a repro cart (FLASH1M tag, SRAM chip) gets NO bank switch and a warning")
def _():
    sram = RUBY[:0x8000]
    game = hwsim.make_game_rom(tag=b"FLASH1M_V103")
    hw, duo = device("sram", save=sram, game=game)
    path, data, info, text, _ = gba_save(duo)
    hw.stop(); clean(hw)
    assert hw.save_writes == 0 and "looks like SRAM" in text and data == sram * 2


@test("types: --save-type override works when the tag is missing")
def _():
    err, data, hw = save_case(None, "sram", save=RUBY[:0x8000], save_type="sram32")
    assert err is None and data == RUBY[:0x8000]


@test("types: tag found anywhere in the ROM, and the DUO's registers are never read")
def _():
    for at in (0x100, 0x7FFF0, 0xFFFF0):
        game = hwsim.make_game_rom(tag=b"FLASH1M_V103", tag_at=at, size=0x100000)
        hw, duo = device("sanyo", game=game)
        path, data, info, text, _ = gba_save(duo)
        hw.stop(); clean(hw)                       # clean() fails on any DUO-register read
        assert data == RUBY and info["tags"] == ["FLASH1M_V103"], at


@test("wake: without the wake-up read the chip stays silent (matches your DUO)")
def _():
    hw, duo = device()
    res = M.Lab(duo).run(lab_read_ops(16, wake=False))
    hw.stop(); clean(hw)
    assert res[-1][1] == b"\xFF" * 16


@test("verify: one bad byte from the chip is caught by the GBA's checksum and re-read")
def _():
    # detection reads 2x32 KB (crc) + 16 bytes; the next run's bank-0 read starts after that
    hw, duo = device(faults={"flip_save_read": {0x10010 + 0x1234}})
    path, data, info, text, _ = gba_save(duo)
    hw.stop(); clean(hw)
    assert data == RUBY and "reading again" in text


@test("verify: a chip that keeps giving different data is refused, no file written")
def _():
    base = 0x10010
    flips = {base + 0x1234, base + 0x10000 * 2 + 0x1234}       # both attempts
    hw, duo = device(faults={"flip_save_read": flips})
    try:
        gba_save(duo); raise AssertionError("inconsistent data accepted")
    except M.DuoError as e:
        assert "different data twice" in str(e)
    hw.stop(); clean(hw)


# ================================================================== lab ops
@test("lab: search finds aligned patterns, reports at most four, pads with FF")
def _():
    game = bytearray(hwsim.make_game_rom(tag=None))
    for at in (0x1000, 0x2004, 0x3008, 0x400C, 0x5000):
        game[at:at + 12] = b"ABCDtail%04X" % at
    hw, duo = device(game=bytes(game))
    w = lambda b: struct.unpack("<I", b)[0]
    res = M.Lab(duo).run([("w16", M.MAP_REG, 0x0101), ("r16", 0x08000000, 2),
                          ("search", 0x08000000, 0x10000, (w(b"ABCD"), w(b"ZZZZ"), w(b"YYYY"))),
                          ("search", 0x08000000, 0x10000, (w(b"QQQQ"), w(b"RRRR"), w(b"SSSS"))),
                          ("w16", M.MAP_REG, 0x0120)])
    hw.stop(); clean(hw)
    found, none = res[1][1], res[2][1]
    addrs = [struct.unpack_from("<I", found, i)[0] for i in range(0, 64, 16)]
    assert addrs == [0x08001000, 0x08002004, 0x08003008, 0x0800400C], [hex(a) for a in addrs]
    assert found[4:16] == b"ABCDtail1000" and none == b"\xFF" * 64


@test("lab: CRC op matches zlib for lengths 0, 1, 3, 4096 and 64 KB")
def _():
    hw, duo = device()
    ns = (0, 1, 3, 4096, 0x10000)
    ops = [("w16", M.MAP_REG, 0x0101), ("r16", 0x08000000, 2)] + [("crc", SAVE, n) for n in ns] + [("w16", M.MAP_REG, 0x0120)]
    res = M.Lab(duo).run(ops)
    hw.stop(); clean(hw)
    for n, (_, d) in zip(ns, res[1:]):
        assert struct.unpack("<I", d)[0] == zlib.crc32(RUBY[:n]), n


@test("lab: the GBA itself refuses erase/program values and stray addresses")
def _():
    real = M.lab_validate
    M.lab_validate = lambda ops: None
    try:
        for bad in ([("w8", 0x0E005555, 0xAA), ("w8", 0x0E002AAA, 0x55), ("w8", 0x0E005555, 0x80)],
                    [("w8", 0x0E005555, 0xA0)], [("w16", 0x08005554, 0x5555)], [("w8", 0x0E001234, 0x00)],
                    [("w16", 0x04000000, 0)]):
            hw, duo = device()
            try:
                M.Lab(duo).run([("w16", M.MAP_REG, 0x0101), ("r16", 0x08000000, 2)] + bad)
                raise AssertionError(f"{bad} accepted")
            except M.DuoError as e:
                assert "ERR!" in str(e)
            hw.stop(); clean(hw)
    finally:
        M.lab_validate = real


@test("fuzz: 120 random allowed scripts - never a save-changing write, always tidy afterwards")
def _():
    rng = random.Random(42)
    hw, duo = device()
    lab = M.Lab(duo)
    for i in range(120):
        ops = []
        for _ in range(rng.randrange(1, 9)):
            k = rng.randrange(9)
            if k == 0:
                ops.append(("w16", M.MAP_REG, rng.choice(sorted(M.LAB_MAPS))))
            elif k == 1:
                ops.append(("r16", 0x08000000 + 2 * rng.randrange(0x1000), 2))
            elif k == 2:
                ops.append(("r8", SAVE + rng.randrange(0x10000 - 64), rng.randrange(65)))
            elif k == 3:
                ops.append(("w8", rng.choice(sorted(M.SAVE_CMD_ADDRS)), rng.choice([0xAA, 0x55, 0x90, 0xF0, 0x00, 0x01])))
            elif k == 4:
                ops.append(("crc", SAVE + rng.randrange(0x100), rng.randrange(0x400)))
            elif k == 5:
                ops.append(("delay", 0, rng.randrange(0x100)))
            elif k == 6:
                ops.append(("r32", 0x08000000 + 4 * rng.randrange(0x1000), 4 * rng.randrange(4)))
            elif k == 7:
                ops.append(("wait16", 0x04000006, rng.randrange(1, 50)))
            else:
                ops.append(("sample8", SAVE, rng.randrange(1, 20) | 1 << 16))
        try:
            lab.run(ops)
        except M.DuoError as e:
            raise AssertionError(f"script {i} {ops}: {e}")
        assert hw.mapped is None and not hw.bad, (i, ops, hw.bad)
        assert bytes(hw.mu.mem_read(0x03000000, 0x178)) == hw.boot_iwram, f"flash routines not restored after {i}"
    hw.stop()
    assert not hw.violations, hw.violations          # B0/90/F0/AA/55 never change data


@test("regression: the earlier lab and v2 suites still pass on v3.1")
def _():
    for script in ("emu_test_lab.py", "emu_test2.py"):
        out = subprocess.run([sys.executable, script, paths.BUILD + "/fw_v3.bin"], cwd=paths.BUILD,
                             capture_output=True, text=True, timeout=1500)
        assert "ALL PASSED" in out.stdout, f"{script}:\n{out.stdout[-1500:]}"


# ======================================================================= main
if __name__ == "__main__":
    pick = sys.argv[1] if len(sys.argv) > 1 else ""
    passed = failed = 0
    t_all = time.time()
    for name, fn in RESULTS:
        if pick and pick not in name:
            continue
        t = time.time()
        try:
            fn()
            for hw in LIVE:
                if hw.thread.is_alive():
                    hw.stop()
            LIVE.clear()
            passed += 1
            print(f"[PASS] {name}  ({time.time() - t:.0f}s)", flush=True)
        except Exception as e:
            failed += 1
            for hw in LIVE:
                if hw.thread.is_alive():
                    hw.stop()
            print(f"[FAIL] {name}\n       {type(e).__name__}: {str(e)[:600]}", flush=True)
            if not isinstance(e, AssertionError):
                traceback.print_exc(limit=3)
    print(f"\n{passed} passed, {failed} failed in {time.time() - t_all:.0f}s")
