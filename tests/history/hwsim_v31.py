"""
hwsim.py - a faithful-as-we-know-it simulation of the Action Replay MAX DUO in
USB SERVER MODE, for testing firmware v3 and the PC tool together.

Models what we learned on real hardware:
  * mapping register 0x0800009C: 0x0120 = DUO program, 0x0101/0x0007/0x0001 =
    game cartridge (the DUO's program vanishes; executing it is a test failure)
  * the save chip SLEEPS after every swap-in and only answers once the game's
    ROM has been read (reads return 0xFF, writes are ignored, until then)
  * save chips: Sanyo / Macronix 128 KB Flash (ID mode, F0 reset, AA55F0 exit,
    bank switch), 64 KB Flash, 32 KB SRAM, EEPROM (nothing at 0x0E000000), none
  * ANY write that could change save data is recorded as a violation
    (Flash erase/program prefixes, any SRAM write, writes to EEPROM carts)
  * USB: Datel's 8-byte send/receive routines are replaced by queues. The link
    is STRICTLY ALTERNATING, as every Datel transfer is and as v3's failure on
    real hardware showed: a PC packet, then a GBA packet, then a PC packet...
    A second PC packet in a row is never accepted (the PC's write times out,
    the GBA never sees it); a second GBA packet in a row hangs the GBA inside
    Datel's send routine. Both are recorded in hw.link_errors and fail tests.
    Optional fault injection: corrupted packets during streams.
  * the on-screen USB log: every line Datel's logger receives is captured
"""
import queue, random, struct, sys, threading, time
from unicorn import *
from unicorn.arm_const import *

ROM, ROMSZ = 0x08000000, 0x02000000
SAVE = 0x0E000000
STOP = 0x02000F00
VIEW = 0x100000                      # bytes of ROM view swapped on mapping changes

F_CARD_DETECT = 0x0800BF2C
F_USB_AVAIL = 0x0800C1DC
F_USB_RECV8 = 0x0800C1FC
F_USB_SEND8 = 0x0800C29C
F_IDLE = 0x080006A8
F_FRAME = 0x08000708
F_LOG_DRAW = 0x08006604
F_LOG_LINE = 0x080066E0
DISPATCHER = 0x08007444
GAME_MAPS = (0x0101, 0x0007, 0x0001)
FRAME_S = 0.002                      # emulated seconds per idle frame (scaled)

FLASH_IDS = {"sanyo": (0x62, 0x13), "macronix": (0xC2, 0x09), "flash512": (0xBF, 0xD4)}


_OPEN_BUS = {}


def open_bus(start, length):
    key = (start, length)
    if key not in _OPEN_BUS:
        import array
        a = array.array("H", (((start + i) >> 1) & 0xFFFF for i in range(0, length, 2)))
        _OPEN_BUS[key] = a.tobytes()
    return _OPEN_BUS[key]


def make_game_rom(title=b"POKEMON RUBY", code=b"AXVE", tag=b"FLASH1M_V103", size=0x80000, tag_at=0x40000, seed=1):
    rng = random.Random(seed)
    rom = bytearray(rng.getrandbits(8) for _ in range(size))
    rom[0:4] = b"\x2e\x00\x00\xea"
    rom[0xA0:0xAC] = title.ljust(12, b"\0")[:12]
    rom[0xAC:0xB0] = code
    for t in (b"FLAS", b"SRAM", b"EEPR"):          # make sure no accidental tags
        i = rom.find(t)
        while i >= 0:
            rom[i] ^= 0x20
            i = rom.find(t)
    if tag:
        rom[tag_at:tag_at + len(tag)] = tag
    return bytes(rom)


