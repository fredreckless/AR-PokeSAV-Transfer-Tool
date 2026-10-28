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
PROG_POLL, PROG_STOP = 0x2A, 0x2B
SAFE_COMMANDS |= {CMD_RUN, PROG_POLL, PROG_STOP}
PROGRAMS = {
    "adapter_detect": bytes.fromhex(
        "524b52319c0100000bb29eac01000000f0b5071c4b4e00200021705004312029fbd34c4c4c4908804c4820810020206020898021084320812089000601d400f045f8f068002808d0b069002805d10120b0613f483b6900f074f8bb6800f071f80028e9d038487b6800f06bf83648c1782b290bd04268012a02d000f014f800e071692a20bb6900f05cf8d5e700f00bf80d1c2b20bb6900f054f800202081281cf0bc02bc084731690904090c326812041143f268002a08d001231b051943284b9a4202d101235b051943704720b520687061316901313161010c0204120cf368002b17d1b368994212d13568042d0dd27168c9430904090c8b420bd1d9430904090c8a4206d10135356003e0f26001e0002131603568042d03d20c496b00c95a00e00f497160d3431b041b0cb360090419432160208980210843208120bc02bc084718478c81030284810302648103026c810302200100043401000400100000018000004e494e54454e444f4164617074657220636865636b20616e737765726564000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
    "adapter_probe": bytes.fromhex(
        "524b5231441a000031dadfab01000000f0b503af99b0d84e0023b38101210a9109030e913180d549ff220a60d449042410940c60d349098811921140d24a1160190012aa5354491c1829fad10d9012a8152108910173cd498180cd4812900021cc4812aa5218435c1374491c0829f8d100210a00435c12ac6418a478a218d218491c0829f6d1d04312a9c8730020de4c12a90a5c09184b781b029a188b781b04d218c978090651182150001d1828efd1d6480068411e01b404bc8a41d1073200083a0f9211600228cc4c01d0882000e080200d9d0e9908183080cd48007800280ed000203080c4480078216809020918c84800680004084319b0f0bc02bc08470a98c1070c9100020b903088000601d500f06dfc0f980068bf490860b8490988119a1140b74b1a6819608a4200d9e431891aba4b1a6889181960b94a116021688a0001a39a5897465d810302a18103028b8103028b8103025d8103020200b14b1a40b14b9a4233d1042931d100202060a648006800280c9900d101000f9a1160022800d1bce08820a3e1881e42424241a648026002293fd10020a54908603de0a44909681f2902d88a00a34b9850481ca0490860a1490968884200d2b9e0954a1068401c1060002900d0b4e00a00b4e09a4200d096e08d4909688900984b5a58521c5a500021974a1160904a1160914a116001222260ff22030a1040924d28608e4d13402b6000d19ae00c9800f0d6fd784e0d9d00f0ebfb854800688b490968884209d203212160411c814a116080008749085800f0d9fb86480078012876d10e9908000830308035000e000120099000f084fe002800d114e12e800024200000f07cfe002800d11fe107947a4800787a490978624a1288119b1a40614c23682260934200d9e432d21a644e3368d41834600e9ad21c2a80079e1296002900d109900d9800f0c6fc00280a9801d158480078c00700d0d7e2e425099845430998002812d04e480088119908404d4a11681060814200d9e430401a504a116840181060001ba84200d37be10d9800f0a2fc00280a9801d146480078c007dfd0b3e28020e6e00c9a904202d1002900d12fe7042900d0bbe00100514a1140514a1268002a00d103e15048036800201ce1002020600c9859e340480268434e306806e0344b1a68521c1a600a003f4e0d9d00f0fdfc00213d4801603e48099101703268100010382d28414b224e00d95ee10aa14000085a4000874428010004cc930302d093030206000004d4930302341200007d7fa10fb8930302b801c001cd019601d50178016e01a0015901e2012d03ec01dd017b007b008901b000b000590159010602a601f101610159015901590159015901590159015901170259012d0366012d03d6005901d60059015901590159015901e70128010004cc930302d093030206000004d4930302e893030200940302d8930302ac94030204940302509a0302dc9303020000ffff000066991894030230950302209403022c9403021c9403020894030214940302e493030234950302b0940302389503027c95030260950302ffff0000249403025c950302649503021e2a00d1bde0fe480068032800d1b8e01d22a7e22b002e800020206012e022000024f8490c60f8490c60f8490c60042111600100f64a114043e02b002e80f54804600400f448006800280c9900d101000f9a116002280d9d01d0882000e080201e000e990818308089e2f84800680028079200d1d2e1212af54900d00ee20a98f44a1070f44808600520e949086000200100f24a5054491c4429fbd100200100ef4a5054491c5029fbd107200021ea4a00f014fc50e20a98ea49087052e2ea4a1468000c84421dd1dc480368032b24d8080060400022da4eb04200d034e1d54800886040b04200d02ee15b1cd348036000220a980d9dd34c032b0ad90a9bdb035b1c13e00022cd4802600a9813000d9dcc4c5b00d54ef35a08e0c74801600120c003431c0a000a980d9dc64cc14e3360c34e7140cc4e3160002812d0002a10d0cb490868401c086001200026310000f0c5fbbd48066026600f9806600e98883005e0180440180f9908600e988030c14e79e7b8480078002800d0e7e0be482730e6e007923500b24ebc4830600020bc4b31181a5c0a71401c1828f9d10720b949086000020990079a1e2a20b440bc03d100209f4908601e220d9dd3e10120b14908600220a24908606e228ee10a98a54908702722c6e10120aa49086000069c490860332281e100200990a74a115c1954401c1828fad11622b4e100200100944a5054491c1c29fbd107209e4908600b99491c916100020990089aa3e1042084490860994800889549401a41424141874801601f2296e10120924908607c480068ff2800d146e1000645e18f4800688f490860172286e18e480068072802d8401c8b4908607b480078012801d100f07efc0a987c490870252274e10021754801706b480991016010226ce101207d490860704800780028119900d101006c480160112222e101207749086069487949016012221ae1012073490860119864490860142212e103205a4908601c224ae1022057490860192245e15548119901603d2240e10120524908601b223be1594800782622012800d035e1079267490868401c08600f208303554a10680104069300d5f3e000230ce15e480068072802d8401c5b4908604b4800782422012800d019e1079200f01bfc14e100213d48016045480991017030220de10a98e0e64a482830fa4d12a9069000f0d7fd002832d0012000f0c4fb00282dd00e9c0b342c8018961898272805dc1898401c189018982828f9db12a90c9800f0bffd00281ad00c98401e12990140f648069a02408032914210d1012000f0a2fb00280bd02c80002000f09cfbed48007800280ad102200021099a04e0129ae54802600420002100f085fa0e9a2a80db480660dd48006800280c9900d101000f9b1960022801d0882000e0802010182880d04e0d9dd14cbee0cc93030258950302249403025c950302ffff0000d093030200940302ca48801fd4490860212a00d096e00020c44992e0cc93030258950302249403025c950302ffff0000d09303020094030280950302b094030260950302412c0000d4950302849503023895030254950302c0930302289403022801000400006699a57d0000e8930302349503022c9403027c950302189603021701830040960302401cba4a1060022824d90a98b04a1070b74808600520a349086000200100b04a5054491c4429fbd100200100ad4a5054491c5029fbd141e00020a54909780029ac4a00d10a008018a349086013220b98099039e000282ed0012000062de000211a009d4b1971800310409d4949689c4a926849030143500208439100984a5118c968c9020143080a5871080c109908439871032301200590c20392480168114200d1dde00993049213002133c26a994260d014005fe07d48801f08600120834908600b980990079a02202060ff211140099801437948803048407f49c86700f0e7f9280000f020f91098c0432168014238d06e480088119908406d4a11681060814200d9e430401a6e4a1168401810606d490968401a0b993b31884222d976490868401c08606d480268032035000026310000f085f92b000e9828805a4c26605c48006800280c9900d101000f9a1160022801d0882000e080200e991e000d9d0818188054480078012801d0fff747fbfff731fb0724994201d0406c00e048300290049d2235a9420395564816d14668109db300c3181b6ba34208d3731c03261e4046606d1ef4d1b300c3181b6bd21a836a9a4200d31a004a4c09e0026c836a9a4201db1a0001e0d3179a43454c6668099b4148c0180190051d2a708b030698034070021843b300e3189c68e40204431443200a6870200c109c2043a870002a0bd0186b029b1d180198c41d1300287820706d1c641c5b1ef9d1039881420bd1701c032101402f48416009988018c31c1b4e0d9d1c4c13e0049c203409988018c31ca142164e0d9d174c09d1262025490860092000210522099300f0f1f8099b1d49079a002b04d0980708b401bc03d10ce011980871059b18001749091800220a71401c81071449f7d1079a0b608008401c0e490860010213480140099113e728010004cc930302d093030206000004d493030200940302d89303020000669904940302509a0302dc93030234950302609503022f006619b0940302e4930302d49503028495030200ff996680950302412c0000412c0100e0930302002a04d0030019705b1c521efbd17047d0b502af002a06d003000c781c705b1c491c521ef9d1d0bc02bc0847f0b503af81b00400806800f081f8002819d06168304e300000f07bf8f0782b2813d13748007837490968090208183649096809040143a2692b2000f06bf833480125057043e0002541e000253179072938d872797f2010407944097949008f44031b0e100b1e2513254800782549096809020818244915e080001d490ae01e480be080001b4905e0022820b402bc1ad880001249095816e01848016813e01248006812490968090401430ce0022820b402bc08d880000a490a580a4908580104114300e0e943a2692a2000f023f8280001b0f0bc02bc08474496030248950302089403023c95030228940302e093030250980302509603024c96030204940302cc930302d0930302ac940302d8930302004708471047c046f0b503af154b1c6801252d0409020143002c15d0601e7f260640b000104b1e58104b33408b420bd1104b9e4208d80e4b1b58934204d10a4908187119016009e06006c00d084b1a502943054a1150601c02490860f0bc01bc0047c0464c96030250960302ffff000050980302fffffefff0b503af85b0029001252c0320000830574e3080280000f0c3f800286dd00194544c2268002a07d1534800688000534b1e58761c1e504e4e514b1868002807d04d48006880004f4e3158491c3150484e002003901860501c20604b48006800286cd04548026850425041029b1900814030000838016001998031002b73d5022a71d042480588ff20029005404048016805602b00a94200d9e433581a3d4a116843181360019a1600803688321900304c2088000658d526802280039804900498012805dc0498401c049004980228f9db2e480488029804402d4804602000a54200d9e430401b41182a480160c81aa02810b420bc1e4cdbd936e027480168491c016026480268032000252900fff740ff1d480560184908683480401c032800d328000860411e01b404bc8a41d1073200083a1160022814d0882013e030000838e90701600198803030800b4d286802280ad0002000f024f8002805d02868019ce0e780202118318005b0f0bc01bc00472801000414940302009403023c9503021894030248950302d093030206000004d4930302509a0302e0930302e4930302f0b503af82b01a490a88ff231a40194c216822601400914200d9e434611a164d2c680c192c6001210191002100912100124d2d886d07ed0f854213d00c4d2d881d400c4e35602e00aa4200d9e436b21a5118094a11600a1ba12a20b404bce7d3009800e0019802b0f0bc02bc0847c04606000004d4930302509a030228010004f0b503af8db0dd480168491c0160dc480068c004c10e4b424b4101200590c603022900d26de1484248410290d54800680890002005220692049002000a960791cf480b928418069821568806000f021f53425341002901d4049a00e001221a432379ff250d402d02ed1809951f251d400c95890719d4002a17d159068d0f01287ed00228c04b66d0032800d00be130004230196801220292814200d003e10020d860300043301860fde0089c344061426141002a059a00d10a00002a00d0f3e03200223259068b0f022802d1089991420dd0012804d131002131089c8c4206d003280fd130002330089981420ad10998c005810f9800a34c201884688c4201d10121816198009f490b189869002800d1cae0190018310020099008609968491c03240c409c609a48089c211801292fd894498d6a096c481b92490864310021318c4200d0ad001e6bad191d63002800dcb5e00a9ea8e0d969481e81413200413218689042059a00d10a00002a1bd032004232904208b440bc059b4bd033005de032004132804e3068904235d1002341e030002330844207990b9800d086e032002032784da7e03c20185c986218003c3041788278120251181962c178027912025118427912048918807900060818586218002c3000210a001e000300203b1960336a534308c0521c042af6d1706af0610a9a4232059b0ce0002001007054491c4429fbd1012070600720f0610a9a4132002332600998c005810fa80030180990c068401c03263040884206d101960395002b0a9e04d1082503e0534b0a9e32e02025002b514b01d14f4b3c330c98002815d00091201d821c099ce06a2c340c99a84203d216781e540a9e2068401c2060491e521c0029f3d1434802680099300042308242404b06d1186a019c4443099ad06a0019d06209980c300160d8690c99401ad861039d9d6007990b980c9a8018821c7b2a22d8881a01281fd9c0e6314d099b6b60089888420a9e0ed12800303000210300283b01241c60ab6a4b4308c0491c0429f5d1eb6a02e0ab60320023320b982b64079908922a60d7e7029b581e834131004331204802688a4200d10593059900292dd146210160002119480a008154521c502afbd11a4a1b4b012105248462c16207240464436482640300483300229a719a8083189960121d102afad1213606600d480178427812025118427c12048918807c00060a1808200021fff7fffc0db0f0bc01bc0047c0461c9603022c94030284950302d495030220960302df7fffff00050001c8930302f0b503af81b00c00050000263000fff7e9fd00280bd008480560084901818031018101890906fcd4006820600126300001b0f0bc02bc08472001000403100000becfc9ffffffffff4e494e54454e444f05d4d4d400000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
    "adapter": bytes.fromhex(
        "524b5231cc200000f7a677a101000000f0b503af9bb0d34e0023b38101210b9109030c913180e749ff220a60e649042411940c60e549098813921140e44a1160190014aa5354491c1829fad1129014a8152108910173df498180df4814900021de4814aa5218435c1374491c0829f8d100210a00435c14ac6418a478a218d218491c0829f6d1d04314a9c8730020d44c14a90a5c09184b781b029a188b781b04d218c978090651182150001d1828efd100200d9000f088feca4908600b9c200000f082fec8490860c048008813990840bf49086030000838e1070400109101600c9888300e903080c048007800280fd000203080b3480078b349096809020918bb480068000408431bb0f0bc02bc08470b9800020f90b74d0a943088000601d500f0fafb2068b4490860a8490988139a1140a74b1a6819608a4200d9e431891aae4b1a6889181960ad4a11609e4909688a0001a39a5897466d81030297810302c7810302918103026d8103020200a54b1a40a54b9a4200d098e0042900d095e0002091490860109820600e983080c3e39e4800681ae09e4909681f2902d88a009c4b9850481c9a4908609b490968884200d293e08e4a1068401c1060002900d09ee00a009ee00d989049086093490968884208d203217c4a1160411c8b4a11608000285896e38e480078012873d10c9d2800083030800120099000f051ff002800d191e035800024200000f049ff002800d12de1079483480078834909786b4a1288139b1a406a4c23682260934200d9e432d21a714d2b68d4182c600c9ad21c3280079a1492002900d10990129800f077fc00280b98674d01d164480078c00700d0b0e2e426099846430998002812d05648008813990840554a11681060814200d9e430401a5d4a116840181060001bb04200d371e1129800f052fc00280b9801d152480078c007dfd08ce29a4217d10021574a11600122434b1a60ff22030a10405a4d28601340534a136031d0109800f0befd4fe13c480d990160109819e3109a904211d100290fd151490868401c08604e4802680520002100f02dfd42e744480268494d286818e0042900d0a1e00100484a1140484a1268002a00d1d7e0464803680020efe0358027480d99016017e12f4b1a68521c1a600a0000f00cfd002236480260364802702b68180010382d28284d00d930e103a14000085a40008744c0462801000483018a01960165019d0149013e016f012b01a901ce02b301a4016e006e0058018c008c002b012b01cc017501b80132012b012b012b012b012b012b012b012b01da012b01ce023701ce02b2002b01b2002b012b012b012b012b01ae01f0950302f495030206000004f8950302341200007d7fa10fd09503020c9603022496030228960302fc950302c4960302c89603022c960302d8a00302009603020000ffff00006699489703023496030244960302309603024c97030250970302e4980302c89803020896030238960302ffff00003c960302c49803021e2b00d1a1e0fc480068032800d19ce01d2355e20024f9490c60f9490c60f9490c600421f84a11600100f84a114031e03580f548046010980a9c73e0f4480068002800d1a2e1212b00d0a9e10b98f1490870f14828600520e749086000200100ee4a5054491c4429fbd1079300200100eb4a5054491c5029fbd107200021e64a00f04afc079b17e20b98e649087017e2e54a1468000c84421cd1da480368032b22d8080060400022d84da84200d01ae1d24800886040a84200d014e15b1cd148036000220b980a9c032b09d90b9bdb035b1c11e00022cb4802600b9813000a9c5b00d24deb5a07e0c54801600120c003431c0a000b980a9cc04d2b60c34d6940c94d2960002810d0002a0ed0c8490868401c086001200025290000f0fdfbba480560109820600e9804e01804401820600c9880303080bf4dcee1b6480078002800d0d2e0bc482730d1e00793bb4828600020bb4b29181a5c0a71401c1828f9d10720b84908600202079b1e2b00d0a7e10020a14908601e23a2e10120b1490860022028606e239ae10b98a6490870272396e10120ab4908600006286033238ee100200990a84aa94b115c1954401c1828fad116238ee0002001006854491c1c29fbd107209f4908600f99491ca9610202089b75e10420884908609b4800889749401a414241418a4801601f2368e101209449086080480068ff2800d1ece00006ebe09148006892490860172358e1099200f098fd7f480078012801d100f0cafd0b9880490870252354e000227948027071480260102343e1012082490860754800780028139900d101002960112336e101207c4908607f48286012232fe101207849086013982860142328e10320624908601c2324e102205f49086019231fe15d48139901603d231ae10120fc4908601b2315e15f4800782623012800d00fe1f5490868401c08600f20800306905b4a1068010400d5cfe00020e5e0099200f041fdf94800782423012803d1079300f071fd079b099af3e00022e9480260f24802703023ece00b98f9e6ea482830e34e14a9069000f02cff002833d0012000f0affc00282ed00c9c0b34348007981a901a98272805dc1a98401c1a901a982828f9db14a9109800f013ff00281ad01098401e14990140dd48069a02408032914210d1012000f08cfc00280bd03480002000f086fcd448007800280ad102200021099a04e0149acc4802600420002100f0d3fa0c983080c348079901600a98109901600e983080bd4e0a9ca5e0be48801f2860212b00d08fe00020b94908608be0401cc84908600228c34a20d90b98be490870c54828600520b1490860002001005054491c4429fbd100200100bb4a5054491c5029fbd16fe00020b34909780029ba4a00d10a0080182860132367e000285fd0012000065ee0f0950302c09803023c960302c4980302f4950302ffff0000e8980302c8980302412c00003c990302ec98030250970302bc980302d895030240960302c896030200006699a57d00000c9603024c97030244960302cc980302e498030217018300002129718003069908405168926849030143500208439100914a5118c968c9020143080a6871080c11990843a8710320099001200490c2038a480168114201d1099ad1e0100007931600030021338548c26a99424fd014004ee07548801f286001207a4908600f9a022070490860ff2119401143714880304840e86700f09cfa129800f011f91198c04368490968014229d06748008813990840664a11681060814200d9e430401a664a11684018106065490968401a0f993b31884213d96e490868401c08606548026803200d9c210000f0f6f90c983080544804600a9c109820600e98308054480078012801d0fff7c8fbfff7b1fb0724994201d0406c00e048300290300022300390814205910196544816d14568119eab00c3181b6ba34208d36b1c03251d404560761ef4d1ab00c3181b6bd21a836a9a4200d31a00494c09e0026c836a9a4201db1a0001e0d3179a43444c65684048099bc0180100061d3270059883030698034068021843ab00e3189c68e40204431443200a7070200c119c2043b070002a0ad0186b029b1e18cc1d130030782070761c641c5b1ef9d1059b039883420ad1681c032101402d48416009988018c21c1a4e0a9c12e00199203109988018c21c8b42164e0a9c09d1262024490860092000211500052200f06ff92a001c4d079b002a04d0900704b401bc03d10be013982871049a09921000291800220a71401c8107f9d1099a2a608008401c0d49086002021348024023e7c89c030228010004f0950302f495030206000004f8950302fc950302000066992c960302d8a00302009603024c970302c89803022f006619c8960302089603023c990302ec98030200ff9966e8980302412c0000412c010004960302002a04d0030019705b1c521efbd17047d0b502af002a06d003000c781c705b1c491c521ef9d1d0bc02bc0847f0b503af81b00400806800f001f9002819d061685f4e300000f0fbf8f0782b2813d17748007877490968090208187649096809040143a2692b2000f0ebf8734801250570a0e000259ee00025e9433279132a00d994e073797f2018407a441279520097440926312e15288d8d33396059132c1811424a6e6b614800786149096809020818604924e0544858e0454856e08000574973e01f2820b402bc70d8810803220240012a63d0002a66d1c8003a4965e0524843e04c4800684c4909e038483de080004b495ae04b4838e04448006844490968090424e00f2820b402bc4fd880003549095a354a25e01f22024040091f2a32d18000334941e04007c00e36490a5c0818417809028918827812048918c0780006091833e02348006823490968090201432ce00f2820b402bc28d880001f49095a1f4a105800040ce0224801681ee022480088224909680906090a4018204909680906411812e0172a20b402bc0ed860214143164840189100415807e0c80009490818416802e0880007490958a2692a2000f046f8280001b0f0bc02bc0847c046cc9c030218980302589803022896030224960302e4950302d49703025497030294970302d897030278980302a499030284990302ec9803023c990302a49c0302c89c0302a89c03028099030238960302b89803024096030204960302d89e0302d89c0302d49c03022c960302f0950302f4950302c4960302fc950302004708471047c046f0b503af154b1c6801252d0409020143002c15d0601e7f260640b000104b1e58104b33408b420bd1104b9e4208d80e4b1b58934204d10a4908187119016009e06006c00d084b1a502943054a1150601c02490860f0bc01bc0047c046d49c0302d89c0302ffff0000d89e0302fffffefff0b503af83b002901b4a10881b4b002114884440240602d15b1ef9d126e01388ff2001900340db2b01d8083300e0dc3b00211024124d0091168801980640b34214d00298002809d02600288800040014002800db491c761ef7d103e02000401efdd1103107488142e6d30099080003b0f0bc02bc0847c04606000004410d03002801000401093d00f0b503af8fb0099001252c03200008307c4e3080280000f01bf900282ad00394794c216800200890c043002920b440bc08d48a00754b9d586d1c9d503500744a116020607349096800296e4c29d072490a6821000839099b0b606b4908600a92082a039a01d308200a90002b29d48032228022e00b950025280000f029f972480168491c01607148026803202900fff73bff34805f480560300008380b99c90708e02600039c3480002000f0d1f830000838e9070160883434800fb0f0bc01bc004753480068002801d45449086054480088ff2102910840524d29682860814200d9e430401a504d29684018059028604e48184000908032069208980b90474801680698208008980d900c90002902d00a00521efdd12288120602d5039a8832228007918900424843585b1c049143500025238859070ad51f21294034008c40690989000caa505820432e4c5050180603d56d1c282decd1029d0a9807281bd80b98002801d0ff2d18d00a9bd80001900c99304a11502902009a8918079801439a002d4881505b1c23490a930b602948019940180d994160ff2d2ad10b98401c0b90800707d11d4800680e2803d81b490868401c08601a480188ff200840194a116810608142099a00d9e430401a164b1968401818600599401a194988428ed318480168491c016006200021fff78cfe63e71248049a8158491c81500248079901605ae728010004e895030254970302ec950302f4950302d4970302e495030206000004f8950302d8a003020000ffffd897030218980302589803027898030217030000b89803020496030208960302f0b503af82b01a490a88ff231a40194c216822601400914200d9e434611a164d2c680c192c6001210191002100912100124d2d886d07ed0f854213d00c4d2d881d400c4e35602e00aa4200d9e436b21a5118094a11600a1ba12a20b404bce7d3009800e0019802b0f0bc02bc0847c04606000004f8950302d8a0030228010004f0b503af12490b68002b0bd40024e24300280ad098000f4c25586d1c25500e4803600a60f0bc01bc004798000b4d2e58761c2e500848026009480568002df0d005689d42edd35b1eead22300e8e7c046e895030254970302ec95030294970302e4950302b0b502af13480168072920d8124a1278531e9a411204114b1c68114b1b681d02254315438a000f4ca550182b08b404bc00d31822002b08d060234b430a4ce3180a4c20cc20c3521efbd1491c0160b0bc01bc004780990302c8980302089603023096030284990302a499030244960302f0b503af8db0dd480168491c0160dc480068c004c10e4b424b4101200590c603022900d26de1484248410290d54800680890002005220692049002000a960791cf480b928418069821568806000f021f53425341002901d4049a00e001221a432379ff250d402d02ed1809951f251d400c95890719d4002a17d159068d0f01287ed00228c04b66d0032800d00be130004230196801220292814200d003e10020d860300043301860fde0089c344061426141002a059a00d10a00002a00d0f3e03200223259068b0f022802d1089991420dd0012804d131002131089c8c4206d003280fd130002330089981420ad10998c005810f9800a34c201884688c4201d10121816198009f490b189869002800d1cae0190018310020099008609968491c03240c409c609a48089c211801292fd894498d6a096c481b92490864310021318c4200d0ad001e6bad191d63002800dcb5e00a9ea8e0d969481e81413200413218689042059a00d10a00002a1bd032004232904208b440bc059b4bd033005de032004132804e3068904235d1002341e030002330844207990b9800d086e032002032784da7e03c20185c986218003c3041788278120251181962c178027912025118427912048918807900060818586218002c3000210a001e000300203b1960336a534308c0521c042af6d1706af0610a9a4232059b0ce0002001007054491c4429fbd1012070600720f0610a9a4132002332600998c005810fa80030180990c068401c03263040884206d101960395002b0a9e04d1082503e0534b0a9e32e02025002b514b01d14f4b3c330c98002815d00091201d821c099ce06a2c340c99a84203d216781e540a9e2068401c2060491e521c0029f3d1434802680099300042308242404b06d1186a019c4443099ad06a0019d06209980c300160d8690c99401ad861039d9d6007990b980c9a8018821c7b2a22d8881a01281fd9c0e6314d099b6b60089888420a9e0ed12800303000210300283b01241c60ab6a4b4308c0491c0429f5d1eb6a02e0ab60320023320b982b64079908922a60d7e7029b581e834131004331204802688a4200d10593059900292dd146210160002119480a008154521c502afbd11a4a1b4b012105248462c16207240464436482640300483300229a719a8083189960121d102afad1213606600d480178427812025118427c12048918807c00060a1808200021fff7f9fb0db0f0bc01bc0047c046a49c030244960302ec9803023c990302a89c0302df7fffff00050001e0950302f0b503af81b00c00050000263000fff77ffd00280bd008480560084901818031018101890906fcd4006820600126300001b0f0bc02bc08472001000403100000becfc9ffffffffff4e494e54454e444f05d4d4d401000000ffffffffffffffff000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
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
}
ADAPTER_TRACE_N = 128


def describe_trace_entry(head, data):
    cmd, length, times = head & 0xFF, (head >> 8) & 0xFF, head >> 16
    if cmd in ADAPTER_EVENTS:
        text = ADAPTER_EVENTS[cmd]
        if cmd == 0x02:
            text += f" ({data} frames)"
        elif cmd in (0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09):
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
    for k in range(min(program_poll(duo, 15), 8)):
        head = program_poll(duo, 16 | (32 * k + 31) << 8)
        n = min((head >> 8) & 0xFF, 24)
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
