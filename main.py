"""
main.py - the ui layer
rendering, menus, spinners, all that stuff.
analysis logic is in engine.py.

usage:
  py main.py           # menu
  py main.py AAPL      # one ticker
  set GROQ_API_KEY in env or it'll ask

testing:
  IS_TESTING = True and fill in keys below
  finnhub is just for news, facts come from yfinance
"""

import sys, os, re, json, threading, itertools, time, random

from groq import Groq

from fetch  import fetch_facts, fetch_finnhub_news
from score  import compute_score, DEFAULT_WEIGHTS
from log    import save_log, load_logs
from engine import (
    MODEL, COMPONENT_ORDER,
    build_prompt, call_groq, parse_response, blend_scores, det_display,
)

# testing mode
IS_TESTING          = False
TESTING_GROQ_KEY    = ""   # fill in locally, never push real keys
TESTING_FINNHUB_KEY = ""   # just for news

# ansi colors
if sys.platform == "win32":
    os.system("")  # enable virtual terminal processing

B  = "\033[1m"
D  = "\033[2m"
R  = "\033[0m"
CY = "\033[96m"
GR = "\033[92m"
YL = "\033[93m"
RD = "\033[91m"
MG = "\033[95m"
WH = "\033[97m"
BL = "\033[94m"
GY = "\033[90m"

W = 58  # display width for body content

# try unicode bars, fall back to ascii if terminal sucks
try:
    "█░".encode(sys.stdout.encoding or "utf-8")
    FILL, EMPTY = "█", "░"
except Exception:
    FILL, EMPTY = "#", "."


# display helpers

def hr(ch="─"):
    return f"{GY}{ch * W}{R}"

def ansi_len(s):
    return len(re.sub(r"\033\[[^m]*m", "", s))

def ansi_ljust(s, width):
    return s + " " * max(0, width - ansi_len(s))

def fmt_score(n, bold=False, scale=100):
    """
    Return a coloured ±N string.
    scale=100  → component scores  (green/red thresholds at ±10 / ±30)
    scale=1000 → total scores      (thresholds at ±100 / ±300)
    """
    t1 = int(scale * 0.30)
    t2 = int(scale * 0.10)
    if   n > t1:  col = f"{B}{GR}" if bold else GR
    elif n > t2:  col = GR
    elif n < -t1: col = f"{B}{RD}" if bold else RD
    elif n < -t2: col = RD
    else:         col = YL
    return f"{col}{n:+d}{R}"

def outlook_badge(o):
    if o == "BULLISH": return f"{GR}{B} BULLISH {R}"
    if o == "BEARISH": return f"{RD}{B} BEARISH {R}"
    return f"{YL}{B} NEUTRAL {R}"

def make_bar(value, maxval=100, width=18):
    """Visual bar scaled so |value| / maxval fills width/2 cells."""
    half  = width // 2
    mag   = min(abs(int(value)), maxval)
    cells = max(1, int(round(mag / maxval * half)))
    col   = GR if value >= 0 else RD
    if value >= 0:
        bar = " " * half + f"{col}{FILL * cells}{R}" + f"{GY}{EMPTY * (half - cells)}{R}"
    else:
        bar = f"{GY}{EMPTY * (half - cells)}{R}" + f"{col}{FILL * cells}{R}" + " " * half
    return bar

def wrap_text(text, width=W - 4, indent="  "):
    words, cur, out = text.split(), [], []
    for word in words:
        if sum(len(x) + 1 for x in cur) + len(word) > width:
            if cur:
                out.append(indent + " ".join(cur))
            cur = [word]
        else:
            cur.append(word)
    if cur:
        out.append(indent + " ".join(cur))
    return "\n".join(out)

def _fmt_mcap(mc):
    if mc >= 1e12: return f"${mc/1e12:.2f}T"
    if mc >= 1e9:  return f"${mc/1e9:.1f}B"
    return f"${mc/1e6:.0f}M"


# ── Spinner ───────────────────────────────────────────────────────────────────

class Spinner:
    BRAILLE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    ASCII   = ["|", "/", "-", "\\"]

    def __init__(self, message=""):
        self.message = message
        self._stop   = threading.Event()
        self._tokens = 0
        try:
            "⠋".encode(sys.stdout.encoding or "utf-8")
            self._frames = self.BRAILLE
        except Exception:
            self._frames = self.ASCII
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        for frame in itertools.cycle(self._frames):
            if self._stop.is_set():
                break
            tok = f"  {GY}{self._tokens} tok{R}" if self._tokens else ""
            sys.stdout.write(f"\r  {CY}{frame}{R}  {self.message}{tok}   ")
            sys.stdout.flush()
            self._stop.wait(0.08)
        sys.stdout.write("\r" + " " * (W + 12) + "\r")
        sys.stdout.flush()

    def tick(self, n=1):
        self._tokens += n

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join()


# ── Banner ────────────────────────────────────────────────────────────────────

