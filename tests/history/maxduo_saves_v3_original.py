"""
maxduo_saves.py - export the DS game saves stored on a Datel Action Replay
MAX DUO (MAX Drive DS), natively on Windows 11, with no Datel driver.

Protocol reverse-engineered from Datel's own PC application
(NintendoDSMaxDrive.exe, 2005):

  USB 05FD:DAAE, interrupt OUT 0x02 / IN 0x81, every message 8 bytes:
      "CBW" + command byte + 32-bit little-endian argument

  0x02  hello            -> 0x03 + firmware version
  0x11  fetch directory  -> 0x14 + size
  0x13  fetch item N     -> 0x14 + size
  0x16  ask for checksum -> 0x17 + CRC-32
  0x18  start sending, then 8-byte data packets, each acknowledged
        with 0x19 + bytes received so far (0x1F if the CRC is wrong)

  Saves arrive compressed: a 0xA8-byte header, then chunks of
  [big-endian size, big-endian compressed size, escape byte, data]
  where  escape, n, v  means  "byte v repeated n+1 times".

READ-ONLY by default: normally this script only sends the reading commands
above. The single exception is --update, which sends a firmware update
exactly as Datel's software did, and only after you type UPDATE to confirm.
It never sends Datel's delete, save-restore or settings commands.

Setup (Windows 11):
  1. pip install pyusb libusb-package
  2. Plug the DUO into the console, connect USB, switch on holding L until
     "USB SERVER MODE" shows.
  3. In Zadig, give USB ID 05FD DAAE the WinUSB driver (done already).
  4. python maxduo_saves.py --list        show what's stored
     python maxduo_saves.py               export every save to .\\saves
     python maxduo_saves.py --cart        read the inserted cart's save directly
     python maxduo_saves.py --update FILE install a firmware update (confirms first)
     python maxduo_saves.py --gba-save    read the GBA game's save (patched firmware)
"""

import argparse
import os
import re
import struct
import sys
import time
import zlib

VID, PID = 0x05FD, 0xDAAE
EP_OUT, EP_IN = 0x02, 0x81
SHORT_TIMEOUT = 5000          # ms, as Datel's software uses
LONG_TIMEOUT = 120000         # ms, the device can be slow to prepare an item

# Commands this script is allowed to send - nothing else, by design.
HELLO, DIRECTORY, ITEM = 0x02, 0x11, 0x13
GET_CRC, START, ACK, BAD_CRC = 0x16, 0x18, 0x19, 0x1F
CART_SIZE, CART_READ, CART_PAGE = 0x1A, 0x12, 0x24   # read the cart in the slot directly
GBA_SAVE = 0x03     # only answered by the patched firmware (duo_1.06_gbasave.bin)
LAB_SETADDR, LAB_POKE, LAB_EXEC = 0x04, 0x05, 0x06   # lab firmware only
SAFE_COMMANDS = {HELLO, DIRECTORY, ITEM, GET_CRC, START, ACK, BAD_CRC,
                 CART_SIZE, CART_READ, CART_PAGE, GBA_SAVE, LAB_SETADDR, LAB_POKE, LAB_EXEC}

# Cartridge mapping values seen in Datel's firmware (version 2 patch lets us pick):
MAPPINGS = {
    0x0007: "game running with codes (Datel's launcher) - default",
    0x0001: "game running without codes (Datel's launcher)",
    0x0101: "peek at the game's program only (version 1 behaviour)",
}

# Firmware update - the ONLY writing this tool can do, and only via --update.
RECV_UPDATE, CHECKSUM_DATA, IS_FILE_GOOD = 0x0C, 0x0F, 0x20
MAX_UPDATE = 0x3C000
PAGE = 0x20000

FLAG_SAVE, FLAG_CODES = 0x20, 0x10


class DuoError(Exception):
    pass


# ---------------------------------------------------------------- USB layer

class UsbTransport:
    def __init__(self):
        try:
            import usb.core
            import usb.util
        except ImportError:
            sys.exit("pyusb is missing. Run:  pip install pyusb libusb-package")
        try:
            import libusb_package
            backend = libusb_package.get_libusb1_backend()
        except ImportError:
            backend = None
        self.usb = usb
        dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
        if dev is None:
            sys.exit("Action Replay MAX DUO not found (USB 05FD:DAAE).\n"
                     "Check it shows USB SERVER MODE on the console, and that Zadig "
                     "has given it the WinUSB driver.")
        try:
            dev.set_configuration(1)
        except usb.core.USBError:
            pass
        usb.util.claim_interface(dev, 0)
        self.dev = dev

    def write(self, data, timeout=SHORT_TIMEOUT):
        self.dev.write(EP_OUT, data, timeout)

    def read(self, timeout=SHORT_TIMEOUT):
        return bytes(self.dev.read(EP_IN, 8, timeout))

    def close(self):
        self.usb.util.release_interface(self.dev, 0)
        self.usb.util.dispose_resources(self.dev)


# ----------------------------------------------------------- protocol layer

def packet(cmd, arg=0):
    if cmd not in SAFE_COMMANDS:
        raise AssertionError(f"refusing to send command 0x{cmd:02X}")
    return b"CBW" + bytes([cmd]) + struct.pack("<I", arg & 0xFFFFFFFF)


def parse(reply):
    if len(reply) != 8:
        raise DuoError(f"short reply ({len(reply)} bytes): {reply.hex(' ')}")
    return reply[3], struct.unpack_from("<I", reply, 4)[0]


