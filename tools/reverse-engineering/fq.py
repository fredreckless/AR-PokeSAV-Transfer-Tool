import re,sys
src=open(__import__('os').environ.get('DUO_FW_C', 'fw.c')).read()
F={}
for f in re.split(r'\n// ===== ', src)[1:]:
    name=f.split(' @ ')[0]; F[name]=f
def find(pat): return [k for k,v in F.items() if re.search(pat,v)]
cmd=sys.argv[1]
if cmd=='b':
    for n in sys.argv[2:]: print('// =====',F[n])
elif cmd=='f':
    for p in sys.argv[2:]: print(p,'->',find(p))
elif cmd=='c':
    for n in sys.argv[2:]: print(n,'<-',[k for k in find(r'\b'+n+r'\b') if k!=n])
