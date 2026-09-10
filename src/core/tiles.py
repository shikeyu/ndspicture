# -*- coding: utf-8 -*-
"""瓦片编解码模块。

移植自 VB6 ndspicture.frm 中的 viewtit16/writetit16/viewtit256/writetit256/
viewpce16/writepce16/viewpce16_8/writepce16_8/viewtit16_2/writetit16_2/
viewtit16map/viewtit256map 等函数。

picmap 约定: 二维列表 picmap[x][y],存储每个像素的调色板索引。
  - 与 VB6 picmap(X, Y) 对应,第一维是 x(列),第二维是 y(行)。

瓦片格式:
  - 16色瓦片: 8×8, 4bit/像素, 32字节, 每字节2像素(高4位=右,低4位=左)
  - 256色瓦片: 8×8, 8bit/像素, 64字节, 行优先
  - PCE16色16×16: 4位平面 × 32字节, 128字节, x翻转
  - PCE16色8×8: 4位平面 × 8字节, 32字节, x翻转
  - 超16色: 16色瓦片 + 每瓦片1字节调色板基址
  - 16色MAP/256色MAP: 用2字节map数据索引瓦片并加调色板偏移
"""

from __future__ import annotations
from typing import List


def _new_picmap(width: int, height: int) -> List[List[int]]:
    """创建 width × height 的 picmap(默认0)。"""
    return [[0] * height for _ in range(width)]


# ---------- 16色瓦片 ----------

def view_tit16(data: bytes, beg: int, titid: int, x: int, y: int, picmap: List[List[int]]) -> None:
    """读取16色瓦片(8×8, 32字节)到 picmap。"""
    base = beg + titid * 32
    for y1 in range(8):
        for x1 in range(4):
            b = data[base + x1 + y1 * 4]
            hi = (b >> 4) & 0x0F
            lo = b & 0x0F
            picmap[x + x1 * 2 + 1][y + y1] = hi
            picmap[x + x1 * 2][y + y1] = lo


def write_tit16(data: bytearray, beg: int, titid: int, x: int, y: int, picmap: List[List[int]]) -> None:
    """把 picmap 的 8×8 区域编码为16色瓦片写回 data。"""
    base = beg + titid * 32
    for y1 in range(8):
        for x1 in range(4):
            hi = picmap[x + x1 * 2 + 1][y + y1] & 0x0F
            lo = picmap[x + x1 * 2][y + y1] & 0x0F
            data[base + x1 + y1 * 4] = (hi << 4) | lo


# ---------- 256色瓦片 ----------

def view_tit256(data: bytes, beg: int, titid: int, x: int, y: int, picmap: List[List[int]]) -> None:
    """读取256色瓦片(8×8, 64字节)到 picmap。"""
    base = beg + titid * 64
    for y1 in range(8):
        for x1 in range(8):
            picmap[x + x1][y + y1] = data[base + x1 + y1 * 8]


def write_tit256(data: bytearray, beg: int, titid: int, x: int, y: int, picmap: List[List[int]]) -> None:
    """把 picmap 的 8×8 区域编码为256色瓦片写回 data。"""
    base = beg + titid * 64
    for y1 in range(8):
        for x1 in range(8):
            data[base + x1 + y1 * 8] = picmap[x + x1][y + y1] & 0xFF


# ---------- PCE 16色 16×16 ----------

def view_pce16(data: bytes, beg: int, titid: int, x: int, y: int, picmap: List[List[int]]) -> None:
    """读取PCE 16色16×16瓦片(128字节, 4位平面, x翻转)到 picmap。"""
    base = beg + titid * 128
    for y1 in range(16):
        for x1 in range(16):
            v = 0
            for z in range(4):
                idx = base + y1 * 2 + x1 // 8 + 32 * z
                if data[idx] & (1 << (x1 % 8)):
                    v |= 1 << z
            picmap[x + 15 - x1][y + y1] = v


def write_pce16(data: bytearray, beg: int, titid: int, x: int, y: int, picmap: List[List[int]]) -> None:
    """把 picmap 的 16×16 区域编码为PCE 16色16×16瓦片写回 data。"""
    base = beg + titid * 128
    tile = bytearray(128)
    for y1 in range(16):
        for x1 in range(16):
            v = picmap[x + 15 - x1][y + y1] & 0x0F
            for z in range(4):
                if v & (1 << z):
                    idx = y1 * 2 + x1 // 8 + 32 * z
                    tile[idx] |= 1 << (x1 % 8)
    data[base:base + 128] = tile


