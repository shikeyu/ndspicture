# -*- coding: utf-8 -*-
"""主窗口。

对应 VB6 ndspicture.frm 的 Form1,实现完整的 UI 和事件处理。
"""

from __future__ import annotations
import os
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor, QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QSplitter, QScrollArea, QGroupBox, QLabel, QLineEdit, QSpinBox, QComboBox,
    QCheckBox, QPushButton, QListWidget, QListWidgetItem, QFileDialog,
    QMessageBox, QStatusBar, QSlider, QFontDialog, QInputDialog, QSizePolicy,
    QMenu,
)
from PySide6.QtCore import QSettings

from ..core.palette import Palette
from ..core.picture_editor import PictureEditor, PicConfig, parse_addr, T_16COLOR
from ..core.gpp_project import PicItem, load_gpp, save_gpp
from ..core import picture_editor as pe
from .canvas import TileCanvas, TextPreviewCanvas


PIC_TYPES = ["16色", "256色", "超16色", "PCE16色", "PCE16色8x8",
             "16色BMP", "256色BMP", "16色MAP", "256色MAP"]
COLOR_TYPES = ["RGB 2BIT", "RGB 3BIT", "RGBA 4BIT"]
TEXT_EFFECTS = ["无效果", "描边", "阴影"]


def parse_int_or_hex(s: str) -> int:
    """解析整数或16进制字符串。支持 '0xE000' / '&hE000' / '57344'。"""
    s = s.strip()
    if not s:
        return 0
    low = s.lower()
    if low.startswith("&h"):
        return int(s[2:], 16)
    if low.startswith("0x"):
        return int(s[2:], 16)
    return int(s, 0) if (low.startswith("0x") or low.startswith("0b")) else int(s)


def _text_with_newlines(s: str) -> str:
    """把输入框中的字面量 \\n 转换为实际换行符。支持 \\n 和 \\r\\n。"""
    return s.replace("\\r\\n", "\n").replace("\\n", "\n")


