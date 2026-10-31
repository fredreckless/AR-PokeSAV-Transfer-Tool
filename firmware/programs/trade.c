/* trade.c - the DUO's GBA plays the other player in a Pokemon Ruby/Sapphire Cable Club Trade
 * Center, over the ordinary GBA link cable, and trades the game one Pokemon: Stage 9,
 * docs/TRADE.md. The game checks it, shows it, asks the player, swaps it and saves it itself;
 * nothing is written to either cartridge by this program.
 *
 * The link engine (transfers, roles, master pacing, the PC) is ereader.c's, unchanged. On top:
 *   - every link session copies the game's link type and language into our link player, with
 *     lp_field_2 = 0 (0x8000 there would turn the game's National Dex on);
 *   - the Trade Center (0x1111): once the game sends room keys (0xCAFE), we send one a frame too,
 *     walking to our seat - player 1: up, up, right, up; player 0: up, up, left, up - then key 22
 *     ("seated");
 *   - the trade screen (0x1122): we answer block requests (0xCCCC) with our party, two Pokemon a
 *     block, then empty mail and gift ribbons, and keep the game's; as the master we ask for them;
 *     then the menu: we choose our Pokemon (0xAABB, or as master 0xDDDD) and say yes (0xBBBB, or
 *     as master 0xCCDD); after the trade, or if the player says no, we choose CANCEL (0xEEAA, or
 *     as master 0xEEBB / 0xEECC);
 *   - the trade scene (0x1144): 0xABCD when ready (as master, 0xDCBA once both are), and every
 *     "ready" barrier (0x2FFE) answered;
 *   - 0x5FFF answered, as always.
 * The PC loads: party count, our pick, trainer name and ID, 6 x 100-byte party, 220-byte mail. */
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;

typedef struct {
    void (*send8)(const void *);
    void (*recv8)(void *);
    u32 (*avail)(void);
    void (*idle)(void);
    void (*log)(const char *);
    void (*log1)(const char *, u32);
    void (*reply)(u32 cmd, u32 value);
    u32 (*crc32)(const void *, u32);
} api_t;

#define REG16(a) (*(volatile u16 *)(a))
#define SIOMULTI(i) REG16(0x04000120 + 2 * (i))
#define SIOCNT      REG16(0x04000128)
#define SIOMLT_SEND REG16(0x0400012A)
#define RCNT        REG16(0x04000134)
#define REG_IE      REG16(0x04000200)
#define REG_IF      REG16(0x04000202)
#define VCOUNT      REG16(0x04000006)

#define MULTI_MODE  0x2000
#define BAUD_115200 0x0003
#define CNT_SI      0x0004
#define CNT_ID_SHIFT 4
#define SI_LOW_NEEDED 16
#define CNT_START   0x0080
#define CNT_IRQ     0x4000
#define IF_SERIAL   0x0080
#define LINES_PER_FRAME 228
#define GAP_AFTER_DONE 13
#define LINK_UP_WAIT   (2 * LINES_PER_FRAME)

#define MASTER_HS 0x8FFF
#define SLAVE_HS  0xB9A0
#define CMD_LEN   8

#define LT_ROOM  0x1111
#define LT_TRADE 0x1122
#define LT_SCENE 0x1144

static void *memcpy(void *d, const void *s, unsigned n) { u8 *p = d; const u8 *q = s; while (n--) *p++ = *q++; return d; }
static void zero(void *d, unsigned n) { u8 *p = d; while (n--) *p++ = 0; }

static u32 lines_total, last_vcount;
static u32 now(void)
{
    u32 v = VCOUNT & 0xFF;
    u32 d = v >= last_vcount ? v - last_vcount : v + LINES_PER_FRAME - last_vcount;
    last_vcount = v;
    lines_total += d;
    return lines_total;
}

