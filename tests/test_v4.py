"""
Test suite for Reckless v4 firmware + PC tool, run against hwsim (which
reproduces every behaviour observed on the real DUO).

    python tests/test_v4.py            run everything (about 15 minutes)
    python tests/test_v4.py stream     run tests whose name contains "stream"
    python tests/run_each.py           run each test in its own process (most reliable)

Needs the firmware built first: python firmware/build_firmware.py PATH_TO_DATEL_1.06_UPDATE --all
"""
import gc, io, contextlib, glob, json, os, random, struct, subprocess, sys, tempfile, threading, time, traceback, types, zlib
import paths
import hwsim, thumbcheck, maxduo_saves as M
import duo_wireless as W

FW = open(os.environ.get("V3_FW") or paths.build_file("fw_v4.bin"), "rb").read()
OFFICIAL = paths.read_build("fw.bin")
LABFW = paths.read_build("fw_lab.bin")
OLD_V3 = paths.read_build("fw_v3_original.bin", required=False)   # the first v3, whose transfers stalled
V31 = paths.read_build("fw_v3.bin")                                  # v3.1
FW42 = open(os.environ.get("V42_FW") or paths.build_file("fw_v42.bin"), "rb").read()    # v4.2
import fixtures
RUBY = fixtures.make_gen3_save("May", 12345, save_no=337)          # synthetic Ruby save
GAME = hwsim.make_game_rom()
SAVE = 0x0E000000
RESULTS = []


class Skip(Exception):
    """A test that needs an optional file which isn't present."""
LIVE = []
M.SHORT_TIMEOUT = 800        # keep the real ratio: DUO waits ~15 s, PC ~5 s (scaled here)
M.BUSY_WAIT_S = 3            # the DUO's 15 s give-up time is 1.8 s in the simulator


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
@test("build: v4 rebuilds byte-for-byte from source (reproducible)")
def _():
    work = tempfile.mkdtemp()
    open(os.path.join(work, "fw.bin"), "wb").write(OFFICIAL)
    out = subprocess.run([sys.executable, "-c", "import build_v4; build_v4.build()"], cwd=work, capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=paths.FIRMWARE))
    assert out.returncode == 0, out.stderr
    assert open(os.path.join(work, "fw_v4.bin"), "rb").read() == FW


@test("build: outside Datel's padding, only command slots and text pointers change")
def _():
    allowed = {0x080074A8, 0x080074BC, 0x080074C4, 0x08007518, 0x08007524,               # cmds 2,7,9,1E,21
               0x080074AC, 0x080074B0, 0x080074B4, 0x080074B8,                           # cmds 3,4,5,6 (lab/v2)
               0x080073A0, 0x080073A4,                                                   # redirected call
               0x0800B238}                                                                # version label
    allowed |= {t + 4 * 36 for t in (0x0802BB70, 0x0802BE94, 0x0802C0B8, 0x0802C2DC)}     # USB-mode line only
    bad = sorted({hex(0x08000000 + i & ~3) for i in range(len(OFFICIAL)) if FW[i] != OFFICIAL[i]
                  and not 0x1E7C2 <= i < 0x1FD90 and (0x08000000 + i & ~3) not in allowed})
    assert not bad, f"unexpected changes at {bad}"


@test("build: Datel's own command handlers are untouched")
def _():
    ours = {2, 3, 4, 5, 6, 7, 9, 0x1E, 0x21}
    for c in range(2, 0x28):
        o = 0x74A8 + 4 * (c - 2)
        if c not in ours:
            assert FW[o:o + 4] == OFFICIAL[o:o + 4], f"command {c:#x} changed"


@test("build: every patch routine (v1, v2, lab, v4) is ARM7TDMI Thumb-1 only")
def _():
    import build_patch as v1, build_patch2 as v2, build_lab as L, build_v4 as V
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
    crc, payload = M.check_update_file(paths.build_file("duo_1.06_reckless_v4.1.bin"))
    assert payload == FW and "v4.1" in M.KNOWN_FIRMWARE.get(crc, "")
    for name, word in (("installed_v4.bin", "use v4.1"), ("duo_1.06_reckless_v3_original.bin", "use v3.1")):   # optional old builds
        if paths.build_file(name, required=False):
            crc, _ = M.check_update_file(paths.build_file(name))
            assert word in M.KNOWN_FIRMWARE.get(crc, "")


@test("update: installing v4.1 over the official, the lab edition, v3, v3.1 AND the first v4, flash simulated")
def _():
    saved, sys.argv = sys.argv, sys.argv[:1]
    try:
        import emu_update_test as U
    finally:
        sys.argv = saved
    for current in (OFFICIAL, LABFW, OLD_V3, V31, paths.read_build("installed_fw_v4.bin", required=False)):
        if current is None:
            continue
        U.OFFICIAL = current
        ok, text = quiet(U.main, paths.build_file("duo_1.06_reckless_v4.1.bin"))
        assert ok, text


# ============================================================ basic behaviour
@test("info: v4 identifies itself; v3.1, v3 and the lab edition are told apart")
def _():
    for fw, kind, fast in ((FW, "v4", True), (V31, "v3.1", True), (OLD_V3, "v3", False), (LABFW, None, False)):
        if fw is None:
            continue
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


@test("full --gba-save of a Ruby save: identical file, no writes beyond bank switches")
def _():
    hw, duo = device()
    t0 = hw.sent_packets
    path, data, info, text, _ = gba_save(duo)
    hw.stop(); clean(hw)
    assert data == RUBY and open(path, "rb").read() == RUBY
    assert info["title"] == "POKEMON RUBY" and info["code"] == "AXVE" and "known game" in info["tags"][0]
    assert hw.save_writes == 8, f"{hw.save_writes} chip writes (expected 2 bank switches = 8)"


@test("screen log: expected messages, in order, all short enough for the screen")
def _():
    hw, duo = device()
    gba_save(duo)
    hw.stop()
    log = hw.log
    want = ["PC connected: RECKLESS V4", "GBA save: reading game", "Sent 64 KB",
            "Reading bank 1 (64 KB)", "Save copied to PC", f"128 KB, CRC {zlib.crc32(RUBY):08X}"]
    pos = 0
    for w in want:
        assert w in log[pos:], f"missing or out of order: {w!r}\n{log}"
        pos = log.index(w, pos) + 1
    assert all(len(l) <= 27 for l in log), [l for l in log if len(l) > 27]


