"""Live video display: paints BGR numpy frames (no copy: QImage wraps the array) at ~30 fps,
with an optional framing guide that shows where the runner should be in the picture."""
from __future__ import annotations

from typing import Any, Callable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from lopanalys.core.i18n import t
from lopanalys.ui import theme


def to_qimage(img: np.ndarray) -> QImage:
    """Wrap a BGR uint8 array as QImage without copying. Keep ``img`` alive while it is used."""
    if not img.flags["C_CONTIGUOUS"]:
        img = np.ascontiguousarray(img)
    h, w = img.shape[:2]
    if img.ndim == 2:
        return QImage(img.data, w, h, img.strides[0], QImage.Format.Format_Grayscale8)
    return QImage(img.data, w, h, img.strides[0], QImage.Format.Format_BGR888)


def guide_box(view: str) -> tuple[float, float, float, float]:
    """The framing target as fractions of the image: (left, top, width, height).

    ``view`` is a camera view, or ``"bike_side"`` for a cyclist from the side: the whole rider and
    both wheels, so a wider box."""
    bw = {"rear": 0.3, "front": 0.5, "bike_side": 0.86}.get(view, 0.42)
    return (0.5 - bw / 2, 0.07, bw, 0.86)


def pose_in_guide(pose: Any, view: str) -> bool | None:
    """True when every well-seen landmark is inside the framing box (None without a pose)."""
    if pose is None or getattr(pose, "xy", None) is None:
        return None
    xy, vis = np.asarray(pose.xy, float), np.asarray(pose.visibility, float)
    w, h = float(pose.width), float(pose.height)
    seen = xy[vis > 0.5]
    if len(seen) < 8 or w <= 0 or h <= 0:
        return False
    l, tp, bw, bh = guide_box(view)
    x, y = seen[:, 0] / w, seen[:, 1] / h
    m = 0.02
    return bool((x >= l - m).all() and (x <= l + bw + m).all() and (y >= tp - m).all() and (y <= tp + bh + m).all())


