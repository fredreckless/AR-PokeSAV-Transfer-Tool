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
            self.log.append(("retry", cmd, len(data)))

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
                h["words"] = list(ack)
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


# ---------------------------------------------------------------------- stage D1: "you may join" over NI
RECV = 0x40
RECV_START, RECEIVING, RECV_LAST, RECV_SUCCESS = BUSY | RECV | 1, BUSY | RECV | 2, BUSY | RECV | 3, RECV | 6


class NIReceiver:
    """librfu's NI receiving half on the child (rfu_STC_NI_receive_Receiver and the two
    initSlot functions), with a user receive buffer (link_rfu_2: childRecvStatus, 1 byte)."""
    def __init__(self, buf_size=1):
        self.buf_size = buf_size
        self.state, self.ack, self.phase, self.n = 0, 0, 0, [0, 0, 0, 0]
        self.remain, self.ctrl, self.buf, self.off = 0, bytearray(7), bytearray(buf_size), [0, 0, 0, 0]
        self.payload = self.size = self.type = 0

    def _store(self, phase, data):
        tgt = self.buf if self.state == RECEIVING else self.ctrl
        for b in data:
            if self.off[phase] < len(tgt):
                tgt[self.off[phase]] = b
            self.off[phase] += 1

    def receive(self, slot_state, phase, n, data):
        check = False
        if slot_state == LCOM_NI_END:
            self.end_seen = True
            if self.state == RECEIVING:
                self.phase, self.n[0], self.state = 0, 0, RECV_LAST
        elif slot_state == LCOM_NI:
            if self.state == RECV_START and self.remain == 0:
                self.type = self.ctrl[0]
                self.payload = int.from_bytes(self.ctrl[1:3], "little")
                self.size = int.from_bytes(self.ctrl[3:7], "little")
                if self.size > self.buf_size:
                    raise LinkError("NI receive buffer overflow")
                self.n = [0, 0, 0, 0]
                self.off = [self.payload * w for w in range(4)]
                self.remain, self.state = self.size, RECEIVING
            check = self.state == RECEIVING
        elif slot_state == LCOM_NI_START:
            if self.state != RECV_START:
                self.state, self.remain, self.ack, self.payload, self.off = RECV_START, 7, 1, 0, [0, 0, 0, 0]
            check = True
        if check and n == ((self.n[phase] + 1) & 3):
            self._store(phase, data)
            if self.state == RECEIVING:
                self.off[phase] += 3 * self.payload
            self.remain -= len(data)
            self.n[phase] = n
        self.phase = phase

    def ack_frame(self):
        h = (self.state & 0xF) << 10 | self.ack << 9 | self.phase << 5 | self.n[self.phase] << 7
        return h.to_bytes(2, "little")


def wait_join_status(game, max_frames=120):
    """RFUSTATE_CHILD_TRY_JOIN: read the host's frames each time it says 'data ready', receive
    its NI status byte, acknowledging every chunk; done when librfu would mark it received."""
    r = NIReceiver(1)
    for _ in range(max_frames):
        ack = yield from game.command(0x26)
        frames = parse_host_frames(ack)
        r.end_seen = (ack[1] & 0x7F) == 0 if len(ack) > 1 else True
        for f in frames:
            if f["recv_first"] == 0 and f["slots"] & 1 and f["state"] != LCOM_UNI and not f["ack"]:
                r.receive(f["state"], f["phase"], f["n"], f["data"])
        if r.state == RECV_LAST and not r.end_seen:
            r.state = RECV_SUCCESS
            return r
        if r.state & BUSY:
            sub = r.ack_frame()
            pad = sub + bytes(-len(sub) % 4)
            words = [len(sub) << 8] + [int.from_bytes(pad[i:i + 4], "little") for i in range(0, len(pad), 4)]
            ready = yield from game.change(0x25, words)
        else:
            ready = yield from game.change(0x27)                 # nothing to send: just hand over the clock
        if ready != 0x99660028:
            raise LinkError(f"expected 'data ready', got {ready:#010x}")
    raise LinkError(f"join status not received (receiver state {r.state:#06x})")


