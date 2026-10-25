"""
Reckless edition v3.1 = lab edition + fixes and speed-ups learned on real hardware.

v3.1 fixes v3, which sent packets back to back. The DUO's link between the GBA
and its USB chip is one shared, handshaken byte channel, and every transfer in
Datel's own firmware strictly alternates (PC packet, GBA packet, PC packet...).
v3's uploads stalled on real hardware for exactly that reason. v3.1 keeps the
compression and block CRCs but answers / waits for one packet at a time.

Changes vs the lab edition (all code lives in Datel's empty padding area;
only command-table slots, one call and the v2 routine are touched):

  * 0x03 GBA save: a 2-byte ROM read wakes the save chip after the swap-in,
    and bank 0 is read without any bank-switch command first.
  * 0x02 hello: Datel's reply, plus a "PC connected" log line.
  * 0x07 LAB_EXEC_STREAM: log PC-supplied messages, run the staged program,
    stream its output as RLE-compressed 4 KB blocks with CRCs. The PC answers
    every packet: CONT to carry on, then ACK (or NAK = send again) per block.
  * 0x09 INFO: reply 'RK31'.
  * 0x1E LAB_UPLOAD n: receive n bytes at the upload cursor (bounds-checked
    against the staging window), answering each packet; the last packet is
    answered with the CRC-32 of everything received.
  * Text: "RECKLESS V3.1 - READY" in USB mode, "V1.06 RM31" in System Test.
"""
import struct, zlib
import build_patch as v1, build_patch2 as v2, build_reckless as rk, build_lab as lab
import thumbcheck
from build_patch import BASE, PAD_END, JUMPTABLE, assemble

# ---------------------------------------------------------------- Datel API
SEND8, RECV8, AVAIL, IDLE = 0x0800C29C, 0x0800C1FC, 0x0800C1DC, 0x080006A8
CRC32, LOGS, LOG1 = 0x080065D0, 0x080066E0, 0x08006714
DATEL_HELLO, LOOP = 0x080068E4, 0x08007454
BUFFER = 0x02010030

# ---------------------------------------------------------------- our layout
STAGE_LO, STAGE_HI, CURSOR = lab.STAGE_LO, lab.STAGE_HI, lab.CURSOR
TEMP = 0x02039000                 # 4 KB compression buffer (after a program has run)
MSG_AT = 0x0203AE00               # PC-supplied log messages (256 bytes)
VARS = 0x0203AF00                 # stream state
ESC = 0xE7                        # RLE escape byte
TIMEOUT_FRAMES = 900              # ~15 s at 60 frames/s
RKL3 = 0x334C4B52                 # v3 (back-to-back packets - did not work on real hardware)
RK31 = 0x31334B52                 # v3.1: strictly alternating packets, like all of Datel's own transfers

CMD_EXEC_STREAM, CMD_RESEND, CMD_INFO, CMD_PACE, CMD_UPLOAD = 0x07, 0x08, 0x09, 0x1D, 0x1E
UP_ACK = 0x1F                     # reply to each upload packet but the last
ST_ACK, ST_PACE, ST_CONT = 0x0E, 0x0F, 0x10   # in-stream only; ignored silently if they arrive late

AT = {  # fixed addresses for each piece (Thumb-1, in padding)
    "send_sbw": 0x0801F000, "wait_pkt": 0x0801F040, "rle": 0x0801F0C0,
    "send_block": 0x0801F180, "stream": 0x0801F240, "log_msgs": 0x0801F320,
    "s_hello": 0x0801F3A0, "s_exec": 0x0801F3C0,
    "s_info": 0x0801F480, "s_upload": 0x0801F4C0,
}
STR_AT = 0x0801F800

STRINGS = {
    "hello": "PC connected: RECKLESS V3.1",
    "run": "LAB: running script",
    "rejected": "LAB: script rejected",
    "ready": "LAB: %d bytes to send",
    "stream": "Sending %d bytes",
    "progress": "Sent %d KB",
    "resend": "Resending block %d",
    "done": "Sent OK, CRC %X",
    "timeout": "PC stopped answering",
    "uploaded": "LAB: received %d bytes",
    "up_timeout": "LAB: upload timed out",
    "up_bad": "LAB: bad upload request",
}


