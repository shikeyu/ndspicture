# -*- coding: utf-8 -*-
"""调色板处理模块。

移植自 VB6 ndspicture.frm 中的 dat2rgb / writepal16 / 颜色加载逻辑。

调色板格式 (Combo3):
- "RGB 2BIT": GBA/NDS 标准 16 位色 BGR555(2字节/色)
- "RGB 3BIT": 24 位色 RGB(3字节/色)
- "RGBA 4BIT": 32 位色 RGBA(4字节/色)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


@dataclass
class Palette:
    """256 色调色板(RGBA)。VB6 中用 nowred/nowgreen/nowblue/nowalpha 四个数组。"""
    red: List[int] = field(default_factory=lambda: [0] * 256)
    green: List[int] = field(default_factory=lambda: [0] * 256)
    blue: List[int] = field(default_factory=lambda: [0] * 256)
    alpha: List[int] = field(default_factory=lambda: [0] * 256)

    def rgb(self, index: int) -> int:
        """返回 RGB 整数值(0xRRGGBB),用于绘制。"""
        i = index & 0xFF
        return (self.red[i] << 16) | (self.green[i] << 8) | self.blue[i]

    def qrgb(self, index: int):
        """返回 (r, g, b) 三元组。"""
        i = index & 0xFF
        return self.red[i], self.green[i], self.blue[i]


def dat2rgb(hdat: int, ldat: int) -> tuple:
    """GBA BGR555 16位色 -> (r, g, b)。

    VB6: nowred=(hdat Mod 32)*8; nowgreen=(hdat\\32 + ldat Mod 4 *8)*8; nowblue=(ldat\\4 Mod 32)*8
    其中 hdat 为低字节, ldat 为高字节(VB6 Get 顺序: 先 dat1 低字节, 后 dat2 高字节)。
    """
    r = (hdat % 32) * 8
    g = ((hdat // 32) + (ldat % 4) * 8) * 8
    b = ((ldat // 4) % 32) * 8
    return r, g, b


def load_palette_from_file(path: str, offset: int, count: int, fmt: str, pal: Palette) -> None:
    """从文件 offset(0-based) 处读取 count 种颜色到 pal。

    与 VB6 Command9 中加载调色板部分一致。
    fmt ∈ {"RGB 2BIT", "RGB 3BIT", "RGBA 4BIT"}。
    """
    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read(count * (2 if fmt == "RGB 2BIT" else (3 if fmt == "RGB 3BIT" else 4)))

    for i in range(count):
        if fmt == "RGB 2BIT":
            if 2 * i + 1 >= len(raw):
                break
            hdat = raw[2 * i]
            ldat = raw[2 * i + 1]
            r, g, b = dat2rgb(hdat, ldat)
            pal.red[i], pal.green[i], pal.blue[i] = r, g, b
        elif fmt == "RGB 3BIT":
            base = 3 * i
            if base + 2 >= len(raw):
                break
            pal.red[i] = raw[base]
            pal.green[i] = raw[base + 1]
            pal.blue[i] = raw[base + 2]
        elif fmt == "RGBA 4BIT":
            base = 4 * i
            if base + 3 >= len(raw):
                break
            pal.red[i] = raw[base]
            pal.green[i] = raw[base + 1]
            pal.blue[i] = raw[base + 2]
            pal.alpha[i] = raw[base + 3]


def load_palette_from_bytes(raw: bytes, offset: int, count: int, fmt: str, pal: Palette) -> None:
    """从字节流 offset(0-based) 处读取 count 种颜色到 pal。用于已解压数据。"""
    for i in range(count):
        if fmt == "RGB 2BIT":
            base = offset + 2 * i
            if base + 1 >= len(raw):
                break
            hdat = raw[base]
            ldat = raw[base + 1]
            r, g, b = dat2rgb(hdat, ldat)
            pal.red[i], pal.green[i], pal.blue[i] = r, g, b
        elif fmt == "RGB 3BIT":
            base = offset + 3 * i
            if base + 2 >= len(raw):
                break
            pal.red[i] = raw[base]
            pal.green[i] = raw[base + 1]
            pal.blue[i] = raw[base + 2]
        elif fmt == "RGBA 4BIT":
            base = offset + 4 * i
            if base + 3 >= len(raw):
                break
            pal.red[i] = raw[base]
            pal.green[i] = raw[base + 1]
            pal.blue[i] = raw[base + 2]
            pal.alpha[i] = raw[base + 3]


def set_gray_palette(pal: Palette, count: int) -> None:
    """未设定调色板地址时,使用灰度调色板(VB6 Command9 中 Text3=0 分支)。"""
    maxcol = count - 1
    for i in range(count):
        v = int(i * 255 / maxcol) if maxcol > 0 else 0
        pal.red[i] = v
        pal.green[i] = v
        pal.blue[i] = v


def write_pal16(path: str, beg: int, pal: Palette) -> None:
    """把前16色以 GBA BGR555 格式写回文件(VB6 writepal16)。

    beg 为 0-based 偏移。写入 2字节/色 × 16 = 32 字节。
    """
    col = bytearray(32)
    for i in range(16):
        h = (pal.blue[i] // 8) * 4 + pal.green[i] // 64
        l = ((pal.green[i] // 8) % 8) * 32 + pal.red[i] // 8
        col[2 * i] = l & 0xFF
        col[2 * i + 1] = h & 0xFF
    with open(path, "r+b") as f:
        f.seek(beg)
        f.write(bytes(col))
