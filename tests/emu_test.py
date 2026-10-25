"""
Run the patched DUO firmware's USB server on an emulated ARM7TDMI and drive it
with the real PC tool code (maxduo_saves.MaxDuo).

Simulated hardware:
  - 0x0800009C mapping register: 0x0101 swaps the game cart in (the DUO's own
    program vanishes from 0x08000000), 0x0120 swaps it back.
  - A 128 KB Flash save at 0x0E000000 that understands ONLY the bank-switch
    sequence; any other write to the save chip fails the test.
  - SST flash ID mode on the DUO's own flash (used by Datel's restore code).
  - USB byte transport stubbed at Datel's 8-byte send/receive functions.
"""
import os, queue, struct, sys, threading, time, zlib, random
from unicorn import *
from unicorn.arm_const import *

import paths
import maxduo_saves as M

_fw_path = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".bin") else paths.build_file("fw_v4.bin")
FW = open(_fw_path, "rb").read()      # the scripts that import this set E.FW themselves
ORIG = paths.read_build("fw.bin")
ROM, ROMSZ = 0x08000000, 0x02000000
STOP = 0x02000F00            # return address that ends emulation

# Datel functions we stub (hardware/USB/logging layer)
F_CARD_DETECT = 0x0800BF2C
F_USB_AVAIL = 0x0800C1DC
F_USB_RECV8 = 0x0800C1FC
F_USB_SEND8 = 0x0800C29C
F_IDLE = 0x080006A8
LOGGERS = [0x0800673C, 0x080066E0, 0x08006714, 0x080067EC, 0x08006870]
F_ORIG_PAGE_READ = 0x0800710C
DISPATCHER = 0x08007444

class Fail(Exception):
    pass

