#!/usr/bin/env python3
"""Triqui (tres en raya) para dos jugadores con interfaz gráfica en tkinter.

Reglas:
  * Jugador 1 usa la ficha X (rosa) y Jugador 2 la ficha O (cian).
  * Cada jugador dispone de TIME_LIMIT segundos en total (reloj de ajedrez):
    su reloj corre solo mientras es su turno y, si llega a cero, pierde.

Controles: clic o teclas 1-9 para jugar, N nueva partida, M sonido.
"""
from __future__ import annotations

import ctypes
import math
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

try:
    import winsound
except ImportError:  # no es Windows
    winsound = None

TIME_LIMIT = 60.0  # segundos por jugador
LOW_TIME = 10.0    # umbral de alerta del reloj
WIN_LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8),
             (0, 3, 6), (1, 4, 7), (2, 5, 8),
             (0, 4, 8), (2, 4, 6))

BG_TOP, BG_BOTTOM = "#0A0E1F", "#1B1146"
PANEL, PANEL_EDGE = "#131B3A", "#27336B"
CELL, CELL_HOVER = "#1A2349", "#26336B"
TEXT, MUTED = "#EEF1FF", "#8D97C7"
GOLD, DANGER = "#FFC857", "#FF5C5C"
ACCENT, ACCENT_HI = "#7C5CFF", "#9B82FF"

PLAYERS = (
    {"label": "Jugador 1", "name": "JUGADOR 1", "mark": "X", "color": "#FF4D8D"},
    {"label": "Jugador 2", "name": "JUGADOR 2", "mark": "O", "color": "#27E1FF"},
)