@test("speed: v4 needs a fifteenth of v2's USB transfers for the same save (both directions counted)")
def _():
    hw, duo = device()
    n0 = hw.sent_packets + hw.pc_packets
    gba_save(duo)
    v31 = hw.sent_packets + hw.pc_packets - n0
    hw.stop(); clean(hw)
    v2 = 2 * (0x20000 // 8) + 8                 # Datel-style: every 8 bytes = 1 packet + 1 ack
    print(f"      USB transfers: v2 {v2}, v4 {v31} -> about {v2 * 0.0092:.0f} s vs {v31 * 0.0092:.0f} s on the real DUO"
          " (9.2 ms per transfer, as measured in lab session 1)")
    assert v31 * 14 < v2, v31


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
        hw, duo = device("sram", save=pat, game=hwsim.make_game_rom(code=b"ZTST", tag=b"SRAM_V113"))
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


@test("reproduction: v3 firmware + v3 tool stall exactly as on the real DUO ('LAB: upload timed out')")
def _():
    import importlib.util
    old_tool = os.path.join(paths.HERE, "history", "maxduo_saves_v3_original.py")
    if OLD_V3 is None or not os.path.exists(old_tool):
        raise Skip("build with --all first: needs the original v3 firmware")
    spec = importlib.util.spec_from_file_location("old_tool", old_tool)
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


@test("fallback: the new tool reads a save through the INSTALLED v3 firmware (slow, proven path)")
def _():
    if OLD_V3 is None:
        raise Skip("build with --all first: needs the original v3 firmware")
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
    game = hwsim.make_game_rom(code=b"ZTST", tag=tag) if tag is not None else hwsim.make_game_rom(code=b"ZTST", tag=None)
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
    game = hwsim.make_game_rom(code=b"ZTST", tag=b"FLASH1M_V103")
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
    game = hwsim.make_game_rom(code=b"ZTST", tag=b"FLASH1M_V103")
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
        game = hwsim.make_game_rom(code=b"ZTST", tag=b"FLASH1M_V103", tag_at=at, size=0x100000)
        hw, duo = device("sanyo", game=game)
        path, data, info, text, _ = gba_save(duo)
        hw.stop(); clean(hw)                       # clean() fails on any DUO-register read
        assert data == RUBY and info["tags"] == ["FLASH1M_V103"], at


@test("wake: without the wake-up read the chip stays silent (matches the real DUO)")
def _():
    hw, duo = device()
    res = M.Lab(duo).run(lab_read_ops(16, wake=False))
    hw.stop(); clean(hw)
    assert res[-1][1] == b"\xFF" * 16


@test("verify: one bad byte from the chip is caught by the GBA's checksum and re-read")
def _():
    # the first run checksums 2x32 KB, then reads bank 0 (from save-read 0x10000), then checksums it
    hw, duo = device(faults={"flip_save_read": {0x10000 + 0x1234}})
    path, data, info, text, _ = gba_save(duo)
    hw.stop(); clean(hw)
    assert data == RUBY and "reading again" in text


@test("verify: a chip that keeps giving different data is refused, no file written")
def _():
    flips = {0x10000 + 0x1234, 0x30000 + 0x1234, 0x50000 + 0x1234}   # first read and both re-reads
    hw, duo = device(faults={"flip_save_read": flips})
    try:
        gba_save(duo); raise AssertionError("inconsistent data accepted")
    except M.DuoError as e:
        assert "different data twice" in str(e)
    hw.stop(); clean(hw)


# ================================================================== lab ops
@test("lab: search finds aligned patterns, reports at most four, pads with FF")
def _():
    game = bytearray(hwsim.make_game_rom(code=b"ZTST", tag=None))
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


@test("regression: the earlier lab and v2 suites still pass on v4")
def _():
    for script in ("emu_test_lab.py", "emu_test2.py"):
        out = subprocess.run([sys.executable, script, paths.build_file("fw_v4.bin")], cwd=paths.HERE,
                             capture_output=True, text=True, timeout=1500)
        assert "ALL PASSED" in out.stdout, f"{script}:\n{out.stdout[-1500:]}"


# ======================================================================= v4
OTHER = fixtures.make_gen3_save("Brendan", 54321, save_no=12, seed=2)  # a different trainer


@test("v4 look: Datel's colours, start-up and title text restored; only the USB line and version differ")
def _():
    assert FW[0xEADC:0xEADC + 14 * 12] == OFFICIAL[0xEADC:0xEADC + 14 * 12], "colour schemes"
    assert FW[0xFA0:0xFA8] == OFFICIAL[0xFA0:0xFA8], "start-up colours"
    rd = lambda fw, a: struct.unpack_from("<I", fw, a - 0x08000000)[0]
    cs = lambda fw, a: fw[a - 0x08000000:fw.index(b"\0", a - 0x08000000)].decode("latin-1")
    for t in (0x0802BB70, 0x0802BE94, 0x0802C0B8, 0x0802C2DC):
        assert rd(FW, t) == rd(OFFICIAL, t) and rd(FW, t + 240) == rd(OFFICIAL, t + 240), "start-up / title text"
        assert cs(FW, rd(FW, t + 144)) == "RECKLESS V4 - READY"
    assert cs(FW, rd(FW, 0x0800B238)) == "V1.06 RM4"


@test("restart: reply first, soft reset, Datel's start-up answers the waiting hello, link stays in step")
def _():
    hw, duo = device()
    took = duo.restart(wait_s=20)
    path, data, info, text, _ = gba_save(duo)                 # fully usable afterwards
    hw.stop(); clean(hw)
    assert data == RUBY
    assert hw.restarts == 1 and hw.boots == ["usb"] and "Restarting (PC request)" in hw.log, (hw.restarts, hw.boots)


@test("restart: ten in a row always come back to USB mode, with real USB latency (v4's race is gone)")
def _():
    hw, duo = device()
    hw.usb_latency = 0.010                       # PC packets take ~10 ms to reach the GBA, as with real USB polling
    for i in range(10):
        duo.restart(wait_s=10)
        assert duo.hello() == 0x104, i
    hw.stop(); clean(hw)
    assert hw.restarts == 10 and hw.boots == ["usb"] * 10, hw.boots


@test("8-bit save bus: a full save and a cartridge report never read the save area wider than 8 bits")
def _():
    hw, duo = device()
    gba_save(duo)
    out = tempfile.mkdtemp(); cwd = os.getcwd(); os.chdir(out)
    try:
        quiet(M.cart_report, duo, types.SimpleNamespace(map=None, slow=False))
    finally:
        os.chdir(cwd)
    hw.stop(); clean(hw)                      # clean() fails on any 16/32-bit save read


@test("restart: three in a row, each followed by a lab run; refused politely on v3.1")
def _():
    hw, duo = device()
    for i in range(3):
        duo.restart(wait_s=20)
        assert M.Lab(duo).run(lab_read_ops(64))[-1][1] == RUBY[:64]
    hw.stop(); clean(hw)
    assert hw.restarts == 3 and hw.boots == ["usb"] * 3
    hw, duo = device(fw=V31)
    try:
        duo.restart(); raise AssertionError("v3.1 accepted a restart")
    except M.DuoError as e:
        assert "v4" in str(e)
    assert duo.hello() == 0x104
    hw.stop(); clean(hw)


@test("power-on: a waiting hello -> USB SERVER MODE; no hello -> the menu, and USB isn't served")
def _():
    hw = hwsim.Hw(FW, GAME, "sanyo", RUBY, powered=False).start(); LIVE.append(hw)
    duo = M.MaxDuo(hwsim.Transport(hw))
    threading.Timer(2.0, hw.power_on).start()    # well after the tool's first wait (0.2 s clearing + 0.8 s)
    v, text = quiet(duo.connect, 20)
    assert v == 0x104 and "Switch the GBA on now" in text
    time.sleep(0.3)
    assert hw.boots == ["usb"]
    hw.stop(); clean(hw)
    hw = hwsim.Hw(FW, GAME, "sanyo", RUBY, powered=False).start(); LIVE.append(hw)
    hw.power_on(); time.sleep(0.5)
    assert hw.boots == ["menu"]
    duo = M.MaxDuo(hwsim.Transport(hw))
    try:
        quiet(duo.connect, 2); raise AssertionError("menu answered USB")
    except M.DuoError as e:
        assert "USB SERVER MODE" in str(e)
    hw.stop()


@test("connect: stale packets are cleared; a DUO still busy with an interrupted transfer is waited for")
def _():
    hw, duo = device()
    hw.inject_stale(b"SBW\x10" + bytes(4)); hw.inject_stale(b"garbage!")
    v, text = quiet(duo.connect)
    assert v == 0x104 and "cleared 2 old packet" in text
    lab = M.Lab(duo)
    prog = M.lab_encode([("w16", M.WAITCNT, 3)] + lab_read_ops(0x8000))
    lab._upload_fast(M.LAB_STAGE, prog)
    duo.t.write(M.packet(M.CMD_EXEC_STREAM, 0)); duo.t.read(5000)          # start a stream, then abandon it
    duo.t.write(M.packet(M.ST_CONT, 0)); duo.t.read(5000)
    duo2 = M.MaxDuo(duo.t)                                                  # 'the tool is run again'
    v, text = quiet(duo2.connect)
    assert v == 0x104
    assert M.Lab(duo2).run(lab_read_ops(64))[-1][1] == RUBY[:64]
    hw.stop(); clean(hw)


@test("resident interpreter: later runs send only header + operations; a damaged copy is rejected, never run")
def _():
    hw, duo = device()
    lab = M.Lab(duo)
    lab.run(lab_read_ops(64))
    p0 = hw.pc_packets
    assert lab.run(lab_read_ops(64))[-1][1] == RUBY[:64]
    partial = hw.pc_packets - p0
    assert getattr(lab, "partial_uploads", 0) == 1
    hw.mu.mem_write(M.LAB_STAGE + 0x100, b"\x00\xde\x00\xde")              # damage the resident interpreter
    p0 = hw.pc_packets
    assert lab.run(lab_read_ops(64))[-1][1] == RUBY[:64]                    # rejected, then sent in full
    full = hw.pc_packets - p0
    assert "LAB: script rejected" in hw.log and duo.lab_resident
    hw.stop(); clean(hw)
    print(f"      PC packets per small run: {partial} with the interpreter resident, {full} after a rejection")
    assert partial * 2 < full


@test("fast save: known games skip the search; unknown games search only their real ROM size")
def _():
    hw, duo = device()
    path, data, info, text, _ = gba_save(duo)
    hw.stop(); clean(hw)
    assert data == RUBY and "known game (Pokemon Ruby)" in text and "search" not in text
    for mb in (1, 2, 4):
        g = hwsim.make_game_rom(code=b"ZTST", tag=b"FLASH1M_V103", size=0x80000, tag_at=0x7FF00, rom_size=mb << 20)
        hw, duo = device(game=g)
        path, data, info, text, _ = gba_save(duo)
        hw.stop(); clean(hw)
        assert data == RUBY and f"({mb} MB ROM)" in text, (mb, text[-400:])


@test("cartridge report: corrected whole-ROM CRC equals the real ROM's, for 1, 2 and 4 MB chips")
def _():
    for mb in (1, 2, 4):
        g = hwsim.make_game_rom(code=b"ZTST", tag=b"FLASH1M_V103", size=min(0x100000, mb << 19), rom_size=mb << 20, seed=mb)
        hw, duo = device(game=g)
        out = tempfile.mkdtemp(); cwd = os.getcwd(); os.chdir(out)
        try:
            rep, text = quiet(M.cart_report, duo, types.SimpleNamespace(map=None, slow=False))
        finally:
            os.chdir(cwd)
        hw.stop(); clean(hw)
        assert rep["rom_size_mb"] == mb and rep["rom_crc32"] == f"{zlib.crc32(g):08X}", (mb, rep)
        assert rep["header_ok"] and rep["logo_start_ok"] and rep["stable"]
        assert rep["timing_game_ok"] and not rep["timing_2cycle_ok"]       # the simulated chip fails 2-cycle reads


@test("cartridge report: reference match -> genuine; mismatch -> flagged; SRAM-in-Flash repro flagged")
def _():
    g = hwsim.make_game_rom(code=b"ZTST", tag=b"FLASH1M_V103", size=0x80000, rom_size=0x100000)
    for ref, word in ((zlib.crc32(g), "genuine"), (0x12345678, "does NOT match")):
        hw, duo = device(game=g)
        out = tempfile.mkdtemp(); cwd = os.getcwd(); os.chdir(out)
        try:
            json.dump({"ZTST rev 0": f"{ref:08X}"}, open(M.REFERENCES_FILE, "w"))
            rep, text = quiet(M.cart_report, duo, types.SimpleNamespace(map=None, slow=False))
        finally:
            os.chdir(cwd)
        hw.stop(); clean(hw)
        assert rep["verdict"].startswith(word), rep["verdict"]
    hw, duo = device("sram", save=RUBY[:0x8000], game=g)
    out = tempfile.mkdtemp(); cwd = os.getcwd(); os.chdir(out)
    try:
        rep, text = quiet(M.cart_report, duo, types.SimpleNamespace(map=None, slow=False))
    finally:
        os.chdir(cwd)
    hw.stop(); clean(hw)
    assert rep["save_halves_mirror"] and "reproduction" in text and hw.save_writes == 0


@test("cartridge report: the logo constants match their derivation and correct a known example")
def _():
    import logo_correction as LC
    assert {k: v for k, v in LC.constants().items()} == M.LOGO_CRC_FIX
    assert M.LOGO_START == hwsim.LOGO[:0x1C]
    rom = bytearray(random.Random(9).getrandbits(8) for _ in range(0x100000)); rom[4:0xA0] = hwsim.LOGO
    seen = bytearray(rom); seen[0x20:0x9C] = bytes(0x7C); seen[0x9C:0xA0] = hwsim.MAPREG_READ
    assert zlib.crc32(seen) ^ M.LOGO_CRC_FIX[0x100000] == zlib.crc32(rom)


@test("backups: per-game folder, identical saves not stored twice, a new trainer is warned about")
def _():
    out = tempfile.mkdtemp()
    def run_save(save):
        hw, duo = device(save=save)
        args = types.SimpleNamespace(out=out, map=None, save_type=None, slow=False)
        (path, data, info), text = quiet(M.gba_save_v3, duo, args)
        hw.stop(); clean(hw)
        return path, text
    p1, t1 = run_save(RUBY)
    assert "May" in os.path.basename(p1) and "Trainer May (ID 12345)" in t1 and "save #337" in t1
    p2, t2 = run_save(RUBY)
    assert p2 == p1 and "not stored again" in t2
    p3, t3 = run_save(OTHER)
    assert "Brendan" in os.path.basename(p3) and "WARNING" in t3 and "May" in t3
    assert open(p1, "rb").read() == RUBY                                     # earlier backup untouched
    assert len(os.listdir(os.path.dirname(p1))) == 2


@test("refusals: DeleteFile and the file-writing commands can never be sent, and a full run never tries")
def _():
    for cmd, word in ((0x1C, "DeleteFile"), (0x0A, "writes"), (0x0B, "writes"), (0x25, "writes"), (0x27, "DS card")):
        try:
            M.packet(cmd); raise AssertionError(f"{cmd:#x} not refused")
        except M.DuoError as e:                      # refused WITH its explanation, not just unlisted
            assert "refusing" in str(e) and word in str(e), str(e)
    assert not (set(M.REFUSED) & M.SAFE_COMMANDS)
    sent = []
    hw, duo = device()
    orig = duo.t.write
    duo.t.write = lambda data, timeout=5000: (sent.append(bytes(data)), orig(data, timeout))[1]
    quiet(duo.connect); gba_save(duo); duo.restart(wait_s=20)
    hw.stop(); clean(hw)
    cmds = {p[3] for p in sent if p[:3] == b"CBW"}
    assert not (cmds & set(M.REFUSED)) and cmds <= M.SAFE_COMMANDS, sorted(map(hex, cmds))


@test("compatibility: the v4 tool saves fast through v3.1 firmware too")
def _():
    hw, duo = device(fw=V31)
    path, data, info, text, _ = gba_save(duo)
    hw.stop(); clean(hw)
    assert data == RUBY and duo.reckless() == "v3.1"


@test("lab: table CRC op (crct) matches zlib on ROM; refused for the 8-bit save area and when unaligned")
def _():
    hw, duo = device()
    lab = M.Lab(duo)
    for n in (0, 4, 8, 4096, 0x10000):
        ops = [("w16", M.MAP_REG, 0x0101), ("delay", 0, 0x400), ("r16", 0x08000000, 2),
               ("crct", 0x08000100, n), ("w16", M.MAP_REG, 0x0120)]
        assert struct.unpack("<I", lab.run(ops)[-1][1])[0] == zlib.crc32(GAME[0x100:0x100 + n]), n
    for bad in (("crct", 0x08000101, 4), ("crct", 0x08000100, 6), ("crct", 0x0E000000, 0x10000)):
        try:
            M.lab_validate([bad]); raise AssertionError(bad)
        except M.DuoError:
            pass
    hw.stop(); clean(hw)


@test("lab_runner: allows the new read-only commands, still refuses --update")
def _():
    src = open(os.path.join(paths.ROOT, "lab_runner.py")).read()
    assert '"--cart-report"' in src and '"--restart"' in src and 'FORBIDDEN = {"--update"}' in src


# ======================================================================= main
@test("fast save: garbled start of a run's output (seen on real hardware) is caught; header re-read")
def _():
    real = M.MaxDuo.receive_stream
    calls = []
    def garbling(self, *a, **k):
        data = bytearray(real(self, *a, **k))
        calls.append(1)
        if len(calls) == 1:
            data[2:9] = bytes([0x00, 0x13, 0x88, 0x01, 0x00, 0x00, 0x7F])     # 7 bytes after the wake read
        return bytes(data)
    for garble_header in (False, True):
        calls.clear()
        M.MaxDuo.receive_stream = garbling
        try:
            hw, duo = device()
            if garble_header:
                def garbling2(self, *a, **k):
                    data = bytearray(real(self, *a, **k)); calls.append(1)
                    if len(calls) == 1:
                        data[34:39] = b"\x00\x13\x88\x01\x00"   # first 5 header bytes
                    return bytes(data)
                M.MaxDuo.receive_stream = garbling2
            path, data, info, text, _ = gba_save(duo)
        finally:
            M.MaxDuo.receive_stream = real
        hw.stop(); clean(hw)
        assert data == RUBY and info["header_ok"] and info["title"] == "POKEMON RUBY", (info, text[-600:])
        assert "POKEMON RUBY" in path
        assert ("garbled" in text) != garble_header and ("reading it again" in text) == garble_header, text[-600:]



# ====================================================================== v4.2
import sio32id_model


def run42(**kw):
    hw = hwsim.Hw(FW42, kw.get("game") or GAME, "sanyo", RUBY).start(); LIVE.append(hw)
    duo = M.MaxDuo(hwsim.Transport(hw)); duo.hello()
    return hw, duo


@test("v4.2 build: only command slot 0x23, the INFO reply and Datel's padding differ from v4.1; reproducible")
def _():
    diff = [i for i in range(len(FW)) if FW42[i] != FW[i]]
    outside = sorted({0x08000000 + (i & ~3) for i in diff if not 0x1E7C2 <= i < 0x1FD90})
    assert outside == [0x080074A8 + 4 * (0x23 - 2)], [hex(x) for x in outside]
    assert struct.pack("<I", M.RK42) in FW42 and struct.pack("<I", M.RK40) not in FW42[0x1F400:0x1F500]
    work = tempfile.mkdtemp()
    open(os.path.join(work, "fw.bin"), "wb").write(OFFICIAL)
    out = subprocess.run([sys.executable, "-c", "import build_v42; build_v42.build()"], cwd=work, capture_output=True,
                         text=True, env=dict(os.environ, PYTHONPATH=paths.FIRMWARE))
    assert out.returncode == 0, out.stderr[-800:]
    assert open(os.path.join(work, "fw_v42.bin"), "rb").read() == FW42


@test("two tools: the save tool has no wireless options; duo_wireless.py has them and needs the save tool beside it")
def _():
    saves = subprocess.run([sys.executable, os.path.join(paths.ROOT, "maxduo_saves.py"), "--help"], capture_output=True, text=True)
    wireless = subprocess.run([sys.executable, os.path.join(paths.ROOT, "duo_wireless.py"), "--help"], capture_output=True, text=True)
    assert saves.returncode == 0 and "adapter" not in saves.stdout.lower() and "wonder" not in saves.stdout.lower(), saves.stdout
    assert wireless.returncode == 0 and all(f in wireless.stdout for f in ("--test", "--trace", "--wonder-card", "--probe"))
    assert W.SAFE_COMMANDS is M.SAFE_COMMANDS and W.PROG_PUT in M.SAFE_COMMANDS   # one shared allow-list


@test("v4.2 programs: the tool's embedded programs are exactly this repository's build, Thumb-1 only")
def _():
    out = subprocess.run([sys.executable, "-c", "import programs, sys; sys.stdout.write(programs.build('adapter_detect').hex())"],
                         cwd=paths.FIRMWARE, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=paths.FIRMWARE))
    assert out.returncode == 0, out.stderr[-800:]
    assert bytes.fromhex(out.stdout) == W.PROGRAMS["adapter_detect"]


