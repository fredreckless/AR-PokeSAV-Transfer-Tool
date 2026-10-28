"""
Programs the DUO's GBA can run from RAM (firmware v4.2, command 0x23 RUN).

Each program is Thumb-1 code linked at STAGE_LO + 16 and receives r0 = the
firmware's API table (see build_v42.API). It must talk to the PC only by
answering PC packets one at a time:
    PC 0x2A POLL (arg)  ->  program 0x2A <value>
    PC 0x2B STOP        ->  program 0x2B <value>, then the program returns
and the DUO goes back to USB SERVER MODE.

adapter_detect
    Answers the Wireless Adapter detection that Pokemon Emerald / FireRed /
    LeafGreen run on the title screen (librfu's Sio32ID exchange: the words
    spelling NINTENDO, then the adapter ID 0x8001). It is the adapter-side half
    of that exchange, ported from the library's own code (the library implements
    both roles). Link port: Normal 32-bit mode, external clock - the game is the
    clock master. Nothing is sent to the game beyond this exchange, and nothing
    on either GBA's cartridge is touched.
    POLL 0 -> status: bits 0-15 transfers, 16-19 matched pairs, 20 ID exchange
              complete, 21 game's final ID was 0x8001
    POLL 1 -> the last 32-bit word received from the game
"""
import os, re, shutil, struct, subprocess, tempfile, zlib
import build_v42 as V
import thumbcheck
from build_patch import assemble

API = {name: 4 * i for i, name in enumerate(V.API_NAMES)}
SIO = 0x04000120          # SIODATA32; SIOCNT = SIO + 8
RCNT = 0x04000134
RFU_ID = 0x8001

