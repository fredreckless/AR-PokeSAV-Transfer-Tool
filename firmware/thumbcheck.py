"""Reject anything an ARM7TDMI (ARMv4T, Thumb-1) cannot execute."""
import capstone
THUMB1 = {"adc","add","adds","and","ands","asr","asrs","b","beq","bne","bhs","bcs","blo","bcc","bmi","bpl",
          "bvs","bvc","bhi","bls","bge","blt","bgt","ble","bic","bics","bl","bx","cmn","cmp","eor","eors",
          "ldm","ldmia","ldr","ldrb","ldrh","ldrsb","ldrsh","lsl","lsls","lsr","lsrs","mov","movs","mul","muls",
          "mvn","mvns","neg","negs","rsbs","orr","orrs","pop","push","ror","rors","sbc","sbcs","stm","stmia",
          "str","strb","strh","sub","subs","svc","swi","tst","nop","adr"}
_md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

def check(code, base, code_len=None, what=""):
    """Disassemble the instruction part (code_len bytes) and refuse non-Thumb-1."""
    n = len(code) if code_len is None else code_len
    off = 0
    problems = []
    while off < n:
        ins = next(_md.disasm(code[off:off + 4], base + off), None)
        if ins is None:
            problems.append(f"{base+off:#x}: undecodable"); off += 2; continue
        if ins.mnemonic not in THUMB1 or (ins.size == 4 and ins.mnemonic != "bl"):
            problems.append(f"{base+off:#x}: {ins.mnemonic} {ins.op_str} ({ins.size} bytes)")
        off += ins.size
    if problems:
        raise ValueError(f"{what}: not ARM7TDMI-compatible:\n  " + "\n  ".join(problems))


def code_part_len(src, base, assemble):
    """Bytes of instructions before the literal pool (everything before the first .align 2)."""
    import re
    head = src.split("    .align 2")[0]
    head = re.sub(r"\blit_\w+", "[pc, #0]", head)
    head = re.sub(r"adr\s+(r\d), \w+", r"add \1, pc, #0", head)
    return len(assemble(head, base))


def checked_assemble(assemble, src, base, what):
    code = assemble(src, base)
    check(code, base, code_part_len(src, base, assemble), what)
    return code