def strings_blob():
    blob, addrs = b"", {}
    for k, s in STRINGS.items():
        assert len(s) <= 27, s
        addrs[k] = STR_AT + len(blob)
        blob += s.encode() + b"\0"
        blob += b"\0" * (-len(blob) % 4)
    return blob, addrs


def sources(S):
    a = AT
    lits_common = f"""
lit_vars:   .word {VARS:#x}
"""
    src = {}
    src["send_sbw"] = f"""send_sbw:
    push {{r4, lr}}
    ldr  r4, lit_vars
    movs r2, #0x53
    strb r2, [r4, #0x10]
    movs r2, #0x42
    strb r2, [r4, #0x11]
    movs r2, #0x57
    strb r2, [r4, #0x12]
    strb r0, [r4, #0x13]
    str  r1, [r4, #0x14]
    adds r0, r4, #0
    adds r0, #0x10
    bl   {SEND8:#x}
    pop  {{r4}}
    pop  {{r1}}
    bx   r1
    .align 2
{lits_common}"""

    src["wait_pkt"] = f"""wait_pkt:
    push {{r4, r5, r6, lr}}
    ldr  r4, lit_vars
    adds r5, r0, #0
    ldr  r6, lit_timeout
wp_loop:
    bl   {AVAIL:#x}
    cmp  r0, #0
    bne  wp_got
    bl   {IDLE:#x}
    subs r6, #1
    bne  wp_loop
    movs r0, #2
    b    wp_ret
wp_got:
    adds r0, r4, #0
    adds r0, #0x18
    bl   {RECV8:#x}
    ldrb r0, [r4, #0x18]
    cmp  r0, #0x43
    bne  wp_other
    ldrb r0, [r4, #0x19]
    cmp  r0, #0x42
    bne  wp_other
    ldrb r0, [r4, #0x1A]
    cmp  r0, #0x57
    bne  wp_other
    ldrb r0, [r4, #0x1B]
    ldr  r1, [r4, #0x1C]
    str  r1, [r4, #0x0C]
    cmp  r0, r5
    beq  wp_ok
wp_other:
    movs r0, #1                 @ anything unexpected: the caller answers by restarting the block
    b    wp_ret
wp_ok:
    movs r0, #0
wp_ret:
    pop  {{r4, r5, r6}}
    pop  {{r1}}
    bx   r1
    .align 2
lit_timeout: .word {TIMEOUT_FRAMES}
{lits_common}"""

    # rle(src r0, len r1) -> r0 = compressed length in TEMP, or 0 if not smaller
    src["rle"] = f"""rle:
    push {{r4, r5, r6, r7, lr}}
    adds r4, r0, #0
    adds r5, r0, r1
    ldr  r2, lit_temp
    adds r3, r2, r1
    subs r3, #1
rl_loop:
    cmp  r4, r5
    bhs  rl_done
    ldrb r6, [r4]
    movs r7, #1
rl_run:
    adds r0, r4, r7
    cmp  r0, r5
    bhs  rl_end
    ldrb r1, [r0]
    cmp  r1, r6
    bne  rl_end
    cmp  r7, #0xFF
    bhi  rl_end
    adds r7, #1
    b    rl_run
rl_end:
    cmp  r7, #4
    bhs  rl_emit
    cmp  r6, #{ESC:#x}
    beq  rl_emit
rl_lit:
    cmp  r2, r3
    bhs  rl_fail
    strb r6, [r2]
    adds r2, #1
    adds r4, #1
    subs r7, #1
    bne  rl_lit
    b    rl_loop
rl_emit:
    adds r0, r2, #3
    cmp  r0, r3
    bhi  rl_fail
    movs r0, #{ESC:#x}
    strb r0, [r2]
    subs r0, r7, #1
    strb r0, [r2, #1]
    strb r6, [r2, #2]
    adds r2, #3
    adds r4, r4, r7
    b    rl_loop
rl_done:
    ldr  r0, lit_temp
    subs r0, r2, r0
    b    rl_ret
rl_fail:
    movs r0, #0
rl_ret:
    pop  {{r4, r5, r6, r7}}
    pop  {{r1}}
    bx   r1
    .align 2
lit_temp: .word {TEMP:#x}
"""

    src["send_block"] = f"""send_block:
    push {{r4, r5, r6, r7, lr}}
    ldr  r4, lit_vars
sb_restart:
    adds r5, r0, #0
    lsls r6, r5, #12
    ldr  r0, [r4, #0]
    subs r7, r0, r6
    ldr  r0, lit_4096
    cmp  r7, r0
    bls  sb_len_ok
    adds r7, r0, #0
sb_len_ok:
    ldr  r0, lit_buffer
    adds r6, r0, r6
    adds r0, r6, #0
    adds r1, r7, #0
    bl   {CRC32:#x}
    str  r0, [r4, #0x24]
    strh r5, [r4, #0x20]
    adds r0, r6, #0
    adds r1, r7, #0
    bl   {a['rle']:#x}
    cmp  r0, #0
    beq  sb_raw
    strh r0, [r4, #0x22]
    adds r5, r0, #0
    ldr  r6, lit_temp
    b    sb_hdr
sb_raw:
    ldr  r0, lit_rawflag
    orrs r0, r7
    strh r0, [r4, #0x22]
    adds r5, r7, #0
sb_hdr:
    adds r0, r4, #0
    adds r0, #0x20
    bl   {SEND8:#x}
    adds r5, #7
    lsrs r5, r5, #3
sb_loop:
    cmp  r5, #0
    beq  sb_done
    movs r0, #{ST_CONT:#x}
    bl   {a['wait_pkt']:#x}
    cmp  r0, #0
    beq  sb_send
    cmp  r0, #1
    bne  sb_tmo
    ldrb r0, [r4, #0x1B]
    cmp  r0, #{ST_ACK:#x}
    bne  sb_again
    ldr  r0, [r4, #0x0C]
    ldrh r1, [r4, #0x20]
    cmp  r0, r1
    bne  sb_again
    movs r0, #3                 @ the PC already accepted this block
    b    sb_ret
sb_again:
    ldrh r0, [r4, #0x20]
    b    sb_restart
sb_send:
    adds r0, r6, #0
    bl   {SEND8:#x}
    adds r6, #8
    subs r5, #1
    b    sb_loop
sb_tmo:
    movs r0, #2
    b    sb_ret
sb_done:
    movs r0, #0
sb_ret:
    pop  {{r4, r5, r6, r7}}
    pop  {{r1}}
    bx   r1
    .align 2
lit_4096:    .word 4096
lit_buffer:  .word {BUFFER:#x}
lit_temp:    .word {TEMP:#x}
lit_rawflag: .word 0x8000
{lits_common}"""

    src["stream"] = f"""stream:
    push {{r4, r5, r6, r7, lr}}
    ldr  r4, lit_vars
    str  r0, [r4, #0]
    ldr  r1, lit_4095
    adds r1, r0, r1
    lsrs r1, r1, #12
    str  r1, [r4, #8]
    adds r1, r0, #0
    ldr  r0, lit_s_stream
    bl   {LOG1:#x}
    movs r0, #0x30
    ldr  r1, [r4, #0]
    bl   {a['send_sbw']:#x}
    movs r0, #{ST_CONT:#x}
    bl   {a['wait_pkt']:#x}
    cmp  r0, #0
    bne  st_timeout
    movs r5, #0
st_loop:
    ldr  r1, [r4, #8]
    cmp  r5, r1
    bhs  st_finish
    adds r0, r5, #0
    bl   {a['send_block']:#x}
    cmp  r0, #3
    beq  st_acked
    cmp  r0, #0
    bne  st_timeout
st_wait:
    movs r0, #{ST_ACK:#x}
    bl   {a['wait_pkt']:#x}
    cmp  r0, #2
    beq  st_timeout
    cmp  r0, #0
    bne  st_again
    ldr  r1, [r4, #0x0C]
    cmp  r1, r5
    beq  st_acked
st_again:
    adds r1, r5, #0
    ldr  r0, lit_s_resend
    bl   {LOG1:#x}
    adds r0, r5, #0
    bl   {a['send_block']:#x}
    cmp  r0, #3
    beq  st_acked
    cmp  r0, #0
    bne  st_timeout
    b    st_wait
st_acked:
    adds r5, #1
    lsls r0, r5, #30
    bne  st_loop
    lsls r1, r5, #2
    ldr  r0, lit_s_progress
    bl   {LOG1:#x}
    b    st_loop
st_finish:
    ldr  r0, lit_buffer
    ldr  r1, [r4, #0]
    bl   {CRC32:#x}
    adds r6, r0, #0
    adds r1, r0, #0
    movs r0, #0x32
    bl   {a['send_sbw']:#x}
    adds r1, r6, #0
    ldr  r0, lit_s_done
    bl   {LOG1:#x}
    movs r0, #0
    b    st_ret
st_timeout:
    ldr  r0, lit_s_timeout
    bl   {LOGS:#x}
    movs r0, #2
st_ret:
    pop  {{r4, r5, r6, r7}}
    pop  {{r1}}
    bx   r1
    .align 2
lit_4095:       .word 4095
lit_buffer:     .word {BUFFER:#x}
lit_s_stream:   .word {S['stream']:#x}
lit_s_resend:   .word {S['resend']:#x}
lit_s_progress: .word {S['progress']:#x}
lit_s_done:     .word {S['done']:#x}
lit_s_timeout:  .word {S['timeout']:#x}
{lits_common}"""

    src["log_msgs"] = f"""log_msgs:
    push {{r4, r5, r6, r7, lr}}
    ldr  r4, lit_msg
    movs r7, #3
lm_next:
    ldrb r0, [r4]
    cmp  r0, #0
    beq  lm_end
    ldr  r5, lit_scratch
    movs r6, #0
lm_copy:
    ldr  r1, lit_msgend
    cmp  r4, r1
    bhs  lm_end
    ldrb r0, [r4]
    cmp  r0, #0
    beq  lm_term
    adds r4, #1
    cmp  r6, #26
    bhs  lm_copy
    cmp  r0, #0x20
    blo  lm_bad
    cmp  r0, #0x7E
    bls  lm_store
lm_bad:
    movs r0, #0x3F
lm_store:
    strb r0, [r5, r6]
    adds r6, #1
    b    lm_copy
lm_term:
    movs r0, #0
    strb r0, [r5, r6]
    adds r4, #1
    adds r0, r5, #0
    bl   {LOGS:#x}
    subs r7, #1
    bne  lm_next
lm_end:
    ldr  r4, lit_msg
    movs r0, #0
    strb r0, [r4]
    pop  {{r4, r5, r6, r7}}
    pop  {{r1}}
    bx   r1
    .align 2
lit_msg:     .word {MSG_AT:#x}
lit_msgend:  .word {MSG_AT + 0xFF:#x}
lit_scratch: .word {VARS + 0x40:#x}
"""

    src["s_hello"] = f"""s_hello:
    bl   {DATEL_HELLO:#x}
    ldr  r0, lit_s_hello
    bl   {LOGS:#x}
    ldr  r0, lit_loop
    bx   r0
    .align 2
lit_s_hello: .word {S['hello']:#x}
lit_loop:    .word {LOOP | 1:#x}
"""

    src["s_exec"] = f"""s_exec:
    bl   {a['log_msgs']:#x}
    ldr  r0, lit_s_run
    bl   {LOGS:#x}
    lsls r0, r5, #2
    lsrs r0, r0, #2
    ldr  r1, lit_execbits
    orrs r0, r1
    bl   {lab.LAB_PREP_AT:#x}
    cmp  r0, #0
    ble  se_bad
    push {{r0}}
    adds r1, r0, #0
    ldr  r0, lit_s_ready
    bl   {LOG1:#x}
    pop  {{r0}}
    bl   {a['stream']:#x}
    b    se_back
se_bad:
    ldr  r0, lit_s_rejected
    bl   {LOGS:#x}
    movs r0, #0x15
    movs r1, #0
    bl   {a['send_sbw']:#x}
se_back:
    ldr  r0, lit_loop
    bx   r0
    .align 2
lit_execbits:   .word 0xC0000000
lit_s_run:      .word {S['run']:#x}
lit_s_ready:    .word {S['ready']:#x}
lit_s_rejected: .word {S['rejected']:#x}
lit_loop:       .word {LOOP | 1:#x}
"""

    src["s_info"] = f"""s_info:
    movs r0, #{CMD_INFO:#x}
    ldr  r1, lit_magic
    bl   {a['send_sbw']:#x}
    ldr  r0, lit_loop
    bx   r0
    .align 2
lit_magic: .word {RK31:#x}
lit_loop:  .word {LOOP | 1:#x}
"""

    src["s_upload"] = f"""s_upload:
    push {{r4, r6, r7}}
    ldr  r7, lit_cursor
    ldr  r6, [r7]
    cmp  r5, #0
    beq  up_bad
    ldr  r0, lit_lo
    cmp  r6, r0
    blo  up_bad
    adds r1, r5, #7
    lsrs r1, r1, #3
    lsls r1, r1, #3
    adds r1, r6, r1
    ldr  r0, lit_hi
    cmp  r1, r0
    bhi  up_bad
    cmp  r1, r6
    bls  up_bad
    movs r0, #{CMD_UPLOAD:#x}
    adds r1, r5, #0
    bl   {a['send_sbw']:#x}
    adds r4, r6, #0
    adds r1, r5, #7
    lsrs r1, r1, #3
    push {{r1}}
up_pkt:
    pop  {{r1}}
    cmp  r1, #0
    beq  up_done
    subs r1, #1
    push {{r1}}
    ldr  r7, lit_timeout
up_wait:
    bl   {AVAIL:#x}
    cmp  r0, #0
    bne  up_recv
    bl   {IDLE:#x}
    subs r7, #1
    bne  up_wait
    add  sp, #4
    ldr  r0, lit_s_uptimeout
    bl   {LOGS:#x}
    b    up_back                 @ no reply: our last packet already answered the PC's
up_recv:
    adds r0, r4, #0
    bl   {RECV8:#x}
    adds r4, #8
    ldr  r1, [sp]
    cmp  r1, #0
    beq  up_pkt
    movs r0, #{UP_ACK:#x}
    subs r1, r4, r6
    bl   {a['send_sbw']:#x}
    b    up_pkt
up_done:
    ldr  r7, lit_cursor
    str  r4, [r7]
    adds r0, r6, #0
    adds r1, r5, #0
    bl   {CRC32:#x}
    adds r1, r0, #0
    movs r0, #{CMD_UPLOAD:#x}
    bl   {a['send_sbw']:#x}
    adds r1, r5, #0
    ldr  r0, lit_s_uploaded
    bl   {LOG1:#x}
    b    up_back
up_bad:
    ldr  r0, lit_s_upbad
    bl   {LOGS:#x}
    movs r0, #0x3F
    ldr  r1, lit_bad
    bl   {a['send_sbw']:#x}
up_back:
    pop  {{r4, r6, r7}}
    ldr  r0, lit_loop
    bx   r0
    .align 2
lit_cursor:      .word {CURSOR:#x}
lit_lo:          .word {STAGE_LO:#x}
lit_hi:          .word {STAGE_HI:#x}
lit_timeout:     .word {TIMEOUT_FRAMES}
lit_bad:         .word 0xBAD
lit_s_uptimeout: .word {S['up_timeout']:#x}
lit_s_uploaded:  .word {S['uploaded']:#x}
lit_s_upbad:     .word {S['up_bad']:#x}
lit_loop:        .word {LOOP | 1:#x}
"""
    return src


