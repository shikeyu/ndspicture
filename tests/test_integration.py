# -*- coding: utf-8 -*-
"""端到端集成测试: 验证 PictureEditor 的 show/write_back 完整流程。"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.palette import Palette
from src.core import tiles
from src.core.picture_editor import PictureEditor, PicConfig


def make_synthetic_rom(path: str):
    """构造一个合成 ROM: 16色瓦片数据 + 调色板(BGR555)。"""
    # 8x8 个瓦片, 每个 8x8 像素, 16色
    # 瓦片数据: 32字节/瓦片 × 64 = 2048字节
    # 调色板: 16色 × 2字节 = 32字节
    # 布局: 偏移0 = 瓦片数据, 偏移2048 = 调色板
    W, H = 64, 64
    original = [[((x // 8) * 8 + (y // 8)) & 0x0F for y in range(H)] for x in range(W)]
    data = bytearray(2048 + 32)
    for ty in range(8):
        for tx in range(8):
            tiles.write_tit16(data, 0, ty * 8 + tx, tx * 8, ty * 8, original)
    # 调色板: 16色,每色r=g=b=i, BGR555编码
    for i in range(16):
        r = g = b = i * 16
        h = (b // 8) * 4 + g // 64
        l = ((g // 8) % 8) * 32 + r // 8
        data[2048 + 2 * i] = l
        data[2048 + 2 * i + 1] = h
    with open(path, "wb") as f:
        f.write(bytes(data))
    return original


def test_show_16color():
    """测试 16色 show 流程。"""
    print("== 16色 show 测试 ==")
    tmp = tempfile.gettempdir()
    rom = os.path.join(tmp, "synth_rom.nds")
    original = make_synthetic_rom(rom)

    editor = PictureEditor()
    cfg = PicConfig(
        rom_path=rom,
        ptype="16色",
        colotype="RGB 2BIT",
        palette_addr="2048",  # 0-based偏移
        tile_addr="0",
        pwidth=8, pheight=8,
        twidth=1, theight=1,
    )
    editor.show(cfg)
    assert editor.viewed
    assert editor.width == 64 and editor.height == 64
    assert editor.picmap == original, "16色 show 后 picmap 不符"
    print(f"  显示 {editor.width}×{editor.height}: OK")

    # 测试导出 BMP
    bmp_path = os.path.join(tmp, "synth_out.bmp")
    editor.export_to_bmp(cfg, bmp_path)
    assert os.path.exists(bmp_path)
    print("  导出 BMP: OK")

    # 测试 write_back (写回新 ROM)
    rom2 = os.path.join(tmp, "synth_rom2.nds")
    with open(rom, "rb") as f:
        rom_data = bytearray(f.read())
    with open(rom2, "wb") as f:
        f.write(bytes(rom_data))
    cfg2 = PicConfig(
        rom_path=rom2,
        ptype="16色",
        colotype="RGB 2BIT",
        palette_addr="2048",
        tile_addr="0",
        pwidth=8, pheight=8,
        twidth=1, theight=1,
    )
    # 修改 picmap 一些像素
    for x in range(8):
        for y in range(8):
            editor.picmap[x][y] = 5
    msg = editor.write_back(cfg2)
    print(f"  write_back: {msg}")

    # 重新读取验证
    editor2 = PictureEditor()
    editor2.show(cfg2)
    for x in range(8):
        for y in range(8):
            assert editor2.picmap[x][y] == 5, f"({x},{y})={editor2.picmap[x][y]} != 5"
    print("  写回后重新读取: OK")


def test_show_256color():
    """测试 256色 show 流程。"""
    print("== 256色 show 测试 ==")
    tmp = tempfile.gettempdir()
    rom = os.path.join(tmp, "synth_rom256.nds")
    # 8x8 个瓦片, 256色, 64字节/瓦片
    W, H = 64, 64
    original = [[((x + y) % 256) for y in range(H)] for x in range(W)]
    data = bytearray(64 * 64 + 1024)  # 瓦片 + 256色调色板
    for ty in range(8):
        for tx in range(8):
            tiles.write_tit256(data, 0, ty * 8 + tx, tx * 8, ty * 8, original)
    for i in range(256):
        data[64 * 64 + i * 4] = i  # R
        data[64 * 64 + i * 4 + 1] = (i * 2) & 0xFF  # G
        data[64 * 64 + i * 4 + 2] = (i * 3) & 0xFF  # B
    with open(rom, "wb") as f:
        f.write(bytes(data))

    editor = PictureEditor()
    cfg = PicConfig(
        rom_path=rom,
        ptype="256色",
        colotype="RGBA 4BIT",
        palette_addr=str(64 * 64),
        tile_addr="0",
        pwidth=8, pheight=8,
    )
    editor.show(cfg)
    assert editor.width == 64 and editor.height == 64
    assert editor.picmap == original, "256色 show 后 picmap 不符"
    print(f"  显示 {editor.width}×{editor.height}: OK")


def test_show_pce16():
    """测试 PCE16色 show 流程。"""
    print("== PCE16色 show 测试 ==")
    tmp = tempfile.gettempdir()
    rom = os.path.join(tmp, "synth_pce.nds")
    # 4x4 个 16x16 瓦片, PCE16色
    W, H = 64, 64
    original = [[((x + y) & 0x0F) for y in range(H)] for x in range(W)]
    data = bytearray(128 * 16)  # 16个瓦片 × 128字节
    for ty in range(4):
        for tx in range(4):
            tiles.write_pce16(data, 0, ty * 4 + tx, tx * 16, ty * 16, original)
    # 调色板
    pal_data = bytearray(32)
    for i in range(16):
        pal_data[2 * i] = i * 8
        pal_data[2 * i + 1] = 0
    data += pal_data
    pal_off = 128 * 16
    with open(rom, "wb") as f:
        f.write(bytes(data))

    editor = PictureEditor()
    cfg = PicConfig(
        rom_path=rom,
        ptype="PCE16色",
        colotype="RGB 2BIT",
        palette_addr=str(pal_off),
        tile_addr="0",
        pwidth=4, pheight=4,
        twidth=1, theight=1,
    )
    editor.show(cfg)
    assert editor.width == 64 and editor.height == 64
    assert editor.picmap == original, "PCE16 show 后 picmap 不符"
    print(f"  显示 {editor.width}×{editor.height}: OK")


def test_compressed_show():
    """测试压缩数据的 show 流程。"""
    print("== 压缩 show 测试 ==")
    from src.core.compression import comp_bytes
    tmp = tempfile.gettempdir()
    rom = os.path.join(tmp, "synth_comp.nds")

    # 构造瓦片数据并压缩
    W, H = 64, 64
    original = [[((x // 8) + (y // 8)) & 0x0F for y in range(H)] for x in range(W)]
    tile_data = bytearray(2048)
    for ty in range(8):
        for tx in range(8):
            tiles.write_tit16(tile_data, 0, ty * 8 + tx, tx * 8, ty * 8, original)
    comp = comp_bytes(bytes(tile_data))
    # ROM 布局: 偏移0x100 = 压缩瓦片数据, 偏移0x800 = 调色板
    rom_data = bytearray(0x1000)
    rom_data[0x100:0x100 + len(comp)] = comp
    for i in range(16):
        rom_data[0x800 + 2 * i] = i * 8
        rom_data[0x800 + 2 * i + 1] = 0
    with open(rom, "wb") as f:
        f.write(bytes(rom_data))

    editor = PictureEditor()
    cfg = PicConfig(
        rom_path=rom,
        ptype="16色",
        colotype="RGB 2BIT",
        palette_addr="0x800",
        tile_addr="0x100;0",  # 压缩数据在0x100, 解压后偏移0
        pwidth=8, pheight=8,
        twidth=1, theight=1,
    )
    editor.show(cfg)
    assert editor.picmap == original, f"压缩 show 后 picmap 不符"
    print(f"  压缩数据 show {editor.width}×{editor.height}: OK")


if __name__ == "__main__":
    test_show_16color()
    test_show_256color()
    test_show_pce16()
    test_compressed_show()
    print("\n所有集成测试通过!")
