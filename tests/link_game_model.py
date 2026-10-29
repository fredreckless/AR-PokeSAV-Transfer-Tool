"""A Pokemon Ruby/Sapphire on the GBA's ordinary link cable, receiving a Mystery Event - the
e-Reader route (docs/EREADER.md). Written for this project from the behaviour described there
(pret/pokeruby link.c and mystery_event_menu.c), as a generator driven by hwsim's multi-player
link model: ("mp_xfer", word) when the game is master, ("mp_slave", word, timeout) when it isn't.
"""

MASTER_HANDSHAKE, SLAVE_HANDSHAKE = 0x8FFF, 0xB9A0
CMD_LENGTH = 8
LINKTYPE_MYSTERY_EVENT = 0x5501
MAGIC = b"GameFreak inc."
LANG_JAPANESE, LANG_ENGLISH = 1, 2
QUEUE_CAPACITY = 50                       # link.c: received commands waiting for the game


class LinkError(Exception):
    pass


def handshake_count(words):
    """Players taking part in the handshake, counted from player 0 up (0xFFFF = nobody there)."""
    n = 0
    for w in words:
        if (w & ~3) == SLAVE_HANDSHAKE or w == MASTER_HANDSHAKE:
            n += 1
        else:
            if w != 0xFFFF:
                n = 0
            break
    return n


def link_player_block(trainer_id=0x0001E240, name=b"\xCC\xC9\xBB\xC3\xFF", language=LANG_ENGLISH,
                      link_type=LINKTYPE_MYSTERY_EVENT, version=2):
    """struct LinkPlayerBlock: magic, LinkPlayer (Ruby layout), magic - 60 bytes."""
    lp = bytearray(28)
    lp[0:2] = (version + 0x4000).to_bytes(2, "little")
    lp[4:8] = trainer_id.to_bytes(4, "little")
    lp[8:8 + len(name)] = name
    lp[0x14:0x18] = link_type.to_bytes(4, "little")
    lp[0x1A:0x1C] = language.to_bytes(2, "little")
    return MAGIC + b"\0\0" + bytes(lp) + MAGIC + b"\0\0"


def parse_link_player(block):
    if block[0:14] != MAGIC or block[44:58] != MAGIC:
        raise LinkError(f"link-player block without the magic: {bytes(block[:16])!r}")
    lp = block[16:44]
    return dict(version=int.from_bytes(lp[0:2], "little"), trainer_id=int.from_bytes(lp[4:8], "little"),
                name=bytes(lp[8:19]), link_type=int.from_bytes(lp[0x14:0x18], "little"),
                language=int.from_bytes(lp[0x1A:0x1C], "little"))


class LinkLayer:
    """One console's link layer once connected: commands of 8 words plus a checksum slot."""
    def __init__(self, my_id, players=2):
        self.my_id, self.players = my_id, players
        self.queue = []                       # commands to send (lists of 8 words)
        self.send_idx = self.recv_idx = 0
        self.checksum, self.checksum_ready = 0, False
        self.frame = [[0] * players for _ in range(CMD_LENGTH)]
        self.nonzero = 0
        self.received = []                    # complete commands: frame[word][player]
        self.loaded = 0                       # what goes out on the next transfer
        self.current = None
        self.bad_checksums = 0

    def on_transfer(self, words):
        words = words[:self.players]
        if self.send_idx == 0:                # the checksum slot
            if self.checksum_ready and any(w != self.checksum for w in words):
                self.bad_checksums += 1
            self.checksum, self.checksum_ready = 0, True
        else:
            for i, w in enumerate(words):
                self.frame[self.recv_idx][i] = w
                self.checksum = (self.checksum + w) & 0xFFFF
                self.nonzero |= w
            self.recv_idx += 1
            if self.recv_idx == CMD_LENGTH and self.nonzero:
                self.received.append([row[:] for row in self.frame])
                self.nonzero = 0
        # load the next word
        if self.send_idx == CMD_LENGTH:
            self.loaded = self.checksum
            self.current = None
        else:
            if self.send_idx == 0:
                self.current = self.queue.pop(0) if self.queue else [0] * CMD_LENGTH
            self.loaded = self.current[self.send_idx]
            self.send_idx += 1
        if self.recv_idx == CMD_LENGTH:
            self.send_idx = self.recv_idx = 0

    def send(self, *words):
        self.queue.append(list(words) + [0] * (CMD_LENGTH - len(words)))

    def send_block(self, data):
        self.send(0xBBBB, len(data), self.my_id + 128)
        for at in range(0, len(data), 14):
            chunk = data[at:at + 14].ljust(14, b"\0")
            self.send(0x8888, *[chunk[j] | chunk[j + 1] << 8 for j in range(0, 14, 2)])


