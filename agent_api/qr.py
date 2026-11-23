"""极简 QR 码生成（纯标准库，byte 模式，纠错等级 M）。

只为「把 otpauth:// 链接变成手机能扫的二维码」服务：版本 1~20 自动选择，
输出 SVG（前端直接插进页面）或矩阵。

不追求打印级容错，但索引/掩码/格式位都按 ISO/IEC 18004 来，扫码器能认。
"""

from __future__ import annotations

# 纠错等级 M：每个版本 (总码字, 每组纠错码字, [(块数, 每块数据码字), ...])
_EC_M: dict[int, tuple[int, int, list[tuple[int, int]]]] = {
    1: (26, 10, [(1, 16)]),
    2: (44, 16, [(1, 28)]),
    3: (70, 26, [(1, 44)]),
    4: (100, 18, [(2, 32)]),
    5: (134, 24, [(2, 43)]),
    6: (172, 16, [(4, 27)]),
    7: (196, 18, [(4, 31)]),
    8: (242, 22, [(2, 38), (2, 39)]),
    9: (292, 22, [(3, 36), (2, 37)]),
    10: (346, 26, [(4, 43), (1, 44)]),
    11: (404, 30, [(1, 50), (4, 51)]),
    12: (466, 30, [(6, 36), (2, 37)]),
    13: (532, 30, [(8, 37), (1, 38)]),
    14: (581, 30, [(4, 40), (5, 41)]),
    15: (655, 30, [(5, 41), (5, 42)]),
    16: (733, 30, [(7, 45), (3, 46)]),
    17: (815, 30, [(10, 46), (1, 47)]),
    18: (901, 30, [(9, 47), (4, 48)]),
    19: (991, 30, [(3, 48), (11, 49)]),
    20: (1085, 30, [(3, 49), (13, 50)]),
}

_ALIGN: dict[int, list[int]] = {
    1: [],
    2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
    11: [6, 30, 54], 12: [6, 32, 58], 13: [6, 34, 62], 14: [6, 26, 46, 66],
    15: [6, 26, 48, 70], 16: [6, 26, 50, 74], 17: [6, 30, 54, 78],
    18: [6, 30, 56, 82], 19: [6, 30, 58, 86], 20: [6, 34, 62, 90],
}


def _gf_mul(a: int, b: int) -> int:
    result = 0
    while b:
        if b & 1:
            result ^= a
        b >>= 1
        a <<= 1
        if a & 0x100:
            a ^= 0x11D
    return result


def _rs_generator(n: int) -> list[int]:
    poly = [1]
    alpha = 1
    for _ in range(n):
        nxt = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            nxt[j] ^= coef
            nxt[j + 1] ^= _gf_mul(coef, alpha)
        poly = nxt
        alpha = _gf_mul(alpha, 2)
    return poly


def _rs_ecc(data: list[int], n: int) -> list[int]:
    gen = _rs_generator(n)
    remainder = [0] * n
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        if factor:
            for i, coef in enumerate(gen[1:]):
                remainder[i] ^= _gf_mul(coef, factor)
    return remainder


def _bits(value: int, length: int) -> list[int]:
    return [(value >> (length - 1 - i)) & 1 for i in range(length)]


def _bch(data: int, poly: int, poly_bits: int) -> int:
    value = data << (poly_bits - 1)
    while value.bit_length() >= poly_bits:
        value ^= poly << (value.bit_length() - poly_bits)
    return (data << (poly_bits - 1)) | value


def _capacity_bits(version: int) -> int:
    total, _ec, groups = _EC_M[version]
    data_codewords = sum(count * size for count, size in groups)
    return data_codewords * 8


def _choose_version(length: int) -> int:
    for version in range(1, 21):
        count_bits = 8 if version <= 9 else 16
        need = 4 + count_bits + length * 8
        if need <= _capacity_bits(version):
            return version
    raise ValueError("内容太长，装不进版本 20")


def _encode_data(text: str, version: int) -> list[int]:
    payload = text.encode("utf-8")
    count_bits = 8 if version <= 9 else 16
    bits: list[int] = []
    bits += _bits(0b0100, 4)
    bits += _bits(len(payload), count_bits)
    for byte in payload:
        bits += _bits(byte, 8)
    capacity = _capacity_bits(version)
    bits += [0] * min(4, capacity - len(bits))
    while len(bits) % 8:
        bits.append(0)
    bytes_out = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    total, _ec, groups = _EC_M[version]
    data_codewords = sum(count * size for count, size in groups)
    pad = [0xEC, 0x11]
    index = 0
    while len(bytes_out) < data_codewords:
        bytes_out.append(pad[index % 2])
        index += 1
    return bytes_out


