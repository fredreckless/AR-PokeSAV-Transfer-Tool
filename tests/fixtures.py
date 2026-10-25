"""Synthetic test data, so the tests need no real saves or game ROMs."""
import random, struct

_CHARS = {" ": 0x00}
for _i, _c in enumerate("0123456789"):
    _CHARS[_c] = 0xA1 + _i
for _i in range(26):
    _CHARS[chr(65 + _i)], _CHARS[chr(97 + _i)] = 0xBB + _i, 0xD5 + _i
SIZES = {0: 3884, 13: 2000}


def _text(s, n):
    b = bytes(_CHARS[c] for c in s) + b"\xff"
    return (b + b"\xff" * n)[:n]


def make_gen3_save(trainer="May", tid=12345, sid=4321, save_no=337, hours=12, minutes=34, seed=1):
    """A 128 KB Pokemon Gen 3 save with two valid copies (slots A and B) whose
    sections have correct checksums. Mostly zeros and FF, like a real early-game
    save, with some random data so compression has something to do."""
    rnd = random.Random(seed)
    data = bytearray(b"\xff" * 0x20000)
    for slot, idx in ((0, save_no), (1, save_no - 1)):
        for k in range(14):
            sec = (k + idx) % 14                       # real games rotate the sections
            body = bytearray(0xFF4)
            n = SIZES.get(sec, 3968)
            for _ in range(2):                         # a few random-looking stretches (real early saves compress to ~5%)
                at, ln = rnd.randrange(0, n - 200), rnd.randrange(16, 100)
                body[at:at + ln] = bytes(rnd.getrandbits(8) for _ in range(ln))
            body[n:] = bytes(len(body) - n)
            assert len(body) == 0xFF4
            if sec == 0:
                body[0:8] = _text(trainer, 8)
                struct.pack_into("<HHHBB", body, 0x0A, tid, sid, hours, minutes, 0)
            c = sum(struct.unpack_from(f"<{n // 4}I", body, 0)) & 0xFFFFFFFF
            ck = ((c >> 16) + c) & 0xFFFF
            blk = bytes(body) + struct.pack("<HHII", sec, ck, 0x08012025, idx)
            o = slot * 0xE000 + k * 0x1000
            data[o:o + 0x1000] = blk
    return bytes(data)