class MaxDuo:
    def __init__(self, transport):
        self.t = transport

    def ask(self, cmd, arg=0, timeout=SHORT_TIMEOUT):
        self.t.write(packet(cmd, arg))
        return parse(self.t.read(timeout))

    def hello(self):
        code, version = self.ask(HELLO)
        if code != 0x03:
            raise DuoError(f"unexpected hello reply 0x{code:02X}")
        return version

    def cart_save(self, progress=None):
        """Read the save straight off the cartridge in the slot, as Datel's
        'back up my game cart save' does. Returns the compressed blob body."""
        code, info = self.ask(CART_SIZE, timeout=LONG_TIMEOUT)
        if code != 0x1B:
            raise DuoError(f"cart query declined (reply 0x{code:02X})")
        if info == 0:
            raise DuoError("no cartridge save detected")
        if info & 0x1FFFF:
            return info, self.download(CART_READ, 0, progress)
        pages = info >> 17
        data = b"".join(self.download(CART_PAGE, i, progress) for i in range(pages))
        return info, data

    def gba_save(self, banks=2, mapping=0x0007, progress=None):
        if mapping not in MAPPINGS:
            raise DuoError(f"mapping 0x{mapping:04X} is not one the DUO is known to use")
        return self.download(GBA_SAVE, banks | (mapping << 8), progress)

    def upload_update(self, payload, progress=None):
        """Send a firmware update exactly as Datel's PC software does."""
        size = len(payload)
        padded = size + (size & 0x7F)          # Datel's own padding rule
        if padded % 8 or padded > MAX_UPDATE:
            raise DuoError(f"unsuitable update size {size:#x}")
        data = payload + b"\0" * (padded - size)

        def send(cmd, arg=0, timeout=SHORT_TIMEOUT):
            self.t.write(b"CBW" + bytes([cmd]) + struct.pack("<I", arg))
            return parse(self.t.read(timeout))

        code, _ = send(RECV_UPDATE, padded, LONG_TIMEOUT)
        if code != 0x0D:
            raise DuoError(f"device refused the update (reply 0x{code:02X})")
        code, _ = send(CHECKSUM_DATA, zlib.crc32(data))
        if code != 0x0D:
            raise DuoError(f"device refused the checksum (reply 0x{code:02X})")
        for off in range(0, padded, 8):
            self.t.write(data[off:off + 8])
            code, got = parse(self.t.read())
            if code != 0x10 or got != off + 8:
                raise DuoError(f"transfer out of step at {off:#x} (reply 0x{code:02X}, {got:#x})")
            if progress:
                progress(off + 8, padded)
        code, status = send(IS_FILE_GOOD, 0, LONG_TIMEOUT)
        if code != 0x21 or status != 0:
            raise DuoError(f"device rejected the update (reply 0x{code:02X}, status {status})")

    # ------------------------------------------------------------- v3 streaming
    def is_v3(self):
        if not hasattr(self, "_v3"):
            code, val = self.ask(CMD_INFO)
            self._v3 = code == CMD_INFO and val == RKL3
            self.pace = 0
        return self._v3

    def set_pace(self, n):
        code, val = self.ask(CMD_PACE, n)
        if (code, val) != (CMD_PACE, n):
            raise DuoError(f"pace not accepted ({code:#x} {val:#x})")
        self.pace = n

    def _drain(self):
        while True:
            try:
                self.t.read(300)
            except Exception as err:
                if _is_timeout(err):
                    return
                raise

    def receive_stream(self, cmd, arg=0, timeout=LONG_TIMEOUT, progress=None, max_tries=6):
        """Run a streaming command; returns the (decompressed, CRC-checked) data."""
        self.stats = {"blocks": 0, "retries": 0, "packets": 0, "raw_blocks": 0, "block_info": [], "payloads": []}
        self.t.write(packet(cmd, arg))
        code, total = parse(self.t.read(timeout))
        if code == 0x15:
            raise DuoError("the DUO rejected the lab program")
        if code != ST_START:
            raise DuoError(f"unexpected stream start {code:#x}")
        out = bytearray(total)
        nblocks = (total + BLOCK - 1) // BLOCK
        for b in range(nblocks):
            want = min(BLOCK, total - b * BLOCK)
            tries = 0
            while True:
                try:
                    hdr = self.t.read(SHORT_TIMEOUT)
                    idx, lf, crc = struct.unpack("<HHI", hdr)
                    raw, dlen = lf & 0x8000, lf & 0x7FFF
                    if idx != b or not 0 < dlen <= BLOCK or (raw and dlen != want):
                        raise DuoError(f"bad block header for block {b}: {hdr.hex()}")
                    need = (dlen + 7) // 8
                    buf = bytearray()
                    for i in range(need):
                        buf += self.t.read(SHORT_TIMEOUT)
                        if self.pace and (i + 1) % self.pace == 0 and i + 1 < need:
                            self.t.write(packet(ST_CONT, b))
                    self.stats["packets"] += need + 1
                    buf = bytes(buf[:dlen])
                    blk = buf if raw else rle3_decode(buf, want)
                    if zlib.crc32(blk) != crc:
                        raise DuoError(f"block {b} checksum mismatch")
                    out[b * BLOCK:b * BLOCK + want] = blk
                    self.stats["raw_blocks"] += bool(raw)
                    self.stats["block_info"].append((b, bool(raw), dlen))
                    self.stats["payloads"].append(buf)
                    self.t.write(packet(ST_ACK, b))
                    break
                except Exception as err:
                    if not (isinstance(err, (DuoError, struct.error, IndexError)) or _is_timeout(err)):
                        raise
                    if "device stopped" in str(err):
                        raise
                    tries += 1
                    self.stats["retries"] += 1
                    if tries > max_tries:
                        raise DuoError(f"block {b} failed {max_tries} times: {err}")
                    self._drain()
                    if tries == 2 and self.pace != 8:
                        self.pace = 8                    # slow down: wait every 8 packets
                        self.t.write(packet(ST_PACE, 8))
                    elif tries == 4 and self.pace != 1:
                        self.pace = 1                    # slowest: Datel-style, every packet
                        self.t.write(packet(ST_PACE, 1))
                    self.t.write(packet(ST_ACK, b | 0x80000000))
            self.stats["blocks"] += 1
            if progress:
                progress(min(total, (b + 1) * BLOCK), total)
        code, crc = parse(self.t.read(SHORT_TIMEOUT))
        if code != ST_END or crc != zlib.crc32(bytes(out)):
            raise DuoError(f"stream end check failed ({code:#x} {crc:#x})")
        return bytes(out)

    def download(self, cmd, arg=0, progress=None, timeout=LONG_TIMEOUT):
        code, size = self.ask(cmd, arg, timeout=timeout)
        if code == 0x01:
            raise DuoError("the Action Replay doesn't know this command"
                           " (is the patched firmware installed?)")
        if code != 0x14:
            raise DuoError(f"device declined request (reply 0x{code:02X})")
        code, crc = self.ask(GET_CRC)
        if code != 0x17:
            raise DuoError(f"no checksum offered (reply 0x{code:02X})")
        self.t.write(packet(START))
        buf = bytearray()
        while len(buf) < size:
            buf += self.t.read()
            done = len(buf)
            if done >= size and zlib.crc32(bytes(buf[:size])) != crc:
                self.t.write(packet(BAD_CRC))
                self.t.read()
                raise DuoError("checksum mismatch - the transfer was garbled")
            self.t.write(packet(ACK, done))
            if progress:
                progress(min(done, size), size)
        self.t.read()          # closing status packet
        return bytes(buf[:size])