class RubyMysteryEvent:
    """mystery_event_menu.c, as far as the link sees it."""
    def __init__(self, master=True, language=LANG_ENGLISH, press_a_after=30, pace=300, frame_blocks=25 * 228):
        """pace: blocks from a transfer's end to the next within a command cycle (Ruby's timer 3,
        0.75 ms); frame_blocks: one frame. As in link.c, a master starts one handshake transfer
        per frame and one 9-transfer cycle per frame, and the game takes in one received command
        per frame (LinkMain1 -> DequeueRecvCmds), with room for 50 waiting."""
        self.master, self.language = master, language
        self.press_a_after, self.pace, self.frame_blocks = press_a_after, pace, frame_blocks
        self.link_players = {}
        self.block = None
        self.log = []

    def _next_frame(self):
        t = yield ("now",)
        yield ("delay", self.frame_blocks - t % self.frame_blocks)

    def _xfer(self, word, cycle_end=True):
        if self.master:
            words = yield ("mp_xfer", word)
            if cycle_end:
                yield from self._next_frame()
            else:
                yield ("delay", self.pace)
        else:
            words = yield ("mp_slave", word, 40000)
            if words is None:
                raise LinkError("the other side never started a transfer")
        return words

    def run(self, max_transfers=20000):
        yield ("until_ready",)
        # the handshake
        loaded, prev, seen_two, n = SLAVE_HANDSHAKE, 0, 0, 0
        while True:
            words = yield from self._xfer(loaded)
            n += 1
            count = handshake_count(words)
            if count > 1 and count == prev and words[0] == MASTER_HANDSHAKE:
                break
            prev = count
            seen_two = seen_two + 1 if count == 2 else 0
            loaded = SLAVE_HANDSHAKE
            if self.master and seen_two == self.press_a_after:
                loaded = MASTER_HANDSHAKE                # the player pressed A
            if n > max_transfers:
                raise LinkError(f"no handshake (last words {[hex(w) for w in words]})")
        self.log.append(("connected", n))
        link = LinkLayer(0 if self.master else 1)
        if self.master:
            link.send(0x2222, LINKTYPE_MYSTERY_EVENT)
        pending, blocks, closing = set(), {}, set()
        own_block = link_player_block(language=self.language)
        sent_close = False
        rxq, last_frame = [], None
        for _ in range(max_transfers):
            # (a master's cycle ends after the transfer that loads the checksum: send index 8)
            words = yield from self._xfer(link.loaded, cycle_end=link.send_idx == CMD_LENGTH)
            link.on_transfer(words)
            if link.bad_checksums:
                raise LinkError("a command's checksum didn't match")
            rxq += link.received
            link.received = []
            if len(rxq) > QUEUE_CAPACITY:
                raise LinkError("the receive queue overflowed: commands came faster than one a frame")
            frame = (yield ("now",)) // self.frame_blocks
            if frame != last_frame and rxq:          # the main loop takes one command a frame
                last_frame = frame
                rxq_now = [rxq.pop(0)]
            else:
                rxq_now = []
            while rxq_now:
                cmd = rxq_now.pop(0)
                for i in range(2):
                    c = cmd[0][i]
                    if c == 0x2222:
                        pending = {0, 1}
                        if i == 0:                      # answered once, by every console
                            link.send_block(own_block)
                    elif c == 0xBBBB:
                        blocks[i] = dict(size=cmd[1][i], data=bytearray())
                    elif c == 0x8888 and i in blocks:
                        b = blocks[i]
                        for j in range(1, CMD_LENGTH):
                            b["data"] += cmd[j][i].to_bytes(2, "little")
                        if len(b["data"]) >= b["size"]:
                            data = bytes(b["data"][:b["size"]])
                            del blocks[i]
                            if i in pending:
                                pending.discard(i)
                                self.link_players[i] = parse_link_player(data)
                            elif i == (1 if self.master else 0):
                                self.block = data
                    elif c == 0x5FFF:
                        closing.add(i)
            if len(self.link_players) == 2 and not pending and "checked" not in self.log:
                lps = self.link_players
                if any(lp["link_type"] != lps[0]["link_type"] for lp in lps.values()):
                    raise LinkError("the link players' link types differ")
                if lps[0]["language"] != lps[1]["language"]:
                    raise LinkError("the languages don't match (CheckLanguageMatch)")
                self.log.append("checked")
            if self.block is not None and not sent_close:
                link.send(0x5FFF)
                sent_close = True
            if closing >= {0, 1}:
                return self.block
        raise LinkError(f"no Mystery Event arrived (link players {sorted(self.link_players)}, "
                        f"block {'yes' if self.block else 'no'})")
