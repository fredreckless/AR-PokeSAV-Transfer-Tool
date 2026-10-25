"""Emulation tests for the lab edition (ARM926 core: Thumb-1 only, like the GBA)."""
import random, struct, sys, threading, time, zlib
import paths
import emu_test as E, maxduo_saves as M
from unicorn.arm_const import UC_ARM_REG_PC

E.FW = open(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".bin") else paths.build_file("fw_lab.bin"), "rb").read()
GAME_HEADER = b"POKEMON RUBYAXVP01"


class LabSim(E.Sim):
    def __init__(self, save):
        super().__init__(save)
        self.id_mode = False
        self.save_writes = []

    def on_rom_write(self, mu, access, addr, size, value, _):
        r = super().on_rom_write(mu, access, addr, size, value, _)
        if addr == 0x0800009C and self.mapped_game:
            mu.mem_write(0x080000A0, GAME_HEADER)          # the game's own header appears
        return r

    def on_save_read(self, mu, access, addr, size, value, _):
        if not self.mapped_game:
            self.unmapped_reads = getattr(self, "unmapped_reads", 0) + 1   # harmless open-bus read
            return
        return super().on_save_read(mu, access, addr, size, value, _)

    def on_save_write(self, mu, access, addr, size, value, _):
        off, v = addr - 0x0E000000, value & 0xFF
        self.save_writes.append((off, v))
        if not self.mapped_game:
            self.fail(f"save write at {addr:#x} while DUO mapped"); return False
        if v in (0x80, 0xA0):
            self.fail(f"FORBIDDEN value {v:#x} reached the save chip"); return False
        seq = self.cmd + [(off, v)]
        unlock = [(0x5555, 0xAA), (0x2AAA, 0x55)]
        if len(seq) <= 2 and seq == unlock[:len(seq)]:
            self.cmd = seq; return True
        if len(seq) == 3 and seq[:2] == unlock and off == 0x5555:
            self.cmd = []
            if v == 0x90:
                self.id_mode = True; mu.mem_write(0x0E000000, bytes([0xC2, 0x09]))
            elif v == 0xF0:
                self.id_mode = False; self.load_bank()
            elif v == 0xB0:
                self.cmd = seq
            else:
                self.fail(f"unexpected save-chip command {v:#x}"); return False
            return True
        if len(seq) == 4 and seq[2] == (0x5555, 0xB0) and off == 0 and v in (0, 1):
            self.bank, self.cmd = v, []; self.load_bank(); return True
        self.fail(f"unexpected save-chip sequence {seq}"); return False


def scenario(name, fn, faulty=False):
    save = bytes(random.Random(3).getrandbits(8) for _ in range(0x20000))
    sim = LabSim(save); sim.ignore_restore = faulty
    t = threading.Thread(target=sim.run, daemon=True); t.start()
    duo = M.MaxDuo(E.EmuTransport(sim))
    try:
        res = fn(duo, sim, save)
    except Exception as e:
        res = f"{type(e).__name__}: {e}"
    if not faulty:
        sim.done.set(); t.join(10)
    ok = res is True and not sim.bad
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" -> {res} {sim.bad}"))
    return ok


def t_probe(duo, sim, save):
    res = M.Lab(duo).run(M.lab_probe_ops())
    data = [d for _, d in res]
    assert data[2] != GAME_HEADER[:16], "DUO header should not show the game"
    for i, m in enumerate((0x0101, 0x0007, 0x0001)):
        title, sv, ident, after = data[4 + 4 * i: 8 + 4 * i]
        assert title == GAME_HEADER[:16], (hex(m), title)
        assert sv == save[:16], (hex(m), sv.hex())
        assert ident == bytes([0xC2, 0x09]), ident.hex()
        assert after == save[:4]
    assert not sim.mapped_game and sim.map_writes[-1] == 0x0120
    assert bytes(sim.mu.mem_read(0x03000000, 0x178)) == sim.boot_iwram, "flash routines not restored"
    return True


def t_peek_duo(duo, sim, save):
    res = M.Lab(duo).run([("r8", 0x0801E800, 32)])
    assert res[0][1] == E.FW[0x1E800:0x1E820]
    return True


def t_program_left_mapped(duo, sim, save):
    M.Lab(duo).run([("w16", M.MAP_REG, 0x0007), ("r8", 0x0E000010, 8)])
    assert not sim.mapped_game and sim.map_writes == [0x0007, 0x0120], sim.map_writes
    assert duo.hello() == 0x104
    return True