class Hw:
    def __init__(self, fw, game_rom, save_type="sanyo", save=None, faults=None, log_lines=True):
        self.fw = fw
        self.game = game_rom
        self.save_type = save_type
        size = {"sanyo": 0x20000, "macronix": 0x20000, "flash512": 0x10000, "sram": 0x8000}.get(save_type, 0)
        self.save = bytearray(save[:size] if save else bytes(random.Random(7).getrandbits(8) for _ in range(size)))
        if len(self.save) < size:
            self.save += b"\xFF" * (size - len(self.save))
        self.original_save = bytes(self.save)
        self.faults = faults or {}
        self.mapped = None                  # None = DUO, else the map value
        self.awake = False
        self.bank = 0
        self.id_mode = False
        self.cmd = []
        self.violations = []
        self.bad = []
        self.log = []
        self.map_writes = []
        self.save_reads = 0
        self.save_writes = 0
        self.ignored = 0
        self.wake_hook = None
        self.h2d = queue.Queue()
        self.d2h = queue.Queue()
        self.done = threading.Event()
        self.sent_packets = 0
        self.rng = random.Random(self.faults.get("seed", 3))
        self.turn = "pc"                     # whose packet the link expects next
        self.link_errors = []
        self.pc_packets = 0

        mu = self.mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        mu.ctl_set_cpu_model(UC_CPU_ARM_926)       # Thumb-1 only, like the GBA's ARM7TDMI
        mu.mem_map(0x02000000, 0x40000)
        mu.mem_map(0x03000000, 0x8000)
        mu.mem_map(0x04000000, 0x1000)
        mu.mem_map(0x05000000, 0x1000)
        mu.mem_map(0x06000000, 0x18000)
        mu.mem_map(0x07000000, 0x1000)
        mu.mem_map(ROM, ROMSZ, UC_PROT_READ | UC_PROT_EXEC)
        mu.mem_map(SAVE, 0x10000, UC_PROT_READ)
        # open bus beyond the cartridge: the GBA returns the address bits
        mu.mem_write(ROM + VIEW, open_bus(ROM + VIEW, ROMSZ - VIEW))
        self.show_duo()
        self.save_view()
        mu.mem_write(0x04000208, struct.pack("<H", 1))
        mu.mem_write(0x04000204, struct.pack("<H", 0x4314))
        mu.mem_write(0x03001C3C, struct.pack("<I", 0x0203C000))
        mu.mem_write(0x03000000, fw[0xCCF4:0xCE6C])
        self.boot_iwram = bytes(mu.mem_read(0x03000000, 0x178))

        mu.hook_add(UC_HOOK_MEM_WRITE_PROT, self.on_rom_write, begin=ROM, end=ROM + ROMSZ - 1)
        mu.hook_add(UC_HOOK_MEM_WRITE_PROT, self.on_save_write, begin=SAVE, end=SAVE + 0xFFFF)
        mu.hook_add(UC_HOOK_MEM_READ, self.on_save_read, begin=SAVE, end=SAVE + 0xFFFF)
        mu.hook_add(UC_HOOK_CODE, self.on_rom_code, begin=ROM, end=ROM + 0x40000)
        mu.hook_add(UC_HOOK_MEM_READ, self.on_duo_regs, begin=0x09FE0000, end=0x09FFFFFF)
        self.flip_at = set(self.faults.get("flip_save_read", ()))
        self.restore = None

    # ------------------------------------------------------------ views
    def show_duo(self):
        img = self.fw + b"\xFF" * (VIEW - len(self.fw))
        self.mu.mem_write(ROM, img[:VIEW])

    def show_game(self):
        img = self.game + open_bus(ROM + len(self.game), VIEW - len(self.game))
        self.mu.mem_write(ROM, img[:VIEW])

    def save_view(self):
        if self.mapped is None or not self.awake or self.save_type in ("eeprom", "none"):
            data = b"\xFF" * 0x10000
        elif self.id_mode and self.save_type in FLASH_IDS:
            data = bytes(FLASH_IDS[self.save_type]) * 0x8000
        elif self.save_type == "sram":
            data = bytes(self.save) * 2
        else:
            data = bytes(self.save[self.bank * 0x10000:(self.bank + 1) * 0x10000])
        self.mu.mem_write(SAVE, data)

    # ------------------------------------------------------------ hooks
    def ret(self, value=None):
        if value is not None:
            self.mu.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
        self.mu.reg_write(UC_ARM_REG_PC, self.mu.reg_read(UC_ARM_REG_LR))

    def fail(self, msg):
        self.bad.append(msg)
        self.mu.emu_stop()

    def cstr(self, addr, n=80):
        b = bytes(self.mu.mem_read(addr, n))
        return b.split(b"\0")[0].decode("latin-1")

    def on_rom_code(self, mu, addr, size, _):
        if self.mapped is not None:
            return self.fail(f"executed ROM at {addr:#x} while the game was mapped")
        if addr == F_CARD_DETECT:
            self.ret(0)
        elif addr == F_USB_AVAIL:
            self.ret(0 if self.h2d.empty() else 1)
        elif addr == F_IDLE:
            if self.done.is_set():
                mu.emu_stop()
            time.sleep(FRAME_S)
            self.ret()
        elif addr in (F_FRAME, F_LOG_DRAW):
            self.ret()
        elif addr == F_LOG_LINE:
            self.log.append(self.cstr(mu.reg_read(UC_ARM_REG_R0)))
            self.ret()
        elif addr == F_USB_RECV8:
            pkt = self.h2d.get()
            mu.mem_write(mu.reg_read(UC_ARM_REG_R0), pkt)
            self.ret(0)
        elif addr == F_USB_SEND8:
            pkt = bytes(mu.mem_read(mu.reg_read(UC_ARM_REG_R0), 8))
            if self.turn != "gba":
                self.link_errors.append(f"GBA sent two packets in a row ({pkt.hex()}) - it would hang here")
                return self.fail(self.link_errors[-1])
            self.turn = "pc"
            self.deliver(pkt)
            self.ret(0)

    def deliver(self, pkt):
        """USB itself is reliable; faults model the DUO's chip during fast streams only."""
        self.sent_packets += 1
        f = self.faults
        if pkt[:4] == b"SBW\x30":
            self.stream_n = 0
            self.d2h.put(pkt)
            return
        if pkt[:4] == b"SBW\x32" or getattr(self, "stream_n", None) is None:
            self.stream_n = None if pkt[:4] == b"SBW\x32" else getattr(self, "stream_n", None)
            self.d2h.put(pkt)
            return
        self.stream_n += 1
        n = self.stream_n
        if n in f.get("corrupt_at", ()) or (f.get("corrupt_p") and self.rng.random() < f["corrupt_p"]):
            b = bytearray(pkt)
            b[self.rng.randrange(8)] ^= 1 << self.rng.randrange(8)
            pkt = bytes(b)
        self.d2h.put(pkt)

    def on_rom_write(self, mu, access, addr, size, value, _):
        v = value & 0xFFFF
        pc = mu.reg_read(UC_ARM_REG_PC)
        if addr == 0x0800009C:
            if not (0x02000000 <= pc < 0x02040000 or 0x03000000 <= pc < 0x03008000):
                self.fail(f"mapping register written from {pc:#x}, not RAM")
                return False
            self.map_writes.append(v)
            if v in GAME_MAPS:
                self.mapped, self.awake = v, False
                self.show_game()
                if self.wake_hook is None:
                    self.wake_hook = mu.hook_add(UC_HOOK_MEM_READ, self.on_rom_read, begin=ROM, end=ROM + ROMSZ - 1)
            elif v == 0x0120:
                self.mapped, self.awake = None, False
                self.show_duo()
            else:
                self.fail(f"unexpected mapping value {v:#x}")
                return False
            self.save_view()
            return True
        if self.mapped is None and addr in (0x0800AAAA, 0x08005554):
            if v == 0x9090:
                mu.mem_write(ROM, struct.pack("<HH", 0xBFBF, 0x2782))
            return True
        if self.mapped is None and addr == ROM and v == 0xF0F0:
            mu.mem_write(ROM, self.fw[:4])
            return True
        self.fail(f"unexpected write {v:#x} to {addr:#x} (mapped={self.mapped})")
        return False

    def on_rom_read(self, mu, access, addr, size, value, _):
        if self.mapped is not None and not self.awake:
            self.awake = True
            self.save_view()
        if self.wake_hook is not None:
            mu.hook_del(self.wake_hook)
            self.wake_hook = None

    def on_duo_regs(self, mu, access, addr, size, value, _):
        if self.mapped is not None:
            self.bad.append(f"read of DUO register area {addr:#x} while the game was mapped")

    def on_save_read_after(self, mu, access, addr, size, value, _):
        if self.restore:
            a, b = self.restore
            self.restore = None
            mu.mem_write(a, bytes([b]))

    def on_save_read(self, mu, access, addr, size, value, _):
        self.save_reads += 1
        if self.restore:
            a, b = self.restore
            self.restore = None
            mu.mem_write(a, bytes([b]))
        if self.save_reads in self.flip_at:
            old = bytes(mu.mem_read(addr, 1))[0]
            mu.mem_write(addr, bytes([old ^ 0x10]))          # one bad read, then the chip is fine
            self.restore = (addr, old)
        ime = struct.unpack("<H", mu.mem_read(0x04000208, 2))[0]
        wc = struct.unpack("<H", mu.mem_read(0x04000204, 2))[0]
        if self.mapped is not None and ime:
            self.bad.append("save read with interrupts enabled")
        if self.mapped is not None and wc & 3 != 3:
            self.bad.append(f"save read with WAITCNT {wc:#x}")

    def on_save_write(self, mu, access, addr, size, value, _):
        off, v = addr - SAVE, value & 0xFF
        self.save_writes += (self.mapped is not None and self.awake)
        if self.mapped is None or not self.awake:
            return True                                    # nothing connected: ignored
        t = self.save_type
        if t == "sram":
            self.violations.append(f"SRAM write {v:#x} at {off:#x}")
            self.save[off & 0x7FFF] = v
            self.save_view()
            return True
        if t in ("eeprom", "none"):
            self.violations.append(f"write to {t} cart at {off:#x}")
            return True
        if off == 0 and v == 0xF0 and not self.cmd:
            self.id_mode = False
            self.save_view()
            return True
        seq = self.cmd + [(off, v)]
        unlock = [(0x5555, 0xAA), (0x2AAA, 0x55)]
        if len(seq) <= 2 and seq == unlock[:len(seq)]:
            self.cmd = seq
            return True
        if len(seq) == 3 and seq[:2] == unlock and off == 0x5555:
            self.cmd = []
            if v == 0x90:
                self.id_mode = True
            elif v == 0xF0:
                self.id_mode = False
            elif v == 0xB0 and t in ("sanyo", "macronix"):
                self.cmd = seq
            else:
                self.violations.append(f"Flash command {v:#x} (could alter the save)")
            self.save_view()
            return True
        if len(seq) == 4 and seq[2] == (0x5555, 0xB0) and off == 0:
            self.bank, self.cmd = v & 1, []
            self.save_view()
            return True
        # real Flash chips ignore an incomplete/out-of-order sequence and reset
        self.ignored += 1
        self.cmd = [(off, v)] if (off, v) == (0x5555, 0xAA) else []
        return True

    # ------------------------------------------------------------ run
    def run(self):
        mu = self.mu
        mu.reg_write(UC_ARM_REG_SP, 0x03007E00)
        mu.reg_write(UC_ARM_REG_LR, STOP | 1)
        try:
            mu.emu_start(DISPATCHER | 1, STOP)
        except UcError as e:
            self.bad.append(f"CPU fault: {e} at {mu.reg_read(UC_ARM_REG_PC):#x}")
        self.d2h.put(None)

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.done.set()
        self.turn = "gba"                          # the nudge is a PC packet like any other
        self.h2d.put(b"CBW\x02" + bytes(4))      # nudge the dispatcher
        self.thread.join(15)


class Transport:
    def __init__(self, hw):
        self.hw = hw

    def write(self, data, timeout=5000):
        hw = self.hw
        if hw.turn != "pc":
            hw.link_errors.append(f"PC sent two packets in a row ({bytes(data).hex()}) - the DUO never takes it")
            time.sleep(min(timeout, 300) / 1000)
            import usb.core
            raise usb.core.USBTimeoutError("timeout")
        hw.turn = "gba"
        hw.pc_packets += 1
        hw.h2d.put(bytes(data))

    def read(self, timeout=5000):
        if self.hw.faults.get("pc_delay"):
            time.sleep(self.hw.faults["pc_delay"])
        try:
            pkt = self.hw.d2h.get(timeout=max(0.05, timeout / 1000))
        except queue.Empty:
            import usb.core
            raise usb.core.USBTimeoutError("timeout")
        if pkt is None:
            import maxduo_saves as M
            raise M.DuoError("emulated device stopped: " + "; ".join(self.hw.bad))
        return pkt

    def close(self):
        pass
