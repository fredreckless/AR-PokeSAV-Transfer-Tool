# Session 15: the overnight build on real hardware (28 October 2026)

**Set-up.**
- `--adapter-trace` with stages D2 and E built in, and no Wonder Card loaded.
- Japanese Emerald, MGB-010 cable.
- Mystery Gift → じゅしん スポット.

**What the trace showed.**
- ID exchanges were fine: 106 of them.
- Every command stopped at its acknowledgement: "the game never noticed the adapter was ready",
  309 times.
- The flick table: widths 1–14 were tried 4 times each. Width 15 (about 12 µs low) was tried
  29,562 times. None was clocked.
- The scope showed the game's SO high throughout. It never saw the low.
- After each flick timeout, the DUO sat low and disarmed, and the game clocked straight away.
  That gave 411 "asked for more" strays.

**Interpretation.** While the DUO's SIO is armed (START set, external clock), its SO pin shows
the loaded word's first bit. SIOCNT bit 3 doesn't reach the pin, which is how GBATEK describes
an active transfer. So, for words starting with a 1 (every `0x9966…`), a low written to bit 3
while armed never reaches the cable, however long it lasts. The steady, disarmed low at a
command's end is seen at once.

This fits the earlier sessions too:
- **Session 12.** The first word was accepted with SO resting high.
- **Sessions 13 and 14.** The ready flick was never seen.
- **Session 11.** The answers were refused.

**Change.** The ready flick now *disarms* for a moment, so SO really goes low. It then re-arms
(SO high, the word's first bit) before the game's first clock edge. Its width still tunes
itself. If the game clocks while the DUO is still disarmed, the word is lost. The DUO sees the
game's SO go low with no word arriving, and narrows the flick.

The simulator now models armed SO as the word's first bit. With the old armed flick, it fails
exactly as this session did; the planted bug "adapter flicks while still armed" checks that.

The tool now retries a poll at the end of a run, so Ctrl+C still gets the report out.

Nothing was written to either cartridge.
