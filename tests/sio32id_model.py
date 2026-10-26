"""Bit-exact model of librfu's Sio32ID exchange (both roles), from the Emerald decompilation."""
DATA=[0x494e,0x544e,0x4e45,0x4f44]; RFU_ID=0x8001
class Side:
    def __init__(s, ms): s.ms=ms; s.count=0; s.send_id=0; s.recv_id=0; s.last=0; s.out=0
    def intr(s, d):
        ms=s.ms
        unk0=((d << (16*ms)) & 0xFFFFFFFF) >> 16
        r=((d << (16*(1-ms))) & 0xFFFFFFFF) >> 16
        if s.last==0:
            if unk0==s.recv_id:
                if s.count<4:
                    if s.recv_id == (~s.send_id & 0xFFFF):
                        if r == (~s.recv_id & 0xFFFF): s.count+=1
                else: s.last=r
            else: s.count=0
        s.send_id = DATA[s.count] if s.count<4 else RFU_ID
        s.recv_id = ~r & 0xFFFF
        s.out = ((s.send_id << (16*(1-ms))) + (s.recv_id << (16*ms))) & 0xFFFFFFFF
def _demo(junk=0):
    m=Side(1); a=Side(0); a.out=junk
    for t in range(200):
        mo,ao=m.out,a.out
        m.intr(ao); a.intr(mo)
        if t<12: print(f"{t:2}: GBA {mo:08X}  adapter {ao:08X}")
        if m.last: return t,m.last,a.last
    return None


