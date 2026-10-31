"""A Pokemon Ruby/Sapphire trading in a Cable Club Trade Center, over the GBA link cable - Stage 9
(docs/TRADE.md). Written for this project from pret/pokeruby (cable_club.c, overworld.c, trade.c,
link.c, data/scripts/cable_club.inc, the Trade Center's map and layout). Like link_game_model.py, it
is a generator driven by hwsim's multi-player link model, and it plays one player through:

  1. the Trade Center room (link type 0x1111): link players, the warp in, then lockstep - one
     0xCAFE key a frame from each player - walking to its seat and waiting (key 22) until both
     are seated; then the link closes;
  2. the trade screen (0x1122): link players, the parties (0xCCCC 1 x3, the mail 0xCCCC 3, the
     gift ribbons 0xCCCC 4), then the menu (0xAABB, 0xDDDD, 0xBBBB, 0xCCDD); the link closes;
  3. the trade scene (0x1144): link players, 0xABCD / 0xDCBA, the trade, then the shared save with
     three 0x2FFE barriers; the link closes;
  4. the trade screen again, where the player chooses CANCEL (0xEEAA, 0xEEBB).

The game is either end of the cable. What the other player (the DUO) does is checked as the game
would: walls and the seat in the room, link types, block sizes, every Pokemon's checksum, the
menu's order. `result` says what the game ended up with.
"""
import struct
from link_game_model import (LinkLayer, LinkError, handshake_count, link_player_block, parse_link_player,
                             MASTER_HANDSHAKE, SLAVE_HANDSHAKE, CMD_LENGTH, QUEUE_CAPACITY, LANG_ENGLISH)

LT_ROOM, LT_TRADE, LT_SCENE = 0x1111, 0x1122, 0x1144
BLOCK_REQUEST_SIZES = (200, 200, 100, 220, 40)        # link.c sBlockRequestLookupTable
KEY_NONE, KEY_DOWN, KEY_UP, KEY_LEFT, KEY_RIGHT, KEY_START, KEY_A = 17, 18, 19, 20, 21, 24, 25
KEY_SEATED, KEY_EXIT, KEY_FREE, KEY_UNSEAT = 22, 23, 26, 29
STEP_FRAMES = 16
# The Trade Center (layout TradeCenter, 12 x 9): '#' blocked. The table is at (5,5)-(6,5).
ROOM = ["############",
        "############",
        "############",
        "#..........#",
        "#..........#",
        "#....##....#",
        "#..........#",
        "#..........#",
        "#####..#####"]
SEATS = {(4, 5): 0, (7, 5): 1}                        # coordinate triggers, VAR_0x8005 = 0 / 1
SPAWN = {0: (5, 8), 1: (6, 8)}                        # player i on the door row, side by side
DIRS = {KEY_DOWN: (0, 1), KEY_UP: (0, -1), KEY_LEFT: (-1, 0), KEY_RIGHT: (1, 0)}
WALK_TO_SEAT = {0: [KEY_UP, KEY_UP, KEY_LEFT, KEY_UP], 1: [KEY_UP, KEY_UP, KEY_RIGHT, KEY_UP]}


def pk3_checksum_ok(mon):
    """A party Pokemon in the game's form: decrypt, sum (pokemon.c), as the game's checks do."""
    pid, otid = struct.unpack_from("<II", mon, 0)
    words = [w ^ pid ^ otid for w in struct.unpack_from("<12I", mon, 0x20)]
    plain = struct.pack("<12I", *words)
    return sum(struct.unpack("<24H", plain)) & 0xFFFF == struct.unpack_from("<H", mon, 0x1C)[0]