# ---------------------------------------------------------------------- lab
# The lab firmware runs small scripts on the GBA. The interpreter below is the
# machine code built (and checked for ARM7TDMI compatibility) by build_lab.py.
LAB_INTERP = bytes.fromhex("f0b5074601b48ba638ce002b00d1f3e0012b00d11ee0022b00d124e0032b00d133e0042b00d13ae0052b00d143e0062b00d150e0072b00d153e0082b00d164e0092b00d163e00a2b00d17ae00b2b00d1b3e0cfe06f4a944203d06f4a944200d0c8e02580d0e72a06120e802a0ad0a02a08d06a4b9c4206d0694b9c4203d0694b9c4200d0b6e02270bee7002d00d1bbe722783a7001340137013df9d1b4e7002d00d1b1e722883a70120a7a7002340237023df7dca8e7002d00d1a5e722683a70120a7a70120aba70120afa7004340437043df3dc98e7002d00d195e7013dfdd192e7228800212388934202d10131a942f9d33970090a7970090ab970090af9703b711b0a7b7106377ee7022000e001202904090c2d0c2d02002900d174e7012805d022883a70120a7a70023702e022783a7001372b1c002b01d0013bfbe70139eae707ce07b4651900239c46ac421dd223680098834207d00198834204d00298834201d00434f1e7201c00f03af8206800f037f8606800f034f8a06800f031f80434634601339c46042bdfd36346042b11d20020c04300f024f80020c04300f020f80020c04300f01cf80020c04300f018f80133ebe703b026e765190020c0431b4aac4209d22178013448400823400800d35040013bfad1f3e7c04300f001f812e73870000a7870000ab870000af870043770470d4a00e00b4a3a70120a7a70120aba70120afa70043701bc381af0bc02bc08479c000008040200045555000eaa2a000e0000000e444f4e45455252212083b8ed")
LAB_STAGE = 0x02038000
LAB_MAGIC = 0x314C4B52
LAB_OPS = {"end": 0, "w16": 1, "w8": 2, "r8": 3, "r16": 4, "r32": 5, "delay": 6,
           "wait16": 7, "sample16": 8, "sample8": 9, "search": 10, "crc": 11}
MAP_REG, WAITCNT = 0x0800009C, 0x04000204
SAVE_CMD_ADDRS = {0x0E005555, 0x0E002AAA, 0x0E000000}
LAB_MAPS = {0x0001, 0x0007, 0x0101, 0x0120}
MAX_PROGRAM = 0x2000
FLASH_IDS = {(0xC2, 0x09): "Macronix MX29L010 (128 KB Flash)", (0x62, 0x13): "Sanyo LE26FV10N1TS (128 KB Flash)",
             (0xBF, 0xD4): "SST 39VF512 (64 KB Flash)", (0x1F, 0x3D): "Atmel AT29LV512 (64 KB Flash)",
             (0x32, 0x1B): "Panasonic MN63F805MNP (64 KB Flash)", (0xC2, 0x1C): "Macronix MX29L512 (64 KB Flash)"}

# v3 firmware (Reckless edition v3)
CMD_EXEC_STREAM, CMD_RESEND, CMD_INFO, CMD_PACE, CMD_UPLOAD = 0x07, 0x08, 0x09, 0x1D, 0x1E
ST_START, ST_END = 0x30, 0x32
ST_ACK, ST_PACE, ST_CONT = 0x0E, 0x0F, 0x10       # in-stream only (the DUO ignores them otherwise)
RKL3 = 0x334C4B52
MSG_AT = 0x0203AE00
RLE_ESC = 0xE7
BLOCK = 4096
SAFE_COMMANDS |= {CMD_EXEC_STREAM, CMD_RESEND, CMD_INFO, CMD_PACE, CMD_UPLOAD, ST_ACK, ST_PACE, ST_CONT}


def _is_timeout(err):
    return "timeout" in type(err).__name__.lower() or "timed out" in str(err).lower()


def rle3_decode(data, size):
    out, i = bytearray(), 0
    while i < len(data) and len(out) < size:
        c = data[i]
        if c == RLE_ESC:
            out += bytes([data[i + 2]]) * (data[i + 1] + 1)
            i += 3
        else:
            out.append(c)
            i += 1
    if len(out) != size:
        raise DuoError(f"block decoded to {len(out)} bytes, expected {size}")
    return bytes(out)


def rle3_encode(data):
    """Reference encoder - the GBA's must produce exactly this."""
    out, i, n = bytearray(), 0, len(data)
    while i < n:
        v, run = data[i], 1
        while i + run < n and data[i + run] == v and run <= 0xFF:
            run += 1
        if run >= 4 or v == RLE_ESC:
            if len(out) + 3 > n - 1:
                return None
            out += bytes([RLE_ESC, run - 1, v])
            i += run
        else:
            for _ in range(run):
                if len(out) >= n - 1:
                    return None
                out.append(v)
            i += run
    return bytes(out)


def _out_size(op):
    name, arg = op[0], op[2] if len(op) > 2 else 0
    return {"r8": arg, "r16": arg, "r32": arg, "wait16": 6, "search": 64, "crc": 4,
            "sample16": (arg & 0xFFFF) * 2, "sample8": arg & 0xFFFF}.get(name, 0)


