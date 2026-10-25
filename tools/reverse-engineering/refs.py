import re,struct,bisect,sys
d=open(__import__('os').environ.get('DUO_FW', 'firmware/build/fw.bin'),'rb').read()
src=open(__import__('os').environ.get('DUO_FW_C', 'fw.c')).read()
ents=sorted(int(m.group(1),16) for m in re.finditer(r'// ===== \S+ @ ([0-9a-f]+)',src))
def owner(a):
    i=bisect.bisect_right(ents,a)-1; return ents[i] if i>=0 else None
for v in [int(x,16) for x in sys.argv[1:]]:
    pat=struct.pack('<I',v); hits=[]; i=d.find(pat)
    while i>=0:
        if i%4==0: hits.append(hex(owner(0x08000000+i) or 0))
        i=d.find(pat,i+1)
    print(hex(v),'<-',sorted(set(hits)))
