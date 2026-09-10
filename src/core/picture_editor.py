# -*- coding: utf-8 -*-
"""图片编辑器核心逻辑。

移植自 VB6 ndspicture.frm 中的 Command9(显示) / Command14(写回) /
Command13(文字贴入) / checksuper16 等主流程。

地址格式 (与 VB6 兼容):
  - "0x100": 16进制,0-based偏移=0x100
  - "256": 10进制,0-based偏移=256
  - "cmpadd;dataoff": 压缩格式,cmpadd是压缩数据1-based位置(VB6风格),
    dataoff是解压后数据的1-based偏移。内部转换为0-based。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import os
import tempfile

from .palette import Palette, load_palette_from_file, load_palette_from_bytes, set_gray_palette, write_pal16
from .compression import (
    uncomp_from_bytes, uncomp_file, comp_bytes, comp_file,
    uncomp_file_dispatch, comp_bytes_dispatch, block_size_dispatch,
)
from . import tiles
from .tiles import (
    hflip_tile16, vflip_tile16, hvflip_tile16,
    hflip_tile256, vflip_tile256, hvflip_tile256,
)
from .crc16 import crc16


# 图片类型常量 (对应 VB6 Combo1)
T_16COLOR = "16色"
T_256COLOR = "256色"
T_TRUE16 = "超16色"
T_PCE16 = "PCE16色"
T_PCE16_8 = "PCE16色8x8"
T_16COLOR_BMP = "16色BMP"
T_256COLOR_BMP = "256色BMP"
T_16COLOR_MAP = "16色MAP"
T_256COLOR_MAP = "256色MAP"


@dataclass(frozen=True)
class ParsedAddr:
    """解析后的地址。comp_kind 决定 cmp_offset/huff_offset 的语义。

    - None: cmp_offset=None, huff_offset=None, data_offset=ROM 内偏移
    - 'lz77'/'rle'/'huff': cmp_offset=ROM 内块地址, huff_offset=None,
                           data_offset=解压后流内偏移
    - 'huff_lz77': cmp_offset=Huffman 解码流内 LZ77 块偏移,
                   huff_offset=ROM 内 Huffman 块地址,
                   data_offset=LZ77 解压后流内偏移
    """
    comp_kind: Optional[str]
    data_offset: int
    cmp_offset: Optional[int] = None
    huff_offset: Optional[int] = None

    @property
    def is_compressed(self) -> bool:
        return self.comp_kind is not None

    def __iter__(self):
        """向后兼容旧式 cmp, off = parse_addr(text) 解包为 (cmp_offset, data_offset)。
        注意: huff_lz77 下此语义不正确, 必须改用命名属性。"""
        return iter((self.cmp_offset, self.data_offset))


@dataclass
class PicConfig:
    """单张图片的配置(对应 VB6 窗体上的 Text/Combo 输入)。"""
    rom_path: str = ""
    ptype: str = T_16COLOR
    colotype: str = "RGB 2BIT"
    palette_addr: str = "0"      # Text3
    tile_addr: str = "0"         # Text4
    pal_idx: str = "0"           # Text14
    pal_py: int = 0              # Text20
    pwidth: int = 1              # Text5 (瓦片数)
    pheight: int = 1             # Text6
    twidth: int = 1             # Text18 (瓦片尺寸因子)
    theight: int = 1            # Text19
    bmp_fname: str = ""          # Text8
    write_pal: bool = False     # Check1
    title: str = ""


def parse_addr(text: str) -> "ParsedAddr":
    """解析地址字符串,返回 ParsedAddr。

    支持的格式(按优先级):
      1. 0xHuff块:0xLZ77块;0x偏移   -> huff_lz77
      2. 0x块:0x偏移                  -> huff
      3. 0x块;0x偏移                  -> lz77
      4. 0x块!0x偏移                  -> rle
      5. 0x偏移                        -> raw

    地址支持十进制和 0x/&h 前缀的十六进制。
    """
    text = text.strip()
    if not text:
        return ParsedAddr(comp_kind=None, data_offset=0)

    has_colon = ":" in text
    has_semi = ";" in text
    has_bang = "!" in text

    # 优先级 1: huff_lz77 (同时含 ':' 和 ';')
    if has_colon and has_semi:
        left, right = text.split(";", 1)
        huff_part, lz77_part = left.split(":", 1)
        return ParsedAddr(
            comp_kind="huff_lz77",
            data_offset=_to_int(right),
            cmp_offset=_to_int(lz77_part),       # LZ77 块在 Huffman 流内偏移
            huff_offset=_to_int(huff_part),      # ROM 中 Huffman 块地址
        )
    # 优先级 2: huff (仅 ':')
    if has_colon:
        left, right = text.split(":", 1)
        return ParsedAddr(
            comp_kind="huff",
            data_offset=_to_int(right),
            cmp_offset=_to_int(left),
        )
    # 优先级 3: lz77 (仅 ';')
    if has_semi:
        left, right = text.split(";", 1)
        return ParsedAddr(
            comp_kind="lz77",
            data_offset=_to_int(right),
            cmp_offset=_to_int(left),
        )
    # 优先级 4: rle (仅 '!')
    if has_bang:
        left, right = text.split("!", 1)
        return ParsedAddr(
            comp_kind="rle",
            data_offset=_to_int(right),
            cmp_offset=_to_int(left),
        )
    # 优先级 5: raw
    return ParsedAddr(comp_kind=None, data_offset=_to_int(text))


def _to_int(s: str) -> int:
    """把字符串解析为整数(0-based偏移),空串返回0。

    支持 16 进制前缀: "0x" / "0X" / "&h" / "&H" / "&H" (VB6 风格) 和 10 进制。
    """
    s = s.strip()
    if not s:
        return 0
    low = s.lower()
    if low.startswith("0x"):
        return int(s, 16)
    if low.startswith("&h"):
        # VB6 风格: &h85544 末尾可能带 & 后缀(&h85544&)
        tail = s[2:]
        if tail.endswith("&"):
            tail = tail[:-1]
        return int(tail, 16)
    return int(s)


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class PictureEditor:
    """图片编辑器,管理 picmap、调色板和当前配置。"""

    MAX_UNDO = 50

    def __init__(self):
        self.palette = Palette()
        self.picmap: List[List[int]] = []  # [x][y]
        self.width: int = 0
        self.height: int = 0
        self.viewed: bool = False
        self.temp_dir = tempfile.gettempdir()
        # 撤销/重做栈, 每项为 (picmap_deepcopy, palette.copy, width, height, viewed)
        self._undo: List[tuple] = []
        self._redo: List[tuple] = []
        # 16色MAP: 按条目(tile_addr, pal_idx)缓存原始map引用的瓦片数, 写入时校验不超出
        # 同一条目第一次显示时记录, 之后ROM被多次写入不再更新(写入后瓦片数只会减少)
        self._map_orig_tile_count: dict = {}  # key=(tile_addr, pal_idx) -> count

    # ---------- 撤销/重做 ----------

    def _snapshot(self) -> tuple | None:
        """当前状态做深拷贝快照, 返回 None 表示无可保存。"""
        if not self.viewed or self.width <= 0 or self.height <= 0:
            return None
        # picmap 深拷贝
        pm = [row[:] for row in self.picmap]
        # 调色板 copy
        pal = Palette()
        try:
            pal.red = list(self.palette.red)
            pal.green = list(self.palette.green)
            pal.blue = list(self.palette.blue)
            if hasattr(self.palette, "alpha") and self.palette.alpha:
                pal.alpha = list(self.palette.alpha)
        except Exception:
            pass
        return (pm, pal, int(self.width), int(self.height), True)

    def _restore(self, snap: tuple) -> None:
        pm, pal, w, h, v = snap
        self.picmap = [row[:] for row in pm]
        self.width = int(w)
        self.height = int(h)
        self.viewed = bool(v)
        try:
            self.palette.red = list(pal.red)
            self.palette.green = list(pal.green)
            self.palette.blue = list(pal.blue)
            if hasattr(pal, "alpha") and pal.alpha is not None:
                self.palette.alpha = list(pal.alpha)
        except Exception:
            pass

    def push_undo(self) -> bool:
        """调用方(主窗口)在执行破坏性操作前调用,把当前状态压入撤销栈。
        返回 True 表示快照已保存。
        """
        snap = self._snapshot()
        if snap is None:
            return False
        self._undo.append(snap)
        if len(self._undo) > self.MAX_UNDO:
            del self._undo[0]
        # 任何新操作都清空重做栈 (与用户习惯一致)
        self._redo.clear()
        return True

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        cur = self._snapshot()
        snap = self._undo.pop()
        self._restore(snap)
        if cur is not None:
            self._redo.append(cur)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        cur = self._snapshot()
        snap = self._redo.pop()
        self._restore(snap)
        if cur is not None:
            self._undo.append(cur)
        return True

    # ---------- 尺寸计算 ----------

    def _picmap_size(self, cfg: PicConfig) -> Tuple[int, int]:
        t = cfg.ptype
        if t == T_16COLOR_MAP:
            # 16色MAP: 片宽/片高固定为8x8, 不使用 twidth/theight
            w = cfg.pwidth * 8
            h = cfg.pheight * 8
        elif t in (T_16COLOR, T_PCE16_8):
            w = cfg.pwidth * cfg.twidth * 8
            h = cfg.pheight * cfg.theight * 8
        elif t == T_PCE16:
            w = cfg.pwidth * cfg.twidth * 16
            h = cfg.pheight * cfg.theight * 16
        elif t == T_256COLOR_MAP:
            w = cfg.pwidth * 8
            h = cfg.pheight * 8
        elif t in (T_256COLOR, T_TRUE16):
            # 256色和超16色: 先按片宽×片高组成小图(8x8 each), 再按图宽×图高组成大图
            w = cfg.pwidth * cfg.twidth * 8
            h = cfg.pheight * cfg.theight * 8
        elif t in (T_16COLOR_BMP, T_256COLOR_BMP):
            w = cfg.pwidth
            h = cfg.pheight
        else:
            w = cfg.pwidth * 8
            h = cfg.pheight * 8
        return w, h

    def _max_color(self, ptype: str) -> int:
        if ptype in (T_16COLOR, T_PCE16, T_PCE16_8, T_16COLOR_BMP, T_16COLOR_MAP):
            return 15
        return 255

    # ---------- 显示 (Command9) ----------

    def show(self, cfg: PicConfig) -> None:
        """从 ROM 读取瓦片到 picmap 并加载调色板。对应 VB6 Command9_Click。"""
        if not cfg.rom_path or not os.path.exists(cfg.rom_path):
            raise FileNotFoundError("ROM文件不存在")

        w, h = self._picmap_size(cfg)
        self.width, self.height = w, h
        self.picmap = [[0] * h for _ in range(w)]

        # 解析地址
        pa_bmp = parse_addr(cfg.tile_addr)
        pa_pal = parse_addr(cfg.palette_addr)
        pa_idx = parse_addr(cfg.pal_idx)

        maxcol = self._max_color(cfg.ptype)

        # 加载调色板
        if cfg.palette_addr.strip() in ("0", ""):
            set_gray_palette(self.palette, maxcol + 1)
        else:
            if pa_pal.is_compressed:
                # 压缩: 先解压到临时文件
                temp_pal = os.path.join(self.temp_dir, "nds_pal.dat")
                uncomp_file_dispatch(cfg.rom_path, temp_pal, pa_pal)
                with open(temp_pal, "rb") as f:
                    raw = f.read()
                load_palette_from_bytes(raw, pa_pal.data_offset, maxcol + 1, cfg.colotype, self.palette)
            else:
                load_palette_from_file(cfg.rom_path, pa_pal.data_offset, maxcol + 1, cfg.colotype, self.palette)

        # 读取瓦片
        if pa_bmp.is_compressed:
            # 压缩: 解压到临时文件
            temp_bmp = os.path.join(self.temp_dir, "nds_bmp.dat")
            uncomp_file_dispatch(cfg.rom_path, temp_bmp, pa_bmp)
            bmp_data = _read_file_bytes(temp_bmp)
            bmp_base = pa_bmp.data_offset
        else:
            bmp_data = _read_file_bytes(cfg.rom_path)
            bmp_base = pa_bmp.data_offset

        # 调色板索引数据(超16色/16色MAP用)
        if pa_idx.is_compressed:
            temp_idx = os.path.join(self.temp_dir, "nds_idx.dat")
            uncomp_file_dispatch(cfg.rom_path, temp_idx, pa_idx)
            idx_data = _read_file_bytes(temp_idx)
            idx_base = pa_idx.data_offset
        else:
            idx_data = None
            idx_base = 0

        # ROM 数据(用于 map 模式的 map 数据)
        rom_data = _read_file_bytes(cfg.rom_path) if cfg.ptype in (T_16COLOR_MAP, T_256COLOR_MAP) else None

        self._render_tiles(cfg, bmp_data, bmp_base, idx_data, idx_base, rom_data)
        self.viewed = True

    def _render_tiles(self, cfg: PicConfig, bmp_data: bytes, bmp_base: int,
                      idx_data: Optional[bytes], idx_base: int, rom_data: Optional[bytes]) -> None:
        """根据图片类型调用对应的瓦片渲染函数。"""
        t = cfg.ptype
        pw, ph = cfg.pwidth, cfg.pheight
        tw, th = cfg.twidth, cfg.theight
        pm = self.picmap

        if t == T_16COLOR:
            for y in range(ph):
                for x in range(pw):
                    for y1 in range(th):
                        for x1 in range(tw):
                            titid = (y * pw + x) * (tw * th) + y1 * tw + x1
                            tiles.view_tit16(bmp_data, bmp_base, titid,
                                             x * 8 * tw + x1 * 8, y * 8 * th + y1 * 8, pm)
        elif t == T_PCE16:
            for y in range(ph):
                for x in range(pw):
                    for y1 in range(th):
                        for x1 in range(tw):
                            titid = (y * pw + x) * (tw * th) + y1 * tw + x1
                            tiles.view_pce16(bmp_data, bmp_base, titid,
                                             x * 16 * tw + x1 * 16, y * 16 * th + y1 * 16, pm)
        elif t == T_PCE16_8:
            for y in range(ph):
                for x in range(pw):
                    for y1 in range(th):
                        for x1 in range(tw):
                            titid = (y * pw + x) * (tw * th) + y1 * tw + x1
                            tiles.view_pce16_8(bmp_data, bmp_base, titid,
                                               x * 8 * tw + x1 * 8, y * 8 * th + y1 * 8, pm)
        elif t == T_256COLOR:
            for y in range(ph):
                for x in range(pw):
                    for y1 in range(th):
                        for x1 in range(tw):
                            titid = (y * pw + x) * (tw * th) + y1 * tw + x1
                            tiles.view_tit256(bmp_data, bmp_base, titid,
                                              x * 8 * tw + x1 * 8, y * 8 * th + y1 * 8, pm)
        elif t == T_TRUE16:
            for y in range(ph):
                for x in range(pw):
                    for y1 in range(th):
                        for x1 in range(tw):
                            # 超16色: pal_idx 是调色板基址文件的偏移
                            pelidx = cfg.pal_py if cfg.pal_idx.strip() in ("0", "") else idx_base
                            titid = (y * pw + x) * (tw * th) + y1 * tw + x1
                            tiles.view_tit16_2(bmp_data, bmp_base, pelidx, titid,
                                               x * 8 * tw + x1 * 8, y * 8 * th + y1 * 8, pm)
        elif t == T_16COLOR_BMP:
            for y in range(ph):
                for x in range(0, pw, 2):
                    b = bmp_data[bmp_base + y * (pw // 2) + x // 2]
                    pm[x][y] = b & 0x0F
                    if x + 1 < pw:
                        pm[x + 1][y] = (b >> 4) & 0x0F
        elif t == T_256COLOR_BMP:
            for y in range(ph):
                for x in range(pw):
                    pm[x][y] = bmp_data[bmp_base + y * pw + x]
        elif t == T_16COLOR_MAP:
            # map 数据地址 = cfg.pal_idx, mappy = cfg.pal_py
            pa_map = parse_addr(cfg.pal_idx)
            if pa_map.is_compressed:
                temp_map = os.path.join(self.temp_dir, "nds_map.dat")
                uncomp_file_dispatch(cfg.rom_path, temp_map, pa_map)
                map_data = _read_file_bytes(temp_map)
                map_base = pa_map.data_offset
            else:
                map_data = rom_data
                map_base = pa_map.data_offset
            # 统计原始map引用的瓦片数(去重), 供写入时校验
            # 按条目缓存, 同一条目仅第一次显示时记录, 之后不再更新
            _map_key = (cfg.tile_addr, cfg.pal_idx)
            if _map_key not in self._map_orig_tile_count:
                _orig_tids = set()
                for y in range(ph):
                    for x in range(pw):
                        mapid = y * pw + x
                        mi = map_base + mapid * 2
                        if mi + 1 < len(map_data):
                            b0 = map_data[mi]
                            b1 = map_data[mi + 1]
                            tid = b0 + (b1 & 0x03) * 256 - (cfg.pal_py % 4096)
                            if 0 <= tid < 1024:
                                _orig_tids.add(tid)
                self._map_orig_tile_count[_map_key] = len(_orig_tids)
            for y in range(ph):
                for x in range(pw):
                    mapid = y * pw + x
                    tiles.view_tit16_map(bmp_data, bmp_base, map_data, map_base,
                                         cfg.pal_py, mapid, x * 8, y * 8, pm)
        elif t == T_256COLOR_MAP:
            pa_map = parse_addr(cfg.pal_idx)
            if pa_map.is_compressed:
                temp_map = os.path.join(self.temp_dir, "nds_map.dat")
                uncomp_file_dispatch(cfg.rom_path, temp_map, pa_map)
                map_data = _read_file_bytes(temp_map)
                map_base = pa_map.data_offset
            else:
                map_data = rom_data
                map_base = pa_map.data_offset
            # 统计原始map引用的瓦片数(去重), 按条目缓存, 仅第一次记录
            _map_key = (cfg.tile_addr, cfg.pal_idx)
            if _map_key not in self._map_orig_tile_count:
                _orig_tids = set()
                for y in range(ph):
                    for x in range(pw):
                        mapid = y * pw + x
                        mi = map_base + mapid * 2
                        if mi + 1 < len(map_data):
                            b0 = map_data[mi]
                            b1 = map_data[mi + 1]
                            # 256色MAP: pal_py不参与, 与 view_tit256_map 一致
                            tid = b0 + (b1 & 0x03) * 256
                            if 0 <= tid < 1024:
                                _orig_tids.add(tid)
                self._map_orig_tile_count[_map_key] = len(_orig_tids)
            for y in range(ph):
                for x in range(pw):
                    mapid = y * pw + x
                    tiles.view_tit256_map(bmp_data, bmp_base, map_data, map_base,
                                          cfg.pal_py, mapid, x * 8, y * 8, pm)

    # ---------- 写回 ROM (Command14) ----------

    def write_back(self, cfg: PicConfig) -> str:
        """把 picmap 编码写回 ROM。返回提示信息。对应 VB6 Command14_Click。

        支持任意字段(调色板/瓦片/MAP)独立或共享压缩块的场景:
        开头按"压缩块唯一键"分组字段, 每个压缩块只解压一次,
        所有字段修改都进入对应 fname buffer, 最后按压缩块分别压缩写回。
        """
        if not self.viewed:
            raise RuntimeError("无法写入,请先执行显示。")
        if not os.path.exists(cfg.rom_path):
            raise FileNotFoundError("ROM文件不存在")

        # ---- 解析各字段地址 ----
        pa_tile = parse_addr(cfg.tile_addr)
        pa_pal = parse_addr(cfg.palette_addr)
        is_map_type = cfg.ptype in (T_16COLOR_MAP, T_256COLOR_MAP)
        pa_map = parse_addr(cfg.pal_idx) if is_map_type else None
        write_pal = bool(cfg.write_pal)

        # ---- 按压缩块唯一键分组字段, 同一压缩块只解压一次 ----
        # 键: ("raw",) 或 ("comp", comp_kind, cmp_offset, huff_offset)
        # 值: {"fname": str, "buffer": bytearray, "pa": ParsedAddr, "fields": [str]}
        groups = {}

        def _comp_key(pa: "ParsedAddr"):
            if pa.comp_kind is None:
                return ("raw",)
            return ("comp", pa.comp_kind, pa.cmp_offset, pa.huff_offset)

        def _get_or_create_group(pa: "ParsedAddr", field_name: str, suffix: str):
            """获取(或创建)字段对应的分组, 返回 (buffer, data_offset)。"""
            key = _comp_key(pa)
            if key not in groups:
                if pa.comp_kind is None:
                    # RAW: 共用整个 ROM 文件 (所有 RAW 字段共享同一 ROM buffer)
                    with open(cfg.rom_path, "rb") as f:
                        groups[("raw",)] = {
                            "fname": cfg.rom_path,
                            "buffer": bytearray(f.read()),
                            "pa": None,  # RAW 无单一 pa
                            "fields": [],
                        }
                else:
                    # 压缩: 解压到独立临时文件
                    fname = os.path.join(self.temp_dir, f"nds_w_{suffix}.bin")
                    uncomp_file_dispatch(cfg.rom_path, fname, pa)
                    with open(fname, "rb") as f:
                        buf = bytearray(f.read())
                    g = groups[key] = {
                        "fname": fname,
                        "buffer": buf,
                        "orig_data": bytes(buf),  # 保存原始解压数据, 用于检测是否被修改
                        "pa": pa,
                        "fields": [],
                    }
            g = groups[key]
            g["fields"].append(field_name)
            return g["buffer"], pa.data_offset

        # 注册字段 (顺序: 调色板 -> 瓦片 -> MAP)
        pal_buf = None
        pal_off = 0
        if write_pal:
            pal_buf, pal_off = _get_or_create_group(pa_pal, "palette", "pal")
        tile_buf, tile_off = _get_or_create_group(pa_tile, "tile", "tile")
        map_buf = None
        map_off = 0
        if pa_map is not None:
            map_buf, map_off = _get_or_create_group(pa_map, "map", "map")

        # ---- 调色板写入 (按图片类型决定颜色数) ----
        # 注意: pal_off == 0 是合法的(调色板在压缩块偏移0), 不能用 pal_off > 0 跳过
        if write_pal and pal_buf is not None:
            n_colors = 256 if cfg.ptype in (T_256COLOR, T_TRUE16, T_256COLOR_MAP, T_256COLOR_BMP) else 16
            _write_pal_inplace(pal_buf, pal_off, self.palette, n_colors)

        # ---- 超16色写入前校验 ----
        if cfg.ptype == T_TRUE16:
            bad_tiles = []
            for ty in range(self.height // 8):
                for tx in range(self.width // 8):
                    if self.check_super16(tx, ty):
                        bad_tiles.append((tx, ty))
            if bad_tiles:
                sample = ", ".join(f"({tx},{ty})" for tx, ty in bad_tiles[:5])
                raise RuntimeError(
                    f"超16色写入校验失败: {len(bad_tiles)} 个 8×8 瓦片内存在跨调色板组的颜色。\n"
                    f"每个瓦片只能使用同一16色调色板组(像素值//16必须相同)。\n"
                    f"不合规瓦片(前5个): {sample}"
                )

        # ---- 瓦片表 + MAP 写入 ----
        self._encode_tiles(cfg, tile_buf, tile_off, map_buf, map_off)

        # ---- 按压缩块分别压缩写回 ----
        with open(cfg.rom_path, "rb") as f:
            rom_raw = f.read()
        msg_parts = []
        for key, g in groups.items():
            if key[0] == "raw":
                # RAW: 整个 ROM buffer 写回 (r+b 只写修改部分, 避免截断)
                with open(cfg.rom_path, "r+b") as f:
                    f.write(bytes(g["buffer"]))
                msg_parts.append("已写入ROM")
            else:
                pa = g["pa"]
                buf = g["buffer"]
                orig_data = g.get("orig_data")
                cmpadd = pa.huff_offset if pa.comp_kind == "huff_lz77" else pa.cmp_offset
                # 检测数据是否被修改: 若未修改则跳过压缩写入, 避免压缩器效率差异导致膨胀
                if orig_data is not None and bytes(buf) == orig_data:
                    msg_parts.append(f"未修改[{','.join(g['fields'])}]")
                    continue
                try:
                    comp_data = comp_bytes_dispatch(bytes(buf), pa.comp_kind, beg=cmpadd)
                except NotImplementedError as e:
                    raise RuntimeError(str(e))
                orig_comp_size = block_size_dispatch(rom_raw, pa)
                new_comp_size = len(comp_data)
                # 4字节对齐
                while new_comp_size % 4 != 0:
                    comp_data += b'\x00'
                    new_comp_size = len(comp_data)
                if new_comp_size > orig_comp_size:
                    raise RuntimeError(
                        f"压缩数据变大({new_comp_size} > {orig_comp_size}字节, "
                        f"字段={g['fields']}), 写入会覆盖ROM后续数据。请减少修改量或联系开发者。"
                    )
                pad = orig_comp_size - new_comp_size
                with open(cfg.rom_path, "r+b") as f:
                    f.seek(cmpadd)
                    f.write(comp_data)
                    if pad > 0:
                        f.write(b'\x00' * pad)
                msg_parts.append(
                    f"压缩[{','.join(g['fields'])}](原始{orig_comp_size}字节, 新{new_comp_size}字节)"
                )
        return "已写入: " + "; ".join(msg_parts)

    def _encoded_size(self, cfg: PicConfig) -> int:
        t = cfg.ptype
        if t == T_16COLOR:
            return cfg.pwidth * cfg.pheight * cfg.twidth * cfg.theight * 32
        if t == T_16COLOR_MAP:
            # 16色MAP: 固定瓦片 32字节
            return cfg.pwidth * cfg.pheight * 32
        if t == T_PCE16:
            return cfg.pwidth * cfg.pheight * cfg.twidth * cfg.theight * 128
        if t == T_PCE16_8:
            return cfg.pwidth * cfg.pheight * cfg.twidth * cfg.theight * 32
        if t == T_256COLOR:
            return cfg.pwidth * cfg.pheight * cfg.twidth * cfg.theight * 64
        if t == T_TRUE16:
            return cfg.pwidth * cfg.pheight * cfg.twidth * cfg.theight * 32
        if t == T_16COLOR_BMP:
            return cfg.pwidth * cfg.pheight // 2
        if t == T_256COLOR_BMP:
            return cfg.pwidth * cfg.pheight
        return 0

    def _encode_tiles(self, cfg: PicConfig, tile_data: bytearray, inadd: int,
                      map_data: Optional[bytearray] = None, map_base: int = 0) -> None:
        """把 picmap 编码到 tile_data 的 inadd 位置。

        对应 VB6 Command14 中的 Select Case。MAP 类型还会写入 map_data[map_base:]。
        tile_data / map_data 分别是 tile_addr / pal_idx 字段对应的 buffer (可能共享)。
        """
        t = cfg.ptype
        pw, ph = cfg.pwidth, cfg.pheight
        tw, th = cfg.twidth, cfg.theight
        pm = self.picmap

        if t == T_16COLOR:
            for y in range(ph):
                for x in range(pw):
                    for y1 in range(th):
                        for x1 in range(tw):
                            titid = (y * pw + x) * (tw * th) + y1 * tw + x1
                            tiles.write_tit16(tile_data, inadd, titid,
                                              x * 8 * tw + x1 * 8, y * 8 * th + y1 * 8, pm)
        elif t == T_PCE16:
            for y in range(ph):
                for x in range(pw):
                    for y1 in range(th):
                        for x1 in range(tw):
                            titid = (y * pw + x) * (tw * th) + y1 * tw + x1
                            tiles.write_pce16(tile_data, inadd, titid,
                                              x * 16 * tw + x1 * 16, y * 16 * th + y1 * 16, pm)
        elif t == T_PCE16_8:
            for y in range(ph):
                for x in range(pw):
                    for y1 in range(th):
                        for x1 in range(tw):
                            titid = (y * pw + x) * (tw * th) + y1 * tw + x1
                            tiles.write_pce16_8(tile_data, inadd, titid,
                                                x * 8 * tw + x1 * 8, y * 8 * th + y1 * 8, pm)
        elif t == T_256COLOR:
            for y in range(ph):
                for x in range(pw):
                    for y1 in range(th):
                        for x1 in range(tw):
                            titid = (y * pw + x) * (tw * th) + y1 * tw + x1
                            tiles.write_tit256(tile_data, inadd, titid,
                                               x * 8 * tw + x1 * 8, y * 8 * th + y1 * 8, pm)
        elif t == T_TRUE16:
            pelidx = cfg.pal_py if cfg.pal_idx.strip() in ("0", "") else parse_addr(cfg.pal_idx).data_offset
            for y in range(ph):
                for x in range(pw):
                    for y1 in range(th):
                        for x1 in range(tw):
                            titid = (y * pw + x) * (tw * th) + y1 * tw + x1
                            tiles.write_tit16_2(tile_data, inadd, pelidx, titid,
                                                x * 8 * tw + x1 * 8, y * 8 * th + y1 * 8, pm)
        elif t == T_16COLOR_BMP:
            for y in range(ph):
                for x in range(0, pw, 2):
                    b = (pm[x + 1][y] & 0x0F) * 16 + (pm[x][y] & 0x0F)
                    tile_data[inadd + y * (pw // 2) + x // 2] = b
        elif t == T_256COLOR_BMP:
            for y in range(ph):
                for x in range(pw):
                    tile_data[inadd + y * pw + x] = pm[x][y] & 0xFF
        elif t == T_16COLOR_MAP:
            self._write_16color_map(cfg, tile_data, inadd, map_data, map_base)
        elif t == T_256COLOR_MAP:
            self._write_256color_map(cfg, tile_data, inadd, map_data, map_base)
        else:
            raise ValueError(f"图片类型 {t} 无法写入")

    def _write_16color_map(self, cfg: PicConfig, tile_data: bytearray, inadd: int,
                           map_data: bytearray, map_base: int) -> None:
        """16色MAP写入: 使用CRC16去重,生成瓦片表+map表。对应 VB6 Command14 16色MAP分支。

        关键:
        - map表写入 map_data[map_base:] (pal_idx 字段对应的 buffer, 压缩或 RAW 均一致)
        - 瓦片表写入 tile_data[inadd:] (tile_addr 字段对应的 buffer)
        - 原始 map entry 从 map_data 读取 (保留调色板号 bits12-15)
        - 去重时支持翻转匹配: 检查瓦片的 HFlip/VFlip/HVFlip 变体是否已存在,
          若匹配则复用已有瓦片ID并设置对应的翻转标志(bits10-11),
          从而减少瓦片数量
        """
        pw, ph = cfg.pwidth, cfg.pheight
        # 16色MAP: 固定8x8瓦片, 忽略twidth/theight
        pixel_w = pw * 8
        pixel_h = ph * 8
        pm = self.picmap

        # 读取原始map entry (从 map_data, 保留调色板号)
        num_map = pw * ph
        orig_map = []
        for i in range(num_map):
            mi = map_base + i * 2
            if mi + 1 < len(map_data):
                b0 = map_data[mi]
                b1 = map_data[mi + 1]
                orig_map.append((b0, b1))
            else:
                orig_map.append((0, 0))

        tdata_list = []  # 瓦片数据(32字节/瓦片)
        crc_list = []    # 每个瓦片的CRC
        map_entries = []  # map表: (瓦片ID, 翻转标志), 翻转标志 0/0x04/0x08/0x0C

        # 翻转变体: (翻转函数, 翻转标志位)
        # 如果 hflip(tile) == 已存在的瓦片A, 则 tile = hflip(A),
        # 显示A时设置HFlip标志即可显示hflip(A)=tile
        flip_variants = [
            (None, 0x00),                 # 原始, 无翻转
            (hflip_tile16, 0x04),         # HFlip (bit 10)
            (vflip_tile16, 0x08),         # VFlip (bit 11)
            (hvflip_tile16, 0x0C),         # HVFlip (bit 10+11)
        ]

        for y in range(0, pixel_h, 8):
            for x in range(0, pixel_w, 8):
                tile = bytearray(32)
                for y1 in range(8):
                    for x1 in range(0, 8, 2):
                        tile[y1 * 4 + x1 // 2] = (pm[x + x1][y + y1] & 0x0F) + \
                            16 * (pm[x + x1 + 1][y + y1] & 0x0F)
                tile = bytes(tile)

                found_id = -1
                found_flip = 0
                for flip_fn, flip_flag in flip_variants:
                    candidate = flip_fn(tile) if flip_fn else tile
                    crc = crc16(candidate)
                    for i, c in enumerate(crc_list):
                        if c == crc and tdata_list[i] == candidate:
                            found_id = i
                            found_flip = flip_flag
                            break
                    if found_id >= 0:
                        break

                if found_id >= 0:
                    map_entries.append((found_id, found_flip))
                else:
                    tdata_list.append(tile)
                    crc_list.append(crc16(tile))
                    map_entries.append((len(tdata_list) - 1, 0))

        # 校验: 去重后的瓦片数不能超过原始map引用的瓦片数, 也不能超过GBA上限1024
        new_tile_count = len(tdata_list)
        _map_key = (cfg.tile_addr, cfg.pal_idx)
        orig_count = self._map_orig_tile_count.get(_map_key, 0)
        max_tiles = max(orig_count, 1)
        if new_tile_count > max_tiles:
            raise RuntimeError(
                f"瓦片数量超出: 导入后去重得到 {new_tile_count} 个瓦片, "
                f"但原始map区只使用了 {orig_count} 个瓦片。"
                f"请减少图片中的颜色变化或增大片宽/片高。"
            )
        if new_tile_count > 1024:
            raise RuntimeError(
                f"瓦片数量超出GBA上限: 去重得到 {new_tile_count} 个瓦片, "
                f"GBA 16色MAP最多支持 1024 个瓦片。"
            )
        # 写入map表: 保留原始调色板号(bits12-15), 设置翻转标志(bits10-11),
        # 更新瓦片号(bits 0-9)
        for i, (tid, flip_flag) in enumerate(map_entries):
            orig_b0, orig_b1 = orig_map[i] if i < len(orig_map) else (0, 0)
            new_b0 = tid & 0xFF
            new_b1 = (orig_b1 & 0xF0) | flip_flag | ((tid >> 8) & 0x03)
            mi = map_base + i * 2
            if mi + 1 < len(map_data):
                map_data[mi] = new_b0
                map_data[mi + 1] = new_b1
        # 写入瓦片表到瓦片数据区(inadd)
        for i, td in enumerate(tdata_list):
            tile_data[inadd + i * 32:inadd + i * 32 + 32] = td

    def _write_256color_map(self, cfg: PicConfig, tile_data: bytearray, inadd: int,
                            map_data: bytearray, map_base: int) -> None:
        """256色MAP写入: 使用CRC16去重,生成瓦片表+map表。

        256色瓦片 = 8x8, 每像素1字节, 共64字节/瓦片。
        map entry: bits0-9=瓦片号, bit10=HFlip, bit11=VFlip, bits12-15=0(无调色板组)
        去重时支持翻转匹配, 减少瓦片数量。
        """
        pw, ph = cfg.pwidth, cfg.pheight
        pixel_w = pw * 8
        pixel_h = ph * 8
        pm = self.picmap

        tdata_list = []  # 瓦片数据(64字节/瓦片)
        crc_list = []
        map_entries = []  # (瓦片ID, 翻转标志)

        flip_variants = [
            (None, 0x00),
            (hflip_tile256, 0x04),
            (vflip_tile256, 0x08),
            (hvflip_tile256, 0x0C),
        ]

        for y in range(0, pixel_h, 8):
            for x in range(0, pixel_w, 8):
                tile = bytearray(64)
                for y1 in range(8):
                    for x1 in range(8):
                        tile[y1 * 8 + x1] = pm[x + x1][y + y1] & 0xFF
                tile = bytes(tile)

                found_id = -1
                found_flip = 0
                for flip_fn, flip_flag in flip_variants:
                    candidate = flip_fn(tile) if flip_fn else tile
                    crc = crc16(candidate)
                    for i, c in enumerate(crc_list):
                        if c == crc and tdata_list[i] == candidate:
                            found_id = i
                            found_flip = flip_flag
                            break
                    if found_id >= 0:
                        break

                if found_id >= 0:
                    map_entries.append((found_id, found_flip))
                else:
                    tdata_list.append(tile)
                    crc_list.append(crc16(tile))
                    map_entries.append((len(tdata_list) - 1, 0))

        # 校验瓦片数
        new_tile_count = len(tdata_list)
        _map_key = (cfg.tile_addr, cfg.pal_idx)
        orig_count = self._map_orig_tile_count.get(_map_key, 0)
        max_tiles = max(orig_count, 1)
        if new_tile_count > max_tiles:
            raise RuntimeError(
                f"瓦片数量超出: 导入后去重得到 {new_tile_count} 个瓦片, "
                f"但原始map区只使用了 {orig_count} 个瓦片。"
            )
        if new_tile_count > 1024:
            raise RuntimeError(
                f"瓦片数量超出GBA上限: 去重得到 {new_tile_count} 个瓦片, "
                f"GBA 256色MAP最多支持 1024 个瓦片。"
            )
        # 写入map表: 设置翻转标志(bits10-11), 256色无调色板组(bits12-15=0),
        # 更新瓦片号(bits0-9)
        for i, (tid, flip_flag) in enumerate(map_entries):
            new_b0 = tid & 0xFF
            new_b1 = flip_flag | ((tid >> 8) & 0x03)
            mi = map_base + i * 2
            if mi + 1 < len(map_data):
                map_data[mi] = new_b0
                map_data[mi + 1] = new_b1
        # 写入瓦片表 (64字节/瓦片)
        for i, td in enumerate(tdata_list):
            tile_data[inadd + i * 64:inadd + i * 64 + 64] = td

    # ---------- 导入 BMP (Command2) ----------

    def import_from_bmp(self, cfg: PicConfig, bmp_path: str, load_pal: bool = True) -> None:
        """从 BMP 导入到 picmap。对应 VB6 Command2_Click。"""
        from .bmp_io import import_bmp

        t = cfg.ptype
        if t == T_16COLOR_MAP:
            # 16色MAP: 固定8x8
            exp_w = cfg.pwidth * 8
            exp_h = cfg.pheight * 8
            exp_bpp = 4
        elif t in (T_16COLOR, T_PCE16_8):
            exp_w = cfg.pwidth * cfg.twidth * 8
            exp_h = cfg.pheight * cfg.theight * 8
            exp_bpp = 4
        elif t == T_PCE16:
            exp_w = cfg.pwidth * cfg.twidth * 16
            exp_h = cfg.pheight * cfg.theight * 16
            exp_bpp = 4
        elif t == T_16COLOR_BMP:
            exp_w = cfg.pwidth
            exp_h = cfg.pheight
            exp_bpp = 4
        elif t == T_256COLOR_MAP:
            exp_w = cfg.pwidth * 8
            exp_h = cfg.pheight * 8
            exp_bpp = 8
        elif t in (T_256COLOR, T_TRUE16):
            # 256色和超16色: 片宽×片高再乘8
            exp_w = cfg.pwidth * cfg.twidth * 8
            exp_h = cfg.pheight * cfg.theight * 8
            exp_bpp = 8
        elif t == T_256COLOR_BMP:
            exp_w = cfg.pwidth
            exp_h = cfg.pheight
            exp_bpp = 8
        else:
            exp_w = exp_h = exp_bpp = 0

        picmap, w, h, red, green, blue = import_bmp(bmp_path, exp_w, exp_h, exp_bpp, load_pal)
        self.picmap = picmap
        self.width = w
        self.height = h
        if load_pal:
            self.palette.red = red + [0] * (256 - len(red))
            self.palette.green = green + [0] * (256 - len(green))
            self.palette.blue = blue + [0] * (256 - len(blue))
        self.viewed = True

    # ---------- 导出 BMP (Command10) ----------

    def export_to_bmp(self, cfg: PicConfig, bmp_path: str) -> None:
        """导出 picmap 到 BMP。对应 VB6 Command10_Click。"""
        from .bmp_io import export_bmp

        t = cfg.ptype
        pal = self.palette
        if t in (T_16COLOR, T_PCE16, T_PCE16_8, T_16COLOR_BMP, T_16COLOR_MAP):
            is_256 = False
        else:
            is_256 = True

        export_bmp(bmp_path, self.picmap, self.width, self.height,
                   pal.red, pal.green, pal.blue, pal.alpha, cfg.colotype, is_256)

    def export_to_png(self, cfg: PicConfig, png_path: str, transparent_index: int = 0) -> None:
        """导出 picmap 到 PNG, 色号 transparent_index 对应像素 Alpha=0, 其余 Alpha=255。

        功能15: 用 Pillow RGBA 逐像素写入, 不依赖 BMP 路径。
        """
        try:
            from PIL import Image  # Pillow
        except Exception as e:
            raise RuntimeError(f"导出 PNG 需要 Pillow: {e}")

        w, h = self.width, self.height
        pal = self.palette
        if w <= 0 or h <= 0 or not self.picmap:
            raise RuntimeError("图片尚未加载,无法导出 PNG")
        idx0 = int(transparent_index) & 0xFF
        rgba = Image.new("RGBA", (w, h))
        put = rgba.putpixel
        pm = self.picmap
        for y in range(h):
            for x in range(w):
                try:
                    ci = pm[x][y] & 0xFF
                except Exception:
                    ci = 0
                r = pal.red[ci] if ci < len(pal.red) else 0
                g = pal.green[ci] if ci < len(pal.green) else 0
                b = pal.blue[ci] if ci < len(pal.blue) else 0
                a = 0 if ci == idx0 else 255
                put((x, y), (r & 0xFF, g & 0xFF, b & 0xFF, a))
        rgba.save(png_path, format="PNG")

    # ---------- 超16色校验 (checksuper16) ----------

    def check_super16(self, x: int, y: int) -> bool:
        """检查 8x8 区域是否所有像素属于同一调色板组(16色组)。
        返回 True 表示不满足超16色约束。对应 VB6 checksuper16。"""
        if x * 8 >= self.width or y * 8 >= self.height:
            return True
        ls = self.picmap[x * 8][y * 8] // 16
        for i in range(8):
            for j in range(8):
                px, py = x * 8 + i, y * 8 + j
                if px >= self.width or py >= self.height:
                    return True
                if self.picmap[px][py] // 16 != ls:
                    return True
        return False


def _write_pal_inplace(data: bytearray, offset: int, pal: Palette, n_colors: int = 16) -> None:
    """把前 n_colors 色以 BGR555 写入 data 的 offset 位置 (0-based)。

    n_colors=16 写 32 字节(16色), n_colors=256 写 512 字节(256色)。
    """
    n = max(0, min(n_colors, len(pal.red), len(pal.green), len(pal.blue)))
    for i in range(n):
        h = (pal.blue[i] // 8) * 4 + pal.green[i] // 64
        l = ((pal.green[i] // 8) % 8) * 32 + pal.red[i] // 8
        data[offset + 2 * i] = l & 0xFF
        data[offset + 2 * i + 1] = h & 0xFF


def _write_pal16_inplace(data: bytearray, offset: int, pal: Palette) -> None:
    """向后兼容: 仅写前16色。新代码应直接用 _write_pal_inplace。"""
    _write_pal_inplace(data, offset, pal, 16)
