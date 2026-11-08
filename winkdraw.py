"""
WinkDraw ✦  두 손으로 화면 위에 네온 그림 그리기
(패스트캠퍼스 강의자료)

  · 오른손 검지              → 커서 / 펜 끝
  · 왼손 주먹 ✊             → 그리기
  · 왼손 펴기 ✋ / 안 보임    → 커서만 이동
  · 왼손 브이 ✌ 1초          → 다음 색
  · 오른손 손바닥 ✋ 2초      → 전체 지우기 (그리는 중이 아닐 때)

모든 창 위에 투명하게 떠 있고, 마우스 클릭은 아래 창으로 그대로 통과합니다.
"""
import ctypes
import math
import os
import random
import sys
import time
import traceback
from ctypes import wintypes

import cv2
import mediapipe as mp
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core.base_options import BaseOptions
from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QConicalGradient, QFont,
                           QGradient, QGuiApplication, QIcon, QImage,
                           QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(HERE, "models")
LOG_PATH = os.path.join(HERE, "winkdraw.log")

# ───────────────────────────── 설정 ─────────────────────────────
CAM_INDEX = 0
CAM_W, CAM_H = 640, 480
# 카메라 화면 중 이 영역이 모니터 전체에 대응 (손을 조금만 움직여도 화면 끝까지 닿음)
ACTIVE_X = (0.20, 0.80)
ACTIVE_Y = (0.15, 0.70)
RIGHT_LABEL = "Left"   # 이 라벨로 나온 손을 오른손으로 취급 (왼손/오른손이 바뀌면 "Right"로)
PEN_ON_FRAMES = 2     # 왼손 주먹이 연속 n프레임 → 펜 내림
PEN_OFF_FRAMES = 3     # 주먹이 아닌 상태가 연속 n프레임 → 펜 올림
COLOR_HOLD = 1.0       # 왼손 ✌ 유지 시간(초) → 다음 색
CLEAR_HOLD = 2.0       # 오른손 ✋ 유지 시간(초) → 전체 지우기
PEN_WIDTHS = [4, 6, 9, 13]
RAINBOW_PERIOD = 360   # 레인보우 그라데이션 반복 길이(px)
MAX_PARTICLES = 900
MAX_SPARKLES = 420
PREVIEW_W, PREVIEW_H = 256, 192

PALETTE = [
    ("네온 핑크", QColor(255, 56, 200)),
    ("레인보우", None),
    ("일렉트릭 시안", QColor(0, 236, 255)),
    ("형광 라임", QColor(170, 255, 40)),
    ("레몬 옐로", QColor(255, 236, 60)),
    ("바이올렛", QColor(176, 96, 255)),
    ("선셋 코랄", QColor(255, 112, 72)),
]
GOLD = QColor(255, 228, 150)
WHITE = QColor(255, 255, 255)

# 네온 튜브 레이어: (굵기 배수, 불투명도)
GLOW_LAYERS = [(6.0, 0.045), (4.0, 0.08), (2.6, 0.18), (1.6, 0.50), (1.0, 0.95)]

HAND_CONN = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
             (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
             (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except OSError:
        pass


def clamp(v, a, b):
    return a if v < a else b if v > b else v


def rainbow_at(x, y):
    """레인보우 펜의 (x, y) 위치 색 — 그라데이션과 같은 공식. 스프라이트 캐시를 위해 양자화."""
    h = ((x + y) / (2 * RAINBOW_PERIOD)) % 1.0
    h = round(h * 36) / 36 % 1.0
    return QColor.fromHsvF(h, 0.78, 1.0)


def rainbow_brush():
    g = QLinearGradient(QPointF(0, 0), QPointF(RAINBOW_PERIOD, RAINBOW_PERIOD))
    g.setSpread(QGradient.Spread.RepeatSpread)
    for i in range(13):
        g.setColorAt(i / 12, QColor.fromHsvF((i / 12) % 1.0, 0.78, 1.0))
    return QBrush(g)


def lighten(c, k):
    return QColor(int(c.red() + (255 - c.red()) * k),
                  int(c.green() + (255 - c.green()) * k),
                  int(c.blue() + (255 - c.blue()) * k))


# ───────────────────────────── 스프라이트 ─────────────────────────────
_sprites = {}


def sprite(kind, color):
    key = (kind, color.rgb())
    pm = _sprites.get(key)
    if pm is not None:
        return pm
    size = 32 if kind == "dot" else 64
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = size / 2
    if kind == "dot":
        g = QRadialGradient(c, c, c)
        g.setColorAt(0.0, WHITE)
        col = QColor(color)
        g.setColorAt(0.16, col)
        col.setAlpha(110)
        g.setColorAt(0.42, col)
        col.setAlpha(0)
        g.setColorAt(1.0, col)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QRectF(0, 0, size, size))
    else:  # 반짝이는 ✦ 별
        halo = QRadialGradient(c, c, c * 0.55)
        col = QColor(color)
        col.setAlpha(120)
        halo.setColorAt(0.0, col)
        col.setAlpha(0)
        halo.setColorAt(1.0, col)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QRectF(c * 0.45, c * 0.45, c * 1.1, c * 1.1))
        r = c * 0.96
        path = QPainterPath()
        path.moveTo(c, c - r)
        path.quadTo(c, c, c + r, c)
        path.quadTo(c, c, c, c + r)
        path.quadTo(c, c, c - r, c)
        path.quadTo(c, c, c, c - r)
        g = QRadialGradient(c, c, r)
        g.setColorAt(0.0, WHITE)
        g.setColorAt(0.25, lighten(color, 0.55))
        g.setColorAt(1.0, color)
        p.setBrush(g)
        p.drawPath(path)
    p.end()
    _sprites[key] = pm
    return pm


