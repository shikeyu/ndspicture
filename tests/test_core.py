# -*- coding: utf-8 -*-
"""核心功能往返测试。

验证瓦片 view/write 往返一致、BMP 导入导出往返一致、GPP 项目读写一致。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import tiles
from src.core.palette import Palette, dat2rgb
from src.core.compression import comp_bytes, uncomp_from_bytes
from src.core.bmp_io import export_bmp, import_bmp
from src.core.gpp_project import PicItem, load_gpp, save_gpp
from src.core.crc16 import crc16
from src.core.picture_editor import PictureEditor, PicConfig, parse_addr


def test_tiles_roundtrip():
    """测试所有瓦片格式的 view/write 往返一致。"""
    print("== 瓦片往返测试 ==")
    # 准备一个 256x256 的 picmap
    W, H = 64, 64
    original = [[(x + y) & 0x0F for y in range(H)] for x in range(W)]

    # 16色瓦片
    data = bytearray(32 * 8 * 8)
    for ty in range(8):
        for tx in range(8):
            tiles.write_tit16(data, 0, ty * 8 + tx, tx * 8, ty * 8, original)
    picmap2 = [[0] * H for _ in range(W)]
    for ty in range(8):
        for tx in range(8):
            tiles.view_tit16(bytes(data), 0, ty * 8 + tx, tx * 8, ty * 8, picmap2)
    assert picmap2 == original, "16色瓦片往返失败"
    print("  16色瓦片: OK")

    # 256色瓦片
    data = bytearray(64 * 8 * 8)
    for ty in range(8):
        for tx in range(8):
            tiles.write_tit256(data, 0, ty * 8 + tx, tx * 8, ty * 8, original)
    picmap2 = [[0] * H for _ in range(W)]
    for ty in range(8):
        for tx in range(8):
            tiles.view_tit256(bytes(data), 0, ty * 8 + tx, tx * 8, ty * 8, picmap2)
    # 256色只有低4位,但我们的数据<16,所以一致
    assert picmap2 == original, "256色瓦片往返失败"
    print("  256色瓦片: OK")

    # PCE16 16x16
    data = bytearray(128 * 4 * 4)
    for ty in range(4):
        for tx in range(4):
            tiles.write_pce16(data, 0, ty * 4 + tx, tx * 16, ty * 16, original)
    picmap2 = [[0] * H for _ in range(W)]
    for ty in range(4):
        for tx in range(4):
            tiles.view_pce16(bytes(data), 0, ty * 4 + tx, tx * 16, ty * 16, picmap2)
    assert picmap2 == original, "PCE16 16x16往返失败"
    print("  PCE16 16x16: OK")

    # PCE16 8x8
    data = bytearray(32 * 8 * 8)
    for ty in range(8):
        for tx in range(8):
            tiles.write_pce16_8(data, 0, ty * 8 + tx, tx * 8, ty * 8, original)
    picmap2 = [[0] * H for _ in range(W)]
    for ty in range(8):
        for tx in range(8):
            tiles.view_pce16_8(bytes(data), 0, ty * 8 + tx, tx * 8, ty * 8, picmap2)
    assert picmap2 == original, "PCE16 8x8往返失败"
    print("  PCE16 8x8: OK")

    # 真16色
    data = bytearray(32 * 8 * 8 + 64)  # 瓦片数据 + 调色板基址
    pelidx = 32 * 8 * 8
    for ty in range(8):
        for tx in range(8):
            tiles.write_tit16_2(data, 0, pelidx, ty * 8 + tx, tx * 8, ty * 8, original)
    picmap2 = [[0] * H for _ in range(W)]
    for ty in range(8):
        for tx in range(8):
            tiles.view_tit16_2(bytes(data), 0, pelidx, ty * 8 + tx, tx * 8, ty * 8, picmap2)
    assert picmap2 == original, "真16色往返失败"
    print("  真16色: OK")


def test_compression():
    """测试 LZ77 压缩往返。"""
    print("== 压缩往返测试 ==")
    # 重复模式(高压缩率)
    data = (b"ABCDEFG" * 100) + bytes(range(256))
    comp = comp_bytes(data)
    dec = uncomp_from_bytes(comp, 0)
    assert dec == data, "压缩往返失败"
    print(f"  原始 {len(data)} -> 压缩 {len(comp)} -> 解压 {len(dec)}: OK")

    # 随机数据(低压缩率)
    import random
    random.seed(42)
    data = bytes(random.randint(0, 255) for _ in range(1000))
    comp = comp_bytes(data)
    dec = uncomp_from_bytes(comp, 0)
    assert dec == data, "随机数据压缩往返失败"
    print(f"  随机数据 {len(data)} -> {len(comp)} -> {len(dec)}: OK")


def test_bmp_roundtrip():
    """测试 BMP 导入导出往返。"""
    print("== BMP 往返测试 ==")
    W, H = 32, 32
    picmap = [[(x * 8 + y) & 0xFF for y in range(H)] for x in range(W)]
    pal = Palette()
    for i in range(256):
        pal.red[i] = i
        pal.green[i] = (i * 2) & 0xFF
        pal.blue[i] = (i * 3) & 0xFF

    tmp = tempfile.gettempdir()
    path = os.path.join(tmp, "test_256.bmp")
    export_bmp(path, picmap, W, H, pal.red, pal.green, pal.blue, pal.alpha, "RGB 2BIT", is_256=True)
    pm2, w, h, r, g, b = import_bmp(path, W, H, 8, load_palette=True)
    assert w == W and h == H
    assert pm2 == picmap, "256色BMP往返失败"
    print("  256色BMP: OK")

    # 16色
    picmap16 = [[(x + y) & 0x0F for y in range(H)] for x in range(W)]
    path16 = os.path.join(tmp, "test_16.bmp")
    export_bmp(path16, picmap16, W, H, pal.red, pal.green, pal.blue, pal.alpha, "RGB 2BIT", is_256=False)
    pm2, w, h, r, g, b = import_bmp(path16, W, H, 4, load_palette=True)
    assert pm2 == picmap16, "16色BMP往返失败"
    print("  16色BMP: OK")


def test_gpp_roundtrip():
    """测试 GPP 项目文件往返。"""
    print("== GPP 往返测试 ==")
    items = [
        PicItem(soufname="c:/test/game.nds", title="标题1", palett="0x100",
                paleidx="0", palepy=5, batmap="0x200;0x10", ptype="16色",
                twidth=2, theight=2, pwidth=4, pheight=4,
                bmpfname="c:/test/title1.bmp", colotype="RGB 2BIT"),
        PicItem(soufname="c:/test/game.nds", title="标题2", palett="256",
                paleidx="100;200", palepy=0, batmap="0x300", ptype="256色",
                twidth=1, theight=1, pwidth=8, pheight=8,
                bmpfname="c:/test/title2.bmp", colotype="RGBA 4BIT"),
    ]
    tmp = tempfile.gettempdir()
    path = os.path.join(tmp, "test.gpp")
    save_gpp(path, "c:/test/game.nds", items)
    rom, loaded = load_gpp(path)

    assert rom == "c:/test/game.nds", f"ROM路径不符: {rom}"
    assert len(loaded) == 2, f"项目数不符: {len(loaded)}"
    a = loaded[0]
    assert a.soufname == "c:/test/game.nds"
    assert a.title == "标题1"
    assert a.palett == "0x100"
    assert a.palepy == 5
    assert a.batmap == "0x200;0x10", f"batmap: {a.batmap}"
    assert a.ptype == "16色"
    assert a.colotype == "RGB 2BIT"
    assert a.twidth == 2 and a.theight == 2
    assert a.pwidth == 4 and a.pheight == 4
    b = loaded[1]
    assert b.paleidx == "100;200"
    assert b.ptype == "256色"
    assert b.colotype == "RGBA 4BIT"
    print("  Var 3.0 读写: OK")


def test_crc16():
    """测试 CRC16。"""
    print("== CRC16 测试 ==")
    # 标准测试向量
    assert crc16(b"123456789") == "7BF6", f"crc16(123456789) 应为 7BF6, 实际 {crc16(b'123456789')}"
    print("  crc16('123456789') = 7BF6: OK")


def test_dat2rgb():
    """测试 GBA BGR555 颜色转换。"""
    print("== 颜色转换测试 ==")
    # 黑色: hdat=0, ldat=0 -> (0,0,0)
    assert dat2rgb(0, 0) == (0, 0, 0)
    # 红色: BGR555中 R=31(满), 即 hdat bit0-4 = 31
    # dat2rgb: r = (hdat % 32) * 8, 所以 hdat=31 -> r=248
    r, g, b = dat2rgb(31, 0)
    assert r == 248 and g == 0 and b == 0, f"红色: {(r,g,b)}"
    # 绿色: g = (hdat//32 + ldat%4*8)*8, hdat=0,ldat=1 -> g=(0+8)*8=64? 不对
    # ldat%4 = 1, *8 = 8, *8 = 64. 但BGR555绿色5位满值是31*8=248
    # hdat//32=0, ldat%4=1 -> 0+8=8, *8=64. 64 != 248
    # 实际上绿色位分布: G高3位在ldat的低3位(bit0-2), G低2位在hdat的高2位(bit5-6)
    # ldat=0b000001(1), hdat=0b000000(0): G = (0 + 1*8) = 8, *8 = 64
    # 这对应绿色5位值 = 0b01000(8)? 不对,BGR555绿色5位
    # 让我重新看VB6: nowgreen = (Int(hdat/32) + (ldat Mod 4)*8) * 8
    # hdat bit5-7 是G的高3位? hdat/32 = hdat>>5, 即bit5-7 (3位)
    # ldat Mod 4 = ldat & 3, 即bit0-1 (2位)
    # 所以G = (hdat>>5) | ((ldat&3)<<3) = 5位, 然后*8扩展到8位
    # 满值: hdat>>5=7, ldat&3=3, G=7+24=31, *8=248 ✓
    r, g, b = dat2rgb(0, 3)  # hdat=0, ldat=0b11 -> G低2位=3 -> G=3<<3=24, *8=192
    assert g == 192, f"绿色(0,3): {(r,g,b)}"
    assert r == 0 and b == 0
    # 蓝色: b = (Int(ldat/4) Mod 32) * 8, ldat bit2-6 (5位)
    # ldat=0b11100(28), ldat/4=7, 7&31=7, *8=56
    r, g, b = dat2rgb(0, 28)
    assert b == 56, f"蓝色(0,28): {(r,g,b)}"
    print("  dat2rgb: OK")


def test_parse_addr():
    print("== 地址解析测试 ==")
    # RAW
    pa = parse_addr("0x100")
    assert pa.comp_kind is None and pa.data_offset == 256
    pa = parse_addr("256")
    assert pa.comp_kind is None and pa.data_offset == 256
    # LZ77
    pa = parse_addr("100;200")
    assert pa.comp_kind == "lz77" and pa.cmp_offset == 100 and pa.data_offset == 200
    pa = parse_addr("0x10;0x20")
    assert pa.comp_kind == "lz77" and pa.cmp_offset == 16 and pa.data_offset == 32
    # RLE
    pa = parse_addr("0x0A1840!0x0064")
    assert pa.comp_kind == "rle" and pa.cmp_offset == 0x0A1840 and pa.data_offset == 0x0064
    # Huffman
    pa = parse_addr("0xBB072C:0x0064")
    assert pa.comp_kind == "huff" and pa.cmp_offset == 0xBB072C and pa.data_offset == 0x0064
    # Huff+LZ77
    pa = parse_addr("0xBB072C:0xBB072C;0x4064")
    assert pa.comp_kind == "huff_lz77"
    assert pa.huff_offset == 0xBB072C
    assert pa.cmp_offset == 0xBB072C  # LZ77 块在 Huffman 流内偏移
    assert pa.data_offset == 0x4064
    # 空
    pa = parse_addr("")
    assert pa.comp_kind is None and pa.data_offset == 0
    # 向后兼容解包
    cmp, off = parse_addr("100;200")
    assert cmp == 100 and off == 200
    print("  parse_addr: OK")


if __name__ == "__main__":
    test_crc16()
    test_dat2rgb()
    test_parse_addr()
    test_compression()
    test_tiles_roundtrip()
    test_bmp_roundtrip()
    test_gpp_roundtrip()
    print("\n所有测试通过!")
