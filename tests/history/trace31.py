import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); import paths
import hwsim, maxduo_saves as M
M.SHORT_TIMEOUT=4000
RUBY=__import__("fixtures").make_gen3_save()
tr=[]
od=hwsim.Hw.deliver
def deliver(self,pkt):
    n0=self.d2h.qsize(); od(self,pkt); 
    got=list(self.d2h.queue)[-1] if self.d2h.qsize()>n0 else None
    tr.append(("GBA",pkt.hex(),"CORRUPT->"+got.hex() if got and got!=pkt else ""))
hwsim.Hw.deliver=deliver
ow=hwsim.Transport.write
def write(self,d,timeout=5000):
    tr.append(("PC ",bytes(d).hex(),"")); return ow(self,d,timeout)
hwsim.Transport.write=write
hw=hwsim.Hw(open("fw_v3.bin","rb").read(),hwsim.make_game_rom(),"sanyo",RUBY,{"corrupt_at":{int(sys.argv[1])}}).start()
duo=M.MaxDuo(hwsim.Transport(hw)); duo.hello()
lab=M.Lab(duo)
ops=[("w16",M.MAP_REG,0x101),("delay",0,0x100),("r16",0x08000000,2),("r8",0x0E000000,0x10000),("w16",M.MAP_REG,0x120)]
try: lab.run(ops); print("OK", duo.stats["retries"])
except Exception as e: print("ERR",e)
start=[i for i,t in enumerate(tr) if t[2]]
for t in tr[-24:]: print(*t)
print(hw.log[-8:]); hw.done.set()