@test("v4.2 run: a staged program with a bad magic, length, entry or CRC is refused and never runs")
def _():
    good = bytearray(W.PROGRAMS["adapter_detect"])
    bad_images = []
    b = bytearray(good); b[0] ^= 1; bad_images.append(b)                         # magic
    b = bytearray(good); struct.pack_into("<I", b, 4, 0x4000); bad_images.append(b)  # length too big
    b = bytearray(good); struct.pack_into("<I", b, 12, 0x1000); bad_images.append(b) # entry outside the code
    b = bytearray(good); b[40] ^= 0x10; bad_images.append(b)                        # code damaged (CRC)
    for img in bad_images:
        hw, duo = run42()
        M.Lab(duo)._upload_fast(M.LAB_STAGE, bytes(img))
        code, val = duo.ask(W.CMD_RUN)
        assert code == 0x3F, hex(code)
        assert duo.hello() == 0x104
        hw.stop(); clean(hw)
        assert "Program rejected" in hw.log and "Running program" not in hw.log


@test("v4.2 adapter test: answers the game's NINTENDO / 0x8001 exchange; the game accepts the adapter; DUO back in USB mode")
def _():
    hw, duo = run42()
    game = sio32id_model.Side(1)                      # the game's half: librfu's own code, clock master
    hw.attach_link_partner(game)
    st = quiet(W.adapter_test, duo, types.SimpleNamespace(adapter_test=30), poll_s=0.1, after_s=0.5)[0]
    assert game.last == 0x8001 and st >> 21 & 1, (hex(game.last), hex(st))
    words = hw.link_words
    assert words[1] == (0x494EFFFF, 0xFFFF494E) and (0x8001B0BB, 0xB0BB8001) in words   # matches the real adapter
    assert duo.hello() == 0x104 and duo.reckless() == "v4"
    hw.stop(); clean(hw)
    assert "Adapter check answered" in hw.log and any(l.startswith("Program finished") for l in hw.log)