class VideoView(QWidget):
    """Shows the newest frame from a getter (e.g. ``analyzer.latest``) polled by a timer."""

    def __init__(self, parent: QWidget | None = None, radius: float = 14.0) -> None:
        super().__init__(parent)
        self._array: np.ndarray | None = None
        self._img: QImage | None = None
        self._getter: Callable[[], Any] | None = None
        self._last_id: Any = None
        self.radius = radius
        self.placeholder = t("ui.video.no_camera")
        self.guide = False
        self.guide_view = "side_left"
        self.frame_count = 0
        self.status = ""            # LiveResult.status, e.g. "Ingen löpare i bild"
        self.caption = ""           # which view this is ("Bakifrån"), shown top right; "" = none
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._poll)
        self.busy = False           # a spinner instead of the camera icon while a camera starts
        self._spin = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.timeout.connect(self._turn)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(160, 90)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def attach(self, getter: Callable[[], Any] | None, fps: float = 30.0) -> None:
        """Poll ``getter()`` (returns LiveResult-like, a Frame or an ndarray) at ``fps``."""
        self._getter = getter
        self._last_id = None
        if getter is None:
            self._timer.stop()
        else:
            self._timer.start(int(1000 / fps))

    def detach(self) -> None:
        self.attach(None)

    def set_busy(self, busy: bool) -> None:
        """Show a turning spinner above the placeholder (no picture yet), or the camera icon again."""
        if busy == self.busy:
            return
        self.busy = busy
        if busy:
            self._spin_timer.start(40)
        else:
            self._spin_timer.stop()
        self.update()

    def _turn(self) -> None:
        self._spin = (self._spin + 12) % 360
        if self._img is None:
            self.update()

    def set_image(self, img: np.ndarray | None) -> None:
        self._array = img
        self._img = to_qimage(img) if img is not None else None
        self.frame_count += 1
        self.update()

    def _poll(self) -> None:
        if self._getter is None:
            return
        try:
            r = self._getter()
        except Exception:
            return
        if r is None:
            return
        img = getattr(r, "overlay", None)
        if img is None:
            frame = getattr(r, "frame", None)
            img = getattr(frame, "image", frame) if frame is not None else getattr(r, "image", r)
        if not isinstance(img, np.ndarray):
            return
        st = getattr(r, "status", "") or ""
        if st != self.status:
            self.status = st
            self.update()
        if id(img) == self._last_id:
            return
        self._last_id = id(img)
        self.set_image(img)

    def image_rect(self) -> QRectF:
        r = QRectF(self.rect())
        if self._img is None:
            iw, ih = 16, 9
        else:
            iw, ih = self._img.width(), self._img.height()
        s = min(r.width() / iw, r.height() / ih)
        w, h = iw * s, ih * s
        return QRectF(r.left() + (r.width() - w) / 2, r.top() + (r.height() - h) / 2, w, h)

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, self.radius, self.radius)
        p.setClipPath(clip)
        p.fillRect(r, QColor("#0B0F14"))
        ir = self.image_rect()
        if self._img is not None:
            p.drawImage(ir, self._img)
        else:
            s = min(r.width(), r.height())
            if self.busy:
                d = s * 0.13
                arc = QRectF(r.center().x() - d / 2, r.center().y() - s * 0.155, d, d)
                pen = QPen(theme.color("accent"), max(3.0, s * 0.012))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawArc(arc, -self._spin * 16, 270 * 16)
            else:
                p.drawPixmap(int(r.center().x() - s * 0.08), int(r.center().y() - s * 0.16),
                             theme.pixmap("camera", "muted", int(s * 0.16)))
            p.setPen(theme.color("muted"))
            p.setFont(theme.font(max(12, int(s * 0.045)), QFont.Weight.DemiBold))
            # Word-wrapped: an error message from the camera can be two or three lines.
            p.drawText(QRectF(r.left() + r.width() * 0.08, r.center().y() + s * 0.03, r.width() * 0.84, s * 0.4),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                       self.placeholder)
        if self.guide:
            self._paint_guide(p, ir)
        if self.caption:
            f = theme.font(max(11, int(ir.height() * 0.032)), QFont.Weight.DemiBold)
            p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(self.caption) + f.pixelSize() * 1.6
            h = f.pixelSize() * 2.0
            pill = QRectF(ir.right() - w - h * 0.4, ir.top() + h * 0.4, w, h)   # top right: the overlay writes top left
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(pill, h / 2, h / 2)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, self.caption)
        if self.status and self._img is not None:
            f = theme.font(max(12, int(ir.height() * 0.028)), QFont.Weight.DemiBold)
            p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(self.status) + f.pixelSize() * 1.6
            h = f.pixelSize() * 2.0
            pill = QRectF(ir.left() + h * 0.5, ir.bottom() - h * 1.5, w, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(pill, h / 2, h / 2)
            p.setPen(theme.color("watch"))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, self.status)

    def _paint_guide(self, p: QPainter, ir: QRectF) -> None:
        """Target box for the runner: head below the top line, feet above the bottom line,
        body in the middle third (side views) or centred (rear view)."""
        gl, gt, gw, gh = guide_box(self.guide_view)
        box = QRectF(ir.left() + gl * ir.width(), ir.top() + gt * ir.height(), gw * ir.width(), gh * ir.height())
        p.setClipping(False)
        shade = QPainterPath()
        shade.addRect(ir)
        hole = QPainterPath()
        hole.addRoundedRect(box, 18, 18)
        p.fillPath(shade.subtracted(hole), QColor(0, 0, 0, 110))
        pen = QPen(theme.color("accent"), 3, Qt.PenStyle.DashLine)
        pen.setDashPattern([6, 5])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box, 18, 18)
        # silhouette hint (a standing figure; a cyclist from the side gets the box only)
        if self.guide_view != "bike_side":
            c = box.center().x()
            sil = QPen(theme.color("accent", 120), 2)
            p.setPen(sil)
            head_r = box.height() * 0.055
            p.drawEllipse(QPointF(c, box.top() + box.height() * 0.1), head_r, head_r)
            p.drawLine(QPointF(c, box.top() + box.height() * 0.16), QPointF(c, box.top() + box.height() * 0.52))
            p.drawLine(QPointF(c, box.top() + box.height() * 0.52),
                       QPointF(c - box.width() * 0.12, box.bottom() - box.height() * 0.04))
            p.drawLine(QPointF(c, box.top() + box.height() * 0.52),
                       QPointF(c + box.width() * 0.12, box.bottom() - box.height() * 0.04))
        f = theme.font(max(11, int(ir.height() * 0.032)), QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(theme.color("accent"))
        p.drawText(QRectF(box.left(), box.top() - f.pixelSize() * 1.6, box.width(), f.pixelSize() * 1.5),
                   Qt.AlignmentFlag.AlignCenter, t("ui.guide.head"))
        p.drawText(QRectF(box.left(), box.bottom() + 2, box.width(), f.pixelSize() * 1.5),
                   Qt.AlignmentFlag.AlignCenter, t("ui.guide.feet"))