BANNER_LINES = [
    " █████╗ ██╗    ███████╗████████╗ ██████╗  ██████╗██╗  ██╗",
    "██╔══██╗██║    ██╔════╝╚══██╔══╝██╔═══██╗██╔════╝██║ ██╔╝",
    "███████║██║    ███████╗   ██║   ██║   ██║██║     █████╔╝ ",
    "██╔══██║██║    ╚════██╗   ██║   ██║   ██║██║     ██╔═██╗ ",
    "██║  ██║██║    ███████║   ██║   ╚██████╔╝╚██████╗██║  ██╗",
    "╚═╝  ╚═╝╚═╝    ╚══════╝   ╚═╝    ╚═════╝  ╚═════╝╚═╝  ╚═╝",
    "",
    "███████╗██╗   ██╗ █████╗ ██╗     ██╗   ██╗ █████╗ ████████╗ ██████╗ ██████╗ ",
    "██╔════╝██║   ██║██╔══██╗██║     ██║   ██║██╔══██╗╚══██╔══╝██╔═══██╗██╔══██╗",
    "█████╗  ██║   ██║███████║██║     ██║   ██║███████║   ██║   ██║   ██║██████╔╝",
    "██╔══╝  ╚██╗ ██╔╝██╔══██║██║     ██║   ██║██╔══██║   ██║   ██║   ██║██╔══██╗",
    "███████╗ ╚████╔╝ ██║  ██║███████╗╚██████╔╝██║  ██║   ██║   ╚██████╔╝██║  ██║",
    "╚══════╝  ╚═══╝  ╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝",
]

def print_banner():
    print()
    for line in BANNER_LINES:
        print(f"  {CY}{B}{line}{R}")
    print()
    model_line = f"  {GY}Model:{R} {WH}{MODEL}{R}"
    if IS_TESTING:
        model_line += f"   {YL}· Testing mode · Finnhub news on{R}"
    print(model_line)
    print(f"  {GY}{'─' * 55}{R}")
    print()

def print_main_menu():
    print(f"  {GY}[A]{R} Analyze a stock   "
          f"{GY}[H]{R} History   "
          f"{GY}[Q]{R} Quit")


# ── Easter egg ────────────────────────────────────────────────────────────────

