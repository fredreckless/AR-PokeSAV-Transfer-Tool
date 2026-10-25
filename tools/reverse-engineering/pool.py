import re,struct,bisect,sys
d=open(__import__('os').environ.get('DUO_FW', 'firmware/build/fw.bin'),'rb').read()
src=open(__import__('os').environ.get('DUO_FW_C', 'fw.c')).read()
ents=sorted(int(m.group(1),16) for m in re.finditer(r'// ===== \S+ @ ([0-9a-f]+)',src))
strs={m.start()+0x08000000:m.group().decode() for m in re.finditer(rb'[\x20-\x7e]{5,}',d)}
def owner(a):
    i=bisect.bisect_right(ents,a)-1; return ents[i] if i>=0 else None
def users(addr):
    pat=struct.pack('<I',addr); out=[]
    i=d.find(pat)
    while i>=0:
        if i%4==0: out.append(owner(i+0x08000000))
        i=d.find(pat,i+1)
    return out
for a,s in sorted(strs.items()):
    if re.search(sys.argv[1],s):
        print(hex(a), s[:50].strip(), '<-', [hex(x) if x else None for x in users(a)])
