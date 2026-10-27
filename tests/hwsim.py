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
  * (v4, from lab sessions 1-4) the Actel overlays 0x08000020-0x0800009F in
    BOTH modes: zeros, with the mapping register reading F0 0F at 0x9C. The
    DUO's flash has no Nintendo logo; it borrows the game's at power-on. So a
    BIOS HardReset (logo check) freezes at "GAME BOY"; a BIOS SoftReset skips
    the check and restarts the DUO program, whose start-up looks once for a
    waiting hello (Datel's routine 0x080073F4, run for real here): hello
    waiting -> USB SERVER MODE, otherwise the menu (USB not served).
  * the USB chip runs on the cable's power: with the GBA off it still takes a
    packet and holds it (Hw(powered=False), then power_on()); old packets from
    an interrupted run can be waiting (inject_stale()).
  * games are padded with FF inside their ROM chip; open bus starts at the
    chip size (rom_size=). The DUO's 512 KB flash appears twice below 1 MB.
  * the save area is an 8-bit bus: 16/32-bit reads are a failure (v4's first
    real-hardware save found this)
  * WAITCNT with 2-cycle first ROM access corrupts game ROM reads (Ruby's chip
    did on real hardware); slower settings read correctly.
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
BOOT_CHECK = 0x080073F4              # Datel: if a hello is waiting, answer it and return 1
BOOT_RET = 0x02000F20                # where the simulated start-up returns to
MENU_LOOP = 0x02000F30               # 'the DUO is showing its menu' (USB not served)
OVERLAY = (0x20, 0xA0)               # Actel register window over the header
MAPREG_READ = b"\xf0\x0f\x00\x00"

# The 156-byte logo every licensed GBA game carries at 0x04 (the BIOS checks it
# at power-on). Used here only to build test ROMs and to model the BIOS check.
LOGO = bytes.fromhex(
    "24FFAE51699AA2213D84820A84E409AD11248B98C0817F21A352BE199309CE2010464A4AF82731EC58C7E833"
    "82E3CEBF85F4DF94CE4B09C194568AC01372A7FC9F844D73A3CA9A615897A327FC039876231DC7610304AE56"
    "BF38840040A70EFDFF52FE036F9530F197FBC08560D68025A963BE03014E38E2F9A234FFBB3E0344780090CB"
    "88113A9465C07C6387F03CAFD625E48B380AAC7221D4F807")
assert len(LOGO) == 156


_OPEN_BUS = {}


def open_bus(start, length):
    key = (start, length)
    if key not in _OPEN_BUS:
        import array
        a = array.array("H", (((start + i) >> 1) & 0xFFFF for i in range(0, length, 2)))
        _OPEN_BUS[key] = a.tobytes()
    return _OPEN_BUS[key]


def make_game_rom(title=b"POKEMON RUBY", code=b"AXVE", tag=b"FLASH1M_V103", size=0x80000, tag_at=0x40000, seed=1,
                  rom_size=None, revision=0):
    """A test game: `size` bytes of data, FF-padded to `rom_size` (the chip size)."""
    rng = random.Random(seed)
    rom = bytearray(rng.getrandbits(8) for _ in range(size))
    rom[0:4] = b"\x2e\x00\x00\xea"
    rom[4:0xA0] = LOGO
    rom[0xA0:0xAC] = title.ljust(12, b"\0")[:12]
    rom[0xAC:0xB0] = code
    rom[0xB0:0xB2] = b"01"
    rom[0xB2] = 0x96
    rom[0xB3:0xBC] = bytes(9)
    rom[0xBC] = revision
    for t in (b"FLAS", b"SRAM", b"EEPR"):          # make sure no accidental tags
        i = rom.find(t)
        while i >= 0:
            rom[i] ^= 0x20
            i = rom.find(t)
    if tag:
        rom[tag_at:tag_at + len(tag)] = tag
    rom[0xBD] = (-(sum(rom[0xA0:0xBD])) - 0x19) & 0xFF
    if rom_size:
        rom += b"\xFF" * (rom_size - len(rom))
    return bytes(rom)


class Hw:
    def __init__(self, fw, game_rom, save_type="sanyo", save=None, faults=None, log_lines=True, powered=True):
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
        self.powered = powered
        self.boots = []                      # 'usb' / 'menu' per simulated start-up
        self.restarts = 0
        self.boot_snapshot = None            # worst case: the start-up check runs the instant the GBA resets
        self.usb_latency = 0.0               # seconds before a PC packet is visible to the GBA (real USB: ~8-10 ms polling)
        self.h2d_times = []                  # when each queued PC packet becomes visible
        self.waitcnt = 0x4314
        self.view = max(VIEW, len(game_rom))

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
        mu.mem_write(ROM + self.view, open_bus(ROM + self.view, ROMSZ - self.view))
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
        mu.hook_add(UC_HOOK_INTR, self.on_swi)
        mu.hook_add(UC_HOOK_CODE, self.on_boot_ret, begin=BOOT_RET, end=MENU_LOOP + 2)
        mu.hook_add(UC_HOOK_MEM_WRITE, self.on_waitcnt, begin=0x04000204, end=0x04000205)
        mu.mem_write(BOOT_RET, b"\xfe\xe7")     # b . (never really executed)
        mu.mem_write(MENU_LOOP, b"\xfe\xe7")
        self.flip_at = set(self.faults.get("flip_save_read", ()))
        self.restore = None
        self.link_partner = None             # a second GBA on the link port (see attach_link_partner)
        self.link_words = []                 # (word sent by the DUO's GBA, word sent by the partner)

    # ------------------------------------------------------------ link port
    def attach_link_partner(self, partner, delay_blocks=40):
        """A second GBA on the link port, as the clock master in Normal 32-bit mode
        (as a Pokemon game is when it looks for a Wireless Adapter). partner.out is
        the word it sends next; partner.intr(word) receives ours. A transfer happens
        only once our side has armed SIOCNT (start bit) with an external clock."""
        self.link_partner = partner
        self.sio_armed = None
        self.blocks = 0
        self.sio_delay = delay_blocks
        self.mu.hook_add(UC_HOOK_MEM_WRITE, self.on_siocnt, begin=0x04000128, end=0x04000129)
        self.mu.hook_add(UC_HOOK_BLOCK, self.on_block)

    # ------------------------------------------------------------ link port, line by line
    def attach_link_game(self, gen, blocks_per_line=25, xfer_blocks=6, lag_bits=0):
        """A second GBA running librfu (tests/rfu_game_model.py): either side can be the
        clock master, and both see each other's SO line (SIOCNT bit 3 -> the other's bit 2).
        A word only arrives if the receiving side was armed (start bit set) when the clock
        ran; otherwise it sees its partner's idle SO level and the word is lost, as on
        hardware. VCOUNT advances one line every blocks_per_line blocks."""
        self.lg = gen
        self.lg_bpl, self.lg_x = blocks_per_line, xfer_blocks
        # lag_bits=1: what lab session 11 suggests - the DUO, as clock slave at 2 MHz, answers one
        # clock late, so the game reads the DUO's idle SO level first, then its word minus the last bit
        self.lg_lag = lag_bits
        self.blocks = 0
        self.game_so, self.game_armed, self.game_got = 0, None, []
        self.lg_op, self.lg_deadline, self.lg_xfer = None, 0, None
        self.duo_xfer = None
        self.lg_result, self.lg_error, self.lg_done = None, None, False
        self.link_trouble = []
        self.mu.hook_add(UC_HOOK_MEM_WRITE, self.lg_on_siocnt, begin=0x04000128, end=0x04000129)
        self.mu.hook_add(UC_HOOK_MEM_READ, self.lg_on_read, begin=0x04000006, end=0x04000007)
        self.mu.hook_add(UC_HOOK_MEM_READ, self.lg_on_read, begin=0x04000128, end=0x04000129)
        self.mu.hook_add(UC_HOOK_BLOCK, self.lg_on_block)
        self.lg_resume(None)

    def lg_cnt(self):
        return struct.unpack("<H", self.mu.mem_read(0x04000128, 2))[0]

    def lg_duo_so(self):
        return (self.lg_cnt() >> 3) & 1

    def lg_on_read(self, mu, access, addr, size, value, _):
        if addr in (0x04000006, 0x04000007):
            mu.mem_write(0x04000006, struct.pack("<H", (self.blocks // self.lg_bpl) % 228))
        else:
            c = self.lg_cnt() & ~4 | (4 if self.game_so else 0)
            mu.mem_write(0x04000128, struct.pack("<H", c))

    def lg_on_siocnt(self, mu, access, addr, size, value, _):
        old = self.lg_cnt()
        v = value & 0xFFFF if addr == 0x04000128 and size >= 2 else \
            (old & 0xFF00 | value & 0xFF) if addr == 0x04000128 else (old & 0xFF | (value << 8) & 0xFF00)
        if v & 0x80 and v & 0x1 and not (old & 0x80 and old & 0x1):        # the DUO clocks a word
            if self.lg_xfer is not None:
                self.link_trouble.append("both sides started a transfer as clock master")
            word = struct.unpack("<I", mu.mem_read(0x04000120, 4))[0]
            self.duo_xfer = (self.blocks + self.lg_x, word)

    def lg_resume(self, value):
        try:
            self.lg_op = self.lg.send(value)
        except StopIteration as e:
            self.lg_op, self.lg_result, self.lg_done = None, e.value, True
            return
        except Exception as e:                     # the game's own error (LinkError etc.)
            self.lg_op, self.lg_error, self.lg_done = None, e, True
            return
        op = self.lg_op
        if op[0] in ("wait_si", "wait_xfer", "delay"):
            self.lg_deadline = self.blocks + (op[1] if op[0] == "delay" else op[2] if op[0] == "wait_si" else op[1])
        self.lg_xfer = None

    def lg_finish_duo_xfer(self, mu):
        done_at, word = self.duo_xfer
        if self.blocks < done_at:
            return
        self.duo_xfer = None
        if self.game_armed is not None:
            got_back, self.game_armed = self.game_armed, None
            self.game_got.append(word)
        else:
            got_back = 0xFFFFFFFF if self.game_so else 0
            self.link_trouble.append(f"the DUO clocked {word:#010x} but the game wasn't ready")
        self.link_words.append((word, got_back))
        mu.mem_write(0x04000120, struct.pack("<I", got_back))
        mu.mem_write(0x04000128, struct.pack("<H", self.lg_cnt() & ~0x80))

    def lg_on_block(self, mu, addr, size, _):
        self.blocks += 1
        if self.duo_xfer is not None:
            self.lg_finish_duo_xfer(mu)
        for _ in range(64):
            op = self.lg_op
            if op is None:
                return
            k = op[0]
            if k == "so":
                self.game_so = op[1]; self.lg_resume(None); continue
            if k == "arm":
                self.game_armed, self.game_so = op[1], 0; self.game_got = []; self.lg_resume(None); continue
            if k == "delay":
                if self.blocks >= self.lg_deadline:
                    self.lg_resume(None); continue
                return
            if k == "wait_si":
                if self.lg_duo_so() == op[1]:
                    self.lg_resume(True); continue
                if self.blocks >= self.lg_deadline:
                    self.lg_resume(False); continue
                return
            if k == "until_ready":                       # the player switches the game on
                cnt = self.lg_cnt()
                if cnt & 0x80 and not cnt & 0x1 and cnt & 0x1000:
                    self.lg_resume(True); continue
                return
            if k == "wait_xfer":
                if self.game_got:
                    self.lg_resume(self.game_got.pop(0)); continue
                if self.blocks >= self.lg_deadline:
                    self.lg_resume(None); continue
                return
            if k == "xfer":
                if self.lg_xfer is None:
                    cnt = self.lg_cnt()
                    armed = cnt & 0x80 and not cnt & 0x1 and cnt & 0x1000
                    duo_word = struct.unpack("<I", mu.mem_read(0x04000120, 4))[0] if armed else None
                    if armed and self.lg_lag and len(op) > 2 and op[2] == 2:
                        duo_word = ((cnt >> 3 & 1) << 31) | (duo_word >> 1)
                    self.lg_xfer = (self.blocks + self.lg_x, duo_word)
                    return
                done_at, duo_word = self.lg_xfer
                if self.blocks < done_at:
                    return
                if duo_word is None:
                    got = 0xFFFFFFFF if self.lg_duo_so() else 0
                    self.link_trouble.append(f"the game clocked {op[1]:#010x} but the DUO wasn't ready")
                else:
                    got = duo_word
                    mu.mem_write(0x04000120, struct.pack("<I", op[1]))
                    mu.mem_write(0x04000128, struct.pack("<H", self.lg_cnt() & ~0x80))
                self.link_words.append((got, op[1]))
                self.lg_resume(got); continue
            return

    def on_siocnt(self, mu, access, addr, size, value, _):
        v = value & 0xFFFF if addr == 0x04000128 else (value << 8) & 0xFF00
        if v & 0x80 and not v & 0x1 and v & 0x1000 and not v & 0x2000:
            self.sio_armed = self.blocks + self.sio_delay      # external clock, 32-bit normal, start
        elif not v & 0x80:
            self.sio_armed = None

    def on_block(self, mu, addr, size, _):
        self.blocks += 1
        if self.sio_armed is not None and self.blocks >= self.sio_armed:
            self.sio_armed = None
            ours = struct.unpack("<I", mu.mem_read(0x04000120, 4))[0]
            theirs = self.link_partner.out
            self.link_partner.intr(ours)
            self.link_words.append((ours, theirs))
            mu.mem_write(0x04000120, struct.pack("<I", theirs))
            cnt = struct.unpack("<H", mu.mem_read(0x04000128, 2))[0]
            mu.mem_write(0x04000128, struct.pack("<H", cnt & ~0x80))

    # ------------------------------------------------------------ views
    def overlay(self):
        a, b = OVERLAY
        self.mu.mem_write(ROM + a, bytes(b - a))
        self.mu.mem_write(ROM + 0x9C, MAPREG_READ)

    def show_duo(self):
        flash = (self.fw + b"\xFF" * (0x80000 - len(self.fw)))[:0x80000]
        img = flash * 2 + open_bus(ROM + 0x100000, max(0, self.view - 0x100000))
        self.mu.mem_write(ROM, img[:self.view])
        self.overlay()

    def show_game(self):
        game = bytearray(self.game)
        if (self.waitcnt >> 2) & 3 == 2:                 # 2-cycle first access: too fast for the ROM
            for o in range(0x100, min(len(game), 0x80000), 0x1000):
                game[o] ^= 0x01
        img = bytes(game) + open_bus(ROM + len(game), self.view - len(game))
        self.mu.mem_write(ROM, img[:self.view])
        self.overlay()

    def on_waitcnt(self, mu, access, addr, size, value, _):
        # Rewriting ROM memory inside an ordinary write hook crashes unicorn, so pause
        # the CPU after this instruction; run() applies the new view and resumes.
        if addr == 0x04000204 and size >= 2:
            old, self.waitcnt = self.waitcnt, value & 0xFFFF
            if self.mapped is not None and ((old >> 2) & 3 == 2) != ((self.waitcnt >> 2) & 3 == 2):
                self.pending_view = True
                mu.emu_stop()

    def inject_stale(self, pkt):
        """A packet the DUO sent during an earlier, interrupted run, still held by the USB chip."""
        self.d2h.put(bytes(pkt))

    # ------------------------------------------------------------ start-up / reset
    def visible(self):
        """Is a PC packet waiting that the GBA can see yet?"""
        if self.h2d.empty():
            return False
        return not self.h2d_times or self.h2d_times[0] <= time.perf_counter()

    def boot(self, mu):
        """Datel's start-up, from the point where it checks for a waiting hello. Modelled as
        happening the instant the GBA resets (the worst case: on real hardware Datel's
        start-up takes a little while first, which only helps)."""
        self.boot_snapshot = self.visible()
        mu.reg_write(UC_ARM_REG_SP, 0x03007E00)
        mu.reg_write(UC_ARM_REG_LR, BOOT_RET | 1)
        mu.reg_write(UC_ARM_REG_PC, BOOT_CHECK | 1)

    def on_boot_ret(self, mu, addr, size, _):
        if addr == BOOT_RET:
            self.boot_snapshot = None
            if mu.reg_read(UC_ARM_REG_R0) == 1:
                self.boots.append("usb")
                mu.reg_write(UC_ARM_REG_SP, 0x03007E00)
                mu.reg_write(UC_ARM_REG_LR, STOP | 1)
                mu.reg_write(UC_ARM_REG_PC, DISPATCHER | 1)
            else:
                self.boots.append("menu")
                mu.reg_write(UC_ARM_REG_PC, MENU_LOOP | 1)
        elif addr == MENU_LOOP:
            if self.done.is_set():
                mu.emu_stop()
            time.sleep(FRAME_S)

    def on_swi(self, mu, intno, _):
        if intno != 2:
            return self.fail(f"unexpected CPU exception {intno}")
        pc = mu.reg_read(UC_ARM_REG_PC)
        num = mu.mem_read(pc - 2, 1)[0]
        if num == 0x00:                                         # SoftReset
            ime = struct.unpack("<H", mu.mem_read(0x04000208, 2))[0]
            flag = mu.mem_read(0x03007FFA, 1)[0]
            if ime:
                return self.fail("SoftReset with interrupts still enabled")
            if flag != 0:
                return self.fail("SoftReset flag not 0: the BIOS would jump into RAM, not the DUO")
            if self.mapped is not None:
                return self.fail("SoftReset with the game mapped: the DUO program would not be found")
            self.restarts += 1
            mu.mem_write(0x03007E00, bytes(0x200))
            self.boot(mu)
        elif num == 0x26:                                       # HardReset: BIOS replays the logo check
            seen = bytes(mu.mem_read(ROM + 4, len(LOGO)))
            if seen != LOGO:
                return self.fail('HardReset: the BIOS logo check reads the masked header and stops at "GAME BOY"')
            self.restarts += 1
            self.boot(mu)
        else:
            self.fail(f"unexpected BIOS call SWI {num:#x}")

    def power_on(self):
        """The person switches the GBA on: the DUO starts and checks once for a waiting hello."""
        assert not self.powered
        self.powered = True
        self.thread = threading.Thread(target=self.run, args=(True,), daemon=True)
        self.thread.start()
        return self

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
            if self.boot_snapshot is not None:          # start-up check: only what was there at the reset
                self.ret(1 if self.boot_snapshot else 0)
            else:
                self.ret(1 if self.visible() else 0)
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
            if self.h2d_times:
                wait = self.h2d_times.pop(0) - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
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
        if size != 1 and self.mapped is not None:
            self.bad.append(f"{8 * size}-bit read of the save area at {addr:#x}: the GBA's save bus is 8 bits wide, "
                            "so the real chip would return one byte repeated (found on real hardware, lab session 5)")
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
    def run(self, from_power_on=False):
        mu = self.mu
        mu.reg_write(UC_ARM_REG_SP, 0x03007E00)
        if from_power_on:
            self.boot_snapshot = self.visible()
            mu.reg_write(UC_ARM_REG_LR, BOOT_RET | 1)
            entry = BOOT_CHECK
        else:
            mu.reg_write(UC_ARM_REG_LR, STOP | 1)
            entry = DISPATCHER
        try:
            self.pending_view = False
            mu.emu_start(entry | 1, STOP)
            while self.pending_view and not self.bad:
                self.pending_view = False
                self.show_game()
                mu.emu_start(mu.reg_read(UC_ARM_REG_PC) | 1, STOP)
        except UcError as e:
            self.bad.append(f"CPU fault: {e} at {mu.reg_read(UC_ARM_REG_PC):#x}")
        self.d2h.put(None)

    def start(self):
        if not self.powered:
            return self                      # GBA off: only the USB chip is alive
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.done.set()
        if not hasattr(self, "thread"):
            return
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
        if hw.usb_latency:
            hw.h2d_times.append(time.perf_counter() + hw.usb_latency)
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