class Room:
    """overworld.c, as far as the lockstep sees it: each player's position, step and status."""
    def __init__(self):
        self.pos = dict(SPAWN)
        self.prev = dict(SPAWN)
        self.moving = {0: 0, 1: 0}
        self.status = {0: 0x80, 1: 0x80}
        self.seated_at = {}

    def blocked(self, who, x, y):
        if ROOM[y][x] == "#":
            return True
        other = 1 - who
        return (x, y) in (self.pos[other], self.prev[other])

    def apply(self, keys):
        """One frame's keys for both players (sub_8055218 -> sub_8054FF8, sub_8055BFC)."""
        for who in (0, 1):
            k = keys[who]
            if self.status[who] == 0x80 and not self.moving[who] and self.pos[who] in SEATS:
                self.status[who] = 0x81               # the seat's coordinate script runs
                self.seated_at[who] = self.pos[who]
            if k == KEY_SEATED:
                self.status[who] = 0x82
            elif k == KEY_EXIT:
                self.status[who] = 0x83
            elif k == KEY_FREE:
                self.status[who] = 0x80
            elif k == KEY_UNSEAT and self.status[who] == 0x82:
                self.status[who] = 0x81
            if self.moving[who]:
                self.moving[who] -= 1
                if not self.moving[who]:
                    self.prev[who] = self.pos[who]
            elif self.status[who] == 0x80 and k in DIRS:
                dx, dy = DIRS[k]
                x, y = self.pos[who][0] + dx, self.pos[who][1] + dy
                if not self.blocked(who, x, y):
                    self.prev[who], self.pos[who] = self.pos[who], (x, y)
                    self.moving[who] = STEP_FRAMES


