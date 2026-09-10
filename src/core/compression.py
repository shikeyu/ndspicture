# -*- coding: utf-8 -*-
"""GBA/NDS 压缩/解压模块。

支持格式:
  - LZ77 (0x10 头): 解压 + 压缩
  - RLE  (0x30 头): 解压 + 压缩
  - Huffman (0x24/0x28 头): 解压 + 压缩 + 块大小
  - Huff+LZ77: 解压 + 压缩

LZ77 数据格式:
  - 头部: 1字节(0x10) + 3字节长度(小端)
  - 压缩流: 每8个单元一组,先1个标志字节,再8个单元
      标志位=0: 1字节字面量
      标志位=1: 2字节, byte1=((len-3)<<4)|((offset-1)>>8), byte2=(offset-1)&0xFF
                  len ∈ [3,18], offset ∈ [1,4096]
  - 末尾用0填充到4字节对齐

RLE 数据格式 (0x30 头):
  - 头部: 1字节(0x30) + 3字节长度(小端)
  - 压缩流: 每段以1字节flag开头
      bit7=0: 字面量段, bits0-6=N, 后跟 N+1 个字面字节
      bit7=1: 重复段, bits0-6=N, 后跟1字节重复值 (重复 N+1 次)
  - 末尾4字节对齐

Huffman 数据格式 (GBA BIOS 风格, 4字节头):
  - 头部: 4字节小端u32
      bits 0-3: 每符号位数 (4=4bit, 8=8bit, 0视为8)
      bits 4-7: 标识位 (0x20 系列, 如 0x24=4bit, 0x28=8bit)
      bits 8-31: 解压后字节数 (24-bit)
  - 树大小: 1字节, treeSize = (byte << 1) + 1
  - 树数据: treeSize 字节, 每节点1字节
      bit 7: LTerm (1=左子是叶子)
      bit 6: RTerm (1=右子是叶子)
      bits 0-5: Offset (子节点对相对偏移, 2字节单位)
      子节点地址: next = (nPointer & ~1) + Offset*2 + 2
      左子在 next, 右子在 next+1
      叶子值直接存储在子节点位置 (1字节)
      内部节点描述字节存储在子节点位置
  - 位流: MSB先, 每次读32位u32, bit=0走左子, bit=1走右子
  - 输出: 每符号读 bits 位, 累积到32位后输出4字节
  - 末尾4字节对齐
"""

from __future__ import annotations
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .picture_editor import ParsedAddr


# ---------- LZ77 解压 ----------

def uncomp_from_bytes(raw: bytes, beg: int) -> bytes:
    """从字节流 raw 的 beg 位置开始解压 LZ77,返回解压后的字节。"""
    oldl = raw[beg + 1] | (raw[beg + 2] << 8) | (raw[beg + 3] << 16)
    out = bytearray()
    p = beg + 4
    n = len(raw)
    while len(out) < oldl:
        if p >= n:
            break
        flag = raw[p]
        p += 1
        for bit in range(8):
            if len(out) >= oldl:
                break
            if flag & (1 << (7 - bit)):
                if p + 1 >= n:
                    raise ValueError("解压数据错误: 压缩块不完整")
                b1 = raw[p]
                b2 = raw[p + 1]
                p += 2
                length = (b1 >> 4) + 3
                offset = ((b1 & 0x0F) << 8) | b2
                src = len(out) - offset - 1
                if src < 0:
                    raise ValueError("解压数据错误: 偏移超出范围")
                for t in range(length):
                    out.append(out[src + (t % (offset + 1))])
            else:
                if p >= n:
                    raise ValueError("解压数据错误: 字面量不完整")
                out.append(raw[p])
                p += 1
    return bytes(out)


def uncomp_file(in_path: str, out_path: str, beg: int) -> None:
    """从 in_path 的 beg 位置解压 LZ77 到 out_path(覆盖)。"""
    with open(in_path, "rb") as f:
        raw = f.read()
    data = uncomp_from_bytes(raw, beg)
    with open(out_path, "wb") as f:
        f.write(data)


