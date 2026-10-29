"""Ruby/Sapphire's Mystery Event script engine, as far as the e-Reader route needs it - written for
this project from the behaviour of pokeruby's mystery_event_script.c, script.c (RunScriptCommand)
and mystery_event_menu.c (what happens to the result). It runs a received buffer the way the game
does and reports what the game would store and show:

    status, saved, store = run(buffer, language="English", version="Ruby")

`store` holds the save fields an event can change here: the RAM script block (checksum + 1000
bytes, as in a .me3) and the Record Mixing gift (checksum + 12 bytes). `saved` is whether the menu
then writes the save (it does for status 0 "loaded OK" and 2 "success").
"""
import struct

LOAD_OK, LOAD_ERROR, SUCCESS, FAILURE, STATUS_FF = 0, 1, 2, 3, 0xFF
LANGUAGE_MASK = {"English": 1 << 1, "German": 1 << 2}
VERSION_MASK = {"Ruby": 1 << 7, "Sapphire": 1 << 8}
N_COMMANDS = 17                            # nop .. crc
BUFFER = 0x7D4                             # gSharedMem, cleared to this size around each event


class Unsupported(Exception):
    pass


def crc16(data):
    c = 0x1121
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0x8408 if c & 1 else c >> 1
    return ~c & 0xFFFF


class Engine:
    def __init__(self, buffer, language, version):
        self.buf = bytes(buffer[:BUFFER]).ljust(BUFFER, b"\0")
        self.lang_mask, self.ver_mask = LANGUAGE_MASK[language], VERSION_MASK[version]
        self.ptr, self.offset, self.base = 0, 0, 0     # mScriptBase = the buffer's own start
        self.status, self.valid, self.stopped = LOAD_OK, False, False
        self.message = None
        self.ram_script = bytearray(4 + 1000)
        self.gift = bytearray(4 + 12)
        self.trace = []

    # ScriptReadByte/Halfword/Word: little-endian, byte by byte
    def byte(self):
        b = self.buf[self.ptr] if self.ptr < BUFFER else 0
        self.ptr += 1
        return b

    def half(self):
        return self.byte() | self.byte() << 8

    def word(self):
        return self.half() | self.half() << 16

    def addr(self):                       # ScriptReadWord(ctx) - mOffset + mScriptBase
        return (self.word() - self.offset + self.base) & 0xFFFFFFFF

    def span(self, start, end):
        if not (0 <= start <= end <= BUFFER):
            raise Unsupported(f"a pointer outside the received buffer ({start:#x}..{end:#x})")
        return self.buf[start:end]

    def run_command(self):
        """script.c RunScriptCommand: run commands until one returns TRUE (or the script stops)."""
        if self.stopped:
            return False
        while True:
            cmd = self.byte()
            if cmd >= N_COMMANDS:                     # func >= cmdTableEnd: stop, and say so
                self.trace.append(f"unknown command {cmd:#04x}: stopped")
                self.stopped = True
                return False
            self.trace.append(cmd)
            if getattr(self, f"cmd_{cmd}")():
                return True

    # ------------------------------------------------------------ the commands
    def cmd_0(self):                              # nop
        return False

    def cmd_1(self):                              # checkcompat
        self.offset = self.word()
        unk0, unk1, unk2, version = self.half(), self.word(), self.half(), self.word()
        if unk0 & self.lang_mask and unk1 & self.lang_mask and unk2 & 0x4 and version & self.ver_mask:
            self.valid = True
        else:
            self.message, self.status = "can't be used", FAILURE      # SetIncompatible
        return True

    def cmd_2(self):                              # end
        self.stopped = True
        return True

    def cmd_3(self):                              # setmsg
        status, at = self.byte(), self.addr()
        if status == STATUS_FF or status == self.status:
            text = self.span(at, min(at + 300, BUFFER))
            self.message = text.split(b"\xFF")[0]
        return False

    def cmd_4(self):                              # setstatus
        self.status = self.byte()
        return False

    def cmd_6(self):                              # initramscript
        group, num, person = self.byte(), self.byte(), self.byte()
        start, end = self.addr(), self.addr()
        script = self.span(start, end)
        self.ram_script[:] = bytes(len(self.ram_script))           # ClearRamScript
        if len(script) <= 995:                                     # InitRamScript
            data = bytes([0x33, group, num, person]) + script
            self.ram_script[4:4 + len(data)] = data
            self.ram_script[0:4] = sum(self.ram_script[4:]).to_bytes(4, "little")
        return False

    def cmd_11(self):                             # setrecordmixinggift
        unk, quantity, item = self.byte(), self.byte(), self.half()
        self.gift[:] = bytes(len(self.gift))
        if unk and quantity and item:
            self.gift[4:8] = bytes([unk, quantity]) + item.to_bytes(2, "little")
            self.gift[0:4] = sum(self.gift[4:]).to_bytes(4, "little")
        return False

    def cmd_15(self):                             # checksum
        expected = struct.unpack("<i", struct.pack("<I", self.word()))[0]
        start, end = self.addr(), self.addr()
        if expected != sum(self.span(start, end)):
            self.valid, self.status = False, LOAD_ERROR
        return True

    def cmd_16(self):                             # crc
        expected = self.word()
        start, end = self.addr(), self.addr()
        if expected != crc16(self.span(start, end)):
            self.valid, self.status = False, LOAD_ERROR
        return True

    def __getattr__(self, name):                  # commands this model doesn't carry out
        if name.startswith("cmd_"):
            raise Unsupported(f"command {int(name[4:])} isn't modelled")
        raise AttributeError(name)


def run(buffer, language="English", version="Ruby"):
    e = Engine(buffer, language, version)
    while e.run_command() and e.valid:            # RunMysteryEventScriptCommand
        pass
    saved = e.status in (LOAD_OK, SUCCESS)         # GetEventLoadMessage returns 0 -> Save_WriteData
    return e.status, saved, dict(ram_script=bytes(e.ram_script), gift=bytes(e.gift), message=e.message,
                                 trace=e.trace)
