import sys, types, time, zlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))); import paths
import hwsim, maxduo_saves as M
RUBY = __import__("fixtures").make_gen3_save()
hw = hwsim.Hw(open("fw_v3.bin", "rb").read(), hwsim.make_game_rom(), "sanyo", RUBY).start()
duo = M.MaxDuo(hwsim.Transport(hw))
print("hello:", hex(duo.hello()), "v3:", duo.is_v3())
args = types.SimpleNamespace(out=__import__("tempfile").mkdtemp(), map=None, save_type=None, slow=False)
t = time.time()
M.gba_save_v3(duo, args)
hw.stop()
import glob
f = sorted(glob.glob(args.out + "/**/*.sav", recursive=True))[-1]
print("\nfile CRC %08X  matches Ruby: %s" % (zlib.crc32(open(f, "rb").read()), open(f, "rb").read() == RUBY))
print("violations:", hw.violations, "problems:", hw.bad)
print("--- DUO screen log ---"); print("\n".join(hw.log))