@test("v4.2 run: an upload answer held up straight after an earlier run (seen on hardware) -> the tool waits and re-sends")
def _():
    hw, duo = run42()
    quiet(W.adapter_test, duo, types.SimpleNamespace(adapter_test=1), poll_s=0.2, after_s=0.5)   # an earlier run
    real_read, reads, held = duo.t.read, [0], []

    def late_read(timeout=5000):
        reads[0] += 1
        if reads[0] == 4:                      # SETADDR, UPLOAD, packet 1 answered; packet 2's answer is held up
            held.append(real_read(timeout))
            threading.Timer(0.3, lambda: hw.d2h.put(held[0])).start()   # ... and turns up much later
            import usb.core
            raise usb.core.USBTimeoutError("timeout")
        return real_read(timeout)
    duo.t.read = late_read
    _, text = quiet(W.run_program, duo, "adapter_detect")
    duo.t.read = real_read
    assert "missed part of the program upload" in text and "cleared 1 old packet" in text, text
    assert duo.ask(W.PROG_STOP)[0] == W.PROG_STOP and duo.hello() == 0x104
    hw.stop(); clean(hw)
    assert "LAB: upload timed out" in hw.log and hw.log.count("Running program") == 2, hw.log


@test("connect: a first answer that's held up (seen on hardware) -> the tool waits and tries again")
def _():
    hw = hwsim.Hw(FW42, GAME, "sanyo", RUBY).start(); LIVE.append(hw)
    duo = M.MaxDuo(hwsim.Transport(hw))
    real_read, reads, held = duo.t.read, [0], []

    def late_read(timeout=5000):
        pkt = real_read(timeout)
        reads[0] += 1
        if reads[0] == 2:                      # hello answered; the firmware check's answer is held up
            held.append(pkt)
            threading.Timer(0.3, lambda: hw.d2h.put(held[0])).start()
            import usb.core
            raise usb.core.USBTimeoutError("timeout")
        return pkt
    duo.t.read = late_read
    (version, v42), text = quiet(W.connect, duo, 30)
    duo.t.read = real_read
    assert version == 0x104 and v42 and "trying again" in text, text
    assert duo.hello() == 0x104
    hw.stop(); clean(hw)


@test("v4.2 adapter test: with nothing on the cable it reports 'nothing from the game' and stops cleanly")
def _():
    hw, duo = run42()
    st, text = quiet(W.adapter_test, duo, types.SimpleNamespace(adapter_test=1), poll_s=0.2, after_s=0.5)
    assert st & 0xFFFF == 0 and "nothing from the game yet" in text and "not detected" in text
    assert duo.hello() == 0x104
    hw.stop(); clean(hw)


@test("v4.2 adapter test: the game starts mid-way (junk words first) and still accepts the adapter")
def _():
    hw, duo = run42()
    game = sio32id_model.Side(1); game.out = 0x12345678; game.count = 2; game.recv_id = 0xBEEF
    hw.attach_link_partner(game)
    st = quiet(W.adapter_test, duo, types.SimpleNamespace(adapter_test=30), poll_s=0.1, after_s=0.5)[0]
    hw.stop(); clean(hw)
    assert game.last == 0x8001 and st >> 21 & 1


# ---------------------------------------------------------------- the adapter program (C)
import rfu_game_model as RFU


_SYMS = {}


def adapter_symbol(name, program="adapter"):
    """Address of a global in an adapter build (needs clang; the embedded copies are checked
    against a fresh build by another test, so the layouts match)."""
    if program not in _SYMS:
        sys.path.insert(0, paths.FIRMWARE)
        import programs
        try:
            _SYMS[program] = programs.symbols(program)
        except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
            raise Skip(f"needs clang/llvm to locate program symbols: {e}")
    return _SYMS[program][name]


def adapter_session(script, seconds=20, attach=True, program="adapter", lag_bits=0, first_bit=False,
                    flick_window=None, wonder_card=None, check_search=False,
                    sweep_serial=False, serial=None, wonder_news=None):
    hw, duo = run42()
    game = RFU.Game()
    if attach:
        flick = (adapter_symbol("flick_w", program),) + tuple(flick_window) if flick_window else None
        hw.attach_link_game(script(game), lag_bits=lag_bits, first_bit=first_bit, flick=flick)
    r = quiet(W.adapter_trace, duo, types.SimpleNamespace(adapter_trace=seconds, adapter_probe=seconds,
                                                          wonder_card=wonder_card, check_search=check_search,
                                                          sweep_serial=sweep_serial, serial=serial,
                                                          wonder_news=wonder_news),
              poll_s=0.2,
              stop_when=(lambda: hw.lg_done) if attach else None, program=program)
    entries, st, counters = r[0][:3]
    hw.probe = r[0][3] if len(r[0]) > 3 else None
    hw.text = r[1]
    assert duo.hello() == 0x104 and duo.reckless() == "v4"          # back in USB server mode
    hw.stop(); clean(hw)
    assert any(l.startswith("Program finished") for l in hw.log)
    trace = [W.describe_trace_entry(h, d) for _, h, d in entries]
    return hw, game, trace, st, counters