def t_bounds(duo, sim, save):
    code, _ = duo.ask(M.LAB_SETADDR, 0x02000000)
    assert code == 0x3F, "SETADDR outside window accepted"
    code, _ = duo.ask(M.LAB_SETADDR, 0x02038002)
    assert code == 0x3F, "unaligned SETADDR accepted"
    code, v = duo.ask(M.LAB_SETADDR, 0x0203AFFC)
    assert (code, v) == (4, 0x0203AFFC)
    assert duo.ask(M.LAB_POKE, 0x11111111) == (5, 0x0203B000)
    code, _ = duo.ask(M.LAB_POKE, 0x22222222)
    assert code == 0x3F, "POKE past the window accepted"
    assert bytes(sim.mu.mem_read(0x0203B000, 4)) != struct.pack("<I", 0x22222222)
    return True


def t_bad_crc(duo, sim, save):
    lab = M.Lab(duo)
    body = M.LAB_INTERP + struct.pack("<III", 0, 0, 0)
    prog = struct.pack("<III", M.LAB_MAGIC, len(body), zlib.crc32(body) ^ 1) + body
    lab._poke(M.LAB_SETADDR, M.LAB_STAGE)
    for i in range(0, len(prog), 4):
        lab._poke(M.LAB_POKE, struct.unpack_from("<I", prog, i)[0])
    try:
        duo.download(M.LAB_EXEC, 0)
        return "corrupt program was executed"
    except M.DuoError:
        return True


def t_gba_side_refusal(duo, sim, save):
    # bypass the PC-side check to prove the GBA itself refuses
    real = M.lab_validate
    M.lab_validate = lambda ops: None
    try:
        for bad in ([("w8", 0x0E005555, 0xAA), ("w8", 0x0E002AAA, 0x55), ("w8", 0x0E005555, 0x80)],
                    [("w16", 0x08005554, 0x5555)],
                    [("w8", 0x0E001234, 0x00)]):
            try:
                M.Lab(duo).run([("w16", M.MAP_REG, 0x0007)] + bad)
                return f"script {bad} not refused"
            except M.DuoError as e:
                assert "ERR!" in str(e), e
    finally:
        M.lab_validate = real
    assert all(v not in (0x80, 0xA0) for _, v in sim.save_writes)
    assert not sim.mapped_game
    return True


def t_pc_side_refusal(duo, sim, save):
    for bad in ([("w8", 0x0E005555, 0xA0)], [("w16", M.MAP_REG, 0x0067)], [("w16", 0x02000000, 1)]):
        try:
            M.lab_validate(bad); return f"{bad} passed PC validation"
        except M.DuoError:
            pass
    return True


def t_scope(duo, sim, save):
    res = M.Lab(duo).run([("w16", M.MAP_REG, 0x0007), ("sample8", 0x0E000000, 50 | 1 << 16)])
    assert res[-1][1] == bytes([save[0]]) * 50
    return True


def t_guard(duo, sim, save):
    lab = M.Lab(duo)
    ops = [("w16", M.WAITCNT, 0x0003), ("w16", M.MAP_REG, 0x0007), ("r8", 0x0E000000, 4)]
    body = M.LAB_INTERP + b"".join(struct.pack("<III", M.LAB_OPS[o[0]], o[1], o[2]) for o in ops) + struct.pack("<III", 0, 0, 0)
    prog = struct.pack("<III", M.LAB_MAGIC, len(body), zlib.crc32(body)) + body
    lab._poke(M.LAB_SETADDR, M.LAB_STAGE)
    for i in range(0, len(prog), 4):
        lab._poke(M.LAB_POKE, struct.unpack_from("<I", prog, i)[0])
    sim.h2d.put(b"CBW\x06" + bytes(4))
    time.sleep(8)
    assert sim.d2h.empty(), "replied despite failed switch-back"
    sim.mu.emu_stop()
    return True


def t_save_still_works(duo, sim, save):
    assert duo.gba_save(2, 0x0007) == save
    return True


ok = all([
    scenario("probe: game header, save bytes and Flash ID under each mapping", t_probe),
    scenario("peek reads the DUO's own program", t_peek_duo),
    scenario("program that leaves the game mapped: DUO restored, still responsive", t_program_left_mapped),
    scenario("upload window bounds enforced by the firmware", t_bounds),
    scenario("corrupted program refused", t_bad_crc),
    scenario("GBA-side interpreter refuses erase/program values and other addresses", t_gba_side_refusal),
    scenario("PC-side validation refuses the same", t_pc_side_refusal),
    scenario("scope sampling", t_scope),
    scenario("guard: faulty switch-back -> waits in RAM", t_guard, faulty=True),
    scenario("GBA save read still works on the lab edition", t_save_still_works),
])
print("ALL PASSED" if ok else "SOME FAILED")