def patch_v2_routine():
    """Wake-up read after the swap-in; switch banks only from bank 1 onwards."""
    s = v2.ASM_RAM2
    old = "    ldr  r4, lit_map\n    strh r3, [r4]\n    ldr  r2, lit_sram\n"
    assert s.count(old) == 1
    s = s.replace(old, "    ldr  r4, lit_map\n    strh r3, [r4]\n    ldr  r5, lit_rom\n    ldrh r5, [r5]\n    ldr  r2, lit_sram\n")
    old = "bank_loop:\n    cmp  r1, #1\n    ble  no_switch\n"
    assert s.count(old) == 1
    s = s.replace(old, "bank_loop:\n    cmp  r6, #0\n    beq  no_switch\n")
    s = s.replace("ramfn_end:", "lit_rom:      .word 0x08000000\nramfn_end:")
    v2.ASM_RAM2 = s


def build(out_fw="fw_v3.bin"):
    patch_v2_routine()
    rk.TEXT[36] = ("THE HOST PC IS IN CONTROL", "RECKLESS V3.1 - READY")
    rk.VERSION = "V1.06 RM31"
    base, _ = v2.build("fw.bin", "fw_v3_stage1.bin")
    base, _, _ = rk.build(base, "fw_v3_stage2.bin")
    base, _, _ = lab.build(base, "fw_v3_stage3.bin")
    fw = bytearray(base)

    blob, S = strings_blob()
    o = STR_AT - BASE
    assert all(x == 0xFF for x in fw[o:o + len(blob)]) and STR_AT + len(blob) < PAD_END
    fw[o:o + len(blob)] = blob

    src = sources(S)
    order = sorted(AT, key=AT.get)
    for i, name in enumerate(order):
        code = thumbcheck.checked_assemble(assemble, src[name], AT[name], name)
        end = AT[order[i + 1]] if i + 1 < len(order) else STR_AT
        assert AT[name] + len(code) <= end, f"{name} overflows into {order[i+1] if i+1 < len(order) else 'strings'}"
        o = AT[name] - BASE
        assert all(x == 0xFF for x in fw[o:o + len(code)]), f"padding not empty at {AT[name]:#x}"
        fw[o:o + len(code)] = code

    # v3.1: every command slot we add answers exactly once; stray stream packets
    # (ACK/CONT) reaching the dispatcher get Datel's ordinary 'unknown' reply,
    # which keeps the link strictly alternating.
    table = {0x02: ("s_hello", 0x08007540), CMD_EXEC_STREAM: ("s_exec", 0x08007572),
             CMD_INFO: ("s_info", 0x08007572), CMD_UPLOAD: ("s_upload", 0x08007572)}
    for cmd, (name, expect) in table.items():
        ent = JUMPTABLE + (cmd - 2) * 4 - BASE
        assert struct.unpack_from("<I", fw, ent)[0] == expect, f"slot {cmd:#x} not as expected"
        struct.pack_into("<I", fw, ent, AT[name])

    fw = bytes(fw)
    open(out_fw, "wb").write(fw)
    return fw


if __name__ == "__main__":
    fw = build()
    crc = zlib.crc32(fw)
    open("duo_1.06_reckless_v3.1.bin", "wb").write(struct.pack("<I", crc) + fw)
    labfw = open("fw_lab.bin", "rb").read()
    diff = [i for i in range(len(fw)) if fw[i] != labfw[i]]
    outside = sorted({hex(BASE + i & ~3) for i in diff if not (0x1E7C2 <= i < 0x1FD90)})
    print(f"v3.1 built: update CRC {crc:08X}; {len(diff)} bytes differ from the lab edition")
    print("changes outside the padding area (should be command-table slots only):", outside)