class RubyTrade:
    def __init__(self, master=True, language=LANG_ENGLISH, party=None, pick=0, accept=True, version=2,
                 pace=300, frame_blocks=25 * 228, lp_field_2=0x8000):
        """party: the game's party, 100-byte Pokemon in the game's form (1-6); pick: which it
        trades; accept: whether the player says yes. lp_field_2: this game's own link-player
        field (0x8000 when it has the National Dex; trade.c turns the partner's National Dex on
        if it sees 0x8000 - the DUO must never send it)."""
        self.master, self.language, self.version = master, language, version
        self.party = [bytes(p) for p in (party or [])]
        assert 1 <= len(self.party) <= 6
        self.pick, self.accept = pick, accept
        self.pace, self.frame_blocks = pace, frame_blocks
        self.lp_field_2 = lp_field_2
        self.my_id = 0 if master else 1
        self.other = 1 - self.my_id
        self.log = []
        self.link_players = {}
        self.result = dict(partner_party=None, partner_pick=None, received=None, saved=False,
                           national_dex_enabled=False, partner_seat=None, gave=None)
        self.frame = 0

    # ------------------------------------------------------------ the link, as link.c runs it
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
            words = yield ("mp_slave", word, 400000)
            if words is None:
                raise LinkError("the other side never started a transfer")
        return words

    def _session(self, link_type, handler, max_frames=6000):
        """Open a link, exchange link players, then run `handler` one frame at a time until it
        closes the link. handler(session) is a generator yielding once per frame."""
        # the handshake (the master's player presses nothing: the link opens by itself)
        loaded, prev, seen_two, n = SLAVE_HANDSHAKE, 0, 0, 0
        while True:
            words = yield from self._xfer(loaded)
            n += 1
            count = handshake_count(words)
            if count > 1 and count == prev and words[0] == MASTER_HANDSHAKE:
                break
            prev = count
            seen_two = seen_two + 1 if count == 2 else 0
            loaded = MASTER_HANDSHAKE if self.master and seen_two >= 30 else SLAVE_HANDSHAKE
            if n > 400000:
                raise LinkError(f"no handshake for link type {link_type:#06x}")
        s = Session(self, link_type)
        gen = handler(s)
        rxq, last_frame, frames = [], None, 0
        if self.master:
            s.after(30, lambda: s.link.send(0x2222, link_type))
        while True:
            words = yield from self._xfer(s.link.loaded, cycle_end=s.link.send_idx == CMD_LENGTH)
            s.link.on_transfer(words)
            if s.link.bad_checksums:
                raise LinkError(f"a command's checksum didn't match (link type {link_type:#06x})")
            rxq += s.link.received
            s.link.received = []
            if len(rxq) > QUEUE_CAPACITY:
                raise LinkError("the receive queue overflowed")
            frame = (yield ("now",)) // self.frame_blocks
            if frame == last_frame:
                continue
            last_frame = frame
            frames += 1
            self.frame += 1
            s.cmd = rxq.pop(0) if rxq else None
            s.process()
            s.tick()
            try:
                next(gen)
            except StopIteration:
                pass
            if s.closed():
                return s
            if frames > max_frames:
                raise LinkError(f"stuck in link type {link_type:#06x}: {s.stage}")

    # ------------------------------------------------------------ the four stages
    def run(self):
        yield ("until_ready",)
        yield from self._session(LT_ROOM, self._room)
        yield from self._session(LT_TRADE, self._trade_screen)
        if self.result["partner_pick"] is None:
            return self.result                        # cancelled in the menu
        yield from self._session(LT_SCENE, self._scene)
        yield from self._session(LT_TRADE, self._leave)
        return self.result

    def _room(self, s):
        s.stage = "room: waiting for link players"
        while not s.players_checked:
            yield
        s.stage = "room: warping in"
        for _ in range(90):                           # the warp and the room's map load
            yield
        room = Room()
        mine = list(WALK_TO_SEAT[self.my_id])
        key_plan, wait = [], 0
        s.stage = "room: walking"
        sent_seated = False
        for frame in range(3000):
            # my key for this frame (sub_8055390): a step, then nothing until it ends
            if room.status[self.my_id] == 0x81 and not sent_seated:
                key = KEY_SEATED                      # the seat script: "Please wait" -> 22
                sent_seated = True
            elif wait:
                key, wait = KEY_NONE, wait - 1
            elif mine and room.status[self.my_id] == 0x80:
                key, wait = mine.pop(0), STEP_FRAMES + 2
            else:
                key = KEY_NONE
            s.link.send(0xCAFE, key)
            keys = s.keys_this_frame()
            if keys is not None:
                room.apply(keys)
            if all(v == 0x82 for v in room.status.values()):
                self.result["partner_seat"] = room.seated_at.get(self.other)
                self.log.append(("seated", frame))
                s.stage = "room: both seated, closing"
                s.close()
                return
            yield
        raise LinkError(f"the partner never sat down (at {room.pos[self.other]}, status {room.status[self.other]:#x})")

    def _trade_screen(self, s):
        s.stage = "trade screen: link players"
        while not s.players_checked:
            yield
        for _ in range(10):
            yield
        # the parties, then the mail and the gift ribbons (sub_8048D44)
        pieces = [(1, b"".join(self.party[0:2])), (1, b"".join(self.party[2:4])), (1, b"".join(self.party[4:6])),
                  (3, bytes(220)), (4, bytes(40))]
        got = []
        for n, data in pieces:
            s.stage = f"trade screen: block {len(got) + 1} of 5"
            s.send_buffer = data.ljust(BLOCK_REQUEST_SIZES[n], b"\0")
            for _ in range(2):
                yield
            if self.master:
                s.link.send(0xCCCC, n)
            while len(s.blocks_in) < 2:
                yield
            if len(s.blocks_in[self.other]) != BLOCK_REQUEST_SIZES[n]:
                raise LinkError(f"the partner's block was {len(s.blocks_in[self.other])} bytes, not {BLOCK_REQUEST_SIZES[n]}")
            got.append(s.blocks_in[self.other])
            s.blocks_in = {}
            yield
        party = got[0] + got[1] + got[2]
        mons = [party[i * 100:i * 100 + 100] for i in range(6)]
        count = 0
        for slot, m in enumerate(mons):               # CalculateEnemyPartyCount: every slot with a species
            if not any(m[:4]) and not any(m[0x20:0x50]):
                continue
            if slot != count:
                raise LinkError(f"the partner's party has a gap before slot {slot}")
            if not pk3_checksum_ok(m):
                raise LinkError(f"the partner's Pokemon {slot} fails its checksum: the game would show a BAD EGG")
            level, hp, max_hp = m[84], *struct.unpack_from("<HH", m, 86)
            if not level or not max_hp or hp > max_hp:
                raise LinkError(f"the partner's Pokemon {slot} has level {level}, HP {hp}/{max_hp}: "
                                "the game keeps those as sent")
            count += 1
        if not count:
            raise LinkError("the partner's party is empty")
        self.result["partner_party"] = mons[:count]
        # the menu: the player looks for a while, then chooses
        s.stage = "trade screen: menu"
        for _ in range(60):
            yield
        mine = self.pick
        partner = None
        if self.master:
            s.messages.clear()
            while partner is None:                    # wait for the slave's choice
                for m in s.take_messages():
                    if m[0] == 0xAABB:
                        partner = m[1]
                    elif m[0] == 0xEEAA:
                        raise LinkError("the partner chose CANCEL")
                yield
            if not 0 <= partner < count:
                raise LinkError(f"the partner chose Pokemon {partner}, but has {count}")
            for _ in range(5):
                yield
            s.send_message(0xDDDD, mine)
            for _ in range(121):                      # "Trade X for Y?" then YES/NO
                yield
            yes = None
            while yes is None:
                for m in s.take_messages():
                    if m[0] in (0xBBBB, 0xBBCC):
                        yes = m[0] == 0xBBBB
                yield
            if not (yes and self.accept):
                s.send_message(0xDDEE, 0)             # back to the menu; then the player leaves
                self.log.append("declined")
                yield from self._cancel(s)
                return
            s.send_message(0xCCDD, 0)
        else:
            s.send_message(0xAABB, mine)
            while partner is None:
                for m in s.take_messages():
                    if m[0] == 0xDDDD:
                        partner = m[1]
                    elif m[0] in (0xEEBB, 0xEECC, 0xDDEE):
                        raise LinkError(f"the partner said {m[0]:#06x} before choosing")
                yield
            if not 0 <= partner < count:
                raise LinkError(f"the partner chose Pokemon {partner}, but has {count}")
            for _ in range(121):
                yield
            s.send_message(0xBBBB if self.accept else 0xBBCC, 0)
            go = None
            while go is None:
                for m in s.take_messages():
                    if m[0] in (0xCCDD, 0xDDEE):
                        go = m[0] == 0xCCDD
                yield
            if not go:
                self.log.append("declined")
                yield from self._cancel(s)
                return
        self.result["partner_pick"] = partner
        s.stage = "trade screen: trading"
        for _ in range(16):
            yield
        s.close()

    def _scene(self, s):
        s.stage = "trade scene: link players"
        while not s.players_checked:
            yield
        s.stage = "trade scene: the animation"
        for _ in range(240):
            yield
        # the swap (sub_804BA94) happens here, in the game's own memory
        partner = self.result["partner_party"][self.result["partner_pick"]]
        self.result["gave"] = self.party[self.pick]
        self.result["received"] = partner
        if any(lp["field_2"] == 0x8000 for i, lp in self.link_players.items() if i != self.my_id):
            self.result["national_dex_enabled"] = True
        s.send_message(0xABCD)
        ready = set()
        while True:
            for i, m in s.take_messages(with_sender=True):
                if m[0] == 0xABCD:
                    ready.add(i)
                elif m[0] == 0xDCBA:
                    ready.add("go")
            if self.master and ready >= {0, 1} and "sent" not in ready:
                s.send_message(0xDCBA)
                ready.add("sent")
            if "go" in ready:
                break
            yield
        # the shared save (sub_804DC88): barrier, save, barrier, barrier
        for step, wait in (("before saving", 30), ("after saving", 60), ("after the save's last part", 60)):
            s.stage = f"trade scene: waiting for the partner {step}"
            yield from s.barrier()
            for _ in range(wait):
                yield
        self.result["saved"] = True
        s.stage = "trade scene: closing"
        s.close()

    def _cancel(self, s):
        """The player chooses CANCEL; the partner must too (0xEEAA), then 0xEEBB and the link closes."""
        s.stage = "trade screen: the player chooses CANCEL"
        for _ in range(30):
            yield
        if self.master:
            while True:
                ms = [m[0] for m in s.take_messages()]
                if 0xEEAA in ms:
                    break
                if 0xAABB in ms:
                    s.send_message(0xEECC, 0)         # "the partner has chosen CANCEL"
                yield
            s.send_message(0xEEBB, 0)
        else:
            s.send_message(0xEEAA, 0)
            while 0xEEBB not in [m[0] for m in s.take_messages()]:
                yield
        for _ in range(20):
            yield
        s.close()

    def _leave(self, s):
        s.stage = "trade screen again: link players"
        while not s.players_checked:
            yield
        for k, n in enumerate((1, 1, 1, 3, 4)):       # the parties again, after the trade
            s.stage = f"trade screen again: block {k + 1} of 5"
            s.send_buffer = bytes(BLOCK_REQUEST_SIZES[n])
            for _ in range(2):
                yield
            if self.master:
                s.link.send(0xCCCC, n)
            while len(s.blocks_in) < 2:
                yield
            s.blocks_in = {}
            yield
        s.stage = "trade screen again: the player chooses CANCEL"
        for _ in range(30):
            yield
        if self.master:
            done = False
            while not done:
                for m in s.take_messages():
                    if m[0] == 0xEEAA:
                        done = True
                    elif m[0] == 0xAABB:
                        raise LinkError("the partner wants another trade; the player is leaving")
                yield
            s.send_message(0xEEBB, 0)
        else:
            s.send_message(0xEEAA, 0)
            done = False
            while not done:
                for m in s.take_messages():
                    if m[0] == 0xEEBB:
                        done = True
                yield
        for _ in range(20):
            yield
        s.close()


