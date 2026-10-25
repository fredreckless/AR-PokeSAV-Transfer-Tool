"""Derive the cartridge report's CRC-32 correction constants (one per ROM size).

Through the DUO, 0x08000020-0x0800009F always reads as zeros except 0x9C-0x9F
(F0 0F 00 00), because the Actel overlays its registers there. CRC-32 is linear:
crc(real) = crc(as_read) ^ crc(diff) ^ crc(zeros), for equal lengths, where diff
is real XOR as_read (non-zero only in that window). So one constant per ROM size
corrects a checksum computed through the DUO. The logo bytes (verified against
devkitPro gbafix) are used only here; the tool ships the four-byte constants.
"""
import zlib, sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from hwsim import LOGO, OVERLAY, MAPREG_READ

def constants():
    real = bytes(0x20) + LOGO[0x1C:]                 # bytes 0x20..0x9F as on the chip (offsets from 0)
    seen = bytes(0x20) + bytes(0x7C) + MAPREG_READ   # as read through the DUO
    diff = bytes(a ^ b for a, b in zip(real, seen))
    out = {}
    for mb in (1, 2, 4, 8, 16, 32):
        L = mb << 20
        d = diff + bytes(L - len(diff))
        out[L] = zlib.crc32(d) ^ zlib.crc32(bytes(L))
    return out

if __name__ == "__main__":
    for L, k in constants().items():
        print(f"    0x{L:08X}: 0x{k:08X},")