def lab_validate(ops):
    """The same write rules the GBA-side interpreter enforces, checked before sending."""
    out = 0
    for op in ops:
        name, addr, arg = op[0], op[1] if len(op) > 1 else 0, op[2] if len(op) > 2 else 0
        if name not in LAB_OPS or name == "end":
            raise DuoError(f"unknown lab operation {name!r}")
        if name == "w16":
            if addr == MAP_REG and arg not in LAB_MAPS:
                raise DuoError(f"mapping value {arg:#x} not allowed")
            if addr not in (MAP_REG, WAITCNT):
                raise DuoError(f"16-bit write to {addr:#x} not allowed")
        if name == "w8" and (addr not in SAVE_CMD_ADDRS or arg & 0xFF in (0x80, 0xA0)):
            raise DuoError(f"save-chip write {arg:#x} to {addr:#x} not allowed")
        if name == "search" and (len(op) != 4 or len(op[3]) != 3 or addr & 3 or arg & 3):
            raise DuoError("search needs an aligned range and three 4-byte patterns")
        out += _out_size(op)
    if out + 8 > PAGE:
        raise DuoError("lab script would return too much data")


def lab_encode(ops):
    script = b""
    for o in ops:
        script += struct.pack("<III", LAB_OPS[o[0]], o[1] if len(o) > 1 else 0, (o[2] if len(o) > 2 else 0) & 0xFFFFFFFF)
        if o[0] == "search":
            script += struct.pack("<III", *[p & 0xFFFFFFFF for p in o[3]])
    body = LAB_INTERP + script + struct.pack("<III", 0, 0, 0)
    prog = struct.pack("<III", LAB_MAGIC, len(body), zlib.crc32(body)) + body
    prog += b"\0" * (-len(prog) % 4)
    if len(prog) > MAX_PROGRAM:
        raise DuoError("lab program too large")
    return prog


class Lab:
    def __init__(self, duo, pace=None):
        self.duo = duo
        self.v3 = duo.is_v3()
        if pace is not None and self.v3:
            duo.set_pace(pace)

    def _poke(self, cmd, arg):
        code, val = self.duo.ask(cmd, arg)
        if code == 0x01:
            raise DuoError("this firmware has no lab (install the lab edition)")
        if code == 0x3F:
            raise DuoError("the DUO rejected a lab upload address")
        return code, val

    def _upload_slow(self, addr, data):
        code, val = self._poke(LAB_SETADDR, addr)
        if (code, val) != (LAB_SETADDR, addr):
            raise DuoError(f"unexpected reply to SETADDR: {code:#x} {val:#x}")
        for i in range(0, len(data), 4):
            code, val = self._poke(LAB_POKE, struct.unpack_from("<I", data, i)[0])
            if (code, val) != (LAB_POKE, addr + i + 4):
                raise DuoError(f"upload out of step at {i:#x}")

    def _upload_fast(self, addr, data):
        data = data + b"\0" * (-len(data) % 8)
        code, val = self._poke(LAB_SETADDR, addr)
        if (code, val) != (LAB_SETADDR, addr):
            raise DuoError(f"unexpected reply to SETADDR: {code:#x} {val:#x}")
        code, val = self._poke(CMD_UPLOAD, len(data))
        if (code, val) != (CMD_UPLOAD, len(data)):
            raise DuoError(f"the DUO refused the upload ({code:#x} {val:#x})")
        for i in range(0, len(data), 8):
            self.duo.t.write(data[i:i + 8])
        code, val = parse(self.duo.t.read(LONG_TIMEOUT))
        if code != CMD_UPLOAD or val != zlib.crc32(data):
            raise DuoError(f"upload checksum mismatch ({code:#x} {val:#x})")

    def run(self, ops, timeout=LONG_TIMEOUT, messages=(), progress=None):
        if not ops or tuple(ops[0][:2]) != ("w16", WAITCNT):
            ops = [("w16", WAITCNT, 0x0003)] + list(ops)   # safe save-chip timing; restored afterwards
        lab_validate(ops)
        prog = lab_encode(ops)
        if not self.v3:
            self._upload_slow(LAB_STAGE, prog)
            data = self.duo.download(LAB_EXEC, 0, timeout=timeout)
            return self.parse(ops, data)
        if messages:
            text = b"".join(m.encode("ascii", "replace")[:26] + b"\0" for m in list(messages)[:3]) + b"\0"
            self._upload_fast(MSG_AT, text)
        self._upload_fast(LAB_STAGE, prog)
        data = self.duo.receive_stream(CMD_EXEC_STREAM, 0, timeout=timeout, progress=progress)
        return self.parse(ops, data)

    @staticmethod
    def parse(ops, data):
        results, pos = [], 0
        for op in ops:
            n = _out_size(op)
            if n:
                results.append((op, data[pos:pos + n]))
                pos += n
        trailer = data[pos:pos + 4]
        if trailer == b"ERR!":
            raise DuoError("the GBA refused part of the script (ERR!)")
        if trailer != b"DONE":
            raise DuoError(f"lab output malformed (trailer {trailer!r})")
        return results


def lab_record(ops, results, folder="lab_results", note=""):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, time.strftime("lab %Y-%m-%d %H%M%S.json"))
    import json
    with open(path, "w") as f:
        json.dump({"note": note, "ops": [list(o) for o in ops],
                   "results": [{"op": list(o), "hex": d.hex()} for o, d in results]}, f, indent=1)
    return path


def lab_probe_ops():
    ops = [("w16", WAITCNT, 0x0003),
           ("r16", 0x09FE0160, 2), ("r16", 0x09FFE140, 2),
           ("r8", 0x080000A0, 16), ("r8", 0x0E000000, 16)]
    for m in (0x0101, 0x0007, 0x0001):
        ops += [("w16", MAP_REG, m), ("delay", 0, 0x1000),
                ("r8", 0x080000A0, 16), ("r8", 0x0E000000, 16),
                ("w8", 0x0E005555, 0xAA), ("w8", 0x0E002AAA, 0x55), ("w8", 0x0E005555, 0x90),
                ("delay", 0, 0x400), ("r8", 0x0E000000, 2),
                ("w8", 0x0E005555, 0xAA), ("w8", 0x0E002AAA, 0x55), ("w8", 0x0E005555, 0xF0),
                ("delay", 0, 0x400), ("r8", 0x0E000000, 4),
                ("w16", MAP_REG, 0x0120), ("delay", 0, 0x400)]
    return ops


