"""Run every test in test_v4.py in its own process (the emulator library can
crash after very many simulated DUOs in one process)."""
import os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
names = re.findall(r'@test\("(.*)"\)', open(os.path.join(HERE, "test_v4.py"), encoding="utf-8").read())
pick = sys.argv[1] if len(sys.argv) > 1 else ""
counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
for n in names:
    if pick not in n:
        continue
    r = subprocess.run([sys.executable, "test_v4.py", n], cwd=HERE, capture_output=True, text=True, timeout=1800)
    lines = [l for l in r.stdout.splitlines() if l.startswith(("[PASS]", "[FAIL]", "[SKIP]"))]
    mine = [l for l in lines if n in l]
    status = mine[0][1:5] if mine else "FAIL"
    if r.returncode not in (0, 1) and status != "FAIL":
        status = "FAIL"
    counts[status] += 1
    print(f"{status} {n}" + ("\n" + r.stdout[-800:] + r.stderr[-800:] if status == "FAIL" else ""), flush=True)
print(f"{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")
sys.exit(1 if counts["FAIL"] else 0)
