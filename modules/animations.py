import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
"""
animations.py — FANCYBOT v3 ULTRA TERMINAL FX
╔══════════════════════════════════════════════════════════════╗
║  CINEMATIC ASCII ENGINE  ·  TRUECOLOR  ·  60fps  ·  INSANE  ║
╚══════════════════════════════════════════════════════════════╝
"""

import sys
import time
import threading
import math
import random
import shutil
from modules.banner import BANNER

# ══════════════════════════════════════════════════════════════
#  TERMINAL CONTROL
# ══════════════════════════════════════════════════════════════

ESC          = "\033["
CLEAR        = "\033[2J"
HOME         = "\033[H"
HIDE_CURSOR  = "\033[?25l"
SHOW_CURSOR  = "\033[?25h"
BOLD         = "\033[1m"
RESET        = "\033[0m"

def goto(x, y):
    return f"\033[{y};{x}H"


def clear():
    sys.stdout.write(CLEAR + HOME)
    sys.stdout.flush()


def hide_cursor():
    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.flush()


def show_cursor():
    sys.stdout.write(SHOW_CURSOR)
    sys.stdout.flush()


def get_terminal_width():
    return shutil.get_terminal_size().columns


def get_terminal_height():
    return shutil.get_terminal_size().lines

# ══════════════════════════════════════════════════════════════
#  COLOR ENGINE  —  TRUECOLOR + BG + PALETTES
# ══════════════════════════════════════════════════════════════

def get_ansi_rgb(r, g, b):           return f"\033[38;2;{r};{g};{b}m"
def get_ansi_bg_rgb(r, g, b):        return f"\033[48;2;{r};{g};{b}m"
def clamp_rgb(v):               return max(0, min(255, int(v)))

# Preset palettes  (r,g,b lambdas of time_offset t and char-index i)
PALETTES = {
    "plasma":   lambda t,i: (clamp_rgb(math.sin(t+i*0.10)*127+128),
                              clamp_rgb(math.sin(t+i*0.13+2)*127+128),
                              clamp_rgb(math.sin(t+i*0.08+4)*127+128)),
    "fire":     lambda t,i: (clamp_rgb(200+math.sin(t+i*0.15)*55),
                              clamp_rgb(math.sin(t+i*0.2+1)*100+60),
                              clamp_rgb(math.sin(t+i*0.3+3)*20+10)),
    "ice":      lambda t,i: (clamp_rgb(math.sin(t+i*0.1+1)*40+30),
                              clamp_rgb(math.sin(t+i*0.12+2)*80+140),
                              clamp_rgb(math.sin(t+i*0.09+3)*80+200)),
    "gold":     lambda t,i: (clamp_rgb(200+math.sin(t+i*0.2)*55),
                              clamp_rgb(150+math.sin(t+i*0.15+1)*80),
                              clamp_rgb(math.sin(t+i*0.25+2)*30+10)),
    "acid":     lambda t,i: (clamp_rgb(math.sin(t+i*0.2+4)*80+40),
                              clamp_rgb(200+math.sin(t+i*0.1)*55),
                              clamp_rgb(math.sin(t+i*0.3)*60+20)),
    "blood":    lambda t,i: (clamp_rgb(180+math.sin(t+i*0.15)*75),
                              clamp_rgb(math.sin(t+i*0.2+3)*20+5),
                              clamp_rgb(math.sin(t+i*0.1+1)*20+5)),
    "void":     lambda t,i: (clamp_rgb(math.sin(t+i*0.08+2)*60+80),
                              clamp_rgb(math.sin(t+i*0.11+4)*40+20),
                              clamp_rgb(180+math.sin(t+i*0.09)*75)),
}

def colorize(text, palette, time_offset, skip_spaces=True):
    fn = PALETTES.get(palette, PALETTES["plasma"])
    out = ""
    for i, c in enumerate(text):
        if skip_spaces and c == " ":
            out += c
        else:
            r, g, b = fn(time_offset, i)
            out += get_ansi_rgb(r, g, b) + c
    return out + RESET


