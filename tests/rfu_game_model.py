"""
The game's side of the Wireless Adapter link, ported from librfu in the Emerald
decompilation (librfu_intr.c, librfu_stwi.c, librfu_sio32id.c), for the simulator.

It is written as a generator: it yields link-port operations, and the simulator
(hwsim.Hw.attach_link_game) carries them out against the DUO's GBA and sends back the
result. Time is counted in emulator blocks; hwsim.BLOCKS_PER_LINE converts scanlines.

  ("so", level)              the game's SO line (its SIOCNT bit 3)
  ("wait_si", level, t)      wait until the DUO's SO is at level; True, or False after t
  ("xfer", word[, 2])        clock one 32-bit word as master (2 = at 2 MHz); returns the DUO's word
  ("arm", word)              ready as clock slave with word loaded (SO low)
  ("wait_xfer", t)           wait for the DUO to clock a word; returns it, or None after t
  ("delay", t)
  ("until_ready",)           wait until the DUO's link port is armed (someone switches the game on)
"""
import sio32id_model

LINE = 25                                   # emulator blocks per scanline (keep in step with hwsim)
MS = 16 * LINE                              # ~ one millisecond (15.7 lines)
IDLE = 0x80000000
CHANGE_ACKS = {0x80 | 0x27, 0x80 | 0x25, 0x80 | 0x35, 0x80 | 0x37}


class LinkError(Exception):
    pass