# ---------- PCE 16色 8×8 ----------

def view_pce16_8(data: bytes, beg: int, titid: int, x: int, y: int, picmap: List[List[int]]) -> None:
    """读取PCE 16色8×8瓦片(32字节, 4位平面, x翻转)到 picmap。"""
    base = beg + titid * 32
    for y1 in range(8):
        for x1 in range(7, -1, -1):
            v = 0
            if data[base + y1 * 2] & (1 << x1):
                v |= 1
            if data[base + y1 * 2 + 1] & (1 << x1):
                v |= 2
            if data[base + y1 * 2 + 16] & (1 << x1):
                v |= 4
            if data[base + y1 * 2 + 17] & (1 << x1):
                v |= 8
            picmap[x + 7 - x1][y + y1] = v


def write_pce16_8(data: bytearray, beg: int, titid: int, x: int, y: int, picmap: List[List[int]]) -> None:
    """把 picmap 的 8×8 区域编码为PCE 16色8×8瓦片写回 data。"""
    base = beg + titid * 32
    tile = bytearray(32)
    for y1 in range(8):
        for x1 in range(8):
            v = picmap[x + 7 - x1][y + y1] & 0x0F
            if v & 1:
                tile[y1 * 2] |= 1 << x1
            if v & 2:
                tile[y1 * 2 + 1] |= 1 << x1
            if v & 4:
                tile[y1 * 2 + 16] |= 1 << x1
            if v & 8:
                tile[y1 * 2 + 17] |= 1 << x1
    data[base:base + 32] = tile


# ---------- 超16色 (每瓦片1字节调色板基址) ----------

def view_tit16_2(data: bytes, beg: int, pelidx: int, titid: int, x: int, y: int, picmap: List[List[int]]) -> None:
    """读取超16色瓦片(32字节瓦片 + pelidx+titid 处1字节调色板基址)。"""
    base = beg + titid * 32
    dat2 = data[pelidx + titid]
    for y1 in range(8):
        for x1 in range(4):
            b = data[base + x1 + y1 * 4]
            hi = (b >> 4) & 0x0F
            lo = b & 0x0F
            picmap[x + x1 * 2 + 1][y + y1] = dat2 + hi
            picmap[x + x1 * 2][y + y1] = dat2 + lo


def write_tit16_2(data: bytearray, beg: int, pelidx: int, titid: int, x: int, y: int, picmap: List[List[int]]) -> None:
    """把 picmap 的 8×8 区域编码为超16色瓦片写回 data。"""
    base = beg + titid * 32
    # 调色板基址 = 该瓦片左上角像素值取16的倍数
    dat2 = picmap[x][y] - (picmap[x][y] % 16)
    for y1 in range(8):
        for x1 in range(4):
            hi = (picmap[x + x1 * 2 + 1][y + y1]) % 16
            lo = (picmap[x + x1 * 2][y + y1]) % 16
            data[base + x1 + y1 * 4] = (hi << 4) | lo
    data[pelidx + titid] = dat2 & 0xFF


# ---------- MAP 瓦片 (用 map 数据索引瓦片) ----------

