"""Made-up e-Cards for the tests: the reverse of duo_events.py's card reader. Everything here is
synthetic - no Nintendo data. A card is built as: a program (made-up bytes around a Mystery Event
script), vpk0-compressed (both of the format's offset methods), split into long strips, each with
a 48-byte strip header holding "NINTENDO" and a 33-byte title, then cut into Reed-Solomon codewords
and interleaved into 104-byte blocks, as duo_events.read_strip expects."""
import struct
import duo_events as W


class Bits:
    def __init__(self):
        self.bits = []

    def put(self, value, n):
        self.bits += [value >> (n - 1 - i) & 1 for i in range(n)]

    def bytes(self):
        b = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int("".join(map(str, b[i:i + 8])), 2) for i in range(0, len(b), 8))


def vpk0_encode(data, method=0, back_bits=12, len_bits=6):
    """A simple vpk0: each Huffman tree holds a single width (a leaf, then the end mark), so every
    distance is `back_bits` wide and every length `len_bits`. Greedy matches of 3 bytes or more."""
    out = Bits()
    for c in b"vpk0":
        out.put(c, 8)
    out.put(len(data), 32)
    out.put(method, 8)
    for width in (back_bits, len_bits):
        out.put(0, 1); out.put(width, 8); out.put(1, 1)       # leaf(width), then end
    i, max_back, max_len = 0, (1 << back_bits) - 1 - (8 if method else 0), (1 << len_bits) - 1
    while i < len(data):
        best = (0, 0)
        for back in range(1, min(i, max_back) + 1):
            if method == 1 and (back + 8) % 4 == 0 and (back + 8) >> 2 < 3:
                continue
            n = 0
            while n < max_len and i + n < len(data) and data[i + n - back] == data[i + n]:
                n += 1
            if n > best[0]:
                best = (n, back)
        n, back = best
        if n >= 3:
            out.put(1, 1)
            if method == 0:
                out.put(back, back_bits)
            elif (back + 8) % 4 == 0:                  # one sample: (m << 2) - 8, m >= 3
                out.put((back + 8) >> 2, back_bits)
            else:                                      # two samples: (m + 1 + (u << 2)) - 8, m < 3
                l = (back + 8) % 4
                out.put(l - 1, back_bits); out.put((back + 8 - l) >> 2, back_bits)
            out.put(n, len_bits)
            i += n
        else:
            out.put(0, 1); out.put(data[i], 8)
            i += 1
    return out.bytes()


def strip_header(number, count):
    """48 made-up header bytes with the NINTENDO mark where a real strip has it."""
    h = bytearray(48)
    h[0:4] = bytes([0x00, 0x30, 0x01, 0x02])
    h[0x10:0x14] = struct.pack(">HH", 0x7000 + number, count)
    h[0x1A:0x22] = b"NINTENDO"
    return bytes(h)


def make_strip(payload, number=1, count=1, title=b"TEST CARD"):
    """One long strip (2,912 bytes) carrying `payload` after its header and title."""
    data = strip_header(number, count) + title.ljust(W.CARD_TITLE, b"\0") + payload
    assert len(data) <= 44 * 48, len(data)
    data = data.ljust(44 * 48, b"\0")
    cw = [data[i * 48:i * 48 + 48] + W.rs_parity(data[i * 48:i * 48 + 48]) for i in range(44)]
    stream = bytes(cw[k % 44][k // 44] for k in range(64 * 44)).ljust(28 * 102, b"\0")
    return b"".join(bytes([0x00, i]) + stream[i * 102:i * 102 + 102] for i in range(28))


def make_card(script, method=0, before=600, after=300):
    """A whole card: a made-up program with `script` inside, compressed, cut into as many strips
    as it needs. Returns the list of strips."""
    filler = bytes(((i * 7 + 3) ^ (i >> 3)) & 0xFF for i in range(before))
    prog = filler + script + bytes((i * 13) & 0xFF for i in range(after))
    blob = vpk0_encode(prog, method)
    card = len(blob).to_bytes(2, "little") + blob
    room = 44 * 48 - 48 - W.CARD_TITLE
    parts = [card[i:i + room] for i in range(0, len(card), room)] or [b""]
    return [make_strip(p, n + 1, len(parts)) for n, p in enumerate(parts)]


def test_trainer(name=b"\xbb\xbc\xbd", seed=1):
    """A made-up e-Reader trainer record (0xBC bytes) with the game's word-sum checksum."""
    rec = bytearray(bytes(((i * 31 + seed * 17) & 0xFF) for i in range(0xB8)))
    rec[0] = 0
    rec[4:12] = name.ljust(8, b"\xff")
    words = struct.unpack("<46I", bytes(rec))
    return bytes(rec) + struct.pack("<I", sum(words) & 0xFFFFFFFF)


def trainer_script(trainer, language="English", ram_script=None):
    """A Mystery Event script like the real trainer cards': checkcompat, (initramscript), addtrainer, end."""
    mask = W.MEV_LANGUAGE_MASKS[language]
    head = bytearray([W.MEV_CHECKCOMPAT]) + struct.pack("<IHIHI", W.MEV_BASE, mask, mask, 4, 0x180)
    n = 17 + (12 if ram_script else 0) + 5 + 1
    n += -n % 4
    tail = trainer
    if ram_script:
        rs_at = W.MEV_BASE + n + len(trainer)
        head += bytes([W.MEV_INITRAMSCRIPT, 25, 4, 1]) + struct.pack("<II", rs_at, rs_at + len(ram_script))
        tail += ram_script
    head += bytes([13]) + struct.pack("<I", W.MEV_BASE + n) + bytes([W.MEV_END])
    return bytes(head.ljust(n, b"\0")) + tail