SKULL_ART = """\
........:::::...........:::::::::::::::::::::::::....::::;;;;;;;;::::::::.....::::::::..:::;;:::.::;
.::.....:::::...........:::::::::::::::::::::::::...:;;;;;;::::.......:::::..:::::;;::..:;xxxx+:...:
:::.....:::::...........:::::::::::::::::::::::::....::::..............::::::::::::::::.:;+++++:....
:::....::::::...........::::::::::::::::::;;;;;;::..........................:::::;xX$X+:.:;++;:.....
:::....::;:::...........:::::::::::::;;;++;:;;++++;;;::......................::::;+xxx+:..:++;:.....
:::....::;::::..........:::::::::::;;+;+xx+;;;;+++++;+++;:....................::::;+++:..:;;+++;;;;+
:::..::::;::::..........:::;;;;;;;;+++;++++;++;;;;++++++;;+;:.................:::.:+x+::;+xxxxxx+++x
:::..::::;::::..........:::::;;;;;++++;;+++;;+++;;;+x+++++;+;;;:........:;;;;xxX;..:;;:;xXXXxxx+++++
:::..::::;;:::............:;;;+;;;+++x+;;;;;;++++;;+xx+++x+;;+;;::...:;++xXXXXXX+:.:;;++xXXXxx+++++x
:::.:::::;;;;;..........::;;:+;:;+++xxx+;;+;;++++;;;++++++;++;++;::;xXxxxx+xxxxxxxxxxXXxxx++xXx++++;
:::.:::::;;;:......:..:::;;;;:+++;;+xxx+;;++;;;;;;;;+++;:;;++++++;:;+x+++++xxxxXXXXXXXXXxxxxX$Xx+XXx
:::..::::::...........::;;;::+++++++x+;;;++x++;;;;;;:;;;;;+xx++++;;;;+x+xxxxXXXxXXXXXXXXXXXXXX$$Xx++
::.....::::::::::::::::;;;:;;++++++++;;;;+x+++++++++++;;;x+++++xx+;;;+xxxxXXXXx+xX$$$$$$$$$$Xxxx:...
.......:;;:::;X$$XXx++;;;;;;;;++++++++++++++;;++++xxxxxxxxXXXxxxxx+;;+++xXXXXXXxxXXXX$$$$XX$X+:....:
...:;:.:;;::::;+xXxx+;;+;+++++xxxxxxxx+++++;;;;+++xxxXXXXXXXXXxxxxx++++;+xXXx+xxx+;;;xXxxxxXx;::;+xX
+;:;;:::::::::::::;++;+++xxxxxxx++++++++++++;;;;+xxxxxxxxxxxxxxXXXXXxx+++XXx;:;;::;;;;++++xXXx+++xX$
$$XXXX$$x;;;;;;;;;;;;;;;;++++++x++++++++++++;;;;+xxxxXXXXXXXXxxxXXXXXx+;;+++;;;;;;;;;;+++xX$X++++xx$
$$$$XxxxXXx+;;++;;;;++++++xxxxx+xxxX$Xxxx+++;::;+xxxxxX$$$XxXXxxX$$XXXx+++++;++;;;;;;++++X$$XXXxxXXX
xX$x++++xXXx+++++;;++xxxxXXXxx+xXXxX$$xxx;;;::::;++++++xXXx+++xxxX$$$XXx++++++;;;++++++;+X$$XxxxxxXX
xX$Xxxxxxxxxxx+++++++xxxXXXXx+++;;;+++++;;;;:::;;++++++++++++++++xX$$XXxx++;;;;;++xxx+;;;x$XxXXXX$$$
+xXXx+xxXxXxxxxxx+++xxxxXXXX+;::;;;;;;;;;::::::::;++;;;;;;;;;;;;;+X$&$XXx+;;;;;;++++;;;;;+$$$XXX$$$$
++xx+;+++++xXXXxx++xxxxxXXXX;:::::::::::::::::::;;++;;;::::::;;;;+X$&&$$Xxx++;;;;;;;;;;;;;;xXXX$$$$$
xxxXxx++;;+xxXXXx++xxxxxXXXx:::::::::::::::;;;+++x++;;;;:::::;;;;+x$&&$$XXxxx++;;;;;;;;::::;xX$$$$$$
xxXXXxx+++++++xxx+++xx+xXXXX::::::::::::::;;;;xX$x+xX$$x+;;;;;;;;;;;++x$&&&$XXxxx++;;;;;;::::::+X$$$$$$$
XX$&$XXXxx++;;:::;;;++xXXxxX:::::::;;++;:;+++++xxxxx+;;;;;;;;;++++x$$$$$$XXxx++;;;;::::::;;x$$$$$$&&
X$$&$$XXx++++;;;;;++xxxxxxXX;:::;;;;+x+;:::::;;;;++;;;;+++++++++xxxX$$$$$x+++++;;;;;;;;;;;+$&&&&&&&&
$$$$$$$&$X++;;;;;;++++++xxXx;:;;;;++x+;:::::::::::::;;;;++x+++xxxxxX$$$$$XX++;;;;;;;;;;;;;x&&&&&&&&&
&$XX$$$&$Xx+;;;;;;;+++xxxxxxx;;;;;+x+;:::;;++++++++;;;;;+++++++xxxX&$$XXXXXXx+++;;;;;;;;;;x&&&&&&&&&
$x;+xxX$$Xxx++++++++xxxxxxxX$+;;;;+++++xx+++;;;++++xxxXXxx+++++xxxX$&$XxxXxxxxx++;;;;;;;;+X&&&&&&&&&
++xxxxxXXXX+++++++++++xxXXXX$$;;;;;++xX$$&&&&$$&&&&&&&&&Xx+;++xxxX$X$$$XXxxx+++;;;;;;;;;;+$&&&&&&&&&
+xxxxx+xxxx++;;;++++++xxX$XXX$X;;;;;++X$&&$$$$$$$$$$$$&&x++;++xxX$$$$$Xxxxx++++;;;;;;;;;+x$&&&&&&&&&
xXXx++++++++;;;;;++++XXxX$X$$X$X;;;;;;+X&$XxxxXXXXXXX$&X++;;+xxX$&&&$$XXXXxx+;;;;;;++++++X$&&&&&&&&&
$$Xx++++++++;;;;;+++X$$XxX$$$$$$X;;;;;;;X$x+++xxxxxxxXx+++;+xxxX&&&&$Xxxxxx++;;;;;++++++x$&&&&&&&&&&
$$$Xx+++++++++++++++xX$$XX$$$$$$&X;;;;;;;xX++++++++xx+++++++xxXXX$$$XXxxxxxx+;;;;:;++++xX&&&&&&&&&&&
&&$Xx++xxx+++++++++xxxxxxXXX$$$$&$+;;;;;;;;++++++++++xx++++xxxxxX$$XXXxxxXxx+++;;;;;++++X&&&&&&&&&&&
&&$Xxx+xxx+++++++xxxxx;:;++xxX&&&&X;;;;;;;+;;;;++++xxx+++++xxxxxxX$XXXxxXX++++++++;;;+++X&&&&&&&&&&&
&&$Xxx++++++++xxxxxx+;::;;+++xX$$$x;;;;;;;;+++++xxxx++++++xxxxxxXXXXxxxxXx++++xxx+;;;;;+X&&&&&&&&&&&
&&&$xxxxxxxxxxxxxx+;::::;;++++xX$Xx+;;;::;;;;++++++++++++xxxxxxxXXxxxxxxx+++xxXx++++;;;+X&&&&&&&&&&&
$&$XXxxxxxxxxxxxx+;:::::;;;++;++XXX+;;;;::;;;;;;;;;;;;++xxxxxXXXXxxxxxxx++xxXXx+++++++++X&&&&&&&&&&&
$&&$XxxXxxxxxxxxxx+::::;;;;;+++++XXx;;;;;;;;;;;;;;;;;++xxxxxXXxxxxxx++++xXXx++++++++++xX&&&&&&&&&&&
&&&$$XXXXXxxxxxx+;:::::;;;;;;+++++xXx;;+++;;;;;;;;+++xxxxxxXXxxxxxx+++xXXxx+++++++++++xX$&&&&&&&&&&
&&&XXXXXXXXXXx+:::::;;;;;;;;;++++++xx+;;++++++++++xxxxxxxxXxxxxxxx+++xXXXxx+++++++++++xX$$&&&&&&&&&&
&&&XXXXXXXXXx;..:::;;;+++;;;;;;;++++++++++++++++xxxxxxxxxxxxxxxxxxxXXXXXxx++++++++++++xXX$&&&&&&&&&&
&&&$XXXXXXXx::::::;;;;;;+xx+;;;;;+++++++xxxx+xxxxxxxxxxxxxxxxxxxXXXXXXXxx++++++++++++xXxX$$&&&&&&&&&
&&&$XXXXXXx:::::::;;;;;;;+xxx+++;;;;++++++xxxXXXXXXxx+xxxxxxxxxxXXXxxxx+++++++++x++++xxx$$$&&&&&&&&&
&$$XXXXXXx;:::::::;;;;;;;;+xxxxx+++++++++++++xxxxx+xxxxxxxxxxXXXxxxxxx++++++++++x++++xxX$$X$&&&&&&&&
&$XXXXXXX+::::::;;;;;;;;;;;;+xxx++xxxxxxx+xx+++++xxxxxxxxxxxxxxxxxxxx++++++++++xx+++xxxX$XX$&&&&&&&&
XX$XXXXXX;::::;;;;;;;;;++;+;;++xx++++xxxxxx+x+++++++xxxxxxxxxxxxxxxx++++++++++xx++++xxXXXXX$&&&&&&&&
$&&$XXXXx;:::;;;;;;;;;;+++++;;++++++++++++++++++++++++xxxxxxxxxxxx++++++++++++xx+++xxXXxxxX$&&&&&&&&
&&$$XXXX+::::;;;;;;;;;;+++++++++++++++++++;+++++++++++xxxxxxxxxxx++++++++++++xx+++xxXxxxxX$$&&&&&&&&
$$$XXX$X+;;;;;;;;;+++;;++++++++++++++++;;;;;;;;+++++++xxxxxxxxxx+++++++++++++xx++xxxx+++xX$$$$$&&&&&
$$$$XX$x;;;;;;;+;++++++;++++++++++++++;:::::::;;;++++++xxxxxxxxx++++++++++++xx+++xxx++++xX$&$$XXxXXX
+X$XXXXx;;;;;;;++++++++;++++++++++++++;;;;;;;;;;;;;;+++++++++xx+++++++++++++xx++xxx+++++xX$&$XXXxxxx
;+X$$$X+;;;;;;+;++++++++++++x++++++++++;;;;;;;;;;;;;;;;;::::;;;;;;;++++++++xx++xXx+++++xX$$$$XXXxxxx
:;xXXXX+;;;;+;++++++++++++++x++++++++++++++;;;+;;;;;;;;;:::::::::::;;;;+++xx++xx++++++xX$$XXxXXXXxxx
:;+XXXx+++;;;+++++++++++x+++xxx++++++xxxxxx+++++++++++;;:::::::::::::::;++x++xxxxx+++xXXxx+++xxxXXXX
:;xXXXX++++;;;++++++++++xxx+xxxxxx++++xxxxxxxxxx++++++;;::::::::::::::::;;+++xx++++xxxx++++++++++xxX
:;X$XXXx;;+;;;;;++xx++++xxxx+xxxxxx+++xxxxxxxxxxx+++++;::::::::::::::::::;;;;+;;;;;+;;;;;;;;;;;+++++"""