/* ------------------------------------------------------------------ what the PC loads */
#define BLOB_MAX (16 + 600 + 220)
static u32 blob_words[BLOB_MAX / 4], put_at, blob_ready;
#define BLOB ((u8 *)blob_words)
#define PARTY_COUNT BLOB[0]
#define OUR_PICK    BLOB[1]
#define OUR_NAME    (BLOB + 4)          /* 8 bytes, game charset */
#define OUR_TID     (BLOB + 12)         /* 4 bytes */
#define PARTY       (BLOB + 16)         /* 6 x 100 */
#define MAIL        (BLOB + 616)        /* 220 */

/* ------------------------------------------------------------------ progress, for the PC */
enum { ST_WAITING, ST_HANDSHAKE, ST_CONNECTED, ST_ROOM, ST_WALKING, ST_SEATED, ST_PARTIES, ST_MENU,
       ST_CHOSEN, ST_AGREED, ST_SCENE, ST_SAVING, ST_TRADED, ST_LEAVING, ST_DECLINED, ST_DONE };
static u32 state, is_master, transfers, bad_checksums, game_lp_type, game_lp_lang, stop_requested;
static u32 role_known, si_low_run, sessions, hs_run, max_gap;
static u8 game_player[60];

/* the trade's own story */
static u32 traded, declined, partner_pick, have_partner_pick, room_keys_seen, walk_frame;
static u32 requests_answered, blocks_from_game, barriers, scene_ready_seen, said_ready;
static u32 menu_timer, sent_choice, sent_yes, master_sent_dddd, game_choice, game_choice_seen;
static u32 game_said, game_said_seen;     /* the game's last menu message (as master we decide) */
static u32 game_blocks_total, req_sent, req_timer, leave_agreed;   /* as master: the block requests we make */
static u8 game_party[600];                /* the game's party, from its blocks */

/* ------------------------------------------------------------------ the link layer */
static u32 send_idx, recv_idx, checksum, checksum_ready, nonzero;
static u16 frame[CMD_LEN][2], current[CMD_LEN];
#define QN 4
static u16 qcmd[QN][3];                   /* small commands waiting to go: up to 3 words */
static u32 qhead, qcount;
static u16 loaded;

static u32 rx_size, rx_have, rx_on;       /* the game's block */
static u8 rx_buf[224];
static u32 player_pending;

static const u8 *tx_src;                  /* our outgoing block */
static u32 tx_len, tx_off, tx_on, tx_header, tx_kind;
static u8 our_player[60], msg[20], blank[40];

static void build_our_player(void)
{
    static const char magic[] = "GameFreak inc.";
    zero(our_player, 60);
    memcpy(our_player, magic, 14);
    memcpy(our_player + 44, magic, 14);
    u8 *lp = our_player + 16;
    lp[0] = game_player[16]; lp[1] = game_player[17];       /* the game's version; lp_field_2 stays 0 */
    memcpy(lp + 4, OUR_TID, 4);
    memcpy(lp + 8, OUR_NAME, 8);
    memcpy(lp + 0x14, game_player + 16 + 0x14, 4);           /* link type */
    memcpy(lp + 0x1A, game_player + 16 + 0x1A, 2);           /* language */
}

static void queue(u16 w0, u16 w1, u16 w2)
{
    if (qcount == QN) return;
    u16 *q = qcmd[(qhead + qcount) % QN];
    q[0] = w0; q[1] = w1; q[2] = w2;
    qcount++;
}

static void send_block(const u8 *src, u32 len, u32 kind)
{
    tx_src = src; tx_len = len; tx_off = 0; tx_header = 0; tx_on = 1; tx_kind = kind;
}

static void send_msg(u16 w0, u16 w1)      /* a 20-byte trade message */
{
    zero(msg, 20);
    msg[0] = w0; msg[1] = w0 >> 8; msg[2] = w1; msg[3] = w1 >> 8;
    send_block(msg, 20, 2);
}

static u16 room_key(void)                 /* our key for this frame in the Trade Center */
{
    /* after the room has settled: a step, then nothing until it's over (16 frames), four times;
     * then "seated" (22), once the game has seen us step onto the seat */
    static const u8 walk1[4] = { 19, 19, 21, 19 }, walk0[4] = { 19, 19, 20, 19 };
    u32 f = walk_frame++;
    if (f < 40) return 17;
    f -= 40;
    if (f < 4 * 20) {
        if (f % 20) return 17;
        return is_master ? walk0[f / 20] : walk1[f / 20];
    }
    if (f == 4 * 20 + 10) { state = ST_SEATED; return 22; }
    return 17;
}

