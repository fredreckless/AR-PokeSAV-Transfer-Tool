# Session 21: two Wonder Cards back to back, with the game chosen automatically (29 October 2026, evening)

**Set-up.**
- The roadmap stage 1 tool (`duo_wireless.py` at commit c1e0960). No `--serial`: the
  distributor chooses its serial from the game's broadcast data.
- Japanese Emerald, MGB-010 cable, Mystery Gift → じゅしん スポット.
- Four runs in a row:
  1. `--trace --wonder-card "E - Item Old Sea Map (JPN).wc3"`.
  2. `--trace --wonder-card "E - Item MysticTicket 2005 (JPN).wc3"`.
  3. The same command again.
  4. The same command once more, stopped with Ctrl+C.

**What the traces showed.**
- **Stage 1 works on hardware.** Each successful run:
  - names the game itself: `broadcast data (0x16) 0F810002 … - Japanese Emerald`;
  - finishes with "Distributor serial used: 0x7f7f, for Japanese Emerald", with no `--serial`;
  - shows no "unknown command (0xBD)" lines, so the echo filter does its job;
  - gives no card/game language warning.
- **Both cards arrived.** Runs 1 and 3 each completed the whole sequence:
  - the search (121 and 29 polls);
  - connect to 0x7DA5;
  - the name and "may join" exchanges, then link players;
  - Mystery Gift server steps 1 to 6;
  - an agreed close, then `0x30`.

  Each had 3 packets sent again and 0 interrupted commands. Each took about 12 s from connecting
  to disconnecting.
- **Runs 2 and 4 stalled while uploading the program.** Both came straight after a successful
  run.
  - Run 2 timed out after 5 s ("The Action Replay stopped answering").
  - Run 4 was stopped with Ctrl+C while still waiting for an upload answer
    (`_upload_fast` → `read`).
  - Run 3 then began with "cleared 1 old packet(s)": the upload answer that had gone missing in
    run 2 turned up later. The GBA had answered; its answer was held up on the way to the PC.
  - The game had been sent into Mystery Gift while run 2 was stalled, so it found no adapter
    and reported a communication error. Restarting the menu was the right move.
- Session 16 also had "two attempts timed out on USB before connecting" between runs, so this
  has happened before.

**Changes made after this session.**
- `run_program`: if a program upload stalls, the tool waits for the DUO to give up (~16 s),
  says hello again (which clears the late answer) and sends the program once more. This is the
  same recovery that run 3 did by hand. There's a simulator test of a held-up answer.
- The trace now says "wait for 'Ready'" and only asks you to use the game once the program is
  running and the card is loaded. So a stalled upload can no longer send the game looking for
  an adapter that isn't there yet.
- `adapter.c` and `ereader.c` now restore RCNT and SIOCNT on exit, as they found them. Before,
  they left the link port in normal mode (`RCNT = 0`). `ereader.c` also clears any pending
  serial request before restoring IE. This is tidy-up, not a proven cause: why an answer is
  held up only after a previous run is still unknown.

**On the GBA.** The game keeps one Wonder Card and one RAM script. Receiving MysticTicket
replaced the Old Sea Map card **and its RAM script**. If the Old Sea Map hadn't been collected
from the delivery man first, it now needs receiving again. **Collect each item before receiving
the next card.**

The DUO wrote nothing to either cartridge. The game saved each card itself.