def comp_block_size(raw: bytes, beg: int) -> int:
    """计算从 beg 开始的 LZ77 压缩块占用的字节数(含4字节头,已4字节对齐)。"""
    oldl = raw[beg + 1] | (raw[beg + 2] << 8) | (raw[beg + 3] << 16)
    p = beg + 4
    n = len(raw)
    out_len = 0
    while out_len < oldl and p < n:
        flag = raw[p]
        p += 1
        for bit in range(8):
            if out_len >= oldl:
                break
            if flag & (1 << (7 - bit)):
                if p + 1 >= n:
                    break
                length = (raw[p] >> 4) + 3
                out_len += length
                p += 2
            else:
                out_len += 1
                p += 1
    comp_len = p - beg
    while comp_len % 4 != 0:
        comp_len += 1
    return comp_len


# ---------- LZ77 压缩 ----------

def comp_bytes(data: bytes) -> bytes:
    """压缩字节序列,返回完整的 LZ77 压缩流(含头部)。

    使用 2 字节 hash + lazy matching 策略提升压缩率:
    - 2 字节 hash (65536 桶) 提高匹配精度
    - lazy matching: 若 i+1 处的匹配更长, 则 i 输出字面量, i+1 输出匹配
    """
    n = len(data)
    out = bytearray()
    out.append(0x10)
    out.append(n & 0xFF)
    out.append((n >> 8) & 0xFF)
    out.append((n >> 16) & 0xFF)

    if n < 2:
        # 数据太短, 直接字面量
        for i in range(n):
            out.append(data[i])
        while len(out) % 4 != 0:
            out.append(0)
        return bytes(out)

    # 2 字节 hash: key = data[i] | (data[i+1] << 8), 桶数 65536
    HASH_SIZE = 65536
    head = [-1] * HASH_SIZE
    prev = [-1] * n

    def _hash2(pos):
        """2 字节 hash, 末尾位置用 data[pos] | 0 (单字节)。"""
        if pos + 1 < n:
            return data[pos] | (data[pos + 1] << 8)
        return data[pos]

    MAX_CHAIN = 1024  # 搜索链最大深度, 平衡速度和压缩率

    def _find_match(i):
        """在位置 i 查找最长匹配, 返回 (length, offset) 或 (0, 0)。

        支持重叠匹配 (length > distance), 与 GBA BIOS 解压器兼容:
        解压公式 out[src + t % (offset+1)] 允许从已解压数据循环复制。
        但跳过 disp=0 (distance=1) 的重叠匹配, 因为部分 GBA 模拟器不支持。
        """
        if i + 3 > n:
            return 0, 0
        yb1 = min(i, 4096)
        yb2 = min(n - i, 18)
        if yb2 < 3:
            return 0, 0
        beg1 = i - yb1
        key = _hash2(i)
        cur = head[key] if key < HASH_SIZE else -1
        maxlen = 0
        maxbeg = -1
        chain = 0
        while cur >= 0 and cur >= beg1 and chain < MAX_CHAIN:
            # 允许重叠匹配: 不再限制 avail = min(yb2, i - cur)
            # 解压器使用 out[src + t % (offset+1)] 可正确处理重叠
            avail = yb2
            l = 0
            while l < avail and data[cur + l] == data[i + l]:
                l += 1
                if l == avail:
                    break
            off = i - cur - 1
            # 跳过 disp=0 (distance=1) 以兼容部分 GBA 模拟器
            if l > maxlen and off > 0:
                maxlen = l
                maxbeg = cur
                if maxlen == yb2:
                    break
            cur = prev[cur]
            chain += 1
        if maxlen >= 3:
            return maxlen, i - maxbeg - 1
        return 0, 0

    def _insert(pos):
        """将位置 pos 插入 hash 链。"""
        if pos + 1 < n:
            key = data[pos] | (data[pos + 1] << 8)
        else:
            key = data[pos]
        if key < HASH_SIZE:
            prev[pos] = head[key]
            head[key] = pos

    flag = 0
    units = []
    bit_count = 0

    def _emit_literal(byte_val):
        nonlocal flag, bit_count, units
        units.append(bytes([byte_val]))
        flag = (flag << 1) | 0
        bit_count += 1
        if bit_count == 8:
            out.append(flag)
            for u in units:
                out.extend(u)
            flag = 0
            units = []
            bit_count = 0

    def _emit_match(length, offset):
        nonlocal flag, bit_count, units
        b1 = ((length - 3) << 4) | ((offset >> 8) & 0x0F)
        b2 = offset & 0xFF
        units.append(bytes([b1, b2]))
        flag = (flag << 1) | 1
        bit_count += 1
        if bit_count == 8:
            out.append(flag)
            for u in units:
                out.extend(u)
            flag = 0
            units = []
            bit_count = 0

    i = 0
    while i < n:
        ml, mo = _find_match(i)
        if ml >= 3:
            # lazy matching: 检查 i+1 处是否有更长匹配
            did_lazy_insert = False
            if i + 1 < n:
                # 先插入 i 位置 (因为 _find_match(i+1) 需要 hash 表已包含 i)
                _insert(i)
                did_lazy_insert = True
                ml2, mo2 = _find_match(i + 1)
                if ml2 > ml:
                    # i 输出字面量, i+1 输出匹配
                    _emit_literal(data[i])
                    _emit_match(ml2, mo2)
                    # 插入匹配范围内的所有位置 (i+1 到 i+ml2)
                    for t in range(0, ml2):
                        _insert(i + 1 + t)
                    i += 1 + ml2
                    continue
            # 正常匹配: 插入匹配范围内所有位置
            # (若 did_lazy_insert 则 i 已插入, 跳过; 否则从头插入)
            start_t = 1 if did_lazy_insert else 0
            for t in range(start_t, ml):
                _insert(i + t)
            _emit_match(ml, mo)
            i += ml
        else:
            _emit_literal(data[i])
            _insert(i)
            i += 1

    if bit_count > 0:
        flag = flag << (8 - bit_count)
        out.append(flag)
        for u in units:
            out.extend(u)
    while len(out) % 4 != 0:
        out.append(0)
    return bytes(out)


