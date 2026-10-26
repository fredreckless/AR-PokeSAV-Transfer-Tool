"""
build_firmware.py - build Reckless v4.1 from Datel's official 1.06 update.

    python build_firmware.py PATH_TO_DATEL_1.06_UPDATE  [--all]

The patched firmware is not distributed: it contains Datel's code. Instead you
supply Datel's own 1.06 update file (its CRC-32 must be 1767E827) and this
script applies the Reckless changes to it, writing

    build/duo_1.06_reckless_v4.1.bin     (update CRC A5303402)
    build/duo_1.06_reckless_v4.2.bin     (v4.1 + run a program from RAM)

which maxduo_saves.py --update can install. With --all it also builds the
earlier versions the test suite uses (the lab edition, v3.1 and the original v3).
Needs: pip install keystone-engine capstone
"""
import os, shutil, struct, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OFFICIAL_CRC = 0x1767E827
EXPECTED = {"duo_1.06_reckless_v4.1.bin": 0xA5303402,
            "duo_1.06_reckless_v4.2.bin": 0xDD59ED8A}


def load_official(path):
    blob = open(path, "rb").read()
    if len(blob) == 191176 and struct.unpack_from("<I", blob)[0] == zlib.crc32(blob[4:]):
        fw = blob[4:]                       # the update file: CRC header + image
    else:
        fw = blob                           # a bare image
    if zlib.crc32(fw) != OFFICIAL_CRC:
        sys.exit(f"This isn't Datel's official 1.06 update (CRC {zlib.crc32(fw):08X}, expected {OFFICIAL_CRC:08X}).")
    return fw


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    fw = load_official(args[0])
    out = os.path.join(HERE, "build")
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, "fw.bin"), "wb").write(fw)
    sys.path.insert(0, HERE)
    os.chdir(out)                            # the build scripts read fw.bin and write stages here
    import build_v4
    image = build_v4.build("fw_v4.bin")
    name = "duo_1.06_reckless_v4.1.bin"
    crc = zlib.crc32(image)
    open(name, "wb").write(struct.pack("<I", crc) + image)
    print(f"Built build/{name}: update CRC {crc:08X}"
          + (" - matches the released v4.1" if crc == EXPECTED[name] else " - DIFFERENT from the released v4.1!"))
    # v4.2 (v4.1 + run a program from RAM) - built in a fresh process, because
    # build_v4 adjusts settings in the earlier stages' modules as it builds.
    import subprocess
    r = subprocess.run([sys.executable, "-c", "import build_v42; build_v42.build('fw_v42.bin')"],
                       cwd=out, env=dict(os.environ, PYTHONPATH=HERE), capture_output=True, text=True)
    if r.returncode:
        sys.exit("v4.2 build failed:\n" + r.stderr[-2000:])
    img42 = open("fw_v42.bin", "rb").read()
    name42 = "duo_1.06_reckless_v4.2.bin"
    crc42 = zlib.crc32(img42)
    open(name42, "wb").write(struct.pack("<I", crc42) + img42)
    print(f"Built build/{name42}: update CRC {crc42:08X}"
          + (" - matches the released v4.2" if crc42 == EXPECTED.get(name42) else " - not a released build"))
    if "--all" in sys.argv:
        import importlib, build_patch2, build_reckless, build_lab
        for mod in (build_patch2, build_reckless, build_lab):
            importlib.reload(mod)            # build_v4 adjusts some settings in these; start clean
        v2, _ = build_patch2.build("fw.bin", "fw_patched2.bin")
        rk, _, _ = build_reckless.build(v2, "fw_reckless.bin")
        build_lab.build(rk, "fw_lab.bin")
        for mod in (build_patch2, build_reckless, build_lab):
            importlib.reload(mod)
        import build_v3
        build_v3.build("fw_v3.bin")
        for mod in (build_patch2, build_reckless, build_lab):
            importlib.reload(mod)
        sys.path.insert(0, os.path.join(HERE, "history"))
        import build_v3_original                      # the first v3: its transfers stall on real hardware
        old = build_v3_original.build("fw_v3_original.bin")
        open("duo_1.06_reckless_v3_original.bin", "wb").write(struct.pack("<I", zlib.crc32(old)) + old)
        print("Also built, for the tests: fw_lab.bin (lab edition), fw_v3.bin (v3.1) and "
              f"fw_v3_original.bin (the first v3, CRC {zlib.crc32(old):08X}).")
    if crc != EXPECTED[name]:
        sys.exit(1)


if __name__ == "__main__":
    main()