# ───────────────────────────── 획(Stroke) ─────────────────────────────
class Stroke:
    def __init__(self, color, width, start):
        self.color = color  # QColor 또는 None(레인보우)
        self.width = width
        self.points = [start]

    def color_at(self, x, y):
        return self.color if self.color is not None else rainbow_at(x, y)

    def path(self):
        pts = self.points
        path = QPainterPath(pts[0])
        if len(pts) == 1:
            path.lineTo(pts[0] + QPointF(0.01, 0.01))
            return path
        if len(pts) == 2:
            path.lineTo(pts[1])
            return path
        for i in range(1, len(pts) - 1):
            mid = (pts[i] + pts[i + 1]) * 0.5
            path.quadTo(pts[i], mid)
        path.lineTo(pts[-1])
        return path


def draw_stroke(p, s):
    path = s.path()
    brush = rainbow_brush() if s.color is None else QBrush(s.color)
    p.save()
    p.setBrush(Qt.BrushStyle.NoBrush)
    for mult, alpha in GLOW_LAYERS:
        p.setOpacity(alpha)
        p.setPen(QPen(brush, s.width * mult, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)
    core = WHITE if s.color is None else lighten(s.color, 0.72)
    p.setOpacity(0.92)
    p.setPen(QPen(QBrush(core), max(1.5, s.width * 0.42), Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)
    p.restore()


class Particle:
    __slots__ = ("x", "y", "vx", "vy", "age", "life", "size", "pm", "ph", "g")

    def __init__(self, x, y, vx, vy, life, size, pm, g=70.0):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.age, self.life, self.size, self.pm = 0.0, life, size, pm
        self.ph = random.uniform(0, 6.283)
        self.g = g


class Sparkle:
    __slots__ = ("x", "y", "pm", "ph", "sp", "size")

    def __init__(self, x, y, color):
        self.x, self.y = x, y
        self.pm = sprite("star", color)
        self.ph = random.uniform(0, 6.283)
        self.sp = random.uniform(1.2, 3.2)
        self.size = random.uniform(7, 15)


class OneEuro:
    """손 떨림은 지우고 빠른 움직임은 그대로 따라가는 필터."""

    def __init__(self, min_cutoff=1.3, beta=7.0, d_cutoff=1.0):
        self.mc, self.beta, self.dc = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self):
        self.x = self.dx = self.t = None

    @staticmethod
    def _a(dt, cutoff):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        if self.t is None:
            self.x, self.dx, self.t = x, 0.0, t
            return x
        dt = max(t - self.t, 1e-3)
        self.t = t
        ad = self._a(dt, self.dc)
        self.dx = ad * (x - self.x) / dt + (1 - ad) * self.dx
        a = self._a(dt, self.mc + self.beta * abs(self.dx))
        self.x = a * x + (1 - a) * self.x
        return self.x


# ───────────────────────────── 웹캠 추적 스레드 ─────────────────────────────
def _finger_up(lm, tip, pip):
    w = lm[0]

    def d(a):
        return math.hypot((a.x - w.x) * CAM_W, (a.y - w.y) * CAM_H)
    return d(lm[tip]) > d(lm[pip]) * 1.12


def classify_hand(lm):
    """엄지는 제외하고 네 손가락으로 판단: fist / palm / peace / point / other"""
    if lm is None:
        return "none"
    i, m = _finger_up(lm, 8, 6), _finger_up(lm, 12, 10)
    r, k = _finger_up(lm, 16, 14), _finger_up(lm, 20, 18)
    n = i + m + r + k
    if n == 0:
        return "fist"
    if n == 4:
        return "palm"
    if i and m and not r and not k:
        return "peace"
    if i and not m and not r and not k:
        return "point"
    return "palm" if n >= 3 else "other"


def split_hands(hr):
    """(오른손, 왼손) 랜드마크. 거울 모드 영상을 넣으면 MediaPipe 라벨이 실제 손과 반대로 나옴."""
    found = [(hd[0].category_name, lm) for lm, hd in zip(hr.hand_landmarks, hr.handedness)]
    if len(found) == 2:
        (la, a), (lb, b) = found
        if la != lb:
            return (a, b) if la == RIGHT_LABEL else (b, a)
        # 라벨이 겹치면 화면 위치로: 거울 속 오른손은 화면 오른쪽
        return (a, b) if a[0].x > b[0].x else (b, a)
    if len(found) == 1:
        label, lm = found[0]
        return (lm, None) if label == RIGHT_LABEL else (None, lm)
    return None, None


class Tracker(QThread):
    updated = Signal(object)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.running = True
        self.want_preview = True

    def _open_camera(self):
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
            for idx in (CAM_INDEX, 1 - CAM_INDEX if CAM_INDEX in (0, 1) else 0):
                cap = cv2.VideoCapture(idx, backend)
                if cap.isOpened():
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_W)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_H)
                    ok, _ = cap.read()
                    if ok:
                        return cap
                cap.release()
        return None

    @staticmethod
    def _model(name):
        # 경로에 한글이 있어도 되도록 파일을 직접 읽어서 버퍼로 넘김
        with open(os.path.join(MODEL_DIR, name), "rb") as f:
            return BaseOptions(model_asset_buffer=f.read())

    def run(self):
        try:
            self._run()
        except Exception:
            log(traceback.format_exc())
            self.failed.emit("추적 엔진 오류 — winkdraw.log를 확인하세요")

    def _run(self):
        hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=self._model("hand_landmarker.task"),
            running_mode=vision.RunningMode.VIDEO, num_hands=2,
            min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5))
        cap = self._open_camera()
        if cap is None:
            self.failed.emit("웹캠을 열 수 없어요. 다른 앱이 카메라를 쓰고 있는지 확인하세요.")
            return
        t0 = time.monotonic()
        last_ts = -1
        fails = 0
        while self.running:
            ok, frame = cap.read()
            if not ok:
                fails += 1
                if fails > 60:
                    self.failed.emit("웹캠 영상이 끊겼어요.")
                    break
                time.sleep(0.02)
                continue
            fails = 0
            frame = cv2.flip(frame, 1)  # 거울 모드
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            ts = int((time.monotonic() - t0) * 1000)
            ts = max(ts, last_ts + 1)
            last_ts = ts
            hr = hands.detect_for_video(img, ts)
            right, left = split_hands(hr)

            data = {
                "right": (right[8].x, right[8].y) if right is not None else None,
                "right_pose": classify_hand(right),
                "left_pose": classify_hand(left),
                "preview": self._preview(frame, right, left) if self.want_preview else None,
            }
            self.updated.emit(data)
        cap.release()
        hands.close()

    @staticmethod
    def _preview(frame, right, left):
        pw, ph = PREVIEW_W, PREVIEW_H
        small = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_AREA)
        small = cv2.convertScaleAbs(small, alpha=0.8, beta=0)
        x0, x1 = int(ACTIVE_X[0] * pw), int(ACTIVE_X[1] * pw)
        y0, y1 = int(ACTIVE_Y[0] * ph), int(ACTIVE_Y[1] * ph)
        cv2.rectangle(small, (x0, y0), (x1, y1), (255, 230, 120), 1, cv2.LINE_AA)
        # 오른손 = 핑크, 왼손 = 시안 (BGR)
        for lm, col in ((right, (220, 80, 255)), (left, (255, 220, 0))):
            if lm is None:
                continue
            pts = [(int(p.x * pw), int(p.y * ph)) for p in lm]
            for a, b in HAND_CONN:
                cv2.line(small, pts[a], pts[b], col, 2, cv2.LINE_AA)
            for q in pts:
                cv2.circle(small, q, 2, (255, 255, 255), -1, cv2.LINE_AA)
            if lm is right:
                cv2.circle(small, pts[8], 6, (80, 255, 255), 2, cv2.LINE_AA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        return QImage(rgb.data, pw, ph, pw * 3, QImage.Format.Format_RGB888).copy()


# ───────────────────────────── 전역 단축키 ─────────────────────────────
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

HOTKEYS = {  # Ctrl+Alt+키
    "clear": ord("C"), "undo": ord("Z"), "color": ord("N"), "preview": ord("P"),
    "toggle_ink": ord("S"), "pause": ord("D"), "help": ord("H"), "quit": ord("Q"),
    "thinner": 0xDB, "thicker": 0xDD,
}


class HotkeyThread(QThread):
    triggered = Signal(str)

    def __init__(self):
        super().__init__()
        self.tid = None
        self.failed = []

    def run(self):
        self.tid = kernel32.GetCurrentThreadId()
        names = list(HOTKEYS)
        for i, name in enumerate(names, 1):
            if not user32.RegisterHotKey(None, i, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, HOTKEYS[name]):
                self.failed.append(name)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and 1 <= msg.wParam <= len(names):
                self.triggered.emit(names[msg.wParam - 1])
        for i in range(1, len(names) + 1):
            user32.UnregisterHotKey(None, i)

    def stop(self):
        if self.tid:
            user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)