def comp_file(in_path: str, out_path: str, beg: int) -> None:
    """LZ77 压缩 in_path 整个文件到 out_path。"""
    with open(in_path, "rb") as f:
        data = f.read()
    comp_data = comp_bytes(data)
    with open(out_path, "wb") as f:
        f.write(comp_data)


# ---------- RLE (0x30) ----------

def uncomp_rle_from_bytes(raw: bytes, beg: int) -> bytes:
    """从 raw[beg] 开始的 RLE (0x30) 块解压。beg 指向 0x30 标识字节。"""
    if beg + 4 > len(raw):
        raise ValueError("RLE 解压错误: 块头不完整")
    if raw[beg] != 0x30:
        raise ValueError(f"RLE 解压错误: 头部标识错误 0x{raw[beg]:02X} (期望 0x30)")
    oldl = raw[beg + 1] | (raw[beg + 2] << 8) | (raw[beg + 3] << 16)
    out = bytearray()
    p = beg + 4
    n = len(raw)
    while len(out) < oldl:
        if p >= n:
            raise ValueError("RLE 解压错误: 数据不完整")
        flag = raw[p]
        p += 1
        count = (flag & 0x7F) + 1
        if flag & 0x80:
            # 重复段: 1字节重复值
            if p >= n:
                raise ValueError("RLE 解压错误: 重复值缺失")
            val = raw[p]
            p += 1
            out.extend(bytes([val]) * count)
        else:
            # 字面量段: count 个字节
            if p + count > n:
                raise ValueError("RLE 解压错误: 字面量不完整")
            out.extend(raw[p:p + count])
            p += count
    return bytes(out)


def rle_block_size(raw: bytes, beg: int) -> int:
    """计算 RLE 块占用的字节数(含4字节头,已4字节对齐)。"""
    oldl = raw[beg + 1] | (raw[beg + 2] << 8) | (raw[beg + 3] << 16)
    p = beg + 4
    n = len(raw)
    out_len = 0
    while out_len < oldl and p < n:
        flag = raw[p]
        p += 1
        count = (flag & 0x7F) + 1
        if flag & 0x80:
            p += 1
            out_len += count
        else:
            p += count
            out_len += count
    comp_len = p - beg
    while comp_len % 4 != 0:
        comp_len += 1
    return comp_len


