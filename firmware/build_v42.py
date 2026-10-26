"""
Reckless edition v4.2 = v4.1 + "run a program from RAM".

v4.2 changes vs v4.1 (all new code in Datel's empty padding; one command slot):
  * 0x23 RUN: runs a program the PC has uploaded to the lab staging area with
    the existing SETADDR (0x04) + UPLOAD (0x1E) commands. The staged image is
        'RKR1' magic, code length, CRC-32 of the code, entry offset (bit 0 = Thumb),
        then the code (linked to run at STAGE_LO + 16).
    The firmware refuses it (reply 0x3F) unless the magic, length and CRC match.
    Otherwise it replies 0x23 'RUN!' - so it is the PC's turn again - shows
    "Running program" and calls the code with r0 = a table of Datel's own
    routines (USB send/receive/available, idle, screen log, packet reply, CRC).
    The program talks to the PC only by answering PC packets one at a time,
    which keeps the link strictly alternating. When it returns, the DUO shows
    "Program finished: <result>" and is back in USB SERVER MODE.
  * INFO replies 'RK42'. Datel's look, v4.1's restart and the lab are unchanged.

Programs are not arbitrary: the PC tool only runs the programs built from this
repository (firmware/programs.py), recognised by their CRC.
"""
import struct, zlib
import build_v4 as v4
import thumbcheck
from build_patch import BASE, PAD_END, JUMPTABLE, assemble

STAGE_LO, STAGE_HI = v4.STAGE_LO, v4.STAGE_HI
CMD_RUN = 0x23
RK42 = 0x32344B52
RUN_MAGIC = 0x31524B52              # 'RKR1'
RUN_ACK = 0x214E5552                # 'RUN!'
RUN_AT = 0x0801F640                 # in the 508 free bytes after v4.1's handlers
API_AT = 0x0801FC00                 # table of Datel routines handed to programs
STR42_AT = 0x0801FB00               # v4.2 strings
PROGRAM_AT = STAGE_LO + 16
MAX_PROGRAM = STAGE_HI - STAGE_LO - 16

STRINGS42 = {
    "run": "Running program",
    "finished": "Program finished: %X",
    "rejected": "Program rejected",
}

API = [v4.SEND8, v4.RECV8, v4.AVAIL, v4.IDLE, v4.LOGS, v4.LOG1, v4.AT["send_sbw"], v4.CRC32]
API_NAMES = ["send8", "recv8", "avail", "idle", "log", "log1", "reply", "crc32"]


def strings_blob():
    blob, addrs = b"", {}
    for k, s in STRINGS42.items():
        assert len(s) <= 27, s
        addrs[k] = STR42_AT + len(blob)
        blob += s.encode() + b"\0"
        blob += b"\0" * (-len(blob) % 4)
    return blob, addrs


def source(S):
    return f"""s_run:
    push {{r4, r6, r7}}
    ldr  r6, lit_stage
    ldr  r0, [r6]
    ldr  r1, lit_magic
    cmp  r0, r1
    bne  run_bad
    ldr  r4, [r6, #4]
    cmp  r4, #0
    beq  run_bad
    ldr  r1, lit_max
    cmp  r4, r1
    bhi  run_bad
    ldr  r1, [r6, #12]
    cmp  r1, r4
    bhs  run_bad
    adds r0, r6, #0
    adds r0, #16
    adds r1, r4, #0
    bl   {v4.CRC32:#x}
    ldr  r1, [r6, #8]
    cmp  r0, r1
    bne  run_bad
    movs r0, #{CMD_RUN:#x}
    ldr  r1, lit_ack
    bl   {v4.AT['send_sbw']:#x}
    ldr  r0, lit_s_run
    bl   {v4.LOGS:#x}
    ldr  r1, [r6, #12]
    adds r1, r1, r6
    adds r1, #16
    ldr  r0, lit_api
    bl   run_call
    adds r1, r0, #0
    ldr  r0, lit_s_fin
    bl   {v4.LOG1:#x}
    b    run_back
run_bad:
    ldr  r0, lit_s_bad
    bl   {v4.LOGS:#x}
    movs r0, #0x3F
    ldr  r1, lit_magic
    bl   {v4.AT['send_sbw']:#x}
run_back:
    pop  {{r4, r6, r7}}
    ldr  r0, lit_loop
    bx   r0
run_call:
    bx   r1
    .align 2
lit_stage:  .word {STAGE_LO:#x}
lit_magic:  .word {RUN_MAGIC:#x}
lit_max:    .word {MAX_PROGRAM:#x}
lit_ack:    .word {RUN_ACK:#x}
lit_api:    .word {API_AT:#x}
lit_s_run:  .word {S['run']:#x}
lit_s_fin:  .word {S['finished']:#x}
lit_s_bad:  .word {S['rejected']:#x}
lit_loop:   .word {v4.LOOP | 1:#x}
"""


def build(out_fw="fw_v42.bin"):
    fw = bytearray(v4.build("fw_v42_base.bin"))

    blob, S = strings_blob()
    code = thumbcheck.checked_assemble(assemble, source(S), RUN_AT, "s_run")
    api = b"".join(struct.pack("<I", a | 1) for a in API)
    for at, data in ((RUN_AT, code), (STR42_AT, blob), (API_AT, api)):
        o = at - BASE
        assert at + len(data) <= PAD_END and all(x == 0xFF for x in fw[o:o + len(data)]), f"no room at {at:#x}"
        fw[o:o + len(data)] = data
    assert RUN_AT + len(code) <= 0x0801F800, "s_run overflows into v4's strings"

    ent = JUMPTABLE + (CMD_RUN - 2) * 4 - BASE
    assert struct.unpack_from("<I", fw, ent)[0] == 0x08007572, "slot 0x23 not free"
    struct.pack_into("<I", fw, ent, RUN_AT | 1)

    # INFO: RK40 -> RK42 (the literal sits in s_info's pool)
    o = v4.AT["s_info"] - BASE
    i = fw.index(struct.pack("<I", v4.RK40), o, o + 0x40)
    struct.pack_into("<I", fw, i, RK42)

    fw = bytes(fw)
    open(out_fw, "wb").write(fw)
    return fw


if __name__ == "__main__":
    fw = build()
    print(f"v4.2 built: update CRC {zlib.crc32(fw):08X}")
