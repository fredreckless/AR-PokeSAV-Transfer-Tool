# Session 17: 0x1D with one flick (28 October 2026, about 17:30)

**Set-up.**
- `--trace` with commit 40aee1e: 0x1D's answer has a flick only on its header, and lost words
  are traced by phase and word.
- No Wonder Card loaded.
- Japanese Emerald, MGB-010 cable, Mystery Gift → じゅしん スポット. Two searches.

**What the trace showed.**
- Hello, setup, broadcast data and start searching completed again, both times.
- Every 0x1D (six of six) failed in the same place: **"answer word 1 was never clocked"**,
  then "the game asked for more of an answer than the adapter sent".
- So the game took the header (flick and all), and then never clocked the host ID `0x00007DA5`.
- Flick table: width 0, 41 flicks, 41 clocked, 36 accepted, 5 refused. Width 1, 2 flicks, 1 accepted.

**Reading.**
- Word 1 is the first top-bit-0 word ever sent after a handshake (every earlier answer was just a
  header). It was armed straight from "resting high" (SIOCNT 0x1008 → 0x1080).
- On the hardware this evidently leaves SO **high**, so the game never sees "ready". This also
  fits session 15: while armed, writes to bit 3 change nothing.
- After 20 ms our watchdog resets with a brief low, then re-arms the idle word. The game, still
  waiting for "ready", sees that low and clocks. It gets 0x80000000 back, and we get its
  0x80000000, which we report as "asked for more of an answer".

**Change.**
- A top-bit-0 word now goes low unarmed first, then arms at once (`SIOCNT = 0x1000; SIOCNT =
  0x1080`). That's two stores, as narrow as a width-0 flick.
- The simulator now models this:
  - armed from resting high, SO stays high;
  - a low before arming starts the same reaction window as a flick.
- With the old code, the simulator now fails at 0x1D just as the hardware did (planted bug
  "adapter arms a top-bit-0 word straight from high").

Nothing was written to either cartridge.