def comp_rle_bytes(data: bytes) -> bytes:
    """RLE 压缩。贪婪扫描: 重复≥3 字节用 run-length, 否则字面量。

    字面量段最多 128 字节, 重复段长度 4-130 (count-1 存入 bits0-6, 范围 0-127+1=1-128,
    但 GBA 实际常用 3-130)。
    """
    n = len(data)
    out = bytearray()
    out.append(0x30)
    out.append(n & 0xFF)
    out.append((n >> 8) & 0xFF)
    out.append((n >> 16) & 0xFF)

    i = 0
    lit_start = 0  # 当前字面量段起点
    lit_len = 0

    def flush_literal(end: int) -> None:
        """把 [lit_start, end) 的字面量按 128 一段写入 out。"""
        nonlocal lit_len
        p = lit_start
        while p < end:
            chunk = min(128, end - p)
            out.append(chunk - 1)  # bit7=0, count-1
            out.extend(data[p:p + chunk])
            p += chunk
        lit_len = 0

    while i < n:
        # 查找从 i 开始的重复长度
        run_len = 1
        while (i + run_len < n
               and run_len < 130
               and data[i + run_len] == data[i]):
            run_len += 1
        if run_len >= 3:
            # 先把之前的字面量刷出
            if lit_len > 0:
                flush_literal(i)
                lit_start = i
            # 写入重复段
            rem = run_len
            while rem > 0:
                chunk = min(128, rem)
                out.append(0x80 | (chunk - 1))
                out.append(data[i])
                rem -= chunk
            i += run_len
            lit_start = i
        else:
            lit_len += 1
            i += 1
    # 末尾剩余字面量
    if lit_len > 0:
        flush_literal(n)
    # 4 字节对齐
    while len(out) % 4 != 0:
        out.append(0)
    return bytes(out)


# ---------- Huffman (GBA BIOS 4字节头) ----------

def _read_u32_le(b: bytes, off: int) -> int:
    return b[off] | (b[off + 1] << 8) | (b[off + 2] << 16) | (b[off + 3] << 24)


def _huff_decode_core(raw: bytes, beg: int) -> tuple:
    """Huffman 解码核心逻辑 (供 uncomp_huff_from_bytes 和 huff_block_size 共用)。

    返回 (out_bytes, bits_consumed)。out_bytes 为解压后字节序列;
    bits_consumed 为位流实际消耗的位数。
    """
    n = len(raw)
    if beg + 5 > n:
        raise ValueError("Huffman 解压错误: 块头不完整")

    header = _read_u32_le(raw, beg)
    bits = header & 0xF
    if bits == 0:
        bits = 8
    if bits == 1 or 32 % bits != 0:
        raise ValueError(f"Huffman 解压错误: 不支持的 bits 值 {bits}")
    remaining = header >> 8
    if remaining < 4 or remaining > 0x40000:
        raise ValueError(f"Huffman 解压错误: 解压长度 {remaining} 越界 (4..0x40000)")

    tree_size_byte = raw[beg + 4]
    tree_size = (tree_size_byte << 1) + 1
    if tree_size < 3 or tree_size > 512:
        raise ValueError(f"Huffman 解压错误: treeSize {tree_size} 越界 (3..512)")

    tree_base = beg + 5
    bit_stream_start = tree_base + tree_size
    if bit_stream_start >= n:
        raise ValueError("Huffman 解压错误: 位流起点越界")

    mask_bits = (1 << bits) - 1

    # 树节点字段: bit7=LTerm, bit6=RTerm, bits0-5=Offset
    out = bytearray()
    source = bit_stream_start
    n_pointer = tree_base
    node = raw[n_pointer]
    block = 0
    bits_seen = 0
    bits_consumed = 0

    while remaining > 0:
        if source + 4 > n:
            raise ValueError("Huffman 解压错误: 位流不完整")
        bitstream = _read_u32_le(raw, source)
        source += 4
        for bits_remaining in range(32, 0, -1):
            if remaining <= 0:
                break
            bit = (bitstream >> 31) & 1
            bitstream = (bitstream << 1) & 0xFFFFFFFF
            bits_consumed += 1

            offset = node & 0x3F
            next_pos = (n_pointer & ~1) + offset * 2 + 2
            if next_pos + 1 >= n:
                raise ValueError("Huffman 解压错误: 子节点越界")

            if bit == 1:
                # Go right
                if node & 0x40:  # RTerm
                    read_bits = raw[next_pos + 1]
                else:
                    n_pointer = next_pos + 1
                    node = raw[n_pointer]
                    continue
            else:
                # Go left
                if node & 0x80:  # LTerm
                    read_bits = raw[next_pos]
                else:
                    n_pointer = next_pos
                    node = raw[n_pointer]
                    continue

            block |= (read_bits & mask_bits) << bits_seen
            bits_seen += bits
            n_pointer = tree_base
            node = raw[n_pointer]
            if bits_seen == 32:
                out.append(block & 0xFF)
                out.append((block >> 8) & 0xFF)
                out.append((block >> 16) & 0xFF)
                out.append((block >> 24) & 0xFF)
                remaining -= 4
                block = 0
                bits_seen = 0

    return bytes(out), bits_consumed