def mix(a: str, b: str, t: float) -> str:
    """Mezcla dos colores hex; t=0 -> a, t=1 -> b."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def ease_out(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


def fmt_time(sec: float) -> str:
    sec = max(sec, 0.0)
    if sec < LOW_TIME:
        return f"00:{sec:04.1f}"
    m, s = divmod(int(math.ceil(sec)), 60)
    return f"{m:02d}:{s:02d}"


class TriquiApp:
    W, H = 640, 860
    CS, GAP = 144, 12                      # tamaño de celda y separación
    BS = 3 * CS + 2 * GAP                  # lado del tablero
    BX, BY = (W - BS) // 2, 278            # esquina del tablero
    CARD_W, CARD_H, CARD_Y = 260, 105, 100
    CARD_X = (30, 350)
    BUTTONS = {
        "new": (30, 772, 310, 818),
        "reset": (330, 772, 610, 818),
        "start": (W // 2 - 130, BY + BS // 2 + 85, W // 2 + 130, BY + BS // 2 + 137),
    }
    BUTTON_LABELS = {"new": "NUEVA PARTIDA", "reset": "REINICIAR MARCADOR", "start": "¡COMENZAR!"}

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Triqui")
        root.resizable(False, False)

        dpi = root.winfo_fpixels("1i") / 96
        fit = (root.winfo_screenheight() - 110) / self.H
        self.S = max(0.6, min(dpi, fit))

        families = set(tkfont.families(root))
        pick = lambda *names: next((n for n in names if n in families), "TkDefaultFont")
        self.f_ui = pick("Segoe UI", "Helvetica Neue", "Helvetica", "DejaVu Sans")
        self.f_black = pick("Segoe UI Black", self.f_ui)
        self.f_mono = pick("Consolas", "Menlo", "Courier New")

        self.c = tk.Canvas(root, width=round(self.W * self.S), height=round(self.H * self.S),
                           highlightthickness=0, bg=BG_TOP)
        self.c.pack()
        w, h = round(self.W * self.S), round(self.H * self.S)
        root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{max(0, (root.winfo_screenheight() - h) // 2 - 30)}")

        self.wins = [0, 0]
        self.draws = 0
        self.next_starter = 0
        self.sound = True
        self.hover_cell: int | None = None
        self.hover_btn: str | None = None
        self.particles: list[dict] = []
        self.last_tick = time.monotonic()

        self.reset_board(self.next_starter)
        self.state = "ready"

        self.draw_static()
        self.c.bind("<Motion>", self.on_motion)
        self.c.bind("<Leave>", self.on_leave)
        self.c.bind("<Button-1>", self.on_click)
        root.bind("<Key>", self.on_key)
        self.tick()

    # ------------------------------------------------------------------ lógica
    def reset_board(self, starter: int) -> None:
        self.board: list[int | None] = [None] * 9
        self.placed_at: dict[int, float] = {}
        self.cur = starter
        self.remaining = [TIME_LIMIT, TIME_LIMIT]
        self.turn_start = time.monotonic()
        self.result: tuple[str, int | None] | None = None
        self.win_line: tuple[int, int, int] | None = None
        self.end_time = 0.0
        self.particles.clear()

    def new_game(self) -> None:
        starter = self.next_starter
        self.next_starter = 1 - starter
        self.reset_board(starter)
        self.state = "playing"
        self.turn_start = time.monotonic()
        self.hover_cell = None

    def start_game(self) -> None:
        self.state = "playing"
        self.turn_start = time.monotonic()
        self.next_starter = 1 - self.cur

    def reset_score(self) -> None:
        self.wins = [0, 0]
        self.draws = 0
        self.new_game()

    def time_left(self, p: int) -> float:
        if self.state == "playing" and p == self.cur:
            return max(0.0, self.remaining[p] - (time.monotonic() - self.turn_start))
        return self.remaining[p]

    def play(self, cell: int) -> None:
        if self.state != "playing" or self.board[cell] is not None:
            return
        now = time.monotonic()
        self.remaining[self.cur] = self.time_left(self.cur)
        self.board[cell] = self.cur
        self.placed_at[cell] = now
        for line in WIN_LINES:
            if all(self.board[i] == self.cur for i in line):
                self.finish("win", self.cur, line)
                return
        if all(v is not None for v in self.board):
            self.finish("draw")
            return
        self.beep([(660 if self.cur == 0 else 520, 70)])
        self.cur = 1 - self.cur
        self.turn_start = now

    def finish(self, kind: str, winner: int | None = None, line=None) -> None:
        self.state = "over"
        self.result = (kind, winner)
        self.win_line = line
        self.end_time = time.monotonic()
        self.hover_cell = None
        if winner is None:
            self.draws += 1
            self.beep([(330, 150), (262, 250)])
        else:
            self.wins[winner] += 1
            self.spawn_confetti(winner)
            if kind == "win":
                self.beep([(523, 110), (659, 110), (784, 110), (1047, 260)])
            else:
                self.beep([(300, 200), (200, 350)])

    def beep(self, seq) -> None:
        if not (self.sound and winsound):
            return

        def run():
            try:
                for freq, dur in seq:
                    winsound.Beep(freq, dur)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ eventos
    def cell_rect(self, i: int) -> tuple[float, float, float, float]:
        r, c = divmod(i, 3)
        x = self.BX + c * (self.CS + self.GAP)
        y = self.BY + r * (self.CS + self.GAP)
        return x, y, x + self.CS, y + self.CS

    def cell_at(self, x: float, y: float) -> int | None:
        for i in range(9):
            x1, y1, x2, y2 = self.cell_rect(i)
            if x1 <= x <= x2 and y1 <= y <= y2:
                return i
        return None

    def visible_buttons(self) -> list[str]:
        return (["start"] if self.state == "ready" else []) + ["new", "reset"]

    def button_at(self, x: float, y: float) -> str | None:
        for key in self.visible_buttons():
            x1, y1, x2, y2 = self.BUTTONS[key]
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

    def on_motion(self, e: tk.Event) -> None:
        x, y = e.x / self.S, e.y / self.S
        self.hover_btn = self.button_at(x, y)
        cell = self.cell_at(x, y) if self.state == "playing" else None
        self.hover_cell = cell if cell is not None and self.board[cell] is None else None
        clickable = self.hover_btn or self.hover_cell is not None
        self.c.config(cursor="hand2" if clickable else "")

    def on_leave(self, _e: tk.Event) -> None:
        self.hover_cell = self.hover_btn = None
        self.c.config(cursor="")

    def on_click(self, e: tk.Event) -> None:
        x, y = e.x / self.S, e.y / self.S
        btn = self.button_at(x, y)
        if btn in ("new", "start"):
            self.new_game() if btn == "new" else self.start_game()
        elif btn == "reset":
            self.reset_score()
        elif self.state == "playing":
            cell = self.cell_at(x, y)
            if cell is not None:
                self.play(cell)

    def on_key(self, e: tk.Event) -> None:
        k = e.keysym.lower()
        if k.isdigit() and k != "0":
            self.play(int(k) - 1)
        elif k == "n":
            self.new_game()
        elif k == "m":
            self.sound = not self.sound
        elif k in ("space", "return"):
            if self.state == "ready":
                self.start_game()
            elif self.state == "over":
                self.new_game()

    # ------------------------------------------------------------------ bucle
    def tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self.last_tick, 0.1)
        self.last_tick = now
        if self.state == "playing" and self.time_left(self.cur) <= 0:
            self.remaining[self.cur] = 0.0
            self.finish("timeout", 1 - self.cur)
        self.update_particles(dt)
        self.render(now)
        self.root.after(16, self.tick)

    # ------------------------------------------------------------------ confeti
    def spawn_confetti(self, winner: int) -> None:
        base = PLAYERS[winner]["color"]
        colors = [base, GOLD, "#FFFFFF", mix(base, "#FFFFFF", 0.5), ACCENT_HI]
        for side in (0, 1):
            ox = 20 if side == 0 else self.W - 20
            for _ in range(90):
                deg = random.uniform(35, 80) if side == 0 else random.uniform(100, 145)
                ang, speed = -math.radians(deg), random.uniform(380, 820)
                self.particles.append({
                    "x": ox, "y": self.H * 0.62,
                    "vx": math.cos(ang) * speed, "vy": math.sin(ang) * speed,
                    "color": random.choice(colors), "size": random.uniform(6, 12),
                    "rot": random.uniform(0, math.tau), "vr": random.uniform(-9, 9),
                })

    def update_particles(self, dt: float) -> None:
        alive = []
        for p in self.particles:
            p["vy"] += 900 * dt
            p["vx"] *= 1 - 0.8 * dt
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["rot"] += p["vr"] * dt
            if p["y"] < self.H + 30:
                alive.append(p)
        self.particles = alive

    # ------------------------------------------------------------------ primitivas
    def _px(self, pts):
        return [v * self.S for v in pts]

    def _w(self, w: float) -> int:
        return max(1, round(w * self.S))

    def font(self, px: int, family: str | None = None, bold: bool = False):
        return (family or self.f_ui, -max(1, round(px * self.S)), "bold" if bold else "normal")

    def bg_at(self, y: float) -> str:
        return mix(BG_TOP, BG_BOTTOM, min(max(y / self.H, 0), 1))

    def line(self, pts, color, width, tag="dyn") -> None:
        self.c.create_line(*self._px(pts), fill=color, width=self._w(width),
                           capstyle=tk.ROUND, joinstyle=tk.ROUND, tags=tag)

    def rrect(self, x1, y1, x2, y2, r, fill="", outline="", width=1, tag="dyn") -> None:
        r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
        pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y1 + r, x2, y2 - r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2,
               x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r,
               x1, y1 + r, x1, y1]
        self.c.create_polygon(*self._px(pts), smooth=True, fill=fill, outline=outline,
                              width=self._w(width) if outline else 0, tags=tag)

    def text(self, x, y, s, color, size, family=None, bold=False, anchor="center", tag="dyn") -> None:
        self.c.create_text(x * self.S, y * self.S, text=s, fill=color,
                           font=self.font(size, family, bold), anchor=anchor, tags=tag)

    def draw_mark(self, kind, cx, cy, half, color, progress=1.0, width=14, glow_bg=None, tag="dyn") -> None:
        layers = [(0, color)]
        if glow_bg:
            layers = [(18, mix(glow_bg, color, 0.10)), (11, mix(glow_bg, color, 0.22)),
                      (5, mix(glow_bg, color, 0.42)), (0, color)]
        for extra, col in layers:
            w = width + extra
            if kind == "X":
                t1 = min(1.0, progress * 2)
                t2 = min(1.0, max(0.0, progress * 2 - 1))
                ax, ay, bx, by = cx - half, cy - half, cx + half, cy + half
                self.line([ax, ay, ax + (bx - ax) * t1, ay + (by - ay) * t1], col, w, tag)
                if t2 > 0:
                    cx2, cy2, dx, dy = cx + half, cy - half, cx - half, cy + half
                    self.line([cx2, cy2, cx2 + (dx - cx2) * t2, cy2 + (dy - cy2) * t2], col, w, tag)
            else:
                r = half * 1.1
                box = self._px([cx - r, cy - r, cx + r, cy + r])
                if progress >= 1:
                    self.c.create_oval(*box, outline=col, width=self._w(w), tags=tag)
                elif progress > 0.01:
                    self.c.create_arc(*box, start=90, extent=-360 * progress, style=tk.ARC,
                                      outline=col, width=self._w(w), tags=tag)

    # ------------------------------------------------------------------ capa estática
    def draw_static(self) -> None:
        step = 4
        for y in range(0, self.H, step):
            self.c.create_rectangle(0, y * self.S, self.W * self.S, (y + step) * self.S + 1,
                                    fill=self.bg_at(y), width=0, tags="bg")
        # círculos decorativos en las esquinas
        for cx, cy, col in ((-40, 60, PLAYERS[0]["color"]), (self.W + 40, 520, PLAYERS[1]["color"]),
                            (self.W - 30, -30, PLAYERS[1]["color"]), (30, self.H + 20, PLAYERS[0]["color"])):
            for r, t in ((150, 0.10), (200, 0.06)):
                self.c.create_oval((cx - r) * self.S, (cy - r) * self.S, (cx + r) * self.S, (cy + r) * self.S,
                                   outline=mix(self.bg_at(cy), col, t), width=self._w(2), tags="bg")
        # título con degradado rosa -> cian
        for i, ch in enumerate("TRIQUI"):
            x, col = self.W / 2 + (i - 2.5) * 44, mix(PLAYERS[0]["color"], PLAYERS[1]["color"], i / 5)
            self.text(x, 47, ch, mix(BG_TOP, col, 0.35), 50, self.f_black, tag="bg")
            self.text(x, 43, ch, col, 50, self.f_black, tag="bg")
        self.text(self.W / 2, 82, f"Dos jugadores  ·  {int(TIME_LIMIT)} segundos cada uno", MUTED, 13, tag="bg")
        # placa del tablero
        self.rrect(self.BX - 14, self.BY - 14, self.BX + self.BS + 14, self.BY + self.BS + 14, 22,
                   fill=PANEL, outline=PANEL_EDGE, width=2, tag="bg")
        self.c.tag_lower("bg")

    # ------------------------------------------------------------------ render
    def render(self, now: float) -> None:
        self.c.delete("dyn")
        self.draw_cards(now)
        self.draw_status()
        self.draw_board(now)
        if self.state == "ready":
            self.draw_ready_overlay(now)
        for key in self.visible_buttons():
            self.draw_button(key, now)
        self.text(self.W / 2, 836, "1-9 jugar  ·  N nueva partida  ·  M sonido "
                  + ("activado" if self.sound else "silenciado"), MUTED, 12)
        self.draw_particles()

    def draw_cards(self, now: float) -> None:
        for p in (0, 1):
            self.draw_card(p, self.CARD_X[p], self.CARD_Y, now)
        cx = self.W / 2
        self.text(cx, 128, "VS", MUTED, 22, self.f_black)
        self.text(cx, 168, "EMPATES", MUTED, 10, bold=True)
        self.text(cx, 190, str(self.draws), TEXT, 20, bold=True)

    def draw_card(self, p: int, x: float, y: float, now: float) -> None:
        info, w, h = PLAYERS[p], self.CARD_W, self.CARD_H
        color = info["color"]
        active = self.state == "playing" and self.cur == p
        left = self.time_left(p)
        low = left < LOW_TIME
        pulse = 0.5 + 0.5 * math.sin(now * 5)
        if active:
            bg = self.bg_at(y + h / 2)
            for extra, t in ((16, 0.10), (10, 0.18), (5, 0.30)):
                self.rrect(x, y, x + w, y + h, 18, outline=mix(bg, color, t * (0.5 + 0.7 * pulse)), width=extra)
        self.rrect(x, y, x + w, y + h, 18, fill=mix(PANEL, color, 0.14) if active else PANEL,
                   outline=color if active else PANEL_EDGE, width=2)
        self.draw_mark(info["mark"], x + 32, y + 27, 9, color, width=4.5)
        self.text(x + 54, y + 27, info["name"], TEXT, 14, bold=True, anchor="w")
        n = self.wins[p]
        self.text(x + w - 16, y + 27, f"{n} victoria{'s' if n != 1 else ''}", GOLD if n else MUTED,
                  12, bold=True, anchor="e")
        if left <= 0:
            tcol = DANGER
        elif low:
            tcol = mix(DANGER, TEXT, 0.5 * pulse) if active else DANGER
        else:
            tcol = TEXT
        self.text(x + w / 2, y + 57, fmt_time(left), tcol, 31, self.f_mono, True)
        bx1, bx2, by1, by2 = x + 18, x + w - 18, y + 79, y + 89
        self.rrect(bx1, by1, bx2, by2, 5, fill=mix(PANEL, "#000000", 0.45))
        frac = left / TIME_LIMIT
        if frac > 0:
            self.rrect(bx1, by1, bx1 + max(10, (bx2 - bx1) * frac), by2, 5, fill=DANGER if low else color)

    def draw_status(self) -> None:
        cx, y = self.W / 2, 238
        if self.state == "ready":
            self.text(cx, y, "Pulsa COMENZAR cuando estén listos", MUTED, 20, bold=True)
        elif self.state == "playing":
            p = PLAYERS[self.cur]
            self.text(cx, y, f"Turno de {p['label']}  ·  ficha {p['mark']}", p["color"], 20, bold=True)
        else:
            kind, winner = self.result
            if kind == "draw":
                msg = "¡Empate! Gran partida"
            elif kind == "win":
                msg = f"¡{PLAYERS[winner]['label']} gana la partida!"
            else:
                msg = f"¡Se acabó el tiempo de {PLAYERS[1 - winner]['label']}! Gana {PLAYERS[winner]['label']}"
            self.text(cx, y, msg, GOLD, 20, bold=True)

    def draw_board(self, now: float) -> None:
        win_cells = set(self.win_line) if self.win_line else set()
        win_color = PLAYERS[self.result[1]]["color"] if self.result and self.result[1] is not None else None
        for i in range(9):
            x1, y1, x2, y2 = self.cell_rect(i)
            fill = CELL
            if i == self.hover_cell:
                fill = CELL_HOVER
            if i in win_cells and win_color:
                fill = mix(CELL, win_color, 0.16 + 0.10 * math.sin((now - self.end_time) * 6))
            self.rrect(x1, y1, x2, y2, 18, fill=fill)
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            owner = self.board[i]
            if owner is not None:
                prog = ease_out((now - self.placed_at[i]) / 0.35)
                info = PLAYERS[owner]
                self.draw_mark(info["mark"], cx, cy, 38, info["color"], prog, 14, glow_bg=fill)
            elif i == self.hover_cell:
                info = PLAYERS[self.cur]
                self.draw_mark(info["mark"], cx, cy, 38, mix(fill, info["color"], 0.30), 1.0, 14)
        if self.win_line and win_color:
            t = ease_out((now - self.end_time - 0.3) / 0.45)
            if t > 0:
                ax, ay = [(a + b) / 2 for a, b in zip(self.cell_rect(self.win_line[0])[:2], self.cell_rect(self.win_line[0])[2:])]
                bx, by = [(a + b) / 2 for a, b in zip(self.cell_rect(self.win_line[2])[:2], self.cell_rect(self.win_line[2])[2:])]
                d = math.hypot(bx - ax, by - ay)
                ux, uy = (bx - ax) / d, (by - ay) / d
                ax, ay, bx, by = ax - ux * 36, ay - uy * 36, bx + ux * 36, by + uy * 36
                ex, ey = ax + (bx - ax) * t, ay + (by - ay) * t
                core = mix(win_color, "#FFFFFF", 0.65)
                for extra, col in ((20, mix(CELL, win_color, 0.15)), (12, mix(CELL, win_color, 0.35)),
                                   (6, mix(CELL, win_color, 0.6)), (0, core)):
                    self.line([ax, ay, ex, ey], col, 8 + extra)

    def draw_ready_overlay(self, now: float) -> None:
        cx, cy = self.W / 2, self.BY + self.BS / 2
        self.rrect(self.BX - 14, self.BY - 14, self.BX + self.BS + 14, self.BY + self.BS + 14, 22,
                   fill=mix(PANEL, BG_TOP, 0.35), outline=PANEL_EDGE, width=2)
        ov = mix(PANEL, BG_TOP, 0.35)
        for k, dx in enumerate((-55, 55)):
            info = PLAYERS[k]
            dy = 5 * math.sin(now * 2.5 + k * math.pi)
            self.draw_mark(info["mark"], cx + dx, cy - 125 + dy, 24, info["color"], 1.0, 10, glow_bg=ov)
        self.text(cx, cy - 50, "¿LISTOS PARA JUGAR?", TEXT, 28, self.f_black)
        self.text(cx, cy - 5, "Cada jugador tiene su propia ficha de color", TEXT, 14)
        self.text(cx, cy + 22, f"y dispone de {int(TIME_LIMIT)} segundos en total para jugar", TEXT, 14)
        self.text(cx, cy + 49, "Si tu reloj llega a cero, pierdes la partida", GOLD, 14, bold=True)

    def draw_button(self, key: str, now: float) -> None:
        x1, y1, x2, y2 = self.BUTTONS[key]
        hover = self.hover_btn == key
        primary = key == "start" or (key == "new" and self.state == "over")
        if primary:
            pulse = 0.5 + 0.5 * math.sin(now * 4)
            bg = self.bg_at((y1 + y2) / 2) if key == "new" else mix(PANEL, BG_TOP, 0.35)
            for extra, t in ((14, 0.12), (8, 0.24)):
                self.rrect(x1, y1, x2, y2, 14, outline=mix(bg, ACCENT, t * (0.4 + 0.9 * pulse)), width=extra)
            self.rrect(x1, y1, x2, y2, 14, fill=ACCENT_HI if hover else ACCENT)
            self.text((x1 + x2) / 2, (y1 + y2) / 2, self.BUTTON_LABELS[key], "#FFFFFF", 16, bold=True)
        else:
            self.rrect(x1, y1, x2, y2, 14, fill=CELL_HOVER if hover else PANEL, outline=PANEL_EDGE, width=2)
            self.text((x1 + x2) / 2, (y1 + y2) / 2, self.BUTTON_LABELS[key], TEXT if hover else MUTED, 14, bold=True)

    def draw_particles(self) -> None:
        for p in self.particles:
            w = p["size"] * abs(math.cos(p["rot"])) + 1
            h = p["size"] * 0.55
            s, c = math.sin(p["rot"] * 0.5), math.cos(p["rot"] * 0.5)
            pts = []
            for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h)):
                pts += [p["x"] + dx * c - dy * s, p["y"] + dx * s + dy * c]
            self.c.create_polygon(*self._px(pts), fill=p["color"], outline="", tags="dyn")


def main() -> None:
    if sys.platform == "win32":  # evita el escalado borroso en pantallas HiDPI
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    root = tk.Tk()
    TriquiApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
