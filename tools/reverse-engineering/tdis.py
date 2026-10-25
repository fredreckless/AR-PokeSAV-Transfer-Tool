import capstone,struct,sys
d=open(__import__('os').environ.get('DUO_FW', 'firmware/build/fw.bin'),'rb').read(); B=0x08000000
md=capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
a0,a1=int(sys.argv[1],16),int(sys.argv[2],16)
for ins in md.disasm(d[a0-B:a1-B], a0):
    extra=''
    if ins.mnemonic=='ldr' and '[pc' in ins.op_str:
        off=int(ins.op_str.split('#')[-1].rstrip(']'),16)
        la=((ins.address+4)&~3)+off
        if B<=la<B+len(d): extra='   ; =0x%08x'%struct.unpack_from('<I',d,la-B)[0]
    print(hex(ins.address), ins.mnemonic, ins.op_str, extra)