def uncomp_huff_from_bytes(raw: bytes, beg: int) -> bytes:
    """从 raw[beg] 开始的 GBA BIOS Huffman 块解压 (4字节头格式)。

    beg 指向 4 字节头起点 (4字节对齐)。自动识别 4-bit (0x24) 和 8-bit (0x28)。
    """
    out, _ = _huff_decode_core(raw, beg)
    return out


def huff_block_size(raw: bytes, beg: int) -> int:
    """计算 Huffman 块占用的字节数 = 5 + tree_size + bit_stream_bytes (4字节对齐)。

    bit_stream_bytes 通过完整解码确定位流长度后转换为字节数。
    """
    n = len(raw)
    if beg + 5 > n:
        raise ValueError("Huffman 块大小计算错误: 块头不完整")
    tree_size_byte = raw[beg + 4]
    tree_size = (tree_size_byte << 1) + 1

    _, bits_consumed = _huff_decode_core(raw, beg)
    bit_stream_bytes = (bits_consumed + 7) // 8

    total = 5 + tree_size + bit_stream_bytes
    while total % 4 != 0:
        total += 1
    return total


class _HuffNode:
    """Huffman 树节点。value is None 表示内部节点。"""
    __slots__ = ['freq', 'value', 'left', 'right']

    def __init__(self, freq, value=None, left=None, right=None):
        self.freq = freq
        self.value = value
        self.left = left
        self.right = right


def _build_huffman_tree(freq: dict) -> _HuffNode:
    """从频率表构建 Huffman 树, 返回根节点。"""
    if not freq:
        raise ValueError("Huffman 压缩错误: 空频率表")

    # 单符号: 添加虚拟符号以满足 GBA BIOS 至少1个内部节点的要求
    if len(freq) == 1:
        sole_val = next(iter(freq))
        dummy_val = sole_val ^ 1
        return _HuffNode(
            freq[sole_val],
            left=_HuffNode(freq[sole_val], value=sole_val),
            right=_HuffNode(0, value=dummy_val),
        )

    import heapq
    heap = []
    counter = 0
    for v, f in freq.items():
        heapq.heappush(heap, (f, counter, _HuffNode(f, value=v)))
        counter += 1

    while len(heap) > 1:
        f1, _, n1 = heapq.heappop(heap)
        f2, _, n2 = heapq.heappop(heap)
        merged = _HuffNode(f1 + f2, left=n1, right=n2)
        heapq.heappush(heap, (merged.freq, counter, merged))
        counter += 1

    return heap[0][2]


