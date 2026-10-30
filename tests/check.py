"""Tiered testing: run what a change needs, not always everything.

    python tests/check.py quick              a fast spread of tests across every area (a few minutes)
    python tests/check.py changed [BASE]     the tests and planted bugs for the files changed since BASE
    python tests/check.py full [BASE]        every test, plus the planted bugs for what changed
    python tests/check.py full --all-bugs    every test and every planted bug that can run here
    python tests/check.py what [BASE]        just say what `changed` would run

BASE is a git revision (default origin/main); uncommitted and new files count as changed too.

When to use which (see docs/HANDOVER.md):
- quick: after each small change while working;
- changed: before calling a piece of work done;
- full: before pushing a code change;
- nothing: a change to documentation only (*.md, lab/, docs/)."""
import fnmatch, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import run_each

ALL = "ALL"

# Test groups, as prefixes of test names in test_v4.py.
MYSTERY_GIFT = ["wireless adapter", "distributor", "serial sweep", "find serial", "every game", "Wonder News",
                "search check", "finishing", "guided start-up", "2 MHz probe", "card checks",
                "v4.2 adapter test", "adapter program", "v4.2 programs"]
EREADER = ["e-Reader", "e-Card", "Mystery Event files", "Enigma Berry", "v4.2 programs", "guided start-up"]
EVENTS_TOOL = MYSTERY_GIFT + EREADER + ["two tools", "v4.2 run", "connect"]

# Which tests a changed file needs, first match wins. ALL for anything shared; [] for documentation.
AREAS = [
    ("*.md", []), ("lab/*", []), ("docs/*", []), ("LICENSE*", []), (".gitignore", []),
    ("firmware/programs/ereader.c", EREADER),
    ("firmware/programs/adapter.c", MYSTERY_GIFT),
    ("firmware/programs/*", MYSTERY_GIFT + EREADER),
    ("firmware/embed_programs.py", MYSTERY_GIFT + EREADER),
    ("firmware/thumbcheck.py", MYSTERY_GIFT + EREADER + ["build:"]),
    ("duo_events.py", EVENTS_TOOL),
    ("duo_wireless.py", ["two tools"]),
    ("lab_runner.py", ["lab_runner"]),
    ("tests/rfu_game_model.py", MYSTERY_GIFT),
    ("tests/link_game_model.py", EREADER),
    ("tests/mevent_model.py", EREADER),
    ("tests/ecard_fixtures.py", EREADER),
    ("tests/check.py", []), ("tests/run_each.py", []), ("tests/history/*", []),
    ("*", ALL),                      # maxduo_saves.py, the firmware builds, hwsim, test_v4.py, fixtures...
]

# Which planted-bug kinds (tests/mutate4.py) a changed file needs.
BUG_KINDS = [
    ("firmware/programs/ereader.c", ["ereader"]),
    ("firmware/programs/adapter.c", ["prog"]),
    ("firmware/programs/*", ["prog", "ereader"]),
    ("firmware/build_v42.py", ["fw42"]),
    ("firmware/build_v4.py", ["fw", "fw42"]),
    ("firmware/build_*.py", ["fw", "fw42"]),
    ("duo_events.py", ["pc"]), ("maxduo_saves.py", ["pc"]),
    ("tests/rfu_game_model.py", ["prog"]),
    ("tests/link_game_model.py", ["ereader"]), ("tests/mevent_model.py", ["ereader", "pc"]),
    ("tests/ecard_fixtures.py", ["pc"]),
    ("tests/test_v4.py", ALL), ("tests/hwsim.py", ALL), ("tests/fixtures.py", ALL),
    ("tests/mutate4.py", ALL),       # a changed or new planted bug should be run
]

# The quick tier: a fast spread across every area, by full test name prefix.
QUICK = [
    "build: every patch routine",                       # Thumb-1 only
    "info: v4 identifies itself",
    "stream: 9 data patterns round-trip exactly",
    "upload: a corrupted upload is detected",
    "types: SRAM",
    "verify: one bad byte",
    "refusals: DeleteFile",
    "cartridge report: the logo constants",
    "backups: per-game folder",
    "two tools:",
    "v4.2 programs:",
    "v4.2 run: a staged program with a bad magic",
    "card checks:",
    "Wonder News files:",
    "Mystery Event files:",
    "wireless adapter: Emerald's Mystery Gift search",
    "distributor: link players exchanged",
    "e-Reader: Ruby as master",
    "e-Card scans:",
]


def changed_files(base):
    def git(*a):
        r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
        return r.stdout.splitlines() if r.returncode == 0 else []
    files = set(git("diff", "--name-only", base)) | set(git("diff", "--name-only", "--cached", base))
    files |= set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(f for f in files if f)


def lookup(table, path):
    for pattern, value in table:
        if fnmatch.fnmatch(path, pattern):
            return value
    return None


def pick(prefixes, names):
    if prefixes == ALL:
        return list(names)
    return [n for n in names if any(n.startswith(p) for p in prefixes)]


def plan(base):
    names = run_each.all_names()
    files = changed_files(base)
    tests, kinds, why = set(), set(), []
    for f in files:
        area = lookup(AREAS, f)
        k = lookup(BUG_KINDS, f) or []
        got = pick(area, names)
        tests |= set(got)
        kinds |= set(["fw", "fw42", "prog", "ereader", "pc"] if k == ALL else k)
        why.append(f"  {f}: " + ("all tests" if area == ALL else f"{len(got)} tests" if got else "no tests")
                   + (f"; planted bugs: {'all' if k == ALL else ', '.join(k)}" if k else ""))
    return files, [n for n in names if n in tests], sorted(kinds), why


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    tier = args[0] if args else "quick"
    base = args[1] if len(args) > 1 else "origin/main"
    jobs = next((int(a.split("=", 1)[1]) for a in flags if a.startswith("--jobs=")), None)
    names = run_each.all_names()
    t0 = time.time()
    if tier == "quick":
        tests = pick(QUICK, names)
        missing = [q for q in QUICK if not pick([q], names)]
        assert not missing, f"QUICK names no longer match a test: {missing}"
        print(f"Quick tier: {len(tests)} of {len(names)} tests", flush=True)
        counts = run_each.run_tests(tests, jobs, quiet_pass=True)
        ok = not counts["FAIL"]
    elif tier in ("changed", "full", "what"):
        files, tests, kinds, why = plan(base)
        print(f"Changed since {base}: {len(files)} file(s)")
        print("\n".join(why) if why else "  (nothing)")
        if tier == "full":
            tests = names
        if "--all-bugs" in flags:
            kinds = None
        print(f"{'Full' if tier == 'full' else 'Changed'} tier: {len(tests)} tests; planted bugs: "
              + ("all" if kinds is None else ", ".join(kinds) or "none"), flush=True)
        if tier == "what":
            return 0
        ok = True
        if tests:
            counts = run_each.run_tests(tests, jobs, quiet_pass=True)
            ok = not counts["FAIL"]
        if kinds is None or kinds:
            import mutate4
            ok = mutate4.run_many(kinds, jobs) and ok
    else:
        sys.exit(__doc__)
    print(f"{'ALL GOOD' if ok else 'FAILURES - see above'} ({time.time() - t0:.0f} s in all)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