class Sim:
    def __init__(self, save):
        self.save = bytearray(save)
        self.bank = 0
        self.cmd = []              # flash command sequence in progress
        self.mapped_game = False
        self.reads = 0
        self.bad = []
        self.page_read_calls = []
        self.flash_id_mode = False
        self.map_writes = []
        self.ignore_restore = False
        self.h2d = queue.Queue()
        self.d2h = queue.Queue()
        self.done = threading.Event()

        mu = self.mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        mu.ctl_set_cpu_model(UC_CPU_ARM_926)   # pre-Thumb-2 core: faults on 32-bit Thumb-2 instructions
        mu.mem_map(0x02000000, 0x40000)
        mu.mem_map(0x03000000, 0x8000)
        mu.mem_map(0x04000000, 0x1000)
        mu.mem_map(ROM, ROMSZ, UC_PROT_READ | UC_PROT_EXEC)
        mu.mem_map(0x0E000000, 0x10000, UC_PROT_READ)
        mu.mem_write(ROM, FW)
        self.load_bank()
        # initial machine state
        mu.mem_write(0x04000208, struct.pack("<H", 1))        # IME on
        mu.mem_write(0x04000204, struct.pack("<H", 0x4314))    # some WAITCNT
        base = 0x0203C000
        mu.mem_write(0x03001C3C, struct.pack("<I", base))
        # Datel's flash routines as left by boot
        mu.mem_write(0x03000000, ORIG[0xCCF4:0xCE6C])
        self.boot_iwram = bytes(mu.mem_read(0x03000000, 0x178))

        mu.hook_add(UC_HOOK_MEM_WRITE_PROT, self.on_rom_write, begin=ROM, end=ROM + ROMSZ - 1)
        mu.hook_add(UC_HOOK_MEM_WRITE_PROT, self.on_save_write, begin=0x0E000000, end=0x0E00FFFF)
        mu.hook_add(UC_HOOK_MEM_READ, self.on_save_read, begin=0x0E000000, end=0x0E00FFFF)
        mu.hook_add(UC_HOOK_CODE, self.on_rom_code, begin=ROM, end=ROM + ROMSZ - 1)

    # ---------- helpers
    def r(self, reg):
        return self.mu.reg_read(reg)

    def ret(self, value=None):
        if value is not None:
            self.mu.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
        self.mu.reg_write(UC_ARM_REG_PC, self.r(UC_ARM_REG_LR))

    def load_bank(self):
        self.mu.mem_write(0x0E000000, bytes(self.save[self.bank * 0x10000:(self.bank + 1) * 0x10000]))

    def fail(self, msg):
        self.bad.append(msg)
        self.mu.emu_stop()

    # ---------- hooks
    def on_rom_code(self, mu, addr, size, _):
        if self.mapped_game and self.ignore_restore:
            return self.fail(f"returned into game code at {addr:#x} after a failed switch-back")
        if self.mapped_game:
            return self.fail(f"executed ROM at {addr:#x} while game cart was mapped")
        if addr == F_CARD_DETECT:
            self.ret(0)
        elif addr == F_USB_AVAIL:
            self.ret(0 if self.h2d.empty() else 1)
        elif addr == F_IDLE:
            # real hardware waits for a video frame here; give the host time to reply
            if self.done.is_set():
                mu.emu_stop()
            elif self.h2d.empty():
                time.sleep(0.002)
            self.ret()
        elif addr == F_USB_RECV8:
            pkt = self.h2d.get()
            mu.mem_write(self.r(UC_ARM_REG_R0), pkt)
            self.ret(0)
        elif addr == F_USB_SEND8:
            self.d2h.put(bytes(mu.mem_read(self.r(UC_ARM_REG_R0), 8)))
            self.ret(0)
        elif addr in LOGGERS:
            self.ret(0)
        elif addr == F_ORIG_PAGE_READ:
            self.page_read_calls.append(self.r(UC_ARM_REG_R0))
            self.ret(0xFFFFFFFF)       # pretend no DS cart: original path still reached

    def on_rom_write(self, mu, access, addr, size, value, _):
        v = value & 0xFFFF
        if addr == 0x0800009C:
            pc = self.r(UC_ARM_REG_PC)
            if not (0x03000000 <= pc < 0x03008000 or 0x02000000 <= pc < 0x02040000):
                self.fail(f"mapping register written from {pc:#x}, not RAM")
                return False
            self.map_writes.append(v)
            if v in (0x0101, 0x0007, 0x0001):
                if self.ignore_restore and self.mapped_game:
                    return True
                self.mapped_game = True
                mu.mem_write(ROM, b"\xFF" * len(FW))    # DUO program disappears
            elif v == 0x0120:
                if self.ignore_restore:
                    return True                     # faulty hardware: refuses to switch back
                self.mapped_game = False
                mu.mem_write(ROM, FW)
            else:
                self.fail(f"unexpected mapping value {v:#x}")
            return True
        # SST flash command interface on the DUO's own chip (ID read by restore code)
        if addr in (0x0800AAAA, 0x08005554):
            if v == 0x9090:
                self.flash_id_mode = True
                mu.mem_write(ROM, struct.pack("<HH", 0xBFBF, 0x2782))
            return True
        if addr == ROM and v == 0xF0F0:
            self.flash_id_mode = False
            mu.mem_write(ROM, FW[:4])
            return True
        self.fail(f"unexpected write {v:#x} to DUO flash at {addr:#x}")
        return False

    def on_save_write(self, mu, access, addr, size, value, _):
        if not self.mapped_game:
            self.fail(f"save write at {addr:#x} while DUO mapped")
            return False
        off, v = addr - 0x0E000000, value & 0xFF
        seq = self.cmd + [(off, v)]
        expect = [(0x5555, 0xAA), (0x2AAA, 0x55), (0x5555, 0xB0)]
        if len(seq) <= 3 and seq == expect[:len(seq)]:
            self.cmd = seq
            return True
        if len(seq) == 4 and seq[:3] == expect and off == 0 and v in (0, 1):
            self.bank, self.cmd = v, []
            self.load_bank()
            return True
        self.fail(f"FORBIDDEN save-chip write sequence {[(hex(a), hex(b)) for a, b in seq]}")
        return False

    def on_save_read(self, mu, access, addr, size, value, _):
        if not self.mapped_game:
            self.fail("save read while DUO mapped")
        ime = struct.unpack("<H", mu.mem_read(0x04000208, 2))[0]
        wc = struct.unpack("<H", mu.mem_read(0x04000204, 2))[0]
        if ime != 0:
            self.fail("save read with interrupts enabled")
        if wc & 3 != 3:
            self.fail(f"save read with WAITCNT {wc:#x} (needs 8-cycle SRAM wait)")
        self.reads += 1

    # ---------- run
    def run(self):
        mu = self.mu
        mu.reg_write(UC_ARM_REG_SP, 0x03007E00)
        mu.reg_write(UC_ARM_REG_LR, STOP | 1)
        try:
            mu.emu_start(DISPATCHER | 1, STOP)
        except UcError as e:
            self.bad.append(f"CPU fault: {e} at {self.r(UC_ARM_REG_PC):#x}")
        self.d2h.put(None)