static void next_command(void)
{
    for (u32 i = 0; i < CMD_LEN; i++) current[i] = 0;
    if (qcount) {
        u16 *q = qcmd[qhead];
        current[0] = q[0]; current[1] = q[1]; current[2] = q[2];
        qhead = (qhead + 1) % QN; qcount--;
        return;
    }
    if (tx_on) {
        if (!tx_header) {
            current[0] = 0xBBBB; current[1] = tx_len; current[2] = (is_master ? 0 : 1) + 128;
            tx_header = 1;
            return;
        }
        current[0] = 0x8888;
        for (u32 j = 0; j < 7; j++) {
            u32 at = tx_off + 2 * j;
            u32 lo = at < tx_len ? tx_src[at] : 0, hi = at + 1 < tx_len ? tx_src[at + 1] : 0;
            current[1 + j] = lo | hi << 8;
        }
        tx_off += 14;
        if (tx_off >= tx_len) tx_on = 0;
        return;
    }
    if (game_lp_type == LT_ROOM && room_keys_seen) {             /* the room: a key every frame */
        current[0] = 0xCAFE;
        current[1] = room_key();
        if (state < ST_WALKING) state = ST_WALKING;
    }
}

static void answer_request(u32 n)         /* 0xCCCC n: our block of that size */
{
    if (n == 1) {
        u32 k = requests_answered < 3 ? requests_answered : 2;
        send_block(PARTY + 200 * k, 200, 1);
        requests_answered++;
    } else if (n == 3) {
        send_block(MAIL, 220, 3);
    } else {
        send_block(blank, n == 4 ? 40 : n == 2 ? 100 : 200, 4);
    }
}

static void game_block(u32 size)          /* a whole block from the game has arrived */
{
    if (size == 60 && player_pending) {
        memcpy(game_player, rx_buf, 60);
        game_lp_type = game_player[16 + 0x14] | game_player[16 + 0x15] << 8;
        game_lp_lang = game_player[16 + 0x1A];
        build_our_player();
        send_block(our_player, 60, 0);
        player_pending = 0;
        if (game_lp_type == LT_ROOM && state < ST_ROOM) state = ST_ROOM;
        if (game_lp_type == LT_TRADE) state = traded ? ST_LEAVING : ST_PARTIES;
        if (game_lp_type == LT_SCENE) state = ST_SCENE;
        return;
    }
    if (size != 20) game_blocks_total++;
    if (size == 200 && blocks_from_game < 3) {
        memcpy(game_party + 200 * blocks_from_game, rx_buf, 200);
        blocks_from_game++;
        return;
    }
    if (size == 20) {                                            /* a trade message */
        game_said = rx_buf[0] | rx_buf[1] << 8;
        game_said_seen = 1;
        if (game_said == 0xAABB) { game_choice = rx_buf[2]; game_choice_seen = 1; }
        if (game_said == 0xDDDD) { partner_pick = rx_buf[2]; have_partner_pick = 1; }
        if (game_said == 0xABCD) scene_ready_seen = 1;
        if (game_said == 0xDCBA) state = ST_SAVING;
        if (game_said == 0xCCDD) state = ST_AGREED;
        if (game_said == 0xDDEE || game_said == 0xEECC) { declined = 1; state = ST_DECLINED; sent_choice = 0; }
        if (game_said == 0xEEBB) leave_agreed = 1;                /* done once the link closes */
    }
}