@test("adapter program: the tool's embedded copy is this repository's build (clang 18.1), Thumb-1 only")
def _():
    sys.path.insert(0, paths.FIRMWARE)
    import programs
    try:
        version = programs.clang_version()
    except RuntimeError as e:
        raise Skip(str(e))
    if version != programs.CLANG_VERSION:
        raise Skip(f"clang {version} here; the embedded copy was built with {programs.CLANG_VERSION}")
    assert programs.build("adapter") == W.PROGRAMS["adapter"]
    assert programs.build("adapter_probe") == W.PROGRAMS["adapter_probe"]


@test("wireless adapter: Emerald's Mystery Gift search, command by command, answered as a real adapter would")
def _():
    def script(game):
        acks = yield from RFU.mystery_gift_search(game, polls=12)
        yield ("delay", 40 * RFU.MS)
        acks["sleep"] = yield from RFU.go_to_sleep(game)
        return acks
    hw, game, trace, st, counters = adapter_session(script)
    assert hw.lg_error is None and hw.lg_done, repr(hw.lg_error)
    acks = hw.lg_result
    assert acks[0x10] == [0x99660090] and acks[0x17] == [0x99660097] and acks[0x16] == [0x99660096]
    assert acks[0x1C] == [0x9966009C] and acks[0x1D][:2] == [0x9966079D, 0x7DA5] and acks[0x1E][:2] == [0x9966079E, 0x7DA5]
    assert acks["wait"] == 0x99660027                                  # the adapter's turn: nothing happened
    assert acks[0x13] == [0x99660193, 0]                               # idle, no ID
    assert acks[0x99] == "rejected" and acks["sleep"] == [0x996600BD]
    assert not game.errors, game.errors
    assert game.retries == 0 and not hw.link_trouble, (game.retries, hw.link_trouble)
    names = [t for t, _ in trace]
    expect = ["ID exchange completed", "Hello", "setup (0x17) 003C0420", "broadcast data", "start searching",
              "search results?", "stop searching", "wait (0x27)", "the adapter's turn", "status (0x13)",
              "unknown command (0x99)", "ID exchange completed", "sleep (0x3D)"]
    assert len(names) == len(expect) and all(n.startswith(e) for n, e in zip(names, expect)), names
    assert trace[5][1] == 12                                          # 12 polls, one line
    assert counters & 0xFFFF == 2 and counters >> 16 == 0


@test("distributor: Emerald's search finds a Wonder Card distributor (serial 0x7F7F as in BPEJ, checksum good, hasCard) and connects")
def _():
    def script(game):
        found = yield from RFU.find_distributor(game)
        conn = yield from RFU.connect_to(game, found["id"])
        yield from game.command(0x24, [0x00000400, 0x12345678])      # a child's first data
        return found, conn
    hw, game, trace, st, counters = adapter_session(script, first_bit=True, program="adapter_lab")
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors)
    found, conn = hw.lg_result
    assert found["serial"] == 0x7F7F and found["checksum_ok"] and found["gname"]["hasCard"], found
    assert found["gname"]["language"] == 1 and found["gname"]["version"] == 3 and found["uname"][:3] == bytes([0xBE, 0xCF, 0xC9])
    assert conn["status"] == 0 and conn["child_id"] == 0x2C41 and conn["swap"] == 0x99660028, conn
    # lab session 16: each top-bit-1 word needs a flick, and flicks lose words - only the header may
    assert found["words"][0] >> 16 == 0x9966 and all(w < 0x80000000 for w in found["words"][1:]), found["words"]
    assert found["uname"][3:5] == bytes([0x00, 0xFF]) and found["gname"]["trainer"] == 0x5678, found
    names = [t for t, _ in trace]
    assert any(n.startswith("the game connected to the distributor") for n in names), names
    assert game.retries == 0, game.retries
    assert W.adapter_trace.last_capture == [(0x24, 2, 1, [0x00000400, 0x12345678])], W.adapter_trace.last_capture


@test("find serial: read-only ROM search reports 0x7F7D when the game has it, and says so when it doesn't")
def _():
    import struct as _s
    for planted, expect in ((True, "same distributor serial"), (False, "serial differs")):
        rom = bytearray(hwsim.make_game_rom(title=b"POKEMON EMER", code=b"BPEJ", tag=b"FLASH1M_V103"))
        for k in range(0, len(rom) - 3, 4):            # no accidental matches in the random data
            if _s.unpack_from("<I", rom, k)[0] in W.SERIAL_PATTERNS:
                rom[k] ^= 1
        _s.pack_into("<I", rom, 0x10000, 0x99660000)
        if planted:
            _s.pack_into("<I", rom, 0x20004, 0x00007F7D)
        hw, duo = run42(game=bytes(rom))
        hits, text = quiet(W.find_serial, duo)
        hw.stop(); clean(hw)                           # clean() fails on any save-chip write
        assert "POKEMON EMER (BPEJ)" in text and expect in text, text
        assert (0x08020004 in [h[0] for h in hits]) == planted and 0x08010000 in [h[0] for h in hits], hits


@test("serial sweep: a game that only takes serial 0x0151 is offered it, finds it again, and connects to it")
def _():
    JP = 0x0151
    def script(game):
        yield ("until_ready",)
        assert (yield from game.check_id()) == 0x8001
        for cmd, data in ((0x10, []), (0x17, [0x003C0420]), (0x16, [2, 0, 0, 0, 0, 0xFFFFFFFF]), (0x1C, [])):
            yield from game.command(cmd, data)
        seen = None
        for n in range(900):                        # Emerald: Task_CardOrNewsOverWireless
            hosts = RFU.parse_candidates((yield from game.command(0x1D)))
            assert len(hosts) == 3 and all(h["checksum_ok"] for h in hosts), hosts
            match = [h for h in hosts if h["serial"] == JP and h["gname"]["hasCard"]]
            if seen is None and match:
                seen = n                            # the chime; 120+ frames on, it looks for it again
            elif seen is not None and n - seen > 120 and match:
                yield from game.command(0x1E)
                yield from game.command(0x1F, [match[0]["id"]])
                return n - seen
        raise RFU.LinkError(f"serial {JP:#x} " + ("never offered" if seen is None else "not offered again"))
    hw, game, trace, st, counters = adapter_session(script, seconds=60, first_bit=True, sweep_serial=True)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors)
    assert hw.lg_result < 120 + 240, hw.lg_result     # within the 240 frames Emerald allows
    assert W.adapter_trace.last_sweep >> 16 == JP, hex(W.adapter_trace.last_sweep)
    assert "the game connected to serial 0x0151" in hw.text, hw.text


@test("find serial --deep: reads the ROM into memory, finds the accepted-serials list code points at, and writes nothing")
def _():
    import struct as _s
    rom = bytearray(hwsim.make_game_rom(title=b"POKEMON EMER", code=b"BPEJ", tag=b"FLASH1M_V103", size=0x60000))
    rom[0x40000:0x40010] = _s.pack("<4H", 0x0002, 0x1234, 0x0000, 0xFFFF) + bytes(8)   # the real list
    _s.pack_into("<I", rom, 0x20100, 0x08040000)                                       # code points at it
    _s.pack_into("<I", rom, 0x20200, 0x00001234)                                       # and compares with it
    rom[0x50002:0x50008] = _s.pack("<3H", 0x0002, 0x0777, 0xFFFF)                       # a decoy, unreferenced
    hw, duo = run42(game=bytes(rom))
    found, text = quiet(W.find_serial_deep, duo)
    hw.stop(); clean(hw)
    assert found and found[0][1:3] == (0x1234, 0x08040000) and found[0][0] == 2, found
    assert 0x0777 not in [f[1] for f in found], found
    assert "Most likely: 0x1234" in text and "BPEJ" in text, text


@test("every game: an English FireRed gets serial 0x7F7D and an English host, and receives a full-size (0x14C) card")
def _():
    path, card, script = test_wc3("English")
    hw, game, trace, st, counters = adapter_session(lambda g: gift_script(g, RFU.EN_FIRERED), first_bit=True,
                                                    seconds=120, wonder_card=path)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors, [t for t, _ in trace][-10:])
    child, client = hw.lg_result
    assert client.result == RFU.CLI_MSG_CARD_RECEIVED and client.saved_card == card and len(card) == 0x14C
    assert client.saved_ram_script == script
    assert W.adapter_trace.last_serial == 0x7F7D | RFU.EN_FIRERED << 16, hex(W.adapter_trace.last_serial)
    assert child.link_players[0][26] == 2, child.link_players[0]                  # host language: English
    names = [t for t, _ in trace]
    assert any(n.startswith("broadcast data (0x16)") and n.endswith("English FireRed") for n in names), names
    assert "Distributor serial used: 0x7f7d, for English FireRed" in hw.text and "Warning" not in hw.text