# ---------------------------------------------------------------------- stage D2: link_rfu_2's child
# Ports from the decomp: link_rfu_2.c (MSCCallback_Child, RfuMain1_Child, RfuHandleReceiveCommand,
# the block send functions, Task_PlayerExchange, Rfu_LinkStandby, the close-link callbacks),
# link_rfu_3.c (the queues), link.c (LocalLinkPlayerToBlock / LinkPlayerFromBlock),
# mystery_gift_link.c and mystery_gift_client.c, util.c (CalcCRC16WithTable).
CMD_SEND_PACKET, CMD_READY_CLOSE, CMD_READY_EXIT = 0x2F00, 0x5F00, 0x6600
CMD_PLAYER_IDS, CMD_PLAYER_IDS_NEW = 0x7700, 0x7800
CMD_BLOCK_INIT, CMD_BLOCK, CMD_BLOCK_REQ, CMD_DISCONNECT = 0x8800, 0x8900, 0xA100, 0xED00
COMM_SLOT_LENGTH, MAX_RFU_PLAYERS, UNI_RECV_SIZE = 14, 5, 70
RECV_READY, RECV_RECEIVING, RECV_FINISHED = 0, 1, 2
BLOCK_REQUEST_SIZES = [200, 200, 100, 220, 40]
MAGIC = b"GameFreak inc."


def crc16(data):
    crc = 0x1121
    for b in data:
        t = (crc ^ b) & 0xFF
        for _ in range(8):
            t = (t >> 1) ^ 0x8408 if t & 1 else t >> 1
        crc = (crc >> 8) ^ t
    return ~crc & 0xFFFF


def link_player_block(trainer_id=0x0003D4C1, name=b"\x60\x61\x62\xFF\xFF\xFF\xFF\xFF", language=1):
    """LocalLinkPlayerToBlock for a Japanese Emerald: magic, struct LinkPlayer, magic (60 bytes)."""
    lp = (bytes([3, 0x40, 0, 0x80]) + trainer_id.to_bytes(4, "little") + name + bytes([0, 0, 0, 0])
          + bytes(4) + bytes(2) + language.to_bytes(2, "little"))
    assert len(lp) == 28
    return MAGIC + bytes(2) + lp + MAGIC + bytes(2)


