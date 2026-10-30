"""Run every test in test_v4.py in its own process (the emulator library can
crash after very many simulated DUOs in one process), several at a time.

    python run_each.py [FILTER] [--jobs N]

FILTER picks the tests whose name contains it. Slowest tests start first. The default is one
process per CPU core: on a two-core machine, more than that made simulator tests time out.
For everyday use, `python check.py quick|changed|full` picks the tests for you."""
import concurrent.futures as cf, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
TIMES = os.path.join(HERE, ".test_times.json")          # remembered durations, to start the slow ones first


def all_names():
    return re.findall(r'@test\("(.*)"\)', open(os.path.join(HERE, "test_v4.py"), encoding="utf-8").read())


def default_jobs():
    return max(1, os.cpu_count() or 2)


def load_times():
    try:
        return json.load(open(TIMES))
    except (OSError, ValueError):
        return {}


def one(n, crashes):
    t0 = time.time()
    for attempt in range(3):
        r = subprocess.run([sys.executable, "test_v4.py", n], cwd=HERE, capture_output=True, text=True, timeout=1800)
        if r.returncode >= 0:
            break
        crashes.append((n, r.returncode))   # the emulator library occasionally segfaults under load (exit -11): run again
    lines = [l for l in r.stdout.splitlines() if l.startswith(("[PASS]", "[FAIL]", "[SKIP]"))]
    mine = [l for l in lines if n in l]
    status = mine[0][1:5] if mine else "FAIL"
    if r.returncode not in (0, 1) and status != "FAIL":
        status = "FAIL"
    return n, status, time.time() - t0, f"(exit code {r.returncode})\n" + r.stdout[-800:] + r.stderr[-800:]


def run_tests(names, jobs=None, quiet_pass=False):
    """Run the named tests, each in its own process. Returns the counts {"PASS", "FAIL", "SKIP"}."""
    jobs = jobs or default_jobs()
    times = load_times()
    order = sorted(names, key=lambda n: -times.get(n, 60))
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    crashes = []
    t_start = time.time()
    with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(one, n, crashes) for n in order]
        for f in cf.as_completed(futures):
            n, status, secs, tail = f.result()
            counts[status] += 1
            times[n] = round(secs, 1)
            if not (quiet_pass and status == "PASS"):
                print(f"{status} {n}  ({secs:.0f} s)" + ("\n" + tail if status == "FAIL" else ""), flush=True)
    try:
        json.dump(times, open(TIMES, "w"), indent=0, sort_keys=True)
    except OSError:
        pass
    if crashes:
        print(f"(the simulator crashed {len(crashes)} time(s) and those tests were run again: "
              + ", ".join(sorted({c[0][:40] for c in crashes})) + ")")
    print(f"{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped "
          f"in {time.time() - t_start:.0f} s with {jobs} at a time", flush=True)
    return counts


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--jobs")]
    jobs = next((int(a.split("=", 1)[1]) for a in sys.argv[1:] if a.startswith("--jobs=")), None)
    pick = args[0] if args else ""
    counts = run_tests([n for n in all_names() if pick in n], jobs)
    sys.exit(1 if counts["FAIL"] else 0)
