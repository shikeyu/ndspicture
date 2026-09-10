# -*- coding: utf-8 -*-
"""画布控件 - 显示瓦片图片并支持交互。

对应 VB6 中的 Picture1(主画布) 和 Picture2(文字预览)。
"""

from __future__ import annotations
from typing import Optional, List

from PySide6.QtCore import Qt, QPoint, QRect, Signal, QSize
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QMouseEvent, QPixmap, QFont
from PySide6.QtWidgets import QWidget


class TileCanvas(QWidget):
    """瓦片画布,根据 picmap 和调色板绘制,支持放大和鼠标交互。

    鼠标交互:
      左键 - 框选(按 8×8 瓦片对齐),拖动确定范围,释放后发出 selection_made
      右键 - 拾取当前像素颜色号与坐标
    """

    color_picked = Signal(int, int, int)   # x, y, color_index (右键)
    selection_made = Signal(int, int, int, int)  # x0, y0, x1, y1 像素坐标, 按8对齐 (左键)
    wheel_delta = Signal(int)              # 滚轮缩放信号 (正=向上放大, 负=向下缩小)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._picmap: List[List[int]] = []
        self._width = 0
        self._height = 0
        self._palette: Optional[object] = None  # Palette
        self._scale = 1  # 放大倍数
        self._image: Optional[QImage] = None
        self._min_size = QSize(300, 400)
        self.setMinimumSize(self._min_size)
        # 瓦片网格 (功能7)
        self._show_grid = False
        self._tile_w = 8    # 单瓦片宽(像素), 按当前图片类型变化
        self._tile_h = 8
        # 框选状态
        self._sel_start: Optional[QPoint] = None  # 起点像素坐标
        self._sel_cur: Optional[QPoint] = None    # 当前终点像素坐标
        self._sel_rect: Optional[QRect] = None     # 已完成的选择矩形(像素), 用于持续显示
        # 文字位置预览框 (像素坐标, 叠加显示)
        self._text_preview_rect: Optional[QRect] = None
        self._show_text_preview: bool = False

    def set_data(self, picmap, width, height, palette):
        self._picmap = picmap
        self._width = width
        self._height = height
        self._palette = palette
        self._rebuild_image()
        self.updateGeometry()
        self.update()

    def set_scale(self, scale: int):
        self._scale = max(1, scale)
        self.updateGeometry()
        self.update()

    def set_show_grid(self, show: bool):
        self._show_grid = bool(show)
        self.update()

    def set_tile_size(self, size: int | tuple):
        """设置瓦片宽高(像素), 用于网格线绘制。"""
        if isinstance(size, (tuple, list)) and len(size) >= 2:
            self._tile_w = max(1, int(size[0]))
            self._tile_h = max(1, int(size[1]))
        else:
            v = max(1, int(size))
            self._tile_w = v
            self._tile_h = v
        self.update()

    def set_text_preview(self, x: int, y: int, w: int, h: int, show: bool = True):
        """设置文字位置预览框 (像素坐标)。

        x,y,w,h 为图片像素坐标; show=False 时隐藏。
        """
        self._show_text_preview = bool(show)
        if show and w > 0 and h > 0:
            self._text_preview_rect = QRect(int(x), int(y), int(w), int(h))
        else:
            self._text_preview_rect = None
        self.update()

    def set_show_text_preview(self, show: bool):
        """开关文字位置预览框 (不改变坐标)。"""
        self._show_text_preview = bool(show)
        if not show:
            self._text_preview_rect = None
        self.update()

    def _rebuild_image(self):
        if self._width <= 0 or self._height <= 0 or not self._picmap:
            self._image = None
            return
        img = QImage(self._width, self._height, QImage.Format_RGB32)
        pal = self._palette
        for y in range(self._height):
            for x in range(self._width):
                idx = self._picmap[x][y] & 0xFF
                r, g, b = pal.red[idx], pal.green[idx], pal.blue[idx]
                img.setPixelColor(x, y, QColor(r, g, b))
        self._image = img

    def sizeHint(self):
        if self._image:
            return QSize(self._width * self._scale, self._height * self._scale)
        return self._min_size

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if self._image is None:
            painter.setPen(QColor(200, 200, 200))
            painter.drawText(self.rect(), Qt.AlignCenter, "请点击「显示」按钮加载图片")
            return
        # 用最近邻放大绘制
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        target = QRect(0, 0, self._width * self._scale, self._height * self._scale)
        painter.drawImage(target, self._image)
        # 叠加瓦片网格
        if self._show_grid and self._tile_w > 0 and self._tile_h > 0 and self._scale > 1:
            tw = self._tile_w * self._scale
            th = self._tile_h * self._scale
            w = self._width * self._scale
            h = self._height * self._scale
            pen = QPen(QColor(255, 255, 0, 110), 1, Qt.DotLine)
            painter.setPen(pen)
            for x in range(0, w + 1, tw):
                painter.drawLine(x, 0, x, h)
            for y in range(0, h + 1, th):
                painter.drawLine(0, y, w, y)
            # 主网格(更粗的每8个瓦片一条线) - 颜色稍深
            pen2 = QPen(QColor(255, 80, 80, 140), 1, Qt.SolidLine)
            painter.setPen(pen2)
            big_x = tw * 8
            big_y = th * 8
            if big_x >= 16:
                for x in range(0, w + 1, big_x):
                    painter.drawLine(x, 0, x, h)
            if big_y >= 16:
                for y in range(0, h + 1, big_y):
                    painter.drawLine(0, y, w, y)
        # 框选矩形 (左键拖动中 或 已完成)
        self._draw_selection(painter)
        # 文字位置预览框
        self._draw_text_preview(painter)

    def _align8(self, v: int) -> int:
        """把像素坐标向下对齐到 8 的倍数。"""
        return (v // 8) * 8

    def _sel_rect_from_points(self, p0: QPoint, p1: QPoint) -> QRect:
        """根据起点和当前点,生成按 8 对齐且限制在图片范围内的选择矩形。"""
        x0 = self._align8(min(p0.x(), p1.x()))
        y0 = self._align8(min(p0.y(), p1.y()))
        x1 = self._align8(max(p0.x(), p1.x()) + 7)  # +7 使包含的瓦片完整
        y1 = self._align8(max(p0.y(), p1.y()) + 7)
        x0 = max(0, min(x0, self._width))
        y0 = max(0, min(y0, self._height))
        x1 = max(0, min(x1, self._width))
        y1 = max(0, min(y1, self._height))
        return QRect(x0, y0, max(0, x1 - x0), max(0, y1 - y0))

    def _draw_selection(self, painter: QPainter):
        """绘制选择矩形(拖动中实时 + 已完成的)。"""
        rects = []
        if self._sel_start is not None and self._sel_cur is not None:
            rects.append(self._sel_rect_from_points(self._sel_start, self._sel_cur))
        if self._sel_rect is not None and self._sel_rect.width() > 0 and self._sel_rect.height() > 0:
            rects.append(self._sel_rect)
        for r in rects:
            sr = QRect(
                r.x() * self._scale, r.y() * self._scale,
                r.width() * self._scale, r.height() * self._scale,
            )
            # 半透明蓝色填充
            painter.fillRect(sr, QColor(0, 120, 255, 90))
            # 蓝色边框
            pen = QPen(QColor(0, 120, 255, 220), 2)
            painter.setPen(pen)
            painter.drawRect(sr)

    def _draw_text_preview(self, painter: QPainter):
        """绘制文字位置预览框 (像素坐标 -> 屏幕)。"""
        if not self._show_text_preview or self._text_preview_rect is None:
            return
        if self._image is None:
            return
        r = self._text_preview_rect
        sr = QRect(
            r.x() * self._scale, r.y() * self._scale,
            r.width() * self._scale, r.height() * self._scale,
        )
        # 半透明绿色填充
        painter.fillRect(sr, QColor(0, 220, 80, 70))
        # 绿色虚线边框
        pen = QPen(QColor(0, 220, 80, 220), 2, Qt.DashLine)
        painter.setPen(pen)
        painter.drawRect(sr)

    def mousePressEvent(self, event: QMouseEvent):
        x = int(event.position().x()) // self._scale
        y = int(event.position().y()) // self._scale
        if not (0 <= x < self._width and 0 <= y < self._height):
            return
        if event.button() == Qt.LeftButton:
            # 开始框选
            self._sel_start = QPoint(x, y)
            self._sel_cur = QPoint(x, y)
            self._sel_rect = None
            self.update()
        elif event.button() == Qt.RightButton:
            # 右键拾取颜色号与坐标
            self.color_picked.emit(x, y, self._picmap[x][y])

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._sel_start is None:
            return
        x = int(event.position().x()) // self._scale
        y = int(event.position().y()) // self._scale
        # 允许超出图片范围, _sel_rect_from_points 会裁剪
        self._sel_cur = QPoint(x, y)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton or self._sel_start is None:
            return
        x = int(event.position().x()) // self._scale
        y = int(event.position().y()) // self._scale
        self._sel_cur = QPoint(x, y)
        rect = self._sel_rect_from_points(self._sel_start, self._sel_cur)
        self._sel_start = None
        self._sel_cur = None
        if rect.width() > 0 and rect.height() > 0:
            self._sel_rect = rect
            self.selection_made.emit(rect.x(), rect.y(),
                                     rect.x() + rect.width(),
                                     rect.y() + rect.height())
        else:
            self._sel_rect = None
        self.update()

    def wheelEvent(self, event):
        # 滚轮向上 -> angleDelta >0 -> 放大
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.angleDelta().x()
        step = 1 if delta > 0 else -1
        self.wheel_delta.emit(step)
        event.accept()

    def get_pixel_color(self, x: int, y: int) -> int:
        if 0 <= x < self._width and 0 <= y < self._height:
            return self._picmap[x][y]
        return -1


class TextPreviewCanvas(QWidget):
    """文字预览画布(对应 VB6 Picture2)。

    用于绘制文字并应用描边/阴影效果,然后把像素贴到主画布。
    支持左键框选(按 8×8 对齐),与主画布框选配合可实现定向图片更新。
    """

    selection_made = Signal(int, int, int, int)  # x0, y0, x1, y1 (按8对齐)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        # GBA 屏幕尺寸 240×160, 固定不拉伸
        self.setFixedSize(240, 160)
        self._text = ""
        # Windows 中文环境优先使用微软雅黑, 回退到宋体; 默认字号 14pt, 保证完整显示
        from PySide6.QtGui import QFontDatabase
        families = set(QFontDatabase.families())
        if "Microsoft YaHei" in families or "微软雅黑" in families:
            self._font = QFont("Microsoft YaHei", 14)
        elif "SimSun" in families or "宋体" in families:
            self._font = QFont("SimSun", 14)
        else:
            self._font = QFont()
            self._font.setPointSize(14)
        self._bg_color = QColor(0, 0, 0)
        self._fg_color = QColor(255, 255, 255)
        self._edge_color = QColor(128, 128, 128)
        self._effect = "无效果"  # 无效果/描边/阴影
        # drawText(x,y) 的 y 是基线位置; 默认基线=字号上方距离(>0), 避免文字被顶部裁掉
        self._text_x = 8
        self._text_y = 24
        self._pixmap = QPixmap(self.size())
        self._pixmap.fill(Qt.black)
        # 框选状态
        self._sel_start: Optional[QPoint] = None
        self._sel_cur: Optional[QPoint] = None
        self._sel_rect: Optional[QRect] = None
        # 载入图片模式: _mode="text" 显示文字, ="image" 显示载入的点阵图
        self._mode = "text"
        self._image_picmap: Optional[List[List[int]]] = None
        self._image_w = 0
        self._image_h = 0
        self._image_palette: Optional[object] = None

    def load_image_pixels(self, picmap, width: int, height: int, palette) -> None:
        """载入点阵图到预览画布(切换到 image 模式)。

        picmap: [x][y]=调色板索引
        palette: 带 red/green/blue 列表的对象
        """
        self._mode = "image"
        self._image_picmap = picmap
        self._image_w = int(width)
        self._image_h = int(height)
        self._image_palette = palette
        self.clear_selection()
        self._render()

    def clear_loaded_image(self) -> None:
        """清除载入的图片, 切回文字模式。"""
        self._mode = "text"
        self._image_picmap = None
        self._image_w = 0
        self._image_h = 0
        self.clear_selection()
        self._render()

    def set_text(self, text: str):
        self._text = text
        self._render()

    def set_font(self, font: QFont):
        # 防御: 只接受真正的 QFont, 防止取消时传 False 等异常值
        if isinstance(font, QFont):
            self._font = font
        self._render()

    def set_colors(self, bg: QColor, fg: QColor, edge: QColor):
        self._bg_color = bg
        self._fg_color = fg
        self._edge_color = edge
        self._render()

    def set_effect(self, effect: str):
        self._effect = effect
        self._render()

    def set_text_pos(self, x: int, y: int):
        self._text_x = max(0, x)
        self._text_y = max(0, y)
        self._render()

    def move_text(self, dx: int = 0, dy: int = 0):
        nx = self._text_x + dx
        ny = self._text_y + dy
        self._text_x = max(0, min(50, nx))
        self._text_y = max(0, min(30, ny))
        self._render()

    def _draw_multiline(self, painter, x, y, text):
        """绘制多行文字。\\n 分隔的每行按行高递进 y(基线)。"""
        lines = text.split("\n")
        fm = painter.fontMetrics()
        line_h = fm.height()
        for i, line in enumerate(lines):
            painter.drawText(x, y + i * line_h, line)

    def _render(self):
        self._pixmap = QPixmap(self.size())
        if self._mode == "image" and self._image_picmap is not None:
            self._pixmap.fill(Qt.black)
            painter = QPainter(self._pixmap)
            pal = self._image_palette
            iw, ih = self._image_w, self._image_h
            for y in range(ih):
                for x in range(iw):
                    if 0 <= x < self._pixmap.width() and 0 <= y < self._pixmap.height():
                        idx = self._image_picmap[x][y] & 0xFF
                        try:
                            r, g, b = pal.red[idx], pal.green[idx], pal.blue[idx]
                        except Exception:
                            r, g, b = 0, 0, 0
                        painter.setPen(QColor(r, g, b))
                        painter.drawPoint(x, y)
            painter.end()
            self.update()
            return
        self._pixmap.fill(self._bg_color)
        painter = QPainter(self._pixmap)
        painter.setFont(self._font)
        if self._effect == "描边":
            # 8方向描边
            pen = QPen(self._edge_color)
            painter.setPen(pen)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    self._draw_multiline(painter, self._text_x + dx, self._text_y, self._text)
                    # 注意: drawText 的 y 是基线位置
            painter.setPen(QPen(self._fg_color))
            self._draw_multiline(painter, self._text_x, self._text_y, self._text)
        elif self._effect == "阴影":
            painter.setPen(QPen(self._edge_color))
            self._draw_multiline(painter, self._text_x - 1, self._text_y - 1, self._text)
            painter.setPen(QPen(self._fg_color))
            self._draw_multiline(painter, self._text_x, self._text_y, self._text)
        else:
            painter.setPen(QPen(self._fg_color))
            self._draw_multiline(painter, self._text_x, self._text_y, self._text)
        painter.end()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if not self._pixmap.isNull():
            painter.drawPixmap(0, 0, self._pixmap)
        self._draw_selection(painter)

    # ---------- 框选功能 (左键拖动, 按 8 对齐) ----------
    def _align8(self, v: int) -> int:
        return (v // 8) * 8

    def _sel_rect_from_points(self, p0: QPoint, p1: QPoint) -> QRect:
        x0 = self._align8(min(p0.x(), p1.x()))
        y0 = self._align8(min(p0.y(), p1.y()))
        x1 = self._align8(max(p0.x(), p1.x()) + 7)
        y1 = self._align8(max(p0.y(), p1.y()) + 7)
        x0 = max(0, min(x0, self.width()))
        y0 = max(0, min(y0, self.height()))
        x1 = max(0, min(x1, self.width()))
        y1 = max(0, min(y1, self.height()))
        return QRect(x0, y0, max(0, x1 - x0), max(0, y1 - y0))

    def _draw_selection(self, painter: QPainter):
        rects = []
        if self._sel_start is not None and self._sel_cur is not None:
            rects.append(self._sel_rect_from_points(self._sel_start, self._sel_cur))
        if self._sel_rect is not None and self._sel_rect.width() > 0 and self._sel_rect.height() > 0:
            rects.append(self._sel_rect)
        for r in rects:
            painter.fillRect(r, QColor(0, 120, 255, 90))
            pen = QPen(QColor(0, 120, 255, 220), 2)
            painter.setPen(pen)
            painter.drawRect(r)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton:
            return
        x = int(event.position().x())
        y = int(event.position().y())
        self._sel_start = QPoint(x, y)
        self._sel_cur = QPoint(x, y)
        self._sel_rect = None
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._sel_start is None:
            return
        x = int(event.position().x())
        y = int(event.position().y())
        self._sel_cur = QPoint(x, y)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton or self._sel_start is None:
            return
        x = int(event.position().x())
        y = int(event.position().y())
        self._sel_cur = QPoint(x, y)
        rect = self._sel_rect_from_points(self._sel_start, self._sel_cur)
        self._sel_start = None
        self._sel_cur = None
        if rect.width() > 0 and rect.height() > 0:
            self._sel_rect = rect
            self.selection_made.emit(rect.x(), rect.y(),
                                     rect.x() + rect.width(),
                                     rect.y() + rect.height())
        else:
            self._sel_rect = None
        self.update()

    def get_selection_pixels(self) -> Optional[List[List[tuple]]]:
        """返回当前框选区域的像素 RGB [x][y]=(r,g,b)，若无框选返回 None。"""
        if self._sel_rect is None or self._sel_rect.width() <= 0 or self._sel_rect.height() <= 0:
            return None
        img = self._pixmap.toImage()
        r = self._sel_rect
        x0, y0 = r.x(), r.y()
        w, h = r.width(), r.height()
        result: List[List[tuple]] = [[(0, 0, 0)] * h for _ in range(w)]
        for y in range(h):
            for x in range(w):
                c = img.pixelColor(x0 + x, y0 + y)
                result[x][y] = (c.red(), c.green(), c.blue())
        return result

    def clear_selection(self):
        """清除框选。"""
        self._sel_rect = None
        self._sel_start = None
        self._sel_cur = None
        self.update()

    def get_pixel_colors(self, width: int = 0, height: int = 0) -> List[List[tuple]]:
        """把当前文字在 (width x height) 的独立画布上按当前基线/颜色渲染, 返回像素RGB。

        关键点:
        - 独立 QPixmap(size=max(1,width), max(1,height)), 不使用控件当前 size(),
          保证贴入尺寸与用户设定的宽/高一致, 不受控件布局拉伸影响。
        - 若 width/height <=0, 回退到当前控件 size()。
        - 返回 [x][y] = (r, g, b), x∈[0, W-1], y∈[0, H-1]
        """
        if width <= 0:
            width = self.width()
        if height <= 0:
            height = self.height()
        width = max(1, int(width))
        height = max(1, int(height))
        pix = QPixmap(width, height)
        pix.fill(self._bg_color)
        painter = QPainter(pix)
        try:
            painter.setFont(self._font)
            tx = self._text_x
            ty = self._text_y
            if self._effect == "描边":
                pen = QPen(self._edge_color)
                painter.setPen(pen)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        self._draw_multiline(painter, tx + dx, ty + dy, self._text)
                painter.setPen(QPen(self._fg_color))
                self._draw_multiline(painter, tx, ty, self._text)
            elif self._effect == "阴影":
                painter.setPen(QPen(self._edge_color))
                self._draw_multiline(painter, tx - 1, ty - 1, self._text)
                painter.setPen(QPen(self._fg_color))
                self._draw_multiline(painter, tx, ty, self._text)
            else:
                painter.setPen(QPen(self._fg_color))
                self._draw_multiline(painter, tx, ty, self._text)
        finally:
            painter.end()
        img = pix.toImage()
        W = img.width()
        H = img.height()
        result: List[List[tuple]] = [[(0, 0, 0)] * H for _ in range(W)]
        for y in range(H):
            for x in range(W):
                c = img.pixelColor(x, y)
                result[x][y] = (c.red(), c.green(), c.blue())
        return result

    def sizeHint(self):
        return QSize(200, 80)
