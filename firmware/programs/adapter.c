/*
 * A Game Boy Advance Wireless Adapter, played by the DUO's GBA (firmware v4.2 program).
 *
 * The Pokemon game on the other GBA talks to "its adapter" exactly as librfu does with
 * the real one (see docs/WIRELESS.md and the Emerald decompilation, librfu_intr.c):
 *
 *   ID exchange   librfu's Sio32ID: NINTENDO, then the adapter ID 0x8001. The game is the
 *                 clock master. Done again after every reset of the adapter.
 *   Commands      0x9966LLCC + LL data words from the game (each answered with 0x80000000),
 *                 then our acknowledgement 0x9966LL(CC+0x80) + LL words of response.
 *                 The game clocks every word at 2 MHz; between words both sides signal
 *                 "ready" on their SO lines (the SIOCNT bit 3 / bit 2 handshake).
 *   Clock swap    after 0x25, 0x27, 0x35 or 0x37 the game becomes the clock slave and waits
 *                 for the adapter to send it a command (0x27 "nothing happened" after the
 *                 configured number of frames, 0x28 "data ready", 0x29 "disconnected").
 *
 * This first version is an adapter with no other adapters in range: searches find nothing.
 * Everything the game asks is recorded in a trace the PC reads.
 *
 * PC conversation (one packet answered per packet received):
 *   0x2A POLL arg   -> 0x2A value   (arg & 0xFF selects, arg >> 8 is an index)
 *        0 status  1 last word  2 trace count  3 trace entry (head)  4 trace entry (data)
 *        5 counters
 *   0x2B STOP       -> 0x2B status, then this program returns to the firmware.
 *
 * Nothing here touches either cartridge.
 */
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
#define REG32(a) (*(volatile u32 *)(a))
#define SIODATA32 REG32(0x04000120)
#define SIOCNT    REG16(0x04000128)
#define RCNT      REG16(0x04000134)
#define VCOUNT    REG16(0x04000006)

#define CNT_INTERNAL 0x0001
#define CNT_2MHZ     0x0002
#define CNT_SI       0x0004
#define CNT_SO_HIGH  0x0008
#define CNT_START    0x0080
#define CNT_32BIT    0x1000

#define IDLE_WORD 0x80000000u
#define RFU_ID    0x8001u
#define LINES_PER_FRAME 228
#define HANDSHAKE_LINES 160          /* ~10 ms: the game answers within microseconds */
#define WATCHDOG_LINES  315          /* ~20 ms */

/* ------------------------------------------------------------------ helpers */
void *memset(void *d, int c, unsigned n) { u8 *p = d; while (n--) *p++ = (u8)c; return d; }
void *memcpy(void *d, const void *s, unsigned n) { u8 *p = d; const u8 *q = s; while (n--) *p++ = *q++; return d; }

static u32 lines_total, last_vcount;

static u32 now(void)                  /* scanlines since start (63.6 us each) */
{
    u32 v = VCOUNT & 0xFF;
    u32 d = v >= last_vcount ? v - last_vcount : v + LINES_PER_FRAME - last_vcount;
    last_vcount = v;
    lines_total += d;
    return lines_total;
}

static int si(void) { return (SIOCNT & CNT_SI) != 0; }

static int wait_si(int level, u32 lines)
{
    u32 t0 = now();
    while (si() != level)
        if (now() - t0 > lines)
            return 0;
    return 1;
}

/* ------------------------------------------------------------------ trace */
#define TRACE_N 128
#define EV_ID_EXCHANGE   0x01    /* pseudo-commands, so the trace reads as a story */
#define EV_WAIT_TIMEOUT  0x02
#define EV_LOST          0x03
#define EV_SWAP_FAILED   0x04

static u32 trace_head[TRACE_N], trace_data[TRACE_N], trace_count;
static u32 n_commands, n_id, n_lost, last_word;