static void process_command(void)
{
    u32 game = is_master ? 1 : 0;
    u16 c = frame[0][game];
    if (c == 0x2222 || frame[0][is_master ? 0 : 1] == 0x2222)
        player_pending = 1;
    if (frame[0][is_master ? 0 : 1] == 0xCCCC)                  /* our own request, as master */
        answer_request(frame[1][is_master ? 0 : 1]);
    switch (c) {
    case 0xBBBB:
        rx_size = frame[1][game] <= sizeof rx_buf ? frame[1][game] : sizeof rx_buf;
        rx_have = 0; rx_on = 1;
        break;
    case 0x8888:
        if (!rx_on) break;
        for (u32 j = 1; j < CMD_LEN && rx_have < rx_size; j++) {
            rx_buf[rx_have++] = frame[j][game];
            if (rx_have < rx_size) rx_buf[rx_have++] = frame[j][game] >> 8;
        }
        if (rx_have >= rx_size) { rx_on = 0; game_block(rx_size); }
        break;
    case 0xCCCC:
        answer_request(frame[1][game]);
        break;
    case 0xCAFE:
        room_keys_seen = 1;
        break;
    case 0x2FFE:
        queue(0x2FFE, 0, 0);                                     /* "ready": we are */
        barriers++;
        break;
    case 0x5FFF:
        queue(0x5FFF, 0, 0);
        if (leave_agreed) state = ST_DONE;
        if (state == ST_SAVING && !traded) {                     /* the trade is saved: swap our copy too */
            state = ST_TRADED; traded = 1;
            memcpy(PARTY + 100 * OUR_PICK, game_party + 100 * partner_pick, 100);
        }
        break;
    }
}

static void each_cycle(void)              /* once a command cycle (a frame): the trade's decisions */
{
    if (tx_on || qcount) return;
    /* as master, we ask for the blocks: parties x3, mail, gift ribbons - each once the last is in */
    if (is_master && game_lp_type == LT_TRADE && (state == ST_PARTIES || state == ST_LEAVING) && req_sent < 5) {
        static const u8 seq[5] = { 1, 1, 1, 3, 4 };
        if (game_blocks_total >= req_sent && ++req_timer > (req_sent ? 12 : 40)) {
            queue(0xCCCC, seq[req_sent], 0);
            req_sent++; req_timer = 0;
        }
        return;
    }
    if (game_lp_type == LT_TRADE && game_blocks_total >= 5 && state >= ST_PARTIES && state <= ST_DECLINED) {
        if (state == ST_PARTIES) { state = ST_MENU; menu_timer = 0; }
        menu_timer++;
        int leaving = traded || declined;
        if (!is_master) {
            if (!sent_choice && menu_timer > 60) {
                send_msg(leaving ? 0xEEAA : 0xAABB, OUR_PICK);
                sent_choice = 1;
                if (!leaving) state = ST_CHOSEN;
            } else if (have_partner_pick && !sent_yes && !leaving && menu_timer > 150) {
                send_msg(0xBBBB, 0);
                sent_yes = 1;
            }
        } else {
            /* as master: the game proposes, we decide */
            if (game_said_seen) {
                game_said_seen = 0;
                if (game_said == 0xAABB && !leaving) {
                    partner_pick = game_choice; have_partner_pick = 1;
                    send_msg(0xDDDD, OUR_PICK);
                    master_sent_dddd = 1; state = ST_CHOSEN;
                } else if (game_said == 0xAABB) {
                    send_msg(0xEECC, 0);                         /* we've chosen CANCEL */
                } else if (game_said == 0xEEAA) {
                    send_msg(0xEEBB, 0); leave_agreed = 1;
                } else if (game_said == 0xBBBB && master_sent_dddd) {
                    send_msg(0xCCDD, 0); state = ST_AGREED;
                } else if (game_said == 0xBBCC) {
                    send_msg(0xDDEE, 0); declined = 1; state = ST_DECLINED;
                }
            }
        }
    }
    if (game_lp_type == LT_SCENE && state == ST_SCENE) {
        if (!said_ready && ++menu_timer > 60) { send_msg(0xABCD, 0); said_ready = 1; }
        if (is_master && said_ready && scene_ready_seen) { send_msg(0xDCBA, 0); scene_ready_seen = 0; state = ST_SAVING; }
    }
}