def _codewords(text: str, version: int) -> list[int]:
    data = _encode_data(text, version)
    _total, ec_len, groups = _EC_M[version]
    blocks: list[list[int]] = []
    pos = 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(data[pos:pos + size])
            pos += size
    ecc_blocks = [_rs_ecc(block, ec_len) for block in blocks]
    out: list[int] = []
    for i in range(max(len(b) for b in blocks)):
        for block in blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(ec_len):
        for block in ecc_blocks:
            out.append(block[i])
    return out


def _blank(size: int) -> list[list[int | None]]:
    return [[None] * size for _ in range(size)]


def _finder(matrix: list[list[int | None]], r: int, c: int) -> None:
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            rr, cc = r + dr, c + dc
            if not (0 <= rr < len(matrix) and 0 <= cc < len(matrix)):
                continue
            inside = 0 <= dr <= 6 and 0 <= dc <= 6
            dark = inside and (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4))
            matrix[rr][cc] = 1 if dark else 0


def _align(matrix: list[list[int | None]], r: int, c: int) -> None:
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            dark = max(abs(dr), abs(dc)) != 1
            matrix[r + dr][c + dc] = 1 if dark else 0


def _base_matrix(version: int) -> list[list[int | None]]:
    size = version * 4 + 17
    m = _blank(size)
    _finder(m, 0, 0)
    _finder(m, 0, size - 7)
    _finder(m, size - 7, 0)
    # 定位线
    for i in range(8, size - 8):
        m[6][i] = 1 if i % 2 == 0 else 0
        m[i][6] = 1 if i % 2 == 0 else 0
    # 校正图形（跳过和三个定位图形重叠的位置）
    centers = _ALIGN[version]
    for r in centers:
        for c in centers:
            if (r, c) in {(6, 6), (6, centers[-1]), (centers[-1], 6)}:
                continue
            _align(m, r, c)
    # 格式信息区先占位
    for i in range(9):
        if m[8][i] is None:
            m[8][i] = 0
        if m[i][8] is None:
            m[i][8] = 0
    for i in range(8):
        m[8][size - 1 - i] = 0
        m[size - 1 - i][8] = 0
    if version >= 7:
        for i in range(6):
            for j in range(3):
                m[size - 11 + j][i] = 0
                m[i][size - 11 + j] = 0
    m[size - 8][8] = 1  # 固定黑点
    return m


def _place_data(m: list[list[int | None]], bits: list[int], version: int) -> None:
    size = len(m)
    idx = 0
    right = size - 1
    while right > 0:
        if right == 6:
            right -= 1
        for vert in range(size):
            upward = ((right + 1) & 2) == 0
            row = size - 1 - vert if upward else vert
            for j in range(2):
                col = right - j
                if m[row][col] is None:
                    m[row][col] = bits[idx] if idx < len(bits) else 0
                    idx += 1
        right -= 2