static void trace(u32 cmd, u32 len, u32 data)
{
    if (trace_count) {
        u32 i = (trace_count - 1) % TRACE_N;
        if ((trace_head[i] & 0xFFFF) == ((len << 8) | cmd) && trace_data[i] == data
                && (trace_head[i] >> 16) != 0xFFFF) {
            trace_head[i] += 0x10000;
            return;
        }
    }
    u32 i = trace_count % TRACE_N;
    trace_head[i] = 0x10000 | (len << 8) | cmd;
    trace_data[i] = data;
    trace_count++;
}

/* ------------------------------------------------------------------ the adapter itself */
enum { ST_IDLE = 0, ST_HOST_CLOSED = 1, ST_HOST_OPEN = 2, ST_SEARCHING = 3, ST_CONNECTING = 4,
       ST_CONNECTED = 5, ST_ASLEEP = 0xFF };

static u32 adapter_state, system_config, game_config[6];
static u32 req[32], req_len, req_cmd;
static u32 ack[32], ack_len;
static int swap_after;                 /* the game hands us the clock after this command */

static void respond(void)
{
    u32 cmd = req_cmd;
    ack_len = 0;
    swap_after = 0;
    switch (cmd) {
    case 0x10: adapter_state = ST_IDLE; break;                         /* Hello / reset */
    case 0x11: ack[0] = 0; ack_len = 1; break;                         /* signal levels */
    case 0x12: ack[0] = 0x00830117; ack_len = 1; break;                /* version */
    case 0x13: ack[0] = (adapter_state == ST_ASLEEP ? 0 : adapter_state) << 24; ack_len = 1; break;
    case 0x14: ack[0] = 0xFF; ack_len = 1; break;                      /* slot status */
    case 0x15: memset(ack, 0, 7 * 4); ack[6] = 257; ack_len = 7; break;
    case 0x16: memcpy(game_config, req, sizeof game_config); break;    /* broadcast data */
    case 0x17: system_config = req[0]; break;                          /* setup */
    case 0x19: adapter_state = ST_HOST_OPEN; break;
    case 0x1A: break;                                                  /* nobody joined */
    case 0x1B: adapter_state = ST_HOST_CLOSED; break;
    case 0x1C: adapter_state = ST_SEARCHING; break;
    case 0x1D: break;                                                  /* nobody in range */
    case 0x1E: adapter_state = ST_IDLE; break;
    case 0x1F: adapter_state = ST_CONNECTING; break;
    case 0x20: ack[0] = 0x01000000; ack_len = 1; break;                /* still trying */
    case 0x21: ack[0] = 0x01000000; ack_len = 1; adapter_state = ST_IDLE; break;   /* failed */
    case 0x24: break;
    case 0x25: swap_after = 1; break;
    case 0x26: break;                                                  /* no data */
    case 0x27: swap_after = 1; break;
    case 0x30: break;
    case 0x32: case 0x34: break;
    case 0x33: ack[0] = 0x01000000; ack_len = 1; break;
    case 0x35: case 0x37: swap_after = 1; break;
    case 0x3D: adapter_state = ST_ASLEEP; break;
    default:   ack[0] = 2; ack_len = 1; cmd = 0xEE - 0x80; break;      /* unknown command */
    }
    ack[31] = 0x99660000 | (ack_len << 8) | ((cmd + 0x80) & 0xFF);
}

/* ------------------------------------------------------------------ ID exchange (Sio32ID) */
static const u16 nintendo[4] = { 0x494E, 0x544E, 0x4E45, 0x4F44 };
static u32 id_count, id_send, id_recv, id_last;

static void id_reset(void) { id_count = id_send = id_recv = id_last = 0; }

static u32 id_step(u32 d)              /* librfu Sio32ID, adapter side; returns our next word */
{
    u32 theirs = d & 0xFFFF, echoed = d >> 16;
    if (id_last == 0) {
        if (echoed == id_recv) {
            if (id_count < 4) {
                if (id_recv == (~id_send & 0xFFFF) && theirs == (~id_recv & 0xFFFF))
                    id_count++;
            } else {
                id_last = theirs;
            }
        } else {
            id_count = 0;
        }
    }
    id_send = id_count < 4 ? nintendo[id_count] : RFU_ID;
    id_recv = ~theirs & 0xFFFF;
    return (id_send << 16) | id_recv;
}

