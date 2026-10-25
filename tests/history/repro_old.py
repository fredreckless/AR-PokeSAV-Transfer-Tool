"""Reproduce the user's real-hardware failure: installed v3 firmware + v3 tool."""
import sys, types, tempfile, importlib.util, io, contextlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))); import paths
import hwsim
spec = importlib.util.spec_from_file_location("old", __import__("os").path.join(paths.HERE, "history", "maxduo_saves_v3_original.py"))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
M.SHORT_TIMEOUT = 800
fw = open("installed_fw_v3.bin", "rb").read()
ruby = __import__("fixtures").make_gen3_save()
hw = hwsim.Hw(fw, hwsim.make_game_rom(), "sanyo", ruby).start()
duo = M.MaxDuo(hwsim.Transport(hw))
print("hello", hex(duo.hello()))
args = types.SimpleNamespace(out=tempfile.mkdtemp(), map=None, save_type=None, slow=False)
try:
    M.gba_save_v3(duo, args)
    print("RESULT: save worked (not a reproduction)")
except Exception as e:
    print("RESULT: failed:", type(e).__name__, e)
import time; time.sleep(20)
print("GBA log:", hw.log)
print("link errors:", hw.link_errors)
hw.done.set()