# ───────────────────────────── 오버레이 ─────────────────────────────
class Overlay(QWidget):
    def __init__(self):
        super().__init__(None,
                         Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        screen = QGuiApplication.primaryScreen()
        self.setGeometry(screen.geometry())
        self.W, self.H = screen.geometry().width(), screen.geometry().height()
        self.dpr = screen.devicePixelRatio()

        # 그림 상태
        self.strokes = []
        self.cur = None
        self.cache = self._new_cache()
        self.fade_pix, self.fade_a = None, 0.0
        self.dust_sources = []
        self.sparkles = []
        self.particles = []
        self.spawn_acc = 0.0
        self.color_idx = 0
        self.width_idx = 1
        self.show_ink = True
        self.paused = False
        self.show_preview = True

        # 추적 상태
        self.fx, self.fy = OneEuro(), OneEuro()
        self.cursor = QPointF(self.W / 2, self.H / 2)
        self.target = QPointF(self.cursor)
        self.cursor_a = 0.0
        self.hand_visible = False
        self.last_hand = 0.0
        self.pen_down = False
        self.on_cnt = self.off_cnt = 0
        self.left_pose = "none"
        self.right_pose = "none"
        self.gesture, self.g_start, self.g_fired = "none", 0.0, False
        self.g_prog = 0.0
        self.preview_img = None
        self.error = None

        self.toasts = []
        self.t = 0.0
        self.t0 = time.perf_counter()
        self.last = self.t0
        self.help_until = 9.0

        self.font_ui = QFont("Malgun Gothic", 10)
        self.font_ui.setWeight(QFont.Weight.DemiBold)
        self.font_title = QFont("Malgun Gothic", 20)
        self.font_title.setWeight(QFont.Weight.Bold)
        self.font_small = QFont("Malgun Gothic", 9)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)
        self.top_timer = QTimer(self)
        self.top_timer.timeout.connect(self.keep_on_top)
        self.top_timer.start(2000)

    # ── 윈도우 스타일 ──
    def showEvent(self, e):
        super().showEvent(e)
        hwnd = int(self.winId())
        GWL_EXSTYLE = -20
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # LAYERED | TRANSPARENT(클릭 통과) | TOOLWINDOW(작업표시줄 숨김) | NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | 0x80000 | 0x20 | 0x80 | 0x08000000)
        self.keep_on_top()

    def keep_on_top(self):
        user32.SetWindowPos(int(self.winId()), -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010)

    def _new_cache(self):
        pm = QPixmap(int(self.W * self.dpr), int(self.H * self.dpr))
        pm.setDevicePixelRatio(self.dpr)
        pm.fill(Qt.GlobalColor.transparent)
        return pm

    # ── 상태 도우미 ──
    @property
    def color(self):
        return PALETTE[self.color_idx][1]

    def toast(self, text, dur=1.8):
        self.toasts = [t for t in self.toasts if t[0] != text][-2:]
        self.toasts.append([text, self.t, dur])

    def current_color_at(self, pt):
        return self.color if self.color is not None else rainbow_at(pt.x(), pt.y())

    # ── 추적 결과 처리 ──
    def on_track(self, d):
        now = time.perf_counter()
        self.preview_img = d["preview"]
        self.left_pose, self.right_pose = d["left_pose"], d["right_pose"]
        if d["right"]:
            ax = (d["right"][0] - ACTIVE_X[0]) / (ACTIVE_X[1] - ACTIVE_X[0])
            ay = (d["right"][1] - ACTIVE_Y[0]) / (ACTIVE_Y[1] - ACTIVE_Y[0])
            ax, ay = clamp(ax, 0.0, 1.0), clamp(ay, 0.0, 1.0)
            if not self.hand_visible:
                self.fx.reset()
                self.fy.reset()
            self.target = QPointF(self.fx(ax, now) * self.W, self.fy(ay, now) * self.H)
            if not self.hand_visible:
                self.cursor = QPointF(self.target)
            self.hand_visible = True
            self.last_hand = now
        elif now - self.last_hand > 0.3:
            self.hand_visible = False

        # 왼손 주먹 = 펜 내림 / 펴거나 안 보이면 펜 올림
        if self.left_pose == "fist":
            self.on_cnt += 1
            self.off_cnt = 0
        else:
            self.off_cnt += 1
            self.on_cnt = 0
        want = self.pen_down
        if not self.pen_down and self.on_cnt >= PEN_ON_FRAMES:
            want = True
        if self.pen_down and self.off_cnt >= PEN_OFF_FRAMES:
            want = False
        if not self.hand_visible or self.paused:
            want = False
        self.set_pen(want)

        # 제스처 (그리는 중이 아닐 때만): 왼손 ✌ → 다음 색, 오른손 ✋ → 전체 지우기
        g = "none"
        if not self.pen_down and not self.paused:
            if self.left_pose == "peace":
                g = "peace"
            elif self.hand_visible and self.right_pose == "palm":
                g = "palm"
        if g != self.gesture:
            self.gesture, self.g_start, self.g_fired = g, now, False
        need = {"palm": CLEAR_HOLD, "peace": COLOR_HOLD}.get(g)
        if need and not self.g_fired:
            self.g_prog = min(1.0, (now - self.g_start) / need)
            if self.g_prog >= 1.0:
                self.g_fired = True
                self.g_prog = 0.0
                if g == "palm":
                    self.clear_all()
                else:
                    self.next_color()
        else:
            self.g_prog = 0.0

    def on_error(self, msg):
        self.error = msg
        log(msg)

    # ── 펜 ──
    def set_pen(self, down):
        if down == self.pen_down:
            return
        self.pen_down = down
        if down:
            self.cur = Stroke(self.color, PEN_WIDTHS[self.width_idx], QPointF(self.cursor))
        elif self.cur is not None:
            self.commit(self.cur)
            self.cur = None

    def commit(self, s):
        self.strokes.append(s)
        p = QPainter(self.cache)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_stroke(p, s)
        p.end()
        self.add_sources(s)

    def add_sources(self, s):
        last, acc_d, acc_s = None, 6.0, 0.0
        for pt in s.points:
            if last is not None:
                step = math.hypot(pt.x() - last.x(), pt.y() - last.y())
                acc_d += step
                acc_s += step
            last = pt
            if acc_d >= 6.0:
                acc_d = 0.0
                self.dust_sources.append((pt.x(), pt.y(), s.color_at(pt.x(), pt.y())))
            if acc_s >= 26.0 or len(s.points) == 1:
                acc_s = 0.0
                if random.random() < 0.75:
                    sp = Sparkle(pt.x() + random.uniform(-4, 4), pt.y() + random.uniform(-4, 4),
                                 s.color_at(pt.x(), pt.y()))
                    if len(self.sparkles) < MAX_SPARKLES:
                        self.sparkles.append(sp)
                    else:
                        self.sparkles[random.randrange(MAX_SPARKLES)] = sp

    def rebuild(self):
        self.cache = self._new_cache()
        self.dust_sources, self.sparkles = [], []
        p = QPainter(self.cache)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for s in self.strokes:
            draw_stroke(p, s)
        p.end()
        for s in self.strokes:
            self.add_sources(s)

    # ── 명령 ──
    def clear_all(self):
        if not self.strokes and self.cur is None:
            return
        self.set_pen(False)
        self.fade_pix, self.fade_a = self.cache, 1.0
        srcs = self.dust_sources
        for x, y, c in random.sample(srcs, min(len(srcs), 260)):
            a = random.uniform(0, 6.283)
            v = random.uniform(30, 160)
            self.add_particle(x, y, math.cos(a) * v, math.sin(a) * v - 40,
                              random.uniform(0.8, 1.8), c, burst=True)
        self.strokes = []
        self.cache = self._new_cache()
        self.dust_sources, self.sparkles = [], []
        self.toast("✦ 깨끗하게 지웠어요")

    def undo(self):
        if self.strokes:
            self.strokes.pop()
            self.rebuild()
            self.toast("↶ 되돌리기")

    def next_color(self):
        self.color_idx = (self.color_idx + 1) % len(PALETTE)
        self.toast(f"● {PALETTE[self.color_idx][0]}")
        c = self.current_color_at(self.cursor)
        for _ in range(26):
            a = random.uniform(0, 6.283)
            v = random.uniform(60, 180)
            self.add_particle(self.cursor.x(), self.cursor.y(), math.cos(a) * v,
                              math.sin(a) * v, random.uniform(0.5, 1.1),
                              self.current_color_at(self.cursor) if self.color is None else c, burst=True)

    def command(self, name):
        if name == "clear":
            self.clear_all()
        elif name == "undo":
            self.undo()
        elif name == "color":
            self.next_color()
        elif name == "preview":
            self.show_preview = not self.show_preview
            self.toast("카메라 미리보기 " + ("켬" if self.show_preview else "끔"))
        elif name == "toggle_ink":
            self.show_ink = not self.show_ink
            self.toast("그림 " + ("보이기" if self.show_ink else "숨기기"))
        elif name == "pause":
            self.paused = not self.paused
            self.set_pen(False)
            self.toast("⏸ 일시정지" if self.paused else "▶ 다시 시작")
        elif name == "help":
            self.help_until = 0.0 if self.t < self.help_until else self.t + 12.0
        elif name in ("thinner", "thicker"):
            self.width_idx = clamp(self.width_idx + (1 if name == "thicker" else -1), 0, len(PEN_WIDTHS) - 1)
            self.toast(f"펜 굵기 {self.width_idx + 1} / {len(PEN_WIDTHS)}")
        elif name == "quit":
            QApplication.quit()

    # ── 입자 ──
    def add_particle(self, x, y, vx, vy, life, color, burst=False):
        if len(self.particles) >= MAX_PARTICLES:
            return
        r = random.random()
        if r < 0.18:
            color = GOLD if random.random() < 0.6 else WHITE
        star = random.random() < (0.38 if burst else 0.25)
        pm = sprite("star" if star else "dot", color)
        size = random.uniform(5, 9) if star else random.uniform(3.5, 7.5)
        self.particles.append(Particle(x, y, vx, vy, life, size, pm,
                                       g=random.uniform(40, 110)))

    # ── 매 프레임 ──
    def tick(self):
        now = time.perf_counter()
        dt = min(now - self.last, 0.05)
        self.last = now
        self.t = now - self.t0

        if self.hand_visible and not self.paused:
            k = 1.0 - math.exp(-dt * 22.0)
            self.cursor += (self.target - self.cursor) * k
            self.cursor_a = min(1.0, self.cursor_a + dt * 5)
        else:
            self.cursor_a = max(0.0, self.cursor_a - dt * 3)

        if self.pen_down and self.cur is not None:
            lp = self.cur.points[-1]
            if math.hypot(self.cursor.x() - lp.x(), self.cursor.y() - lp.y()) > 1.5:
                self.cur.points.append(QPointF(self.cursor))
            c = self.current_color_at(self.cursor)
            for _ in range(3):
                self.add_particle(self.cursor.x() + random.uniform(-3, 3),
                                  self.cursor.y() + random.uniform(-3, 3),
                                  random.uniform(-45, 45), random.uniform(-40, 25),
                                  random.uniform(0.5, 1.3), c)

        # 글씨에서 반짝이 가루가 떨어짐
        if self.dust_sources and self.show_ink:
            rate = min(90.0, 16.0 + len(self.dust_sources) * 0.08)
            self.spawn_acc += rate * dt
            while self.spawn_acc >= 1.0:
                self.spawn_acc -= 1.0
                x, y, c = random.choice(self.dust_sources)
                self.add_particle(x + random.uniform(-2, 2), y + random.uniform(-1, 3),
                                  random.uniform(-10, 10), random.uniform(4, 28),
                                  random.uniform(1.4, 3.2), c)

        alive = []
        for q in self.particles:
            q.age += dt
            if q.age >= q.life or q.y > self.H + 20:
                continue
            q.vy += q.g * dt
            q.vx *= 1.0 - 1.6 * dt
            q.x += (q.vx + math.sin(q.age * 3.0 + q.ph) * 12.0) * dt
            q.y += q.vy * dt
            alive.append(q)
        self.particles = alive

        if self.fade_pix is not None:
            self.fade_a -= dt * 1.6
            if self.fade_a <= 0:
                self.fade_pix = None
        self.toasts = [t for t in self.toasts if self.t - t[1] < t[2]]
        self.update()

    # ── 그리기 ──
    def paintEvent(self, e):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)

        if self.show_ink:
            if self.fade_pix is not None:
                p.setOpacity(max(0.0, self.fade_a))
                p.drawPixmap(0, 0, self.fade_pix)
                p.setOpacity(1.0)
            p.drawPixmap(0, 0, self.cache)
            if self.cur is not None:
                draw_stroke(p, self.cur)
            self.paint_sparkles(p)
        self.paint_particles(p)
        self.paint_cursor(p)
        self.paint_hud(p)
        if self.show_preview:
            self.paint_preview(p)
        self.paint_help(p)
        self.paint_toasts(p)
        p.end()

    def paint_sparkles(self, p):
        t = self.t
        for s in self.sparkles:
            v = math.sin(t * s.sp + s.ph)
            if v < 0.55:
                continue
            a = (v - 0.55) / 0.45
            sz = s.size * (0.35 + 0.85 * a)
            p.setOpacity(a)
            p.drawPixmap(QRectF(s.x - sz, s.y - sz, sz * 2, sz * 2), s.pm, QRectF(s.pm.rect()))
        p.setOpacity(1.0)

    def paint_particles(self, p):
        for q in self.particles:
            k = 1.0 - q.age / q.life
            a = (k ** 0.8) * (0.62 + 0.38 * math.sin(self.t * 17.0 + q.ph))
            sz = q.size * (0.55 + 0.45 * k) * 2.2
            p.setOpacity(clamp(a, 0.0, 1.0))
            p.drawPixmap(QRectF(q.x - sz / 2, q.y - sz / 2, sz, sz), q.pm, QRectF(q.pm.rect()))
        p.setOpacity(1.0)

    def paint_cursor(self, p):
        if self.cursor_a <= 0.01:
            return
        c = self.current_color_at(self.cursor)
        x, y = self.cursor.x(), self.cursor.y()
        p.save()
        p.setOpacity(self.cursor_a)
        if self.pen_down:
            d = sprite("dot", c)
            r = 18 + 3 * math.sin(self.t * 14)
            p.drawPixmap(QRectF(x - r, y - r, 2 * r, 2 * r), d, QRectF(d.rect()))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(WHITE)
            p.drawEllipse(QPointF(x, y), 3, 3)
        else:
            glow = QColor(c)
            glow.setAlpha(70)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(glow, 7))
            p.drawEllipse(QPointF(x, y), 14, 14)
            p.setPen(QPen(lighten(c, 0.35), 2))
            p.drawEllipse(QPointF(x, y), 14, 14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(lighten(c, 0.5))
            p.drawEllipse(QPointF(x, y), 2.2, 2.2)
            st = sprite("star", c)
            for i in range(2):
                ang = self.t * 2.4 + i * math.pi
                sx, sy = x + math.cos(ang) * 22, y + math.sin(ang) * 22
                s = 5.5 + 1.5 * math.sin(self.t * 6 + i)
                p.drawPixmap(QRectF(sx - s, sy - s, 2 * s, 2 * s), st, QRectF(st.rect()))
        if self.g_prog > 0.02:
            label = "지우는 중…" if self.gesture == "palm" else "색 바꾸는 중…"
            p.setPen(QPen(QColor(255, 255, 255, 60), 4))
            p.drawEllipse(QPointF(x, y), 30, 30)
            p.setPen(QPen(lighten(c, 0.2), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(x - 30, y - 30, 60, 60), 90 * 16, -int(360 * 16 * self.g_prog))
            self._text_pill(p, QPointF(x, y + 52), label, c)
        p.restore()

    def _text_pill(self, p, center, text, accent, font=None):
        p.setFont(font or self.font_ui)
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 28, fm.height() + 12
        r = QRectF(center.x() - w / 2, center.y() - h / 2, w, h)
        border = QColor(accent)
        border.setAlpha(150)
        p.setPen(QPen(border, 1.2))
        p.setBrush(QColor(14, 8, 28, 190))
        p.drawRoundedRect(r, h / 2, h / 2)
        p.setPen(WHITE)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        return r

    def paint_hud(self, p):
        if self.error:
            state = "⚠ " + self.error
        elif self.paused:
            state = "⏸ 일시정지 (Ctrl+Alt+D)"
        elif not self.hand_visible:
            state = "오른손 검지를 카메라에 보여주세요"
        elif self.pen_down:
            state = "✎ 그리는 중"
        elif self.left_pose == "none":
            state = "커서 이동 · 왼손 주먹을 쥐면 그려져요"
        else:
            state = "커서 이동 · 왼손 주먹 = 그리기"
        name = PALETTE[self.color_idx][0]
        text = f"{name}   ·   {state}"
        p.save()
        p.setFont(self.font_ui)
        fm = p.fontMetrics()
        h = fm.height() + 16
        w = fm.horizontalAdvance(text) + h + 30
        r = QRectF(22, self.H - h - 60, w, h)
        accent = self.color if self.color is not None else QColor(255, 120, 230)
        border = QColor(accent)
        border.setAlpha(140)
        p.setOpacity(0.92)
        p.setPen(QPen(border, 1.2))
        p.setBrush(QColor(14, 8, 28, 185))
        p.drawRoundedRect(r, h / 2, h / 2)
        sw = QRectF(r.left() + 9, r.top() + 7, h - 14, h - 14)
        if self.color is None:
            g = QConicalGradient(sw.center(), self.t * 90)
            for i in range(7):
                g.setColorAt(i / 6, QColor.fromHsvF((i / 6) % 1.0, 0.8, 1.0))
            p.setBrush(QBrush(g))
        else:
            p.setBrush(self.color)
        p.setPen(QPen(WHITE, 1.5))
        p.drawEllipse(sw)
        p.setPen(QColor(255, 120, 120) if self.error else WHITE)
        p.drawText(QRectF(sw.right() + 10, r.top(), w, h),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        p.restore()

    def paint_preview(self, p):
        pw, ph = PREVIEW_W, PREVIEW_H
        bar_h = 50
        x, y = self.W - pw - 22, self.H - ph - bar_h - 60
        box = QRectF(x, y, pw, ph + bar_h)
        p.save()
        p.setOpacity(0.95)
        p.setPen(QPen(QColor(255, 90, 220, 150), 1.2))
        p.setBrush(QColor(14, 8, 28, 200))
        p.drawRoundedRect(box.adjusted(-1, -1, 1, 1), 14, 14)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(x, y, pw, ph), 13, 13)
        p.setClipPath(clip)
        if self.preview_img is not None:
            p.drawImage(QRectF(x, y, pw, ph), self.preview_img)
        else:
            p.setPen(QColor(255, 255, 255, 160))
            p.setFont(self.font_small)
            p.drawText(QRectF(x, y, pw, ph), Qt.AlignmentFlag.AlignCenter, "카메라 준비 중…")
        p.setClipping(False)
        # 손 상태 표시
        p.setFont(self.font_small)
        pose_txt = {"fist": "✊ 주먹 → 그리기", "palm": "✋ 펴짐 → 커서", "peace": "✌ 브이 → 색 변경",
                    "point": "☝ 검지", "other": "… 인식 중", "none": "안 보임 → 커서"}
        rows = (("왼손", pose_txt[self.left_pose], QColor(0, 220, 255), self.left_pose == "fist"),
                ("오른손", "☝ 커서" if self.hand_visible else "안 보임", QColor(255, 80, 220),
                 self.hand_visible))
        for i, (lab, txt, col, on) in enumerate(rows):
            by = y + ph + 8 + i * 20
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col if on else QColor(255, 255, 255, 50))
            p.drawEllipse(QPointF(x + 14, by + 8), 4.5, 4.5)
            p.setPen(QColor(255, 255, 255, 210))
            p.drawText(QRectF(x + 26, by, 50, 16), Qt.AlignmentFlag.AlignVCenter, lab)
            p.setPen(col.lighter(130) if on else QColor(255, 255, 255, 150))
            p.drawText(QRectF(x + 76, by, pw - 84, 16), Qt.AlignmentFlag.AlignVCenter, txt)
        p.restore()

    def paint_help(self, p):
        if self.t >= self.help_until:
            return
        a = clamp(min(self.t / 0.6, (self.help_until - self.t) / 0.8), 0.0, 1.0)
        lines = [
            ("☝  오른손 검지", "커서 이동"),
            ("✊  왼손 주먹 쥐기", "그리기"),
            ("✋  왼손 펴기 / 안 보임", "커서만 이동"),
            ("✌  왼손 브이 1초", "다음 색"),
            ("✋  오른손 손바닥 2초", "전체 지우기"),
        ]
        keys = "Ctrl+Alt +  Z 되돌리기 · C 지우기 · N 색 · [ ] 굵기\nP 미리보기 · S 그림 숨김 · D 일시정지 · H 도움말 · Q 종료"
        w, h = 580, 366
        r = QRectF((self.W - w) / 2, (self.H - h) / 2 - 40, w, h)
        p.save()
        p.setOpacity(a)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, QColor(255, 60, 200, 200))
        g.setColorAt(0.5, QColor(170, 90, 255, 200))
        g.setColorAt(1, QColor(0, 230, 255, 200))
        p.setPen(QPen(QBrush(g), 2))
        p.setBrush(QColor(12, 6, 26, 215))
        p.drawRoundedRect(r, 22, 22)
        p.setFont(self.font_title)
        tg = QLinearGradient(r.left() + 150, 0, r.right() - 150, 0)
        tg.setColorAt(0, QColor(255, 120, 230))
        tg.setColorAt(1, QColor(120, 240, 255))
        p.setPen(QPen(QBrush(tg), 1))
        p.drawText(QRectF(r.left(), r.top() + 22, w, 40), Qt.AlignmentFlag.AlignCenter, "✦ WinkDraw ✦")
        p.setFont(self.font_ui)
        for i, (k, v) in enumerate(lines):
            yy = r.top() + 86 + i * 36
            p.setPen(WHITE)
            p.drawText(QRectF(r.left() + 70, yy, 260, 30), Qt.AlignmentFlag.AlignVCenter, k)
            p.setPen(QColor(255, 236, 120))
            p.drawText(QRectF(r.left() + 330, yy, 180, 30), Qt.AlignmentFlag.AlignVCenter, "→  " + v)
        p.setFont(self.font_small)
        p.setPen(QColor(255, 255, 255, 170))
        p.drawText(QRectF(r.left() + 20, r.bottom() - 74, w - 40, 56), Qt.AlignmentFlag.AlignCenter, keys)
        st = sprite("star", QColor(255, 120, 230))
        for i in range(4):
            sx = r.left() + (36 if i % 2 == 0 else w - 36)
            sy = r.top() + (36 if i < 2 else h - 36)
            s = 7 + 4 * math.sin(self.t * 3 + i * 1.7)
            p.drawPixmap(QRectF(sx - s, sy - s, 2 * s, 2 * s), st, QRectF(st.rect()))
        p.restore()

    def paint_toasts(self, p):
        p.save()
        for i, (text, t0, dur) in enumerate(self.toasts):
            age = self.t - t0
            a = clamp(min(age / 0.2, (dur - age) / 0.4), 0.0, 1.0)
            p.setOpacity(a)
            accent = self.color if self.color is not None else QColor(255, 120, 230)
            self._text_pill(p, QPointF(self.W / 2, 70 + i * 46 - (1 - a) * 10), text, accent)
        p.restore()