ADAPTER_DETECT = f"""start:
    push {{r4, r5, r6, r7, lr}}
    adds r7, r0, #0
    ldr  r6, lit_vars
    movs r0, #0
    movs r1, #0
zero:
    str  r0, [r6, r1]
    adds r1, #4
    cmp  r1, #32
    blo  zero
    ldr  r4, lit_sio
    ldr  r1, lit_rcnt
    strh r0, [r1]
    ldr  r0, lit_normal32
    strh r0, [r4, #8]
    movs r0, #0
    str  r0, [r4]
    ldrh r0, [r4, #8]
    movs r1, #0x80
    orrs r0, r1
    strh r0, [r4, #8]
loop:
    ldrh r0, [r4, #8]
    lsls r0, r0, #24
    bmi  no_xfer
    bl   xfer
no_xfer:
    ldr  r0, [r6, #12]
    cmp  r0, #0
    beq  check_pc
    ldr  r0, [r6, #24]
    cmp  r0, #0
    bne  check_pc
    movs r0, #1
    str  r0, [r6, #24]
    ldr  r0, lit_msg
    ldr  r3, [r7, #{API['log']}]
    bl   call_r3
check_pc:
    ldr  r3, [r7, #{API['avail']}]
    bl   call_r3
    cmp  r0, #0
    beq  loop
    ldr  r0, lit_pkt
    ldr  r3, [r7, #{API['recv8']}]
    bl   call_r3
    ldr  r0, lit_pkt
    ldrb r1, [r0, #3]
    cmp  r1, #0x2B
    beq  stop
    ldr  r2, [r0, #4]
    cmp  r2, #1
    beq  poll_word
    bl   status
    b    poll_reply
poll_word:
    ldr  r1, [r6, #20]
poll_reply:
    movs r0, #0x2A
    ldr  r3, [r7, #{API['reply']}]
    bl   call_r3
    b    loop
stop:
    bl   status
    adds r5, r1, #0
    movs r0, #0x2B
    ldr  r3, [r7, #{API['reply']}]
    bl   call_r3
    movs r0, #0
    strh r0, [r4, #8]
    adds r0, r5, #0
    pop  {{r4, r5, r6, r7}}
    pop  {{r1}}
    bx   r1

status:
    ldr  r1, [r6, #16]
    lsls r1, r1, #16
    lsrs r1, r1, #16
    ldr  r2, [r6, #0]
    lsls r2, r2, #16
    orrs r1, r2
    ldr  r2, [r6, #12]
    cmp  r2, #0
    beq  st_done
    movs r3, #1
    lsls r3, r3, #20
    orrs r1, r3
    ldr  r3, lit_rfuid
    cmp  r2, r3
    bne  st_done
    movs r3, #1
    lsls r3, r3, #21
    orrs r1, r3
st_done:
    bx   lr

xfer:
    push {{r5, lr}}
    ldr  r0, [r4]
    str  r0, [r6, #20]
    ldr  r1, [r6, #16]
    adds r1, #1
    str  r1, [r6, #16]
    lsrs r1, r0, #16
    lsls r2, r0, #16
    lsrs r2, r2, #16
    ldr  r3, [r6, #12]
    cmp  r3, #0
    bne  after
    ldr  r3, [r6, #8]
    cmp  r1, r3
    bne  reset_count
    ldr  r5, [r6, #0]
    cmp  r5, #4
    bhs  set_last
    ldr  r1, [r6, #4]
    mvns r1, r1
    lsls r1, r1, #16
    lsrs r1, r1, #16
    cmp  r3, r1
    bne  after
    mvns r1, r3
    lsls r1, r1, #16
    lsrs r1, r1, #16
    cmp  r2, r1
    bne  after
    adds r5, #1
    str  r5, [r6, #0]
    b    after
set_last:
    str  r2, [r6, #12]
    b    after
reset_count:
    movs r1, #0
    str  r1, [r6, #0]
after:
    ldr  r5, [r6, #0]
    cmp  r5, #4
    bhs  use_id
    ldr  r1, lit_data
    lsls r3, r5, #1
    ldrh r1, [r1, r3]
    b    have_id
use_id:
    ldr  r1, lit_rfuid
have_id:
    str  r1, [r6, #4]
    mvns r3, r2
    lsls r3, r3, #16
    lsrs r3, r3, #16
    str  r3, [r6, #8]
    lsls r1, r1, #16
    orrs r1, r3
    str  r1, [r4]
    ldrh r0, [r4, #8]
    movs r1, #0x80
    orrs r0, r1
    strh r0, [r4, #8]
    pop  {{r5}}
    pop  {{r1}}
    bx   r1

call_r3:
    bx   r3
    .align 2
lit_vars:     .word vars
lit_pkt:      .word pkt
lit_data:     .word nintendo
lit_msg:      .word msg
lit_sio:      .word {SIO:#x}
lit_rcnt:     .word {RCNT:#x}
lit_normal32: .word 0x1000
lit_rfuid:    .word {RFU_ID:#x}
nintendo:
    .short 0x494e, 0x544e, 0x4e45, 0x4f44
msg:
    .ascii "Adapter check answered"
    .byte 0
    .align 2
pkt:
    .word 0, 0
vars:
    .word 0, 0, 0, 0, 0, 0, 0, 0
"""
# vars: +0 count, +4 send_id, +8 recv_id, +12 last id from the game, +16 transfers,
#       +20 last word received, +24 'logged' flag

PROGRAMS = {"adapter_detect": ADAPTER_DETECT}

# Programs written in C (firmware/programs/<name>.c), built with clang + lld for the
# ARM7TDMI (ARMv4T, Thumb), linked to run at PROGRAM_AT, variables included in the image.
C_PROGRAMS = {"adapter": ("adapter.c", []), "adapter_probe": ("adapter.c", ["-DPROBE"])}
CLANG_VERSION = "18.1"                       # the version the embedded copies were built with
_HERE = os.path.dirname(os.path.abspath(__file__))


def _tool(name):
    for cand in (name, f"{name}-18"):
        path = shutil.which(cand)
        if path:
            return path
    raise RuntimeError(f"{name} is needed to build the C programs (LLVM {CLANG_VERSION})")


def clang_version():
    out = subprocess.run([_tool("clang"), "--version"], capture_output=True, text=True).stdout
    m = re.search(r"clang version (\d+\.\d+)", out)
    return m.group(1) if m else "?"


