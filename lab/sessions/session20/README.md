# Session 20: the Old Sea Map Wonder Card is received (28 October 2026, about 21:50)

**Set-up.**
- `--trace --wonder-card "E - Item Old Sea Map (JPN).wc3"`, distributor serial 0x7F7F.
- Japanese Emerald, MGB-010 cable, Mystery Gift → じゅしん スポット.
- The save was backed up first.

**What the trace showed.**
- "Wonder Card loaded: … (Japanese, event flag 0x03ea, 164 + 1000 bytes)".
- The game found the distributor after 121 polls (about 2 s) and connected to 0x7DA5. Then:
  - NI name and "may join";
  - link players;
  - Mystery Gift server steps 1 to 6: game data, client script, **Wonder Card**, **RAM script**,
    ready to end, finished;
  - close agreed, then the game disconnected (`0x30`).
- Interrupted commands: 0. Packets sent again: 3. Flicks: 3,994 of 3,998 accepted at width 0.

**On the GBA.** After saving, the card menu offers よみこむ / すてる / やめる (Receive / Toss /
Cancel: `sListMenuItems_ReceiveToss`). That's the menu for a saved card that can't be shared, so
**the card is in the save**. The item comes from the delivery man in any Pokémon Center.

**Facts about the `.wc3` file** (from Project Pokémon's EventsGallery):
- The card's CRC-16 and the RAM script's CRC-16 are both valid. The script has magic 0x33 and
  map 0xFF/0xFF/0xFF.
- The Japanese section gives item 0x178 (Old Sea Map).
- The 1,000-byte script also holds English and French sections for a Lilycove-port ticket
  (item 0x173). They sit in one consistently laid-out block, and only the game's own language
  section runs.

The DUO wrote nothing to either cartridge. The game saved the card itself.