def view_tit16_map(data: bytes, beg: int, map_data: bytes, map_beg: int, mappy: int, mapid: int, x: int, y: int, picmap: List[List[int]]) -> None:
    """16色MAP: 用map数据(2字节)索引瓦片,并加调色板偏移aa。

    GBA 16位map entry 格式:
      bits 0-9   = 瓦片号 (0-1023)
      bit  10    = 水平翻转 (HF)
      bit  11    = 垂直翻转 (VF)
      bits 12-15 = 调色板号 (PALBANK, 0-15)
    """
    b0 = map_data[map_beg + mapid * 2]
    b1 = map_data[map_beg + mapid * 2 + 1]
    # 瓦片号 = bits 0-9
    titid = b0 + (b1 & 0x03) * 256 - (mappy % 4096)
    # 调色板号 = bits 12-15
    aa = (b1 & 0xF0) - (mappy // 4096) * 16
    # 翻转标志
    hflip = (b1 >> 2) & 1
    vflip = (b1 >> 3) & 1
    if titid < 0 or aa < 0:
        return
    base = beg + titid * 32
    for y1 in range(8):
        for x1 in range(4):
            b = data[base + x1 + y1 * 4]
            hi = (b >> 4) & 0x0F
            lo = b & 0x0F
            # 应用翻转: 水平翻转交换左右像素, 垂直翻转交换上下行
            sx = (7 - x1 * 2) if hflip else x1 * 2
            sx2 = (7 - x1 * 2 - 1) if hflip else x1 * 2 + 1
            sy = (7 - y1) if vflip else y1
            picmap[x + sx2][y + sy] = hi + aa
            picmap[x + sx][y + sy] = lo + aa


def view_tit256_map(data: bytes, beg: int, map_data: bytes, map_beg: int, mappy: int, mapid: int, x: int, y: int, picmap: List[List[int]]) -> None:
    """256色MAP: 用map数据(2字节)索引瓦片。

    GBA 16位map entry 格式 (256色模式 PALBANK 无效, 但翻转仍有效):
      bits 0-9   = 瓦片号 (0-1023)
      bit  10    = 水平翻转 (HF)
      bit  11    = 垂直翻转 (VF)
      bits 12-15 = 调色板号 (256色模式忽略)
    """
    b0 = map_data[map_beg + mapid * 2]
    b1 = map_data[map_beg + mapid * 2 + 1]
    # 瓦片号 = bits 0-9
    titid = b0 + (b1 & 0x03) * 256
    # 翻转标志
    hflip = (b1 >> 2) & 1
    vflip = (b1 >> 3) & 1
    base = beg + titid * 64
    for y1 in range(8):
        for x1 in range(8):
            # 应用翻转
            sx = (7 - x1) if hflip else x1
            sy = (7 - y1) if vflip else y1
            picmap[x + sx][y + sy] = data[base + x1 + y1 * 8]


# ---------- 瓦片翻转辅助 (用于 MAP 写入去重) ----------

def hflip_tile16(tile: bytes) -> bytes:
    """16色瓦片(32字节, 8x8, 每字节2像素)水平翻转。

    每行4字节反转, 且每字节高低4位交换(因为低4位=左像素, 高4位=右像素)。
    """
    out = bytearray(32)
    for y in range(8):
        for x in range(4):
            b = tile[y * 4 + x]
            # 交换高低4位, 并反转字节顺序
            out[y * 4 + (3 - x)] = ((b & 0x0F) << 4) | ((b >> 4) & 0x0F)
    return bytes(out)


def vflip_tile16(tile: bytes) -> bytes:
    """16色瓦片(32字节, 8x8)垂直翻转。行顺序反转。"""
    out = bytearray(32)
    for y in range(8):
        out[y * 4:(y + 1) * 4] = tile[(7 - y) * 4:(8 - y) * 4]
    return bytes(out)


def hvflip_tile16(tile: bytes) -> bytes:
    """16色瓦片同时水平和垂直翻转。"""
    return hflip_tile16(vflip_tile16(tile))


def hflip_tile256(tile: bytes) -> bytes:
    """256色瓦片(64字节, 8x8, 每字节1像素)水平翻转。每行8字节反转。"""
    out = bytearray(64)
    for y in range(8):
        for x in range(8):
            out[y * 8 + x] = tile[y * 8 + (7 - x)]
    return bytes(out)


def vflip_tile256(tile: bytes) -> bytes:
    """256色瓦片(64字节, 8x8)垂直翻转。行顺序反转。"""
    out = bytearray(64)
    for y in range(8):
        out[y * 8:(y + 1) * 8] = tile[(7 - y) * 8:(8 - y) * 8]
    return bytes(out)


def hvflip_tile256(tile: bytes) -> bytes:
    """256色瓦片同时水平和垂直翻转。"""
    return hflip_tile256(vflip_tile256(tile))


# ---------- BMP 直读 (无瓦片) ----------

def view_bmp16(data: bytes, beg: int, width: int, height: int, picmap: List[List[int]]) -> None:
    """16色BMP原始数据(每字节2像素, 高4位右低4位左)读取到 picmap。

    VB6 viewbmp16 实际未被主流程调用(主流程在 Command9 中直接处理BMP),
    此函数保留以兼容。
    """
    row_bytes = (width + 1) // 2
    for y1 in range(height):
        for x1 in range(row_bytes):
            b = data[beg + row_bytes * y1 + x1]
            hi = (b >> 4) & 0x0F
            lo = b & 0x0F
            if x1 * 2 < width:
                picmap[x1 * 2][y1] = lo
            if x1 * 2 + 1 < width:
                picmap[x1 * 2 + 1][y1] = hi


def view_bmp256(data: bytes, beg: int, width: int, height: int, picmap: List[List[int]]) -> None:
    """256色BMP原始数据(每字节1像素)读取到 picmap。"""
    for y1 in range(height):
        for x1 in range(width):
            picmap[x1][y1] = data[beg + width * y1 + x1]