class Game:
    def __init__(self):
        self.log = []                         # (what, details) as the game saw it
        self.retries = 0
        self.errors = []

    # ------------------------------------------------------------------ ID exchange
    def check_id(self, max_tries=30):
        """AgbRFU_checkID: Sio32ID as clock master. Returns the adapter's ID or 0."""
        side = sio32id_model.Side(1)
        for _ in range(max_tries * 8):
            for _ in range(64):                               # one Sio32IDMain() round
                got = yield ("xfer", side.out)
                d = got
                side.intr(d)
                if side.last:
                    self.log.append(("id", side.last))
                    yield ("delay", 228 * LINE)               # the link manager's next step is a frame later
                    return side.last
                theirs = d & 0xFFFF
                if not (side.count or theirs == 0x494E):
                    break                                     # wait for the next round
                yield ("delay", 2 * LINE)                     # the 600-iteration loop (~150 us)
            yield ("delay", 2 * MS)
        self.log.append(("id", 0))
        return 0

    # ------------------------------------------------------------------ commands (game = clock master)
    def command(self, cmd, data=()):
        """STWI_send_* + sio32intr_clock_master, with librfu's timeouts and two retries.
        Returns the acknowledgement words (header first). Raises LinkError like librfu."""
        data = list(data)
        recovery = 0
        while True:
            result = yield from self._attempt(cmd, data)
            if result is not None:
                self.log.append(("cmd", cmd, tuple(data), tuple(result)))
                return result
            if recovery >= 2:
                self.errors.append(f"command {cmd:#04x}: clock drift (no answer after retries)")
                raise LinkError(f"command {cmd:#04x} failed")
            recovery += 1
            self.retries += 1

    def _attempt(self, cmd, data):
        words = [0x99660000 | (len(data) << 8) | cmd] + data
        state, nxt, rx, ack_len = 0, 1, [], 0
        out = words[0]
        yield ("so", 0)
        while True:
            got = yield ("xfer", out, 2)                      # STWI_start_Command: 2 MHz
            if state == 0:
                if got != IDLE:
                    yield ("delay", 130 * MS)                 # timer 130 -> STWI_restart_Command
                    return None
                if nxt <= len(data):
                    out = words[nxt]; nxt += 1
                else:
                    state, out = 1, IDLE
            elif state == 1:
                if got >> 16 != 0x9966:
                    yield ("delay", 130 * MS)
                    return None
                rx = [got]
                ack_len = (got >> 8) & 0xFF
                if ack_len >= 1:
                    state, out = 2, IDLE
                else:
                    state = 3
            elif state == 2:
                rx.append(got)
                if ack_len < len(rx):
                    state = 3
                else:
                    out = IDLE
            # the ready handshake: wait for the adapter high, go high, wait for it low
            if not (yield ("wait_si", 1, 80 * MS)):
                yield ("delay", 50 * MS)
                return None
            yield ("delay", 8)                                # the loop exits, then SIOCNT |= SD (generous)
            yield ("so", 1)
            if not (yield ("wait_si", 0, 80 * MS)):
                yield ("delay", 50 * MS)
                return None
            if state == 3:
                yield ("so", 0)
                # the next command can follow within microseconds: link_rfu_2.c does
                # rfu_REQ_recvData(); rfu_waitREQComplete(); rfu_LMAN_REQ_sendData(...)
                yield ("delay", 4)
                if (rx[0] & 0xFF) == 0xEE:
                    self.errors.append(f"command {cmd:#04x} rejected by the adapter: {rx}")
                    raise LinkError(f"command {cmd:#04x} rejected")
                return rx
            yield ("so", 0)

    # ------------------------------------------------------------------ the adapter's turn (game = clock slave)
    def slave_turn(self, timeout):
        """After 0x25/0x27/0x35/0x37: sio32intr_clock_slave until the adapter's command has
        been acknowledged. Returns that command word."""
        yield ("arm", IDLE)
        state, tx, ack_next, req = 5, [], 0, None
        wait = timeout
        while True:
            got = yield ("wait_xfer", wait)
            if got is None:
                self.errors.append("the adapter never took its turn (clock slave timeout)")
                raise LinkError("slave timeout")
            wait = 100 * MS
            if not (yield ("wait_si", 0, 100 * MS)):
                raise LinkError("slave handshake (low) timed out")
            yield ("so", 1)
            if state == 5:
                req = got
                if got >> 16 == 0x9966 and (got >> 8) & 0xFF == 0 and (got & 0xFF) in (0x27, 0x28, 0x29, 0x36):
                    tx = [0x99660000 | ((got & 0xFF) + 0x80)]
                else:
                    tx = [0x996601EE, 1]
                    self.errors.append(f"the adapter sent an unexpected command {got:#010x}")
                out, ack_next, state = tx[0], 1, 7
            elif state == 7:
                if got != IDLE:
                    raise LinkError(f"slave: expected 0x80000000, got {got:#010x}")
                if len(tx) - 1 < ack_next:
                    state = 8
                else:
                    out = tx[ack_next]; ack_next += 1
            if not (yield ("wait_si", 1, 100 * MS)):
                raise LinkError("slave handshake (high) timed out")
            if state == 8:
                yield ("so", 0)                               # back to clock master
                self.log.append(("adapter", req))
                yield ("delay", LINE // 2)                    # the slave callback, then the next request
                return req
            yield ("delay", 2)                                # >= 40 us
            yield ("arm", out)

    def change(self, cmd, data=(), timeout=None):
        """A command that hands the clock to the adapter (0x25, 0x27...)."""
        ack = yield from self.command(cmd, data)
        if ack[0] & 0xFF not in CHANGE_ACKS:
            raise LinkError(f"unexpected ack {ack[0]:#010x}")
        return (yield from self.slave_turn(timeout or 2000 * MS))


def mystery_gift_search(game, polls=30, emerald_config=0x003C0420):
    """What Emerald does from 'Wireless Communication' to giving up, as far as the
    adapter sees it, with nobody in range: reset + ID, Hello, Setup, broadcast data,
    search start, a run of polls (one per frame), search end, a wait the adapter has
    to time out, a status check, then sleep (ID again + 0x3D)."""
    acks = {}
    yield ("until_ready",)
    if (yield from game.check_id()) != 0x8001:
        raise LinkError("adapter not found")
    acks[0x10] = yield from game.command(0x10)
    acks[0x17] = yield from game.command(0x17, [emerald_config])
    acks[0x16] = yield from game.command(0x16, [0x00020002, 0x11223344, 0x55667788, 0x99AABBCC, 0xDDEEFF00, 0x0000FFFF])
    acks[0x1C] = yield from game.command(0x1C)
    for _ in range(polls):
        acks[0x1D] = yield from game.command(0x1D)
        yield ("delay", 16 * MS)
    acks[0x1E] = yield from game.command(0x1E)
    acks["wait"] = yield from game.change(0x27)
    acks[0x13] = yield from game.command(0x13)
    try:
        yield from game.command(0x99)                     # not a real command: must be refused politely
        acks[0x99] = "accepted"
    except LinkError:
        acks[0x99] = "rejected"
        game.errors.remove(next(e for e in game.errors if "0x99 rejected" in e))
    return acks


def go_to_sleep(game):
    if (yield from game.check_id(8)) != 0x8001:
        raise LinkError("adapter not found before sleep")
    return (yield from game.command(0x3D))