class ChildLink:
    """The game's link_rfu_2 + librfu UNI, child in slot 0 (as link player 1 of 2)."""
    def __init__(self, player_block=None):
        self.recv_queue, self.recv_full, self.send_queue = [], False, []
        self.child_send_count, self.child_send_buffer, self.uni_ready = 0, bytes(14), False
        self.send_cmd = [0] * 8
        self.recv_cmds = [[0] * 8 for _ in range(MAX_RFU_PLAYERS)]
        self.recv_block = [dict(next=0, count=0, owner=0, flags=0, receiving=RECV_READY) for _ in range(MAX_RFU_PLAYERS)]
        self.block_received = [False] * MAX_RFU_PLAYERS
        self.block_recv_buffer = [bytearray(256) for _ in range(MAX_RFU_PLAYERS)]
        self.block_send_buffer = bytearray(256)
        self.send_block = dict(next=0, count=0, payload=b"", sending=False, owner=0, failed=0)
        self.callback = None
        self.player_count, self.multiplayer_id, self.link_player_idx = 0, 0, [0] * 4
        self.child_send_cmd_id = 0
        self.ready_exit_standby, self.all_ready_num, self.resend_exit_timer = [False] * 5, 0, 0
        self.ready_close_link = [False] * 5
        self.received_remote_link_players = False
        self.exchange_state, self.exchange_active = 0, True
        self.link_players = [None] * MAX_RFU_PLAYERS
        self.local_block = player_block or link_player_block()
        self.disconnect_mode, self.close_task = 0, False
        self.errors, self.cmds_seen = [], []
        self.parent_packets = 0

    # -- librfu / MSCCallback_Child --------------------------------------------------------
    def msc(self, ack):
        self.child_send_buffer = bytes(14)
        new = None
        for f in parse_host_frames(ack):
            if f["recv_first"] == 0 and f["slots"] & 1 and f["state"] == LCOM_UNI:
                if f["size"] <= UNI_RECV_SIZE:                     # rfu_STC_UNI_receive
                    new = f["data"] + bytes(UNI_RECV_SIZE - len(f["data"]))
                else:
                    self.errors.append("UNI frame too big for the receive buffer")
        if new is None:
            return
        self.parent_packets += 1
        self.child_send_count += 1
        if len(self.recv_queue) < 32:                               # RfuRecvQueue_Enqueue
            if any(new[i] or new[i + 1] for i in range(0, UNI_RECV_SIZE, COMM_SLOT_LENGTH)):
                self.recv_queue.append(new)
        else:
            self.recv_full = True
        if self.send_queue:                                         # UpdateBackupQueue
            self.child_send_buffer = self.send_queue.pop(0)
        self.uni_ready = True                                       # rfu_UNI_readySendData

    def packet(self):
        """rfu_REQ_sendData(TRUE) for the child: the UNI sub-frame if one is ready, else just
        hand the clock over (STWI_send_MS_ChangeREQ)."""
        if not self.uni_ready:
            return 0x27, []
        self.uni_ready = False
        sub = (LCOM_UNI << 10 | COMM_SLOT_LENGTH).to_bytes(2, "little") + self.child_send_buffer
        return 0x25, [len(sub) << 8] + [int.from_bytes(sub[i:i + 4], "little") for i in range(0, len(sub), 4)]

    # -- RfuMain1_Child --------------------------------------------------------------------
    def main(self):
        if self.recv_queue and not self.recv_full:
            recv = self.recv_queue.pop(0)
        else:
            recv = bytes(UNI_RECV_SIZE)
        for i in range(MAX_RFU_PLAYERS):
            for j in range(7):
                self.recv_cmds[i][j] = recv[i * 14 + 2 * j] | recv[i * 14 + 2 * j + 1] << 8
        for i in range(MAX_RFU_PLAYERS):
            if self.recv_cmds[i][0]:
                self.cmds_seen.append((i, self.recv_cmds[i][0]))
        self.handle_receive()
        if self.child_send_count:
            self.child_send_count -= 1
            if self.callback:
                self.callback()
            if self.send_cmd[0]:                                    # ChildBuildSendCmd
                self.send_cmd[0] |= self.child_send_cmd_id << 5
                self.child_send_cmd_id = (self.child_send_cmd_id + 1) & 7
                out = b"".join((c & 0xFFFF).to_bytes(2, "little") for c in self.send_cmd[:7])
            else:
                out = bytes(14)
            if any(out) and len(self.send_queue) < 40:              # RfuSendQueue_Enqueue
                self.send_queue.append(out)
            self.send_cmd = [0] * 8
        if self.exchange_active:
            self.player_exchange()

    def handle_receive(self):
        for i in range(MAX_RFU_PLAYERS):
            c = self.recv_cmds[i]
            kind = c[0] & 0xFF00
            rb = self.recv_block[i]
            if kind in (CMD_PLAYER_IDS, CMD_PLAYER_IDS_NEW):
                if kind == CMD_PLAYER_IDS_NEW and self.received_remote_link_players:
                    return
                self.player_count = c[1]
                ids = [c[2] & 0xFF, c[2] >> 8, c[3] & 0xFF, c[3] >> 8]
                self.link_player_idx = ids
                self.multiplayer_id = ids[0]                        # childSlot 0
            elif kind == CMD_BLOCK_INIT:
                if rb["receiving"] == RECV_READY:
                    rb.update(next=0, count=c[1], owner=c[2], flags=0, receiving=RECV_RECEIVING)
                    self.block_received[i] = False
            elif kind == CMD_BLOCK:
                if rb["receiving"] == RECV_RECEIVING:
                    n = c[0] & 0xFF
                    rb["next"] = n
                    rb["flags"] |= 1 << n
                    for j in range(6):
                        if n * 12 + 2 * j + 1 < 256:
                            self.block_recv_buffer[i][n * 12 + 2 * j:n * 12 + 2 * j + 2] = c[j + 1].to_bytes(2, "little")
                    if rb["flags"] == (1 << rb["count"]) - 1:
                        rb["receiving"] = RECV_FINISHED
                        self.block_received[i] = True               # Rfu_SetBlockReceivedFlag (child)
            elif kind == CMD_BLOCK_REQ:
                size = BLOCK_REQUEST_SIZES[c[1]] if c[1] < len(BLOCK_REQUEST_SIZES) else 0
                self.init_block_send(self.block_send_buffer, size)
            elif kind == CMD_READY_CLOSE:
                self.ready_close_link[i] = True
            elif kind == CMD_READY_EXIT:
                if self.all_ready_num == c[1]:
                    self.ready_exit_standby[i] = True
            elif kind == CMD_DISCONNECT:
                self.errors.append(f"disconnect command from player {i}")

    # -- blocks ----------------------------------------------------------------------------
    def init_block_send(self, src, size):
        if self.callback is not None or self.send_cmd[0] or self.send_block["sending"]:
            return False
        sb = self.send_block
        sb.update(owner=self.multiplayer_id, sending=True, count=size // 12 + (size % 12 != 0), next=0)
        if src is not self.block_send_buffer:
            self.block_send_buffer[:len(src[:256])] = src[:256]
        sb["payload"] = bytes(src) if size > 256 else bytes(self.block_send_buffer)
        self.prepare(CMD_BLOCK_INIT)
        self.callback = self.handle_block_send
        return True

    def prepare(self, cmd):                                         # RfuPrepareSendBuffer
        self.send_cmd[0] = cmd
        if cmd == CMD_BLOCK_INIT:
            self.send_cmd[1] = self.send_block["count"]
            self.send_cmd[2] = self.send_block["owner"] + 0x80
        elif cmd in (CMD_READY_EXIT, CMD_READY_CLOSE):
            self.send_cmd[1] = self.all_ready_num

    def handle_block_send(self):
        if self.send_cmd[0] == 0:
            self.prepare(CMD_BLOCK_INIT)
            if (self.recv_cmds[self.multiplayer_id][0] & 0xFF00) == CMD_BLOCK_INIT:
                self.callback = self.send_next_block

    def _chunk(self, n):
        p = self.send_block["payload"]
        return [CMD_BLOCK | n] + [(p[n * 12 + 2 * i] if n * 12 + 2 * i < len(p) else 0)
                                  | (p[n * 12 + 2 * i + 1] if n * 12 + 2 * i + 1 < len(p) else 0) << 8 for i in range(7)]

    def send_next_block(self):
        sb = self.send_block
        self.send_cmd = self._chunk(sb["next"])
        sb["next"] += 1
        if sb["count"] <= sb["next"]:
            sb["sending"] = False
            self.callback = self.send_last_block

    def send_last_block(self):
        sb, me = self.send_block, self.multiplayer_id
        self.send_cmd = self._chunk(sb["count"] - 1)
        if (self.recv_cmds[me][0] & 0xFF) == sb["count"] - 1:
            rb = self.recv_block[me]
            if rb["flags"] != (1 << rb["count"]) - 1:
                # HandleSendFailure: queue every chunk not yet echoed (raw, no sequence number).
                # (An empty echo row also has low byte 0, so this happens for 1-chunk blocks.)
                sb["failed"] += 1
                for n in range(sb["count"]):
                    if not (rb["flags"] >> n) & 1 and len(self.send_queue) < 40:
                        self.send_queue.append(b"".join(w.to_bytes(2, "little") for w in self._chunk(n)[:7]))
            else:
                self.callback = None

    def block_status(self):
        return sum(1 << i for i in range(MAX_RFU_PLAYERS)
                   if self.recv_block[i]["receiving"] == RECV_FINISHED and self.block_received[i])

    def reset_block_received(self, i):
        self.block_received[i] = False
        self.recv_block[i]["receiving"] = RECV_READY

    # -- Task_PlayerExchange (child path) ------------------------------------------------------
    def player_exchange(self):
        s = self.exchange_state
        if s == 0:
            if all(rb["receiving"] == RECV_READY for rb in self.recv_block):
                self.block_received = [False] * MAX_RFU_PLAYERS
                self.block_send_buffer[:len(self.local_block)] = self.local_block
                self.exchange_state = 1
        elif s == 1:
            self.exchange_state = 2
        elif s == 2:
            if self.player_count:
                self.exchange_state = 3
        elif s == 3:
            self.exchange_state = 4
        elif s == 4:
            if all(self.recv_block[i]["receiving"] == RECV_FINISHED and self.block_received[i]
                   for i in range(self.player_count)):
                self.exchange_state = 5
        elif s == 5:
            for i in range(self.player_count):
                block = bytes(self.block_recv_buffer[i][:60])
                self.link_players[i] = block[16:44]
                if block[:16].split(b"\0")[0] != MAGIC or block[44:60].split(b"\0")[0] != MAGIC:
                    self.errors.append(f"link player {i}: bad magic -> CB2_LinkError")
                    raise LinkError("link error (link player block)")
                self.reset_block_received(i)
            self.exchange_state = 6
        elif s == 6:
            self.received_remote_link_players = True
            self.exchange_active = False

    # -- standby and closing -------------------------------------------------------------------
    def set_standby(self):
        if self.callback is None:
            self.callback = self.link_standby
            self.resend_exit_timer = 0

    def link_standby(self):                                         # Rfu_LinkStandby, not the leader
        if not self.recv_queue and self.send_cmd[0] == 0:
            self.prepare(CMD_READY_EXIT)
            self.callback = self.send_ready_exit_until_all_ready

    def send_ready_exit_until_all_ready(self):
        if not self.recv_queue and self.resend_exit_timer > 60:
            self.prepare(CMD_READY_EXIT)
            self.resend_exit_timer = 0
        if all(self.ready_exit_standby[i] for i in range(self.player_count)):
            self.ready_exit_standby = [False] * 5
            self.all_ready_num += 1
            self.callback = None
        self.resend_exit_timer += 1

    def tasks(self):
        if self.close_task and self.callback is None:              # Task_TryReadyCloseLink
            self.close_task = False
            self.callback = self.send_ready_close

    def send_ready_close(self):
        if self.send_cmd[0] == 0 and not self.exchange_active:
            self.prepare(CMD_READY_CLOSE)
            self.callback = self.wait_all_ready_close

    def wait_all_ready_close(self):
        if sum(self.ready_close_link) == self.player_count:
            self.disconnect_mode = 1                                # TryDisconnectRfu (child)
            self.callback = None


class MGLink:
    """mystery_gift_link.c: blocks of up to 252 bytes behind a (ident, crc, size) header."""
    def __init__(self, child, send_id, recv_id):
        self.c, self.send_id, self.recv_id = child, send_id, recv_id
        self.state = 0

    def init_send(self, ident, data, size):
        self.state, self.send_ident, self.send_counter = 0, ident, 0
        self.send_size = size if size else 0x400
        self.send_buf = bytes(data) + bytes(max(0, self.send_size - len(data)))

    def init_recv(self, ident):
        self.state, self.recv_ident, self.recv_counter, self.recv_size = 0, ident, 0, 0
        self.recv_buf = bytearray(0x400)

    def _got(self, i):
        return (self.c.block_status() >> i) & 1

    def recv(self):
        c = self.c
        if self.state == 0:
            if self._got(self.recv_id):
                h = bytes(c.block_recv_buffer[self.recv_id][:6])
                ident, crc, size = (int.from_bytes(h[k:k + 2], "little") for k in (0, 2, 4))
                if size > 0x400 or ident != self.recv_ident:
                    raise LinkError(f"MG link: header ident {ident} size {size}, expected ident {self.recv_ident}")
                self.recv_size, self.recv_crc, self.recv_counter = size, crc, 0
                c.reset_block_received(self.recv_id)
                self.state = 1
        elif self.state == 1:
            if self._got(self.recv_id):
                at = self.recv_counter * 252
                n = min(252, self.recv_size - at)
                self.recv_buf[at:at + n] = c.block_recv_buffer[self.recv_id][:n]
                self.recv_counter += 1
                if self.recv_size - at <= 252:
                    self.state = 2
                c.reset_block_received(self.recv_id)
        elif self.state == 2:
            if crc16(self.recv_buf[:self.recv_size]) != self.recv_crc:
                raise LinkError("MG link: CRC mismatch -> LinkRfu_FatalError")
            self.state = 0
            return True
        return False

    def send(self):
        c = self.c
        if self.state == 0:
            if c.callback is None:
                head = self.send_ident.to_bytes(2, "little") + crc16(self.send_buf[:self.send_size]).to_bytes(2, "little") \
                    + self.send_size.to_bytes(2, "little")
                c.init_block_send(head, 6)
                self.state = 1
        elif self.state == 1:
            if c.callback is None and self._got(self.send_id):
                c.reset_block_received(self.send_id)
                at = 252 * self.send_counter
                n = min(252, self.send_size - at)
                c.init_block_send(self.send_buf[at:at + n], n)
                self.send_counter += 1
                if self.send_size - at <= 252:
                    self.state = 2
        elif self.state == 2:
            if c.callback is None:
                self.state = 3
        elif self.state == 3:
            if self._got(self.send_id):
                c.reset_block_received(self.send_id)
                self.state = 0
                return True
        return False


CLI_NONE, CLI_RETURN, CLI_RECV, CLI_SEND_LOADED, CLI_COPY_RECV = 0, 1, 2, 3, 4
CLI_LOAD_GAME_DATA, CLI_SAVE_CARD, CLI_SAVE_RAM_SCRIPT, CLI_SEND_READY_END = 8, 10, 17, 20
CLI_MSG_NOTHING_SENT, CLI_MSG_CARD_RECEIVED = 0, 2
MG_ID_CLIENT_SCRIPT, MG_ID_GAME_DATA, MG_ID_READY_END = 16, 17, 20


class MGClient:
    """mystery_gift_client.c, the commands a Wonder Card distributor uses."""
    def __init__(self, child, game_data=None):
        self.link = MGLink(child, 1, 0)
        self.func, self.param = "init", 0
        self.send_buf = bytearray(0x400)
        self.game_data = game_data or bytes(range(0x60))
        self.saved_card = self.saved_ram_script = None
        self.result = None
        self.history = []

    def run(self):
        f = self.func
        if f == "init":
            self.script, self.idx, self.func = [(CLI_RECV, MG_ID_CLIENT_SCRIPT), (CLI_COPY_RECV, 0)], 0, "run"
        elif f == "recv":
            if self.link.recv():
                self.func = "run"
        elif f == "send":
            if self.link.send():
                self.func = "run"
        elif f == "run":
            instr, p = self.script[self.idx] if self.idx < len(self.script) else (CLI_NONE, 0)
            self.idx += 1
            self.history.append((instr, p))
            if instr == CLI_RETURN:
                self.param, self.func, self.result = p, "done", p
            elif instr == CLI_RECV:
                self.link.init_recv(p)
                self.func = "recv"
            elif instr == CLI_SEND_LOADED:
                self.func = "send"
            elif instr == CLI_SEND_READY_END:
                self.link.init_send(MG_ID_READY_END, self.send_buf, 0)
                self.func = "send"
            elif instr == CLI_COPY_RECV:
                raw = bytes(self.link.recv_buf)
                self.script = [(int.from_bytes(raw[i:i + 4], "little"), int.from_bytes(raw[i + 4:i + 8], "little"))
                               for i in range(0, 0x400, 8)]
                self.idx = 0
            elif instr == CLI_LOAD_GAME_DATA:
                self.send_buf[:] = bytes(0x400)
                self.send_buf[:len(self.game_data)] = self.game_data
                self.link.init_send(MG_ID_GAME_DATA, self.send_buf, len(self.game_data))
            elif instr == CLI_SAVE_CARD:
                self.saved_card = bytes(self.link.recv_buf[:self.link.recv_size])
            elif instr == CLI_SAVE_RAM_SCRIPT:
                self.saved_ram_script = bytes(self.link.recv_buf[:1000])
            elif instr != CLI_NONE:
                raise LinkError(f"client script command {instr} not modelled")
        return self.func == "done"


def receive_gift(game, child, max_frames=3000, drop_every=0):
    """After joining: every frame the adapter says 'data ready', the game reads the host's packet,
    runs link_rfu_2 and its tasks (player exchange, standby, the Mystery Gift client), sends its
    own sub-frame and hands the clock back. Ends after the link is closed (0x30).
    drop_every=N: every Nth packet is lost on the way (read, then thrown away, and the game hands
    the clock straight back with nothing to say), as a failed transfer would leave it."""
    client, standby_set, closed = None, False, False
    child.dropped = 0
    for frame in range(max_frames):
        ack = yield from game.command(0x26)
        if drop_every and frame % drop_every == drop_every - 1:
            child.dropped += 1
            ready = yield from game.change(0x27)
            if ready != 0x99660028:
                raise LinkError(f"expected 'data ready', got {ready:#010x}")
            continue
        child.msc(ack)
        child.main()
        if child.received_remote_link_players and client is None:
            client = MGClient(child)                                # MG_STATE_CLIENT_LINK_WAIT
            if not standby_set:
                child.set_standby()                                 # Task_CardOrNewsOverWireless 13
                standby_set = True
        if client and client.result is None:
            if client.run():
                child.close_task = True                             # Rfu_SetCloseLinkCallback
        child.tasks()
        if child.disconnect_mode:
            # rfu_LMAN_requestChangeAgbClockMaster: keep the clock this time, then disconnect
            cmd, words = child.packet()
            yield from game.command(0x24, words if cmd == 0x25 else [])
            yield from game.command(0x30, [1])
            child.received_remote_link_players = False
            return client
        if child.errors:
            raise LinkError("; ".join(child.errors))
        cmd, words = child.packet()
        ready = yield from game.change(cmd, words)
        if ready != 0x99660028:
            raise LinkError(f"expected 'data ready', got {ready:#010x}")
    raise LinkError(f"gift not finished (exchange state {child.exchange_state}, "
                    f"client {client and client.func}, step {client and client.idx})")
