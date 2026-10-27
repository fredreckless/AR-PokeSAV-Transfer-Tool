"""Run every test in test_v4.py in its own process (the emulator library can
crash after very many simulated DUOs in one process), several at a time.

    python run_each.py [FILTER] [--jobs N]

Much of each test is waiting (simulated USB, the PC tool's polling), so running a few
more processes than there are CPU cores still helps. Slowest tests start first."""
import concurrent.futures as cf, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
TIMES = os.path.join(HERE, ".test_times.json")          # remembered durations, to start the slow ones first
args = [a for a in sys.argv[1:] if not a.startswith("--jobs")]
jobs = next((int(a.split("=", 1)[1]) for a in sys.argv[1:] if a.startswith("--jobs=")), (os.cpu_count() or 2) * 2)
pick = args[0] if args else ""
names = [n for n in re.findall(r'@test\("(.*)"\)', open(os.path.join(HERE, "test_v4.py"), encoding="utf-8").read())
         if pick in n]
try:
    times = json.load(open(TIMES))
except (OSError, ValueError):
    times = {}
order = sorted(names, key=lambda n: -times.get(n, 60))


def one(n):
    t0 = time.time()
    r = subprocess.run([sys.executable, "test_v4.py", n], cwd=HERE, capture_output=True, text=True, timeout=1800)
    lines = [l for l in r.stdout.splitlines() if l.startswith(("[PASS]", "[FAIL]", "[SKIP]"))]
    mine = [l for l in lines if n in l]
    status = mine[0][1:5] if mine else "FAIL"
    if r.returncode not in (0, 1) and status != "FAIL":
        status = "FAIL"
    return n, status, time.time() - t0, r.stdout[-800:] + r.stderr[-800:]


counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
t_start = time.time()
with cf.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
    futures = [pool.submit(one, n) for n in order]
    for f in cf.as_completed(futures):
        n, status, secs, tail = f.result()
        counts[status] += 1
        times[n] = round(secs, 1)
        print(f"{status} {n}  ({secs:.0f} s)" + ("\n" + tail if status == "FAIL" else ""), flush=True)
try:
    json.dump(times, open(TIMES, "w"), indent=0, sort_keys=True)
except OSError:
    pass
print(f"{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped "
      f"in {time.time() - t_start:.0f} s with {jobs} at a time")
sys.exit(1 if counts["FAIL"] else 0)