def _mask_bit(mask: int, i: int, j: int) -> bool:
    if mask == 0:
        return (i + j) % 2 == 0
    if mask == 1:
        return i % 2 == 0
    if mask == 2:
        return j % 3 == 0
    if mask == 3:
        return (i + j) % 3 == 0
    if mask == 4:
        return (i // 2 + j // 3) % 2 == 0
    if mask == 5:
        return (i * j) % 2 + (i * j) % 3 == 0
    if mask == 6:
        return ((i * j) % 2 + (i * j) % 3) % 2 == 0
    return ((i + j) % 2 + (i * j) % 3) % 2 == 0


def _is_function(version: int, row: int, col: int) -> bool:
    size = version * 4 + 17
    if row < 9 and col < 9:
        return True
    if row < 9 and col >= size - 8:
        return True
    if row >= size - 8 and col < 9:
        return True
    if row == 6 or col == 6:
        return True
    centers = _ALIGN[version]
    for r in centers:
        for c in centers:
            if (r, c) in {(6, 6), (6, centers[-1]), (centers[-1], 6)}:
                continue
            if abs(row - r) <= 2 and abs(col - c) <= 2:
                return True
    if version >= 7:
        if row < 6 and col >= size - 11 and col < size - 8:
            return True
        if col < 6 and row >= size - 11 and row < size - 8:
            return True
    return False


def _apply_mask(m: list[list[int | None]], version: int, mask: int) -> list[list[int]]:
    size = len(m)
    out = [[0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            value = int(m[i][j] or 0)
            if not _is_function(version, i, j) and _mask_bit(mask, i, j):
                value ^= 1
            out[i][j] = value
    return out


def _format_int(mask: int) -> int:
    data = (0b00 << 3) | mask  # 纠错等级 M = 00
    return _bch(data, 0x537, 11) ^ 0x5412


def _draw_format(m: list[list[int]], mask: int, version: int) -> None:
    size = len(m)
    bits = _format_int(mask)
    # 竖排（左上 + 左下）
    for i in range(15):
        value = (bits >> i) & 1
        if i < 6:
            m[i][8] = value
        elif i < 8:
            m[i + 1][8] = value
        else:
            m[size - 15 + i][8] = value
    # 横排（右上 + 左上）
    for i in range(15):
        value = (bits >> i) & 1
        if i < 8:
            m[8][size - 1 - i] = value
        elif i < 9:
            m[8][7] = value
        else:
            m[8][15 - i - 1] = value
    m[size - 8][8] = 1  # 固定黑点
    if version >= 7:
        vbits = _bch(version, 0x1F25, 13)
        for i in range(18):
            value = (vbits >> i) & 1
            m[i // 3][i % 3 + size - 11] = value
            m[i % 3 + size - 11][i // 3] = value


def _penalty(m: list[list[int]]) -> int:
    size = len(m)
    score = 0
    # 规则 1：行/列连续同色
    for rows in (m, list(map(list, zip(*m))) ):
        for line in rows:
            run = 1
            for i in range(1, size):
                if line[i] == line[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run = 1
            if run >= 5:
                score += 3 + (run - 5)
    # 规则 2：2x2 同色
    for i in range(size - 1):
        for j in range(size - 1):
            if m[i][j] == m[i][j + 1] == m[i + 1][j] == m[i + 1][j + 1]:
                score += 3
    # 规则 3：1:1:3:1:1 找像定位图形
    pattern = [1, 0, 1, 1, 1, 0, 1]
    for rows in (m, list(map(list, zip(*m)))):
        for line in rows:
            for i in range(size - 6):
                if line[i:i + 7] == pattern:
                    before = line[max(0, i - 4):i]
                    after = line[i + 7:i + 11]
                    if before == [0] * len(before) or after == [0] * len(after):
                        score += 40
    # 规则 4：黑白比例
    dark = sum(sum(row) for row in m)
    ratio = dark * 100 // (size * size)
    score += 10 * (abs(ratio - 50) // 5)
    return score


def matrix(text: str) -> list[list[int]]:
    """返回 QR 矩阵（1 = 黑）。"""
    version = _choose_version(len(text.encode("utf-8")))
    bits: list[int] = []
    for word in _codewords(text, version):
        bits += _bits(word, 8)
    base = _base_matrix(version)
    _place_data(base, bits, version)
    best: list[list[int]] | None = None
    best_mask = 0
    best_score = 10 ** 9
    for mask in range(8):
        candidate = _apply_mask(base, version, mask)
        _draw_format(candidate, mask, version)
        score = _penalty(candidate)
        if score < best_score:
            best, best_mask, best_score = candidate, mask, score
    assert best is not None
    return best


def svg(text: str, module: int = 5, border: int = 4) -> str:
    """输出可直接内联的 SVG（黑底白点用 CSS 控制也行，这里给黑白原色）。"""
    m = matrix(text)
    size = len(m)
    total = (size + border * 2) * module
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{total}" '
        f'viewBox="0 0 {total} {total}" shape-rendering="crispEdges">',
        f'<rect width="{total}" height="{total}" fill="#fff"/>',
    ]
    for i, row in enumerate(m):
        for j, value in enumerate(row):
            if value:
                parts.append(
                    f'<rect x="{(j + border) * module}" y="{(i + border) * module}" '
                    f'width="{module}" height="{module}" fill="#000"/>'
                )
    parts.append("</svg>")
    return "".join(parts)