def easter_egg_67():
    CHAOS = (
        "!@#$%^&*()_-+=[]{}|;':\",./<>?~`"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        "░▒▓█▄▀▐▌┼┤├┴┬│─┘└┐┌"
        "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÐÑÒÓÔÕÖØÙÚÛÜÝß"
    )
    try:
        CHAOS.encode(sys.stdout.encoding or "utf-8")
    except Exception:
        CHAOS = "!@#$%^&*()-+=[]{}|;,./<>?`~ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

    print(f"\n  {GY}Fetching ticker 67 ...{R}")
    time.sleep(0.6)
    spin = Spinner("Connecting to exchange ...").start()
    time.sleep(1.8)
    spin.stop()

    print(f"  {YL}Warning: unusual data stream detected{R}")
    time.sleep(0.35)
    print(f"  {RD}Warning: data integrity failure — do not panic{R}")
    time.sleep(0.25)
    print(f"  {RD}{B}CRITICAL: stream corruption ...{R}")
    time.sleep(0.2)

    COLORS = [RD, YL, GR, CY, MG, WH, BL]
    for _ in range(210):
        ln  = "".join(random.choice(CHAOS) for _ in range(random.randint(45, 72)))
        col = random.choice(COLORS)
        sys.stdout.write(f"\r{col}{ln}{R}\n")
        sys.stdout.flush()
        time.sleep(random.uniform(0.004, 0.022))

    for _ in range(30):
        ln = "".join(random.choice(CHAOS) for _ in range(72))
        sys.stdout.write(f"\r{RD}{B}{ln}{R}\n")
        sys.stdout.flush()
        time.sleep(0.008)

    time.sleep(0.4)
    os.system("cls" if sys.platform == "win32" else "clear")
    time.sleep(0.15)

    print()
    for line in SKULL_ART.splitlines():
        sys.stdout.write(f"{RD}{line}{R}\n")
    sys.stdout.flush()

    print(f"\n{RD}{B}")
    print("  ██████╗  ██████╗  ██████╗ ██╗")
    print("  ██╔══██╗██╔═══██╗██╔═══██╗██║")
    print("  ██████╔╝██║   ██║██║   ██║██║")
    print("  ██╔══██╗██║   ██║██║   ██║╚═╝")
    print("  ██████╔╝╚██████╔╝╚██████╔╝██╗")
    print(f"  ╚═════╝  ╚═════╝  ╚═════╝ ╚═╝{R}")
    print()
    print(f"  {YL}{B}Ticker 67 doesn't exist.  You found the secret.{R}")
    print(f"  {GY}(seed=6767 — that's where the 67 comes from){R}")
    print()
    time.sleep(4)
    os.system("cls" if sys.platform == "win32" else "clear")
    print(f"\n  {GY}ok ok ... back to normal now.{R}\n")
    time.sleep(0.8)