# ───────────────────────────── 트레이 & 실행 ─────────────────────────────
def make_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(20, 10, 36))
    p.drawEllipse(2, 2, 60, 60)
    st = sprite("star", QColor(255, 70, 210))
    p.drawPixmap(QRectF(6, 6, 52, 52), st, QRectF(st.rect()))
    p.end()
    return QIcon(pm)


def main():
    sys.excepthook = lambda *a: log("".join(traceback.format_exception(*a)))
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    overlay = Overlay()
    tracker = Tracker()
    tracker.updated.connect(overlay.on_track)
    tracker.failed.connect(overlay.on_error)
    hotkeys = HotkeyThread()
    hotkeys.triggered.connect(overlay.command)

    tray = QSystemTrayIcon(make_icon())
    tray.setToolTip("WinkDraw ✦ 윙크로 그리기")
    menu = QMenu()
    for label, cmd in [("다음 색  (Ctrl+Alt+N)", "color"), ("되돌리기  (Ctrl+Alt+Z)", "undo"),
                       ("전체 지우기  (Ctrl+Alt+C)", "clear"), ("카메라 미리보기  (Ctrl+Alt+P)", "preview"),
                       ("그림 숨기기/보이기  (Ctrl+Alt+S)", "toggle_ink"),
                       ("일시정지  (Ctrl+Alt+D)", "pause"), ("도움말  (Ctrl+Alt+H)", "help")]:
        act = QAction(label, menu)
        act.triggered.connect(lambda _=False, c=cmd: overlay.command(c))
        menu.addAction(act)
    menu.addSeparator()
    quit_act = QAction("종료  (Ctrl+Alt+Q)", menu)
    quit_act.triggered.connect(app.quit)
    menu.addAction(quit_act)
    tray.setContextMenu(menu)
    tray.show()

    def on_preview_toggle():
        tracker.want_preview = overlay.show_preview
    hotkeys.triggered.connect(lambda _: on_preview_toggle())
    menu.triggered.connect(lambda _: on_preview_toggle())

    def shutdown():
        tracker.running = False
        hotkeys.stop()
        tracker.wait(3000)
        hotkeys.wait(1000)
        tray.hide()
    app.aboutToQuit.connect(shutdown)

    overlay.show()
    tracker.start()
    hotkeys.start()
    QTimer.singleShot(1500, lambda: hotkeys.failed and overlay.toast(
        "일부 단축키를 등록하지 못했어요: " + ", ".join(hotkeys.failed), 4))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
