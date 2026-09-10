# -*- coding: utf-8 -*-
"""GPP 项目文件模块(兼容 VB6 版 Var 1.0 / 2.0 / 3.0)。

移植自 VB6 ndspicture.frm 中的 Command4(读取) / Command5(保存)。

文件格式:
  第1行: "Var X.0" (1.0/2.0/3.0)
  第2行: ROM 源文件路径
  后续每2行一个图片项:
    行1(Var1): soufname,title,palett,paleidx,batmap,ptype
    行1(Var2): + palepy
    行1(Var3): + palepy, colotype
    行2: twidth,theight,pwidth,pheight,bmpfname

VB6 用 Input #1, a, b, c, ... 按逗号分隔读取,字符串字段可不带引号。
为兼容,读取时按逗号分隔并处理可选双引号;保存时按 VB6 风格逗号连接(不加引号)。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
import os


@dataclass
class PicItem:
    """对应 VB6 ndspic 结构体。"""
    soufname: str = ""        # ROM 源文件路径
    title: str = ""           # 标题(末尾带*为删除标记)
    palett: str = "0"         # 调色板地址(字符串,可能 "cmpadd;paladd" 表示压缩)
    paleidx: str = "0"        # 调色板索引(字符串,可能 "cmpadd;idx" 表示压缩)
    palepy: int = 0           # 调色板偏移
    batmap: str = "0"         # 瓦片/位图地址(字符串,可能 "cmpadd;bmpadd")
    ptype: str = "16色"       # 图片类型
    twidth: int = 1           # 瓦片宽(横向瓦片数,8或16的倍数因子)
    theight: int = 1          # 瓦片高
    pwidth: int = 1           # 图片宽(瓦片数)
    pheight: int = 1          # 图片高(瓦片数)
    bmpfname: str = ""        # 对应BMP文件名
    colotype: str = "RGB 2BIT"  # 颜色类型


def _split_csv(line: str) -> List[str]:
    """按逗号分隔,处理可选的双引号字段(与 VB6 Input # 兼容)。"""
    fields = []
    i = 0
    n = len(line)
    while i < n:
        if line[i] == '"':
            # 引号字符串,内部双引号转义为""
            j = i + 1
            buf = []
            while j < n:
                if line[j] == '"':
                    if j + 1 < n and line[j + 1] == '"':
                        buf.append('"')
                        j += 2
                    else:
                        j += 1
                        break
                else:
                    buf.append(line[j])
                    j += 1
            fields.append("".join(buf))
            # 跳过到下一个逗号
            while j < n and line[j] != ',':
                j += 1
            if j < n and line[j] == ',':
                j += 1
            i = j
        else:
            # 无引号,到逗号或行尾
            j = line.find(',', i)
            if j == -1:
                fields.append(line[i:].strip())
                i = n
            else:
                fields.append(line[i:j].strip())
                i = j + 1
    return fields


def load_gpp(path: str) -> tuple:
    """读取 GPP 项目文件,返回 (rom_path, [PicItem, ...])。

    编码: VB6 在中文 Windows 下生成的是 GBK(ANSI)编码,这里优先用 GBK 读取,
    失败时回退 UTF-8,以保证中文标题不乱码。
    """
    try:
        with open(path, "r", encoding="gbk") as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    lines = content.splitlines()

    idx = 0
    # 第一行: 版本
    ver = 1
    first = lines[idx].strip() if lines else ""
    if first == "Var 2.0":
        ver = 2
        idx += 1
    elif first == "Var 3.0":
        ver = 3
        idx += 1
    elif first.startswith("Var"):
        # 其他版本按1.0处理
        idx += 1
    # 第二行: ROM 路径
    rom_path = lines[idx].strip() if idx < len(lines) else ""
    idx += 1

    items: List[PicItem] = []
    while idx < len(lines):
        line1 = lines[idx].rstrip("\r")
        idx += 1
        if not line1.strip():
            continue
        if idx >= len(lines):
            break
        line2 = lines[idx].rstrip("\r")
        idx += 1

        f1 = _split_csv(line1)
        f2 = _split_csv(line2)
        item = PicItem()
        if ver == 1:
            # soufname,title,palett,paleidx,batmap,ptype
            if len(f1) < 6:
                continue
            item.soufname = f1[0]
            item.title = f1[1]
            item.palett = f1[2]
            item.paleidx = f1[3]
            item.batmap = f1[4]
            item.ptype = f1[5]
            item.colotype = "RGB 2BIT"
        elif ver == 2:
            # soufname,title,palett,paleidx,palepy,batmap,ptype
            if len(f1) < 7:
                continue
            item.soufname = f1[0]
            item.title = f1[1]
            item.palett = f1[2]
            item.paleidx = f1[3]
            try:
                item.palepy = int(f1[4])
            except ValueError:
                item.palepy = 0
            item.batmap = f1[5]
            item.ptype = f1[6]
            item.colotype = "RGB 2BIT"
        else:  # ver == 3
            # soufname,title,palett,paleidx,palepy,batmap,ptype,colotype
            if len(f1) < 8:
                continue
            item.soufname = f1[0]
            item.title = f1[1]
            item.palett = f1[2]
            item.paleidx = f1[3]
            try:
                item.palepy = int(f1[4])
            except ValueError:
                item.palepy = 0
            item.batmap = f1[5]
            item.ptype = f1[6]
            item.colotype = f1[7]
        # 第二行: twidth,theight,pwidth,pheight,bmpfname
        if len(f2) >= 5:
            try:
                item.twidth = int(f2[0])
            except ValueError:
                item.twidth = 1
            try:
                item.theight = int(f2[1])
            except ValueError:
                item.theight = 1
            try:
                item.pwidth = int(f2[2])
            except ValueError:
                item.pwidth = 1
            try:
                item.pheight = int(f2[3])
            except ValueError:
                item.pheight = 1
            item.bmpfname = f2[4]
        # 兼容旧版: "真16色" -> "超16色"
        if item.ptype == "真16色":
            item.ptype = "超16色"
        items.append(item)
    return rom_path, items


def save_gpp(path: str, rom_path: str, items: List[PicItem]) -> None:
    """保存为 GPP 项目文件(Var 3.0 格式,与 VB6 兼容)。

    跳过 title 以 "*" 结尾的项(VB6 Command5 中 Right(title,1)<>"*" 才写入)。
    """
    lines = []
    lines.append("Var 3.0")
    lines.append(rom_path)
    for it in items:
        if it.title.endswith("*"):
            continue
        line1 = ",".join([
            it.soufname, it.title, it.palett, str(it.paleidx),
            str(it.palepy), it.batmap, it.ptype, it.colotype,
        ])
        line2 = ",".join([
            str(it.twidth), str(it.theight), str(it.pwidth),
            str(it.pheight), it.bmpfname,
        ])
        lines.append(line1)
        lines.append(line2)
    # 编码: 用 GBK 保存,与 VB6 中文 Windows 兼容;若含非 GBK 字符则回退 UTF-8
    try:
        with open(path, "w", encoding="gbk", newline="") as f:
            f.write("\n".join(lines) + "\n")
    except UnicodeEncodeError:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines) + "\n")
