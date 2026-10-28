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


def parse_candidates(ack):
    """librfu's rfu_STC_readParentCandidateList: up to four hosts from a 0x1D/0x1E answer."""
    raw = b"".join(w.to_bytes(4, "little") for w in ack[1:])
    hosts = []
    for i in range(0, len(raw) - 27, 28):
        e = raw[i:i + 28]
        gname, uname = e[6:19], e[20:28]
        ok = (~e[19] & 0xFF) == (sum(gname[:8]) + sum(uname)) & 0xFF
        compat = gname[0] | gname[1] << 8
        hosts.append(dict(id=e[0] | e[1] << 8, slot=e[2], serial=(e[4] | e[5] << 8) & 0x7FFF, checksum_ok=ok,
                          uname=uname, gname=dict(language=compat & 0xF, hasNews=compat >> 4 & 1,
                                                  hasCard=compat >> 5 & 1, version=compat >> 10 & 0xF,
                                                  trainer=gname[2] | gname[3] << 8, activity=gname[10] & 0x7F,
                                                  started=gname[10] >> 7)))
    return hosts


def find_distributor(game, polls=150):
    """Emerald's Mystery Gift search (InitializeRfuLinkManager_JoinGroup + the union room's
    wonder-distributor filter): keep polling until a host with serial 0x7F7D and hasCard shows."""
    yield ("until_ready",)
    if (yield from game.check_id()) != 0x8001:
        raise LinkError("adapter not found")
    yield from game.command(0x10)
    yield from game.command(0x17, [0x003C0420])
    yield from game.command(0x16, [0x00000002, 0, 0, 0, 0, 0xFFFFFFFF])
    yield from game.command(0x1C)
    for _ in range(polls):
        ack = yield from game.command(0x1D)
        for h in parse_candidates(ack):
            if h["checksum_ok"] and h["serial"] == 0x7F7D and h["gname"]["hasCard"] and not h["gname"]["started"]:
                yield from game.command(0x1E)
                return h
        yield ("delay", 16 * MS)
    raise LinkError("no distributor found")


def connect_to(game, host_id, polls=60):
    """rfu_LMAN_CHILD_connectParent: 0x1F, poll 0x20 until done, 0x21, then the clock swap."""
    yield from game.command(0x1F, [host_id])
    status = None
    for _ in range(polls):
        ack = yield from game.command(0x20)
        status = ack[1] >> 24 if len(ack) > 1 else None
        if status == 0:
            break
        yield ("delay", 16 * MS)
    ack = yield from game.command(0x21)
    word = ack[1]
    res = dict(status=word >> 24, child_id=word & 0xFFFF, slot=word >> 16 & 0xFF)
    if res["status"] == 0:
        res["swap"] = yield from game.change(0x27)
    return res


# ---------------------------------------------------------------------- stage C: the child's name over NI
# A port of librfu's NI sender (rfu_STC_setSendData_org, rfu_STC_NI_constructLLSF,
# rfu_STC_NI_receive_Sender, rfu_CB_sendData) and of the child's frame parsing
# (rfu_STC_CHILD_analyzeRecvPacket / rfu_STC_analyzeLLSF), for the child in slot 0.
LCOM_NULL, LCOM_NI_START, LCOM_NI, LCOM_NI_END, LCOM_UNI = 0, 1, 2, 3, 4
BUSY, SEND = 0x8000, 0x20
SEND_START, SENDING, SEND_LAST, SEND_NULL = BUSY | SEND | 1, BUSY | SEND | 2, BUSY | SEND | 3, BUSY | SEND | 0
SEND_SUCCESS = SEND | 6