static void new_session(void)             /* the link is up again: forget the last one's blocks */
{
    rx_on = tx_on = player_pending = 0;
    qcount = 0;
    blocks_from_game = requests_answered = game_blocks_total = req_sent = req_timer = 0;
    sent_choice = sent_yes = master_sent_dddd = 0;
    game_said_seen = game_choice_seen = 0;
    said_ready = scene_ready_seen = 0;
    menu_timer = 0;
}

static u32 master_session_type(void)      /* as master, which link this is */
{
    static const u16 order[4] = { LT_ROOM, LT_TRADE, LT_SCENE, LT_TRADE };
    return order[(sessions - 1) & 3];
}

static void on_transfer(void)
{
    u16 w[2] = { SIOMULTI(0), SIOMULTI(1) };
    transfers++;
    is_master = (SIOCNT >> CNT_ID_SHIFT & 3) == 0;
    role_known = 1;
    if (state == ST_HANDSHAKE || state == ST_WAITING || hs_run == 0xFFFF) {
        static u32 prev, seen_two;
        u32 n = 0;
        for (u32 i = 0; i < 2; i++) {
            if ((w[i] & ~3) == SLAVE_HS || w[i] == MASTER_HS) n++;
            else break;
        }
        if (n > 1 && n == prev && w[0] == MASTER_HS) {
            sessions++; hs_run = 0;
            send_idx = recv_idx = 0; checksum_ready = 0; nonzero = 0;
            new_session();
            if (state < ST_CONNECTED) state = ST_CONNECTED;
            if (is_master) queue(0x2222, master_session_type(), 0);
            loaded = 0;
            SIOMLT_SEND = loaded;
            return;
        }
        prev = n;
        seen_two = n == 2 ? seen_two + 1 : 0;
        loaded = is_master && seen_two >= 20 ? MASTER_HS : SLAVE_HS;
        SIOMLT_SEND = loaded;
        return;
    }
    u16 gw = w[is_master ? 1 : 0];
    hs_run = ((gw & ~3) == SLAVE_HS || gw == MASTER_HS) ? hs_run + 1 : 0;
    if (hs_run >= 3) {                                           /* the game opened a new link */
        hs_run = 0xFFFF;
        rx_on = tx_on = qcount = player_pending = 0;
        loaded = SLAVE_HS;
        SIOMLT_SEND = loaded;
        return;
    }
    if (send_idx == 0) {
        if (checksum_ready && (w[0] != checksum || w[1] != checksum) && !hs_run)   /* (not a new link starting) */
            bad_checksums++;
        checksum = 0; checksum_ready = 1;
    } else {
        frame[recv_idx][0] = w[0]; frame[recv_idx][1] = w[1];
        checksum = (checksum + w[0] + w[1]) & 0xFFFF;
        nonzero |= w[0] | w[1];
        recv_idx++;
        if (recv_idx == CMD_LEN && nonzero) {
            process_command();
            nonzero = 0;
        }
    }
    if (send_idx == CMD_LEN) {
        loaded = checksum;
    } else {
        if (send_idx == 0) { each_cycle(); next_command(); }
        loaded = current[send_idx++];
    }
    if (recv_idx == CMD_LEN)
        send_idx = recv_idx = 0;
    SIOMLT_SEND = loaded;
}

