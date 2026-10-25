import sys, types, tempfile, time, io, contextlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))); import paths
import hwsim, maxduo_saves as M, zlib
M.SHORT_TIMEOUT = 800
ruby = __import__("fixtures").make_gen3_save()
for label, fwfile in [("installed v3", "installed_fw_v3.bin"), ("new v3.1", "fw_v3.bin")]:
    hw = hwsim.Hw(open(fwfile, "rb").read(), hwsim.make_game_rom(), "sanyo", ruby).start()
    duo = M.MaxDuo(hwsim.Transport(hw)); duo.hello()
    args = types.SimpleNamespace(out=tempfile.mkdtemp(), map=None, save_type=None, slow=False)
    t = time.time(); buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            path, data, info = M.gba_save_v3(duo, args)
        print(f"{label}: CRC {zlib.crc32(data):08X} in {time.time()-t:.1f}s, packets sent by GBA {hw.sent_packets}")
    except Exception as e:
        print(f"{label}: FAILED {type(e).__name__} {e}")
    print("  link errors:", hw.link_errors, " bad:", hw.bad, " violations:", hw.violations, " save writes:", hw.save_writes)
    print("  GBA log tail:", hw.log[-6:])
    hw.done.set(); hw.h2d.put(b"CBW\x02"+bytes(4)); hw.thread.join(10)
    print(buf.getvalue()[-600:])