def lab_probe_report(results):
    it = iter(results)
    def nxt(): return next(it)[1]
    show = lambda b: b.hex(" ")
    title = lambda b: "".join(chr(c) if 32 <= c < 127 else "." for c in b)
    print(f"  registers: 0x09FE0160={nxt().hex()}  0x09FFE140={nxt().hex()}")
    print(f"  DUO mapped:  header title area: {title(nxt())!r}   save area: {show(nxt())}")
    for m in (0x0101, 0x0007, 0x0001):
        t, sv, ident, after = nxt(), nxt(), nxt(), nxt()
        chip = FLASH_IDS.get((ident[0], ident[1]), "no recognised chip")
        print(f"  map 0x{m:04X}: title {title(t)!r}")
        print(f"      save area {show(sv)}")
        print(f"      flash ID {show(ident)} -> {chip};  after exit: {show(after)}")


def cstr(b):
    return b.split(b"\0", 1)[0].decode("latin-1").strip()


def parse_directory(d):
    users = [cstr(d[i * 0x20:i * 0x20 + 0x10]) if d[i * 0x20 + 0x10] else None
             for i in range(4)]
    count = struct.unpack_from("<I", d, 0x80)[0]
    entries = []
    for i in range(count):
        e = d[0x88 + i * 0x20:0x88 + (i + 1) * 0x20]
        if len(e) < 0x20:
            break
        flags = e[0x1B]
        entries.append({
            "index": i,
            "name": cstr(e[4:0x1B]),
            "flags": flags,
                        "users": [users[u] or f"User {u + 1}" for u in range(4) if flags & (1 << u)],
            "stored_bytes": struct.unpack_from("<H", e, 0x1E)[0] * 0x80,
            "raw": e,
        })
    return users, entries


def rle_decode(data, size):
    esc, i, out = data[0], 1, bytearray()
    while i < len(data):
        c = data[i]
        if c == esc:
            out += bytes([data[i + 2]]) * (data[i + 1] + 1)
            i += 3
        else:
            out.append(c)
            i += 1
    if len(out) != size:
        raise DuoError(f"chunk decoded to {len(out)} bytes, expected {size}")
    return out


def decode_save(blob):
    """Return (game, name, description, raw_save) from a save blob."""
    game, name, desc = cstr(blob[4:0x20]), cstr(blob[0x20:0x40]), cstr(blob[0x40:0xA0])
    total, chunks = struct.unpack_from("<II", blob, 0xA0)
    pos, out = 0xA8, bytearray()
    for _ in range(chunks):
        usize, csize = struct.unpack_from(">II", blob, pos)
        pos += 8
        out += rle_decode(blob[pos:pos + csize], usize)
        pos += csize
    if len(out) != total:
        raise DuoError(f"save decoded to {len(out)} bytes, expected {total}")
    return game, name, desc, bytes(out)


def dst_file(blob, game, name, desc):
    """Datel's own .dst format, so the software (or Codejunkies) can re-use it."""
    def w(s, chars):
        return s.encode("utf-16-le")[:chars * 2 - 2].ljust(chars * 2, b"\0")
    header = b"ARDS000000000001" + w(game, 0x20) + w(name, 0x20) + w(desc, 0x60) + b"\0" * 4
    assert len(header) == 0x154
    return header + blob


def safe_filename(s):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(" .")
    return s or "save"


# ------------------------------------------------------------------ program

_last_pct = [-1]


def bar(done, total):
    pct = 100 * done // total if total else 100
    if pct == _last_pct[0]:
        return
    _last_pct[0] = pct
    width = 30
    filled = width * done // total if total else width
    print(f"\r    [{'#' * filled}{'.' * (width - filled)}] {done:,}/{total:,} bytes",
          end="", flush=True)


