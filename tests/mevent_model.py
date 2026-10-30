"""Ruby/Sapphire's Mystery Event script engine, as far as the e-Reader route needs it - written for
this project from the behaviour of pokeruby's mystery_event_script.c, script.c (RunScriptCommand)
and mystery_event_menu.c (what happens to the result). It runs a received buffer the way the game
does and reports what the game would store and show:

    status, saved, store = run(buffer, language="English", version="Ruby")

`store` holds the save fields an event can change here: the RAM script block (checksum + 1000
bytes, as in a .me3), the Record Mixing gift (checksum + 12 bytes), and the Enigma Berry (0x530
bytes, as in a .ecb, with `berry_valid` and `berry_available` for berry.c IsEnigmaBerryValid and
VAR_ENIGMA_BERRY_AVAILABLE). Pass `berry=` to start from a game that already holds one. `saved` is whether the menu
then writes the save (it does for status 0 "loaded OK" and 2 "success").
"""
import struct

LOAD_OK, LOAD_ERROR, SUCCESS, FAILURE, STATUS_FF = 0, 1, 2, 3, 0xFF
LANGUAGE_MASK = {"Japanese": 1 << 0, "English": 1 << 1, "German": 1 << 2}
VERSION_MASK = {"Ruby": 1 << 7, "Sapphire": 1 << 8}
N_COMMANDS = 17                            # nop .. crc
BUFFER = 0x7D4                             # gSharedMem, cleared to this size around each event
BERRY_SIZE, BERRY_CHECKSUM_AT = 0x530, 0x52C
TRAINER_SIZE = 0xBC                        # BattleTowerEReaderTrainer: 46 words, then their sum


def trainer_valid(rec):
    """battle_tower.c ValidateEReaderTrainer: not all zero, and the word sum matches."""
    words = struct.unpack("<47I", bytes(rec))
    return any(words[:46]) and sum(words[:46]) & 0xFFFFFFFF == words[46]
SAVE_BERRY_DESC1, SAVE_BERRY_DESC2 = 0x02026B44, 0x02026B71   # stand-ins for the save's description addresses (the checksum skips them)


def berry_checksum(rec):
    """berry.c GetEnigmaBerryChecksum: a byte sum up to the checksum, the two pointers taken as 0."""
    r = bytearray(rec[:BERRY_CHECKSUM_AT])
    r[0x0C:0x14] = bytes(8)
    return sum(r)


def berry_valid(rec):
    """berry.c IsEnigmaBerryValid."""
    return bool(rec[0x14] and rec[0x0A]) and berry_checksum(rec) == int.from_bytes(rec[BERRY_CHECKSUM_AT:BERRY_CHECKSUM_AT + 4], "little")


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
    def __init__(self, buffer, language, version, berry=None):
        self.buf = bytes(buffer[:BUFFER]).ljust(BUFFER, b"\0")
        self.lang_mask, self.ver_mask = LANGUAGE_MASK[language], VERSION_MASK[version]
        self.ptr, self.offset, self.base = 0, 0, 0     # mScriptBase = the buffer's own start
        self.status, self.valid, self.stopped = LOAD_OK, False, False
        self.message = None
        self.ram_script = bytearray(4 + 1000)
        self.gift = bytearray(4 + 12)
        self.berry = bytearray(berry or bytes(BERRY_SIZE))
        self.berry_available = False
        self.trainer = bytearray(TRAINER_SIZE)
        self.scripts_run = []
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

    def span(self, start, end, size=BERRY_SIZE):
        if end is None:                   # a fixed-size record: the berry, or a trainer
            end = start + size
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

    def cmd_7(self):                              # setenigmaberry
        had = berry_valid(self.berry)
        old_name = bytes(self.berry[:7])
        rec = self.span(self.addr(), None)
        self.berry[:] = rec                                          # SetEnigmaBerry: copy 0x530 bytes
        self.berry[0x0C:0x10] = SAVE_BERRY_DESC1.to_bytes(4, "little")   # ...then point at its descriptions
        self.berry[0x10:0x14] = SAVE_BERRY_DESC2.to_bytes(4, "little")
        if not had:
            self.message = "berry"
        elif old_name.split(b"\xFF")[0] != bytes(self.berry[:7]).split(b"\xFF")[0]:
            self.message = "berry transform"
        else:
            self.message = "berry obtained"
        self.status = SUCCESS
        if berry_valid(self.berry):
            self.berry_available = True                              # VarSet(VAR_ENIGMA_BERRY_AVAILABLE, 1)
        else:
            self.status = LOAD_ERROR
        return False

    def cmd_5(self):                              # runscript: an ordinary script, run at once
        self.scripts_run.append(self.addr())      # (not modelled further: recorded)
        return False

    def cmd_13(self):                             # addtrainer
        self.trainer[:] = self.span(self.addr(), None, TRAINER_SIZE)
        if not trainer_valid(self.trainer):       # ValidateEReaderTrainer clears a bad one
            self.trainer[:] = bytes(TRAINER_SIZE)
        self.message, self.status = "new trainer", SUCCESS
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


def run(buffer, language="English", version="Ruby", berry=None):
    e = Engine(buffer, language, version, berry)
    while e.run_command() and e.valid:            # RunMysteryEventScriptCommand
        pass
    saved = e.status in (LOAD_OK, SUCCESS)         # GetEventLoadMessage returns 0 -> Save_WriteData
    return e.status, saved, dict(ram_script=bytes(e.ram_script), gift=bytes(e.gift), message=e.message,
                                 trace=e.trace, berry=bytes(e.berry), berry_valid=berry_valid(e.berry),
                                 berry_available=e.berry_available, trainer=bytes(e.trainer),
                                 trainer_valid=trainer_valid(e.trainer), scripts_run=e.scripts_run)