class MainWindow(QMainWindow):
    """主窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ROM图片编辑器 1.1 (PySide6 重写版)")
        self.resize(1280, 800)

        self.editor = PictureEditor()
        self.items: list[PicItem] = []
        self.gpp_path = ""
        self.rom_path = ""
        self._current_idx = -1
        self._settings = QSettings("ndspicture", "ndspicture")
        self._recent_gpps: list[str] = []
        self._recent_max = 10

        self._build_ui()
        self._connect_signals()
        self._load_recent_gpp()
        self._refresh_recent_menu()
        # 恢复上次保存的字体
        saved_font = self._settings.value("text_font", "", type=str)
        if saved_font:
            from PySide6.QtGui import QFont as _QFont
            f = _QFont()
            if f.fromString(saved_font):
                self.text_preview.set_font(f)
        # 初始基线与预览控件保持一致, 避免首次点"显示"被 spin 的 0/0 覆盖
        self.spin_text_x.setValue(int(getattr(self.text_preview, "_text_x", 0)))
        self.spin_text_y.setValue(int(getattr(self.text_preview, "_text_y", 0)))
        # 默认宽/高给一个非 0 的合理初始值 (预览画布的 60×30), 方便首次显示/贴入就有裁剪上限
        self.spin_text_w.setValue(80)
        self.spin_text_h.setValue(40)
        # 撤销/重做快捷键 (Ctrl+Z / Ctrl+Y)
        QShortcut(QKeySequence.Undo, self, activated=self.on_undo)
        QShortcut(QKeySequence.Redo, self, activated=self.on_redo)
        # 初始把网格参数传递给画布 (避免先显示时网格未生效)
        self.canvas.set_show_grid(self.chk_grid.isChecked())
        try:
            cfg = self._read_config()
            self.canvas.set_tile_size(self._tile_px_for_type(cfg))
        except Exception:
            pass

    # ---------- UI 构建 ----------

    def _build_ui(self):
        # ---------- 菜单栏 ----------
        menubar = self.menuBar()
        # 文件菜单
        menu_file = menubar.addMenu("文件(&F)")
        self.act_new = QAction("新建(&N)", self)
        self.act_new.setShortcut(QKeySequence.New)
        menu_file.addAction(self.act_new)
        self.act_open_gpp = QAction("打开GPP项目(&O)...", self)
        self.act_open_gpp.setShortcut(QKeySequence.Open)
        menu_file.addAction(self.act_open_gpp)
        self.act_save_gpp = QAction("保存GPP项目(&S)...", self)
        self.act_save_gpp.setShortcut(QKeySequence.Save)
        menu_file.addAction(self.act_save_gpp)
        menu_file.addSeparator()
        # 最近工程: 同时出现在「文件(F)」子菜单里, 也作为独立顶层菜单, 方便用户直接找到
        self.menu_recent = menu_file.addMenu("最近打开的工程(&R)")
        self.menu_recent_top = menubar.addMenu("最近工程(&R)")
        self.act_clear_recent = QAction("清空最近列表", self)
        menu_file.addAction(self.act_clear_recent)
        menu_file.addSeparator()
        self.act_quit = QAction("退出(&X)", self)
        self.act_quit.setShortcut(QKeySequence.Quit)
        menu_file.addAction(self.act_quit)
        # 系统菜单 (保留VB6原标题, 占位供扩展)
        menu_sys = menubar.addMenu("系统(&S)")
        self.act_about = QAction("关于(&A)...", self)
        menu_sys.addAction(self.act_about)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # 左侧: 画布 + 文字工具
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 画布工具栏
        bar = QHBoxLayout()
        # 撤销/重做按钮
        self.btn_undo = QPushButton("↶ 撤销")
        self.btn_undo.setToolTip("撤销上一步 (Ctrl+Z)")
        self.btn_undo.setEnabled(False)
        self.btn_redo = QPushButton("↷ 重做")
        self.btn_redo.setToolTip("重做已撤销的步骤 (Ctrl+Y)")
        self.btn_redo.setEnabled(False)
        bar.addWidget(self.btn_undo)
        bar.addWidget(self.btn_redo)
        bar.addSpacing(12)
        # 网格显示开关 (功能7)
        self.chk_grid = QCheckBox("显示瓦片网格")
        self.chk_grid.setToolTip("按当前图片类型对应的瓦片尺寸叠加网格线")
        bar.addWidget(self.chk_grid)
        bar.addSpacing(12)
        self.lbl_scale = QLabel("缩放:")
        self.slider_scale = QSlider(Qt.Horizontal)
        self.slider_scale.setRange(1, 16)  # 提升上限支持更高放大
        self.slider_scale.setValue(2)
        self.slider_scale.setMaximumWidth(140)
        self.lbl_scale_val = QLabel("2x")
        self.lbl_color = QLabel("颜色号: -")
        self.lbl_color.setStyleSheet("color: blue;")
        bar.addWidget(self.lbl_scale)
        bar.addWidget(self.slider_scale)
        bar.addWidget(self.lbl_scale_val)
        bar.addStretch()
        bar.addWidget(self.lbl_color)
        left_layout.addLayout(bar)

        # 画布滚动区
        self.canvas = TileCanvas()
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignCenter)
        left_layout.addWidget(self.scroll, 1)

        # 文字工具组 — 外层 HBox: [左=预览画布 | 中=QGrid参数 | 右=方向键十字]
        text_group = QGroupBox("文字插入工具栏")
        outer = QHBoxLayout(text_group)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(8)

        # --- 左: 文字预览画布 ---
        self.text_preview = TextPreviewCanvas()
        self.text_preview.setMinimumHeight(96)
        outer.addWidget(self.text_preview, 0)

        # --- 中: 参数区域 + 文字输入 (QVBoxLayout 套 QGridLayout + 底部 HBox) ---
        param_vbox = QVBoxLayout()
        param_vbox.setSpacing(6)

        # 3 行 × 9 列 统一列宽网格 (每行列宽绝对一致, 避免 addSpacing 估算误差)
        #   col0: 颜色标签    col1: 颜色值 SpinBox
        #   col2: 坐标名标签  col3: 坐标值 SpinBox
        #   col4: 尺寸名标签  col5: 尺寸值 / 显示按钮
        #   col6: 效果下拉框 / 更新按钮
        #   col7: 字体按钮    / 贴入按钮
        #   col8: (stretch 占满剩余)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        LBL_W, SPIN_W, COMBO_W, BTN_W = 54, 90, 94, 68

        def _lbl(text: str, w=LBL_W):
            lab = QLabel(text)
            lab.setFixedWidth(w)
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return lab

        def _spin(vmin=0, vmax=9999, val=0, w=SPIN_W):
            sp = QSpinBox()
            sp.setRange(vmin, vmax)
            sp.setValue(val)
            sp.setFixedWidth(w)
            return sp

        # ---- 第 0 行: 背景色 | 横坐标 | 宽 | 无效果 | 字体 ----
        grid.addWidget(_lbl("背景色"), 0, 0)
        self.spin_bg = _spin(0, 255, 0)
        grid.addWidget(self.spin_bg, 0, 1)
        grid.addWidget(_lbl("横坐标"), 0, 2)
        self.spin_text_x = _spin(0, 9999, 0)
        grid.addWidget(self.spin_text_x, 0, 3)
        grid.addWidget(_lbl("宽"), 0, 4)
        self.spin_text_w = _spin(0, 9999, 0)
        grid.addWidget(self.spin_text_w, 0, 5)
        self.combo_effect = QComboBox()
        self.combo_effect.addItems(TEXT_EFFECTS)
        self.combo_effect.setFixedWidth(COMBO_W)
        grid.addWidget(self.combo_effect, 0, 6)
        self.btn_font = QPushButton("字体")
        self.btn_font.setFixedWidth(BTN_W)
        grid.addWidget(self.btn_font, 0, 7)

        # ---- 第 1 行: 前景色 | 纵坐标 | 高 | 位置预览 ----
        grid.addWidget(_lbl("前景色"), 1, 0)
        self.spin_fg = _spin(0, 255, 1)
        grid.addWidget(self.spin_fg, 1, 1)
        grid.addWidget(_lbl("纵坐标"), 1, 2)
        self.spin_text_y = _spin(0, 9999, 0)
        grid.addWidget(self.spin_text_y, 1, 3)
        grid.addWidget(_lbl("高"), 1, 4)
        self.spin_text_h = _spin(0, 9999, 0)
        grid.addWidget(self.spin_text_h, 1, 5)
        # col6-7: 位于「效果」正下方的位置预览勾选框
        self.chk_text_preview = QCheckBox("位置预览")
        grid.addWidget(self.chk_text_preview, 1, 6, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        # ---- 第 2 行: 阴景色 | (空) | (空) | 载入 | 显示 | 更新 | 贴入 ----
        grid.addWidget(_lbl("阴景色"), 2, 0)
        self.spin_edge = _spin(0, 255, 2)
        grid.addWidget(self.spin_edge, 2, 1)
        # col2-3 留空, 与上两行的 横坐标/纵坐标 标签+值 对应位置对齐
        self.btn_load_image = QPushButton("载入")
        self.btn_load_image.setFixedWidth(BTN_W)
        grid.addWidget(self.btn_load_image, 2, 4)
        self.btn_apply_text = QPushButton("显示")
        self.btn_apply_text.setFixedWidth(BTN_W)
        grid.addWidget(self.btn_apply_text, 2, 5)  # 位于「宽/高」正下方
        self.btn_update_text = QPushButton("更新")
        self.btn_update_text.setFixedWidth(BTN_W)
        grid.addWidget(self.btn_update_text, 2, 6)  # 位于「效果」正下方
        self.btn_paste_text = QPushButton("贴入")
        self.btn_paste_text.setFixedWidth(BTN_W)
        grid.addWidget(self.btn_paste_text, 2, 7)  # 位于「字体」正下方

        grid.setColumnStretch(8, 1)  # 最右侧列吸收剩余空间
        param_vbox.addLayout(grid)

        # 底部行: 文字输入框 (扩展占满整行)
        txt_row = QHBoxLayout()
        txt_row.setSpacing(6)
        lab_t = _lbl("文字")
        txt_row.addWidget(lab_t)
        self.edit_text = QLineEdit("文字")
        txt_row.addWidget(self.edit_text, 1)
        param_vbox.addLayout(txt_row)

        outer.addLayout(param_vbox, 1)

        # --- 右: 方向键十字控件 (独立 3x3, 绝对对称: ↑(0,1) ←(1,0) →(1,2) ↓(2,1)) ---
        dir_panel = QWidget()
        dir_grid = QGridLayout(dir_panel)
        dir_grid.setContentsMargins(0, 0, 0, 0)
        dir_grid.setSpacing(2)
        AW, AH = 30, 24
        # 三列设置相等的最小宽度 (每列 = 按钮宽 + 留白), 保证 ← 与 → 对称位于中央 ↑↓ 的两侧
        for col in (0, 1, 2):
            dir_grid.setColumnMinimumWidth(col, AW + 2)
        self.btn_up = QPushButton("↑");    self.btn_up.setFixedSize(AW, AH)
        self.btn_left = QPushButton("←");  self.btn_left.setFixedSize(AW, AH)
        self.btn_right = QPushButton("→"); self.btn_right.setFixedSize(AW, AH)
        self.btn_down = QPushButton("↓");  self.btn_down.setFixedSize(AW, AH)
        dir_grid.addWidget(self.btn_up, 0, 1, Qt.AlignCenter)
        dir_grid.addWidget(self.btn_left, 1, 0, Qt.AlignCenter)
        dir_grid.addWidget(self.btn_right, 1, 2, Qt.AlignCenter)
        dir_grid.addWidget(self.btn_down, 2, 1, Qt.AlignCenter)
        outer.addWidget(dir_panel, 0, Qt.AlignVCenter)

        left_layout.addWidget(text_group)

        splitter.addWidget(left)

        # 右侧: 设置面板
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # GPP 项目
        gpp_group = QGroupBox("项目")
        gpp_layout = QGridLayout(gpp_group)
        gpp_layout.addWidget(QLabel("项目文件"), 0, 0)
        self.edit_gpp = QLineEdit()
        gpp_layout.addWidget(self.edit_gpp, 0, 1)
        self.btn_open_gpp = QPushButton("打开")
        gpp_layout.addWidget(self.btn_open_gpp, 0, 2)
        self.btn_save_gpp = QPushButton("保存")
        gpp_layout.addWidget(self.btn_save_gpp, 0, 3)

        self.list_items = QListWidget()
        gpp_layout.addWidget(self.list_items, 1, 0, 1, 4)
        right_layout.addWidget(gpp_group)

        # 图片数据
        data_group = QGroupBox("图片数据")
        data_layout = QFormLayout(data_group)
        # 图片名称 (title) - 显示在列表里的条目名 + 默认 BMP 名前缀
        self.edit_title = QLineEdit()
        self.edit_title.setPlaceholderText("输入此图片条目名称,例如: 主菜单/背景图…")
        data_layout.addRow("图片名称:", self.edit_title)

        self.edit_rom = QLineEdit()
        self.btn_pick_rom = QPushButton(".")
        self.btn_pick_rom.setMaximumWidth(30)
        rom_row = QHBoxLayout()
        rom_row.addWidget(self.edit_rom)
        rom_row.addWidget(self.btn_pick_rom)
        data_layout.addRow("ROM:", self._wrap(rom_row))

        self.combo_type = QComboBox()
        self.combo_type.addItems(PIC_TYPES)
        data_layout.addRow("图片格式:", self.combo_type)

        self.combo_color = QComboBox()
        self.combo_color.addItems(COLOR_TYPES)
        data_layout.addRow("颜色格式:", self.combo_color)

        self.edit_pal_addr = QLineEdit("0")
        data_layout.addRow("调色板:", self.edit_pal_addr)

        self.edit_pal_idx = QLineEdit("0")
        self.lbl_pal_idx = QLabel("色板号:")
        data_layout.addRow(self.lbl_pal_idx, self.edit_pal_idx)

        self.edit_pal_py = QLineEdit("0")
        data_layout.addRow("调色板偏移:", self.edit_pal_py)

        self.edit_tile_addr = QLineEdit("0")
        self.lbl_tile_addr = QLabel("图片数据:")
        data_layout.addRow(self.lbl_tile_addr, self.edit_tile_addr)

        # 片宽/片高 (瓦片尺寸因子, twidth/theight)
        tile_row = QHBoxLayout()
        self.spin_tw = QSpinBox()
        self.spin_tw.setRange(1, 99)
        self.spin_tw.setValue(1)
        self.spin_th = QSpinBox()
        self.spin_th.setRange(1, 99)
        self.spin_th.setValue(1)
        tile_row.addWidget(QLabel("片宽"))
        tile_row.addWidget(self.spin_tw)
        tile_row.addSpacing(12)
        tile_row.addWidget(QLabel("片高"))
        tile_row.addWidget(self.spin_th)
        data_layout.addRow("", self._wrap(tile_row))

        # 图宽/图高 (图片尺寸, 瓦片数, pwidth/pheight)
        size_row = QHBoxLayout()
        self.spin_pw = QSpinBox()
        self.spin_pw.setRange(1, 256)
        self.spin_pw.setValue(1)
        self.spin_ph = QSpinBox()
        self.spin_ph.setRange(1, 256)
        self.spin_ph.setValue(1)
        size_row.addWidget(QLabel("图宽"))
        size_row.addWidget(self.spin_pw)
        size_row.addSpacing(12)
        size_row.addWidget(QLabel("图高"))
        size_row.addWidget(self.spin_ph)
        data_layout.addRow("", self._wrap(size_row))

        self.edit_bmp = QLineEdit()
        self.btn_pick_bmp = QPushButton(".")
        self.btn_pick_bmp.setMaximumWidth(30)
        bmp_row = QHBoxLayout()
        bmp_row.addWidget(self.edit_bmp)
        bmp_row.addWidget(self.btn_pick_bmp)
        data_layout.addRow("对应图片:", self._wrap(bmp_row))

        self.chk_write_pal = QCheckBox("写色板")
        data_layout.addRow("", self.chk_write_pal)

        # 条目操作按钮
        item_btn_row = QHBoxLayout()
        self.btn_add = QPushButton("新增")
        self.btn_next = QPushButton("添加下一个")
        self.btn_clear = QPushButton("移除")
        item_btn_row.addWidget(self.btn_add)
        item_btn_row.addWidget(self.btn_next)
        item_btn_row.addWidget(self.btn_clear)
        data_layout.addRow("", self._wrap(item_btn_row))

        right_layout.addWidget(data_group)

        # 操作按钮
        op_group = QGroupBox("操作")
        op_layout = QGridLayout(op_group)
        self.btn_show = QPushButton("显示")
        self.btn_import = QPushButton("导入")
        self.btn_export = QPushButton("导出")
        self.btn_write = QPushButton("写入")
        self.btn_set_tile_count = QPushButton("设定瓦片数")
        op_layout.addWidget(self.btn_show, 0, 0)
        op_layout.addWidget(self.btn_import, 0, 1)
        op_layout.addWidget(self.btn_export, 0, 2)
        op_layout.addWidget(self.btn_write, 1, 0)
        op_layout.addWidget(self.btn_set_tile_count, 1, 1)
        right_layout.addWidget(op_group)

        right_layout.addStretch()
        splitter.addWidget(right)

        splitter.setSizes([850, 430])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        self.statusBar().showMessage("就绪")

    def _wrap(self, layout):
        w = QWidget()
        w.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return w

    # ---------- 信号连接 ----------

    def _connect_signals(self):
        self.btn_open_gpp.clicked.connect(self.open_gpp)
        self.btn_save_gpp.clicked.connect(self.save_gpp)
        # 菜单栏动作
        self.act_new.triggered.connect(self.new_project)
        self.act_open_gpp.triggered.connect(self.open_gpp)
        self.act_save_gpp.triggered.connect(self.save_gpp)
        self.act_clear_recent.triggered.connect(self._clear_recent_gpp)
        self.act_quit.triggered.connect(self.close)
        self.act_about.triggered.connect(self._show_about)
        self.btn_pick_rom.clicked.connect(self.pick_rom)
        self.btn_pick_bmp.clicked.connect(self.pick_bmp)
        self.btn_add.clicked.connect(self.add_item)
        self.btn_next.clicked.connect(self.add_next_item)
        self.btn_clear.clicked.connect(self.remove_item)
        self.list_items.currentRowChanged.connect(self.on_item_changed)
        self.edit_title.textChanged.connect(self.on_title_edited)

        # 所有输入框实时同步到当前条目 (像标题一样, 不再依赖"修改"按钮)
        self.edit_rom.textChanged.connect(self._sync_item)
        self.combo_type.currentTextChanged.connect(self._sync_item)
        self.combo_color.currentTextChanged.connect(self._sync_item)
        self.edit_pal_addr.textChanged.connect(self._sync_item)
        self.edit_pal_idx.textChanged.connect(self._sync_item)
        self.edit_pal_py.textChanged.connect(self._sync_item)
        self.edit_tile_addr.textChanged.connect(self._sync_item)
        self.spin_pw.valueChanged.connect(self._sync_item)
        self.spin_ph.valueChanged.connect(self._sync_item)
        self.spin_tw.valueChanged.connect(self._sync_item)
        self.spin_th.valueChanged.connect(self._sync_item)
        self.edit_bmp.textChanged.connect(self._sync_item)

        self.btn_show.clicked.connect(self.show_picture)
        self.btn_import.clicked.connect(self.import_bmp)
        self.btn_export.clicked.connect(self.export_bmp)
        self.btn_write.clicked.connect(self.write_back)
        self.btn_set_tile_count.clicked.connect(self.set_tile_count)

        # 撤销/重做 + 快捷键
        self.btn_undo.clicked.connect(self.on_undo)
        self.btn_redo.clicked.connect(self.on_redo)
        # 网格开关 (功能7)
        self.chk_grid.toggled.connect(self.on_grid_toggled)

        self.slider_scale.valueChanged.connect(self.on_scale_changed)
        self.canvas.color_picked.connect(self.on_color_picked)
        self.canvas.selection_made.connect(self.on_selection_made)
        self.canvas.wheel_delta.connect(self.on_wheel_scale)  # 功能7: 滚轮缩放

        self.combo_type.currentTextChanged.connect(self.on_type_changed)

        # 文字工具
        self.edit_text.textChanged.connect(self.on_text_changed)
        self.spin_bg.valueChanged.connect(self.on_text_colors_changed)
        self.spin_fg.valueChanged.connect(self.on_text_colors_changed)
        self.spin_edge.valueChanged.connect(self.on_text_colors_changed)
        self.combo_effect.currentTextChanged.connect(self.on_text_colors_changed)
        self.btn_font.clicked.connect(self.pick_font)
        self.btn_apply_text.clicked.connect(self.apply_text_preview)
        self.btn_update_text.clicked.connect(self.update_text)
        self.btn_paste_text.clicked.connect(self.paste_text)
        self.btn_load_image.clicked.connect(self.load_image_to_preview)
        # 位置预览勾选框: 切换/坐标/尺寸变化都刷新预览
        self.chk_text_preview.toggled.connect(self._update_text_preview_rect)
        self.spin_text_x.valueChanged.connect(self._update_text_preview_rect)
        self.spin_text_y.valueChanged.connect(self._update_text_preview_rect)
        self.spin_text_w.valueChanged.connect(self._update_text_preview_rect)
        self.spin_text_h.valueChanged.connect(self._update_text_preview_rect)
        self.text_preview.selection_made.connect(self.on_preview_selection)
        self.btn_up.clicked.connect(lambda: self.text_preview.move_text(dy=-1))
        self.btn_down.clicked.connect(lambda: self.text_preview.move_text(dy=1))
        self.btn_left.clicked.connect(lambda: self.text_preview.move_text(dx=-1))
        self.btn_right.clicked.connect(lambda: self.text_preview.move_text(dx=1))

    # ---------- 配置读取/写入 ----------

    def _read_config(self) -> PicConfig:
        cfg = PicConfig()
        cfg.rom_path = self.edit_rom.text().strip()
        cfg.ptype = self.combo_type.currentText()
        cfg.colotype = self.combo_color.currentText()
        cfg.palette_addr = self.edit_pal_addr.text().strip()
        cfg.tile_addr = self.edit_tile_addr.text().strip()
        cfg.pal_idx = self.edit_pal_idx.text().strip()
        cfg.pal_py = parse_int_or_hex(self.edit_pal_py.text().strip())
        cfg.pwidth = self.spin_pw.value()
        cfg.pheight = self.spin_ph.value()
        cfg.twidth = self.spin_tw.value()
        cfg.theight = self.spin_th.value()
        cfg.bmp_fname = self.edit_bmp.text().strip()
        cfg.write_pal = self.chk_write_pal.isChecked()
        cfg.title = self.edit_text.text()  # 不用,占位
        return cfg

    def _apply_item(self, item: PicItem):
        """将条目字段回填到UI控件, 期间屏蔽信号避免触发实时同步回环。"""
        widgets = [
            self.edit_title, self.edit_rom, self.edit_pal_addr, self.edit_pal_idx,
            self.edit_pal_py, self.edit_tile_addr, self.edit_bmp,
            self.combo_type, self.combo_color,
            self.spin_pw, self.spin_ph, self.spin_tw, self.spin_th,
        ]
        for w in widgets:
            w.blockSignals(True)
        try:
            self.edit_title.setText(item.title)
            self.edit_rom.setText(item.soufname)
            self.combo_type.setCurrentText(item.ptype)
            self.combo_color.setCurrentText(item.colotype)
            self.edit_pal_addr.setText(item.palett)
            self.edit_pal_idx.setText(item.paleidx)
            self.edit_pal_py.setText(str(item.palepy))
            self.edit_tile_addr.setText(item.batmap)
            self.spin_pw.setValue(item.pwidth)
            self.spin_ph.setValue(item.pheight)
            self.spin_tw.setValue(item.twidth)
            self.spin_th.setValue(item.theight)
            self.edit_bmp.setText(item.bmpfname)
            # 根据类型设置片宽片高启用态
            is_map = "MAP" in (item.ptype or "")
            self.spin_tw.setEnabled(not is_map)
            self.spin_th.setEnabled(not is_map)
        finally:
            for w in widgets:
                w.blockSignals(False)
        # 手动触发类型切换, 更新标签和片宽片高启用态
        self.on_type_changed(self.combo_type.currentText())

    def _sync_item(self):
        """所有输入框变化时实时同步到当前条目 (替代"修改"按钮)。"""
        if self._current_idx is None or self._current_idx < 0 or self._current_idx >= len(self.items):
            return
        it = self.items[self._current_idx]
        it.soufname = self.edit_rom.text().strip()
        it.ptype = self.combo_type.currentText()
        it.colotype = self.combo_color.currentText()
        it.palett = self.edit_pal_addr.text().strip()
        it.paleidx = self.edit_pal_idx.text().strip()
        it.palepy = parse_int_or_hex(self.edit_pal_py.text().strip())
        it.batmap = self.edit_tile_addr.text().strip()
        it.pwidth = self.spin_pw.value()
        it.pheight = self.spin_ph.value()
        it.twidth = self.spin_tw.value()
        it.theight = self.spin_th.value()
        it.bmpfname = self.edit_bmp.text().strip()

    def _collect_item(self, title: str = "") -> PicItem:
        it = PicItem()
        it.soufname = self.edit_rom.text().strip()
        it.title = title or self.edit_title.text().strip() or self._current_title()
        it.palett = self.edit_pal_addr.text().strip()
        it.paleidx = self.edit_pal_idx.text().strip()
        it.palepy = parse_int_or_hex(self.edit_pal_py.text().strip())
        it.batmap = self.edit_tile_addr.text().strip()
        it.ptype = self.combo_type.currentText()
        it.twidth = self.spin_tw.value()
        it.theight = self.spin_th.value()
        it.pwidth = self.spin_pw.value()
        it.pheight = self.spin_ph.value()
        it.colotype = self.combo_color.currentText()
        bmp = self.edit_bmp.text().strip()
        if not bmp.lower().endswith(".bmp"):
            base = os.path.basename(it.soufname)
            d = os.path.dirname(it.soufname)
            it.bmpfname = os.path.join(d, it.title + ".bmp") if it.title else bmp
        else:
            it.bmpfname = bmp
        return it

    def _current_title(self):
        if 0 <= self._current_idx < len(self.items):
            return self.items[self._current_idx].title
        return "新图片"

    # ---------- GPP 项目 ----------

    def new_project(self):
        """新建项目: 清空所有数据, 恢复到初始打开状态。"""
        self.items.clear()
        self._current_idx = -1
        self.gpp_path = ""
        self.rom_path = ""
        self.edit_gpp.setText("")
        # 清空UI输入框
        for w in [self.edit_title, self.edit_rom, self.edit_pal_addr, self.edit_pal_idx,
                  self.edit_pal_py, self.edit_tile_addr, self.edit_bmp]:
            w.blockSignals(True)
            w.setText("")
        for w in [self.edit_title, self.edit_rom, self.edit_pal_addr, self.edit_pal_idx,
                  self.edit_pal_py, self.edit_tile_addr, self.edit_bmp]:
            w.blockSignals(False)
        self.spin_pw.blockSignals(True)
        self.spin_ph.blockSignals(True)
        self.spin_tw.blockSignals(True)
        self.spin_th.blockSignals(True)
        self.spin_pw.setValue(1)
        self.spin_ph.setValue(1)
        self.spin_tw.setValue(1)
        self.spin_th.setValue(1)
        self.spin_pw.blockSignals(False)
        self.spin_ph.blockSignals(False)
        self.spin_tw.blockSignals(False)
        self.spin_th.blockSignals(False)
        # 清空画布
        self.canvas.set_data([], 0, 0, None)
        # 清空列表
        self.list_items.blockSignals(True)
        self.list_items.clear()
        self.list_items.blockSignals(False)
        self.statusBar().showMessage("已新建项目")

    def open_gpp(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开GPP项目", "", "GPP项目(*.gpp);;所有(*.*)")
        if not path:
            return
        self._open_gpp_by_path(path)

    def save_gpp(self):
        if not self.items:
            QMessageBox.information(self, "提示", "没有项目可保存")
            return
        # 保存前强制同步一次当前UI值到items, 防止信号遗漏
        self._sync_item()
        path, _ = QFileDialog.getSaveFileName(self, "保存GPP项目", self.gpp_path, "GPP项目(*.gpp)")
        if not path:
            return
        rom_path = self.edit_rom.text().strip() or self.rom_path
        try:
            save_gpp(path, rom_path, self.items)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存项目失败:\n{e}")
            return
        self.gpp_path = path
        self.edit_gpp.setText(path)
        self.statusBar().showMessage("项目已保存")
        self._add_recent_gpp(path)

    # ---------- 最近打开工程记录 ----------

    def _load_recent_gpp(self):
        """从 QSettings 读取最近打开的 GPP 路径列表。"""
        raw = self._settings.value("recent_gpps", [], type=list)
        self._recent_gpps = [p for p in raw if isinstance(p, str) and p][: self._recent_max]

    def _save_recent_gpp(self):
        """把当前最近列表写入 QSettings。"""
        self._settings.setValue("recent_gpps", self._recent_gpps)
        self._settings.sync()

    def _add_recent_gpp(self, path: str):
        """新增一条最近打开记录：去重、置顶、最多 _recent_max 条。"""
        if not path:
            return
        path = os.path.abspath(path)
        if path in self._recent_gpps:
            self._recent_gpps.remove(path)
        self._recent_gpps.insert(0, path)
        if len(self._recent_gpps) > self._recent_max:
            self._recent_gpps = self._recent_gpps[: self._recent_max]
        self._save_recent_gpp()
        self._refresh_recent_menu()

    def _refresh_recent_menu(self):
        """重建『最近打开的工程』菜单项（同时更新文件子菜单与顶层「最近工程」菜单）。"""
        self.menu_recent.clear()
        if hasattr(self, "menu_recent_top"):
            self.menu_recent_top.clear()
        if not self._recent_gpps:
            act = QAction("（暂无）", self)
            act.setEnabled(False)
            self.menu_recent.addAction(act)
            if hasattr(self, "menu_recent_top"):
                act2 = QAction("（暂无）", self)
                act2.setEnabled(False)
                self.menu_recent_top.addAction(act2)
            return
        for idx, path in enumerate(self._recent_gpps):
            # 显示: "1 F:\game\youlin\new.gpp" (带序号+加速键 1..9)
            if idx < 9:
                title = f"&{idx + 1}  {path}"
            else:
                title = f"    {idx + 1}.  {path}"
            # 文件子菜单 action
            act = QAction(title, self)
            act.setData(path)
            act.triggered.connect(lambda _checked=False, p=path: self._open_recent_gpp(p))
            self.menu_recent.addAction(act)
            # 顶层菜单 action (共用同一个 p 绑定)
            if hasattr(self, "menu_recent_top"):
                act_t = QAction(title, self)
                act_t.setData(path)
                act_t.triggered.connect(lambda _checked=False, p=path: self._open_recent_gpp(p))
                self.menu_recent_top.addAction(act_t)

    def _open_recent_gpp(self, path: str):
        """点击最近菜单项：按路径直接打开 GPP。"""
        if not os.path.isfile(path):
            QMessageBox.warning(self, "提示", f"文件不存在:\n{path}")
            # 顺便从最近列表中移除
            if path in self._recent_gpps:
                self._recent_gpps.remove(path)
                self._save_recent_gpp()
                self._refresh_recent_menu()
            return
        self._open_gpp_by_path(path)

    def _open_gpp_by_path(self, path: str):
        """按给定路径直接加载 GPP（供 open_gpp / _open_recent_gpp 共用）。"""
        try:
            rom_path, items = load_gpp(path)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"读取项目失败:\n{e}")
            return False
        self.gpp_path = path
        self.edit_gpp.setText(path)
        self.items = items
        self._refresh_list()
        self.rom_path = rom_path
        if items:
            self.list_items.setCurrentRow(0)
        self.statusBar().showMessage(f"已加载 {len(items)} 个项目")
        self._add_recent_gpp(path)
        return True

    def _clear_recent_gpp(self):
        """清空最近打开记录。"""
        self._recent_gpps = []
        self._save_recent_gpp()
        self._refresh_recent_menu()

    def _show_about(self):
        QMessageBox.information(
            self, "关于",
            "ROM图片编辑器 1.1 (PySide6 重写版)\n\n"
            "兼容 Var1.0 / Var2.0 / Var3.0 .gpp 工程文件\n"
            "原 VB6 版本作者: SHIKEYU\n"
            "重写: Python 3.12 + PySide6",
        )

    def on_title_edited(self, text: str):
        """用户在图片名称输入框改了文字 → 实时同步到当前 PicItem.title 和左侧列表条目。"""
        if self._current_idx < 0 or self._current_idx >= len(self.items):
            return
        self.items[self._current_idx].title = text
        # 同步更新列表同一项的显示文字, 不需要整表重建 (避免 currentRow 被重置)
        qitem = self.list_items.item(self._current_idx)
        if qitem is not None and qitem.text() != text:
            qitem.setText(text)

    def _refresh_list(self):
        self.list_items.blockSignals(True)
        self.list_items.clear()
        for it in self.items:
            self.list_items.addItem(it.title)
        self.list_items.blockSignals(False)

    def on_item_changed(self, row):
        if row < 0 or row >= len(self.items):
            return
        self._current_idx = row
        self._apply_item(self.items[row])
        # 切换条目后自动执行显示
        self.show_picture()

    def add_item(self):
        title, ok = QInputDialog.getText(self, "添加", "标题:")
        if not ok or not title:
            return
        # 计算默认值, 不要依赖当前UI里可能残留的旧条目值
        rom_path = self.edit_rom.text().strip()
        if rom_path:
            d = os.path.dirname(rom_path)
            default_bmp = os.path.join(d, title + ".bmp")
        else:
            default_bmp = title + ".bmp"
        # 统一构造新条目默认值
        it = PicItem()
        it.title = title
        it.soufname = rom_path                         # ROM路径: 沿用当前项目打开的ROM
        it.ptype = "16色MAP"                            # 图片格式: 默认16色MAP
        it.colotype = "RGB 2BIT"                        # 颜色格式: 默认 RGB 2BIT
        it.palett = "0"                                 # 调色板地址: 默认0
        it.paleidx = "0"                                # 地图/色板号: 默认0
        it.palepy = 0                                   # 调色板偏移: 默认0
        it.batmap = "0"                                 # 图片数据地址: 默认0
        it.twidth = 1                                   # 片宽片高
        it.theight = 1
        it.pwidth = 8                                   # 图宽图高: 默认8×8=64瓦片(常见小游戏贴图尺寸)
        it.pheight = 8
        it.bmpfname = default_bmp                       # 对应图片: ROM目录/标题.bmp
        # 同步填入UI, 让用户看到的就是新条目默认值
        for w in [self.edit_title, self.edit_rom, self.edit_pal_addr, self.edit_pal_idx,
                  self.edit_pal_py, self.edit_tile_addr, self.edit_bmp]:
            w.blockSignals(True)
        self.edit_title.setText(title)
        self.edit_rom.setText(rom_path)
        self.edit_pal_addr.setText("0")
        self.edit_pal_idx.setText("0")
        self.edit_pal_py.setText("0")
        self.edit_tile_addr.setText("0")
        self.edit_bmp.setText(default_bmp)
        for w in [self.edit_title, self.edit_rom, self.edit_pal_addr, self.edit_pal_idx,
                  self.edit_pal_py, self.edit_tile_addr, self.edit_bmp]:
            w.blockSignals(False)
        for s in [self.spin_pw, self.spin_ph, self.spin_tw, self.spin_th,
                  self.combo_type, self.combo_color]:
            s.blockSignals(True)
        self.spin_pw.setValue(it.pwidth)
        self.spin_ph.setValue(it.pheight)
        self.spin_tw.setValue(it.twidth)
        self.spin_th.setValue(it.theight)
        self.combo_type.setCurrentText(it.ptype)
        self.combo_color.setCurrentText(it.colotype)
        for s in [self.spin_pw, self.spin_ph, self.spin_tw, self.spin_th,
                  self.combo_type, self.combo_color]:
            s.blockSignals(False)
        self.items.append(it)
        self._refresh_list()
        self.list_items.setCurrentRow(len(self.items) - 1)

    def add_next_item(self):
        """在当前条目后追加一项: 标题自动加编号, 对应图片相应改名, 其它设定不变,
        图片数据地址按原计算按钮逻辑(当前条目瓦片结束地址)设置。"""
        if self._current_idx < 0 or self._current_idx >= len(self.items):
            # 没有当前条目时退化为普通新增
            self.add_item()
            return
        # 1) 先同步当前UI值到当前条目, 保证拿到的是最新值
        self._sync_item()
        cur = self.items[self._current_idx]
        # 2) 取当前标题的末尾数字, 做自增; 若没有数字则默认追加"2"
        base = cur.title.rstrip("*")
        num_str = ""
        i = len(base) - 1
        while i >= 0 and base[i].isdigit():
            num_str = base[i] + num_str
            i -= 1
        prefix = base[: i + 1]
        if num_str:
            next_num = int(num_str) + 1
            next_title = f"{prefix}{next_num}"
        else:
            next_title = f"{prefix}2"
        # 3) 计算当前条目瓦片数据结束地址 (= 下一项起始地址), 复用 calc_addr 逻辑
        try:
            bmp_off = parse_addr(cur.batmap).data_offset
        except Exception:
            bmp_off = 0
        t = cur.ptype
        if t == "16色":
            size = cur.pwidth * cur.pheight * cur.twidth * cur.theight * 32
        elif t in ("超16色", "16色MAP"):
            size = cur.pwidth * cur.pheight * 32
        elif t in ("256色", "256色MAP"):
            size = cur.pwidth * cur.pheight * 64
        elif t == "PCE16色":
            size = cur.pwidth * cur.pheight * cur.twidth * cur.theight * 128
        elif t == "PCE16色8x8":
            size = cur.pwidth * cur.pheight * cur.twidth * cur.theight * 32
        else:
            size = 0
        next_batmap = hex(bmp_off + size)
        # 4) 对应图片: 把原文件名中的旧标题/数字替换为新标题
        next_bmpfname = cur.bmpfname
        if next_bmpfname:
            d = os.path.dirname(next_bmpfname)
            old_basename = os.path.basename(next_bmpfname)
            # 把旧标题去掉扩展名后, 替换为新标题, 保持扩展名
            name, ext = os.path.splitext(old_basename)
            # 如果 name 以旧 base 结尾 (或相等), 替换前缀
            if name.endswith(base) or name == base:
                name = name[: len(name) - len(base)] + next_title
            elif num_str and name.endswith(prefix) and not num_str:
                # 没有数字的情况: 直接换
                name = name[: len(name) - len(prefix)] + next_title
            else:
                # 兜底: 整个 basename 用新标题
                name = next_title
            if not ext.lower() in (".bmp", ".png", ".jpg", ".jpeg", ".tif", ".tiff"):
                ext = ".bmp"
            next_bmpfname = os.path.join(d, name + ext) if d else (name + ext)
        else:
            rom_path = cur.soufname or self.edit_rom.text().strip()
            if rom_path:
                d = os.path.dirname(rom_path)
                next_bmpfname = os.path.join(d, next_title + ".bmp")
            else:
                next_bmpfname = next_title + ".bmp"
        # 5) 构造新条目, 拷贝当前条目其余字段
        import copy
        new_it = copy.deepcopy(cur)
        new_it.title = next_title
        new_it.batmap = next_batmap       # 图片数据地址: 跳到下一段
        new_it.bmpfname = next_bmpfname   # 对应图片文件名: 同步变化
        # 插入到当前条目之后
        insert_idx = self._current_idx + 1
        self.items.insert(insert_idx, new_it)
        # 6) 同步填入UI (blockSignals 防止信号反写)
        all_text = [self.edit_title, self.edit_rom, self.edit_pal_addr, self.edit_pal_idx,
                    self.edit_pal_py, self.edit_tile_addr, self.edit_bmp]
        for w in all_text:
            w.blockSignals(True)
        self.edit_title.setText(new_it.title)
        self.edit_rom.setText(new_it.soufname)
        self.edit_pal_addr.setText(new_it.palett)
        self.edit_pal_idx.setText(new_it.paleidx)
        self.edit_pal_py.setText(str(new_it.palepy))
        self.edit_tile_addr.setText(new_it.batmap)
        self.edit_bmp.setText(new_it.bmpfname)
        for w in all_text:
            w.blockSignals(False)
        all_mix = [self.spin_pw, self.spin_ph, self.spin_tw, self.spin_th,
                   self.combo_type, self.combo_color]
        for s in all_mix:
            s.blockSignals(True)
        self.spin_pw.setValue(new_it.pwidth)
        self.spin_ph.setValue(new_it.pheight)
        self.spin_tw.setValue(new_it.twidth)
        self.spin_th.setValue(new_it.theight)
        self.combo_type.setCurrentText(new_it.ptype)
        self.combo_color.setCurrentText(new_it.colotype)
        for s in all_mix:
            s.blockSignals(False)
        self._refresh_list()
        self.list_items.setCurrentRow(insert_idx)

    def toggle_delete_item(self):
        # 标星按钮已移除, 保留函数兼容旧引用(如快捷键), 实际不再挂接
        if self._current_idx < 0:
            return
        t = self.items[self._current_idx].title
        if t.endswith("*"):
            self.items[self._current_idx].title = t[:-1]
        else:
            self.items[self._current_idx].title = t + "*"
        self.edit_title.blockSignals(True)
        try:
            self.edit_title.setText(self.items[self._current_idx].title)
        finally:
            self.edit_title.blockSignals(False)
        self._refresh_list()
        self.list_items.setCurrentRow(self._current_idx)

    def remove_item(self):
        if self._current_idx < 0:
            return
        del self.items[self._current_idx]
        self._current_idx = -1
        self._refresh_list()

    # ---------- 文件选择 ----------

    def pick_rom(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择ROM", "", "所有(*.*)")
        if path:
            self.edit_rom.setText(path)

    def pick_bmp(self):
        path, _ = QFileDialog.getSaveFileName(self, "对应图片", "", "BMP(*.bmp)")
        if path:
            self.edit_bmp.setText(path)

    # ---------- 显示/写入/导入/导出 ----------

    def show_picture(self):
        cfg = self._read_config()
        try:
            self.editor.show(cfg)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"显示失败:\n{e}")
            return
        self.canvas.set_data(self.editor.picmap, self.editor.width,
                             self.editor.height, self.editor.palette)
        self.canvas.set_scale(self.slider_scale.value())
        # 功能7: 瓦片网格尺寸按当前图片类型同步
        self.canvas.set_tile_size(self._tile_px_for_type(cfg))
        self.statusBar().showMessage(f"已显示: {self.editor.width}×{self.editor.height}")
        # 新一次"显示"会丢弃所有旧历史, 清空撤销栈以避免奇怪的切换
        self.editor._undo.clear()
        self.editor._redo.clear()
        self._refresh_undo_btns()

    def write_back(self):
        cfg = self._read_config()
        # 写入 ROM 前做一次撤销快照 (用户万一不满意可撤回 picmap 变化)
        self.editor.push_undo()
        try:
            msg = self.editor.write_back(cfg)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"写入失败:\n{e}")
            return
        self._refresh_undo_btns()
        QMessageBox.information(self, "成功", msg)

    def import_bmp(self):
        cfg = self._read_config()
        bmp_path = cfg.bmp_fname or cfg.rom_path
        # 用户需求: 对应图片框有值且文件存在 → 跳过文件选择窗口,直接导入
        path = ""
        if bmp_path:
            cand = bmp_path.strip()
            if cand and os.path.isfile(cand):
                path = cand
        if not path:
            # 功能6: 同时支持 BMP/PNG/JPG/TIFF, 只要 Pillow 可用
            try:
                import PIL  # noqa: F401
                filters = "图片文件 (*.bmp *.png *.jpg *.jpeg *.tif *.tiff);;BMP (*.bmp);;PNG (*.png);;JPEG (*.jpg *.jpeg);;TIFF (*.tif *.tiff);;所有文件 (*.*)"
            except Exception:
                filters = "BMP (*.bmp)"
            chosen, _ = QFileDialog.getOpenFileName(self, "导入图片", bmp_path, filters)
            if not chosen:
                return
            path = chosen
        self.editor.push_undo()
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext in (".bmp",):
                self.editor.import_from_bmp(cfg, path, load_pal=self.chk_write_pal.isChecked())
            else:
                # 非 BMP: 走 Pillow 量化流程 (功能6)
                self._import_image_with_pillow(cfg, path, load_pal=self.chk_write_pal.isChecked())
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "错误", f"导入失败:\n{e}")
            return
        self.canvas.set_data(self.editor.picmap, self.editor.width,
                             self.editor.height, self.editor.palette)
        self._refresh_undo_btns()
        self.statusBar().showMessage(f"已导入图片 {os.path.basename(path)}")

    def _tile_px_for_type(self, cfg) -> tuple:
        """根据当前图片类型返回单瓦片 (宽, 高) 像素数, 供网格线使用。"""
        t = cfg.ptype if cfg else ""
        if t == "PCE16色":
            return (16, 16)
        return (8, 8)

    # ---------- 撤销/重做 ----------

    def _refresh_undo_btns(self):
        self.btn_undo.setEnabled(self.editor.can_undo())
        self.btn_redo.setEnabled(self.editor.can_redo())

    def _refresh_canvas_after_undo(self):
        if self.editor.viewed:
            self.canvas.set_data(self.editor.picmap, self.editor.width,
                                 self.editor.height, self.editor.palette)

    def on_undo(self):
        if not self.editor.can_undo():
            return
        if not self.editor.undo():
            return
        self._refresh_canvas_after_undo()
        self._refresh_undo_btns()
        self.statusBar().showMessage("已撤销")

    def on_redo(self):
        if not self.editor.can_redo():
            return
        if not self.editor.redo():
            return
        self._refresh_canvas_after_undo()
        self._refresh_undo_btns()
        self.statusBar().showMessage("已重做")

    # ---------- 网格/滚轮缩放 ----------

    def on_grid_toggled(self, checked: bool):
        self.canvas.set_show_grid(bool(checked))

    def on_wheel_scale(self, step: int):
        cur = int(self.slider_scale.value())
        new = max(self.slider_scale.minimum(), min(self.slider_scale.maximum(), cur + int(step)))
        if new != cur:
            self.slider_scale.setValue(new)

    def export_bmp(self):
        if not self.editor.viewed:
            QMessageBox.warning(self, "提示", "无法导出,请先执行显示。")
            return
        cfg = self._read_config()
        # 功能15: 支持 BMP / PNG 两种导出格式 (PNG 默认色号0透明)
        filters = "图片文件 (*.bmp *.png);;BMP (*.bmp);;PNG (色号0透明, *.png)"
        default_name = cfg.bmp_fname or "export.bmp"
        # 用户需求: 导出时如果目标文件已经存在, 显式弹出"是否覆盖"确认
        # 这里用 DontConfirmOverwrite 去掉系统默认自动附名/覆盖行为, 由我们手弹确认框
        opts = QFileDialog.Options(QFileDialog.DontConfirmOverwrite)
        path, sel = QFileDialog.getSaveFileName(self, "导出图片", default_name, filters, "", opts)
        if not path:
            return
        try:
            ext = os.path.splitext(path)[1].lower()
            # 用户选 "PNG" 过滤器但后缀没写 .png 时自动补
            if ext not in (".bmp", ".png"):
                if "PNG" in sel:
                    path += ".png"
                    ext = ".png"
                else:
                    path += ".bmp"
                    ext = ".bmp"
            # --- 是否覆盖确认 ---
            if os.path.isfile(path):
                ret = QMessageBox.question(
                    self,
                    "是否覆盖",
                    f"文件已存在:\n{path}\n\n是否覆盖?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if ret != QMessageBox.Yes:
                    return
            if ext == ".png":
                # 功能15: 导出带透明通道的 PNG
                self.editor.export_to_png(cfg, path, transparent_index=0)
            else:
                self.editor.export_to_bmp(cfg, path)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "错误", f"导出失败:\n{e}")
            return
        # 导出后同步更新"对应图片"输入框, 使下次导入直接使用新文件
        self.edit_bmp.setText(path)
        # 同步到当前条目
        if self._current_idx is not None and 0 <= self._current_idx < len(self.items):
            self.items[self._current_idx].bmpfname = path
        QMessageBox.information(self, "成功", f"已导出到 {path}")

    def calc_addr(self):
        """计算瓦片数据结束地址。对应 VB6 Command15。"""
        cfg = self._read_config()
        try:
            bmp_off = parse_addr(cfg.tile_addr).data_offset
        except Exception:
            bmp_off = 0
        t = cfg.ptype
        if t == "16色":
            size = cfg.pwidth * cfg.pheight * cfg.twidth * cfg.theight * 32
        elif t in ("超16色", "16色MAP"):
            size = cfg.pwidth * cfg.pheight * 32
        elif t in ("256色", "256色MAP"):
            size = cfg.pwidth * cfg.pheight * 64
        elif t == "PCE16色":
            size = cfg.pwidth * cfg.pheight * cfg.twidth * cfg.theight * 128
        elif t == "PCE16色8x8":
            size = cfg.pwidth * cfg.pheight * cfg.twidth * cfg.theight * 32
        else:
            size = 0
        end = bmp_off + size
        self.edit_tile_addr.setText(str(end))
        self.statusBar().showMessage(f"计算地址: {end}")

    def set_tile_count(self):
        """手工设定当前条目的MAP瓦片数量上限。
        显示正确图片后, 用当前ROM中MAP区实际引用的瓦片数更新缓存。
        """
        cfg = self._read_config()
        if cfg.ptype not in ("16色MAP", "256色MAP"):
            QMessageBox.warning(self, "提示", "只有16色MAP和256色MAP类型才能设定瓦片数。")
            return
        if not self.editor.viewed:
            QMessageBox.warning(self, "提示", "请先执行显示。")
            return
        # 重新统计当前ROM中MAP区引用的瓦片数
        try:
            pa_map = parse_addr(cfg.pal_idx)
            if pa_map.is_compressed:
                import os
                temp_map = os.path.join(self.editor.temp_dir, "nds_map_cnt.dat")
                from ..core.compression import uncomp_file_dispatch
                uncomp_file_dispatch(cfg.rom_path, temp_map, pa_map)
                map_data = pe._read_file_bytes(temp_map)
            else:
                map_data = pe._read_file_bytes(cfg.rom_path)
            map_base = pa_map.data_offset
            pw, ph = cfg.pwidth, cfg.pheight
            _tids = set()
            for y in range(ph):
                for x in range(pw):
                    mapid = y * pw + x
                    mi = map_base + mapid * 2
                    if mi + 1 < len(map_data):
                        b0 = map_data[mi]
                        b1 = map_data[mi + 1]
                        tid = b0 + (b1 & 0x03) * 256 - (cfg.pal_py % 4096)
                        if 0 <= tid < 1024:
                            _tids.add(tid)
            count = len(_tids)
            _map_key = (cfg.tile_addr, cfg.pal_idx)
            self.editor._map_orig_tile_count[_map_key] = count
            self.statusBar().showMessage(f"已设定瓦片数: {count}")
            QMessageBox.information(self, "设定瓦片数", f"当前条目MAP区引用瓦片数已更新为: {count}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "错误", f"设定瓦片数失败:\n{e}")

    # ---------- 画布交互 ----------

    def on_scale_changed(self, v):
        self.lbl_scale_val.setText(f"{v}x")
        self.canvas.set_scale(v)

    def on_color_picked(self, x, y, color_idx):
        self.lbl_color.setText(f"颜色号: {color_idx} (位置 {x},{y})")

    def on_selection_made(self, x0, y0, x1, y1):
        """左键框选完成,显示选中的 8×8 瓦片范围。"""
        tx0, ty0 = x0 // 8, y0 // 8
        tx1, ty1 = (x1 - 1) // 8, (y1 - 1) // 8  # 转为 0-based 瓦片索引
        cols = tx1 - tx0 + 1
        rows = ty1 - ty0 + 1
        self.lbl_color.setText(
            f"框选: 像素({x0},{y0})-({x1-1},{y1-1})  瓦片({tx0},{ty0})-({tx1},{ty1})  {cols}×{rows}块"
        )

    def on_type_changed(self, text):
        """根据类型切换标签 (对应 VB6 Combo1_Click)。

        MAP 类型: 色板号→地图, 数据→片号
        非 MAP:   色板号→色板, 数据→数据
        """
        is_map = "MAP" in text
        if is_map:
            self.lbl_pal_idx.setText("地图:")
            self.lbl_tile_addr.setText("图片数据:")
            # 16色/256色MAP: 片宽片高固定8x8, 禁用输入
            self.spin_tw.setEnabled(False)
            self.spin_th.setEnabled(False)
        else:
            self.lbl_pal_idx.setText("色板号:")
            self.lbl_tile_addr.setText("图片数据:")
            self.spin_tw.setEnabled(True)
            self.spin_th.setEnabled(True)
        # 功能7: 网格瓦片尺寸根据类型变化
        try:
            cfg = self._read_config()
            self.canvas.set_tile_size(self._tile_px_for_type(cfg))
        except Exception:
            pass

    # ---------- 辅助: Pillow 导入 PNG/JPG/TIFF (功能6) ----------

    def _import_image_with_pillow(self, cfg, img_path: str, load_pal: bool = True) -> None:
        """用 Pillow 读取任意图片格式: resize→转索引色→生成临时BMP→复用现有import_from_bmp导入链。"""
        import tempfile
        from PIL import Image  # Pillow

        # 计算预期目标尺寸 (与 import_from_bmp 内一致)
        t = cfg.ptype
        if t in ("16色", "PCE16色8x8", "16色MAP"):
            exp_w = cfg.pwidth * cfg.twidth * 8
            exp_h = cfg.pheight * cfg.theight * 8
        elif t == "PCE16色":
            exp_w = cfg.pwidth * cfg.twidth * 16
            exp_h = cfg.pheight * cfg.theight * 16
        elif t == "16色BMP":
            exp_w = cfg.pwidth
            exp_h = cfg.pheight
        elif t in ("256色", "超16色", "256色MAP"):
            exp_w = cfg.pwidth * 8
            exp_h = cfg.pheight * 8
        elif t == "256色BMP":
            exp_w = cfg.pwidth
            exp_h = cfg.pheight
        else:
            exp_w = cfg.pwidth * 8
            exp_h = cfg.pheight * 8

        # Pillow 读图并 resize 到目标尺寸 (最近邻=保持像素风)
        im = Image.open(img_path)
        # 如果是带透明的 RGBA, 转白底 + 保留alpha (避免透明区域成黑色)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            base = im.convert("RGBA")
            bg.paste(base, mask=base.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        if im.size != (exp_w, exp_h):
            im = im.resize((exp_w, exp_h), Image.NEAREST)

        # 量化成最大允许色数 (16色格式=16色, 其余=256色)
        if t in ("16色", "PCE16色", "PCE16色8x8", "16色BMP", "16色MAP"):
            colors = 16
        else:
            colors = 256
        # MEDIANCUT 量化, 转P模式索引色
        im_q = im.quantize(colors=colors, method=Image.MEDIANCUT)

        # 写入临时 BMP, 直接用现有 import_from_bmp 链解析
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".bmp")
        os.close(tmp_fd)
        try:
            im_q.save(tmp_path, format="BMP")
            self.editor.import_from_bmp(cfg, tmp_path, load_pal=load_pal)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    # ---------- 文字工具 ----------

    def _palette_color(self, idx):
        """根据调色板索引取颜色, 容错: 空调色板/越界则回退默认黑白。"""
        idx = int(idx) & 0xFF
        p = getattr(self.editor, "palette", None)
        try:
            if p is not None and hasattr(p, "red") and 0 <= idx < len(p.red):
                return QColor(p.red[idx], p.green[idx], p.blue[idx])
        except Exception:
            pass
        # 默认: 0→黑, 1~254→白(保证对比), 255→黑(安全兜底)
        return QColor(0, 0, 0) if idx == 0 else QColor(255, 255, 255)

    def _nearest_palette_index(self, r: int, g: int, b: int) -> int:
        """在 editor.palette 中找与(r,g,b)欧氏距离最近的色号。
        palette为空时退化到 黑/白/灰 三色近似, 保证至少贴入不崩。
        """
        r, g, b = int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF
        p = getattr(self.editor, "palette", None)
        if p is None or not hasattr(p, "red") or len(p.red) == 0:
            d0 = r * r + g * g + b * b
            d1 = (r - 255) ** 2 + (g - 255) ** 2 + (b - 255) ** 2
            d2 = (r - 128) ** 2 + (g - 128) ** 2 + (b - 128) ** 2
            return min((d0, 0), (d1, 1), (d2, 2))[1]
        n = len(p.red)
        best_idx = 0
        best_d = 1 << 30
        for i in range(n):
            dr = r - int(p.red[i])
            dg = g - int(p.green[i])
            db = b - int(p.blue[i])
            d = dr * dr + dg * dg + db * db
            if d < best_d:
                best_d = d
                best_idx = i
                if d == 0:
                    break
        return best_idx

    def on_text_changed(self):
        self.text_preview.set_text(_text_with_newlines(self.edit_text.text()))

    def on_text_colors_changed(self):
        bg = self._palette_color(self.spin_bg.value())
        fg = self._palette_color(self.spin_fg.value())
        edge = self._palette_color(self.spin_edge.value())
        self.text_preview.set_colors(bg, fg, edge)
        self.text_preview.set_effect(self.combo_effect.currentText())

    def pick_font(self):
        """字体按钮：调用 QFontDialog，兼容 PySide6 返回 (ok:bool, font:QFont) 顺序。

        PySide6 实测 getFont 返回结构 (2026-08-27):
          确认: (True,  <QFont>)
          取消: (False, <初始传入的QFont / 默认QFont>)
              或部分版本: (False,)
        """
        res = QFontDialog.getFont(self.text_preview._font, self)
        font: QFont | None = None
        ok = False
        if isinstance(res, tuple) and len(res) >= 2 and isinstance(res[1], QFont):
            # PySide6 标准: (ok, QFont)
            ok = bool(res[0])
            font = res[1]
        elif isinstance(res, tuple) and len(res) >= 1 and isinstance(res[0], QFont):
            # 兼容 PyQt5 / 旧版: (QFont, ok)
            font = res[0]
            ok = bool(res[1]) if len(res) >= 2 else True
        if ok and isinstance(font, QFont):
            self.text_preview.set_font(font)
            # 保存字体到 QSettings, 下次启动恢复
            self._settings.setValue("text_font", font.toString())

    def _update_text_preview_rect(self, *args):
        """根据勾选状态和 横/纵/宽/高 输入刷新主画布上的文字位置预览框。"""
        if not self.chk_text_preview.isChecked():
            self.canvas.set_show_text_preview(False)
            return
        x = self.spin_text_x.value()
        y = self.spin_text_y.value()
        w = self.spin_text_w.value()
        h = self.spin_text_h.value()
        self.canvas.set_text_preview(x, y, w, h, show=True)

    def apply_text_preview(self):
        """刷新文字预览 (显示按钮)。"""
        try:
            # 若之前载入过图片, 切回文字模式
            if getattr(self.text_preview, "_mode", "text") == "image":
                self.text_preview.clear_loaded_image()
            self.text_preview.set_text(_text_with_newlines(self.edit_text.text()))
            self.on_text_colors_changed()
            # 同步横/纵坐标 (显示按钮生效一次, 防止方向键移动后数据不一致)
            self.text_preview.set_text_pos(self.spin_text_x.value(), self.spin_text_y.value())
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "错误", f"预览刷新失败:\n{e}")

    def load_image_to_preview(self):
        """载入按钮: 载入 16色/256色 BMP 到预览画布(仅点阵, 使用当前编辑图片的调色板)。

        - 编辑 16色 系列图片时只能载入 4bpp BMP
        - 编辑 256色 系列图片时只能载入 8bpp BMP
        - 图片宽高不超过 240×160
        """
        try:
            if not self.editor.viewed or self.editor.palette is None:
                QMessageBox.warning(self, "提示", "请先在主图上点「显示」加载图片, 再载入预览图")
                return
            cur_type = self.combo_type.currentText()
            sixteen_types = ("16色", "超16色", "PCE16色", "PCE16色8x8", "16色BMP", "16色MAP")
            twentyfour_types = ("256色", "256色MAP", "256色BMP")
            if cur_type in sixteen_types:
                expected_bpp = 4
                expected_label = "16色"
            elif cur_type in twentyfour_types:
                expected_bpp = 8
                expected_label = "256色"
            else:
                QMessageBox.warning(self, "提示",
                    f"当前图片格式「{cur_type}」不支持载入预览图\n"
                    f"仅支持: {', '.join(sixteen_types + twentyfour_types)}")
                return
            path, _ = QFileDialog.getOpenFileName(
                self, f"载入{expected_label} BMP (宽≤240, 高≤160)", "", "BMP 图片 (*.bmp)"
            )
            if not path:
                return
            # 先读文件头校验 bpp 和尺寸
            with open(path, "rb") as f:
                head = f.read(30)
            if head[:2] != b"BM":
                QMessageBox.warning(self, "提示", "不是有效的 BMP 文件")
                return
            bpp = int.from_bytes(head[28:30], "little")
            if bpp != expected_bpp:
                QMessageBox.warning(self, "提示",
                    f"位深不匹配: 当前编辑 {expected_label} 图片, 应载入 {expected_bpp}bpp BMP\n"
                    f"所选文件为 {bpp}bpp")
                return
            w = int.from_bytes(head[18:22], "little")
            h = int.from_bytes(head[22:26], "little")
            if w > 240 or h > 160:
                QMessageBox.warning(self, "提示",
                    f"图片尺寸 {w}×{h} 超过 240×160")
                return
            from ..core.bmp_io import import_bmp
            picmap, w, h, _r, _g, _b = import_bmp(path, load_palette=False)
            self.text_preview.load_image_pixels(picmap, w, h, self.editor.palette)
            self.statusBar().showMessage(
                f"已载入: {path}  {w}×{h}  (使用当前编辑图片调色板)"
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "错误", f"载入失败:\n{e}")

    def on_preview_selection(self, x0, y0, x1, y1):
        """文字预览框选完成, 显示选区信息。"""
        w = x1 - x0
        h = y1 - y0
        self.statusBar().showMessage(
            f"预览框选: ({x0},{y0})-({x1-1},{y1-1})  尺寸 {w}×{h}"
        )

    def update_text(self):
        """更新按钮: 定向图片更新。
        若主画布框选尺寸 == 文字预览框选尺寸, 把文字预览框选内容贴到主画布框选位置。
        """
        try:
            if not self.editor.viewed:
                QMessageBox.warning(self, "提示", "请先显示图片")
                return
            canvas_rect = self.canvas._sel_rect
            if canvas_rect is None or canvas_rect.width() <= 0 or canvas_rect.height() <= 0:
                QMessageBox.warning(self, "提示", "请先在主图上左键框选目标区域")
                return
            preview_rect = self.text_preview._sel_rect
            if preview_rect is None or preview_rect.width() <= 0 or preview_rect.height() <= 0:
                QMessageBox.warning(self, "提示", "请先在文字预览上左键框选源区域")
                return
            if canvas_rect.width() != preview_rect.width() or canvas_rect.height() != preview_rect.height():
                QMessageBox.warning(self, "提示",
                    f"框选尺寸不一致\n主图: {canvas_rect.width()}×{canvas_rect.height()}\n"
                    f"预览: {preview_rect.width()}×{preview_rect.height()}")
                return
            # 同步颜色/效果/文字到预览 (不重置文字位置)
            self.text_preview.set_colors(
                self._palette_color(self.spin_bg.value()),
                self._palette_color(self.spin_fg.value()),
                self._palette_color(self.spin_edge.value()),
            )
            self.text_preview.set_effect(self.combo_effect.currentText())
            self.text_preview.set_text(_text_with_newlines(self.edit_text.text()))
            # 获取预览框选区域像素
            pixels = self.text_preview.get_selection_pixels()
            if not pixels:
                QMessageBox.warning(self, "提示", "预览框选区域为空")
                return
            PW = len(pixels)
            PH = len(pixels[0]) if pixels else 0
            # 贴入主画布框选位置
            self.editor.push_undo()
            pm = self.editor.picmap
            EW = int(self.editor.width)
            EH = int(self.editor.height)
            dst_x0 = canvas_rect.x()
            dst_y0 = canvas_rect.y()
            for y in range(PH):
                for x in range(PW):
                    r, g, b = pixels[x][y]
                    idx = self._nearest_palette_index(r, g, b)
                    tx = dst_x0 + x
                    ty = dst_y0 + y
                    if 0 <= tx < EW and 0 <= ty < EH:
                        try:
                            pm[tx][ty] = idx & 0xFF
                        except Exception:
                            pass
            self.canvas.set_data(pm, EW, EH, self.editor.palette)
            self._refresh_undo_btns()
            self.statusBar().showMessage(
                f"定向更新 @({dst_x0},{dst_y0}) 尺寸 {PW}×{PH}"
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "错误", f"更新失败:\n{e}")

    def paste_text(self):
        """把文字预览贴入主画布。对应 VB6 Command13_Click。

        行为约定 (用户反馈验证版):
          * 不改变预览控件上文字 _text_x / _text_y 的位置 (用户肉眼看到预览的文字不应跳动)
          * 贴入尺寸严格取 spin_text_w × spin_text_h (get_pixel_colors 在此尺寸画布独立 render, 不受控件布局影响)
          * 贴入主画布的左上角 = (spin_text_x, spin_text_y)
          * 每个像素都在 editor.palette 里做最近色号匹配 (RGB L2 距离), 不再靠 3 个 idx 的精确相等
        """
        try:
            if not self.editor.viewed:
                QMessageBox.warning(self, "提示", "请先显示图片")
                return
            pm = self.editor.picmap
            if pm is None:
                QMessageBox.warning(self, "提示", "图片数据尚未加载")
                return
            # 贴入前保存撤销快照 (功能1)
            self.editor.push_undo()

            # 1) 把文字工具栏最新的颜色/效果同步到预览 (只改颜色/效果, 不动位置)
            self.text_preview.set_colors(
                self._palette_color(self.spin_bg.value()),
                self._palette_color(self.spin_fg.value()),
                self._palette_color(self.spin_edge.value()),
            )
            self.text_preview.set_effect(self.combo_effect.currentText())
            self.text_preview.set_text(_text_with_newlines(self.edit_text.text()))

            # 2) 贴入尺寸 (严格取 spin)
            W_clip = max(1, int(self.spin_text_w.value()))
            H_clip = max(1, int(self.spin_text_h.value()))

            # 3) 在 W_clip × H_clip 独立画布上按当前基线渲染文字, 得到 [x][y]=RGB
            pixels = self.text_preview.get_pixel_colors(W_clip, H_clip)
            if not pixels:
                return
            PW = len(pixels)
            PH = len(pixels[0]) if pixels else 0

            # 4) 贴入主画布的左上角坐标
            dst_x0 = int(self.spin_text_x.value())
            dst_y0 = int(self.spin_text_y.value())
            EW = int(self.editor.width)
            EH = int(self.editor.height)

            # 5) 遍历每个像素: 最近色号匹配 + 坐标裁剪写入
            for y in range(PH):
                for x in range(PW):
                    r, g, b = pixels[x][y]
                    idx = self._nearest_palette_index(r, g, b)
                    tx = dst_x0 + x
                    ty = dst_y0 + y
                    if 0 <= tx < EW and 0 <= ty < EH:
                        try:
                            pm[tx][ty] = idx & 0xFF
                        except Exception:
                            # 兼容 pm 的形状异常 (比如不是 [EW][EH] 的嵌套 list)
                            pass

            self.canvas.set_data(pm, EW, EH, self.editor.palette)
            self._refresh_undo_btns()
            self.statusBar().showMessage(
                f"文字已贴入 @({dst_x0},{dst_y0}) 尺寸 {PW}×{PH}"
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "错误", f"贴入失败:\n{e}")
