"""
"Reckless Modding 2026" edition: patch v2 plus cosmetic, DATA-ONLY changes.

No code is modified here - only:
  * new strings placed in the empty padding area,
  * pointers in the four language tables and one System Test pointer,
  * the user colour-scheme table (schemes 0-13) and two start-up colour
    constants.
Every colour keeps its original brightness, so text contrast is unchanged.
"""
import colorsys, struct, zlib
from build_patch import BASE, PAD_START, PAD_END
import build_patch2

STRINGS_AT = 0x0801EC00
LANG_TABLES = {"EN": 0x0802BB70, "FR": 0x0802BE94, "DE": 0x0802C0B8, "IT": 0x0802C2DC}
SCHEMES = 0x0800EADC
N_SCHEMES = 14                      # rows 0-13 are the selectable user schemes
BOOT_ACCENT = 0x08000FA0            # literal 0x50C0 (blue) in start-up palette setup
BOOT_ACCENT_LIGHT = 0x08000FA4      # literal 0x7316 (light blue)
VERSION_PTR = 0x0800B238            # System Test's pointer to "V 1.06"

TEXT = {
    0:  ("This product is not licensed or endorsed by \nNintendo Entertainment \nof \nNorth America",
         "RECKLESS MODDING 2026\n \nAction Replay MAX DUO\nGBA save edition"),
    36: ("THE HOST PC IS IN CONTROL", "GBA SAVE PATCH V2 READY"),
    60: (None, "RECKLESS MODDING 2026"),          # title differs per language; replace all
}
VERSION = "V1.06 RM"
COLOURS = True                      # v4 sets this False to keep Datel's colours


def bgr555_to_rgb(c):
    return tuple(((c >> s) & 31) / 31 for s in (0, 5, 10))


def rgb_to_bgr555(r, g, b):
    q = lambda x: max(0, min(31, round(x * 31)))
    return q(r) | q(g) << 5 | q(b) << 10


def reckless(c, row_hue):
    """Rotate hue / tint greys, keeping lightness so contrast is preserved."""
    h, l, s = colorsys.rgb_to_hls(*bgr555_to_rgb(c))
    if s < 0.12 or l > 0.93:                        # whites and greys: light tint
        s2 = 0.0 if l < 0.05 else min(0.55, 0.35 + s)
        h2 = row_hue
    else:                                           # accents: new hue, a touch more vivid
        h2 = (h + 0.42) % 1.0
        s2 = min(1.0, s * 1.15)
    return rgb_to_bgr555(*colorsys.hls_to_rgb(h2, l, s2))


def build(src_fw, out_fw):
    fw = bytearray(src_fw)
    rd = lambda a: struct.unpack_from("<I", fw, a - BASE)[0]
    wr = lambda a, v: struct.pack_into("<I", fw, a - BASE, v)

    # --- strings into padding
    cursor = STRINGS_AT
    def put(text):
        nonlocal cursor
        raw = text.encode("latin-1") + b"\0"
        o = cursor - BASE
        assert cursor + len(raw) < PAD_END and all(x == 0xFF for x in fw[o:o + len(raw)])
        fw[o:o + len(raw)] = raw
        addr, cursor = cursor, cursor + len(raw)
        return addr

    def cstr(a):
        o = a - BASE
        return fw[o:fw.index(b"\0", o)].decode("latin-1")

    for msg, (expect, new) in TEXT.items():
        addr = put(new)
        for lang, table in LANG_TABLES.items():
            slot = table + 4 * msg
            old = cstr(rd(slot))
            if expect is not None:
                assert old == expect, (lang, msg, old)
            else:
                assert old.startswith("ACTION REPLAY GBA / MAX DRIVE DS"), (lang, old)
            assert len(new) <= len(old), "new text longer than original"
            wr(slot, addr)

    assert cstr(rd(VERSION_PTR)) == "V 1.06"
    wr(VERSION_PTR, put(VERSION))

    # --- colours (v4 keeps Datel's original colours: COLOURS = False)
    old_rows, new_rows = [], []
    if not COLOURS:
        open(out_fw, "wb").write(fw)
        return bytes(fw), old_rows, new_rows
    hues = [0.97, 0.10, 0.55, 0.80, 0.33, 0.62, 0.0, 0.70, 0.90, 0.15, 0.45, 0.05, 0.25, 0.85]
    for i in range(N_SCHEMES):
        o = SCHEMES - BASE + 12 * i
        row = list(struct.unpack_from("<6H", fw, o))
        new = [reckless(c, hues[i]) for c in row]
        if i == 0:   # default scheme: Reckless crimson
            new[4] = rgb_to_bgr555(0.69, 0.04, 0.16)
            new[5] = rgb_to_bgr555(1.00, 0.78, 0.82)
        struct.pack_into("<6H", fw, o, *new)
        old_rows.append(row); new_rows.append(new)

    assert rd(BOOT_ACCENT) == 0x50C0 and rd(BOOT_ACCENT_LIGHT) == 0x7316
    wr(BOOT_ACCENT, new_rows[0][4])
    wr(BOOT_ACCENT_LIGHT, new_rows[0][5])

    open(out_fw, "wb").write(fw)
    return bytes(fw), old_rows, new_rows


if __name__ == "__main__":
    base, _ = build_patch2.build("fw.bin", "fw_patched2.bin")
    fw, old_rows, new_rows = build(base, "fw_reckless.bin")
    crc = zlib.crc32(fw)
    open("duo_1.06_reckless.bin", "wb").write(struct.pack("<I", crc) + fw)
    print(f"Reckless edition built, update CRC {crc:08X}")
    diff = [i for i in range(len(base)) if base[i] != fw[i]]
    spans = []
    for i in diff:
        if spans and i <= spans[-1][1] + 1:
            spans[-1][1] = i
        else:
            spans.append([i, i])
    print("changed vs patch v2:", [(hex(BASE + a), b - a + 1) for a, b in spans])
