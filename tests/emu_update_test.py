"""
Emulate a full firmware update: the real tool's upload_update() talks to the
official 1.06 firmware's own receive code, which then runs Datel's RAM-resident
reflash routine against a simulated SST flash chip. Afterwards the chip must
hold exactly the new image, and the DUO's storage area must be untouched.
"""
import struct, sys, threading, zlib, queue
from unicorn import *
from unicorn.arm_const import *
import paths
import maxduo_saves as M
import emu_test as E

OFFICIAL = paths.read_build("fw.bin")
CHIP_SIZE = 0x40000
REFLASH_DONE = 0x03001000      # Datel's post-update wait loop


class UpdateSim(E.Sim):
    def __init__(self):
        E.FW = OFFICIAL                      # device currently runs official firmware
        super().__init__(bytes(0x20000))
        mu = self.mu
        for base, size in ((0x05000000, 0x400), (0x06000000, 0x18000), (0x07000000, 0x400)):
            mu.mem_map(base, size)
        # the DUO's flash chip: program + a sentinel "storage" area after 0x30000
        self.chip = bytearray(OFFICIAL) + b"\xFF" * (0x30000 - len(OFFICIAL))
        self.chip += bytes((i * 7) & 0xFF for i in range(CHIP_SIZE - 0x30000))
        self.storage_before = bytes(self.chip[0x30000:])
        mu.mem_write(E.ROM, bytes(self.chip))
        self.sst = []
        self.program_next = False
        self.erased = set()
        self.log = []
        mu.hook_add(UC_HOOK_CODE, self.on_ram_code, begin=REFLASH_DONE, end=REFLASH_DONE + 1)
        mu.hook_add(UC_HOOK_MEM_UNMAPPED, self.on_unmapped)
        self.extra_pages = set()

    def on_unmapped(self, mu, access, addr, size, value, _):
        page = addr & ~0xFFF
        if 0x08000000 <= addr < 0x0F000000:
            self.bad.append(f"unmapped access at {addr:#x}")
            return False
        mu.mem_map(page, 0x1000)
        self.extra_pages.add(page)
        return True

    def on_ram_code(self, mu, addr, size, _):
        if "call 0x800b98c" not in self.log:
            # before reflashing, 0x03001000 holds a UI helper normally loaded at boot
            return self.ret()
        self.log.append(f"reached {addr:#x} after reflash")
        self.finished = True
        mu.emu_stop()

    def on_rom_code(self, mu, addr, size, _):
        if addr == E.F_USB_SEND8:
            self.log.append("dev->pc " + bytes(mu.mem_read(self.r(UC_ARM_REG_R0), 8)).hex())
        if addr in (0x08006C88, 0x0800B98C, 0x0800B9E4, 0x08001CC4, 0x08006CB0):
            self.log.append(f"call {addr:#x}")
        if addr == 0x08006714 or addr == 0x0800673C or addr == 0x080066E0:
            self.log.append(f"log r0={self.r(UC_ARM_REG_R0):#x} r1={self.r(UC_ARM_REG_R1):#x}")
        if addr == 0x08006714 or addr == 0x0800673C or addr == 0x080066E0:
            return self.ret(0)
        return super().on_rom_code(mu, addr, size, _)

    def on_rom_write(self, mu, access, addr, size, value, _):
        if addr == 0x0800009C and not self.program_next:
            return super().on_rom_write(mu, access, addr, size, value, _)
        off, v = addr - E.ROM, value & 0xFFFF
        if self.program_next:
            self.program_next = False
            cur = struct.unpack_from("<H", self.chip, off)[0]
            new = cur & v                              # flash can only clear bits
            struct.pack_into("<H", self.chip, off, new)
            mu.mem_write(addr, struct.pack("<H", new))
            if new != v:
                self.bad.append(f"programmed {v:#06x} over {cur:#06x} at {off:#x} without erase")
            return True
        seq = self.sst + [(off, v)]
        unlock = [(0xAAAA, 0xAAAA), (0x5554, 0x5555)]
        if seq[:len(unlock)] == unlock[:len(seq)] and len(seq) <= 2:
            self.sst = seq
            return True
        if len(seq) == 3 and seq[:2] == unlock and off == 0xAAAA:
            cmd = v & 0xFF
            if cmd == 0xA0:
                self.program_next, self.sst = True, []
            elif cmd == 0x90:
                self.sst = []
                mu.mem_write(E.ROM, struct.pack("<HH", 0xBFBF, 0x2782))
            elif cmd == 0x80:
                self.sst = seq
            else:
                self.bad.append(f"unknown SST command {cmd:#x}")
                return False
            return True
        if len(seq) in (4, 5) and seq[2][1] & 0xFF == 0x80 and seq[3:] == unlock[:len(seq) - 3]:
            self.sst = seq
            return True
        if len(seq) == 6 and seq[2][1] & 0xFF == 0x80 and v & 0xFF == 0x30:
            sector = off & ~(self.sector - 1)
            self.chip[sector:sector + self.sector] = b"\xFF" * self.sector
            mu.mem_write(E.ROM + sector, b"\xFF" * self.sector)
            self.erased.add(sector)
            self.sst = []
            return True
        if v == 0xF0F0:
            self.sst = []
            mu.mem_write(E.ROM, bytes(self.chip[:4]))
            return True
        self.bad.append(f"unexpected flash write {v:#x} at {off:#x} (seq {seq})")
        return False

    sector = 0x2000   # firmware picks 8 KB sectors when the chip ID reads 0xBFBF


def main(update_path):
    crc, payload = M.check_update_file(update_path)
    sim = UpdateSim()
    sim.finished = False
    t = threading.Thread(target=sim.run, daemon=True)
    t.start()
    duo = M.MaxDuo(E.EmuTransport(sim))
    err = None
    try:
        assert duo.hello() == 0x104
        duo.upload_update(payload)
    except Exception as e:
        err = e
    t.join(600)
    padded = len(payload) + (len(payload) & 0x7F)
    expect = payload + b"\0" * (padded - len(payload))
    checks = {
        "tool and firmware completed the upload": err is None,
        "reflash ran to Datel's completion loop": sim.finished,
        "no unexpected flash operations": not sim.bad,
        "new program on chip matches update exactly": bytes(sim.chip[:padded]) == expect,
        "DUO storage area untouched": bytes(sim.chip[0x30000:]) == sim.storage_before,
        "erased only the program area": all(s < 0x30000 for s in sim.erased) and len(sim.erased) == 0x30000 // sim.sector,
    }
    print(f"update {update_path.split('/')[-1]} (CRC {crc:08X})")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    if err: print("   error:", err)
    if sim.bad: print("   problems:", sim.bad[:5])
    print("   (display/IO pages auto-mapped:", sorted(hex(p) for p in sim.extra_pages), ")")
    if not all(checks.values()):
        print("   last events:", *sim.log[-25:], sep="\n     ")
    return all(checks.values())


if __name__ == "__main__":
    ok = all(main(p) for p in sys.argv[1:])
    print("ALL PASSED" if ok else "SOME FAILED")