class NISender:
    def __init__(self, data, sub_frame=0x0E, data_type=1):
        self.data = bytes(data)
        self.payload = sub_frame - 2                 # child sub-frame header: 2 bytes
        self.dataType, self.dataSize = data_type, len(self.data)
        ctrl = bytes([data_type]) + self.payload.to_bytes(2, "little") + self.dataSize.to_bytes(4, "little")
        self.ctrl = ctrl                              # the 7 bytes from &dataType
        self.src = ctrl                               # during SEND_START, now_p points into the control block
        self.now = [0, 0, 0, 0]
        self.remain = 7
        self.ack, self.phase = 0, 0
        self.n = [1, 1, 1, 1]
        self.recvAck = [0, 0, 0, 0]
        self.state = SEND_START
        self.frames_sent = 0

    def _buf(self):
        return self.ctrl if self.state == SEND_START else self.data

    def construct(self):
        """One child sub-frame (2-byte header + payload)."""
        buf = self._buf()
        if self.state == SENDING:
            for _ in range(4):
                if self.now[self.phase] < len(buf):
                    break
                self.phase = (self.phase + 1) & 3
        if self.state == SENDING:
            size = min(self.payload, len(buf) - self.now[self.phase])
        else:
            size = self.payload if self.remain >= self.payload else max(0, self.remain)
        h = (self.state & 0xF) << 10 | self.ack << 9 | self.phase << 5 | self.n[self.phase] << 7 | size
        out = h.to_bytes(2, "little") + buf[self.now[self.phase]:self.now[self.phase] + size]
        if self.state == SENDING:
            self.phase = (self.phase + 1) & 3
        self.frames_sent += 1
        return out

    def on_ack(self, slot_state, phase, n):
        """rfu_STC_NI_receive_Sender, for the host's acknowledgement frames."""
        if ((slot_state == LCOM_NI and self.state == SENDING) or (slot_state == LCOM_NI_START and self.state == SEND_START)
                or (slot_state == LCOM_NI_END and self.state == SEND_LAST)):
            if self.n[phase] == n:
                self.recvAck[phase] |= 1
        if self.recvAck[phase] & 1:
            self.n[phase] = (self.n[phase] + 1) & 3
            self.recvAck[phase] = 0
            if self.state in (SEND_START, SENDING):
                self.now[phase] += self.payload if self.state == SEND_START else self.payload << 2
                self.remain -= self.payload
                if self.remain <= 0:
                    self.phase = 0
                    if self.state == SEND_START:
                        self.n = [1, 1, 1, 1]
                        self.now = [self.payload * i for i in range(4)]
                        self.remain = self.dataSize
                        self.state = SENDING
                    else:
                        self.n[0] = 0
                        self.remain = 0
                        self.state = SEND_LAST
            elif self.state == SEND_LAST:
                self.state = SEND_NULL

    def after_send(self):
        """rfu_CB_sendData: a NULL frame having gone out ends the transfer."""
        if self.state == SEND_NULL:
            self.state = SEND_SUCCESS


def parse_host_frames(ack):
    """rfu_STC_CHILD_analyzeRecvPacket: the host's 3-byte-headed sub-frames from a 0x26 answer."""
    if len(ack) < 2:
        return []
    count = ack[1] & 0x7F
    raw = b"".join(w.to_bytes(4, "little") for w in ack[2:])
    out, at = [], 0
    while count - at >= 3:
        h = raw[at] | raw[at + 1] << 8 | raw[at + 2] << 16
        size = h & 0x7F
        out.append(dict(size=size, phase=h >> 9 & 3, n=h >> 11 & 3, ack=h >> 13 & 1, state=h >> 14 & 0xF,
                        slots=h >> 18 & 0xF, recv_first=h >> 22 & 3, data=raw[at + 3:at + 3 + size]))
        at += 3 + size
    return out


def child_name_bytes(serial=0x0002, trainer=0x4C1D, uname=b"\x60\x61\x62\xFF\xFF\xFF\xFF\xFF\x00"):
    """The child's RfuTgtData from serialNo on: serial (2), gname (13 + 2 padding), uname (9)."""
    gname = bytes([0x01 | 1 << 7, 0x0F]) + trainer.to_bytes(2, "little") + bytes(9)
    return serial.to_bytes(2, "little") + gname + bytes(2) + uname


def send_child_name(game, name, max_frames=120):
    """After connecting: each frame the adapter says 'data ready' (0x28), the child reads the
    host's frames (0x26), handles any acknowledgements, and answers with its own frame using
    'send, then wait' (0x25) - until librfu's NI sender reports success."""
    s = NISender(name)
    ready = None
    for _ in range(max_frames):
        ack = yield from game.command(0x26)
        for f in parse_host_frames(ack):
            if f["recv_first"] == 0 and f["slots"] & 1 and f["state"] != LCOM_UNI and f["ack"]:
                s.on_ack(f["state"], f["phase"], f["n"])
        if s.state == SEND_SUCCESS:
            return s
        sub = s.construct()
        pad = sub + bytes(-len(sub) % 4)
        words = [len(sub) << 8] + [int.from_bytes(pad[i:i + 4], "little") for i in range(0, len(pad), 4)]
        ready = yield from game.change(0x25, words)
        s.after_send()
        if s.state == SEND_SUCCESS:
            return s
        if ready != 0x99660028:
            raise LinkError(f"expected 'data ready', got {ready:#010x}")
    raise LinkError(f"name not delivered (sender state {s.state:#06x})")
