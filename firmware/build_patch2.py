"""
Version 2 of the GBA-save patch.

Changes from version 1:
  * The cartridge mapping value is chosen by the PC (bits 8..20 of the
    argument). Default 0x0007 - the value Datel's own launcher uses while a
    game runs (and saves) with codes on. Version 1 used 0x0101, Datel's
    "peek at the game's header" value, which turned out not to connect the
    save chip.
  * Safety guard: after switching back, the routine checks the DUO's own
    program is visible again. If not, it waits in RAM (switch off to recover)
    instead of returning into whatever code is mapped.
Same two hook points as version 1; everything else is Datel's 1.06.
"""
import struct, zlib
import build_patch as v1
from build_patch import (BASE, PAD_START, PAD_END, JUMPTABLE, CMD, STUB_BL,
                         ORIG_PAGE_READ, MEMCPY, RESTORE_FLASH_FNS, BUFFER,
                         STUB_AT, PREP_AT, RAM_AT, ASM_STUB, assemble, bl)

DEFAULT_MAP = 0x0007
DUO_MAP = 0x0120

ASM_PREP2 = f"""prepare:
    cmp  r0, #0
    blt  gba
    ldr  r1, lit_orig
    bx   r1
gba:
    push {{r4, r5, lr}}
    lsls r4, r0, #24
    lsrs r4, r4, #24
    cmp  r4, #0
    beq  bad
    cmp  r4, #2
    bhi  bad
    lsls r5, r0, #11
    lsrs r5, r5, #19
    bne  have_map
    movs r5, #{DEFAULT_MAP}
have_map:
    movs r0, #3
    lsls r0, r0, #24
    ldr  r1, lit_ram
    ldr  r2, lit_ramlen
    bl   {MEMCPY:#x}
    ldr  r0, lit_buf
    adds r1, r4, #0
    adds r3, r5, #0
    ldr  r2, lit_entry
    bl   call_r2
    movs r0, #3
    lsls r0, r0, #24
    bl   {RESTORE_FLASH_FNS:#x}
    lsls r0, r4, #16
    pop  {{r4, r5}}
    pop  {{r1}}
    bx   r1
bad:
    movs r0, #1
    rsbs r0, r0, #0
    pop  {{r4, r5}}
    pop  {{r1}}
    bx   r1
call_r2:
    bx   r2
    .align 2
lit_orig:   .word {ORIG_PAGE_READ | 1:#x}
lit_ram:    .word RAMFN_ADDR
lit_ramlen: .word RAMFN_LEN
lit_buf:    .word {BUFFER:#x}
lit_entry:  .word 0x03000001
"""

ASM_RAM2 = f"""ramfn:
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
    strh r3, [r4]
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
    ldr  r4, lit_probe_at
    ldr  r5, [r4]
    ldr  r4, lit_probe
    cmp  r4, r5
    bne  hang
    ldr  r4, lit_waitcnt
    pop  {{r7}}
    strh r7, [r4]
    ldr  r4, lit_ime
    pop  {{r7}}
    strh r7, [r4]
    pop  {{r4, r5, r6, r7}}
    pop  {{r0}}
    bx   r0
hang:
    b    hang
    .align 2
lit_ime:      .word 0x04000208
lit_waitcnt:  .word 0x04000204
lit_map:      .word 0x0800009C
lit_duo:      .word {DUO_MAP:#x}
lit_sram:     .word 0x0E000000
lit_5555:     .word 0x0E005555
lit_2aaa:     .word 0x0E002AAA
lit_probe_at: .word {STUB_AT:#x}
lit_probe:    .word PROBE
ramfn_end:
"""


def build(fw_path, out_path):
    fw = bytearray(open(fw_path, "rb").read())
    stub = assemble(ASM_STUB, STUB_AT)
    probe = struct.unpack_from("<I", stub)[0]
    ram = assemble(ASM_RAM2.replace("PROBE", hex(probe)), RAM_AT)
    prep = assemble(ASM_PREP2.replace("RAMFN_ADDR", hex(RAM_AT)).replace("RAMFN_LEN", hex(len(ram))), PREP_AT)
    assert len(stub) <= PREP_AT - STUB_AT and len(prep) <= RAM_AT - PREP_AT
    assert RAM_AT + len(ram) < PAD_END and STUB_AT > PAD_START
    for at, blob in ((STUB_AT, stub), (PREP_AT, prep), (RAM_AT, ram)):
        o = at - BASE
        assert all(x == 0xFF for x in fw[o:o + len(blob)]), "padding not empty"
        fw[o:o + len(blob)] = blob
    ent = JUMPTABLE + (CMD - 2) * 4 - BASE
    assert struct.unpack_from("<I", fw, ent)[0] == 0x08007572
    struct.pack_into("<I", fw, ent, STUB_AT)
    b = STUB_BL - BASE
    assert fw[b:b + 4] == bl(STUB_BL, ORIG_PAGE_READ)
    fw[b:b + 4] = bl(STUB_BL, PREP_AT)
    open(out_path, "wb").write(fw)
    return bytes(fw), len(stub) + len(prep) + len(ram)


if __name__ == "__main__":
    fw, n = build("fw.bin", "fw_patched2.bin")
    open("duo_1.06_gbasave2.bin", "wb").write(struct.pack("<I", zlib.crc32(fw)) + fw)
    print(f"v2 patch: {n} bytes, update CRC {zlib.crc32(fw):08X}")
    orig = open("fw.bin", "rb").read()
    diff = [i for i in range(len(orig)) if orig[i] != fw[i]]
    print("changed bytes:", len(diff), "outside padding:",
          [hex(BASE + i) for i in diff if not (PAD_START <= BASE + i < PAD_END)])