# ── Result display ────────────────────────────────────────────────────────────

def print_results(facts, det, parsed, scores):
    name    = facts.get("companyName", facts.get("ticker", ""))
    ticker  = facts.get("ticker", "")
    sector  = facts.get("sector", "")
    ind     = facts.get("industry", "")
    bl      = scores["blended"]
    outlook = scores["outlook"]
    bc      = scores["blended_components"]

    print()
    print(hr("━"))
    print(f"  {B}{WH}{name}{R}  {GY}({ticker}){R}")
    if sector or ind:
        sep = "  ·  " if sector and ind else ""
        print(f"  {GY}{sector}{sep}{ind}{R}")
    print(hr("─"))

    # ── Score + target ────────────────────────────────────────────────────────
    score_s   = fmt_score(bl, bold=True, scale=1000)
    outlook_s = outlook_badge(outlook)
    print(f"  {GY}Score{R}  {ansi_ljust(score_s, 9)}  {D}/1000{R}   {outlook_s}", end="")
    if parsed.get("price_target"):
        cur    = facts.get("price", 0)
        pt     = parsed["price_target"]
        upside = ((pt / cur) - 1) * 100 if cur else 0
        col    = GR if upside >= 0 else RD
        print(f"   {GY}Target{R}  {col}{B}${pt:.0f}{R}  {GY}({upside:+.1f}%){R}", end="")
    print()

    # ── Quick stats: price · mkt cap · P/E · beta · div ──────────────────────
    parts = []
    price = facts.get("price")
    if price is not None:
        parts.append(f"{WH}${price:,.2f}{R}")
    mc = facts.get("marketCapUSD")
    if mc is not None:
        parts.append(_fmt_mcap(mc))
    pe = facts.get("peTTM")
    if pe is not None and pe > 0:
        parts.append(f"P/E {pe:.1f}×")
    beta = facts.get("beta")
    if beta is not None:
        parts.append(f"β {beta:.2f}")
    div = facts.get("dividendYieldPct")
    if div is not None and div > 0:
        parts.append(f"Div {div:.2f}%")
    if parts:
        print(f"  {GY}{'  ·  '.join(parts)}{R}")
    print()

    # ── Price performance bars ────────────────────────────────────────────────
    PERF_ROWS = [
        ("1d",  "dayChangePct",         5.0),
        ("1w",  "weekTrendPct",        15.0),
        ("1mo", "oneMonthTrendPct",    30.0),
        ("3mo", "threeMonthTrendPct",  60.0),
        ("1yr", "yearTrendPct",       100.0),
    ]
    perf_data = [
        (lbl, facts[k], mx)
        for lbl, k, mx in PERF_ROWS
        if isinstance(facts.get(k), (int, float))
    ]
    if perf_data:
        for i, (lbl, v, mx) in enumerate(perf_data):
            col   = GR if v >= 0 else RD
            pct_s = f"{v:+.1f}%"
            bar   = make_bar(v, maxval=mx)
            # Append 52w context on the last row
            suffix = ""
            if i == len(perf_data) - 1:
                ctx = []
                h = facts.get("distFrom52wHigh")
                l = facts.get("distFrom52wLow")
                if isinstance(h, (int, float)): ctx.append(f"52wH {h:+.1f}%")
                if isinstance(l, (int, float)): ctx.append(f"52wL +{l:.1f}%")
                if ctx:
                    suffix = f"  {GY}{'  ·  '.join(ctx)}{R}"
            print(f"  {GY}{lbl:<4}{R}  {col}{ansi_ljust(pct_s, 8)}{R}  {bar}{suffix}")
        print()

    # ── Thesis ────────────────────────────────────────────────────────────────
    if parsed["explanation"]:
        print(wrap_text(parsed["explanation"]))
        print()

    # ── Per-category bars ─────────────────────────────────────────────────────
    if bc:
        print(f"  {GY}Category breakdown{R}")
        for k in COMPONENT_ORDER:
            v   = bc.get(k, 0)
            s   = ansi_ljust(fmt_score(v, scale=100), 9)
            bar = make_bar(v, maxval=100)
            print(f"    {k:<14}  {s}  {bar}")
        print()

    # ── Buy / sell reasons ────────────────────────────────────────────────────
    if parsed["buy_reasons"]:
        print(f"  {GR}{B}Why buy{R}")
        for r in parsed["buy_reasons"]:
            print(f"  {GR}+{R}  {r}")
        print()

    if parsed["sell_reasons"]:
        print(f"  {RD}{B}Why sell{R}")
        for r in parsed["sell_reasons"]:
            print(f"  {RD}−{R}  {r}")
        print()

    print(hr("━"))