/* ------------------------------------------------------------------ the PC */
static u8 pkt[8];
static int pc_service(api_t *api)
{
    if (!api->avail())
        return 0;
    api->recv8(pkt);
    u32 arg = pkt[4] | pkt[5] << 8 | pkt[6] << 16 | (u32)pkt[7] << 24, v, idx = arg >> 8;
    if (pkt[3] == 0x2B) {
        api->reply(0x2B, state | transfers << 8);
        stop_requested = 1;
        return 1;
    }
    if (pkt[3] == 0x2C) {
        if (put_at < sizeof blob_words / 4)
            blob_words[put_at++] = arg;
        api->reply(0x2C, put_at);
        return 0;
    }
    switch (arg & 0xFF) {
    case 0: v = state | is_master << 4 | (sessions & 7) << 5 | (transfers & 0xFFFFFF) << 8; break;
    case 1: v = bad_checksums | barriers << 16; break;
    case 2: v = game_lp_type | game_lp_lang << 16; break;
    case 3: v = partner_pick | have_partner_pick << 8 | traded << 9 | declined << 10 | blocks_from_game << 12
                | requests_answered << 16; break;
    case 4: {                                                    /* the game's party, a word at a time */
        u32 at = 4 * idx;
        v = at + 3 < sizeof game_party ? game_party[at] | game_party[at + 1] << 8 | game_party[at + 2] << 16
                                         | (u32)game_party[at + 3] << 24 : 0;
        break;
    }
    case 5: {                                                    /* our party (after a trade: what we got) */
        u32 at = 4 * idx;
        v = at + 3 < 600 ? PARTY[at] | PARTY[at + 1] << 8 | PARTY[at + 2] << 16 | (u32)PARTY[at + 3] << 24 : 0;
        break;
    }
    case 8: v = max_gap; break;
    case 20: put_at = 0; blob_ready = 0; v = sizeof blob_words; break;
    case 21: v = api->crc32(blob_words, put_at * 4);
             blob_ready = put_at * 4 >= 16 + 600 + 220 && PARTY_COUNT >= 1 && PARTY_COUNT <= 6 && OUR_PICK < PARTY_COUNT;
             break;
    default: v = 0xFFFFFFFF; break;
    }
    api->reply(0x2A, v);
    return 0;
}

/* ------------------------------------------------------------------ entry */
__attribute__((section(".text.start")))
u32 start(api_t *api)
{
    u16 saved_ie = REG_IE, saved_rcnt = RCNT, saved_siocnt = SIOCNT;
    REG_IE = saved_ie & ~IF_SERIAL;
    RCNT = 0;
    SIOCNT = MULTI_MODE | BAUD_115200 | CNT_IRQ;
    REG_IF = IF_SERIAL;
    loaded = SLAVE_HS;
    SIOMLT_SEND = loaded;
    last_vcount = VCOUNT & 0xFF;
    u32 last_done = now(), next_cycle = last_done, in_flight = 0, started_at = 0;
    u32 last_iter = now();
    while (!stop_requested) {
        u32 t = now();
        if (state >= ST_CONNECTED && t - last_iter > max_gap)
            max_gap = t - last_iter;
        last_iter = t;
        if (!role_known) {
            si_low_run = SIOCNT & CNT_SI ? 0 : si_low_run < SI_LOW_NEEDED ? si_low_run + 1 : si_low_run;
            is_master = si_low_run >= SI_LOW_NEEDED;
        }
        int linked = state >= ST_CONNECTED && hs_run != 0xFFFF;
        if (REG_IF & IF_SERIAL) {
            REG_IF = IF_SERIAL;
            in_flight = 0;
            int was = linked;
            if (blob_ready) on_transfer();                       /* (only once the PC has loaded us) */
            last_done = now();
            if (!was && state >= ST_CONNECTED && hs_run != 0xFFFF)
                next_cycle = last_done + LINK_UP_WAIT;
        } else if (in_flight && now() - started_at > 2 * LINES_PER_FRAME) {
            in_flight = 0;
        } else if (blob_ready && is_master && !in_flight && !(SIOCNT & CNT_START) && now() - last_done >= GAP_AFTER_DONE) {
            int cycle_start = !linked || send_idx == 0;
            if (!cycle_start || (int)(now() - next_cycle) >= 0) {
                if (cycle_start)
                    next_cycle = now() + LINES_PER_FRAME;
                in_flight = 1; started_at = now();
                SIOCNT = MULTI_MODE | BAUD_115200 | CNT_IRQ | CNT_START;
            }
        }
        if (!(REG_IF & IF_SERIAL))
            pc_service(api);
    }
    SIOCNT = saved_siocnt & ~(CNT_START | CNT_IRQ);
    RCNT = saved_rcnt;
    REG_IF = IF_SERIAL;
    REG_IE = saved_ie;
    return state | transfers << 8;
}
