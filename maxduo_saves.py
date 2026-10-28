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
  3. In Zadig, give USB ID 05FD DAAE the WinUSB driver (once).
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
    if cmd in REFUSED:
        raise DuoError(f"refusing to send command 0x{cmd:02X}: {REFUSED[cmd]}")
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
    def reckless(self):
        """'v3.1', 'v3' (fast mode broken on real hardware) or None."""
        if not hasattr(self, "_reckless"):
            code, val = self.ask(CMD_INFO)
            self._reckless = {RK40: "v4", RK42: "v4", RK31: "v3.1", RKL3: "v3"}.get(val) if code == CMD_INFO else None
            self._can_run = code == CMD_INFO and val == RK42
        return self._reckless

    def can_run(self):
        """True on Reckless v4.2, which can run this repository's programs from RAM."""
        self.reckless()
        return getattr(self, "_can_run", False)

    def is_v3(self):
        """True when the fast (compressed, CRC-checked) transfers are available."""
        return self.reckless() in ("v3.1", "v4")

    def drain(self):
        """Clear packets the USB chip is still holding from an interrupted run."""
        n = 0
        while True:
            try:
                self.t.read(200)
                n += 1
            except Exception as err:
                if _is_timeout(err):
                    return n
                raise

    def connect(self, wait_s=120, say=print):
        """Say hello; if the DUO isn't in USB mode yet, wait for the person to switch on.
        A hello sent before switch-on waits in the USB chip, and the DUO's own start-up
        then goes straight into USB SERVER MODE (tested on real hardware)."""
        cleared = self.drain()
        if cleared:
            say(f"(cleared {cleared} old packet(s) left over from an earlier, interrupted run)")
        t0, asked, retries = time.time(), False, 0
        self.t.write(packet(HELLO))
        while True:
            try:
                reply = self.t.read(SHORT_TIMEOUT if not asked else 1000)
            except Exception as err:
                if not _is_timeout(err):
                    raise
                if time.time() - t0 > wait_s:
                    raise DuoError("the DUO didn't answer. Is the GBA on and showing USB SERVER MODE?")
                if not asked:
                    say("The DUO isn't answering yet. Switch the GBA on now - it will go straight to\n"
                        "USB SERVER MODE - or choose USB SERVER MODE in its menu. Waiting...")
                    asked = True
                continue
            code, version = parse(reply)
            if code == 0x03:
                return version
            if retries >= 2:
                raise DuoError(f"unexpected answer to hello ({reply.hex(' ')})")
            retries += 1
            say(f"The DUO was still finishing an earlier, interrupted transfer; giving it {BUSY_WAIT_S} s to give up...")
            time.sleep(BUSY_WAIT_S)
            self.drain()
            self.t.write(packet(HELLO))

    def restart(self, wait_s=60):
        """Reckless v4: restart the DUO program (BIOS soft reset) and bring it back into USB mode."""
        if self.reckless() != "v4":
            raise DuoError("restarting needs the Reckless v4 firmware")
        code, val = self.ask(CMD_RESTART)
        if (code, val) != (CMD_RESTART, RESTART_ACK):
            raise DuoError(f"restart not accepted ({code:#x} {val:#x})")
        t0 = time.time()
        self.t.write(packet(HELLO))            # waits in the USB chip; the DUO's start-up answers it
        while time.time() - t0 < wait_s:
            try:
                reply = self.t.read(1000)
            except Exception as err:
                if _is_timeout(err):
                    continue
                raise
            if parse(reply)[0] == 0x03:
                return time.time() - t0
            raise DuoError(f"unexpected answer after the restart ({reply.hex(' ')})")
        raise DuoError("the DUO didn't come back after the restart - switch the GBA off and on")

    def _drain(self):
        while True:
            try:
                self.t.read(300)
            except Exception as err:
                if _is_timeout(err):
                    return
                raise

    def receive_stream(self, cmd, arg=0, timeout=LONG_TIMEOUT, progress=None, max_tries=6):
        """Run a streaming command; returns the (decompressed, CRC-checked) data.

        The DUO's link strictly alternates, so the PC answers every packet the
        GBA sends: CONT for "carry on", and ACK (or NAK = send that block again)
        after the last packet of each block. Nothing is ever sent twice in a row.
        """
        self.stats = {"blocks": 0, "retries": 0, "packets": 0, "raw_blocks": 0, "block_info": [], "payloads": []}
        self.t.write(packet(cmd, arg))
        code, total = parse(self.t.read(timeout))
        if code == 0x15:
            raise DuoError("the DUO rejected the lab program")
        if code != ST_START:
            raise DuoError(f"unexpected stream start {code:#x}")
        out = bytearray(total)
        nblocks = (total + BLOCK - 1) // BLOCK
        reply = packet(ST_CONT, 0)                # answer to the start packet
        for b in range(nblocks):
            want = min(BLOCK, total - b * BLOCK)
            tries = 0
            while True:
                self.t.write(reply)
                hdr = self.t.read(SHORT_TIMEOUT)
                self.stats["packets"] += 1
                problem = None
                idx, lf, crc = struct.unpack("<HHI", hdr)
                raw, dlen = lf & 0x8000, lf & 0x7FFF
                if idx != b or not 0 < dlen <= BLOCK or (raw and dlen != want):
                    problem = f"bad block header for block {b}: {hdr.hex()}"
                else:
                    buf = bytearray()
                    for i in range((dlen + 7) // 8):
                        self.t.write(packet(ST_CONT, b))
                        buf += self.t.read(SHORT_TIMEOUT)
                        self.stats["packets"] += 1
                    buf = bytes(buf[:dlen])
                    try:
                        blk = buf if raw else rle3_decode(buf, want)
                    except DuoError as err:
                        blk, problem = None, str(err)
                    if blk is not None and zlib.crc32(blk) != crc:
                        problem = f"block {b} checksum mismatch"
                if problem is None:
                    out[b * BLOCK:b * BLOCK + want] = blk
                    self.stats["raw_blocks"] += bool(raw)
                    self.stats["block_info"].append((b, bool(raw), dlen))
                    self.stats["payloads"].append(buf)
                    reply = packet(ST_ACK, b)
                    break
                tries += 1
                self.stats["retries"] += 1
                if tries > max_tries:
                    raise DuoError(f"block {b} failed {max_tries} times: {problem}")
                reply = packet(ST_ACK, b | 0x80000000)    # NAK: the DUO sends the block again
            self.stats["blocks"] += 1
            if progress:
                progress(min(total, (b + 1) * BLOCK), total)
        self.t.write(reply)                           # ACK of the last block
        code, crc = parse(self.t.read(timeout))          # the DUO checksums everything first
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
LAB_INTERP = bytes.fromhex("f0b5074601b4a1a638ce002b00d11de1012b00d121e0022b00d127e0032b00d136e0042b00d13de0052b00d146e0062b00d153e0072b00d156e0082b00d167e0092b00d166e00a2b00d17de00b2b00d1b6e00c2b00d1c7e0f6e0834a944203d0824a944200d0efe02580cde72a06120e802a0ad0a02a08d07d4b9c4206d07d4b9c4203d07c4b9c4200d0dde02270bbe7002d00d1b8e722783a7001340137013df9d1b1e7002d00d1aee722883a70120a7a7002340237023df7dca5e7002d00d1a2e722683a70120a7a70120aba70120afa7004340437043df3dc95e7002d00d192e7013dfdd18fe7228800212388934202d10131a942f9d33970090a7970090ab970090af9703b711b0a7b7106377be7022000e001202904090c2d0c2d02002900d171e7012805d022883a70120a7a70023702e022783a7001372b1c002b01d0013bfbe70139eae707ce07b4651900239c46ac421dd223680098834207d00198834204d00298834201d00434f1e7201c00f061f8206800f05ef8606800f05bf8a06800f058f80434634601339c46042bdfd36346042b11d20020c04300f04bf80020c04300f047f80020c04300f043f80020c04300f03ff80133ebe703b023e765190020c0432f4aac4209d22178013448400823400800d35040013bfad1f3e7c04300f028f80fe740b4274e0021081c0823400801d3234a5040013bf9d18a00b0500131ff29f2d965190020c043ac420cd208cc0421021c5a401206920db258000a50401b0a0139f5d1f0e7c04340bc00f001f8e8e63870000a7870000ab870000af870043770470d4a00e00b4a3a70120a7a70120aba70120afa70043701bc381af0bc02bc08479c000008040200045555000eaa2a000e0000000e444f4e45455252212083b8ed00a00302")
LAB_STAGE = 0x02038000
LAB_MAGIC = 0x314C4B52
LAB_OPS = {"end": 0, "w16": 1, "w8": 2, "r8": 3, "r16": 4, "r32": 5, "delay": 6,
           "wait16": 7, "sample16": 8, "sample8": 9, "search": 10, "crc": 11, "crct": 12}
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
ST_ACK, ST_CONT = 0x0E, 0x10       # in-stream answers from the PC
RKL3 = 0x334C4B52        # Reckless v3: its back-to-back transfers stall on real hardware
RK31 = 0x31334B52        # Reckless v3.1: strictly alternating, like Datel's own transfers
UP_ACK = 0x1F            # v3.1 answer to each upload packet but the last
MSG_AT = 0x0203AE00
RLE_ESC = 0xE7
BLOCK = 4096
SAFE_COMMANDS |= {CMD_EXEC_STREAM, CMD_INFO, CMD_UPLOAD, ST_ACK, ST_CONT}


def _is_timeout(err):
    return "timeout" in type(err).__name__.lower() or "timed out" in str(err).lower()


def rle3_decode(data, size):
    out, i = bytearray(), 0
    while i < len(data) and len(out) < size:
        c = data[i]
        if c == RLE_ESC:
            if i + 2 >= len(data):
                raise DuoError("block ends inside a run")
            out += bytes([data[i + 2]]) * (data[i + 1] + 1)
            i += 3
        else:
            out.append(c)
            i += 1
    if len(out) != size or i != len(data):
        raise DuoError(f"block decoded to {len(out)} bytes from {i} of {len(data)}, expected {size} from all")
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
    return {"r8": arg, "r16": arg, "r32": arg, "wait16": 6, "search": 64, "crc": 4, "crct": 4,
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
        if name == "crct" and (addr & 3 or arg & 3):
            raise DuoError("crct needs a 4-byte-aligned address and length")
        if name == "crct" and 0x0E000000 <= addr < 0x0F000000:
            raise DuoError("crct reads 32 bits at a time; the save area is an 8-bit bus - use crc there")
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
    def __init__(self, duo, slow=False):
        self.duo = duo
        self.v3 = duo.is_v3() and not slow      # else Datel-style lab transfers (lab edition, v3, --slow)

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
            code, val = parse(self.duo.t.read(SHORT_TIMEOUT))
            if i + 8 < len(data) and (code, val) != (UP_ACK, i + 8):
                raise DuoError(f"upload out of step at {i:#x} ({code:#x} {val:#x})")
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
        head = 12 + len(LAB_INTERP)
        if getattr(self.duo, "lab_resident", False) and head % 8 == 0:
            # The interpreter is still in the GBA's RAM from an earlier run: send only the
            # header and the operations. The firmware CRC-checks the whole program before
            # running it, so a missing or damaged interpreter is rejected, never run.
            self._upload_fast(LAB_STAGE, prog[:16])
            self._upload_fast(LAB_STAGE + head, prog[head:])
            try:
                data = self.duo.receive_stream(CMD_EXEC_STREAM, 0, timeout=timeout, progress=progress)
                self.partial_uploads = getattr(self, "partial_uploads", 0) + 1
                return self.parse(ops, data)
            except DuoError as err:
                if "rejected" not in str(err):
                    raise
                self.duo.lab_resident = False
        self._upload_fast(LAB_STAGE, prog)
        data = self.duo.receive_stream(CMD_EXEC_STREAM, 0, timeout=timeout, progress=progress)
        self.duo.lab_resident = True
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
    version = duo.connect(wait_s=getattr(args, "wait", 120))
    edition = {"v4": "Reckless v4", "v3.1": "Reckless v3.1", "v3": "Reckless v3"}.get(duo.reckless(), "")
    if edition and duo.can_run():
        edition = "Reckless v4.2"
    print(f"Connected to Action Replay MAX DUO (Datel protocol {version >> 8}.{version & 0xFF:02d}"
          + (f", {edition})" if edition else ")"))

    if getattr(args, "restart", False):
        took = duo.restart()
        print(f"Restarted - back in USB SERVER MODE after {took:.1f} s.")
        return

    if getattr(args, "cart_report", False):
        return cart_report(duo, args)

    if getattr(args, "adapter_test", None):
        return adapter_test(duo, args)
    if getattr(args, "adapter_trace", None):
        return adapter_trace(duo, args)
    if getattr(args, "adapter_probe", None):
        return adapter_trace(duo, args, program="adapter_probe")

    if getattr(args, "lab", None):
        return do_lab(duo, args)

    if getattr(args, "update", None):
        return do_update(duo, args.update, version)

    if getattr(args, "gba_save", False) and (duo.reckless() or getattr(args, "slow", False)):
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
    0xF82D33F9: "Reckless v3 (its fast transfers stall on real hardware - use v3.1)",
    0x99C72FF9: "Reckless v3.1 (fast, strictly alternating transfers, save-type detection, verbose log, lab)",
    0xD1003397: "Reckless v4 (restart can occasionally land in the menu - use v4.1)",
    0xA5303402: "Reckless v4.1 (v3.1 + reliable restart from the PC, Datel's original look)",
    0xDD59ED8A: "Reckless v4.2 (v4.1 + runs this project's programs from RAM, e.g. --adapter-test)",
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

import json


# ======================================================================= v4
# Everything below is new in v4. Lab sessions 1-4 on real hardware shaped it:
#   * the save-tag search took 33 of a save's 58 s -> known games skip it, and
#     unknown games search only up to the ROM's real size;
#   * the lab interpreter was re-uploaded for every step (~1.4 s) -> it stays
#     in the GBA's RAM, and only the header and operations are re-sent; the
#     firmware's own CRC check rejects the run if the resident copy is gone;
#   * the USB chip keeps packets while the GBA is off -> stale ones are cleared
#     before saying hello, and a hello sent early starts USB mode at switch-on;
#   * the Actel masks 0x20-0x9F of every header -> whole-ROM checksums are
#     corrected with one published-logo-derived constant per ROM size.

RK40 = 0x30344B52              # Reckless v4 / v4.1
RK42 = 0x32344B52              # Reckless v4.2: v4.1 + run a program from RAM
BUSY_WAIT_S = 16               # the DUO gives up on a stalled transfer after ~15 s
CMD_RESTART, RESTART_ACK = 0x21, 0x54534552
SAFE_COMMANDS |= {CMD_RESTART}

# Datel commands this tool will never send, whatever happens, with the reason.
REFUSED = {
    0x1C: "DeleteFile - it deletes a file stored on the DUO (another GBA tool sends it at start-up!)",
    0x0A: "ReceiveFile - it writes a file into the DUO's own memory",
    0x0B: "ReceiveFile - it writes a file into the DUO's own memory",
    0x25: "ReceiveFile - it writes to the DUO's memory or a DS card",
    0x27: "DS card status - it sends commands to a DS card's save chip",
}

# Save library of well-known games, by the first three letters of the game code
# (the fourth is the region). Verified on real cartridges in lab session 4.
KNOWN_SAVE_GAMES = {
    "AXV": ("flash128", "Pokemon Ruby"), "AXP": ("flash128", "Pokemon Sapphire"),
    "BPE": ("flash128", "Pokemon Emerald"), "BPR": ("flash128", "Pokemon FireRed"),
    "BPG": ("flash128", "Pokemon LeafGreen"),
}
GEN3_CODES = {"AXV", "AXP", "BPE", "BPR", "BPG"}

# CRC-32 corrections for the Actel's header overlay, per ROM size (see
# fw/logo_correction.py; derived from the standard logo as published in
# devkitPro's gbafix, and checked against a known identity in the tests).
LOGO_CRC_FIX = {
    0x00100000: 0x23EED3AE, 0x00200000: 0x80530ABB, 0x00400000: 0x81F30CB3,
    0x00800000: 0xE5EAB2C5, 0x01000000: 0x62B5CBC1, 0x02000000: 0xC85AA3FD,
}
ROM_SIZES = sorted(LOGO_CRC_FIX)
LOGO_START = bytes.fromhex("24ffae51699aa2213d84820a84e409ad11248b98c0817f21a352be19")   # 0x04-0x1F, visible
MAX_ROM_READ = SEARCH_END - 0x08000000 if "SEARCH_END" in globals() else 0x01FE0000
REFERENCES_FILE = "cart_references.json"


def _open_bus(addr, data):
    return all(struct.unpack_from("<H", data, i)[0] == ((addr + i) >> 1) & 0xFFFF for i in range(0, len(data) - 1, 2))


def rom_size_ops():
    """Read 16 bytes at each power-of-two boundary: open bus there = the ROM ends."""
    return [("r8", 0x08000000 + n, 16) for n in ROM_SIZES[:-1]]


def rom_size_from(reads):
    for n, data in zip(ROM_SIZES[:-1], reads):
        if _open_bus(0x08000000 + n, data):
            return n
    return ROM_SIZES[-1]


def describe_header(h):
    """h = 0xC0 bytes from 0x08000000, or 0x20 bytes from 0x080000A0."""
    if len(h) >= 0xC0:
        h = h[0xA0:0xC0]
    title = "".join(chr(c) if 32 <= c < 127 else " " for c in h[:12]).strip()
    code = h[12:16].decode("ascii", "replace")
    ok = h[0x1D] == (-(sum(h[:0x1D])) - 0x19) & 0xFF
    return {"title": title, "code": code, "maker": h[16:18].decode("ascii", "replace"),
            "revision": h[0x1C], "header_ok": ok}


# ------------------------------------------------------------- Gen 3 saves
_G3 = {0x00: " "}
for _i, _c in enumerate("0123456789!?.-"):
    _G3[0xA1 + _i] = _c
for _i in range(26):
    _G3[0xBB + _i], _G3[0xD5 + _i] = chr(65 + _i), chr(97 + _i)


def _g3text(b):
    return "".join(_G3.get(x, "?") for x in b.split(b"\xff")[0]).strip()


def gen3_summary(data):
    """Trainer, ID, play time and save number of the newest valid copy, or None."""
    best = None
    for slot in range(2):
        secs = {}
        for k in range(14):
            o = slot * 0xE000 + k * 0x1000
            if o + 0x1000 > len(data):
                break
            blk = data[o:o + 0x1000]
            sid, ck, sig, idx = struct.unpack_from("<HHII", blk, 0xFF4)
            if sig != 0x08012025 or sid > 13:
                continue
            n = {0: 3884, 13: 2000}.get(sid, 3968)
            c = sum(struct.unpack_from(f"<{n // 4}I", blk, 0)) & 0xFFFFFFFF
            secs[sid] = (blk, idx, ((c >> 16) + c) & 0xFFFF == ck)
        if 0 not in secs or not secs[0][2]:
            continue
        blk, idx, _ = secs[0]
        tid, sid2 = struct.unpack_from("<HH", blk, 0x0A)
        h, m, s = struct.unpack_from("<HBB", blk, 0x0E)
        info = {"slot": "AB"[slot], "save_no": idx, "trainer": _g3text(blk[0:7]), "id": tid,
                "playtime": f"{h}h {m:02d}m {s:02d}s", "valid_sections": sum(v[2] for v in secs.values())}
        if best is None or idx > best["save_no"]:
            best = info
    return best


def backup_save(data, info, out_root):
    """Dated backup in a folder per game; identical saves are not stored twice.
    Returns (path, note, summary, previous_summary)."""
    folder = os.path.join(out_root, safe_filename(f"{info['code']} {info['title'] or 'GBA'}"))
    os.makedirs(folder, exist_ok=True)
    crc = zlib.crc32(data)
    existing = sorted((f for f in os.listdir(folder) if f.endswith(".sav")),
                      key=lambda f: os.path.getmtime(os.path.join(folder, f)))
    summary = gen3_summary(data) if info["code"][:3] in GEN3_CODES else None
    previous = None
    if existing:
        last = open(os.path.join(folder, existing[-1]), "rb").read()
        previous = gen3_summary(last) if summary else None
    for f in existing:
        p = os.path.join(folder, f)
        if os.path.getsize(p) == len(data) and zlib.crc32(open(p, "rb").read()) == crc:
            return p, "identical to an earlier backup - not stored again", summary, previous
    who = f" {summary['trainer']} #{summary['save_no']}" if summary else ""
    name = safe_filename(time.strftime("%Y-%m-%d %H%M%S") + who) + ".sav"
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(data)
    return path, None, summary, previous


def print_save_summary(summary, previous):
    if not summary:
        return
    print(f"  Trainer {summary['trainer']} (ID {summary['id']:05d}), play time {summary['playtime']}, "
          f"save #{summary['save_no']}, {summary['valid_sections']}/14 sections valid")
    if previous and (previous["trainer"], previous["id"]) != (summary["trainer"], summary["id"]):
        print(f"  !! WARNING: this save belongs to {summary['trainer']} (ID {summary['id']:05d}), but your previous\n"
              f"  !! backup of this game was {previous['trainer']} (ID {previous['id']:05d}). A new game may have\n"
              f"  !! been started on the cartridge. Your earlier backups are untouched.")


class Stages:
    """Per-stage timings, printed as the work goes."""
    def __init__(self):
        self.t0 = self.t = time.perf_counter()
        self.rows = []

    def done(self, name):
        now = time.perf_counter()
        self.rows.append((name, now - self.t))
        self.t = now
        print(f"    ({name}: {self.rows[-1][1]:.1f} s)")

    def total(self):
        return time.perf_counter() - self.t0


# ------------------------------------------------------------ fast GBA save
def gba_save_fast(duo, args):
    lab = Lab(duo, slow=getattr(args, "slow", False))
    if duo.reckless() == "v3":
        print("Note: this DUO has Reckless v3, whose fast transfers stall on real hardware.")
        print("      Using the slower, proven lab method instead (a few minutes).")
        print("      Install the latest Reckless firmware for the fast version.")
    elif not lab.v3:
        print("Using the slower lab method (a few minutes).")
    mapping = args.map if getattr(args, "map", None) else 0x0101
    st = Stages()
    print("Reading the game's header and the first 64 KB of its save...")
    wake = [("w16", MAP_REG, mapping), ("delay", 0, 0x400), ("r16", 0x08000000, 2)]
    # A 32-byte guard read comes first: on real hardware the first few bytes of a run's
    # output were occasionally garbled (twice, both in the first run after a start-up),
    # and the guard keeps that away from the header. Its visible logo bytes show if it happens.
    ops = wake + [("r8", 0x08000000, 32), ("r8", 0x080000A0, 32), ("crc", SAVE_BASE, 0x8000),
                  ("crc", SAVE_BASE + 0x8000, 0x8000),
                  ("r8", SAVE_BASE, 0x10000), ("crc", SAVE_BASE, 0x10000), ("w16", MAP_REG, 0x0120)]
    res = lab.run(ops, messages=["GBA save: reading game"], progress=bar)
    print()
    wake_bytes, guard, header, c0, c1, bank0, crc0 = (d for _, d in res)
    st.done("header + bank 0")
    if guard[4:32] != LOGO_START:
        print(f"  Note: the start of this run's output was garbled (seen before on real hardware):")
        print(f"        {(wake_bytes + guard).hex(' ')}")
    info = describe_header(header)
    for attempt in range(2):
        if info["header_ok"]:
            break
        print(f"  The header's checksum doesn't match ({header.hex(' ')}); reading it again...")
        header = lab.run(wake + [("r8", 0x08000000, 32), ("r8", 0x080000A0, 32),
                                 ("w16", MAP_REG, 0x0120)])[2][1]
        info = describe_header(header)
    print(f"  Game: {info['title']} ({info['code']}, revision {info['revision']})"
          + ("" if info["header_ok"] else "   (header checksum does NOT match)"))
    blank = {zlib.crc32(b"\xFF" * 0x8000), zlib.crc32(b"\0" * 0x8000)}
    info["mirror"] = _w(c0) == _w(c1) and _w(c0) not in blank
    forced = getattr(args, "save_type", None)
    known = KNOWN_SAVE_GAMES.get(info["code"][:3])
    if forced:
        info["kinds"], info["tags"] = {forced}, [f"--save-type {forced}"]
    elif known:
        info["kinds"], info["tags"] = {known[0]}, [f"known game ({known[1]})"]
    else:
        print("  Not a game I know - looking for its save library...")
        sres = lab.run(wake + rom_size_ops() + [("w16", MAP_REG, 0x0120)])
        size = rom_size_from([d for _, d in sres[1:-1]])
        found = lab.run(wake + [("search", 0x08000000, min(size, MAX_ROM_READ),
                                 (_w(b"FLAS"), _w(b"SRAM"), _w(b"EEPR"))), ("w16", MAP_REG, 0x0120)])[1][1]
        tags = []
        for i in range(0, 64, 16):
            if _w(found[i:i + 4]) != 0xFFFFFFFF:
                tags.append("".join(chr(c) for c in found[i + 4:i + 16].split(b"\0")[0] if 32 <= c < 127))
        info["tags"], info["kinds"] = tags, classify_tags(tags)
        st.done(f"save-type search ({size >> 20} MB ROM)")
    print(f"  Save library: {', '.join(info['tags']) or 'none found'}")
    kind, size, banks, warning = plan_read(info)
    print(f"  Plan: {kind}, {size // 1024} KB in {banks} bank(s)")
    if warning:
        print("  Note: " + warning)
    if zlib.crc32(bank0) != _w(crc0):
        print("    The GBA's own checksum of bank 0 disagreed; reading again...")
        bank0 = None
    data = bytes(bank0[:min(size, 0x10000)]) if bank0 is not None else b""
    for b in range(0 if bank0 is None else 1, banks):
        ops = list(wake)
        if b:
            ops += _switch(b) + [("delay", 0, 0x400)]
        ops += [("r8", SAVE_BASE, 0x10000), ("crc", SAVE_BASE, 0x10000)]
        if b:
            ops += _switch(0)
        ops += [("w16", MAP_REG, 0x0120)]
        for attempt in range(2):
            res = lab.run(ops, messages=[f"Reading bank {b} (64 KB)"], progress=bar)
            print()
            chunk, gba_crc = res[-2][1], _w(res[-1][1])
            if zlib.crc32(chunk) == gba_crc:
                break
            print(f"    The GBA's own checksum disagreed ({gba_crc:08X}); reading again...")
        else:
            raise DuoError(f"bank {b} gave different data twice - not saving it")
        data += chunk[:min(size, 0x10000)]
        st.done(f"bank {b}")
    crc = zlib.crc32(data)
    path, note, summary, previous = backup_save(data, info, args.out)
    if lab.v3:
        lab.run([], messages=["Save copied to PC", f"{len(data) // 1024} KB, CRC {crc:08X}"])
    print(f"\n{'Already backed up' if note else 'Saved'}: {path}\n  ({len(data):,} bytes, CRC {crc:08X}) in {st.total():.0f} s"
          + (f" - {note}" if note else ""))
    print_save_summary(summary, previous)
    describe_gba_save(data)
    return path, data, info


gba_save_v3 = gba_save_fast          # the tests and lab_runner use this name


# --------------------------------------------------------- cartridge report
def load_references():
    """Optional verified-dump checksums, {"AXVE rev 2": "CRC32 hex", ...} - add your own."""
    try:
        with open(REFERENCES_FILE) as f:
            return {k.upper(): int(v, 16) for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


def cart_report(duo, args):
    lab = Lab(duo, slow=getattr(args, "slow", False))
    st = Stages()
    m = args.map if getattr(args, "map", None) else 0x0101
    wake = [("w16", MAP_REG, m), ("delay", 0, 0x400), ("r16", 0x08000000, 2)]
    print("Cartridge report (reads only)...")
    res = lab.run(wake + [("r8", 0x08000000, 32), ("r8", 0x08000000, 0xC0)] + rom_size_ops()   # 32-byte guard first
                  + [("crc", SAVE_BASE, 0x8000), ("crc", SAVE_BASE + 0x8000, 0x8000), ("r8", SAVE_BASE, 16),
                     ("w16", MAP_REG, 0x0120)], messages=["Cartridge report"])
    d = [x for _, x in res]
    header, sizes, c0, c1, save16 = d[2], d[3:3 + len(ROM_SIZES) - 1], d[-3], d[-2], d[-1]
    info = describe_header(header)
    size = rom_size_from(sizes)
    st.done("header and size")
    read = min(size, MAX_ROM_READ)
    known = KNOWN_SAVE_GAMES.get(info["code"][:3])
    secs = max(5, round((size >> 20) * 3.7))            # measured: about 3.6 s per MB on real hardware
    print(f"  Checksumming the whole {size >> 20} MB ROM on the GBA (about {secs} s)...")
    ops = wake + [("crct", 0x08000000, read), ("crct", 0x08000000, 0x100000), ("crct", 0x08000000, 0x100000),
                  ("crc", SAVE_BASE, 0x10000), ("crc", SAVE_BASE, 0x10000),
                  ("w16", WAITCNT, 0x4317), ("crct", 0x08000000, 0x40000),
                  ("w16", WAITCNT, 0x401B), ("crct", 0x08000000, 0x40000),
                  ("w16", WAITCNT, 0x0003), ("crct", 0x08000000, 0x40000)]
    if not known:
        ops += [("search", 0x08000000, read, (_w(b"FLAS"), _w(b"SRAM"), _w(b"EEPR")))]
    ops += [("w16", MAP_REG, 0x0120)]
    timeout = int((size >> 20) * 4000 + 120000)
    res = lab.run([("w16", WAITCNT, 0x0003)] + ops, timeout=timeout, messages=["Checksumming the ROM"])
    d = [x for op, x in res if op[0] in ("crct", "crc", "search")]
    rom_crc, r1, r2, s1, s2, t_game, t_fast, t_default = (_w(x) for x in d[:8])
    tags = []
    if not known:
        found = d[8]
        for i in range(0, 64, 16):
            if _w(found[i:i + 4]) != 0xFFFFFFFF:
                tags.append("".join(chr(c) for c in found[i + 4:i + 16].split(b"\0")[0] if 32 <= c < 127))
    st.done("checksums")
    corrected = rom_crc ^ LOGO_CRC_FIX[size] if read == size else None
    refs = load_references()
    ref = refs.get(f"{info['code']} REV {info['revision']}")
    blank = {zlib.crc32(b"\xFF" * 0x8000), zlib.crc32(b"\0" * 0x8000)}
    report = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"), **info,
        "logo_start_ok": header[4:0x20] == LOGO_START, "rom_size_mb": size >> 20,
        "rom_crc32": f"{corrected:08X}" if corrected is not None else None,
        "rom_crc32_as_read": f"{rom_crc:08X}",
        "stable": r1 == r2 and s1 == s2,
        "timing_game_ok": t_game == t_default, "timing_2cycle_ok": t_fast == t_default,
        "save_library": known[0] + " (known game)" if known else ", ".join(tags) or "none found",
        "save_halves_mirror": _w(c0) == _w(c1) and _w(c0) not in blank,
        "save_start": save16.hex(),
        "reference": f"{ref:08X}" if ref is not None else None,
    }
    if ref is not None:
        report["verdict"] = ("genuine - matches the verified dump" if corrected == ref
                             else "does NOT match the verified dump - reproduction, other revision or bad contacts")
    else:
        report["verdict"] = ("consistent with a genuine cartridge (no reference checksum on file yet)"
                             if info["header_ok"] and report["logo_start_ok"] and report["stable"]
                             and not report["save_halves_mirror"] else "worth a closer look")
    print_cart_report(report)
    with open("cart_reports.jsonl", "a") as f:
        f.write(json.dumps(report) + "\n")
    print_cart_table()
    return report


def print_cart_report(r):
    yes = lambda v: "yes" if v else "NO"
    print(f"\n  {r['title']} ({r['code']}, maker {r['maker']}, revision {r['revision']})")
    print(f"    Header checksum valid:      {yes(r['header_ok'])}")
    print(f"    Visible logo bytes correct: {yes(r['logo_start_ok'])}   (0x20-0x9F are masked by the DUO)")
    print(f"    ROM size:                   {r['rom_size_mb']} MB")
    print(f"    Whole-ROM CRC-32:           {r['rom_crc32'] or 'n/a (ROM larger than the DUO can read)'}"
          f"   (as read through the DUO: {r['rom_crc32_as_read']})")
    if r["reference"]:
        print(f"    Verified dump CRC-32:       {r['reference']}")
    print(f"    Repeated reads identical:   {yes(r['stable'])}")
    print(f"    Reads OK at games' timing:  {yes(r['timing_game_ok'])};  at 2-cycle timing: {yes(r['timing_2cycle_ok'])}")
    print(f"    Save library:               {r['save_library']}")
    if r["save_halves_mirror"]:
        print("    !! The save's two 32 KB halves are identical - typical of an SRAM chip on a reproduction.")
    print(f"    Verdict: {r['verdict']}")


def print_cart_table():
    try:
        rows = [json.loads(l) for l in open("cart_reports.jsonl")]
    except OSError:
        return
    latest = {}
    for r in rows:
        latest[(r["code"], r["revision"])] = r
    if len(latest) < 2:
        return
    print("\n  All cartridges reported so far:")
    print(f"    {'Game':14} {'Code':5} {'Rev':3} {'ROM':5} {'CRC-32':9} {'Stable':6} Verdict")
    for r in latest.values():
        print(f"    {r['title'][:14]:14} {r['code']:5} {r['revision']:<3} {r['rom_size_mb']:>3} MB "
              f"{(r['rom_crc32'] or '-'):9} {'yes' if r['stable'] else 'NO':6} {r['verdict'][:40]}")



# ===================================================================== v4.2
# Reckless v4.2 can run a program from the GBA's RAM (command 0x23). The tool
# only runs the programs built from this repository (firmware/programs.py),
# embedded here and checked by the tests against a fresh build.
CMD_RUN, RUN_ACK = 0x23, 0x214E5552
PROG_POLL, PROG_STOP, PROG_PUT = 0x2A, 0x2B, 0x2C
SAFE_COMMANDS |= {CMD_RUN, PROG_POLL, PROG_STOP, PROG_PUT}
PROGRAMS = {
    "adapter_detect": bytes.fromhex(
        "524b52319c0100000bb29eac01000000f0b5071c4b4e00200021705004312029fbd34c4c4c4908804c4820810020206020898021084320812089000601d400f045f8f068002808d0b069002805d10120b0613f483b6900f074f8bb6800f071f80028e9d038487b6800f06bf83648c1782b290bd04268012a02d000f014f800e071692a20bb6900f05cf8d5e700f00bf80d1c2b20bb6900f054f800202081281cf0bc02bc084731690904090c326812041143f268002a08d001231b051943284b9a4202d101235b051943704720b520687061316901313161010c0204120cf368002b17d1b368994212d13568042d0dd27168c9430904090c8b420bd1d9430904090c8a4206d10135356003e0f26001e0002131603568042d03d20c496b00c95a00e00f497160d3431b041b0cb360090419432160208980210843208120bc02bc084718478c81030284810302648103026c810302200100043401000400100000018000004e494e54454e444f4164617074657220636865636b20616e737765726564000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
    "adapter_probe": bytes.fromhex(
        "524b5231142700001afdb4ed01000000f0b503af99b01190dc490020888101220b9212030e920a80d949ff250d60d949042210920a60d84909882940d74a1160010012aa5054491c1829fad112a8152109910173d2498180d24812900021d24812aa5218435c1374491c0829f8d100210a00435c12ac6418a478a218d218491c0829f6d1d04312a9c8730020de4c12a90a5c09184b781b029a188b781b04d218c978090651182150001d1828efd1d7480068411e01b404bc8a41d107b34c2200083a0f921160022801d0882000e080200e9908182080ce48007800280fd000202080ab480078ab49096809020918c94800680004084319b0f0bc02bc08470b98c1070d9100020c902088000601d500f05dfc0f980668c04806609f48008828409e4a11681060814200d9e430401abb4a116840181060ba49086096480068810001a251588f46c0465d810302c1810302ab810302ab8103025d8103023100b14a1140b14a914202d1042800d1e3e0914200d0c4e0a54800688000ac4a1158491c11500021aa480160aa480160aa4801600120804a1060ff20320a0640a74b1e600240a74802601cd0c0e0811e4a424a41a0490a60022834d10020a249086032e09d4800681f2802d881009f4a5650401c994908609a490968884200d2a6e0974806688c4a1068401c106001220023272e04b401bc00d81800242e00d31a00934b1b68002b0b9b00d01300002b0b9a00d10200012a00d0a9e0002900d09fe00a009fe0884800688a490968884209d20321584a1160411c834a116080008549085875e08548007801286dd10e9e30000830208001200a9001f07df800286ad026800020089001f076f8002863d07b4800787b490978484a12882a40484c23682260934200d9e432d21a644e3368d41834600e9ad21c3d4b1a80089a1292002900d10a90119800f0a6fe00280b9801d158480078c00700d088e3e4260a9846430a98002811d0344800882840344a11681060814200d9e430401a504a116840181060001bb04200d37ee1119800f083fe00280b9801d146480078c007e0d065e30d998e4201d1002819d0042800d0bae030005449084054490a68002a00d10ee153490b68002126e100201a4908600d9800f041ff4be326800020164908603348006800280d9900d101000f9a1160022801d0882000e080200e990818208036e33a480268300000f0eefe34480668364a00243948047036480460300010382d2800d950e10aa14000085a4000874428010004389b03023c9b030206000004409b0302341200007d7fa10fd49a0302a701ae01bc018601c4016a01600190014b01d0011103d901cb01710071007a01890089004b014b01f7019501de0153014b014b014b014b014b014b014b014b0107024b01110358011103d3004b01d3004b014b014b014b014b01d501549b03026c9b0302449b0302189c0302709b030220a70302489b03020000ffff00006699749b0302809b0302849b03028c9b0302509b0302889b0302a09c0302989b03021c9c0302a49c0302209c0302a89c0302ec9c0302d09c0302ffff0000909b0302cc9c03021e2e00d1bfe0ce480068032800d1bae01d2695e20021cb480160cb480160cb4801600420ca4a10603000ca4a10404fe0c9480068002800d156e2212ec74905d0401cc54a1060022800d862e20b98c44a1070c44808600520b949086000200100c14a5054491c4429fbd100200100bf4a5054491c5029fbd10020bd490860bd490860bd490870bd4908700100bc4a5054491c2029fbd100200100ba4a5054491c5029fbd10020b8490870b8490860b8490860212e00d03fe207200021a94a00f009fe39e20b98b349087039e2b2490968320c91421cd19d4a1368032b21d804004c4000229b4eb44200d026e1954c24884c40b44200d020e15b1c94490b6000220b99a64c032b08d90b9bdb035b1c0fe000228e490a600b9913005b00a04ef35a06e0894908600121c9034b1c02000b99844e3360874e7040974e3060002913d0002a11d097490868401c086001200026310000f0bffd944806607d4806600f9806600e988830c0e6190408180f9908600e988030b9e67a4800788c49002800d028e1273127e1754c894820600020894b21181a5c0a71401c1828f9d107208649086004021e2e00d0cae10020644908601e26c5e10120804908600220674908606e26bce10b98734908702726b8e10120794908600006614908603326afe100242000764b115c1954401c1828fad11626a6e100200100594a5054491c1c29fbd107206d4908600c99491c91610402099e96e104204a49086010886549401a414241414d4801601f268ae101206249086043480068ff2800d15fe100065ee110685f49086017267be15e480068032802d8401c5c49086042480078012801d100f081fe0b984b490870252669e100243c48047033480460102662e101204e49086037480078002820b402bc00d1010033480160112653e10120474908602f484a49016012264be10120434908602b480560142644e10320224908601c2640e102201f49086019263be11d4805603d2637e101201b4908601b2632e12548006881000dd00321c9434143384a1c4b14781c704c1c5b1c521c8c4210b402bcf6d22e490860040232480440262619e12d480068032802d8401c2b490860114800782426012800d00ce100f01dfe09e10024044804600b480470302602e10b99174cece6389b0302c89c0302909b0302cc9c03023c9b0302ffff0000f09c0302209c0302d09c0302412c00004c9e0302f49c0302449d03021c9c0302489d03024c9d0302509d0302709d0302c49d0302c09d0302c89d0302a89c0302c49c030228010004dc9a0302949b03026c9b030200006699a57d0000549b0302a49c0302d49c0302ec9c0302909e030217018300cc9d030200ff996628310791d34cd44e012800d00ce3d34800780128d2490ad1d2480078002806d1d14a1068884204b402bc00d2f7e2cb48089b0370cd490868401c0860cc4a1068010416d5002121710007800b5168926849030143500208439100c54a5118c968c9020143080a6071080c10990843a07103230122d003bf490c68044264d1be4800680590002800d137e1069300210a00b154521c0e2afbd100210a00b74bad4c9954521c0e2afbd1b5490978012900d0a9e10021b24a1170b2484278002a00d1a1e102781f2313400370425c43185b781b029a187252891c0e29f6d1aa480168491c016031880a00aa43a848824200dd0de15f2109028a4200d127e1332149028a4200d07fe10b99a14801707188a148016078e1a048801fa0490860212e21d100209f491de000209c4b9e49097800299d4a00d10a0080181860132615e000280dd0012000060ce004902130ce6a8442009600d1072684425bd0486c5be08e48801f08600120fa4908600c9c0220fa490860ff2131402143f848803048408749c86700f0f5fbf64c119800f011fb1098c043f1490968014233d07f4800882840f04a11681060814200d9e430401aee4a116840181060ed490968401a0c993b3188421ed9ea490868401c0860e948026803200026310000f093fb0e982080de480660e548006800280d9900d101000f9a1160022801d0882000e080200e9908182080de480078012801d0fff75bfbfff744fb080048300190049822300594029084420693039216d14b68109c98000818006bb04208d3581c032303404b60641ef4d198000818006b009a141a886a84420ad3040008e00c6c886a844201db040001e0e01784434b683a48069a80180090021d14705802059e3607b60b0643980008188168c902314321430e0a5670090c109e31439170002c0bd0006b01990a180098c61d200011783170521c761c401ef9d1059a0298824208d1581c032101402c48416006980019c31c18e00499203106980019c31c8a4211d126202549086009200026052231001c0000f009fb2300a34806602048039a0260164c01e0154c039a002b00d07ce1257113007de189231b029a424ad01121c9028a4273d19949096800296fd10121964a1160002191607288182a00d31822924b5a600a00914b9b181974521c402af9d15ce00b99f548017058e0cc9d0302ea9e0302cc9e0302ffff00004c9d0302d09e0302d49e03024c9e0302f49c03021c9c0302dc9e0302489d0302989e0302f89e0302ff8700003c9f0302409f030206000004209c0302389b0302d09c0302412c01002940172929d8ec4a1268012a25d10c224a43e94b981802241619761e3f2e03d80619e04b1b5bf381a41c0e2cf4d101228a40e14b9968114399605a680323db43db1c93404b40591cd34cd64e05d10021d94a1160d168491cd160d0480168491c04910160059b032b27d1d34ad168002923d000201118097cd04b1954cf49401c4028f7d1087d0a00497d09020818917d09044018d17d090642180a200024210000f058fa0423be490b60bc4804940460c149c968c24a1160b74cbd4a1178012909d10020107033204002b7490880b948006848809de0b548017801290bd1002101705f200002b04a1080b84802680d2000f030fa8de0b34ad16900294ad0516907290ad88020a84b98801120c0021880d0685880481c50617be0d168106988422ed201008920000208430b009e4908800c200393584306ca05900818049000210e0005988319934202b410bc01d20498845d5b1c934202b408bc02d2049883195b78914880191b021b194380b61c0c2ee7d10398401c93490861874c894e48e018009169491c91610829069b42d30021d1610300581e0328069b3bd802a1085c40008744a49c03021bc826313c9b03020000669928010004409b030220a70302489b03024c9b0302509b03026c9b0302449b0302d89e0302709d03020120724a9080022050806e49086077200002108010e00498f1280dd36f480068002809d101206749086005e004980b2802d300f049fd069be318052098710021597146201871da1d181d07241955641c4d2cfbd15d4c635c5354491c0e29fad111300021554c725c4254491c0e29fad10b9851490870069b4933980708b401bc06d01800211800220a71401c8107f9d12360800857490022464b1a70401c086012a9079e300000f0b5fc002837d0012000f06afa002832d00e9c0b3438480480089818901898272805dc1898401c189018982828f9db12a90d9800f09bfc00281dd00d98401e12990140434806408036b14214d1012000f047fa00280fd0284804800400002000f03ffa2948007800280dd1022000210a9a00f02ef907e0129a224802600420002100f026f91c4c0e9820801c48089901601b48006800280d9900d101000f9a1160022801d0882000e080200e990818208051e500241b494c80a1200002088000f075fc012005901121c8223c231c4e1d4809c6069b32607160b460f4603461059870610f4e03200d4908600b480460094c4ee7c046449f0302280100043c9b03026c9b0302709b0302d09c03024c9d0302cc9e0302cc9d0302d89e03021c9c0302ea9e0302dc9e0302449f03023c9f0302409f0302709d0302fc9e0302c09d0302509d0302459f0302c89d0302449d03022f006619002a04d0030019705b1c521efbd17047d0b502af002a06d003000c781c705b1c491c521ef9d1d0bc02bc0847f0b503af81b00500806800f09df80024002800d173e069683b4e300000f095f832797079f1782c2913d02b2927d13948007839490968090208183849096809040143aa692b2000f081f835480124047055e02e4909684b08a62b0dd800028018b27912048018f279120680188a00284b9850491c25480160aa692c203de00024072a37d83f2108407a44127952009744031b0e100b1e25131e4800781e490968090208181d4915e0800023490ae024480be08000214905e0022810b402bc1ad880001849095816e01e48016813e01848006818490968090401430ce0022810b402bc08d8800010490a58104908580104114300e0e143aa692a2000f027f8200001b0f0bc02bc084710a5030218a50302d89f0302389b03023c9b0302189c0302449b0302b89c0302749b0302ac9c0302949b03024c9b030220a6030220a503021ca50302709b0302004708471047c046f0b503af154b1c6801252d0409020143002c15d0601e3f260640b000104b1e58104b33408b420bd1104b9e4208d80e4b1b58934204d10a490818711901600ae0a000ff2303400848c25029430448c150601c02490860f0bc01bc00471ca5030220a50302ffff000020a60302fffffefff0b503af85b0029001252c0320000830574e3080280000f0c3f800286dd00194544c2268002a07d1534800688000534b1e58761c1e504e4e514b1868002807d04d48006880004f4e3158491c3150484e002003901860501c20604b48006800286cd04548026850425041029b1900814030000838016001998031002b73d5022a71d042480588ff20029005404048016805602b00a94200d9e433581a3d4a116843181360019a1600803688321900304c2088000658d526802280039804900498012805dc0498401c049004980228f9db2e480488029804402d4804602000a54200d9e430401b41182a480160c81aa02810b420bc1e4cdbd936e027480168491c016026480268032000252900fff740ff1d480560184908683480401c032800d328000860411e01b404bc8a41d1073200083a1160022814d0882013e030000838e90701600198803030800b4d286802280ad0002000f024f8002805d02868019ce0e780202118318005b0f0bc01bc004728010004809b03026c9b0302ac9c0302849b0302b89c03023c9b030206000004409b030220a703024c9b0302509b0302f0b503af82b01a490a88ff231a40194c216822601400914200d9e434611a164d2c680c192c6001210191002100912100124d2d886d07ed0f854213d00c4d2d881d400c4e35602e00aa4200d9e436b21a5118094a11600a1ba12a20b404bce7d3009800e0019802b0f0bc02bc0847c04606000004409b030220a7030228010004f0b503af8fb0f5480168491c0160f4480068c004c10e4b424b4101200690c603022900d29ee108004042089148410090ec4800680990ec48006802900020059001000b96e6480d91451869798806000f041f621e94410a061216002a01da059b00e0012323402c790e0236190c961f2626400e962d1d0a9589071ed4002b1cd06106890f01280b9e00d1a9e00228db4c0e9d00d190e0032800d036e10b0030004230216801220092814204d10020e060300043302060190027e109990b9e31404d1ea9410b40012b4cd13300223361068a0f02280e9d02d1099999420dd0012804d131002131099c8c4206d003280fd130002330099981420ad10c98c005810f9000b84c201884688c4201d1012181619000b44909188869002800d1f6e00a00183200200c9010608a68521c032414408c60b148099c2218012a59d8a948856a026c521b0264100032002132944200d0ad000e6bad190d63002800dce0e00b9e0d98fae0002a0e9d00d5cfe004289f4c00d0cbe0102d00d9c8e0002d0bd00a98801c21002a0003780b70491c401c521ef9d10f2d05d8280000212154401c1028fad1069892490870924908700299491c8d4802910160a9e00791e169481e81413200413220689042069a00d10a00002a18d0330042339842069807994ad060e033004133854c2068984233d1002041e03000233084420d9800d088e033002033784da8e03c20205ca06220003c3041788278120251182162c178027912025118427912048918807900060818606220002c3000210a000300203b1960236a534308c0521c042af6d1606ae0613300423306980de00791002001006054491c4429fbd1012060600720e06133004133002007992360039032000c98c005860f880020180c90c068401c0325019528400496b04205d107910398002804d1082103e016000e9d31e0202116000e9d0028544a01d122003c32002d14d00a98831c2c000c98c56a2c308d4202d21e78565505686d1c0560641e5b1c002cf4d1474c23680b9e0e9d30004230834206d1206a019a42430c99c86a8018c8620c990c3104980860e069401be0610799a1600d984019811c7b2923d80898401a01281fd991e6304d0c996960099890420b9e0ed12800303000210200283a01241460aa6a4a4304c0491c0429f5d1e96a02e0a960330023330d98296409932b600e9dd7e7009b581e834131004331244802688a4200d10693069900292dd146210160002119480a008154521c502afbd11e4a1e4b012105248462c16207240464436482640300483300229a719a8083189960121d102afad12136066012480178427812025118427c12048918807c00060a1808200021fff7cefc0fb0f0bc01bc0047949e0302989b0302f49c0302a89e0302989e03024c9d0302489d0302df7fffff4c9e0302ac9e030200050001e49a0302f0b503af81b00c00050000263000fff7b1fd00280bd008480560084901818031018101890906fcd4006820600126300001b0f0bc02bc08472001000403100000b0b502af002119480a008154521c3c2afbd1002216498b5c8354521c0e2afad100222c2384188d5ce554521c0e2af9d1c9218176cf214176be21017612214175342101758021c17440214174032101740021ff224318da76491c0529fad12a2101224254b0bc01bc0047c046459f0302189b0302f0b503af83b06c480168032927d86b4b2022102600250124880002a1085821008746c046eb980302c9980302d5980302df9803020021112625000a000b000ae0604b10262100320005e05d48031d0288142600210d005c48c069002803d003b0f0bc01bc0047002920d05a480078002842d05b4915000a6895427fd901200024514e34617461b461f06198183060a81afc2800d3fc2082180a6001000b31564a4a43110c7060b060f160d8e74749c868474a13689842d2d0106046480078002839d047480268534253411d40424b1b689c1afc2c00d3fc24012d07d10025414e4819007c70556d1c402df9d1a0183c4908609842b3d341e0002a029241d011000096019438480824384a1d78684025004608c00700d0564030006d1ef8d15b1c491ef2d132494840ff21020a08400a40019c009e27e0888a274d286001229302984200d80022098ab14210b408bc00d11300012b0ad1214a13685b1c1360090442180c200021fff7a3fb286819490c701b4900220a60002800d070e700f03ef86de7ee22de201b49029b0b71ca708870002048700e7006220b4d06c52a606c60a860e86028616c610d4a106009480470180a487153e7c046c89d0302e89a0302d89f0302089b0302509d0302709d0302c09d0302c49d0302909f0302949f03028c9f0302989f03022111000008840000ffff0000819f03025615000080b500af0b48002101700b490868401c0860032803d88100094a515800e0074902040a430b200021fff744fb80bc01bc0047c046c49d0302c89d0302ffff0000289b0302becfc9ffffffffff4e494e54454e444f0500000008000000000000000300000000000000020000001000000004000000000000001400000000000000010000000000000047616d65467265616b20696e632e00d41000000011000000100000001400000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
    "adapter": bytes.fromhex(
        "524b5231682d0000ae2ea51c01000000f0b503af9db01590d9490020888101220e9213030b80d749ff260e60d649042214920a60d54909883140d54a1160010016aa5054491c1829fad10d9316a815210b910173cf498180cf4816900021cf4816aa5218435c1374491c0829f8d100210a00435c16ac6418a478a218d218491c0829f6d1d04316a9c8730020df4c16a90a5c09184b781b029a188b781b04d218c978090651182150001d1828efd100200f9001f02bf9d64908600e9d280001f025f9d4490860cf4800883040ce490860ab4c20000838e9071390129101600d98883010902080cc48007800280fd000202080c2480078c249096809020918c7480068000408431db0f0bc02bc08470e98000211902088000601d500f047fc13980568bf480560b74800883040b64a11681060814200d9e430401aba4a116840181060b9490860ae480068810001a251588f46c0466981030297810302bb81030291810302698103022900b04a1140b04a914200d093e0042800d090e00020a04908601398129901601098208000f00efca848006814e0a84800681f2802d88100a64a5550401ca4490860a5490968884200d2a6e0a348056882e00f989d490860a1490968884209d203218d4a1160411c984a116080009d49085893e09c480078012800d08ae00d9d28000830208001200c9001f0fbf9002800d1a5e0258000200a9001f0f3f9002800d149e191480078914909787b4a128832407b4c23682260934200d9e432d21a7e4d2b68d4182c600d9ad21c524b1a800a9a1692002900d10c90159800f0a6fe00280e9801d172480078c00700d0a8e3e4250c9845430c98002811d0674800883040674a11681060814200d9e430401a6a4a116840181060001ba84200d3aae1159800f083fe00280e9801d160480078c007e0d085e3914233d10021644801600120554a1060ff202a0a0540634b1d6002406148026020d1564a1068401c106001220023272d04b401bc00d81800242d00d31a005f4b1b68002b0e9b00d01300002b0e9a00d10200012a35d100292cd10a002ce040480f990160129801f046f850e312998d420cd100280ad152490868401c0860494802680520002100f0baff2be7042800d0a9e028004c4908404c490a68002a00d1ffe04a490b68002117e125802d480f99016017e73a480268280000f0a0ff39480568364a00243a48047037480460280010382d2800d952e10aa14000085a4000874428010004b49f0302b89f030206000004bc9f0302341200007d7fa10f249f0302a401ab01b9018801c1016c01620192014d01cd010703d601c801770077007c01910091004d014d01f4019701db0155014d014d014d014d014d014d014d014d01ff014d0107035a010703db004d01db004d014d014d014d014d01d201b49f0302b89f030206000004bc9f0302d09f0302e89f0302ec9f0302c09f030288a00302f09f030274ad0302c49f03020000ffff0000669910a10302f89f030208a00302f49f0302cc9f030214a1030290a0030218a10302aca2030290a203028ca00302fc9f0302ffff000000a003028ca203021e2d00d1bbe0c7480068032800d1b6e01d2585e20021c4480160c4480160c44801600420c34a10602800c34a104051e025806be6c1480068002800d144e2212dbf4905d0401cbd4a1060022800d850e20e98bc4a1070bc4808600520b149086000200100b94a5054491c4429fbd100200100b74a5054491c5029fbd10020b5490860b5490860b5490870b54908700100b44a5054491c2029fbd100200100b24a5054491c5029fbd10020b0490870b0490860b0490860212d00d02de207200021a14a00f0b3fe27e20e98ab49087027e2aa4909682a0c91421cd1954a1368032b21d804004c400022934dac4200d016e18d4c24884c40ac4200d010e15b1c8c490b6000220e999e4c032b08d90e9bdb035b1c0fe0002286490a600e9913005b00984deb5a06e0814908600121c9034b1c02000e997c4d2b607f4d68408f4d286000290dd0002a0bd08f490868401c086001200025290000f069fe76480560d8e519040818139908600d988030d5e5754800788649002800d01ae1273119e1704c834820600020834b21181a5c0a71401c1828f9d107208049086004021e2d00d0bee100205f4908601e25b9e101207a4908600220624908606e25b0e10e986e4908702725ace101207349086000065c4908603325a3e100242000704b115c1954401c1828fad116259ae100200100544a5054491c1c29fbd10720674908601199491c916104020b9d8ae104204549086010885f49401a41424141484801601f257ee101205c4908603e480068ff2800d153e1000652e110685949086017256fe101f00ef83f480078012801d101f040f80e9849490870252562e10024394804703048046010255be101204b49086035480078002840b402bc00d101003048016011254ce10120444908602d4845490160122544e10120404908602948066014253de103201f4908601c2539e102201d490860192534e11b4806603d2530e10120184908601b252be12248006881000dd00321c9434143344a194b14781c704c1c5b1c521c8c4210b402bcf6d22a49086004022e480440262512e100f0b1ff114800782425012800d00ae100f0e1ff07e10024044804600b480470302500e10e99174cfce6b49f030288a2030200a003028ca20302b89f0302ffff0000b0a2030290a0030290a20302412c00000ca40302b4a2030204a303028ca0030208a303020ca3030210a3030230a3030284a3030280a3030288a3030218a1030284a20302280100042c9f030204a0030200006699a57d0000d09f030214a1030294a20302aca20302170183008ca3030200ff996628310991cf4c012800d000e3ce4800780128ce490ad1ce480078002806d1cd4a1068884204b402bc00d2ebe2fe480a9b0370fe490868401c08600f208503fc4a1068010417d50021fa4b1971800328405168926849030143500208439100f44a5118c968c9020143080a5871080c149908439871032306950122d003ee490d68054263d1ed4800680790002800d12be1089300210a00a154521c0e2afbd100210a00e74b9954521c0e2afbd1e5490978012900d07be10021e24a1170e2484278002a00d173e102781f2313400370425c43185b781b029a186252891c0e29f6d1da480168491c016021880a00b243d848824200dd02e15f2109028a4200d11ce1332149028a4200d051e10e99d14801706188d14801604ae1d048801fd0490860212d21d10020cf491de00020cc4bce4909780029cd4a00d10a0080181860132515e000280dd0012000060ce005902130cc6a8542089400d10724854250d0486c50e0be48801f08600120f5490860119c0220f4490860ff2129402143f24880304840b749c86700f0f5fcf04c159800f0f1fa1498c043eb490968014228d0af4800883040ea4a11681060814200d9e430401ae84a116840181060e7490968401a11993b31884213d9e4490868401c0860e348026803200f9d290000f04ffc0d982080d848056013981299016010982080dc480078012801d0fff77cfbfff767fb08004830029005982230039085420492079516d14d68149aa8000818006ba04208d3681c032505404d60521ef4d1a8000818006b089a141a886a84420ad3040008e00c6c886a844201db040001e0e01784434d6877480893c0180190031d1c7007988003069a10406a020243a80008188168c902114321430a0a5a70090c149a11439970002c0bd0006b02990b180198c21d2000197811705b1c521c401ef9d1079a0398824208d1681c032101406248416008980019c31c18e00599203108980019c31c8a4211d126205b49086009200025052229001c0000f0d0fb2300a24805605648049a0260524c01e0514c049a002b00d078e12671130079e189231b029a4229d01121c9028a4250d19749096800294cd10121954a1160002191606288182a00d31822914b5a600a008f4b9b181974521c402af9d139e00e99f748017035e0c0463aa503021ca50302ffff00000ca3030220a503023140172927d8f24a1268012a23d10c224a43ef4b9818022415196d1e3f2d03d80519e64b1b5beb81a41c0e2cf4d101228a40e74b9968114399605a680323db43db1c93404b40591c05d10021e04a1160d168491cd160d74801684d1c0560079b032b25d1da4ad168002921d000201118097cd84b1954d749401c4028f7d1087d0a00497d09020818917d09044018d17d090642180a200025290000f043fb0423c5490b60c3480560c949c968ca4a1160c54a11780129be4c2ed10020107033204002bf490880c14800684880bde0c0461ca5030224a503020ca403028ca30302b4a203028ca003022ca5030208a30302e8a4030248a50302ff8700008ca5030290a503020600000490a00302b49f030290a20302412c0100aa48017801290bd1002101705f200002a54a1080ad4802680d2000f0f7fa88e0a84ad169002949d0516907290ad880209d4b98801120c0021880d0685880481c506176e0d168106988422dd201008920000208430b00944908800c200593584306ca07900818069000210d0007984319934202b410bc01d20698445d5b1c934202b408bc02d2069843195b78864840191b021b194380ad1c0c2de7d10598401c884908617c4c44e018009169491c91610829089b3ed30021d1610300581e0328089b37d801a1085c4000874419ba242e14a10302b89f03020000669928010004bc9f030274ad0302c49f0302c89f0302cc9f0302c09f030228a5030230a303020120694a908002205080654908607720000210800ee0f12d0cd367480068002808d101205e49086004e00b2d02d300f01fff089be318052098710021597146201871da1d181d07241955641c4d2cfbd1544d554c635c5354491c0e29fad1113000214d4c6a5c4254491c0e29fad10e9848490870089b4933980708b401bc06d01800211800220a71401c8107f9d1236080084f4900223e4b1a70401c086016a9099d280000f08afe002837d0012000f0d5fb002832d00d9c0b34314804800a981c901c98272805dc1c98401c1c901c982828f9db16a9129800f070fe00281dd01298401e169901403a4805408035a94214d1012000f0b2fb00280fd0204804800400002000f0aafb2048007800280dd1022000210c9a00f0f9f907e0169a1a4802600420002100f0f1f9154c0d98208014480a990160139812990160109820806be5002418494c80a1200002088000f055fe012007901121c8223c23194d1a4809c5089b2a606960ac60ec602c610798686103200a49086008480460064c59e728010004b89f0302f09f030290a203020ca303021ca503028ca3030228a503028ca003023aa503022ca5030294a503028ca5030290a5030230a303024ca5030280a3030210a3030295a5030288a3030204a303022f006619002a04d0030019705b1c521efbd17047d0b502af002a06d003000c781c705b1c491c521ef9d1d0bc02bc0847f0b503af81b00400806800f079f90025002800d10ce161688f4e300000f071f9337970790002c018b17909044018f17909064218f0782c2813d02b2822d18948007889490968090208188849096809040143a2692b2000f055f9854801250570e6e07e480168ff233e331133994204d28b007b4ef250491c0160a2692c2000f041f9d5e00020c143192b00d9cbe00090100a3f250540c0467b441b795b009f440c665e8d9060c2c2686c9a9428642e20737ea8a5143cb41c2a226a4800786a490968090208186949aae072480021016062480160a720c100a5e00007800e664960e07e4883e06548008865490968090462e06c487be00007800e5e4952e01f2d72d8a90803221040012800d19ce0002800d09ee0c800604955e0e2694f48006881004f4d280000f0edf80100ff223e322b881d00103d0120954201b404bc009e00d33200fb25ad005b19444d2d68ad00ab4200d9300010404e4a106063e0664845e06148006861495ae04d483fe063483de05b4800685b4952e00f2d34d8a8004d49095a4d4a2ce00021d30b4bd1520b1f2318401f2850d190004a4913e04007c00e4d490a5c0818417809028918827812048918c0780006091834e0a8004d4901e0a8004a4909582de03748006837490968090226e00f2d06d8a8003449095a344a105800040ee000211ce03648016819e036480088364909680906090a401834490968090641180de01e4800681e490968090101431d480068000208431c49096809040143a2692a2000f06bf80025280001b0f0bc02bc08470728f3d851011f4a51188000bee7c800134908184168e9e788001149b6e764ab03026cab03022ca60302b49f0302b89f030288a00302c09f03024ca50302e8a50302f8a4030220a503028ca0030288a30302e4a5030248a5030228a60302e0a1030220a20302ec9f0302e89f0302a89f03029ca103021ca103025ca10302a0a1030240a2030264a4030254a40302b4a203020ca40302e4a4030224a50302fca4030250a40302fc9f030280a2030204a00302c89f030274ac030274ab030270ab0302f09f0302004708471047c046f0b503af154b1c6801252d0409020143002c15d0601e3f260640b000104b1e58104b33408b420bd1104b9e4208d80e4b1b58934204d10a490818711901600ae0a000ff2303400848c25029430448c150601c02490860f0bc01bc004770ab030274ab0302ffff000074ac0302fffffefff0b503af83b002901b4a10881b4b002114884440240602d15b1ef9d126e01388ff2001900340db2b01d8083300e0dc3b00211024124d0091168801980640b34214d00298002809d02600288800040014002800db491c761ef7d103e02000401efdd1103107488142e6d30099080003b0f0bc02bc0847c04606000004410d03002801000401093d00f0b503af8fb0089001252c03200008307d4e3080280000f01ff9002827d00b9502947a4c216800200790c043002907d48a00774b9d586d1c9d50764a116020607549096800290b9b2ad074490a6831000839089b0b606d4908600992082a029a01d308200990002b2fd4803228e000200b9000f031f976480168491c01607548026803200b99fff73fff348062480b99016030000838e90701608834348010e0029a328069483188490704d5411e002802b401bcf7d130000838d9070160883232800fb0f0bc01bc004752480068002801d45349086053480088ff2101910840514c21682060814200d9e430401a4f4c21684018049020604d48184000908032059207980a90464801680598308007980d900c90002902d00a00521efdd13288120602d5029a8832328006918900414843585b1c0391435000253388590709d51f2129400b9c8c40690989000caa505820435050180603d56d1c282dedd1019d0998072819d80a98002801d0ff2d16d0099bd8000c99304a11502902009a8918069a11439a002d4ca1505b1c234909930b60294908180d994160ff2d2ad10a98401c0a90800707d11d4800680e2803d81b490868401c08601a480188ff200840194a116810608142089a00d9e430401a164b1968401818600499401a1949884291d319480168491c016006200021fff78bfe66e71248039a8158491c81500348069901605de7c04628010004ac9f03021ca10302b09f0302b89f03029ca10302a89f030206000004bc9f030274ad03020000ffffa0a10302e0a1030220a2030240a203021703000080a203023f0d0300c89f0302cc9f0302f0b503af82b01a490a88ff231a40194c216822601400914200d9e434611a164d2c680c192c6001210191002100912100124d2d886d07ed0f854213d00c4d2d881d400c4e35602e00aa4200d9e436b21a5118094a11600a1ba12a20b404bce7d3009800e0019802b0f0bc02bc0847c04606000004bc9f030274ad030228010004f0b503af12490b68002b0bd40024e24300280ad098000f4c25586d1c25500e4803600a60f0bc01bc004798000b4d2e58761c2e500848026009480568002df0d005689d42edd35b1eead22300e8e7c046ac9f03021ca10302b09f03025ca10302a89f0302b0b502af1348016803291fd8124a1278531e9a411204114b1c68114b1b681d02254315438a000f4ca550082b08b404bc00d30822002b07d04b010b4ce3180b4c20cc20c3521efbd1491c0160b0bc01bc0047c04650a4030290a20302cc9f0302f49f030254a4030264a4030208a00302f0b503af8fb0f5480168491c0160f4480068c004c10e4b424b4101200690c603022900d29ee108004042089148410090ec4800680990ec48006802900020059001000b96e6480d91451869798806000f041f621e94410a061216002a01da059b00e0012323402c790e0236190c961f2626400e962d1d0a9589071ed4002b1cd06106890f01280b9e00d1a9e00228db4c0e9d00d190e0032800d036e10b0030004230216801220092814204d10020e060300043302060190027e109990b9e31404d1ea9410b40012b4cd13300223361068a0f02280e9d02d1099999420dd0012804d131002131099c8c4206d003280fd130002330099981420ad10c98c005810f9000b84c201884688c4201d1012181619000b44909188869002800d1f6e00a00183200200c9010608a68521c032414408c60b148099c2218012a59d8a948856a026c521b0264100032002132944200d0ad000e6bad190d63002800dce0e00b9e0d98fae0002a0e9d00d5cfe004289f4c00d0cbe0102d00d9c8e0002d0bd00a98801c21002a0003780b70491c401c521ef9d10f2d05d8280000212154401c1028fad1069892490870924908700299491c8d4802910160a9e00791e169481e81413200413220689042069a00d10a00002a18d0330042339842069807994ad060e033004133854c2068984233d1002041e03000233084420d9800d088e033002033784da8e03c20205ca06220003c3041788278120251182162c178027912025118427912048918807900060818606220002c3000210a000300203b1960236a534308c0521c042af6d1606ae0613300423306980de00791002001006054491c4429fbd1012060600720e06133004133002007992360039032000c98c005860f880020180c90c068401c0325019528400496b04205d107910398002804d1082103e016000e9d31e0202116000e9d0028544a01d122003c32002d14d00a98831c2c000c98c56a2c308d4202d21e78565505686d1c0560641e5b1c002cf4d1474c23680b9e0e9d30004230834206d1206a019a42430c99c86a8018c8620c990c3104980860e069401be0610799a1600d984019811c7b2923d80898401a01281fd991e6304d0c996960099890420b9e0ed12800303000210200283a01241460aa6a4a4304c0491c0429f5d1e96a02e0a960330023330d98296409932b600e9dd7e7009b581e834131004331244802688a4200d10693069900292dd146210160002119480a008154521c502afbd11e4a1e4b012105248462c16207240464436482640300483300229a719a8083189960121d102afad12136066012480178427812025118427c12048918807c00060a1808200021fff7c4fb0fb0f0bc01bc0047e4a4030208a00302b4a20302f8a40302e8a403020ca3030208a30302df7fffff0ca40302fca4030200050001349f0302f0b503af81b00c00050000263000fff747fd00280bd008480560084901818031018101890906fcd4006820600126300001b0f0bc02bc08472001000403100000b0b502af002119480a008154521c3c2afbd1002216498b5c8354521c0e2afad100222c2384188d5ce554521c0e2af9d1c9218176cf214176be21017612214175342101758021c17440214174032101740021ff224318da76491c0529fad12a2101224254b0bc01bc0047c04695a50302989f0302f0b503af89b06a48006805a9019106a9009108a907aa04ab00f0e6f8654de969481e81410898821e534253410b4300d0bae00126002828d05f48c1685f4a1368994200d1b0e011605d4d2978002900d186e05e490b685b4a1268d41afc2c00d3fc24079d112d09d1002b07d10025584e4119097c71556d1c402df9d1e01853490860904200d38de08ee04d480078002824d04e4c21680598884200d882e0012300222a616a61aa61eb61049b5b182b60431afc2b00d3fc23ab6058182060069c601a984200d318008c4200d8100068600b3344485843000ce86065e00296069c3c483d4d002c10d0049e0821039123003178484003994208c00700d06a401000491ef8d1761c5b1ef2d1059eb4420bd208210b004208c00700d06a4010005b1ef8d1641cb442f4d12c490e7100224a70079b0b7006231f4c0ac42360029d6560a260e26022616561204b1a601c4a1570320a4a71224a42408a70100ac87023e0818a184c216001229302994200d80022008a079b984240b408bc00d11300012b0ad1114a13685b1c136000040a180c200021fff783fa21682e700c4800220260002901d100f094f809b0f0bc01bc004788a3030210a3030230a3030280a3030284a30302e0a50302e4a50302dca50302e8a503022111000008840000d1a50302ffff000056150000f0b503af81b0fd6800262e601e60bc682660052844d8009478440079400087440235202c0c390e6010201060202028602c4919600099086048e026480068444244416000086019201060234801887d24e400009a146018342c604018001d186034e00e60102010601a4909680029009a28d030202860194926e016480068444244410c6000280fd016200ee00120086011201ae00f48006800280fd001200ee002200860166011e01420009c10600a4801882960001d1860216007e002200860142002e0286006491960106001b0f0bc01bc004728a603022ca60302589f0302889f0302389f0302b0b502af88b01048002404700f4801684d1c056004a8019005a8009007a906aa03ab2800fff778ff0798022801d0069800e007482a0402430b202100fff7c6f908b0b0bc01bc004784a3030288a30302ffff0000becfc9ffffffffff4e494e54454e444f05000000080000000000000003000000000000000200000010000000040000000000000002000000160000000a0000000000000002000000190000001100000000000000140000000000000001000000020000001400000000000000010000000000000047616d65467265616b20696e632e00d401000000ffffffffffffffff0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
}


def run_program(duo, name):
    """Upload one of this repository's programs and start it. Afterwards the PC
    talks to it with program_poll() and must finish with program_stop()."""
    if not duo.can_run():
        raise DuoError("running programs needs the Reckless v4.2 firmware")
    image = PROGRAMS[name]
    Lab(duo)._upload_fast(LAB_STAGE, image)
    duo.lab_resident = False                       # the staging area now holds the program
    code, val = duo.ask(CMD_RUN)
    if (code, val) != (CMD_RUN, RUN_ACK):
        raise DuoError(f"the DUO refused the program ({code:#x} {val:#x})")


def program_poll(duo, arg=0):
    code, val = duo.ask(PROG_POLL, arg)
    if code != PROG_POLL:
        raise DuoError(f"the program didn't answer ({code:#x} {val:#x})")
    return val


def program_stop(duo):
    code, val = duo.ask(PROG_STOP)
    if code != PROG_STOP:
        raise DuoError(f"the program didn't stop cleanly ({code:#x} {val:#x})")
    return val


def describe_adapter_status(st):
    pairs, done, good = (st >> 16) & 0xF, st >> 20 & 1, st >> 21 & 1
    if good:
        return "the game accepted the adapter ID (0x8001) - it believes a Wireless Adapter is connected"
    if done:
        return "the exchange finished, but with an unexpected ID"
    if pairs:
        return f"exchanging the NINTENDO handshake ({pairs}/4 matched)"
    if st & 0xFFFF:
        return "the game is clocking the link, waiting for the handshake to line up"
    return "nothing from the game yet"


def adapter_test(duo, args, poll_s=0.5, after_s=5.0):
    """Detection test: the DUO's GBA answers the Wireless Adapter check of a Pokemon
    game on a second GBA. Only the detection exchange - nothing is written anywhere."""
    seconds = args.adapter_test
    print("Wireless Adapter detection test (reads and writes nothing on either cartridge)")
    print("  Connect the two GBAs with a link cable whose data lines are crossed (a GBC link")
    print("  cable, CGB-003). Then switch the second GBA on with your Pokemon game.")
    run_program(duo, "adapter_detect")
    print("  Program running on the DUO's GBA. Waiting for the game's check...")
    last_text, t0, done_at, st = None, time.time(), None, 0
    try:
        while time.time() - t0 < seconds:
            st = program_poll(duo)
            text = describe_adapter_status(st)
            if text != last_text:
                print(f"  [{time.time() - t0:5.1f} s] {text}")
                last_text = text
            if st >> 21 & 1 and done_at is None:
                done_at = time.time()
            if done_at and time.time() - done_at > after_s:
                break
            time.sleep(poll_s)
    finally:
        word = program_poll(duo, 1)
        st = program_stop(duo)
    print(f"  Transfers: {st & 0xFFFF}, last word from the game: {word:08X}")
    print("  Result: " + ("PASS - the game saw a Wireless Adapter." if st >> 21 & 1 else
                         "not detected - see the notes in docs for what to check."))
    return st


ADAPTER_COMMANDS = {
    0x10: "Hello (reset)", 0x11: "signal levels", 0x12: "version", 0x13: "status", 0x14: "slot status",
    0x15: "configuration", 0x16: "broadcast data", 0x17: "setup", 0x19: "start hosting",
    0x1A: "who has joined?", 0x1B: "stop hosting", 0x1C: "start searching", 0x1D: "search results?",
    0x1E: "stop searching", 0x1F: "connect", 0x20: "connected yet?", 0x21: "finish connecting",
    0x24: "send data", 0x25: "send data, then wait", 0x26: "receive data", 0x27: "wait",
    0x30: "disconnect", 0x32: "reconnect", 0x33: "reconnected yet?", 0x34: "finish reconnecting",
    0x35: "wait (0x35)", 0x37: "resend, then wait", 0x3D: "sleep",
}
ADAPTER_EVENTS = {
    0x01: "ID exchange completed (NINTENDO / 0x8001)",
    0x02: "the adapter's turn: told the game nothing happened",
    0x03: "the game stopped answering in the middle of a command",
    0x04: "handing the clock back to the game failed",
    0x05: "the game asked for more of an answer than the adapter sent",
    0x06: "the game never noticed the adapter was ready",
    0x07: "the game connected to the distributor",
    0x08: "the distributor received the game's name over the reliable channel",
    0x09: "the distributor told the game it may join (acknowledged)",
    0x0A: "link players exchanged (the game's trainer ID follows)",
    0x0B: "Mystery Gift server step",
    0x0C: "Mystery Gift: the game sent something unexpected",
    0x0D: "the game is ready to close the link; the distributor agreed",
}
ADAPTER_TRACE_N = 64
MG_STEPS = {0: "sent the game its first script", 1: "received the game's data", 2: "sent the next script",
            3: "sent the Wonder Card (or got 'ready to end')", 4: "sent the RAM script", 5: "got 'ready to end'"}
MG_IDENTS = {16: "client script", 17: "game data", 20: "ready to end", 22: "Wonder Card", 25: "RAM script",
             0xFFFF: "finished"}


def describe_trace_entry(head, data):
    cmd, length, times = head & 0xFF, (head >> 8) & 0xFF, head >> 16
    if cmd in ADAPTER_EVENTS:
        text = ADAPTER_EVENTS[cmd]
        if cmd == 0x02:
            text += f" ({data} frames)"
        elif cmd == 0x0B:
            text += f" {data >> 16}: next, {MG_IDENTS.get(data & 0xFFFF, hex(data & 0xFFFF))}"
        elif cmd in (0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0C, 0x0D):
            text += f" ({data:#x})"
    else:
        text = f"{ADAPTER_COMMANDS.get(cmd, 'unknown command')} (0x{cmd:02X})"
        if length:
            text += f" {data:08X}" + (f" +{length - 1} words" if length > 1 else "")
    return text, times


PROBE_ENCODINGS = ("one-bit lag (load W<<1, SO = top bit)", "first bit late (load W, SO = top bit)",
                   "as designed")


def probe_report(duo):
    rows = []
    for e, name in enumerate(PROBE_ENCODINGS):
        v = program_poll(duo, 6 | e << 8)
        rows.append((name, v & 0xFFFF, v >> 16, program_poll(duo, 7 | e << 8)))
    print("\n  2 MHz probe - how the game took each way of answering:")
    print(f"    {'Answer loaded as':40} {'tries':>5} {'accepted':>8} {'ack got through':>15}")
    for name, tries, acc, ack in rows:
        print(f"    {name:40} {tries:5} {acc:8} {ack:15}")
    lag, first, plain = rows
    if plain[2]:
        verdict = "the plain answer sometimes works - the link is marginal rather than consistently late"
    elif lag[2] and not first[2]:
        verdict = "consistent one-bit lag: the DUO's answers arrive exactly one clock late"
    elif first[2] and not lag[2]:
        verdict = "only the first bit is late"
    elif lag[2] and first[2]:
        verdict = "mixed - both late encodings were accepted at times"
    elif sum(r[1] for r in rows):
        verdict = "no encoding was accepted - the answers are garbled, or the game can't see the ready signal"
    else:
        verdict = "no commands arrived - try going into the game's wireless menu while this runs"
    if any(r[3] for r in rows):
        verdict += "; the SO flick got an acknowledgement through"
    print("  Verdict: " + verdict)
    return rows


LINE_US = 63.556                       # one GBA scanline


def adapter_tuning_report(duo, show=True):
    """The adapter's self-tuned 'ready' flick: per-width counts, the width it settled on, its own
    timing calibration, and the link scope (the game's SO line sampled after the first flicks)."""
    spin8, poll8 = program_poll(duo, 12), program_poll(duo, 13)
    us_spin = 8 * LINE_US / spin8 if spin8 else 0.0
    us_poll = 8 * LINE_US / poll8 if poll8 else 0.0
    rows = []
    for w in range(16):
        a, b = program_poll(duo, 9 | w << 8), program_poll(duo, 10 | w << 8)
        rows.append((w, a & 0xFFFF, a >> 16, b & 0xFFFF, b >> 16))
    state = program_poll(duo, 11)
    width, ncap = state & 0xFF, (state >> 8) & 0xFF
    scope = []
    for k in range(min(ncap, 8)):
        lo, hi, meta = (program_poll(duo, 14 | (4 * k + j) << 8) for j in range(3))
        scope.append((meta & 0xFF, (meta >> 8) & 0xFF, meta >> 16, lo | hi << 32))
    if show and any(r[1] for r in rows):
        print(f"\n  'Ready' flick tuning (one width step = about {us_spin:.2f} us; "
              f"one scope sample = about {us_poll:.2f} us):")
        print(f"    {'width':>5} {'flicks':>7} {'clocked':>8} {'accepted':>9} {'refused':>8}")
        for w, p_, c, a, r in rows:
            if p_ or c or a or r:
                print(f"    {w:5} {p_:7} {c:8} {a:9} {r:8}")
        print(f"  Settled on width {width}.")
        for fw, done, word, bits in scope:
            trace_line = "".join("-" if bits >> i & 1 else "_" for i in range(min(64, done + 1 if done != 0xFF else 40)))
            end = f"word clocked by sample {done}" if done != 0xFF else "not clocked"
            print(f"    scope, width {fw:2}, word {word:04X}....: {trace_line}  ({end})")
    return dict(rows=rows, width=width, scope=scope, us_spin=us_spin, us_poll=us_poll)


def adapter_capture_report(duo, show=True):
    """The first data commands the game sent the adapter (0x24/0x25), word for word: librfu's
    framing of what a connected game says first."""
    caps = []
    for k in range(min(program_poll(duo, 15), 4)):
        head = program_poll(duo, 16 | (32 * k + 31) << 8)
        n = min((head >> 8) & 0xFF, 8)
        words = [program_poll(duo, 16 | (32 * k + i) << 8) for i in range(n)]
        caps.append((head & 0xFF, (head >> 8) & 0xFF, head >> 16 & 1, words))
    if show and caps:
        print("\n  What the game sent (data commands), word by word:")
        for cmd, length, connected, words in caps:
            print(f"    0x{cmd:02X} ({length} words{', connected' if connected else ''}): "
                  + " ".join(f"{w:08X}" for w in words))
    return caps


def adapter_child_report(duo, show=True):
    """What the distributor's host side received from the connected game over librfu's NI
    channel: its serial number, game data and name (26 bytes)."""
    state = program_poll(duo, 18)
    raw = b"".join(program_poll(duo, 17 | k << 8).to_bytes(4, "little") for k in range(7))[:26]
    got = (state & 0xFFFF) == 0x0046                       # librfu's SLOT_STATE_RECV_SUCCESS
    info = dict(received=got, ni_state=state & 0xFFFF, child_frames=state >> 16 & 0xFF,
                host_frames=state >> 24, raw=raw, serial=raw[0] | raw[1] << 8)
    if show and (got or info["child_frames"]):
        print(f"\n  Host side: {info['child_frames']} frames from the game, {info['host_frames']} from the host; "
              + ("the game's name was received: serial %04X, data %s" % (info["serial"], raw.hex())
                 if got else f"name not complete yet (receiver state {state & 0xFFFF:#06x})"))
    return info


# A .wc3 file (as saved by suloku's Gen III Mystery Gift Tool and kept in Project Pokemon's
# EventsGallery): checksum (4), the Wonder Card, its metadata (0x28 + 0x28), then the RAM script
# with its checksum (4 + 1000). Japanese cards are 0xA4 bytes, the others 0x14C.
WC3_LAYOUTS = {1252: ("Japanese", 0xA4), 1420: ("English/European", 0x14C)}


def parse_wc3(data):
    """Returns (language, card, ram_script) from a .wc3 file's bytes, or raises ValueError."""
    if len(data) not in WC3_LAYOUTS:
        raise ValueError(f"a .wc3 file is {' or '.join(map(str, WC3_LAYOUTS))} bytes; this one is {len(data)}")
    language, size = WC3_LAYOUTS[len(data)]
    card = data[4:4 + size]
    script = data[4 + size + 0x50 + 4:4 + size + 0x50 + 4 + 1000]
    flag_id, card_type = card[0] | card[1] << 8, card[8] & 3
    if flag_id == 0 or card_type > 2:
        raise ValueError("this doesn't look like a Wonder Card (no event flag, or an unknown card type)")
    return language, card, script


def load_gift(duo, path):
    """Hands the distributor its Wonder Card and RAM script (0x2C PUT, then a CRC check)."""
    language, card, script = parse_wc3(open(path, "rb").read())
    blob = len(card).to_bytes(4, "little") + card + script
    blob += bytes(-len(blob) % 4)
    capacity = program_poll(duo, 20)
    if len(blob) > capacity:
        raise DuoError(f"the Wonder Card doesn't fit ({len(blob)} bytes, room for {capacity})")
    for i in range(0, len(blob), 4):
        code, n = duo.ask(PROG_PUT, int.from_bytes(blob[i:i + 4], "little"))
        if code != PROG_PUT or n != i // 4 + 1:
            raise DuoError(f"the program didn't take the Wonder Card ({code:#x} {n})")
    if program_poll(duo, 21) != zlib.crc32(blob):
        raise DuoError("the Wonder Card arrived damaged (CRC mismatch)")
    flag_id = card[0] | card[1] << 8
    print(f"  Wonder Card loaded: {os.path.basename(path)} ({language}, event flag {flag_id:#06x}, "
          f"{len(card)} + 1000 bytes)")
    return language, card, script


LINK_STATES = ("waiting for the game", "sending player IDs", "asking for link players", "exchanging link players",
               "Mystery Gift", "done")


def adapter_link_report(duo, show=True):
    """Stage D: link_rfu_2's link players and the Mystery Gift server's progress."""
    v = program_poll(duo, 22)
    info = dict(link_state=v & 0xF, mg_step=v >> 4 & 0xF, mg_errors=v >> 8 & 0xFF, child_cmds=v >> 16,
                player=b"".join(program_poll(duo, 24 | k << 8).to_bytes(4, "little") for k in range(16))[:60],
                game_data=b"".join(program_poll(duo, 23 | k << 8).to_bytes(4, "little") for k in range(16)))
    r = program_poll(duo, 25)
    info["uni_frames"], info["resent"] = r & 0xFFFF, r >> 16
    p = info["player"]
    info["player_ok"] = p[:14] == b"GameFreak inc." and p[44:58] == b"GameFreak inc."
    info["trainer_id"] = int.from_bytes(p[20:24], "little")
    if show and info["link_state"]:
        print(f"\n  Link: {LINK_STATES[min(info['link_state'], 5)]}; {info['child_cmds']} commands from the game; "
              f"server step {info['mg_step']}" + (f", {info['mg_errors']} unexpected" if info["mg_errors"] else "")
              + (f"; {info['resent']} packets sent again" if info["resent"] else ""))
        if info["player_ok"]:
            print(f"    the game's link player: trainer ID {info['trainer_id'] & 0xFFFF:05d}, "
                  f"name bytes {p[24:32].hex()}, language {p[42]}")
        if any(info["game_data"]):
            print(f"    the game's Mystery Gift data (first 64 bytes): {info['game_data'].hex()}")
    return info


def adapter_trace(duo, args, poll_s=0.3, stop_when=None, program="adapter"):
    """The DUO's GBA plays a Wireless Adapter with nobody else in range, and lists every
    command the game sends it. Nothing is written to either cartridge."""
    seconds = args.adapter_trace if program == "adapter" else args.adapter_probe
    if program == "adapter_probe":
        print("Wireless Adapter 2 MHz probe (reads and writes nothing on either cartridge)")
        print("  Each time the game sends a command, the DUO answers it a different way; the")
        print("  table at the end shows which ways the game accepted.")
    else:
        print("Wireless Adapter trace (reads and writes nothing on either cartridge)")
        print("  The DUO's GBA is now a Wireless Adapter, with one Wonder Card distributor in range.")
    print("  Use the game's wireless features (e.g. Mystery Gift > Wireless Communication);")
    print("  every command it sends the adapter is listed here.")
    run_program(duo, program)
    if program == "adapter" and getattr(args, "wonder_card", None):
        try:
            load_gift(duo, args.wonder_card)
        except (OSError, ValueError, DuoError) as e:
            print(f"  Wonder Card not loaded ({e}); the distributor will say it has nothing to send")
    probe_rows = None
    entries, seen, t0 = [], 0, time.time()

    def finish_last():
        if seen:
            i = seen - 1
            head = program_poll(duo, 3 | (i % ADAPTER_TRACE_N) << 8)
            text, times = describe_trace_entry(head, entries[-1][2])
            entries[-1] = (entries[-1][0], head, entries[-1][2])
            if times > 1:
                print(f"           ... repeated, {times} times in all")
    try:
      try:
        while time.time() - t0 < seconds:
            n = program_poll(duo, 2)
            if n > seen:
                finish_last()
                if n - seen > ADAPTER_TRACE_N:
                    print(f"  ({n - seen - ADAPTER_TRACE_N} entries came too fast to show)")
                    seen = n - ADAPTER_TRACE_N
                for i in range(seen, n):
                    head = program_poll(duo, 3 | (i % ADAPTER_TRACE_N) << 8)
                    data = program_poll(duo, 4 | (i % ADAPTER_TRACE_N) << 8)
                    entries.append((time.time() - t0, head, data))
                    text, times = describe_trace_entry(head, data)
                    more = f"  (x{times} so far)" if times > 1 else ""
                    print(f"  [{time.time() - t0:6.1f} s] {text}{more}")
                    seen = i + 1
            if stop_when and stop_when() and program_poll(duo, 2) == seen:
                break
            time.sleep(poll_s)
      except KeyboardInterrupt:
        print("  Stopping (Ctrl+C)...")
    finally:
        counters = program_poll(duo, 5)
        if seen:
            finish_last()
        if program == "adapter_probe":
            probe_rows = probe_report(duo)
        else:
            extra = program_poll(duo, 8)
            if extra & 0xFFFF or extra >> 16:
                print(f"  Over-long answers asked for: {extra & 0xFFFF}, ready signals missed: {extra >> 16}")
            adapter_trace.last_tuning = adapter_tuning_report(duo)
            adapter_trace.last_capture = adapter_capture_report(duo)
            adapter_trace.last_child = adapter_child_report(duo)
            adapter_trace.last_link = adapter_link_report(duo)
        st = program_stop(duo)
    print(f"  Commands answered: {st >> 16}, ID exchanges: {counters & 0xFFFF}, "
          f"interrupted commands: {counters >> 16}")
    if probe_rows is not None:
        return entries, st, counters, probe_rows
    return entries, st, counters


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
                    help="lab firmware: probe | peek ADDR LEN | scope ADDR SECONDS | script FILE.json | batch FILE.json")
    ap.add_argument("--width", type=int, default=1, choices=(1, 2, 4), help="access width for --lab peek/scope")
    ap.add_argument("--map-lab", type=lambda v: int(v, 0), default=0, help="mapping for --lab peek/scope")
    ap.add_argument("--save-type", choices=sorted(SAVE_KINDS),
                    help="override save-type detection for --gba-save (v3 firmware)")
    ap.add_argument("--slow", action="store_true",
                    help="use the slower Datel-style lab transfers even on v3.1")
    ap.add_argument("--restart", action="store_true",
                    help="restart the DUO program and bring it back into USB SERVER MODE (Reckless v4)")
    ap.add_argument("--cart-report", action="store_true",
                    help="read-only report on the GBA cartridge: header, size, whole-ROM checksum, health")
    ap.add_argument("--wait", type=int, default=120,
                    help="seconds to wait for the GBA to be switched on (default 120)")
    ap.add_argument("--update", metavar="FILE",
                    help="install a DUO firmware update file (asks for confirmation)")
    ap.add_argument("--adapter-test", type=int, nargs="?", const=180, metavar="SECONDS",
                    help="Reckless v4.2: answer a Pokemon game's Wireless Adapter check on a second GBA "
                         "(detection only; default 180 s)")
    ap.add_argument("--adapter-probe", type=int, nargs="?", const=600, metavar="SECONDS",
                    help="experimental (firmware v4.2): like --adapter-trace, but answers each command a "
                         "different way to find out how 2 MHz answers reach the game")
    ap.add_argument("--adapter-trace", type=int, nargs="?", const=600, metavar="SECONDS",
                    help="experimental (firmware v4.2): the DUO's GBA plays a Wireless Adapter and lists "
                         "every command a game on a second GBA sends it (default 600 s)")
    ap.add_argument("--wonder-card", metavar="FILE.wc3",
                    help="with --adapter-trace: the Wonder Card the distributor offers (your own .wc3 file); "
                         "without it, the distributor says it has nothing to send")
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
