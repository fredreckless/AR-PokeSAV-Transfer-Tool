"""Rebuild this repository's GBA programs (firmware/programs/) and embed them in duo_events.py.
Run from the repository root after changing a program:  python firmware/embed_programs.py
(The tests check the embedded copies match a fresh build, so forgetting this fails the suite.)"""
import os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "firmware"))
import programs

p = os.path.join(ROOT, "duo_events.py")
s = open(p).read()
for name in list(programs.PROGRAMS) + list(programs.C_PROGRAMS):
    img = programs.build(name).hex()
    s, n = re.subn(r'("%s": bytes\.fromhex\(\n        ")[0-9a-f]+(")' % name, lambda m: m.group(1) + img + m.group(2), s)
    assert n == 1, name
open(p, "w").write(s)
print("embedded:", ", ".join(list(programs.PROGRAMS) + list(programs.C_PROGRAMS)))