@test("every game: Japanese Emerald still gets 0x7F7F; --serial overrides the choice; a card in the wrong language is warned about")
def _():
    def script(game):
        return (yield from RFU.find_distributor(game, compat=RFU.JP_EMERALD))
    hw, game, trace, st, counters = adapter_session(script, first_bit=True)
    assert hw.lg_error is None and hw.lg_result["serial"] == 0x7F7F and hw.lg_result["gname"]["language"] == 1
    def forced(game):
        return (yield from RFU.find_distributor(game, compat=RFU.EN_EMERALD, serial=0x1234))
    hw, game, trace, st, counters = adapter_session(forced, first_bit=True, serial=0x1234)
    assert hw.lg_error is None and hw.lg_result["serial"] == 0x1234, (repr(hw.lg_error), game.errors)
    assert "fixed at 0x1234" in hw.text and hw.lg_result["gname"]["language"] == 2
    path, card, scr = test_wc3("Japanese")
    def english(game):
        return (yield from RFU.find_distributor(game, compat=RFU.EN_EMERALD))
    hw, game, trace, st, counters = adapter_session(english, first_bit=True, wonder_card=path)
    assert hw.lg_error is None and hw.lg_result["serial"] == 0x7F7D
    assert "the Wonder Card is Japanese, but the game is English Emerald" in hw.text, hw.text


def test_news(language="Japanese", with_crc=True, news_id=0x0042):
    """Made-up Wonder News in the layout the tool reads (test data only - not any real event)."""
    size = 202 if language == "Japanese" else 444
    news = bytearray((i * 11 + 3) & 0xFF for i in range(size))
    news[0:2] = news_id.to_bytes(2, "little")
    news[2], news[3] = 1, 2                                 # sendType, bgType
    data = (W.game_crc16(news).to_bytes(4, "little") if with_crc else b"") + bytes(news)
    path = os.path.join(tempfile.gettempdir(), f"test_news_{language[:2]}_{int(with_crc)}.bin")
    open(path, "wb").write(data)
    return path, bytes(news)


def news_script(compat=RFU.JP_EMERALD, saved_news=None, news_size=202):
    def script(game):
        found = yield from RFU.find_distributor(game, compat=compat, news=True)
        yield from RFU.connect_to(game, found["id"])
        yield from RFU.send_child_name(game, RFU.child_name_bytes())
        yield from RFU.wait_join_status(game)
        child = RFU.ChildLink()
        client = yield from RFU.receive_gift(game, child, client_kw=dict(saved_news=saved_news, news_size=news_size))
        return found, child, client
    return script


@test("Wonder News: Japanese Emerald finds a news distributor (hasNews), saves the news and is told 'news received'")
def _():
    path, news = test_news("Japanese")
    hw, game, trace, st, counters = adapter_session(news_script(), first_bit=True, seconds=120, wonder_card=None,
                                                    wonder_news=path)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors, [t for t, _ in trace][-10:])
    found, child, client = hw.lg_result
    assert found["gname"]["hasNews"] and not found["gname"]["hasCard"] and found["gname"]["activity"] == 22, found
    assert client.result == RFU.CLI_MSG_NEWS_RECEIVED and client.saved_news == news, (client.result, client.history)
    assert "Wonder News loaded" in hw.text and "the game saved the news" in hw.text, hw.text
    names = [t for t, _ in trace]
    assert any("next, Wonder News" in n for n in names) and any("next, the game's answer" in n for n in names), names


@test("Wonder News: a game that already has the same news keeps it and is told so; an English game takes 444-byte news")
def _():
    path, news = test_news("Japanese", with_crc=False)
    hw, game, trace, st, counters = adapter_session(news_script(saved_news=news), first_bit=True, seconds=120,
                                                    wonder_news=path)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors)
    found, child, client = hw.lg_result
    assert client.result == RFU.CLI_MSG_HAD_NEWS and client.saved_news == news, (client.result, client.history)
    assert "already had this news" in hw.text, hw.text
    path, news = test_news("English")
    hw, game, trace, st, counters = adapter_session(news_script(RFU.EN_FIRERED, news_size=444), first_bit=True,
                                                    seconds=120, wonder_news=path)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors)
    found, child, client = hw.lg_result
    assert client.result == RFU.CLI_MSG_NEWS_RECEIVED and client.saved_news == news and found["serial"] == 0x7F7D


@test("Wonder News files: bare or with the save's CRC, Japanese or English; bad sizes refused, damage and ID 0 reported")
def _():
    for language in ("Japanese", "English"):
        for with_crc in (True, False):
            path, news = test_news(language, with_crc)
            lang, got, problems = W.parse_wonder_news(open(path, "rb").read())
            assert got == news and problems == [] and lang.startswith(language[:2]), (lang, problems)
    data = bytearray(open(test_news("English")[0], "rb").read()); data[20] ^= 1
    assert "checksum" in " ".join(W.parse_wonder_news(bytes(data))[2])
    assert "ID is 0" in " ".join(W.parse_wonder_news(open(test_news("Japanese", news_id=0)[0], "rb").read())[2])
    try:
        W.parse_wonder_news(bytes(300)); raise AssertionError("300 bytes accepted")
    except ValueError as e:
        assert "202" in str(e) and "444" in str(e)


import link_game_model as LG


def ereader_run(game, event=None, seconds=60):
    """The DUO's e-Reader program against a simulated Ruby on the GBA link cable."""
    hw, duo = run42()
    hw.attach_mp_game(game.run(), game_master=game.master)      # the cable: who has the purple plug
    path = None
    if event is not None:
        path = os.path.join(tempfile.gettempdir(), "test_event.bin")
        open(path, "wb").write(event)
    r = quiet(W.ereader_session, duo, types.SimpleNamespace(ereader=seconds, mystery_event=path),
              poll_s=0.2, stop_when=lambda: hw.lg_done)
    hw.text = r[1]
    assert duo.hello() == 0x104
    hw.stop(); clean(hw)                              # nothing written to any cartridge
    return hw, r[0]


TEST_EVENT = bytes((i * 29 + 7) & 0xFF for i in range(700))    # made-up bytes, not a real event


@test("e-Reader: Ruby as master (purple plug) - handshake, link players, the event arrives intact, the link closes")
def _():
    game = LG.RubyMysteryEvent(master=True)
    hw, report = ereader_run(game, TEST_EVENT)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.log, hw.text[-600:])
    assert hw.lg_result == TEST_EVENT, (len(hw.lg_result or b""), hw.text[-400:])
    duo_lp = game.link_players[1]
    assert duo_lp["link_type"] == 0x5501 and duo_lp["language"] == LG.LANG_ENGLISH, duo_lp
    assert report["bad_checksums"] == 0 and report["game_language"] == 2, report
    for line in ("Mystery Event loaded", "link up (the DUO is player 1)", "got the game's link player: link type 0x5501, English",
                 "sent the Mystery Event", "the game closed the link"):
        assert line in hw.text, (line, hw.text)


@test("e-Reader: Ruby as slave (the DUO on the purple plug) - the DUO starts the transfers and the handshake; a Japanese game gets a Japanese partner")
def _():
    game = LG.RubyMysteryEvent(master=False, language=LG.LANG_JAPANESE)
    hw, report = ereader_run(game, TEST_EVENT[:300])
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.log, hw.text[-600:])
    assert hw.lg_result == TEST_EVENT[:300]
    assert game.link_players[0]["language"] == LG.LANG_JAPANESE and "the DUO is player 0" in hw.text, hw.text
    # paced like Ruby's own master: a transfer and the one nine later are a whole frame apart
    # (one handshake transfer, or one 9-transfer command cycle, per frame; a few lines of jitter
    # within a cycle are fine - back to back is ~4,000 blocks), and the game was
    # always ready for each transfer (lab session 24)
    st, frame = hw.mp_start_blocks, hw.mp_frame_blocks
    assert len(st) > 100 and all(st[i + 9] - st[i] >= frame - 300 for i in range(len(st) - 9)), \
        min(st[i + 9] - st[i] for i in range(len(st) - 9))
    assert hw.mp_missed == 0, hw.mp_missed


@test("e-Reader: with no event loaded only link players are exchanged (a dry run); oversize files are refused")
def _():
    game = LG.RubyMysteryEvent(master=True)
    game_gen = game.run(max_transfers=6000)
    hw, duo = run42()
    hw.attach_mp_game(game_gen)
    r = quiet(W.ereader_session, duo, types.SimpleNamespace(ereader=60, mystery_event=None), poll_s=0.2,
              stop_when=lambda: hw.lg_done)
    hw.stop(); clean(hw)
    assert isinstance(hw.lg_error, LG.LinkError) and "no Mystery Event" in str(hw.lg_error), repr(hw.lg_error)
    assert len(game.link_players) == 2 and "sent our link player" in r[1], r[1]
    big = os.path.join(tempfile.gettempdir(), "test_event_big.bin"); open(big, "wb").write(bytes(2005))
    try:
        W.load_event(None, big); raise AssertionError("2005 bytes accepted")
    except ValueError as e:
        assert "2004" in str(e)