def run(duo, args):
    version = duo.hello()
    print(f"Connected to Action Replay MAX DUO (firmware {version >> 8}.{version & 0xFF:02d})")

    if getattr(args, "lab", None):
        return do_lab(duo, args)

    if getattr(args, "update", None):
        return do_update(duo, args.update, version)

    if getattr(args, "gba_save", False) and duo.is_v3():
        return gba_save_v3(duo, args)

    if getattr(args, "gba_save", False):
        print("Reading the GBA cartridge's save (patched firmware)...")
        mapping = getattr(args, "map", None) or 0x0007
        print(f"  Using mapping 0x{mapping:04X}: {MAPPINGS.get(mapping, '?')}")
        data = duo.gba_save(getattr(args, "banks", 2), mapping, progress=bar)
        print()
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, time.strftime("gba save %Y-%m-%d %H%M%S.sav"))
        with open(path, "wb") as f:
            f.write(data)
        print(f"  Saved {path} ({len(data):,} bytes)")
        describe_gba_save(data)
        return

    if args.cart:
        print("Reading the save directly from the cartridge in the slot...")
        info, data = duo.cart_save(progress=bar)
        print(f"\n  Cart reported 0x{info:X}; received {len(data):,} bytes")
        os.makedirs(args.out, exist_ok=True)
        stem = os.path.join(args.out, time.strftime("cart %Y-%m-%d %H%M%S"))
        with open(stem + ".raw", "wb") as f:
            f.write(data)
        try:
            *_, raw = decode_save(b"\0" * 0xA0 + data)
        except (DuoError, struct.error, IndexError) as err:
            print(f"  Couldn't decode it as a save ({err}); kept the raw data as {stem}.raw")
            return
        with open(stem + ".sav", "wb") as f:
            f.write(raw)
        print(f"  Saved {os.path.basename(stem)}.sav ({len(raw):,} bytes)")
        return

    print("Reading the list of stored items...")
    users, entries = parse_directory(duo.download(DIRECTORY))
    print(f"Found {len(entries)} item(s).\n")

    for e in entries:
        who = ", ".join(e["users"]) or "-"
        line = f"  [{e['index']:3}] {e['name']:<24} flags 0x{e['flags']:02X}  {who}"
        if args.verbose:
            line += f"   raw {e['raw'].hex()}"
        print(line)
    if args.list or not entries:
        return

    os.makedirs(args.out, exist_ok=True)
    print(f"\nExporting to {os.path.abspath(args.out)}")
    saved = kept = failed = 0
    for e in entries:
        print(f"\n  [{e['index']}] {e['name']}")
        try:
            blob = duo.download(ITEM, e["index"], progress=bar)
            print()
        except DuoError as err:
            print(f"\n    Skipped: {err}")
            failed += 1
            continue
        try:
            game, name, desc, raw = decode_save(blob)
        except (DuoError, struct.error, IndexError) as err:
            # not a save we understand - keep it untouched for inspection
            if not isinstance(err, DuoError):
                err = "unrecognised layout"
            path = os.path.join(args.out, safe_filename(f"item {e['index']} - {e['name']}") + ".bin")
            with open(path, "wb") as f:
                f.write(blob)
            print(f"    Not a save ({err}); kept as {os.path.basename(path)} ({len(blob):,} bytes)")
            kept += 1
            continue
        base = safe_filename(f"{game} - {name}" if name and name != game else game or e["name"])
        stem, n = os.path.join(args.out, base), 2
        while os.path.exists(stem + ".sav"):
            stem, n = os.path.join(args.out, f"{base} ({n})"), n + 1
        with open(stem + ".sav", "wb") as f:
            f.write(raw)
        with open(stem + ".dst", "wb") as f:
            f.write(dst_file(blob, game, name, desc))
        print(f"    Saved {os.path.basename(stem)}.sav ({len(raw):,} bytes) and .dst backup")
        saved += 1

    print(f"\nFinished: {saved} save(s) exported, {kept} other item(s) kept as .bin"
          + (f", {failed} failed." if failed else "."))


KNOWN_FIRMWARE = {
    0x1767E827: "Datel's official 1.06 update",
    0xBFD2B800: "1.06 with the GBA-save patch (version 1)",
    0x516B9E06: "1.06 with the GBA-save patch (version 2)",
    0x70B948B7: "Reckless Modding 2026 edition (GBA-save patch v2 + new look)",
    0xC94624F7: "Reckless lab edition (v2 + new look + safe debugging lab)",
    0xF82D33F9: "Reckless v3 (fast streaming, save-type detection, verbose log, lab)",
}


def check_update_file(path):
    blob = open(path, "rb").read()
    if len(blob) < 8:
        raise DuoError("file too small")
    stored, payload = struct.unpack_from("<I", blob)[0], blob[4:]
    if zlib.crc32(payload) != stored:
        raise DuoError("the file's own checksum is wrong - it is damaged")
    if len(payload) + (len(payload) & 0x7F) > MAX_UPDATE:
        raise DuoError("file is too large for the DUO")
    if payload[:4] != bytes.fromhex("2e0000ea") or b"ACTION REPLAY GBA / MAX DRIVE DS" not in payload:
        raise DuoError("this doesn't look like Action Replay MAX DUO firmware")
    return stored, payload


def do_update(duo, path, version):
    try:
        crc, payload = check_update_file(path)
    except (OSError, DuoError) as err:
        sys.exit(f"Not updating: {err}")
    name = KNOWN_FIRMWARE.get(crc, "a modified firmware (e.g. the GBA-save patch)")
    print(f"\nUpdate file: {os.path.basename(path)}")
    print(f"  {len(payload):,} bytes, CRC {crc:08X} - {name}")
    print("\nThis rewrites the DUO's program. If power is lost while it writes,"
          "\nthe DUO may not start again. Use fresh batteries and don't touch it"
          "\nuntil this tool says it's done.")
    if input("\nType UPDATE to continue: ").strip() != "UPDATE":
        print("Cancelled - nothing was sent.")
        return
    print("Sending update...")
    duo.upload_update(payload, progress=bar)
    print("\n\nThe DUO accepted the update and is now writing it to its flash.")
    print("Leave it alone for at least 30 seconds, then switch the console off and on.")
    print(f"(Afterwards, the hello line should show protocol 1.04 instead of "
          f"{version >> 8}.{version & 0xFF:02d}.)")


def _lab_ops(spec):
    return [tuple(int(x, 0) if isinstance(x, str) and x[:2] in ("0x", "0X") else x for x in o) for o in spec["ops"]]


def _lab_print(res, tag="read"):
    os.makedirs("lab_results", exist_ok=True)
    for n, (op, d) in enumerate(res):
        if len(d) > 64:
            path = os.path.join("lab_results", time.strftime(f"{tag} %Y-%m-%d %H%M%S #{n}.bin"))
            with open(path, "wb") as f:
                f.write(d)
            print(f"  {op}: {len(d):,} bytes -> {path}  (crc {zlib.crc32(d):08X})")
        else:
            print(f"  {op}: {d.hex(' ')}")