def print_more(facts, det, parsed, scores):
    """Extended details: per-component table + raw data."""
    bc    = scores["blended_components"]
    dn    = scores["det_norm"]
    ai    = parsed["ratings"]

    print()
    print(f"  {GY}{B}Extended details{R}")
    print(hr("─"))

    # Component table
    hdr = (f"  {GY}{'Component':<14}  {'AI':>5}  {'Det(norm)':>9}  "
           f"{'Blended':>7}  {'Weight':>6}{R}")
    sep = f"  {GY}{'-'*14}  {'-'*5}  {'-'*9}  {'-'*7}  {'-'*6}{R}"
    print(hdr)
    print(sep)
    for k in COMPONENT_ORDER:
        av = ai.get(k, 0)
        dv = dn.get(k, 0)
        bv = bc.get(k, 0)
        print(f"  {k:<14}  "
              f"{ansi_ljust(fmt_score(av), 10)}"
              f"{ansi_ljust(fmt_score(dv), 14)}"
              f"{ansi_ljust(fmt_score(bv), 12)}"
              f"{DEFAULT_WEIGHTS[k]:>6.0%}")
    print()
    print(f"  {GY}AI weighted avg:{R}  {fmt_score(scores['ai_total'], scale=1000)}"
          f"   {GY}Det weighted avg:{R}  {fmt_score(scores['det_total'], scale=1000)}"
          f"   {GY}Blended:{R}  {fmt_score(scores['blended'], bold=True, scale=1000)}"
          f"  {GY}/1000{R}")
    print()

    # Raw data grid
    f = facts
    def _p(k):
        v = f.get(k)
        return f"{v:+.2f}%" if isinstance(v, (int, float)) else "N/A"
    def _v(k, fmt=".2f", pre="", suf=""):
        v = f.get(k)
        return f"{pre}{v:{fmt}}{suf}" if isinstance(v, (int, float)) else "N/A"

    print(f"  {GY}Raw data{R}")
    rows = [
        ("Price",         _v("price",         ".2f", "$")),
        ("Market cap",    _v("marketCapUSD",   ",.0f", "$")),
        ("P/E (TTM)",     _v("peTTM",          ".1f", suf="x")),
        ("P/B",           _v("pb",             ".1f", suf="x")),
        ("EPS (TTM)",     _v("epsTTM",         ".2f", "$")),
        ("Debt/Equity",   _v("debtToEquity",   ".2f", suf="x")),
        ("Rev growth",    _p("revenueGrowthPct")),
        ("EPS growth",    _p("epsGrowthTTMYoy")),
        ("Net margin",    _p("netMarginPct")),
        ("Gross margin",  _p("grossMarginPct")),
        ("ROE",           _p("roeTTM")),
        ("Beta",          _v("beta",           ".2f")),
        ("Div yield",     _p("dividendYieldPct")),
        ("Current ratio", _v("currentRatio",   ".2f")),
        ("1Y trend",      _p("yearTrendPct")),
        ("vs 52w high",   _p("distFrom52wHigh")),
    ]
    for i in range(0, len(rows), 2):
        lbl, v_ = rows[i]
        if i + 1 < len(rows):
            rbl, rv = rows[i + 1]
            print(f"    {GY}{lbl:<16}{R}  {v_:<14}    {GY}{rbl:<16}{R}  {rv}")
        else:
            print(f"    {GY}{lbl:<16}{R}  {v_}")
    print()
    print(f"  {GY}Sector profile:{R}  {det.get('sector_profile', 'general')}")
    print()
    print(hr("─"))


# ── History display ───────────────────────────────────────────────────────────