@test("search check: four entries per answer, all with good checksums, so librfu restarts its search (0x1E, 0x1C)")
def _():
    def script(game):
        yield ("until_ready",)
        assert (yield from game.check_id()) == 0x8001
        for cmd, data in ((0x10, []), (0x17, [0x003C0420]), (0x16, [2, 0, 0, 0, 0, 0xFFFFFFFF]), (0x1C, [])):
            yield from game.command(cmd, data)
        for _ in range(30):
            hosts = RFU.parse_candidates((yield from game.command(0x1D)))
            if len(hosts) == 4:                     # librfu: findParentCount == RFU_CHILD_MAX
                yield from game.command(0x1E)       # rfu_REQ_endSearchParent, then start again
                yield from game.command(0x1C)
                return hosts
            yield ("delay", 16 * RFU.MS)
        raise RFU.LinkError(f"never four entries (last answer had {len(hosts)})")
    hw, game, trace, st, counters = adapter_session(script, first_bit=True, check_search=True)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors)
    hosts = hw.lg_result
    assert [h["id"] for h in hosts] == [0x7DA5, 0x7DA6, 0x7DA7, 0x7DA8] and all(h["checksum_ok"] for h in hosts), hosts
    names = [t for t, _ in trace]
    assert "stop searching (0x1E)" in names and names.count("start searching (0x1C)") == 2, names
    assert "Search check" in hw.text, hw.text


@test("distributor: after connecting, the game's name arrives over librfu's NI channel, every chunk acknowledged")
def _():
    name = RFU.child_name_bytes()
    def script(game):
        found = yield from RFU.find_distributor(game)
        conn = yield from RFU.connect_to(game, found["id"])
        sender = yield from RFU.send_child_name(game, name)
        return conn, sender
    hw, game, trace, st, counters = adapter_session(script, first_bit=True, seconds=40)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors, [t for t, _ in trace][-8:])
    conn, sender = hw.lg_result
    assert sender.state == RFU.SEND_SUCCESS and sender.frames_sent <= 12, (hex(sender.state), sender.frames_sent)
    child = W.adapter_trace.last_child
    assert child["received"] and child["raw"] == name, (child, name.hex())
    names = [t for t, _ in trace]
    assert any(n.startswith("the distributor received the game's name") for n in names), names[-6:]
    assert game.retries == 0, game.retries


@test("distributor: then the host tells the game it may join (RFU_STATUS_JOIN_GROUP_OK over NI) and the game receives it")
def _():
    def script(game):
        found = yield from RFU.find_distributor(game)
        conn = yield from RFU.connect_to(game, found["id"])
        yield from RFU.send_child_name(game, RFU.child_name_bytes())
        r = yield from RFU.wait_join_status(game)
        return r
    hw, game, trace, st, counters = adapter_session(script, first_bit=True, seconds=40)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors, [t for t, _ in trace][-8:])
    r = hw.lg_result
    assert r.state == RFU.RECV_SUCCESS and bytes(r.buf) == b"\x05" and r.type == 0 and r.payload == 5, (hex(r.state), r.buf)
    names = [t for t, _ in trace]
    assert any(n.startswith("the distributor told the game it may join") for n in names), names[-6:]
    assert game.retries == 0, game.retries


def test_wc3(language="Japanese"):
    """A made-up Wonder Card in .wc3 layout (test data only - not any real event)."""
    size = 0xA4 if language == "Japanese" else 0x14C
    card = bytearray(size)
    card[0:2] = (0x0123).to_bytes(2, "little")             # flagId
    card[2:4] = (0x0019).to_bytes(2, "little")             # iconSpecies
    card[4:8] = (0x00C0FFEE).to_bytes(4, "little")         # idNumber
    card[8] = 0 | 1 << 2 | 1 << 6                          # type 0, bgType 1, sendType 1
    for i in range(10, size):
        card[i] = (i * 7) & 0xFF
    script = bytes([0x33, 0x05, 0x0A, 0x01]) + bytes((i * 13 + 5) & 0xFF for i in range(996))
    wc3 = (W.game_crc16(card).to_bytes(4, "little") + bytes(card) + bytes(0x50)
           + W.game_crc16(script).to_bytes(4, "little") + script)           # valid, as the game checks
    path = os.path.join(tempfile.gettempdir(), f"test_card_{language[:2]}.wc3")
    open(path, "wb").write(wc3)
    return path, bytes(card), script


def gift_script(game, compat=RFU.JP_EMERALD):
    found = yield from RFU.find_distributor(game, compat=compat)
    conn = yield from RFU.connect_to(game, found["id"])
    yield from RFU.send_child_name(game, RFU.child_name_bytes())
    yield from RFU.wait_join_status(game)
    child = RFU.ChildLink()
    client = yield from RFU.receive_gift(game, child)
    return child, client


@test("distributor: link players exchanged, then Mystery Gift delivers the Wonder Card and RAM script; the link closes")
def _():
    path, card, script = test_wc3()
    hw, game, trace, st, counters = adapter_session(gift_script, first_bit=True, seconds=120, wonder_card=path)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors, [t for t, _ in trace][-10:])
    child, client = hw.lg_result
    assert child.player_count == 2 and child.multiplayer_id == 1, (child.player_count, child.multiplayer_id)
    assert child.link_players[0][8:11] == bytes([0xBE, 0xCF, 0xC9]), child.link_players[0]
    assert client.result == RFU.CLI_MSG_CARD_RECEIVED, (client.result, client.history)
    assert client.saved_card == card and client.saved_ram_script == script
    link = W.adapter_trace.last_link
    assert link["player_ok"] and link["trainer_id"] == 0x0003D4C1 and link["mg_step"] == 6 and not link["mg_errors"], link
    assert link["game_data"] == bytes(range(0x40)), link["game_data"].hex()
    names = [t for t, _ in trace]
    assert any(n.startswith("link players exchanged") for n in names), names
    assert any(n.startswith("the game is ready to close the link") for n in names), names
    assert "Wonder Card loaded" in hw.text and game.retries == 0, (hw.text[:400], game.retries)


@test("distributor: packets lost on the way (every 7th) are sent again until the game shows it got them; the card still arrives intact")
def _():
    path, card, script = test_wc3()
    def script_(game):
        found = yield from RFU.find_distributor(game)
        yield from RFU.connect_to(game, found["id"])
        yield from RFU.send_child_name(game, RFU.child_name_bytes())
        yield from RFU.wait_join_status(game)
        child = RFU.ChildLink()
        client = yield from RFU.receive_gift(game, child, drop_every=7)
        return child, client
    hw, game, trace, st, counters = adapter_session(script_, first_bit=True, seconds=150, wonder_card=path)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors, [t for t, _ in trace][-10:])
    child, client = hw.lg_result
    assert child.dropped > 20 and client.result == RFU.CLI_MSG_CARD_RECEIVED, (child.dropped, client.result)
    assert client.saved_card == card and client.saved_ram_script == script
    assert W.adapter_trace.last_link["resent"] >= child.dropped - 1, (W.adapter_trace.last_link, child.dropped)


@test("distributor: without a Wonder Card the game is told there is nothing to send, and the link closes tidily")
def _():
    hw, game, trace, st, counters = adapter_session(gift_script, first_bit=True, seconds=120)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors, [t for t, _ in trace][-10:])
    child, client = hw.lg_result
    assert client.result == RFU.CLI_MSG_NOTHING_SENT and client.saved_card is None, (client.result, client.history)
    assert W.adapter_trace.last_link["mg_step"] == 4, W.adapter_trace.last_link


@test("wireless adapter: the game gives up half-way through a command and starts it again; the adapter follows")
def _():
    def script(game):
        yield ("until_ready",)
        assert (yield from game.check_id()) == 0x8001
        yield from game.command(0x10)
        # 0x16 with six data words, abandoned after two, once the adapter is ready for the third;
        # librfu then starts the whole command again 130 ms later (STWI_restart_Command)
        got = yield ("xfer", 0x99660616)
        for w in (1, 2):
            yield ("wait_si", 1, 80 * RFU.MS); yield ("so", 1); yield ("wait_si", 0, 80 * RFU.MS); yield ("so", 0)
            got = yield ("xfer", w)
        yield ("wait_si", 1, 80 * RFU.MS); yield ("so", 1); yield ("wait_si", 0, 80 * RFU.MS); yield ("so", 0)
        yield ("delay", 130 * RFU.MS)
        again = yield from game.command(0x16, [1, 2, 3, 4, 5, 6])
        return again, (yield from game.command(0x13))
    hw, game, trace, st, counters = adapter_session(script)
    assert hw.lg_error is None, repr(hw.lg_error)
    assert hw.lg_result == ([0x99660096], [0x99660193, 0]), hw.lg_result
    names = [t for t, _ in trace]
    assert any(n.startswith("the game stopped answering") for n in names[2:4]), names
    assert names[-2].startswith("broadcast data (0x16) 00000001") and names[-1].startswith("status (0x13)"), names
    assert counters >> 16 == 1 and game.retries == 0, (counters, game.retries)


