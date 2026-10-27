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
#define EV_STRAY         0x05    /* the game wanted more of an answer than we sent */
#define EV_FLICK_TIMEOUT 0x06    /* the game never noticed we were ready */
#define EV_CONNECTED     0x07    /* the game has connected to our distributor */

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

/* ------------------------------------------------------------------ a distributor in range
 * What a searching game sees (0x1D): one host, broadcasting what Emerald's Mystery Gift looks
 * for (link_rfu_2.c / union_room.c in the decomp): serial number 0x7F7D, RFU_SERIAL_WONDER_
 * DISTRIBUTOR, and as its "game name" a struct RfuGameData with compatibility.hasCard set.
 * Layout as librfu's rfu_REQ_configGameData builds it: serial (2), gname (13), checksum (1),
 * uname (8); checksum = ~(sum of gname[0..7] + uname[0..7]). */
#define HOST_ID      0x7DA5
#define CHILD_ID     0x2C41
#define SERIAL_WONDER_DISTRIBUTOR 0x7F7D
static u32 host_cfg[6], host_on = 1, connect_polls, child_connected;

static void build_host(void)
{
    u8 b[24];
    u32 compat = 1                       /* language: Japanese */
               | 1 << 5                  /* hasCard */
               | 1 << 7 | 1 << 8 | 1 << 9 /* canLinkNationally, hasNationalDex, gameClear */
               | 3 << 10;                /* version: Emerald */
    static const u8 uname[8] = { 0xBE, 0xCF, 0xC9, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };   /* "DUO" */
    u8 sum = 0;
    memset(b, 0, sizeof b);
    b[0] = SERIAL_WONDER_DISTRIBUTOR & 0xFF;
    b[1] = SERIAL_WONDER_DISTRIBUTOR >> 8;
    b[2] = compat; b[3] = compat >> 8;   /* RfuGameData.compatibility */
    b[4] = 0x34;   b[5] = 0x12;          /* .playerTrainerId */
    b[12] = 21;                          /* .activity = ACTIVITY_WONDER_CARD, not started */
    memcpy(b + 16, uname, 8);
    for (int i = 0; i < 8; i++)
        sum += b[2 + i] + uname[i];
    b[15] = ~sum;
    for (int i = 0; i < 6; i++)
        host_cfg[i] = b[4 * i] | b[4 * i + 1] << 8 | b[4 * i + 2] << 16 | (u32)b[4 * i + 3] << 24;
}