def do_lab(duo, args):
    lab = Lab(duo)
    what = args.lab[0]
    if what == "probe":
        ops = lab_probe_ops()
        print("Running lab probe (reads only; save-chip ID commands; DUO restored afterwards)...")
        res = lab.run(ops)
        lab_probe_report(res)
    elif what == "peek":
        addr, length = int(args.lab[1], 0), int(args.lab[2], 0)
        width = {1: "r8", 2: "r16", 4: "r32"}[args.width]
        ops = [("w16", WAITCNT, 0x0003)]
        if args.map_lab:
            ops.append(("w16", MAP_REG, args.map_lab))
        ops.append((width, addr, length))
        res = lab.run(ops)
        data = res[-1][1]
        for o in range(0, len(data), 16):
            print(f"  {addr + o:08X}  {data[o:o + 16].hex(' ')}")
    elif what == "scope":
        addr, seconds = int(args.lab[1], 0), float(args.lab[2])
        samples = 2000
        delay = max(1, min(0xFFFF, int(seconds * 16_780_000 / 10 / samples / 256)))
        op = "sample8" if args.width == 1 else "sample16"
        ops = [("w16", WAITCNT, 0x0003)]
        if args.map_lab:
            ops.append(("w16", MAP_REG, args.map_lab))
        ops.append((op, addr, samples | delay << 16))
        print(f"Sampling {addr:#x} {samples} times over roughly {seconds:.0f} s - flip the switch now if testing it...")
        res = lab.run(ops, timeout=int((seconds * 3 + 60) * 1000))
        data = res[-1][1]
        vals = list(data) if op == "sample8" else [struct.unpack_from("<H", data, i)[0] for i in range(0, len(data), 2)]
        runs, last = [], None
        for i, v in enumerate(vals):
            if v != last:
                runs.append((i, v)); last = v
        print(f"  {len(runs)} distinct runs:")
        for i, v in runs[:40]:
            print(f"    from sample {i:5}: {v:#06x}")
    elif what == "batch":
        import json
        steps = json.load(open(args.lab[1]))["steps"]
        saved = {}
        for step in steps:
            print(f"\n--- {step['name']} ---")
            spec = json.load(open(step["script"]))
            ops = _lab_ops(spec)
            res = lab.run(ops, timeout=int(spec.get("timeout_s", 120) * 1000))
            _lab_print(res, step["name"])
            lab_record(ops, res, note=step["name"])
            if step.get("keep"):
                saved[step["keep"]] = res[step.get("keep_index", -1)][1]
        for out, parts in json.load(open(args.lab[1])).get("join", {}).items():
            data = b"".join(saved[k] for k in parts)
            with open(os.path.join("lab_results", out), "wb") as f:
                f.write(data)
            print(f"\nJoined {parts} -> lab_results/{out}: {len(data):,} bytes, crc {zlib.crc32(data):08X}")
        print("\nBatch finished.")
        return
    elif what == "script":
        import json
        spec = json.load(open(args.lab[1]))
        ops = _lab_ops(spec)
        res = lab.run(ops, timeout=int(spec.get("timeout_s", 120) * 1000))
        os.makedirs("lab_results", exist_ok=True)
        for n, (op, d) in enumerate(res):
            if len(d) > 64:
                path = os.path.join("lab_results", time.strftime(f"read %Y-%m-%d %H%M%S #{n}.bin"))
                with open(path, "wb") as f:
                    f.write(d)
                print(f"  {op}: {len(d):,} bytes -> {path}  (crc {zlib.crc32(d):08X})")
            else:
                print(f"  {op}: {d.hex(' ')}")
    else:
        raise DuoError(f"unknown lab command {what!r}")
    path = lab_record(ops, res, note=" ".join(args.lab))
    print(f"\nResults saved to {path}")


SAVE_BASE = 0x0E000000
SEARCH_END = 0x09FE0000            # stop before the DUO's own registers at 0x09FE0000
SAVE_KINDS = {                     # kind: (bytes, banks, needs chip commands)
    "flash128": (0x20000, 2, True), "flash64": (0x10000, 1, False), "sram32": (0x8000, 1, False)}


def _w(b):
    return struct.unpack("<I", b)[0]


def classify_tags(tags):
    kinds = set()
    for t in tags:
        if t.startswith("FLASH1M_V"):
            kinds.add("flash128")
        elif t.startswith("FLASH512_V") or t.startswith("FLASH_V"):
            kinds.add("flash64")
        elif t.startswith("SRAM_V") or t.startswith("SRAM_F_V"):
            kinds.add("sram32")
        elif t.startswith("EEPROM_V"):
            kinds.add("eeprom")
    return kinds


def _switch(bank):
    return [("w8", 0x0E005555, 0xAA), ("w8", 0x0E002AAA, 0x55), ("w8", 0x0E005555, 0xB0), ("w8", SAVE_BASE, bank)]


def detect_cart(lab, mapping=0x0101):
    ops = [("w16", MAP_REG, mapping), ("delay", 0, 0x400), ("r16", 0x08000000, 2),
           ("r8", 0x080000A0, 16),
           ("search", 0x08000000, SEARCH_END - 0x08000000, (_w(b"FLAS"), _w(b"SRAM"), _w(b"EEPR"))),
           ("crc", SAVE_BASE, 0x8000), ("crc", SAVE_BASE + 0x8000, 0x8000), ("r8", SAVE_BASE, 16),
           ("w16", MAP_REG, 0x0120)]
    res = lab.run(ops, messages=["GBA save: checking game"])
    _wake, header, found, c0, c1, first = (d for _, d in res)
    title = "".join(chr(c) if 32 <= c < 127 else " " for c in header[:12]).strip()
    code = header[12:16].decode("ascii", "replace")
    tags = []
    for i in range(0, 64, 16):
        addr = _w(found[i:i + 4])
        if addr == 0xFFFFFFFF:
            continue
        text = found[i + 4:i + 16].split(b"\0")[0]
        tags.append("".join(chr(c) for c in text if 32 <= c < 127))
    blank = {zlib.crc32(b"\xFF" * 0x8000), zlib.crc32(b"\0" * 0x8000)}
    return {"title": title, "code": code, "tags": tags, "kinds": classify_tags(tags),
            "mirror": _w(c0) == _w(c1) and _w(c0) not in blank, "first": first}


def plan_read(info, forced=None):
    if forced:
        kind = forced
    else:
        kinds = info["kinds"]
        if "eeprom" in kinds:
            raise DuoError("this game saves to EEPROM, which isn't supported yet - nothing was sent to the chip")
        if not kinds:
            raise DuoError("couldn't find the game's save-library tag; use --save-type if you know it")
        if len(kinds) > 1:
            raise DuoError(f"the game has several save tags ({', '.join(info['tags'])}); use --save-type")
        kind = kinds.pop()
    size, banks, _ = SAVE_KINDS[kind]
    warning = None
    if kind == "flash128" and info["mirror"]:
        banks, size, warning = 1, 0x10000, ("the first 64 KB repeats every 32 KB, which looks like SRAM, "
                                            "not Flash - so no bank switch was sent; only the first 64 KB was read")
    return kind, size, banks, warning


