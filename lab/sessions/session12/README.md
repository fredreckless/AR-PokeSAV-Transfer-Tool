# Session 12: the 2 MHz probe (27 October 2026)

**Set-up.**
- Firmware v4.2 with `adapter_probe` (`--adapter-probe`).
- Japanese Emerald, MGB-010 cable.
- The game was left in its Mystery Gift wireless screen, which kept resetting the adapter and
  retrying, so the probe gathered several hundred attempts on its own.

**Result.**

```
Answer loaded as                         tries accepted ack got through
one-bit lag (load W<<1, SO = top bit)      180        0               0
first bit late (load W, SO = top bit)      359      359             359
as designed                                  0        0               0
Commands answered: 539, ID exchanges: 189, interrupted commands: 180
```

**Conclusions.**
- At 2 MHz, with the DUO's GBA as clock slave, the game reads the **first bit** of each word from
  the DUO's SO resting level (SIOCNT bit 3). The other 31 bits arrive intact.
- Holding SO at the word's top bit beforehand fixes the first word of a command, every time.
- For words after the ready handshake (SO must go low to mean "ready"), briefly flicking SO low
  and back high gets the game to clock. The acknowledgement header (`0x9966…`) was then
  accepted, every time.
- The commands still didn't complete; the game kept retrying. The probe went on flicking SIOCNT
  for the whole of each word. The likeliest explanation is that this disturbed the word's lower
  bits, such as the length byte. The game then asked for more of the answer, and the probe didn't
  give it.

**Next.** The `adapter` program now loads every answer the first-bit way, and stops flicking as
soon as the game's SO shows it is about to clock. It also records it if the game asks for more of
an answer than was sent. The simulator now models the first-bit behaviour, and Emerald's whole
search completes cleanly there with no retries.

Nothing was written to either cartridge.
