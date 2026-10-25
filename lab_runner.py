"""
lab_runner.py - lets Claude run read-only DUO experiments from Thonny.

Claude writes lab_job.json into this folder; pressing Run executes it and
appends to lab_runner_log.txt. Only read-only jobs are allowed. Firmware updates
(--update) are refused here - those stay a manual, typed-confirmation step.

lab_job.json is either one job   {"id": "...", "args": [...]}
or a list of jobs               {"id": "...", "jobs": [{"id": "...", "args": [...]}, ...]}
Each job is timed. A list stops at the first job that fails.

Special jobs:
  {"special": "boot_hello", "wait_s": 120}
  {"special": "datel_0x22"}  - asks Datel's command 0x22 once (its handler only
                               replies with a fixed value; nothing else is sent)

boot_hello:
It only ever sends HELLO (the DUO's greeting) and listens: it waits for the DUO
to appear on USB, clears any old packet, sends one hello, and reports when (and
what) the DUO answers - used to test the DUO's start-up behaviour.
"""
import contextlib, io, json, os, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

ALLOWED_FIRST = {"--lab", "--gba-save", "--list", "--cart", "--cart-report", "--restart"}   # all read-only
FORBIDDEN = {"--update"}


def load_job():
    path = os.path.join(HERE, "lab_job.json")
    if not os.path.exists(path):
        return {"id": "default-probe", "args": ["--list"]}
    with open(path) as f:
        return json.load(f)


def run_one(M, job, log):
    args = [str(a) for a in job.get("args", [])]
    header = f"\n=== job {job.get('id', '?')} at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\nargs: {args}\n"
    print(header, end="")
    log.write(header)
    if not args or args[0] not in ALLOWED_FIRST or FORBIDDEN & set(args):
        msg = "REFUSED: only read-only jobs can run from lab_runner.\n"
        print(msg); log.write(msg)
        return 1
    buf = io.StringIO()
    sys.argv = ["maxduo_saves.py"] + args
    code = 0
    t0 = time.perf_counter()
    try:
        with contextlib.redirect_stdout(buf):
            M.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if e.code not in (None, 0):
            buf.write(f"\n{e.code}\n")
    except Exception:
        code = 1
        buf.write("\n" + traceback.format_exc())
    out = buf.getvalue() + f"\nexit code {code}, took {time.perf_counter() - t0:.2f} s\n"
    print(out); log.write(out)
    return code


def boot_hello(M, job, log):
    wait_s = float(job.get("wait_s", 120))
    lines = []
    def say(msg):
        line = f"[{time.perf_counter() - t0:7.2f} s] {msg}"
        print(line, flush=True); lines.append(line)
    t0 = time.perf_counter()
    header = f"\n=== job {job.get('id', '?')} (boot_hello) at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"
    print(header, end=""); log.write(header)
    t = None
    while t is None and time.perf_counter() - t0 < wait_s:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                t = M.UsbTransport()
        except SystemExit:
            time.sleep(0.25)
    if t is None:
        say("the DUO never appeared on USB"); log.write("\n".join(lines) + "\n"); return 1
    say("the DUO is present on USB")
    stale = 0
    while True:
        try:
            pkt = t.read(300)
            stale += 1
            say(f"cleared an old packet: {pkt.hex(' ')}")
        except Exception:
            break
    code = 1
    try:
        while True:
            try:
                t.write(M.packet(M.HELLO, 0), 2000)
                break
            except Exception as err:
                if time.perf_counter() - t0 > wait_s:
                    raise
                say(f"the DUO is not taking packets yet ({err}); retrying")
        say("hello sent - switch the GBA on now if it is off")
        while time.perf_counter() - t0 < wait_s:
            try:
                pkt = t.read(1000)
            except Exception:
                continue
            c, v = M.parse(pkt)
            say(f"answer: code {c:#04x}, value {v:#x}" + ("  (hello reply)" if c == 0x03 else ""))
            if c == 0x03:
                code = 0
                break
        else:
            say("no answer within the time allowed")
    finally:
        t.close()
    log.write("\n".join(lines) + f"\nexit code {code}\n")
    return code


def datel_0x22(M, job, log):
    header = f"\n=== job {job.get('id', '?')} (datel_0x22) at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"
    print(header, end=""); log.write(header)
    with contextlib.redirect_stdout(io.StringIO()):
        t = M.UsbTransport()
    try:
        while True:                                  # clear anything left over
            try:
                t.read(300)
            except Exception:
                break
        t.write(b"CBW\x22" + bytes(4), 2000)
        reply = t.read(5000)
        c, v = M.parse(reply)
        line = f"reply {reply.hex(' ')}  ->  code {c:#04x}, value {v:#x}\nexit code 0\n"
    finally:
        t.close()
    print(line); log.write(line)
    return 0


def main():
    job = load_job()
    jobs = job["jobs"] if "jobs" in job else [job]
    log = io.StringIO()
    log.write(f"##### {job.get('id', '?')}: {len(jobs)} job(s) #####\n")
    import maxduo_saves as M
    for j in jobs:
        special = j.get("special")
        if special == "boot_hello":
            rc = boot_hello(M, j, log)
        elif special == "datel_0x22":
            rc = datel_0x22(M, j, log)
        elif special:
            print(f"REFUSED: unknown special job {special!r}"); log.write("REFUSED\n"); rc = 1
        else:
            rc = run_one(M, j, log)
        if rc != 0:
            log.write("Stopping: a job failed.\n")
            print("Stopping: a job failed.")
            break
    with open(os.path.join(HERE, "lab_runner_log.txt"), "a", encoding="utf-8") as f:
        f.write(log.getvalue() + "\n")
    print("Log written to lab_runner_log.txt")


if __name__ == "__main__":
    main()