static void respond(void)
{
    u32 cmd = req_cmd;
    ack_len = 0;
    swap_after = 0;
    switch (cmd) {
    case 0x10: adapter_state = ST_IDLE; child_connected = 0; break;   /* Hello / reset */
    case 0x11: ack[0] = child_connected ? 0xFF : 0; ack_len = 1; break; /* signal levels */
    case 0x12: ack[0] = 0x00830117; ack_len = 1; break;                /* version */
    case 0x13: ack[0] = (adapter_state == ST_ASLEEP ? 0 : adapter_state) << 24
                       | (child_connected ? 1 << 16 | CHILD_ID : 0); ack_len = 1; break;
    case 0x14: ack[0] = 0xFF; ack_len = 1; break;                      /* slot status */
    case 0x15: memset(ack, 0, 7 * 4); ack[6] = 257; ack_len = 7; break;
    case 0x16: memcpy(game_config, req, sizeof game_config); break;    /* broadcast data */
    case 0x17: system_config = req[0]; break;                          /* setup */
    case 0x19: adapter_state = ST_HOST_OPEN; break;
    case 0x1A: break;                                                  /* nobody joined */
    case 0x1B: adapter_state = ST_HOST_CLOSED; break;
    case 0x1C: adapter_state = ST_SEARCHING; break;
    case 0x1D: case 0x1E:                                              /* who is in range */
        if (host_on && (adapter_state == ST_SEARCHING || cmd == 0x1E)) {
            ack[0] = HOST_ID;                                          /* id, next free slot 0 */
            memcpy(ack + 1, host_cfg, sizeof host_cfg);
            ack_len = 7;
        }
        if (cmd == 0x1E) adapter_state = ST_IDLE;
        break;
    case 0x1F:
        adapter_state = ST_CONNECTING;
        connect_polls = host_on && (req[0] & 0xFFFF) == HOST_ID ? 1 : 0;
        break;
    case 0x20: case 0x21:                                              /* connected yet? */
        if (connect_polls && (cmd == 0x21 || ++connect_polls > 2)) {
            ack[0] = CHILD_ID;                                         /* slot 0, status 0: done */
            child_connected = 1;
            adapter_state = ST_CONNECTED;
            if (cmd == 0x21) trace(EV_CONNECTED, 0, CHILD_ID);
        } else {
            ack[0] = connect_polls ? 0x01000000 : 0x04000000;          /* trying / failed */
            if (cmd == 0x21) adapter_state = ST_IDLE;
        }
        ack_len = 1;
        break;
    case 0x24: break;
    case 0x25: swap_after = 1; break;
    case 0x26: break;                                                  /* no data */
    case 0x27: swap_after = 1; break;
    case 0x30: child_connected = 0; adapter_state = ST_IDLE; break;
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

#ifdef PROBE
/* ------------------------------------------------------------------ 2 MHz probe (adapter_probe)
 * Real hardware (lab session 11): the game's words reach us at 2 MHz, but it rejects our
 * 0x80000000. Each attempt of a command tries one way of loading that answer:
 *   E_LAG1      every bit arrives one clock late: load W << 1 and hold SO at W's top bit
 *   E_FIRSTBIT  only the first bit is late: load W and hold SO at its top bit
 *   E_PLAIN     as designed (fails on real hardware so far)
 * The game only goes on to the ready handshake if it got exactly 0x80000000, so a handshake
 * after the first word means that encoding works. Words after a handshake must be loaded with
 * SO low ("ready"); for top-bit-1 words we flick SO low just long enough for the game to see
 * it, and back high before its first clock edge - the counters say whether that worked. */
enum { E_LAG1, E_FIRSTBIT, E_PLAIN, N_ENC };
static u32 enc, tries[N_ENC], accepts[N_ENC], acks_ok[N_ENC], hs_count, hs_after_header;

static void arm_idle(void)
{
    if (enc == E_PLAIN) { arm(IDLE_WORD); return; }
    SIODATA32 = enc == E_LAG1 ? IDLE_WORD << 1 : IDLE_WORD;
    SIOCNT = CNT_32BIT | CNT_START | CNT_SO_HIGH;
}

static void arm_word(u32 w)
{
    u32 b = enc == E_PLAIN ? 0 : w >> 31, t0;
    SIODATA32 = enc == E_LAG1 ? w << 1 : w;
    if (!b) {
        SIOCNT = CNT_32BIT | CNT_START;
        return;
    }
    t0 = now();
    while (SIOCNT & CNT_START) {
        SIOCNT = CNT_32BIT | CNT_START;                       /* low: ready */
        SIOCNT = CNT_32BIT | CNT_START | CNT_SO_HIGH;         /* high before the first clock edge */
        for (volatile int i = 0; i < 2; i++) ;
        if (now() - t0 > HANDSHAKE_LINES)
            break;
    }
}

/* End of a command: the game's last handshake wants us low; once it has gone idle (low too),
 * load the next idle answer the probe's way (SO high). */
static void idle_after_command(void)
{
    arm(IDLE_WORD);
    if (enc != E_PLAIN && wait_si(0, HANDSHAKE_LINES))
        arm_idle();
}
#else
/* Real hardware (lab session 12): at 2 MHz the game reads the FIRST bit of each of our words
 * correctly only if SO is already resting at that bit's level (SIOCNT bit 3) - the other 31
 * bits are fine. So every word is loaded with SO at its top bit.
 *   - The first word of a command has no handshake: just rest SO at 1 (0x80000000).
 *   - After a handshake SO must go low to say "ready", but a top-bit-1 word needs SO high when
 *     the game's clock starts. So we FLICK it: low for a moment, then high again. The game starts
 *     its clock a microsecond or two after it notices the low (librfu_intr.c: handshake_wait(0),
 *     STWI_stop_timer_in_RAM, two SIOCNT writes), so the flick must be long enough to be noticed
 *     down the cable, and short enough to be over before that first clock edge.
 *   - Lab sessions 12-14: a two-instruction flick (~0.25 us) is noticed only rarely. The width
 *     is now tuned while running: if the game never clocks, flicks get wider; if it clocks but
 *     then rejects the word (first bit wrong), they get narrower. Per-width counts go to the PC.
 *   - After each flick we keep our hands off SIOCNT for longer than a whole word (16 us):
 *     touching SIOCNT mid-word loses it (session 13).
 * The end of a command needs a plain low (unarmed) until the game has gone idle. */
static u32 n_stray, n_flick_timeouts;

#define W_MAX 15
volatile u32 flick_w = 1;                /* spin-loop iterations the flick stays low (tuned) */
static u32 w_pulses[W_MAX + 1], w_clocked[W_MAX + 1], w_accepted[W_MAX + 1], w_rejected[W_MAX + 1];
static int last_flick = -1;              /* width of the flick that started the last word, or -1 */
static int flick_good = -1;              /* the last width the game accepted, or -1 */
static u32 spin_per_8_lines, polls_per_8_lines;

/* link-port scope: SI (the game's SO) sampled during the quiet spell after the first flicks */
#define SCOPE_N 8
static u32 scope_si[SCOPE_N][2], scope_meta[SCOPE_N], scope_count;

static void spin(u32 n)                  /* two instructions a step: fine, even steps */
{
    if (n)
        __asm__ volatile("1: subs %0, %0, #1\n\tbne 1b" : "+l"(n) : : "cc");
}

static u32 count_8_lines(int polls)       /* units of work done in 8 scanlines (0 if VCOUNT is stuck) */
{
    u32 n = 0, guard = 0, v = VCOUNT & 0xFF;
    while ((VCOUNT & 0xFF) == v)
        if (++guard > 200000) return 0;
    v = (VCOUNT & 0xFF) + 8;
    if (v >= LINES_PER_FRAME) v -= LINES_PER_FRAME;
    while ((VCOUNT & 0xFF) != v) {
        if (polls) {
            for (int i = 0; i < 16; i++)
                if (!(SIOCNT & 0x8000)) n++;             /* the same shape as the quiet-spell poll */
        } else {
            spin(16);
            n += 16;
        }
        if (n > 4000000) return 0;
    }
    return n;
}

static void calibrate(void)              /* how fast spin() and the polling loop run */
{
    spin_per_8_lines = count_8_lines(0);
    polls_per_8_lines = count_8_lines(1);
}

static void arm_idle(void)
{
    SIODATA32 = IDLE_WORD;
    SIOCNT = CNT_32BIT | CNT_START | CNT_SO_HIGH;
}

#define FLICK_QUIET 40          /* polls of SIOCNT (see calibrate): well over one 16 us word */
#define PULSES_PER_WIDTH 4
#define FLICK_LINES 790        /* ~50 ms of flicking: inside librfu's 80 ms handshake timeout */

static void arm_word(u32 w)
{
    u32 t0, tries = 0, cap = scope_count < SCOPE_N ? scope_count : SCOPE_N;
    SIODATA32 = w;
    last_flick = -1;
    if (!(w >> 31)) {
        SIOCNT = CNT_32BIT | CNT_START;
        return;
    }
    if (flick_good >= 0)
        flick_w = flick_good;                                 /* start from the last width that worked */
    t0 = now();
    for (;;) {
        u32 fw = flick_w, si_bits[2] = { 0, 0 }, done_at = 0xFF;
        SIOCNT = CNT_32BIT | CNT_START;                       /* low: ready */
        spin(fw);
        if (SIOCNT & CNT_START)                               /* (never re-arm a finished word) */
            SIOCNT = CNT_32BIT | CNT_START | CNT_SO_HIGH;     /* high again for the first bit */
        w_pulses[fw]++;
        for (u32 i = 0; i < FLICK_QUIET; i++) {
            u32 c = SIOCNT;
            if (i < 64 && (c & CNT_SI))
                si_bits[i >> 5] |= 1u << (i & 31);
            if (!(c & CNT_START)) { done_at = i; break; }
        }
        if (cap < SCOPE_N && (done_at != 0xFF || tries == 0)) {
            scope_si[cap][0] = si_bits[0];
            scope_si[cap][1] = si_bits[1];
            scope_meta[cap] = fw | (done_at << 8) | (w & 0xFFFF0000);
            scope_count = ++cap;
        }
        if (done_at != 0xFF) {
            w_clocked[fw]++;
            last_flick = fw;                                  /* judged at the next handshake */
            return;
        }
        if (++tries % PULSES_PER_WIDTH == 0 && flick_w < W_MAX)
            flick_w++;                                        /* not noticed: wider */
        if (now() - t0 > FLICK_LINES) {
            n_flick_timeouts++;
            trace(EV_FLICK_TIMEOUT, 0, w);
            return;
        }
    }
}

/* Called by the next handshake: did the game accept the word that flick started? */
static void judge_flick(int accepted)
{
    if (last_flick < 0)
        return;
    if (accepted) {
        w_accepted[last_flick]++;
        flick_good = last_flick;
    } else {
        w_rejected[last_flick]++;
        flick_good = -1;
        if (flick_w > 0 && flick_w >= (u32)last_flick)
            flick_w = last_flick ? last_flick - 1 : 0;       /* clocked but refused: narrower */
    }
    last_flick = -1;
}

static void idle_after_command(void)
{
    SIOCNT = CNT_32BIT;                                       /* low, unarmed: the game's last check */
    wait_si(0, HANDSHAKE_LINES);                              /* it has seen us and gone idle */
    arm_idle();                                               /* rest high for the next command */
}
#endif

static void back_to_idle(void)
{
    phase = PH_COMMAND;
    SIOCNT = CNT_32BIT;
    arm_idle();
}

/* After every word of a command: SO high, wait for the game's SO high, then load the next
 * word, arm, and drop SO in one write - the game starts the next word when it sees us low. */
static int handshake_then_arm(u32 word)
{
    SIOCNT = CNT_32BIT | CNT_SO_HIGH;
    if (!wait_si(1, HANDSHAKE_LINES)) {
#ifndef PROBE
        judge_flick(0);
#endif
        n_lost++;
        trace(EV_LOST, 0, req_cmd);
#ifdef PROBE
        enc = enc + 1 < N_ENC ? enc + 1 : 0;                   /* the game will try again */
#endif
        back_to_idle();
        return 0;
    }
#ifdef PROBE
    if (hs_count == 0) accepts[enc]++;
    if (hs_after_header) acks_ok[enc]++;
    hs_count++;
    hs_after_header = 0;
#else
    judge_flick(1);
#endif
    if (phase == PH_COMMAND) {                                 /* the command is complete */
        idle_after_command();
        return 1;
    }
    arm_word(word);
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
    if ((got & 0x7FFFFFFF) != (0x996600A7 & 0x7FFFFFFF)) goto failed;   /* its first bit is late too */
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
            arm_idle();
            return;
        }
        if ((in >> 16) == 0x9966) {
#ifdef PROBE
            tries[enc]++;
            hs_count = 0;
            hs_after_header = 0;
#endif
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
#ifndef PROBE
            n_stray++;
            trace(EV_STRAY, 0, req_cmd);
#endif
            arm_idle();
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
#ifdef PROBE
                enc = E_LAG1;
#endif
                arm_idle();
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
#ifdef PROBE
        hs_after_header = phase == PH_ACK_HEADER;
#endif
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
#ifndef PROBE
    case 8: v = n_stray | (n_flick_timeouts << 16); break;
    case 9: v = i <= W_MAX ? (w_pulses[i] & 0xFFFF) | (w_clocked[i] << 16) : 0; break;
    case 10: v = i <= W_MAX ? (w_accepted[i] & 0xFFFF) | (w_rejected[i] << 16) : 0; break;
    case 11: v = flick_w | (scope_count << 8); break;
    case 12: v = spin_per_8_lines; break;
    case 13: v = polls_per_8_lines; break;
    case 14: v = (i >> 2) < SCOPE_N ? ((i & 3) == 0 ? scope_si[i >> 2][0] : (i & 3) == 1 ? scope_si[i >> 2][1]
                                        : scope_meta[i >> 2]) : 0; break;
#endif
#ifdef PROBE
    case 6: v = i < N_ENC ? (tries[i] | accepts[i] << 16) : 0; break;
    case 7: v = i < N_ENC ? acks_ok[i] : 0; break;
#endif
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
    build_host();
#ifndef PROBE
    calibrate();
    last_vcount = VCOUNT & 0xFF;
#endif
    arm_idle();
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
