"""
Build a patched DUO firmware 1.06 that adds one read-only USB command:

  CBW 0x03, arg = number of 64 KB banks (1 or 2)
      -> streams the GBA cartridge's save area (0x0E000000) using the
         firmware's own proven send routine (size, CRC-32, 8-byte packets).

Only two things change in Datel's image:
  1. command table entry for 0x03 (previously "unknown command")
  2. the BL in SendFile's type-3 stub now goes through a small wrapper that
     calls Datel's original page-read routine unless bit 31 of the argument
     is set - so command 0x24 behaves exactly as before.
All new code lives in the erased padding area at 0x0801E800.
"""
import struct, sys, zlib
from keystone import Ks, KS_ARCH_ARM, KS_MODE_THUMB

BASE = 0x08000000
CODE_AT = 0x0801E800
PAD_START, PAD_END = 0x0801E7C2, 0x0801FD90

JUMPTABLE = 0x080074A8          # entries for commands 2..0x27
CMD = 0x03
STUB_BL = 0x080073A2            # "bl 0x0800710c" inside SendFile type-3 stub
ORIG_PAGE_READ = 0x0800710C
SENDFILE = 0x08007188
MEMCPY = 0x08000B60
RESTORE_FLASH_FNS = 0x0800B9E4  # Datel's own: recopies flash routines to IWRAM
LOOP = 0x08007454               # dispatcher loop head
BUFFER = 0x02010030             # SendFile's data buffer (holds 0x20000)

STUB_AT, PREP_AT, RAM_AT = 0x0801E800, 0x0801E840, 0x0801E900

ASM_STUB = f"""
cmd3_stub:
    movs r0, #3
    adds r1, r5, #0
    movs r2, #1
    lsls r2, r2, #31
    orrs r1, r2
    bl   {SENDFILE:#x}
    ldr  r0, lit_loop
    bx   r0
    .align 2
lit_loop:   .word {LOOP | 1:#x}

"""

ASM_PREP = f"""prepare:
    cmp  r0, #0
    blt  gba
    ldr  r1, lit_orig
    bx   r1
gba:
    push {{r4, lr}}
    lsls r4, r0, #24
    lsrs r4, r4, #24
    cmp  r4, #0
    beq  bad
    cmp  r4, #2
    bhi  bad
    movs r0, #3
    lsls r0, r0, #24
    ldr  r1, lit_ram
    ldr  r2, lit_ramlen
    bl   {MEMCPY:#x}
    ldr  r0, lit_buf
    adds r1, r4, #0
    ldr  r3, lit_entry
    bl   call_r3
    movs r0, #3
    lsls r0, r0, #24
    bl   {RESTORE_FLASH_FNS:#x}
    lsls r0, r4, #16
    pop  {{r4}}
    pop  {{r1}}
    bx   r1
bad:
    movs r0, #1
    rsbs r0, r0, #0
    pop  {{r4}}
    pop  {{r1}}
    bx   r1
call_r3:
    bx   r3
    .align 2
lit_orig:   .word {ORIG_PAGE_READ | 1:#x}
lit_ram:    .word RAMFN_ADDR
lit_ramlen: .word RAMFN_LEN
lit_buf:    .word {BUFFER:#x}
lit_entry:  .word 0x03000001

"""

