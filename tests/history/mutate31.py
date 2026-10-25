"""Plant one alternation bug at a time in v3.1 and check a full save catches it."""
import sys, types, tempfile, io, contextlib, importlib, zlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))); import paths
import hwsim, maxduo_saves as M
M.SHORT_TIMEOUT = 800
RUBY = __import__("fixtures").make_gen3_save()
SRC = open("build_v3.py").read()
MUTANTS = {
    "no wait after start packet": ("""    bl   {a['send_sbw']:#x}
    movs r0, #{ST_CONT:#x}
    bl   {a['wait_pkt']:#x}
    cmp  r0, #0
    bne  st_timeout
    movs r5, #0""", """    bl   {a['send_sbw']:#x}
    movs r5, #0"""),
    "no upload acks": ("""    movs r0, #{UP_ACK:#x}
    subs r1, r4, r6
    bl   {a['send_sbw']:#x}
    b    up_pkt""", """    b    up_pkt"""),
    "data packets without waiting": ("""    movs r0, #{ST_CONT:#x}
    bl   {a['wait_pkt']:#x}
    cmp  r0, #0
    beq  sb_send""", """    b    sb_send"""),
    "NAK ignored mid-block": ("""    cmp  r0, #1
    bne  sb_tmo
    ldrb r0, [r4, #0x1B]""", """    b    sb_send
    ldrb r0, [r4, #0x1B]"""),
    "unexpected packet ignored at block end": ("""    cmp  r0, #0
    bne  st_again
    ldr  r1, [r4, #0x0C]""", """    cmp  r0, #0
    bne  st_wait
    ldr  r1, [r4, #0x0C]"""),
}
for name, (old, new) in [(n, MUTANTS[n]) for n in MUTANTS if n == sys.argv[1]]:
    assert SRC.count(old) == 1, name
    open("build_v3_mut.py", "w").write(SRC.replace(old, new).replace('open("duo_1.06_reckless_v3.1.bin"', 'open("mut.bin"'))
    import build_v3_mut; importlib.reload(build_v3_mut)
    fw = build_v3_mut.build("fw_mut.bin")
    faults = {"corrupt_at": {1, 30}} if ("NAK" in name or "unexpected" in name) else None
    hw = hwsim.Hw(fw, hwsim.make_game_rom(), "sanyo", RUBY, faults).start()
    duo = M.MaxDuo(hwsim.Transport(hw)); duo.hello()
    args = types.SimpleNamespace(out=tempfile.mkdtemp(), map=None, save_type=None, slow=False)
    caught = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            path, data, info = M.gba_save_v3(duo, args)
        if data != RUBY: caught = "wrong data"
        elif hw.link_errors: caught = "link errors: " + hw.link_errors[0][:60]
    except Exception as e:
        caught = f"{type(e).__name__}: {str(e)[:70]}" + (f" | link: {hw.link_errors[0][:50]}" if hw.link_errors else "")
    hw.done.set(); hw.h2d.put(b"CBW\x02" + bytes(4)); hw.thread.join(20)
    print(f"{'CAUGHT' if caught else 'MISSED'}  {name}: {caught}", flush=True)