class Session:
    """One link's worth of link.c: commands in, blocks, link players, block requests, barriers."""
    def __init__(self, game, link_type):
        self.game, self.link_type = game, link_type
        self.link = LinkLayer(game.my_id)
        self.cmd = None
        self.stage = "starting"
        self.blocks = {}
        self.blocks_in = {}                           # complete blocks this round, by sender
        self.messages = []                            # 20-byte trade messages: (sender, words)
        self.pending = set()
        self.players_checked = False
        self.send_buffer = bytes(200)
        self.closing, self.sent_close = set(), False
        self.barrier_seen = set()
        self.timers = []

    def after(self, frames, fn):
        self.timers.append([frames, fn])

    def tick(self):
        for t in self.timers:
            t[0] -= 1
            if t[0] <= 0:
                t[1]()
        self.timers = [t for t in self.timers if t[0] > 0]

    def keys_this_frame(self):
        if self.cmd is None:
            return None
        return [self.cmd[1][i] if self.cmd[0][i] == 0xCAFE else 0 for i in range(2)]

    def send_message(self, *words):
        data = b"".join(w.to_bytes(2, "little") for w in words).ljust(20, b"\0")
        self.link.send_block(data)

    def take_messages(self, with_sender=False):
        out = [(i, m) for i, m in self.messages if with_sender or i != self.game.my_id]
        self.messages = []
        return out if with_sender else [m for i, m in out]

    def close(self):
        if not self.sent_close:
            self.link.send(0x5FFF)
            self.sent_close = True

    def closed(self):
        return self.sent_close and self.closing >= {0, 1}

    def barrier(self):
        self.link.send(0x2FFE)
        while not self.barrier_seen >= {0, 1}:
            yield
        self.barrier_seen = set()

    def process(self):
        cmd = self.cmd
        if cmd is None:
            return
        g = self.game
        for i in range(2):
            c = cmd[0][i]
            if c == 0x2222:
                if cmd[1][i] != self.link_type:
                    raise LinkError(f"0x2222 asked for link type {cmd[1][i]:#06x}, expected {self.link_type:#06x}")
                self.pending = {0, 1}
                block = bytearray(link_player_block(language=g.language, link_type=self.link_type,
                                                    version=g.version))
                block[18:20] = g.lp_field_2.to_bytes(2, "little")
                self.link.send_block(bytes(block))
            elif c == 0xBBBB:
                self.blocks[i] = dict(size=cmd[1][i], data=bytearray())
            elif c == 0x8888 and i in self.blocks:
                b = self.blocks[i]
                for j in range(1, CMD_LENGTH):
                    b["data"] += cmd[j][i].to_bytes(2, "little")
                if len(b["data"]) >= b["size"]:
                    data = bytes(b["data"][:b["size"]])
                    del self.blocks[i]
                    if i in self.pending:
                        self.pending.discard(i)
                        lp = parse_link_player(data)
                        lp["field_2"] = int.from_bytes(data[18:20], "little")
                        g.link_players[i] = lp
                        if not self.pending:
                            self.check_players()
                    elif len(data) == 20:
                        self.messages.append((i, list(struct.unpack("<10H", data))))
                    else:
                        self.blocks_in[i] = data
            elif c == 0xCCCC:
                n = cmd[1][i]
                if n >= len(BLOCK_REQUEST_SIZES):
                    raise LinkError(f"a block request for type {n}")
                self.link.send_block(self.send_buffer[:BLOCK_REQUEST_SIZES[n]].ljust(BLOCK_REQUEST_SIZES[n], b"\0"))
            elif c == 0x2FFE:
                self.barrier_seen.add(i)
            elif c == 0x5FFF:
                self.closing.add(i)

    def check_players(self):
        lps = self.game.link_players
        for i, lp in lps.items():
            if lp["link_type"] != self.link_type:
                raise LinkError(f"player {i}'s link type is {lp['link_type']:#06x}, not {self.link_type:#06x}")
        if lps[0]["language"] != lps[1]["language"] and LANG_ENGLISH not in (lps[0]["language"], lps[1]["language"]):
            pass                                      # (the games allow mixed languages in trades)
        self.players_checked = True