# ══════════════════════════════════════════════════════════════
#  LAYOUT HELPERS
# ══════════════════════════════════════════════════════════════

def center_block(text):
    """Centers a multi-line block of text horizontally."""
    width = get_terminal_width()
    lines = text.split("\n")
    return "\n".join(
        " " * max(0, (width - len(line)) // 2) + line
        for line in lines
    )

def vcenter_offset(text):
    """Return row offset so text appears vertically centered."""
    height = get_terminal_height()
    lines = [line for line in text.split("\n") if line.strip()]
    return max(1, (height - len(lines)) // 2)

def print_centered(text):
    """Print block both horizontally and vertically centered."""
    row_offset = vcenter_offset(text)
    lines = text.split("\n")
    width = get_terminal_width()
    out = "\n" * row_offset
    for line in lines:
        padding = max(0, (width - len(line)) // 2)
        out += " " * padding + line + "\n"
    sys.stdout.write(out)
    sys.stdout.flush()


# ══════════════════════════════════════════════════════════════
#  SCREEN BUFFER  —  write full frames atomically
# ══════════════════════════════════════════════════════════════

class ScreenBuffer:
    """An off-screen buffer for terminal rendering to avoid flickering."""
    def __init__(self):
        self.width, self.height = get_terminal_width(), get_terminal_height()
        self._buffer = [[(" ", None)] * self.width for _ in range(self.height)]

    def put(self, x, y, char, color=None):
        """Places a character with optional color at (x, y)."""
        x, y = int(x), int(y)
        if 0 <= x < self.width and 0 <= y < self.height:
            self._buffer[y][x] = (char, color)

    def write_text(self, x, y, text, color=None):
        """Writes a string starting at (x, y)."""
        for i, char in enumerate(text):
            self.put(x + i, y, char, color)

    def flush(self):
        """Flushes the buffer to the terminal."""
        out = HOME
        for row in self._buffer:
            for char, color in row:
                if color:
                    out += color + char + RESET
                else:
                    out += char
            out += "\n"
        sys.stdout.write(out)
        sys.stdout.flush()

    def clear(self):
        """Clears the buffer."""
        self._buffer = [[(" ", None)] * self.width for _ in range(self.height)]


# ══════════════════════════════════════════════════════════════
#  PARTICLE SYSTEM  —  physics, glyphs, colors
# ══════════════════════════════════════════════════════════════

PARTICLE_GLYPHS = list("·∙•◦○◌◎●★✦✧✨⬡⬢◆◇▪▫▸▹▷▶❯❱")
EMBER_GLYPHS    = list("·∘°*+×✕✗")
SPARK_GLYPHS    = list("╱╲╴╵╶╷|─┃│")

class Particle:
    def __init__(self, x, y, glyph_set=None, palette="plasma",
                 vx=None, vy=None, life=None, gravity=0.0):
        self.x       = float(x)
        self.y       = float(y)
        self.vx      = vx if vx is not None else random.uniform(-1.2, 1.2)
        self.vy      = vy if vy is not None else random.uniform(-2.0, -0.3)
        self.life    = life if life is not None else random.randint(15, 45)
        self.max_life = self.life
        self.glyph   = random.choice(glyph_set or PARTICLE_GLYPHS)
        self.palette = palette
        self.gravity = gravity
        self.age     = 0

    @property
    def alive(self): return self.life > 0

    @property
    def alpha(self): return self.life / self.max_life  # 1.0 → 0.0

    def update(self):
        self.x   += self.vx
        self.vy  += self.gravity
        self.y   += self.vy
        self.life -= 1
        self.age  += 1
        # Slight turbulence
        self.vx  += random.uniform(-0.05, 0.05)

    def color(self, t):
        fn = PALETTES.get(self.palette, PALETTES["plasma"])
        fade = self.alpha
        r, g, b = fn(t, self.age)
        return get_ansi_rgb(clamp_rgb(r * fade), clamp_rgb(g * fade), clamp_rgb(b * fade))


class ParticleSystem:
    def __init__(self):
        self.particles = []

    def emit(self, x, y, count, **kwargs):
        for _ in range(count):
            self.particles.append(Particle(x, y, **kwargs))

    def explode(self, x, y, count=120, palette="plasma"):
        for _ in range(count):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(0.5, 3.5)
            vx    = math.cos(angle) * speed
            vy    = math.sin(angle) * speed * 0.5  # terminals are taller than wide
            life  = random.randint(20, 60)
            self.particles.append(Particle(x, y,
                vx=vx, vy=vy, life=life,
                glyph_set=PARTICLE_GLYPHS,
                palette=palette, gravity=0.04))

    def update(self):
        for particle in self.particles:
            particle.update()
        self.particles = [p for p in self.particles if p.alive]

    def render(self, buf, t):
        for particle in self.particles:
            buf.put(particle.x, particle.y, particle.glyph, particle.color(t))


# ══════════════════════════════════════════════════════════════
#  SHOCKWAVE  —  expanding ring effect
# ══════════════════════════════════════════════════════════════

class Shockwave:
    def __init__(self, cx, cy, palette="plasma"):
        self.cx, self.cy = cx, cy
        self.radius = 0.0
        self.speed  = 1.5
        self.life   = 40
        self.palette = palette

    @property
    def alive(self): return self.life > 0

    def update(self):
        self.radius += self.speed
        self.life   -= 1

    def render(self, buf, t):
        glyphs = "·∘○◌◎"
        fn = PALETTES.get(self.palette, PALETTES["plasma"])
        alpha = self.life / 40
        steps = max(1, int(self.radius * 3))
        for i in range(steps):
            angle = (2 * math.pi / steps) * i
            x = self.cx + math.cos(angle) * self.radius
            y = self.cy + math.sin(angle) * self.radius * 0.45
            r, g, b = fn(t, i)
            col = get_ansi_rgb(clamp_rgb(r * alpha), clamp_rgb(g * alpha), clamp_rgb(b * alpha))
            buf.put(x, y, random.choice(glyphs), col)


# ══════════════════════════════════════════════════════════════
#  GLITCH ENGINE
# ══════════════════════════════════════════════════════════════

def glitch_text(text, intensity=0.15):
    """Randomly corrupt characters in text for glitch effect."""
    GLITCH_CHARS = "█▓▒░╳╬╪╫▲▼◄►◆◇"
    out = ""
    for c in text:
        if c != " " and random.random() < intensity:
            out += random.choice(GLITCH_CHARS)
        else:
            out += c
    return out

def chromatic_shift(text, palette, t, shift=2):
    """Render text 3 times with slight horizontal shift for RGB split look."""
    fn = PALETTES.get(palette, PALETTES["plasma"])
    lines = text.split("\n")
    result = []
    for li, line in enumerate(lines):
        # shadow pass (red channel, offset left)
        shadow = ""
        for i, c in enumerate(line):
            if c != " ":
                r, _, _ = fn(t, i + li * 10)
                shadow += get_ansi_rgb(clamp_rgb(r), 0, 0) + c
            else:
                shadow += c
        shadow += RESET
        # main pass
        main = ""
        for i, c in enumerate(line):
            if c != " ":
                r, g, b = fn(t, i + li * 10)
                main += get_ansi_rgb(r, g, b) + c
            else:
                main += c
        main += RESET
        result.append(main)
    return "\n".join(result)


# ══════════════════════════════════════════════════════════════
#  SCANLINE / NOISE OVERLAYS
# ══════════════════════════════════════════════════════════════

NOISE_CHARS = " ·░▒▓"

def noise_background(buf, density=0.05, palette="void"):
    """Scatter noise glyphs across the whole buffer."""
    fn = PALETTES.get(palette, PALETTES["plasma"])
    t  = time.time()
    for y in range(buf.height):
        for x in range(buf.width):
            if random.random() < density:
                r, g, b = fn(t, x + y * 10)
                fade = random.uniform(0.05, 0.25)
                col  = get_ansi_rgb(clamp_rgb(r * fade), clamp_rgb(g * fade), clamp_rgb(b * fade))
                buf.put(x, y, random.choice(NOISE_CHARS), col)

def scanlines(buf, t):
    """Darken every other row slightly for CRT scanline feel."""
    # We simulate this by inserting dim horizontal rule chars
    row = int(t * 20) % buf.height
    for x in range(buf.width):
        existing_char, existing_col = buf._buffer[row][x]
        if existing_char == " ":
            buf.put(x, row, "·", get_ansi_rgb(20, 20, 20))


# ══════════════════════════════════════════════════════════════
#  BORDER / FRAME RENDERER
# ══════════════════════════════════════════════════════════════

BORDER_STYLES = {
    "double": ("╔","╗","╚","╝","═","║","╠","╣","╦","╩"),
    "heavy":  ("┏","┓","┗","┛","━","┃","┣","┫","┳","┻"),
    "ascii":  ("╓","╖","╙","╜","─","│","├","┤","┬","┴"),
    "dots":   ("·","·","·","·","·","·","·","·","·","·"),
}

def draw_border(buf, x1, y1, x2, y2, style="double", palette="plasma", t=0):
    tl, tr, bl, br, h, v, ml, mr, mt, mb = BORDER_STYLES[style]
    fn = PALETTES[palette]
    def col(i):
        r, g, b = fn(t, i)
        return get_ansi_rgb(r, g, b)

    for x in range(x1+1, x2):
        buf.put(x, y1, h, col(x))
        buf.put(x, y2, h, col(x+100))
    for y in range(y1+1, y2):
        buf.put(x1, y, v, col(y*3))
        buf.put(x2, y, v, col(y*3+50))
    buf.put(x1, y1, tl, col(0))
    buf.put(x2, y1, tr, col(10))
    buf.put(x1, y2, bl, col(20))
    buf.put(x2, y2, br, col(30))


# ══════════════════════════════════════════════════════════════
#  TEXT RENDERER  (per-char colored, onto ScreenBuffer)
# ══════════════════════════════════════════════════════════════

def render_text_to_buf(buf, text, palette, t, cx=None, cy=None, glitch=False):
    """
    Render multi-line `text` centered at (cx,cy) onto `buf`.
    cx/cy default to center of buffer.
    """
    lines = [line_str for line_str in text.split("\n")]
    if cx is None:
        cx = buf.width // 2
    if cy is None:
        cy = buf.height // 2

    # vertical center
    total_h = len(lines)
    start_y = cy - total_h // 2

    fn = PALETTES.get(palette, PALETTES["plasma"])
    for li, line in enumerate(lines):
        y = start_y + li
        disp = glitch_text(line, 0.08) if glitch else line
        start_x = cx - len(disp) // 2
        for i, c in enumerate(disp):
            if c != " ":
                r, g, b = fn(t, i + li * 13)
                buf.put(start_x + i, y, c, get_ansi_rgb(r, g, b))


# ══════════════════════════════════════════════════════════════
#  MAIN ANIMATOR
# ══════════════════════════════════════════════════════════════

class Animator:
    def __init__(self, fps=30):
        self.fps     = fps
        self.dt      = 1 / fps
        self.running = False
        self.thread  = None

    # ── internal frame pump ──
    def _loop(self, fn, duration):
        hide_cursor()
        start = time.time()
        try:
            while time.time() - start < duration:
                t0 = time.time()
                fn(time.time())
                elapsed = time.time() - t0
                sleep   = max(0, self.dt - elapsed)
                time.sleep(sleep)
        finally:
            show_cursor()

    # ─────────────────────────────────────────────
    #  GLOW  —  pulsating full-screen gradient text
    # ─────────────────────────────────────────────
    def glow(self, text, duration=2, palette="plasma"):
        def frame(t):
            buf = ScreenBuffer()
            noise_background(buf, density=0.02, palette="void")
            render_text_to_buf(buf, text, palette, t * 3)
            draw_border(buf, 1, 1, buf.width-2, buf.height-2,
                        style="double", palette=palette, t=t*3)
            buf.flush()
        self._loop(frame, duration)

    # ─────────────────────────────────────────────
    #  WAVE  —  sine-displaced rows + chromatic
    # ─────────────────────────────────────────────
    def wave(self, text, duration=3, palette="plasma"):
        lines = text.split("\n")
        def frame(t):
            buf  = ScreenBuffer()
            tt   = t * 3
            noise_background(buf, density=0.015, palette="void")
            cx   = buf.width // 2
            cy   = buf.height // 2
            total_h = len(lines)
            start_y = cy - total_h // 2
            fn = PALETTES[palette]
            for li, line in enumerate(lines):
                offset_x = int(math.sin(tt + li * 0.7) * 4)
                offset_y = int(math.sin(tt * 0.5 + li * 0.3) * 2)
                y        = start_y + li + offset_y
                start_x  = cx - len(line) // 2 + offset_x
                for i, c in enumerate(line):
                    if c != " ":
                        wave_t  = tt + i * 0.1 + li * 0.5
                        r, g, b = fn(wave_t, i)
                        buf.put(start_x + i, y, c, get_ansi_rgb(r, g, b))
            draw_border(buf, 2, 1, buf.width-3, buf.height-2,
                        style="heavy", palette=palette, t=tt)
            buf.flush()
        self._loop(frame, duration)

    # ─────────────────────────────────────────────
    #  PARTICLES  —  emitter rain + centered text
    # ─────────────────────────────────────────────
    def particles(self, text, duration=2, palette="plasma",
                  glyph_set=None, emitter="rain"):
        ps = ParticleSystem()
        def frame(t):
            buf = ScreenBuffer()
            tt  = t * 3
            # Emit new particles every frame
            if emitter == "rain":
                for _ in range(8):
                    ps.emit(random.randint(0, buf.width-1), 0,
                            count=1,
                            vy=random.uniform(0.4, 1.5),
                            vx=random.uniform(-0.1, 0.1),
                            life=random.randint(buf.height//2, buf.height),
                            glyph_set=glyph_set or SPARK_GLYPHS,
                            palette=palette, gravity=0.01)
            elif emitter == "sparks":
                for _ in range(12):
                    ps.emit(buf.width//2, buf.height//2, count=1,
                            glyph_set=glyph_set or PARTICLE_GLYPHS,
                            palette=palette, gravity=0.05)
            ps.update()
            ps.render(buf, tt)
            render_text_to_buf(buf, text, palette, tt)
            draw_border(buf, 1, 1, buf.width-2, buf.height-2,
                        style="ascii", palette=palette, t=tt)
            buf.flush()
        self._loop(frame, duration)

    # ─────────────────────────────────────────────
    #  EXPLOSION  —  shockwave + particle burst
    # ─────────────────────────────────────────────
    def explosion(self, text, duration=3, palette="fire"):
        ps     = ParticleSystem()
        waves  = []
        fired  = False

        def frame(t):
            nonlocal fired
            tt  = t * 3
            buf = ScreenBuffer()
            cx, cy = buf.width // 2, buf.height // 2

            if not fired:
                ps.explode(cx, cy, count=150, palette=palette)
                for _ in range(4):
                    waves.append(Shockwave(cx, cy, palette=palette))
                fired = True

            ps.update()
            for wave in waves:
                wave.update()
            waves[:] = [w for w in waves if w.alive]

            noise_background(buf, density=0.03, palette=palette)
            ps.render(buf, tt)
            for wave in waves:
                wave.render(buf, tt)
            render_text_to_buf(buf, text, palette, tt, glitch=True)
            draw_border(buf, 0, 0, buf.width-1, buf.height-1,
                        style="double", palette=palette, t=tt)
            buf.flush()

        self._loop(frame, duration)

    # ─────────────────────────────────────────────
    #  SCAN  —  wipe reveal line-by-line
    # ─────────────────────────────────────────────
    def scan(self, text, palette="ice", duration=2.5):
        lines = [line_str for line_str in text.split("\n")]
        def frame(t):
            elapsed = t - _start[0]
            buf     = ScreenBuffer()
            tt      = t * 3
            noise_background(buf, density=0.01, palette=palette)
            total   = len(lines)
            reveal  = int((elapsed / duration) * total * 1.5)
            cy = buf.height // 2
            sy = cy - total // 2
            fn = PALETTES[palette]
            for li, line_str in enumerate(lines):
                if li > reveal:
                    break
                y      = sy + li
                sx     = buf.width // 2 - len(line_str) // 2
                # scanline highlight on the reveal frontier
                is_frontier = (li == reveal)
                for i, c in enumerate(line_str):
                    if c != " ":
                        r, g, b = fn(tt, i + li * 7)
                        if is_frontier:
                            r = min(255, r + 80)
                            g = min(255, g + 80)
                            b = min(255, b + 80)
                        buf.put(sx + i, y, c, get_ansi_rgb(r, g, b))
            draw_border(buf, 1, 1, buf.width-2, buf.height-2,
                        style="double", palette=palette, t=tt)
            buf.flush()
        _start = [time.time()]
        self._loop(frame, duration + 0.5)

        # ─────────────────────────────────────────────

        #  MATRIX RAIN  —  full screen + text reveal

        # ─────────────────────────────────────────────

    
    def matrix(self, text, duration=3, palette="acid"):
        MATRIX_CHARS = "ｦｧｨｩｪｫｬｭｮｯｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789"
        columns = {}
        def frame(t):
            buf = ScreenBuffer()
            tt  = t * 2
            fn  = PALETTES[palette]
            # init / grow columns
            for x in range(0, buf.width, 2):
                if x not in columns or random.random() < 0.02:
                    columns[x] = {"y": random.randint(-buf.height, 0),
                                   "speed": random.uniform(0.4, 1.2),
                                   "len":   random.randint(4, buf.height // 2)}
            for x, col in columns.items():
                col["y"] += col["speed"]
                for dy in range(col["len"]):
                    y = int(col["y"]) - dy
                    if 0 <= y < buf.height:
                        c = random.choice(MATRIX_CHARS)
                        fade = 1.0 - dy / col["len"]
                        if dy == 0:
                            color = get_ansi_rgb(200, 255, 200)  # bright head
                        else:
                            r, g, b = fn(tt, x + dy * 3)
                            color = get_ansi_rgb(clamp_rgb(r*fade*0.4),
                                        clamp_rgb(g*fade),
                                        clamp_rgb(b*fade*0.4))
                        buf.put(x, y, c, color)
            render_text_to_buf(buf, text, palette, tt)
            draw_border(buf, 1, 1, buf.width-2, buf.height-2,
                        style="double", palette=palette, t=tt)
            buf.flush()
        self._loop(frame, duration)

    # ─────────────────────────────────────────────
    #  GLITCH  —  noisy/distorted flicker
    # ─────────────────────────────────────────────
    def glitch(self, text, duration=1.5, palette="plasma"):
        def frame(t):
            buf = ScreenBuffer()
            tt  = t * 5
            noise_background(buf, density=0.035, palette=palette)
            render_text_to_buf(buf, text, palette, tt, glitch=True)
            draw_border(buf, 1, 1, buf.width-2, buf.height-2,
                        style="ascii", palette=palette, t=tt)
            buf.flush()
        self._loop(frame, duration)

    # ─────────────────────────────────────────────
    #  SHATTER  —  text breaks apart & reassembles
    # ─────────────────────────────────────────────
    def shatter(self, text, duration=3, palette="blood"):
        lines   = text.split("\n")
        chars   = []
        # collect all character positions
        for li, line in enumerate(lines):
            for i, c in enumerate(line):
                if c.strip():
                    chars.append({
                        "c": c, "li": li, "ci": i,
                        "ox": i, "oy": li,
                        "vx": random.uniform(-3, 3),
                        "vy": random.uniform(-2, 2),
                    })

        def frame(t):
            elapsed = t - _start[0]
            buf     = ScreenBuffer()
            tt      = t * 3
            bw, bh  = buf.width, buf.height
            cx      = bw // 2
            cy      = bh // 2
            total_h = len(lines)
            fn      = PALETTES[palette]
            noise_background(buf, density=0.015, palette=palette)

            phase = elapsed / duration
            for ch in chars:
                if phase < 0.3:           # hold
                    tx = cx - len(lines[ch["li"]]) // 2 + ch["ci"]
                    ty = cy - total_h // 2 + ch["li"]
                elif phase < 0.6:         # shatter out
                    frac = (phase - 0.3) / 0.3
                    base_x = cx - len(lines[ch["li"]]) // 2 + ch["ci"]
                    base_y = cy - total_h // 2 + ch["li"]
                    tx = int(base_x + ch["vx"] * frac * bw * 0.4)
                    ty = int(base_y + ch["vy"] * frac * bh * 0.4)
                else:                     # reassemble
                    frac = 1.0 - (phase - 0.6) / 0.4
                    frac = max(0, frac)
                    base_x = cx - len(lines[ch["li"]]) // 2 + ch["ci"]
                    base_y = cy - total_h // 2 + ch["li"]
                    tx = int(base_x + ch["vx"] * frac * bw * 0.4)
                    ty = int(base_y + ch["vy"] * frac * bh * 0.4)

                r, g, b = fn(tt, ch["ci"] + ch["li"] * 10)
                buf.put(tx, ty, ch["c"], get_ansi_rgb(r, g, b))

            draw_border(buf, 1, 1, buf.width-2, buf.height-2,
                        style="double", palette=palette, t=tt)
            buf.flush()

        _start = [time.time()]
        self._loop(frame, duration)

    # ─────────────────────────────────────────────
    #  ASYNC RUNNER
    # ─────────────────────────────────────────────
    def run_async(self, func, *args, **kwargs):
        if self.running:
            return
        def target():
            self.running = True
            func(*args, **kwargs)
            self.running = False
        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()

    def wait(self):
        if self.thread:
            self.thread.join()


# ══════════════════════════════════════════════════════════════
#  CINEMATIC ASCII BANNERS
# ══════════════════════════════════════════════════════════════

BOOT_SCREEN = BANNER + """
           ═══════════════════════════════════════════════
           ▸ v3 ULTRA  ·  CINEMATIC ENGINE  ·  TRUECOLOR ◂
           ═══════════════════════════════════════════════
"""

SIGNAL_TEXT = """
╔══════════════════════════════╗
║                              ║
║   ⚡  SIGNAL  DETECTED  ⚡   ║
║                              ║
║   ░░░░░░░░░░░░░░░░░░░░░░░░   ║
║   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓   ║
╚══════════════════════════════╝
"""

LONG_TEXT = """
╔══════════════════════╗
║                      ║
║   ▲  L O N G  ▲     ║
║   ▲  E N T R Y ▲    ║
║                      ║
║   ████████████████   ║
╚══════════════════════╝
"""

SHORT_TEXT = """
╔══════════════════════╗
║                      ║
║   ▼  S H O R T  ▼   ║
║   ▼  E N T R Y ▼    ║
║                      ║
║   ░░░░░░░░░░░░░░░░   ║
╚══════════════════════╝
"""

WIN_TEXT = """
╔══════════════════════════╗
║                          ║
║   ✓  P R O F I T        ║
║   ✓  L O C K E D  ✓    ║
║                          ║
║   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓   ║
╚══════════════════════════╝
"""

BIG_WIN_TEXT = """
╔══════════════════════════════════╗
║                                  ║
║  💰  M A X I M U M              ║
║  💰  P R O F I T  💰            ║
║                                  ║
║  ████████████████████████████    ║
║  ░  POSITION CLOSED  ·  +PNL  ░  ║
╚══════════════════════════════════╝
"""

LOSS_TEXT = """
╔══════════════════════════════╗
║                              ║
║   ✗  S T O P   L O S S      ║
║   ✗  H I T  ✗               ║
║                              ║
║   ░░░░░░░░░░░░░░░░░░░░░░░░   ║
╚══════════════════════════════╝
"""

KILL_TEXT = """
╔══════════════════════════════════════╗
║                                      ║
║  🛑  K I L L   S W I T C H          ║
║  🛑  E N G A G E D  🛑              ║
║                                      ║
║  ██████████████████████████████████  ║
║  ░  ALL POSITIONS HALTED  ·  SAFE  ░ ║
╚══════════════════════════════════════╝
"""


# ══════════════════════════════════════════════════════════════
#  EVENT FUNCTIONS  —  each one is a unique cinematic moment
# ══════════════════════════════════════════════════════════════

anim = Animator(fps=30)

def boot():
    """Plasma wave boot sequence — the grand entrance."""
    anim.scan(BOOT_SCREEN,    palette="ice",    duration=1.5)
    anim.wave(BOOT_SCREEN,    palette="plasma", duration=3)

def signal():
    """Matrix rain descends, signal materializes from the noise."""
    anim.matrix(SIGNAL_TEXT,  palette="acid",   duration=3)

def long():
    """Upward shockwave + gold glow for a long entry."""
    anim.explosion(LONG_TEXT, palette="gold",   duration=2)
    anim.glow(LONG_TEXT,      palette="gold",   duration=1.5)

def short():
    """Ice glitch for a short entry — cold and precise."""
    anim.glitch(SHORT_TEXT,   palette="ice",    duration=1)
    anim.glow(SHORT_TEXT,     palette="ice",    duration=1.5)

def win():
    """Particle rain celebration + pulsing wave."""
    anim.particles(WIN_TEXT,  palette="acid",
                  emitter="rain", duration=2)
    anim.wave(WIN_TEXT,       palette="plasma", duration=1.5)

def big_win():
    """Full detonation — explosion, shockwave, matrix, the works."""
    anim.explosion(BIG_WIN_TEXT, palette="gold",   duration=3)
    anim.matrix(BIG_WIN_TEXT,    palette="acid",   duration=2)
    anim.glow(BIG_WIN_TEXT,      palette="gold",   duration=2)

def loss():
    """Blood-red shatter — text breaks apart then rebuilds."""
    anim.shatter(LOSS_TEXT,   palette="blood",  duration=3)
    anim.glitch(LOSS_TEXT,    palette="blood",  duration=1.5)

def kill():
    """Void glitch → total system shutdown aesthetic."""
    anim.glitch(KILL_TEXT,    palette="void",   duration=1)
    anim.explosion(KILL_TEXT, palette="blood",  duration=2)
    anim.glitch(KILL_TEXT,    palette="void",   duration=2)


# ══════════════════════════════════════════════════════════════
#  DEMO  —  run all events in sequence
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    events = [
        ("BOOT",       boot),
        ("SIGNAL",     signal),
        ("LONG ENTRY", long),
        ("SHORT ENTRY",short),
        ("WIN",        win),
        ("BIG WIN",    big_win),
        ("LOSS",       loss),
        ("KILL",       kill),
    ]

    print(HIDE_CURSOR, end="")
    try:
        for name, fn in events:
            fn()
            # brief black flash between events
            clear()
            time.sleep(0.15)
    finally:
        print(SHOW_CURSOR, end="")
        print(RESET)
        clear()
        print("\nFANCYBOT v3 ULTRA — demo complete.\n")
