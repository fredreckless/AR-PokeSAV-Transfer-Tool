/* ereader.c - the DUO's GBA plays the Card e-Reader+ on the ordinary GBA link cable, handing a
 * Pokemon Ruby/Sapphire (or Japanese Emerald) a Mystery Event: roadmap stage 3, docs/EREADER.md.
 *
 * The link is the GBA's multi-player mode at 115 kbit/s. The cable decides who is master (player
 * 0, the purple plug of an AGB-005); this program works either way:
 *   - handshake: 0xB9A0 from slaves, 0x8FFF from the master; up once player 0 says 0x8FFF;
 *   - then commands of 8 words, each followed by a checksum slot (the sum of everyone's words);
 *   - 0x2222 asks for link players; each side answers with its 60-byte link-player block
 *     (0xBBBB size id+128, then 0x8888 + 7 words a command); we send ours after the game's, with
 *     its link type and language copied, so the game's checks always agree;
 *   - then the Mystery Event script from the PC, as one more block;
 *   - 0x5FFF from everyone closes the link.
 * Nothing is written to either cartridge. Without an event loaded, only the link players are
 * exchanged and the game times out by itself: a safe dry run. */
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
#define CNT_SI      0x0004               /* 0: we are the master (player 0) */
#define CNT_START   0x0080
#define CNT_IRQ     0x4000               /* sets IF bit 7 when a transfer completes */
#define IF_SERIAL   0x0080
#define LINES_PER_FRAME 228
/* As master we pace exactly like Ruby's own master (link.c), because a slave game needs it:
 *  - one transfer per frame while handshaking, and one 9-transfer command cycle per frame after:
 *    the game takes in (and sends) at most one command per frame;
 *  - within a cycle, the next transfer starts 0.75 ms after the last one *finished* (Ruby: timer 3,
 *    197 x 64 cycles, started at the end of its serial interrupt) - time for the slave's interrupt
 *    to load its next word. Lab session 24: 12 scanlines from the *start* (~0.44 ms after the end),
 *    and cycles back to back, gave checksum mismatches on 45% of commands. */
#define GAP_AFTER_DONE 13                /* scanlines (~0.83 ms) from a transfer's end to the next */
#define LINK_UP_WAIT   (2 * LINES_PER_FRAME)

#define MASTER_HS 0x8FFF
#define SLAVE_HS  0xB9A0
#define CMD_LEN   8

static void *memcpy(void *d, const void *s, unsigned n) { u8 *p = d; const u8 *q = s; while (n--) *p++ = *q++; return d; }

static u32 lines_total, last_vcount;
static u32 now(void)                     /* scanlines since start */
{
    u32 v = VCOUNT & 0xFF;
    u32 d = v >= last_vcount ? v - last_vcount : v + LINES_PER_FRAME - last_vcount;
    last_vcount = v;
    lines_total += d;
    return lines_total;
}

/* ------------------------------------------------------------------ what the PC loads */
#define EVENT_MAX 2004                   /* the game's shared buffer is cleared for 0x7D4 bytes */
static u32 event_words[(4 + EVENT_MAX + 3) / 4], put_at, event_ready;
#define EVENT_SIZE (event_words[0] & 0xFFFF)
#define EVENT ((const u8 *)event_words + 4)

/* ------------------------------------------------------------------ progress, for the PC */
enum { ST_WAITING, ST_HANDSHAKE, ST_CONNECTED, ST_GOT_PLAYER, ST_SENT_PLAYER, ST_SENT_EVENT, ST_CLOSED };
static u32 state, is_master, transfers, bad_checksums, game_lp_type, game_lp_lang, stop_requested;
static u32 sessions;                     /* link-ups so far (the game can start again after a close) */
static u32 hs_run;                       /* consecutive handshake words from the game while linked */

/* diagnostics for the PC (lab session 24/25): where checksum mismatches happen, and our own timing */
#define MM_N 4
static u32 mm_info[MM_N], mm_words[MM_N], mm_sum[MM_N], mm_n;
static u16 mm_by_state[8];
static u32 max_gap, events_sent;
static u8 game_player[60];

/* ------------------------------------------------------------------ the link layer */
static u32 send_idx, recv_idx, checksum, checksum_ready, nonzero;
static u16 frame[CMD_LEN][2], current[CMD_LEN], queue_cmd[CMD_LEN];
static u32 queue_full;                   /* one command waiting to go */
static u16 loaded;                       /* what goes out on the next transfer */

static u32 rx_size, rx_have, rx_on;      /* the game's block (its link player) */
static u32 player_pending;               /* the game asked for link players */