@test("wireless adapter: with the first-bit behaviour measured on real hardware, Emerald's search completes cleanly")
def _():
    def script(game):
        acks = yield from RFU.mystery_gift_search(game, polls=6)
        yield ("delay", 40 * RFU.MS)
        acks["sleep"] = yield from RFU.go_to_sleep(game)
        return acks
    hw, game, trace, st, counters = adapter_session(script, first_bit=True)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors, [t for t, _ in trace][-6:])
    acks = hw.lg_result
    assert acks[0x10] == [0x99660090] and acks[0x1D][:2] == [0x9966079D, 0x7DA5] and acks["wait"] == 0x99660027
    assert acks[0x13] == [0x99660193, 0] and acks["sleep"] == [0x996600BD]
    assert game.retries == 0 and not game.errors, (game.retries, game.errors)
    assert counters >> 16 == 0 and "Over-long answers" not in hw.text


@test("wireless adapter: the 'ready' flick tunes its own width into the window the game needs (too narrow: unseen, too wide: first bit wrong)")
def _():
    def script(game):
        acks = yield from RFU.mystery_gift_search(game, polls=4)
        acks["sleep"] = yield from RFU.go_to_sleep(game)
        return acks
    for window in ((6, 9), (11, 12), (0, 0)):       # climb, climb to a narrow window, back off
        hw, game, trace, st, counters = adapter_session(script, first_bit=True, flick_window=window, seconds=60,
                                                        program="adapter_lab")
        assert hw.lg_error is None and hw.lg_done, (window, repr(hw.lg_error), game.errors)
        tuning = W.adapter_trace.last_tuning
        lo, hi = window
        assert lo <= tuning["width"] <= hi, (window, tuning["width"], tuning["rows"])
        assert any(r[2] for r in tuning["rows"]) and tuning["scope"], tuning


@test("wireless adapter: with a one-bit lag the simulator reproduces lab session 11 - each command fails three times")
def _():
    def script(game):
        yield ("until_ready",)
        assert (yield from game.check_id()) == 0x8001
        try:
            yield from game.command(0x3D)
            return "accepted"
        except RFU.LinkError:
            return "failed"
    hw, game, trace, st, counters = adapter_session(script, lag_bits=1)
    names = [t for t, _ in trace]
    assert hw.lg_result == "failed" and game.retries == 2, (hw.lg_result, game.retries)
    assert names[0].startswith("ID exchange") and names.count("the game stopped answering in the middle of a command (0x3d: no handshake after its command word)") == 3, names


@test("2 MHz probe: on perfect wires the lagged answer is refused and the unshifted ones are accepted")
def _():
    def script(game):
        yield ("until_ready",)
        assert (yield from game.check_id()) == 0x8001
        for cmd in (0x10, 0x13):
            try:
                yield from game.command(cmd)
            except RFU.LinkError:
                pass
        return "done"
    hw, game, trace, st, counters = adapter_session(script, program="adapter_probe")
    (lag, first, plain) = hw.probe
    assert lag[1] >= 1 and lag[2] == 0 and first[2] >= 1, hw.probe     # the same word, just SO high


@test("2 MHz probe: with a one-bit lag (as lab session 11 suggests) only the lagged answer is accepted")
def _():
    def script(game):
        yield ("until_ready",)
        assert (yield from game.check_id()) == 0x8001
        try:
            yield from game.command(0x10)
        except RFU.LinkError:
            pass
        return "done"
    hw, game, trace, st, counters = adapter_session(script, program="adapter_probe", lag_bits=1)
    (lag, first, plain) = hw.probe
    assert lag[1] >= 1 and lag[2] >= 1 and first[2] == 0 and plain[2] == 0, hw.probe
    assert "consistent one-bit lag" in hw.text, hw.text[-600:]


@test("wireless adapter: an echo of our own acknowledgement after sleep (session 16) is ignored, not answered")
def _():
    def script(game):
        yield ("until_ready",)
        yield from RFU.go_to_sleep(game)
        yield ("delay", 30 * RFU.MS)
        yield ("xfer", 0x996600BD, 2)                # the game clocks once with its stale data
        yield ("delay", 30 * RFU.MS)
        return (yield from RFU.mystery_gift_search(game, polls=4))
    hw, game, trace, st, counters = adapter_session(script, first_bit=True)
    assert hw.lg_error is None and hw.lg_done, (repr(hw.lg_error), game.errors)
    names = [t for t, _ in trace]
    assert "unknown command (0xBD)" not in names, names           # the echo isn't treated as a command
    assert "unknown command (0x99)" in names, names               # a real unknown command still is
    assert "start searching (0x1C)" in names and counters >> 16 == 0, names


@test("card checks: a synthetic .wc3 with good CRC-16s passes; damage to the card or script, or a missing marker, is reported")
def _():
    for size, total in ((0xA4, 1252), (0x14C, 1420)):
        card = bytearray(size); card[0:2] = (1002).to_bytes(2, "little")
        script = bytearray(1000); script[0:4] = b"\x33\xff\xff\xff"; script[4:8] = b"\x02\x00\x00\x00"
        def build(card, script):
            return (W.game_crc16(card).to_bytes(4, "little") + bytes(card) + bytes(0x50)
                    + W.game_crc16(script).to_bytes(4, "little") + bytes(script))
        good = build(card, script)
        assert len(good) == total and W.check_wc3(good) == [], W.check_wc3(good)
        bad_card = bytearray(good); bad_card[10] ^= 1
        assert "card's checksum" in " ".join(W.check_wc3(bytes(bad_card)))
        bad_script = bytearray(good); bad_script[-1] ^= 1
        assert "RAM script's checksum" in " ".join(W.check_wc3(bytes(bad_script)))
        no_magic = bytearray(script); no_magic[0] = 0
        assert "marker" in " ".join(W.check_wc3(build(card, no_magic)))
    assert W.game_crc16(b"") == (~0x1121 & 0xFFFF)                 # the game's starting value


@test("wireless adapter: with nothing on the cable the trace stays empty and the program stops cleanly")
def _():
    hw, game, trace, st, counters = adapter_session(None, seconds=2, attach=False)
    assert trace == [] and st >> 16 == 0


@test("v4.2: everything else still works - fast save, cartridge report, restart; older firmware refuses programs")
def _():
    hw, duo = run42()
    path, data, info, text, _ = gba_save(duo)
    duo.restart(wait_s=20)
    hw.stop(); clean(hw)
    assert data == RUBY and hw.restarts == 1
    hw, duo = device()                                 # v4.1
    try:
        W.run_program(duo, "adapter_detect"); raise AssertionError("v4.1 ran a program")
    except M.DuoError as e:
        assert "v4.2" in str(e)
    hw.stop(); clean(hw)


@test("v4.2 update: installing v4.2 over the official firmware and over v4.1, flash simulated")
def _():
    saved, sys.argv = sys.argv, sys.argv[:1]
    try:
        import emu_update_test as U
    finally:
        sys.argv = saved
    for current in (OFFICIAL, FW):
        U.OFFICIAL = current
        ok, text = quiet(U.main, paths.build_file("duo_1.06_reckless_v4.2.bin"))
        assert ok, text
    crc, payload = M.check_update_file(paths.build_file("duo_1.06_reckless_v4.2.bin"))
    assert payload == FW42 and "v4.2" in M.KNOWN_FIRMWARE.get(crc, "")

if __name__ == "__main__":
    pick = sys.argv[1] if len(sys.argv) > 1 else ""
    passed = failed = skipped = 0
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
        except Skip as e:
            skipped += 1
            for hw in LIVE:
                if hw.thread.is_alive():
                    hw.stop()
            LIVE.clear()
            print(f"[SKIP] {name}  ({e})", flush=True)
        except Exception as e:
            failed += 1
            for hw in LIVE:
                if hw.thread.is_alive():
                    hw.stop()
            print(f"[FAIL] {name}\n       {type(e).__name__}: {str(e)[:600]}", flush=True)
            if not isinstance(e, AssertionError):
                traceback.print_exc(limit=3)
    print(f"\n{passed} passed, {failed} failed" + (f", {skipped} skipped" if skipped else "") + f" in {time.time() - t_all:.0f}s")
    sys.exit(1 if failed else 0)
