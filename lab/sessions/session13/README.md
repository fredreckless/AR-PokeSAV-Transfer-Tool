# Session 13: the first-bit fix on real hardware (27 October 2026)

**Set-up.**
- `--adapter-trace` with the first version of the first-bit fix.
- Japanese Emerald, MGB-010 cable.
- Main menu, then Mystery Gift → じゅしん スポット.

**What the trace showed.**
- The first word of every command is now accepted: the ready handshake follows every time.
- The acknowledgement then never goes out. Every time, the trace says "the game never noticed
  the adapter was ready".
- After that comes "the game asked for more of an answer than the adapter sent". That's a
  knock-on effect: the game clocks whatever the DUO has loaded after it gives up.

**Interpretation.** This version flicked SO, then checked whether the game had reacted, and
flicked again. If the game clocked the word in between, the next flick re-armed the link, so the
DUO never saw the word complete. In session 12, the probe always checked "word complete?" before
touching SIOCNT. It got the acknowledgement through every time, but it also kept flicking during
the word, which may be why the game still retried.

**Change.** After each flick, the program now leaves SIOCNT alone for longer than a whole word
takes (16 µs at 2 MHz), watching only for the word to complete. Then it flicks again if needed. The
end of a command uses a plain low until the game goes idle, then rests SO high, ready for the next
command. The decomp shows that can follow within microseconds (`rfu_REQ_recvData()` then
`rfu_LMAN_REQ_sendData()`).

The simulator now models:
- the quick follow-on command;
- a word being disturbed if the DUO touches SIOCNT mid-word.

Nothing was written to either cartridge.