/* our outgoing block: header, then 14 bytes a command */
static const u8 *tx_src;
static u32 tx_len, tx_off, tx_on, tx_header;
static u8 our_player[60];

static void build_our_player(void)       /* LinkPlayerBlock, Ruby layout, from the game's */
{
    static const char magic[] = "GameFreak inc.";
    for (u32 i = 0; i < 60; i++) our_player[i] = 0;
    memcpy(our_player, magic, 14);
    memcpy(our_player + 44, magic, 14);
    u8 *lp = our_player + 16;
    lp[0] = game_player[16]; lp[1] = game_player[17];      /* the same version as the game */
    lp[4] = 0x2A; lp[5] = 0xE0;                             /* trainer ID 0xE02A ("e-Reader") */
    lp[8] = 0xBF; lp[9] = 0xCC; lp[10] = 0xBF; lp[11] = 0xBB; lp[12] = 0xBE; lp[13] = 0xBF; lp[14] = 0xCC;
    lp[15] = 0xFF;                                          /* "EREADER" in the game's charset */
    memcpy(lp + 0x14, game_player + 16 + 0x14, 4);          /* link type */
    memcpy(lp + 0x1A, game_player + 16 + 0x1A, 2);          /* language */
}

static void next_command(void)           /* what we send in the next 8-word command */
{
    for (u32 i = 0; i < CMD_LEN; i++) current[i] = 0;
    if (queue_full) {
        memcpy(current, queue_cmd, sizeof current);
        queue_full = 0;
        return;
    }
    if (!tx_on)
        return;
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
    if (tx_off >= tx_len) {
        tx_on = 0;
        if (tx_src == our_player) {
            state = ST_SENT_PLAYER;
            if (event_ready) {                                  /* then the event itself */
                tx_src = EVENT; tx_len = EVENT_SIZE; tx_off = 0; tx_header = 0; tx_on = 1;
            }
        } else {
            state = ST_SENT_EVENT;
            events_sent++;
        }
    }
}

static void queue(u16 w0, u16 w1)
{
    for (u32 i = 0; i < CMD_LEN; i++) queue_cmd[i] = 0;
    queue_cmd[0] = w0; queue_cmd[1] = w1;
    queue_full = 1;
}

static void process_command(void)        /* a whole command has arrived */
{
    u32 game = is_master ? 1 : 0;
    u16 c = frame[0][game];
    if (c == 0x2222 || frame[0][is_master ? 0 : 1] == 0x2222)
        player_pending = 1;
    if (c == 0xBBBB) {
        rx_size = frame[1][game] < 60 ? frame[1][game] : 60;
        rx_have = 0; rx_on = 1;
    } else if (c == 0x8888 && rx_on) {
        for (u32 j = 1; j < CMD_LEN && rx_have < rx_size; j++) {
            game_player[rx_have++] = frame[j][game];
            if (rx_have < rx_size) game_player[rx_have++] = frame[j][game] >> 8;
        }
        if (rx_have >= rx_size) {
            rx_on = 0;
            game_lp_type = game_player[16 + 0x14] | game_player[16 + 0x15] << 8;
            game_lp_lang = game_player[16 + 0x1A];
            state = ST_GOT_PLAYER;
            if (player_pending) {                               /* now answer with ours */
                build_our_player();
                tx_src = our_player; tx_len = 60; tx_off = 0; tx_header = 0; tx_on = 1;
                player_pending = 0;
            }
        }
    } else if (c == 0x5FFF) {
        queue(0x5FFF, 0);                                       /* close: everyone says so */
        state = ST_CLOSED;
    }
}