def print_history_detail(entry):
    """Show full breakdown for one history entry."""
    print()
    print(hr("─"))
    ticker  = entry.get("ticker", "?")
    company = entry.get("company", ticker)
    ts      = entry.get("ts", "")[:16].replace("T", "  ")
    print(f"  {B}{WH}{company}{R}  {GY}({ticker}){R}")
    print(f"  {GY}Analyzed:{R} {ts}")
    if entry.get("sector"):
        print(f"  {GY}Sector:{R} {entry['sector']}")
    print()

    if not entry.get("success"):
        err = entry.get("error", "unknown error")
        print(f"  {RD}Analysis failed:{R} {err}")
        print()
        print(hr("─"))
        return

    blended = entry.get("blended", 0)
    ai_tot  = entry.get("ai_total", 0)
    det_tot = entry.get("det_total", 0)
    outlook = entry.get("outlook", "NEUTRAL")
    pt      = entry.get("price_target")

    sc_s  = fmt_score(blended, bold=True, scale=1000)
    out_s = outlook_badge(outlook)
    print(f"  {GY}Score{R}  {ansi_ljust(sc_s, 9)}  {D}/1000{R}   {out_s}", end="")
    if pt:
        print(f"   {GY}Target{R}  {WH}${pt:.0f}{R}", end="")
    print()
    print(f"  {GY}AI total:{R} {fmt_score(ai_tot, scale=1000)}   "
          f"{GY}Det total:{R} {fmt_score(det_tot, scale=1000)}")

    bc = entry.get("blended_components")
    if bc:
        print()
        print(f"  {GY}Component breakdown (±100){R}")
        for k in COMPONENT_ORDER:
            v   = bc.get(k, 0)
            s   = ansi_ljust(fmt_score(v), 9)
            bar = make_bar(v, maxval=100)
            print(f"    {k:<14}  {s}  {bar}")

    print()
    print(hr("─"))


def print_history():
    """Print numbered list of recent analyses. Returns entries (newest first)."""
    entries = list(reversed(load_logs(30)))

    print()
    print(f"  {GY}{B}Search history{R}  {GY}(last {len(entries)}){R}")
    print(hr("─"))

    if not entries:
        print(f"  {GY}No searches yet.{R}")
        print()
        return []

    for idx, e in enumerate(entries, 1):
        ts     = e.get("ts", "")[:16].replace("T", "  ")
        ticker = e.get("ticker", "?")
        num_s  = f"{GY}{idx:>2}.{R}"
        if e.get("success"):
            score   = e.get("blended", 0)
            outlook = e.get("outlook", "NEUTRAL")
            company = e.get("company", "")
            oc      = GR if outlook == "BULLISH" else RD if outlook == "BEARISH" else YL
            print(f"  {num_s}  {GY}{ts}{R}  {B}{ticker:<6}{R}  "
                  f"{ansi_ljust(fmt_score(score, scale=1000), 9)}  "
                  f"{oc}{outlook:<7}{R}  "
                  f"{GY}{company}{R}")
        else:
            err = e.get("error", "unknown error")[:35]
            print(f"  {num_s}  {GY}{ts}{R}  {B}{ticker:<6}{R}  "
                  f"{RD}FAILED{R}  {GY}{err}{R}")

    print(hr("─"))
    print()
    return entries


# ── Analysis orchestrator ─────────────────────────────────────────────────────

def analyze_one(symbol, groq_key, finnhub_key):
    """
    Run a full analysis for one ticker with spinner UI.
    Returns (facts, det, parsed, scores) on success, or None on any error.
    """
    # 1 — Fetch market data
    spin = Spinner(f"Fetching {symbol} ...").start()
    try:
        facts = fetch_facts(symbol, finnhub_key if not IS_TESTING else None)
    except Exception as e:
        spin.stop()
        print(f"\n  {RD}Fetch error:{R} {e}", flush=True)
        save_log({"ticker": symbol, "success": False, "error": f"fetch: {e}"})
        return None
    spin.stop()

    name   = facts.get("companyName", symbol)
    sector = facts.get("sector", "")
    ind    = facts.get("industry", "")
    sep    = "  ·  " if sector and ind else ""
    print(f"  {GY}↳{R} {name}  {GY}{sector}{sep}{ind}{R}", flush=True)

    # 1b — News headlines (testing mode only)
    headlines = []
    if IS_TESTING and finnhub_key:
        spin = Spinner("Fetching news ...").start()
        try:
            headlines = fetch_finnhub_news(symbol, finnhub_key)
        except Exception:
            pass
        spin.stop()
        if headlines:
            print(f"  {GY}↳{R} {len(headlines)} recent headlines", flush=True)

    # 2 — Deterministic score
    det      = compute_score(facts, DEFAULT_WEIGHTS)
    det_disp = det_display(det)
    print(f"  {GY}↳{R} Det signal  "
          f"{fmt_score(det_disp, scale=1000)}  "
          f"{GY}/1000  ({det.get('sector_profile', '?')}){R}", flush=True)

    # 3 — LLM analysis
    prompt = build_prompt(facts, det, headlines or None)
    spin   = Spinner("Generating analysis ...").start()
    try:
        client     = Groq(api_key=groq_key)
        raw, usage = call_groq(client, prompt, spinner=spin)
    except Exception as e:
        spin.stop()
        print(f"\n  {RD}Groq error:{R} {e}", flush=True)
        save_log({"ticker": symbol, "success": False, "error": f"groq: {e}"})
        return None
    spin.stop()

    # Token usage
    if usage is not None:
        pt_tok = getattr(usage, "prompt_tokens",     "?")
        ct_tok = getattr(usage, "completion_tokens", "?")
        tt_tok = getattr(usage, "total_tokens",       "?")
        print(f"  {GY}↳{R} {pt_tok} prompt + {ct_tok} completion = {tt_tok} tokens",
              flush=True)
    else:
        print(f"  {GY}↳{R} {len(raw)} chars received", flush=True)

    # 4 — Parse
    parsed = parse_response(raw)

    if not parsed["ratings"]:
        print(f"\n  {RD}Could not parse AI response.{R}", flush=True)
        print(f"  {YL}Raw output ({len(raw)} chars):{R}", flush=True)
        print(f"  {GY}{'─' * 50}{R}", flush=True)
        for ln in raw.strip().splitlines()[:40]:
            print(f"  {GY}{ln}{R}", flush=True)
        print(f"  {GY}{'─' * 50}{R}", flush=True)
        print(flush=True)
        save_log({"ticker": symbol, "success": False,
                  "error": "parse_failed", "raw_preview": raw[:600]})
        return None

    # 5 — Blend + log
    scores = blend_scores(parsed["ratings"], det)

    save_log({
        "ticker":             symbol,
        "company":            name,
        "success":            True,
        "blended":            scores["blended"],
        "ai_total":           scores["ai_total"],
        "det_total":          scores["det_total"],
        "outlook":            scores["outlook"],
        "sector":             sector,
        "price_target":       parsed.get("price_target"),
        "blended_components": scores["blended_components"],
    })

    return facts, det, parsed, scores