def gba_save_v3(duo, args):
    lab = Lab(duo, pace=1 if getattr(args, "slow", False) else None)
    if not lab.v3:
        raise DuoError("this needs the Reckless v3 firmware")
    mapping = args.map if getattr(args, "map", None) else 0x0101
    t0 = time.time()
    print("Checking the game cartridge...")
    info = detect_cart(lab, mapping)
    print(f"  Game: {info['title']} ({info['code']})   save library: {', '.join(info['tags']) or 'none found'}")
    kind, size, banks, warning = plan_read(info, getattr(args, "save_type", None))
    print(f"  Plan: {kind}, {size // 1024} KB in {banks} bank(s)")
    if warning:
        print("  Note: " + warning)
    data = b""
    bank_size = min(size, 0x10000)
    for b in range(banks):
        ops = [("w16", MAP_REG, mapping), ("delay", 0, 0x400), ("r16", 0x08000000, 2)]
        if b:
            ops += _switch(b) + [("delay", 0, 0x400)]
        ops += [("r8", SAVE_BASE, bank_size), ("crc", SAVE_BASE, bank_size)]
        if b:
            ops += _switch(0)
        ops += [("w16", MAP_REG, 0x0120)]
        msgs = ([f"Game: {info['title']}"] if b == 0 else []) + [f"Reading bank {b} ({bank_size // 1024} KB)"]
        for attempt in range(2):
            print(f"  Bank {b}:")
            res = lab.run(ops, messages=msgs, progress=bar)
            print()
            chunk, gba_crc = res[-2][1], _w(res[-1][1])
            if zlib.crc32(chunk) == gba_crc:
                break
            print(f"    The GBA's own checksum disagreed ({gba_crc:08X}); reading again...")
        else:
            raise DuoError(f"bank {b} gave different data twice - not saving it")
        st = duo.stats
        print(f"    {len(chunk) // 1024} KB verified, sent as {st['packets']} packets, {st['retries']} retries")
        data += chunk
    crc = zlib.crc32(data)
    os.makedirs(args.out, exist_ok=True)
    name = safe_filename(f"{info['title'] or 'GBA'} ({info['code']}) " + time.strftime("%Y-%m-%d %H%M%S"))
    path = os.path.join(args.out, name + ".sav")
    with open(path, "wb") as f:
        f.write(data)
    lab.run([], messages=["Save copied to PC", f"{len(data) // 1024} KB, CRC {crc:08X}"])
    print(f"\nSaved {path} ({len(data):,} bytes, CRC {crc:08X}) in {time.time() - t0:.0f} s")
    describe_gba_save(data)
    return path, data, info


def describe_gba_save(data):
    if len(set(data)) <= 1:
        print(f"  Warning: every byte is 0x{data[0]:02X} - the save area may not have been reachable.")
        return
    banks = [data[i:i + 0x10000] for i in range(0, len(data), 0x10000)]
    if all(b[0] == n and set(b[1:]) == {0xFF} for n, b in enumerate(banks)):
        print("  The save chip didn't answer: only the bank numbers we wrote echoed back.\n"
              "  Try another --map value (see --help).")
        return
    # Pokemon Ruby/Sapphire/Emerald/FR/LG: 4 KB sections ending in signature 0x08012025
    sections = sum(1 for o in range(0, len(data) - 0xFFF, 0x1000)
                   if struct.unpack_from("<I", data, o + 0xFF8)[0] == 0x08012025)
    if sections:
        print(f"  Found {sections} valid Pokemon Gen 3 save sections - this looks like a real save.")


def main():
    ap = argparse.ArgumentParser(description="Export DS saves from an Action Replay MAX DUO (read-only)")
    ap.add_argument("--list", action="store_true", help="only list what's stored")
    ap.add_argument("--out", default="saves", help="folder for exported saves (default: saves)")
    ap.add_argument("--verbose", action="store_true", help="show raw directory entries")
    ap.add_argument("--gba-save", action="store_true",
                    help="read the GBA cartridge's save (needs the patched firmware)")
    ap.add_argument("--banks", type=int, default=2, choices=(1, 2),
                    help="64 KB banks to read for --gba-save (2 for 128 KB Flash, e.g. Pokemon)")
    ap.add_argument("--map", type=lambda v: int(v, 0), default=None,
                    help="cartridge mapping for --gba-save: 0x7 (default), 0x1 or 0x101 "
                         "(needs patch version 2)")
    ap.add_argument("--lab", nargs="+", metavar="ARG",
                    help="lab firmware: probe | peek ADDR LEN | scope ADDR SECONDS | script FILE.json")
    ap.add_argument("--width", type=int, default=1, choices=(1, 2, 4), help="access width for --lab peek/scope")
    ap.add_argument("--map-lab", type=lambda v: int(v, 0), default=0, help="mapping for --lab peek/scope")
    ap.add_argument("--save-type", choices=sorted(SAVE_KINDS),
                    help="override save-type detection for --gba-save (v3 firmware)")
    ap.add_argument("--slow", action="store_true",
                    help="v3: wait for the PC after every packet (slower, for awkward USB setups)")
    ap.add_argument("--update", metavar="FILE",
                    help="install a DUO firmware update file (asks for confirmation)")
    ap.add_argument("--cart", action="store_true",
                    help="read the save straight from the cartridge in the slot")
    args = ap.parse_args()

    t = UsbTransport()
    try:
        run(MaxDuo(t), args)
    except DuoError as err:
        sys.exit(f"\nStopped: {err}")
    except Exception as err:
        if type(err).__name__ in ("USBTimeoutError", "USBError"):
            sys.exit(f"\nThe Action Replay stopped answering ({err}).\n"
                     "Check the console batteries and that USB SERVER MODE is still showing.")
        raise
    finally:
        t.close()


if __name__ == "__main__":
    main()