static void on_transfer(void)            /* after every transfer, as the game's link layer does */
{
    u16 w[2] = { SIOMULTI(0), SIOMULTI(1) };
    transfers++;
    if (state == ST_HANDSHAKE || state == ST_WAITING) {
        static u32 prev, seen_two;
        u32 n = 0;
        for (u32 i = 0; i < 2; i++) {
            if ((w[i] & ~3) == SLAVE_HS || w[i] == MASTER_HS) n++;
            else break;
        }
        state = ST_HANDSHAKE;
        if (n > 1 && n == prev && w[0] == MASTER_HS) {           /* the link is up */
            state = ST_CONNECTED;
            sessions++; hs_run = 0;
            send_idx = recv_idx = 0; checksum_ready = 0; nonzero = 0;
            if (is_master) queue(0x2222, 0x5501);                /* ask for link players */
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
    /* the game handshaking again while we think we're linked: it has closed the link (after an
     * event, or after an error) and opened it again - go back to the handshake */
    u16 gw = w[is_master ? 1 : 0];
    hs_run = ((gw & ~3) == SLAVE_HS || gw == MASTER_HS) ? hs_run + 1 : 0;
    if (hs_run >= 3) {
        state = ST_HANDSHAKE; hs_run = 0;
        rx_on = tx_on = queue_full = player_pending = 0;
        loaded = SLAVE_HS;
        SIOMLT_SEND = loaded;
        return;
    }
    if (send_idx == 0) {                                         /* the checksum slot */
        if (checksum_ready && (w[0] != checksum || w[1] != checksum) && !hs_run) {   /* (not the game starting over) */
            bad_checksums++;
            mm_by_state[state & 7]++;
            if (mm_n < MM_N) {
                mm_info[mm_n] = state | (transfers & 0xFFFFFF) << 8;
                mm_words[mm_n] = w[0] | (u32)w[1] << 16;
                mm_sum[mm_n] = checksum;
                mm_n++;
            }
        }
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
        if (send_idx == 0) next_command();
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
    if (pkt[3] == 0x2B) {                                         /* stop */
        api->reply(0x2B, state | transfers << 8);
        stop_requested = 1;
        return 1;
    }
    if (pkt[3] == 0x2C) {                                         /* PUT: the next word of the event */
        if (put_at < sizeof event_words / 4)
            event_words[put_at++] = arg;
        api->reply(0x2C, put_at);
        return 0;
    }
    switch (arg & 0xFF) {
    case 0: v = state | is_master << 4 | (sessions & 7) << 5 | (transfers & 0xFFFFFF) << 8; break;
    case 4: v = idx < MM_N ? mm_info[idx] : 0; break;
    case 5: v = idx < MM_N ? mm_words[idx] : 0; break;
    case 6: v = idx < MM_N ? mm_sum[idx] : 0; break;
    case 7: v = idx < 8 ? mm_by_state[idx] : 0; break;
    case 8: v = max_gap | events_sent << 16; break;
    case 1: v = bad_checksums | (tx_off & 0xFFFF) << 16; break;
    case 2: v = game_lp_type | game_lp_lang << 16; break;
    case 3: v = idx < 15 ? game_player[4 * idx] | game_player[4 * idx + 1] << 8
                 | game_player[4 * idx + 2] << 16 | (u32)game_player[4 * idx + 3] << 24 : 0; break;
    case 20: put_at = 0; event_ready = 0; v = sizeof event_words; break;
    case 21: v = api->crc32(event_words, put_at * 4);
             event_ready = put_at * 4 >= 4 + EVENT_SIZE && EVENT_SIZE >= 1 && EVENT_SIZE <= EVENT_MAX;
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
    u16 saved_ie = REG_IE, saved_rcnt = RCNT, saved_siocnt = SIOCNT;   /* all handed back at the end */
    REG_IE = saved_ie & ~IF_SERIAL;              /* we poll IF ourselves: no serial interrupt */
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
            max_gap = t - last_iter;                 /* our longest look away from the link */
        last_iter = t;
        is_master = !(SIOCNT & CNT_SI);
        if (REG_IF & IF_SERIAL) {
            REG_IF = IF_SERIAL;
            in_flight = 0;
            u32 was = state;
            on_transfer();
            last_done = now();
            if (was < ST_CONNECTED && state >= ST_CONNECTED)
                next_cycle = last_done + LINK_UP_WAIT;          /* Ruby's master pauses here too */
        } else if (in_flight && now() - started_at > 2 * LINES_PER_FRAME) {
            in_flight = 0;                           /* never completed (cable pulled?): carry on */
        } else if (is_master && !in_flight && !(SIOCNT & CNT_START) && now() - last_done >= GAP_AFTER_DONE) {
            /* (in_flight, not just the busy bit: a transfer can finish between our look at IF
             * and our look at SIOCNT, and the next must not start before we've handled it) */
            /* the first transfer of a cycle (or any handshake transfer) waits for the next frame */
            int cycle_start = state < ST_CONNECTED || send_idx == 0;
            if (!cycle_start || (int)(now() - next_cycle) >= 0) {
                if (cycle_start)
                    next_cycle = now() + LINES_PER_FRAME;
                in_flight = 1; started_at = now();
                SIOCNT = MULTI_MODE | BAUD_115200 | CNT_IRQ | CNT_START;   /* our turn to start one */
            }
        }
        if (!(REG_IF & IF_SERIAL))               /* the link first: never let a transfer wait */
            pc_service(api);
    }
    SIOCNT = saved_siocnt & ~(CNT_START | CNT_IRQ);
    RCNT = saved_rcnt;
    REG_IF = IF_SERIAL;                          /* no serial request left pending for the firmware */
    REG_IE = saved_ie;
    return state | transfers << 8;
}