def symbols(name):
    """Addresses of a C program's global symbols (for the simulator's hardware models)."""
    src_name, defines = C_PROGRAMS[name]
    src = os.path.join(_HERE, "programs", src_name)
    ld = os.path.join(_HERE, "programs", "link.ld")
    with tempfile.TemporaryDirectory() as work:
        obj, elf = os.path.join(work, "p.o"), os.path.join(work, "p.elf")
        subprocess.run([_tool("clang"), "--target=armv4t-none-eabi", "-mthumb", "-Os", "-ffreestanding",
                        "-fno-builtin", "-nostdlib", "-fno-pic", "-Wall", "-Werror", *defines, "-c", src, "-o", obj],
                       check=True)
        subprocess.run([_tool("ld.lld"), "-T", ld, obj, "-o", elf], check=True)
        out = subprocess.run([_tool("llvm-nm"), elf], capture_output=True, text=True, check=True).stdout
    return {parts[2]: int(parts[0], 16) for parts in (l.split() for l in out.splitlines()) if len(parts) == 3}


def build_c(name):
    src_name, defines = C_PROGRAMS[name]
    src = os.path.join(_HERE, "programs", src_name)
    ld = os.path.join(_HERE, "programs", "link.ld")
    with tempfile.TemporaryDirectory() as work:
        obj, elf, binf = (os.path.join(work, f) for f in ("p.o", "p.elf", "p.bin"))
        subprocess.run([_tool("clang"), "--target=armv4t-none-eabi", "-mthumb", "-Os", "-ffreestanding",
                        "-fno-builtin", "-nostdlib", "-fno-pic", "-Wall", "-Werror", *defines, "-c", src, "-o", obj],
                       check=True)
        subprocess.run([_tool("ld.lld"), "-T", ld, obj, "-o", elf], check=True)
        subprocess.run([_tool("llvm-objcopy"), "-O", "binary", elf, binf], check=True)
        dis = subprocess.run([_tool("llvm-objdump"), "-d", "--triple=thumbv4t-none-eabi", elf],
                             capture_output=True, text=True, check=True).stdout
        code = open(binf, "rb").read()
    bad = []
    for line in dis.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\s+((?:[0-9a-f]{4} ?)+)\s+(\S+)", line)
        if not m or m.group(3).startswith("."):
            continue                                          # labels, literal pools (.word)
        mnem = m.group(3).split(".")[0]
        size = len(m.group(2).replace(" ", "")) // 2
        if mnem not in thumbcheck.THUMB1 | {"adcs"} or (size == 4 and mnem != "bl"):
            bad.append(line.strip())
    if bad:
        raise ValueError(f"{name}: not ARM7TDMI-compatible:\n  " + "\n  ".join(bad[:20]))
    return code


def build(name):
    if name in C_PROGRAMS:
        code = build_c(name)
        # Code, data AND variables must end below 0x0203AE00: above it are the firmware's
        # log messages and, from 0x0203AF00, its transfer state (build_v4: MSG_AT, VARS) -
        # V.MAX_PROGRAM (to 0x0203B000) is only what the firmware's own length check allows.
        assert V.PROGRAM_AT + len(code) <= 0x0203AE00, f"{name}: {len(code)} bytes, ends past 0x0203AE00"
        assert len(code) <= V.MAX_PROGRAM
        return struct.pack("<IIII", V.RUN_MAGIC, len(code), zlib.crc32(code), 1) + code
    code = thumbcheck.checked_assemble(assemble, PROGRAMS[name], V.PROGRAM_AT, name)
    assert len(code) <= V.MAX_PROGRAM
    entry = 1                                   # Thumb, at the start of the code
    return struct.pack("<IIII", V.RUN_MAGIC, len(code), zlib.crc32(code), entry) + code


if __name__ == "__main__":
    for n in list(PROGRAMS) + list(C_PROGRAMS):
        img = build(n)
        print(f"{n}: {len(img) - 16} bytes, image CRC {zlib.crc32(img):08X}")
        print(img.hex())