/* ------------------------------------------------------------------ the link port */
enum { PH_COMMAND, PH_REQUEST, PH_ACK_HEADER, PH_ACK_DATA, PH_ID };
static u32 phase, ack_next, req_next;

static void arm(u32 word)                /* clock slave, ready: next word loaded, SO low */
{
    SIODATA32 = word;
    SIOCNT = CNT_32BIT | CNT_START;
}

static void back_to_idle(void)
{
    phase = PH_COMMAND;
    SIOCNT = CNT_32BIT;
    arm(IDLE_WORD);
}

/* After every word of a command: SO high, wait for the game's SO high, then load the next
 * word, arm, and drop SO in one write - the game starts the next word when it sees us low. */
static int handshake_then_arm(u32 word)
{
    SIOCNT = CNT_32BIT | CNT_SO_HIGH;
    if (!wait_si(1, HANDSHAKE_LINES)) {
        n_lost++;
        trace(EV_LOST, 0, req_cmd);
        back_to_idle();
        return 0;
    }
    SIODATA32 = word;
    SIOCNT = CNT_32BIT | CNT_START;
    return 1;
}

/* Clock master for one word (the game is waiting as clock slave). */
static int master_word(u32 word, u32 *got)
{
    if (!wait_si(0, HANDSHAKE_LINES))            /* the game is armed and ready */
        return 0;
    SIODATA32 = word;
    SIOCNT = CNT_32BIT | CNT_2MHZ | CNT_INTERNAL;
    SIOCNT = CNT_32BIT | CNT_2MHZ | CNT_INTERNAL | CNT_START;
    while (SIOCNT & CNT_START)
        ;
    *got = SIODATA32;
    return 1;
}

/* The game has handed us the clock. Wait (serving the PC) until there is something to tell
 * it, then send that command, take its acknowledgement and hand the clock back. */
static int pc_service(api_t *api);
static int stop_requested;

static void clock_master_turn(api_t *api)
{
    u32 frames = system_config & 0xFF, t0 = now(), got = 0, cmd;
    SIOCNT = CNT_32BIT | CNT_2MHZ | CNT_INTERNAL;           /* SO low */
    for (;;) {
        if (pc_service(api) || stop_requested)
            return;
        if (frames && now() - t0 >= frames * LINES_PER_FRAME)
            break;
    }
    cmd = 0x99660027;                                          /* nothing happened */
    if (!master_word(cmd, &got)) goto failed;
    /* inverted handshake: we stay low, the game goes high, we go high, the game re-arms (low) */
    if (!wait_si(1, HANDSHAKE_LINES)) goto failed;
    SIOCNT = CNT_32BIT | CNT_2MHZ | CNT_INTERNAL | CNT_SO_HIGH;
    for (volatile int i = 0; i < 40; i++) ;                    /* the game wants >= 40 us */
    if (!master_word(IDLE_WORD, &got)) goto failed;
    if (got != 0x996600A7) goto failed;
    /* final handshake, then the game is clock master again */
    if (!wait_si(1, HANDSHAKE_LINES)) goto failed;
    SIOCNT = CNT_32BIT | CNT_2MHZ | CNT_INTERNAL | CNT_SO_HIGH;
    wait_si(0, HANDSHAKE_LINES);
    trace(EV_WAIT_TIMEOUT, 0, frames);
    back_to_idle();
    return;
failed:
    last_word = got;
    trace(EV_SWAP_FAILED, 0, got);
    back_to_idle();
}

static u32 last_word_at;