ASM_RAM = f"""ramfn:
    push {{r4, r5, r6, r7, lr}}
    ldr  r4, lit_ime
    ldrh r7, [r4]
    push {{r7}}
    movs r5, #0
    strh r5, [r4]
    ldr  r4, lit_waitcnt
    ldrh r7, [r4]
    push {{r7}}
    movs r5, #3
    orrs r5, r7
    strh r5, [r4]
    ldr  r4, lit_map
    ldr  r5, lit_game
    strh r5, [r4]
    ldr  r2, lit_sram
    movs r6, #0
bank_loop:
    cmp  r1, #1
    ble  no_switch
    ldr  r3, lit_5555
    movs r5, #0xAA
    strb r5, [r3]
    ldr  r3, lit_2aaa
    movs r5, #0x55
    strb r5, [r3]
    ldr  r3, lit_5555
    movs r5, #0xB0
    strb r5, [r3]
    strb r6, [r2]
no_switch:
    movs r3, #0
    movs r7, #1
    lsls r7, r7, #16
copy:
    ldrb r5, [r2, r3]
    strb r5, [r0]
    adds r0, #1
    adds r3, #1
    cmp  r3, r7
    blt  copy
    adds r6, #1
    cmp  r6, r1
    blt  bank_loop
    cmp  r1, #1
    ble  restore
    ldr  r3, lit_5555
    movs r5, #0xAA
    strb r5, [r3]
    ldr  r3, lit_2aaa
    movs r5, #0x55
    strb r5, [r3]
    ldr  r3, lit_5555
    movs r5, #0xB0
    strb r5, [r3]
    movs r5, #0
    strb r5, [r2]
restore:
    ldr  r4, lit_map
    ldr  r5, lit_duo
    strh r5, [r4]
    ldr  r4, lit_waitcnt
    pop  {{r7}}
    strh r7, [r4]
    ldr  r4, lit_ime
    pop  {{r7}}
    strh r7, [r4]
    pop  {{r4, r5, r6, r7}}
    pop  {{r0}}
    bx   r0
    .align 2
lit_ime:     .word 0x04000208
lit_waitcnt: .word 0x04000204
lit_map:     .word 0x0800009C
lit_game:    .word 0x00000101
lit_duo:     .word 0x00000120
lit_sram:    .word 0x0E000000
lit_5555:    .word 0x0E005555
lit_2aaa:    .word 0x0E002AAA
ramfn_end:
"""


def assemble(src, addr):
    ks = Ks(KS_ARCH_ARM, KS_MODE_THUMB)
    enc, _ = ks.asm(src, addr)
    return bytes(enc)


def bl(frm, to):
    off = (to - (frm + 4)) >> 1
    hi = 0xF000 | ((off >> 11) & 0x7FF)
    lo = 0xF800 | (off & 0x7FF)
    return struct.pack("<HH", hi, lo)


def build(fw_path, out_path):
    fw = bytearray(open(fw_path, "rb").read())
    ram = assemble(ASM_RAM, RAM_AT)
    prep = assemble(ASM_PREP.replace("RAMFN_ADDR", hex(RAM_AT)).replace("RAMFN_LEN", hex(len(ram))), PREP_AT)
    stub = assemble(ASM_STUB, STUB_AT)
    assert len(stub) <= PREP_AT - STUB_AT and len(prep) <= RAM_AT - PREP_AT
    assert RAM_AT + len(ram) < PAD_END and STUB_AT > PAD_START
    for at, blob in ((STUB_AT, stub), (PREP_AT, prep), (RAM_AT, ram)):
        o = at - BASE
        assert all(x == 0xFF for x in fw[o:o + len(blob)]), "padding not empty"
        fw[o:o + len(blob)] = blob

    ent = JUMPTABLE + (CMD - 2) * 4 - BASE
    assert struct.unpack_from("<I", fw, ent)[0] == 0x08007572, "unexpected jump table"
    struct.pack_into("<I", fw, ent, STUB_AT)

    b = STUB_BL - BASE
    assert fw[b:b + 4] == bl(STUB_BL, ORIG_PAGE_READ), "unexpected stub"
    fw[b:b + 4] = bl(STUB_BL, PREP_AT)

    open(out_path, "wb").write(fw)
    syms = {"cmd3_stub": STUB_AT, "prepare": PREP_AT, "ramfn": RAM_AT, "ramfn_end": RAM_AT + len(ram)}
    return fw, stub + prep + ram, syms


def update_file(fw, path):
    """Datel flash-update format: CRC-32 of payload, then payload."""
    open(path, "wb").write(struct.pack("<I", zlib.crc32(fw)) + fw)


if __name__ == "__main__":
    fw, code, syms = build("fw.bin", "fw_patched.bin")
    update_file(bytes(fw), "duo_1.06_gbasave.bin")
    print(f"patch: {len(code)} bytes at {CODE_AT:#x}")
    for k in ("cmd3_stub", "prepare", "ramfn", "ramfn_end"):
        print(f"  {k:10} {syms[k]:#x}")
    orig = open("fw.bin", "rb").read()
    diffs = [i for i in range(len(orig)) if orig[i] != fw[i]]
    regions = []
    for i in diffs:
        if regions and i == regions[-1][1] + 1:
            regions[-1][1] = i
        else:
            regions.append([i, i])
    print("changed regions:", [(hex(BASE + a), b - a + 1) for a, b in regions])