def _layout_huffman_tree(root: _HuffNode, tree_base: int = 0) -> tuple:
    """BFS 布局 Huffman 树为 GBA BIOS 格式。

    Args:
        root: Huffman 树根节点
        tree_base: 树数据在 ROM 中的绝对起始地址 (beg + 5)。
            用于正确计算节点对的 offset (GBA BIOS 公式依赖地址对齐)。

    返回 (tree_data: bytearray, codes: dict{symbol: (code, length)}, tree_size: int)。

    树格式:
    - 节点按 pair 排列 (left, right), 每个 pair 2 字节
    - pair 索引 = (绝对地址 & ~1) // 2
    - 每个内部节点的子节点对由 offset 字段定位: child_pair = current_pair + offset + 1
    - 子节点绝对地址: left = child_pair * 2, right = child_pair * 2 + 1
    - offset < 64 (6 bits)
    - 叶节点位置存符号值, 内部节点位置存节点字节 (offset | LTerm | RTerm)
    """
    # 使用绝对地址分配位置, 确保 offset 与 GBA BIOS 公式一致
    positions = {root: tree_base}
    queue = [root]
    next_pair = (tree_base // 2) + 1  # 根节点所在 pair 的下一个 pair

    while queue:
        node = queue.pop(0)
        if node.value is not None:
            continue  # 叶节点跳过
        pos = positions[node]
        current_pair = (pos & ~1) // 2
        child_pair = next_pair
        offset = child_pair - current_pair - 1
        if offset > 63:
            raise ValueError(
                f"Huffman 压缩错误: offset {offset} > 63, 树太深。"
                f"建议使用 4-bit 模式或减少唯一符号数。"
            )
        positions[node.left] = child_pair * 2
        positions[node.right] = child_pair * 2 + 1
        next_pair += 1
        if node.left.value is None:
            queue.append(node.left)
        if node.right.value is None:
            queue.append(node.right)

    # 转换为 tree_data 索引 (相对于 tree_base)
    max_idx = max(p - tree_base for p in positions.values())
    tree_size = max_idx + 1
    # treeSize 必须为奇数: (tree_size_byte << 1) + 1
    if tree_size % 2 == 0:
        tree_size += 1
    tree_data = bytearray(tree_size)

    # 填充树数据
    for node, pos in positions.items():
        idx = pos - tree_base
        if idx >= tree_size:
            continue
        if node.value is not None:
            # 叶节点: 直接存符号值
            tree_data[idx] = node.value
        else:
            # 内部节点: 计算节点字节
            left_abs = positions[node.left]
            current_pair = (pos & ~1) // 2
            child_pair = (left_abs & ~1) // 2
            offset = child_pair - current_pair - 1
            node_byte = offset & 0x3F
            if node.left.value is not None:
                node_byte |= 0x80  # LTerm: 左子是叶
            if node.right.value is not None:
                node_byte |= 0x40  # RTerm: 右子是叶
            tree_data[idx] = node_byte

    # 生成 Huffman 编码
    codes = {}

    def gen_codes(node, code, length):
        if node.value is not None:
            codes[node.value] = (code, max(length, 1))
        else:
            gen_codes(node.left, code << 1, length + 1)
            gen_codes(node.right, (code << 1) | 1, length + 1)

    gen_codes(root, 0, 0)

    return tree_data, codes, tree_size


def comp_huff_bytes(data: bytes, bits: int = 8, beg: int = 0) -> bytes:
    """Huffman 压缩 data, 返回 GBA BIOS 4 字节头格式的压缩块。

    Args:
        data: 待压缩数据
        bits: 4 或 8 (每符号位数)
        beg: 压缩块在 ROM 中的起始地址 (4字节对齐)。
            用于计算树数据的绝对地址 tree_base = beg + 5,
            确保 Huffman 树节点的 offset 字段与 GBA BIOS 解压公式一致。

    Returns: 4 字节头 (bits4 + decompSize24) + tree_size_byte + 树数据 + 位流
    """
    if not data:
        raise ValueError("Huffman 压缩错误: 空数据")
    if bits not in (4, 8):
        raise ValueError(f"Huffman 压缩错误: 不支持的 bits {bits} (仅支持 4/8)")
    if 32 % bits != 0:
        raise ValueError(f"Huffman 压缩错误: 32 % {bits} != 0")

    # 数据长度必须是 4 的倍数 (每 32 位块 = 4 字节输出)
    padded = data
    if len(data) % 4 != 0:
        padded = data + b'\x00' * (4 - len(data) % 4)

    decomp_size = len(padded)
    if decomp_size < 4:
        raise ValueError(f"Huffman 压缩错误: 数据长度 {decomp_size} < 4")
    if decomp_size > 0xFFFFFF:
        raise ValueError(f"Huffman 压缩错误: 数据长度 {decomp_size} > 0xFFFFFF")

    # 树数据的绝对起始地址 (4字节头 + 1字节tree_size)
    tree_base = beg + 5

    # 提取符号
    if bits == 8:
        symbols = list(padded)
    else:  # bits == 4
        symbols = []
        for b in padded:
            symbols.append(b & 0xF)        # 低 nibble
            symbols.append((b >> 4) & 0xF)  # 高 nibble

    # 统计频率
    freq = {}
    for s in symbols:
        freq[s] = freq.get(s, 0) + 1

    # 构建 Huffman 树
    root = _build_huffman_tree(freq)

    # 布局树 (传入 tree_base 以正确计算 offset)
    tree_data, codes, tree_size = _layout_huffman_tree(root, tree_base)

    # 校验 tree_size
    tree_size_byte = (tree_size - 1) // 2
    if tree_size_byte > 255:
        raise ValueError(
            f"Huffman 压缩错误: treeSize {tree_size} 太大 "
            f"(tree_size_byte {tree_size_byte} > 255, 唯一符号 {len(freq)} 个)"
        )
    if tree_size < 3:
        raise ValueError(f"Huffman 压缩错误: treeSize {tree_size} < 3")

    # 编码位流 (32 位块, MSB 先, 小端存储)
    # 找最短编码用于位流末尾填充 (确保填充形成有效符号, 避免解压器卡在树中间)
    shortest_sym = min(codes, key=lambda s: codes[s][1])
    shortest_code, shortest_len = codes[shortest_sym]

    bitstream = 0
    bits_in_stream = 0
    out_bytes = bytearray()

    for s in symbols:
        code, length = codes[s]
        for i in range(length - 1, -1, -1):
            bit = (code >> i) & 1
            bitstream = (bitstream << 1) | bit
            bits_in_stream += 1
            if bits_in_stream == 32:
                out_bytes.append(bitstream & 0xFF)
                out_bytes.append((bitstream >> 8) & 0xFF)
                out_bytes.append((bitstream >> 16) & 0xFF)
                out_bytes.append((bitstream >> 24) & 0xFF)
                bitstream = 0
                bits_in_stream = 0

    # 刷新剩余位: 用最短编码填充, 不足部分补 0
    # 这样解压器能正确解码填充符号以完成最后一个 4 字节块
    while bits_in_stream > 0 and bits_in_stream < 32:
        for i in range(shortest_len - 1, -1, -1):
            if bits_in_stream >= 32:
                break
            bit = (shortest_code >> i) & 1
            bitstream = (bitstream << 1) | bit
            bits_in_stream += 1
    if bits_in_stream == 32:
        out_bytes.append(bitstream & 0xFF)
        out_bytes.append((bitstream >> 8) & 0xFF)
        out_bytes.append((bitstream >> 16) & 0xFF)
        out_bytes.append((bitstream >> 24) & 0xFF)

    # 组装: 4 字节头 + tree_size_byte + 树数据 + 位流
    header = (decomp_size << 8) | bits
    result = bytearray()
    result.append(header & 0xFF)
    result.append((header >> 8) & 0xFF)
    result.append((header >> 16) & 0xFF)
    result.append((header >> 24) & 0xFF)
    result.append(tree_size_byte)
    result.extend(tree_data)
    result.extend(out_bytes)

    return bytes(result)


def comp_huff_bytes_auto(data: bytes, beg: int = 0) -> bytes:
    """自动选择更小的 8-bit 或 4-bit Huffman 压缩。

    4-bit 模式符号多样性低 (最多16种), 树更小, 对低熵数据更高效;
    8-bit 模式符号独立编码, 对高熵数据更高效。两者都尝试取更小者。
    """
    candidates = []
    for bits in (8, 4):
        try:
            candidates.append(comp_huff_bytes(data, bits=bits, beg=beg))
        except ValueError:
            pass
    if not candidates:
        raise ValueError("Huffman 压缩错误: 4-bit 和 8-bit 模式均失败")
    return min(candidates, key=len)


# ---------- Huff+LZ77 ----------

def uncomp_huff_lz77_from_bytes(
    rom_raw: bytes,
    huff_off: int,
    lz77_off: int,
    data_off: int,
) -> bytes:
    """两级解压 Huff+LZ77。

    1. Huffman 解码 rom_raw[huff_off:] -> 流 S1
    2. LZ77 解压 S1[lz77_off:] -> 流 S2
    3. 返回 S2 (调用方取 data_off 偏移)

    注意: data_off 由调用方在使用返回值时处理, 本函数返回完整 S2。
    """
    s1 = uncomp_huff_from_bytes(rom_raw, huff_off)
    s2 = uncomp_from_bytes(s1, lz77_off)
    return s2


# ---------- 统一调度入口 ----------

def uncomp_file_dispatch(rom_path: str, out_path: str, pa: "ParsedAddr") -> None:
    """根据 pa.comp_kind 选择解压器, 把解压后的数据写到 out_path。

    RAW 不应调用此函数(调用方应直接读 ROM)。
    """
    with open(rom_path, "rb") as f:
        rom_raw = f.read()

    if pa.comp_kind == "lz77":
        data = uncomp_from_bytes(rom_raw, pa.cmp_offset)
    elif pa.comp_kind == "rle":
        data = uncomp_rle_from_bytes(rom_raw, pa.cmp_offset)
    elif pa.comp_kind == "huff":
        data = uncomp_huff_from_bytes(rom_raw, pa.cmp_offset)
    elif pa.comp_kind == "huff_lz77":
        data = uncomp_huff_lz77_from_bytes(
            rom_raw, pa.huff_offset, pa.cmp_offset, pa.data_offset
        )
    else:
        raise ValueError(f"不支持的压缩类型: {pa.comp_kind}")

    with open(out_path, "wb") as f:
        f.write(data)


def comp_bytes_dispatch(data: bytes, comp_kind: Optional[str], beg: int = 0) -> bytes:
    """根据 comp_kind 调用对应压缩器。None 返回原 data。

    Args:
        data: 待压缩数据
        comp_kind: 压缩类型 (None/lz77/rle/huff/huff_lz77)
        beg: 压缩块在 ROM 中的起始地址 (仅 Huffman 需要, 用于树布局)
    """
    if comp_kind is None:
        return data
    if comp_kind == "lz77":
        return comp_bytes(data)
    if comp_kind == "rle":
        return comp_rle_bytes(data)
    if comp_kind == "huff":
        return comp_huff_bytes_auto(data, beg=beg)
    if comp_kind == "huff_lz77":
        # 先 LZ77 压缩, 再 Huffman 压缩
        lz77_data = comp_bytes(data)
        return comp_huff_bytes_auto(lz77_data, beg=beg)
    raise ValueError(f"不支持的压缩类型: {comp_kind}")


def block_size_dispatch(rom_raw: bytes, pa: "ParsedAddr") -> int:
    """根据 pa.comp_kind 调用对应块大小计算函数。

    huff_lz77 返回 Huffman 块的字节数(因 LZ77 块嵌在 Huffman 流内,不直接占 ROM 空间)。
    """
    if pa.comp_kind == "lz77":
        return comp_block_size(rom_raw, pa.cmp_offset)
    if pa.comp_kind == "rle":
        return rle_block_size(rom_raw, pa.cmp_offset)
    if pa.comp_kind == "huff":
        return huff_block_size(rom_raw, pa.cmp_offset)
    if pa.comp_kind == "huff_lz77":
        # LZ77 块嵌在 Huffman 流内, ROM 中只占 Huffman 块大小
        return huff_block_size(rom_raw, pa.huff_offset)
    raise ValueError(f"不支持的压缩类型: {pa.comp_kind}")