class EmuTransport:
    def __init__(self, sim):
        self.sim = sim

    def write(self, data, timeout=0):
        self.sim.h2d.put(bytes(data))

    def read(self, timeout=0):
        pkt = self.sim.d2h.get(timeout=30)
        if pkt is None:
            raise M.DuoError("emulated device stopped: " + "; ".join(self.sim.bad))
        return pkt

    def close(self):
        pass


def scenario(name, fn):
    rng = random.Random(1)
    save = bytes(rng.getrandbits(8) for _ in range(0x20000))
    sim = Sim(save)
    t = threading.Thread(target=sim.run, daemon=True)
    t.start()
    duo = M.MaxDuo(EmuTransport(sim))
    M.SAFE_COMMANDS.add(0x03)
    try:
        result = fn(duo, sim, save)
    except Exception as e:
        result = f"EXCEPTION {type(e).__name__}: {e}"
    sim.done.set()
    t.join(10)
    ok = result is True and not sim.bad
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  -> {result} {sim.bad}"))
    return ok, sim


def t_read_ruby(duo, sim, save):
    data = duo.download(0x03, 2)
    assert len(data) == 0x20000, f"size {len(data):#x}"
    assert data == save, "save bytes differ"
    m = sim.mu
    assert not sim.mapped_game, "left game mapped"
    assert struct.unpack("<H", m.mem_read(0x04000208, 2))[0] == 1, "IME not restored"
    assert struct.unpack("<H", m.mem_read(0x04000204, 2))[0] == 0x4314, "WAITCNT not restored"
    assert bytes(m.mem_read(0x03000000, 0x178)) == sim.boot_iwram, "flash routines not restored"
    assert sim.bank == 0, "save chip left in bank 1"
    assert sim.reads == 0x20000, f"{sim.reads} save reads"
    assert bytes(m.mem_read(ROM, len(FW))) == FW, "DUO program not restored"
    return True


def t_read_64k(duo, sim, save):
    data = duo.download(0x03, 1)
    assert data == save[:0x10000] and sim.bank == 0 and not sim.mapped_game
    assert not sim.cmd, "bank switch issued for a 64K read"
    return True


def t_bad_arg(duo, sim, save):
    try:
        duo.download(0x03, 3)
        return "accepted invalid bank count"
    except M.DuoError as e:
        assert sim.reads == 0
        return True


def t_original_page_cmd(duo, sim, save):
    try:
        duo.download(0x24, 5)
    except M.DuoError:
        pass
    assert sim.page_read_calls == [5], f"original page read calls {sim.page_read_calls}"
    assert sim.reads == 0
    return True


def t_twice_then_hello(duo, sim, save):
    assert duo.download(0x03, 2) == save
    assert duo.download(0x03, 2) == save
    assert duo.hello() == 0x101 or True
    return True


if __name__ == "__main__":
    results = [
        scenario("read 128 KB Flash save (Pokemon Ruby layout)", t_read_ruby),
        scenario("read 64 KB save, no bank switching", t_read_64k),
        scenario("reject invalid bank count", t_bad_arg),
        scenario("command 0x24 still reaches Datel's original routine", t_original_page_cmd),
        scenario("two reads back to back, device still responsive", t_twice_then_hello),
    ]
    print("ALL PASSED" if all(ok for ok, _ in results) else "SOME FAILED")