# ── Interactive loop ──────────────────────────────────────────────────────────

def run(groq_key, finnhub_key):
    while True:
        print()
        print_main_menu()
        try:
            inp = input(f"\n  {CY}›{R} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not inp or inp.lower() in ("q", "quit", "exit"):
            print(f"\n  {GY}Goodbye.{R}\n")
            break

        # ── History ──────────────────────────────────────────────────────────
        if inp.lower() in ("h", "history"):
            entries = print_history()
            if not entries:
                continue
            try:
                sel = input(f"  {CY}Enter number for details, or Enter to go back:{R} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if sel.isdigit():
                idx = int(sel) - 1
                if 0 <= idx < len(entries):
                    print_history_detail(entries[idx])
                else:
                    print(f"  {RD}No entry {sel}.{R}")
            continue

        # ── Analyze ───────────────────────────────────────────────────────────
        if inp.lower() in ("a", "analyze"):
            try:
                inp = input(f"\n  {CY}Ticker:{R} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

        symbol = inp.upper()

        if symbol == "67":
            easter_egg_67()
            continue

        print()
        result = analyze_one(symbol, groq_key, finnhub_key)
        if result is None:
            continue

        facts, det, parsed, scores = result
        print_results(facts, det, parsed, scores)

        # Post-result sub-loop
        more_shown = False
        while True:
            print(f"  {GY}[M]{R} More details   "
                  f"{GY}[T]{R} Try another   "
                  f"{GY}[Q]{R} Quit")
            try:
                choice = input(f"  {CY}›{R} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return

            if choice in ("q", "quit", "exit"):
                print(f"\n  {GY}Goodbye.{R}\n")
                return
            elif choice in ("t", ""):
                break
            elif choice in ("m", "more"):
                if more_shown:
                    print(f"  {GY}(already shown above){R}")
                else:
                    print_more(facts, det, parsed, scores)
                    more_shown = True
            else:
                maybe = choice.upper()
                if re.match(r"^[A-Z.\-]{1,6}$", maybe):
                    if maybe == "67":
                        easter_egg_67()
                        break
                    print()
                    result2 = analyze_one(maybe, groq_key, finnhub_key)
                    if result2:
                        facts, det, parsed, scores = result2
                        print_results(facts, det, parsed, scores)
                        more_shown = False


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if IS_TESTING:
        groq_key    = TESTING_GROQ_KEY    or os.environ.get("GROQ_API_KEY", "")
        finnhub_key = TESTING_FINNHUB_KEY or ""
    else:
        groq_key    = (sys.argv[2] if len(sys.argv) > 2 else None) or os.environ.get("GROQ_API_KEY", "")
        finnhub_key = sys.argv[3] if len(sys.argv) > 3 else ""

    if not groq_key:
        try:
            groq_key = input(f"  {CY}Groq API key:{R} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
    if not groq_key:
        print(f"  {RD}Groq API key required.{R}")
        sys.exit(1)

    print_banner()

    if len(sys.argv) > 1 and not IS_TESTING:
        symbol = sys.argv[1].upper()
        if symbol == "67":
            easter_egg_67()
            return
        print()
        result = analyze_one(symbol, groq_key, finnhub_key)
        if result:
            facts, det, parsed, scores = result
            print_results(facts, det, parsed, scores)
            print()
            choice = input(f"  {GY}[M]{R} More details   {GY}[any]{R} Quit:  ").strip().lower()
            if choice in ("m", "more"):
                print_more(facts, det, parsed, scores)
        return

    run(groq_key, finnhub_key)


if __name__ == "__main__":
    main()
