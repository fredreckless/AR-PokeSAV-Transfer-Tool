# Session 22: back to back, in the right order (29 October 2026, about 21:10)

**Set-up.**
- Session 21's tool: it waits for "Ready", re-sends a stalled upload, and hands the link port
  back as it found it.
- `--trace --wonder-card "E - Item Old Sea Map (JPN).wc3"`, run twice in a row.
- Japanese Emerald, MGB-010 cable. The game's GBA was started after "Ready".

**What the traces showed.**
- **Both runs were clean.** Each had the program upload, the card loaded and "Ready". Then the
  whole exchange ran:
  - the search (121 polls);
  - connect to 0x7DA5, the name and "may join" exchanges, link players;
  - Mystery Gift server steps 1 to 6, an agreed close, then `0x30`.
- Each had 3 packets sent again and 0 interrupted commands. Japanese Emerald and serial 0x7F7F
  were chosen automatically.
- There was no stalled upload, so the recovery wasn't needed.

**What this corrects.** The user explained it. In sessions 17 and 21 they had opened
ふしぎな おくりもの → ふしぎな カード while the DUO was still being set up. The game looks for a
Wireless Adapter as Mystery Gift opens, found none, and offered the e-Reader. By the time they
had backed out, the adapter was ready. Starting early also lined up with the stalled uploads in
session 21. Earlier notes put the e-Reader message down to the separate Mystery Event menu;
that was wrong.

**To do (the user's design):** a guided start-up order, in the hand-over's ideas list.
1. Both GBAs off.
2. Switch on the DUO's GBA.
3. A progress bar while it's set up.
4. Only then switch on the game's GBA.

The DUO wrote nothing to either cartridge. The game saved the card itself.
