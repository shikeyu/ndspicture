# -*- coding: utf-8 -*-
"""BMP 导入导出模块。

移植自 VB6 ndspicture.frm 中的 Command10(导出) / Command2(导入)。

BMP 格式:
  - 16色: 14+40+64字节头 + 像素数据(每字节2像素, 左高4位右低4位, 从下到上)
  - 256色: 14+40+1024字节头 + 像素数据(每字节1像素, 从下到上)
  - 行宽自动4字节对齐(本工具宽度都是8/16倍数,天然对齐)
"""

from __future__ import annotations
from typing import List, Tuple


def _u32(v: int) -> bytes:
    return bytes([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF])


def _u16(v: int) -> bytes:
    return bytes([v & 0xFF, (v >> 8) & 0xFF])


def _row_bytes(width: int, bpp: int) -> int:
    """每行字节数(4字节对齐)。"""
    raw = (width * bpp + 7) // 8
    return (raw + 3) // 4 * 4


def export_bmp(path: str, picmap: List[List[int]], width: int, height: int,
               pal_red: List[int], pal_green: List[int], pal_blue: List[int],
               pal_alpha: List[int] = None, color_type: str = "RGB 2BIT",
               is_256: bool = False) -> None:
    """导出 picmap 到 BMP 文件。

    is_256=False: 16色BMP; is_256=True: 256色BMP。
    当 color_type == "RGBA 4BIT" 且 is_256 时,额外导出 -a.bmp 作为alpha灰度图。
    """
    if is_256:
        pal_count = 256
        bpp = 8
        header_size = 14 + 40 + 1024
    else:
        pal_count = 16
        bpp = 4
        header_size = 14 + 40 + 64

    rb = _row_bytes(width, bpp)
    pixel_size = rb * height
    file_size = header_size + pixel_size

    out = bytearray()
    # 文件头 (14字节)
    out += b"BM"
    out += _u32(file_size)
    out += _u32(0)
    out += _u32(header_size)
    # 信息头 (40字节)
    out += _u32(40)
    out += _u32(width)
    out += _u32(height)
    out += _u16(1)
    out += _u16(bpp)
    out += _u32(0)
    out += _u32(pixel_size)
    out += _u32(2835)
    out += _u32(2835)
    out += _u32(pal_count)
    out += _u32(pal_count)
    # 调色板 (BGR0)
    for i in range(pal_count):
        out.append(pal_blue[i] & 0xFF)
        out.append(pal_green[i] & 0xFF)
        out.append(pal_red[i] & 0xFF)
        out.append(0)

    # 像素数据 (从下到上)
    if is_256:
        for y in range(height - 1, -1, -1):
            row = bytearray(rb)
            for x in range(width):
                row[x] = picmap[x][y] & 0xFF
            out += row
    else:
        for y in range(height - 1, -1, -1):
            row = bytearray(rb)
            for x in range(0, width, 2):
                left = picmap[x][y] & 0x0F
                right = picmap[x + 1][y] & 0x0F if x + 1 < width else 0
                row[x // 2] = (left << 4) | right
            out += row

    with open(path, "wb") as f:
        f.write(bytes(out))

    # RGBA alpha 图
    if is_256 and color_type == "RGBA 4BIT" and pal_alpha is not None:
        a_path = path[:-4] + "-a.bmp" if path.lower().endswith(".bmp") else path + "-a"
        a_out = bytearray()
        a_file_size = 14 + 40 + 1024 + pixel_size
        a_out += b"BM"
        a_out += _u32(a_file_size)
        a_out += _u32(0)
        a_out += _u32(14 + 40 + 1024)
        a_out += _u32(40)
        a_out += _u32(width)
        a_out += _u32(height)
        a_out += _u16(1)
        a_out += _u16(8)
        a_out += _u32(0)
        a_out += _u32(pixel_size)
        a_out += _u32(2835)
        a_out += _u32(2835)
        a_out += _u32(256)
        a_out += _u32(256)
        for i in range(256):
            a = pal_alpha[i] & 0xFF
            if a == 0x80:
                a = 0
            a_out.append(a)
            a_out.append(a)
            a_out.append(a)
            a_out.append(0)
        for y in range(height - 1, -1, -1):
            row = bytearray(rb)
            for x in range(width):
                row[x] = picmap[x][y] & 0xFF
            a_out += row
        with open(a_path, "wb") as f:
            f.write(bytes(a_out))


def import_bmp(path: str, expected_width: int = 0, expected_height: int = 0,
               expected_bpp: int = 0, load_palette: bool = True
               ) -> Tuple[List[List[int]], int, int, List[int], List[int], List[int]]:
    """导入 BMP 文件,返回 (picmap, width, height, red, green, blue)。

    picmap 维度为 [width][height]。
    若指定 expected_* 且不匹配,抛出 ValueError。
    """
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"BM":
        raise ValueError("不是有效的BMP文件")
    width = int.from_bytes(data[18:22], "little")
    height = int.from_bytes(data[22:26], "little")
    bpp = int.from_bytes(data[28:30], "little")
    header_off = int.from_bytes(data[10:14], "little")
    if expected_width and width != expected_width:
        raise ValueError(f"图片宽度不匹配: {width} != {expected_width}")
    if expected_height and height != expected_height:
        raise ValueError(f"图片高度不匹配: {height} != {expected_height}")
    if expected_bpp and bpp != expected_bpp:
        raise ValueError(f"位深不匹配: {bpp} != {expected_bpp}")

    red = [0] * 256
    green = [0] * 256
    blue = [0] * 256
    if load_palette:
        pal_count = 1 << bpp
        for i in range(pal_count):
            base = 54 + i * 4
            blue[i] = data[base]
            green[i] = data[base + 1]
            red[i] = data[base + 2]

    picmap = [[0] * height for _ in range(width)]
    rb = _row_bytes(width, bpp)
    if bpp == 4:
        for y in range(height):
            src_y = height - 1 - y
            for x in range(0, width, 2):
                b = data[header_off + src_y * rb + x // 2]
                # 左像素(偶数x)在高4位, 右像素(奇数x)在低4位 (与 VB6 一致)
                picmap[x][y] = (b >> 4) & 0x0F
                if x + 1 < width:
                    picmap[x + 1][y] = b & 0x0F
    elif bpp == 8:
        for y in range(height):
            src_y = height - 1 - y
            for x in range(width):
                picmap[x][y] = data[header_off + src_y * rb + x]
    else:
        raise ValueError(f"不支持的位深: {bpp}")
    return picmap, width, height, red, green, blue
