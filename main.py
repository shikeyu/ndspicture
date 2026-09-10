# -*- coding: utf-8 -*-
"""ROM 瓦片图片编辑器 - 入口。

用法: python main.py
"""

import sys
import os

# 把项目根目录加入 sys.path,保证 src 包可被导入
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from src.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ROM图片编辑器")
    # 设置默认中文字体,避免乱码
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
