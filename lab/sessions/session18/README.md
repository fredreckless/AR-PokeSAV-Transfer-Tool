# Session 18: 0x1D goes through, but the game doesn't pick the distributor (28 October 2026, about 20:40)

**Set-up.**
- `--trace` with commit 77e6d93: top-bit-0 words go low unarmed, then arm.
- Japanese Emerald, MGB-010 cable, Mystery Gift → じゅしん スポット.
- No Wonder Card loaded.

**What the trace showed.**
- `search results? (0x1D)` was answered **4,171 times** in about 70 s, once a frame.
- There were no interrupted commands. Flicks: 4,195 of 4,198 accepted at width 0.
- But the game never stopped searching (no `0x1E`) or tried to connect (`0x1F`). After about
  70 s it restarted the adapter (Hello, setup, broadcast), then put it to sleep.
- "unknown command (0x96)" once: the echo seen with 0xBD, this time of our 0x16 acknowledgement.

**Reading.**
- Every word of the answer is now clocked, but only the header is checked by the game. The data
  words could still arrive changed without anything showing.
- librfu keeps an entry only if its checksum is good (`rfu_STC_readParentCandidateList`).
  Emerald then wants serial 0x7F7D and hasCard (`Rfu_GetWonderDistributorPlayerData`,
  `Task_CardOrNewsOverWireless`).
- If it had kept the entry, the screen would have moved on within about 2 s and the game would
  have sent 0x1E and 0x1F. It didn't, so either the data words arrive changed, or the Japanese
  game wants something different.
- The game's own broadcast data (`0x0F810002`: serial 0x0002, compatibility 0x0F81) has the same
  layout as the English decomp's, so the layout itself is probably not the difference.

**Next: `--check-search`.**
- The distributor is listed four times in each answer.
- librfu's fast search (`fastSearchParent_flag`, on in Emerald's config) ends and restarts the
  search as soon as one answer holds four entries with good checksums.
- So `0x1E` then `0x1C` in the trace proves the data words arrive intact, without relying on the
  menu.

Nothing was written to either cartridge.
