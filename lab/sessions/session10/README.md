# Session 10: first real Wireless Adapter detection (27 October 2026)

**Set-up.**
- The DUO is running Reckless v4.2.
- The second GBA has a Japanese Pokémon Emerald in it.
- The two are joined with an MGB-010 "universal" Game Link cable, small plug to small plug (the big plug unused).

**Run.**
1. `python maxduo_saves.py --adapter-test`
2. Switch on the Emerald GBA.
3. Go to the main menu.

```
Connected to Action Replay MAX DUO (Datel protocol 1.04, Reckless v4.2)
Wireless Adapter detection test (reads and writes nothing on either cartridge)
  Program running on the DUO's GBA. Waiting for the game's check...
  [  0.0 s] nothing from the game yet
  [111.8 s] the game accepted the adapter ID (0x8001) - it believes a Wireless Adapter is connected
  Transfers: 14, last word from the game: 9966003D
  Result: PASS - the game saw a Wireless Adapter.
```

**Conclusions.**
- A Game Boy-style crossed cable carries the adapter's Normal-mode 32-bit link between two GBAs.
- The DUO's GBA keeps up with the game's 256 kHz clock and its timing between words.
- Emerald accepts the librfu adapter-side handshake ported to the DUO. The 111.8 s figure is just how long it took to switch on and reach the menu.
- The last word, `9966003D`, is an adapter command packet: header `0x9966`, no data, command **0x3D**. That's the game moving past detection to its first real adapter command. It's the "stop mode" step noted in [WIRELESS.md](../../../docs/WIRELESS.md), and the next layer to build.

Nothing was written to either cartridge.