static void link_word(api_t *api, u32 in)
{
    last_word = in;
    last_word_at = now();
    switch (phase) {
    case PH_ID:
    case PH_COMMAND:
        if ((in >> 16) == 0x9966 && phase == PH_ID) {
            /* Our answer to this word was part of the ID exchange, not 0x80000000, so the
             * game ignores it and sends the command again ~130 ms later. Be ready for it. */
            phase = PH_COMMAND;
            arm(IDLE_WORD);
            return;
        }
        if ((in >> 16) == 0x9966) {
            req_cmd = in & 0xFF;
            req_len = (in >> 8) & 0xFF;
            req_next = 0;
            phase = PH_REQUEST;
            if (req_len == 0)
                goto request_done;
            handshake_then_arm(IDLE_WORD);
            return;
        }
        if (phase == PH_COMMAND && in == IDLE_WORD) {          /* nothing for us */
            arm(IDLE_WORD);
            return;
        }
        if (phase != PH_ID) {                                  /* the game is resetting us */
            id_reset();
            phase = PH_ID;
        }
        {
            u32 before = id_last;
            u32 w = id_step(in);
            if (!before && id_last) {
                /* The last pair carried both IDs (game B0BB8001 / us 8001B0BB): the game is
                 * done too, and its next word is a command - which wants 0x80000000 from us. */
                n_id++;
                trace(EV_ID_EXCHANGE, 0, id_last);
                phase = PH_COMMAND;
                arm(IDLE_WORD);
                return;
            }
            arm(w);                                            /* no handshake in the ID exchange */
        }
        return;
    case PH_REQUEST:
        if (req_next < 32) req[req_next] = in;
        req_next++;
        if (req_next < req_len) {
            handshake_then_arm(IDLE_WORD);
            return;
        }
    request_done:
        n_commands++;
        trace(req_cmd, req_len, req_len ? req[0] : 0);
        respond();
        phase = PH_ACK_HEADER;
        handshake_then_arm(ack[31]);
        return;
    case PH_ACK_HEADER:
    case PH_ACK_DATA:
        if (phase == PH_ACK_HEADER)
            ack_next = 0;
        if (ack_next < ack_len) {
            phase = PH_ACK_DATA;
            handshake_then_arm(ack[ack_next++]);
            return;
        }
        if (swap_after) {
            /* last handshake, then the game becomes clock slave */
            SIOCNT = CNT_32BIT | CNT_SO_HIGH;
            if (!wait_si(1, HANDSHAKE_LINES)) { back_to_idle(); return; }
            SIOCNT = CNT_32BIT;                                /* low: done */
            if (!wait_si(0, HANDSHAKE_LINES)) { back_to_idle(); return; }
            clock_master_turn(api);
            return;
        }
        phase = PH_COMMAND;
        handshake_then_arm(IDLE_WORD);
        return;
    }
}

/* ------------------------------------------------------------------ the PC */
static u8 pkt[8];
static u32 status_word(void)
{
    return (adapter_state & 0xFF) | (phase << 8) | (n_commands << 16);
}

static int pc_service(api_t *api)
{
    if (!api->avail())
        return 0;
    api->recv8(pkt);
    u32 arg = pkt[4] | pkt[5] << 8 | pkt[6] << 16 | (u32)pkt[7] << 24;
    if (pkt[3] == 0x2B) {
        api->reply(0x2B, status_word());
        stop_requested = 1;
        return 1;
    }
    u32 v, i = (arg >> 8) % TRACE_N;
    switch (arg & 0xFF) {
    case 0: v = status_word(); break;
    case 1: v = last_word; break;
    case 2: v = trace_count; break;
    case 3: v = trace_head[i]; break;
    case 4: v = trace_data[i]; break;
    case 5: v = n_id | (n_lost << 16); break;
    default: v = 0xFFFFFFFF; break;
    }
    api->reply(0x2A, v);
    return 0;
}

/* ------------------------------------------------------------------ entry */
__attribute__((section(".text.start")))
u32 start(api_t *api)
{
    RCNT = 0;
    SIOCNT = CNT_32BIT;
    adapter_state = ST_ASLEEP;
    phase = PH_ID;
    last_vcount = VCOUNT & 0xFF;
    arm(IDLE_WORD);
    while (!stop_requested) {
        if (!(SIOCNT & CNT_START))
            link_word(api, SIODATA32);
        pc_service(api);
        /* Every word of a command is handshaken, so a long silence mid-command means the
         * game gave up on it (and will reset us or start again): wait for a new command. */
        if (phase != PH_COMMAND && phase != PH_ID && now() - last_word_at > WATCHDOG_LINES) {
            n_lost++;
            trace(EV_LOST, 0, req_cmd);
            back_to_idle();
        }
    }
    SIOCNT = 0;
    return status_word();
}
