# Session 23: English LeafGreen receives the Aurora Ticket (29 October 2026, about 21:20)

**Set-up.**
- `--trace --wonder-card "FL - Item AuroraTicket (ENG) (UK).wc3"`. No `--serial`.
- English LeafGreen, MGB-010 cable, Mystery Gift → Wonder Cards → Wireless.
- The game's GBA was started after "Ready".

**What the trace showed.**
- "Wonder Card loaded: … (English/European, event flag 0x03e8, 332 + 1000 bytes)": the full-size
  international card (0x14C).
- `broadcast data (0x16) 14020002 … - English LeafGreen`. The game is recognised from its own
  broadcast data, and the host followed it with an English link player.
- The game found the distributor after 121 polls and connected to 0x7DA5. Then:
  - the name and "may join" exchanges;
  - link players (the game's language: 2, English);
  - Mystery Gift server steps 1 to 6: game data, client script, **Wonder Card**, **RAM script**,
    ready to end, finished;
  - an agreed close, then `0x30`.
- Distributor serial used: **0x7F7D, for English LeafGreen**. That confirms the decomps' serial
  for this game.
- 5 packets sent again, 0 interrupted commands. The larger card took 21 polls at step 3,
  against Emerald's 14.

**What this confirms.**
- **Roadmap stage 1 beyond Japanese Emerald.** It's the first game other than Japanese Emerald,
  the first English game, and the first full-size card. It worked first time, exactly as the
  simulator predicted (`rfu_game_model` EN_FIRERED with a 0x14C card).
- FireRed/LeafGreen's Mystery Gift is the same protocol as Emerald's on real hardware.

The DUO wrote nothing to either cartridge. The game saved the card itself.
