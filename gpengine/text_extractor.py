# -*- coding: utf-8 -*-
"""PDF 文本层直读模块：适用于矢量导出（含文字层）的吉他谱 PDF。

Guitar Pro 8 等软件导出的 PDF 里，品位数字是真实文字、弦线/小节线是矢量路径、
节奏符号（符干/符尾/附点/休止符）是矢量线段或音乐字体字形。
直接从 PDF 内部读取这些元素，识别准确率远高于渲染成图再 OCR。

结构（实测 GP8 导出格式）:
    - 弦线: 近水平线段（数字处会被打断成小段），按 y 聚类合并
    - 小节线: 细高矩形 (width<3pt, 高≈系统高度)
    - 品位: 文字层单词（1~2 位数字），bbox 顶边 ≈ 弦线 y - 3pt
    - 符干: 竖直线段（从谱表下方伸入谱表，长约 5 倍弦距）
    - 符尾: U+E240/E241=八分, U+E242/E243=十六分, U+E244/E245=三十二分
    - 附点: U+E1E7
    - 休止: U+E4E3=全, U+E4E4=二分, U+E4E5=四分, U+E4E6=八分, U+E4E7=十六分
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# SMuFL 音乐符号字形 -> 时值
_FLAG_DURATIONS = {
    0xE240: 8, 0xE241: 8,          # 八分符尾（上/下）
    0xE242: 16, 0xE243: 16,        # 十六分符尾
    0xE244: 32, 0xE245: 32,        # 三十二分符尾
}
_REST_DURATIONS = {
    0xE4E3: 1, 0xE4E4: 2, 0xE4E5: 4, 0xE4E6: 8, 0xE4E7: 16, 0xE4E8: 32,
}
_DOT = 0xE1E7


@dataclass
class TextTabSystem:
    """文本层读出的一个谱表系统。"""
    string_ys: list
    top: float
    bottom: float
    left: float
    right: float
    barlines: list = field(default_factory=list)
    measures: list = field(default_factory=list)   # spec 格式的 measures
    tuning: list | None = None

    @property
    def gap(self) -> float:
        n = len(self.string_ys)
        return (self.string_ys[-1] - self.string_ys[0]) / (n - 1) if n > 1 else 0.0


def has_text_layer(pdf_path: str, min_digits: int = 10) -> bool:
    """粗略判断 PDF 是否有可用的文字层（品位数字为文字）。"""
    import fitz
    doc = fitz.open(pdf_path)
    try:
        n = 0
        for page in doc:
            for w in page.get_text("words"):
                if w[4].isdigit() and 1 <= len(w[4]) <= 2:
                    n += 1
                    if n >= min_digits:
                        return True
        return False
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# 几何元素提取
# ---------------------------------------------------------------------------

def _extract_line_clusters(page) -> list:
    """提取近水平线段并按 y 聚类。返回 [(y, x_min, x_max), ...]（按 y 升序）。"""
    clusters = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                if abs(p1.y - p2.y) < 0.5 and abs(p2.x - p1.x) >= 3:
                    y = (p1.y + p2.y) / 2
                    clusters.append((y, min(p1.x, p2.x), max(p1.x, p2.x)))
            elif it[0] == "re":
                r = it[1]
                if r.height < 1.5 and r.width >= 3:
                    y = (r.y0 + r.y1) / 2
                    clusters.append((y, r.x0, r.x1))
    clusters.sort(key=lambda t: t[0])
    merged = []
    for y, x1, x2 in clusters:
        if merged and abs(y - merged[-1][0]) <= 1.5:
            py, px1, px2 = merged[-1]
            merged[-1] = ((py + y) / 2, min(px1, x1), max(px2, x2))
        else:
            merged.append((y, x1, x2))
    return merged


def _group_into_systems(lines: list) -> list:
    """把相邻等距的横线分组为谱表系统。

    - 若一组超过 8 条线（异常合并），在组内最大间隙处递归拆分。
    - 剔除组首/组尾间距明显偏离中位数的杂散线（如音符上方的注释线）。
    """
    groups = []
    if len(lines) < 4:
        return groups
    run = [lines[0]]
    for i in range(1, len(lines)):
        gap = lines[i][0] - lines[i - 1][0]
        if len(run) > 1:
            avg = sum(lines[j][0] - lines[j - 1][0] for j in range(1, len(run))) / (len(run) - 1)
            keep = gap <= max(avg * 1.7, avg + 1.5)
        else:
            keep = gap <= 15
        if keep:
            run.append(lines[i])
        else:
            groups.append(run)
            run = [lines[i]]
    groups.append(run)

    result = []
    # 页级主导弦距：过滤间距远小于主导值的组（和弦指法图、五线谱等）。
    # 注意：必须保持各组原始的 y 顺序（即阅读顺序），不能排序！
    import statistics
    medians = []
    for grp in groups:
        if len(grp) >= 4:
            gaps = [grp[i][0] - grp[i - 1][0] for i in range(1, len(grp))]
            medians.append(statistics.median(gaps))
        else:
            medians.append(None)
    dominant = max((m for m in medians if m is not None), default=None)
    for grp, med in zip(groups, medians):
        if med is None or len(grp) < 4:
            continue
        if dominant and med < dominant * 0.65:
            continue      # 和弦指法图 / 五线谱等小间距线条组
        grp = _trim_stray_lines(grp)
        if len(grp) < 4:
            continue
        # 拆分异常大的组（> 8 条线）
        while len(grp) > 8:
            gaps = [(grp[j][0] - grp[j - 1][0], j) for j in range(1, len(grp))]
            _, split_at = max(gaps)
            left, right = grp[:split_at], grp[split_at:]
            if len(left) >= 4 and len(right) >= 4:
                result.append(left)
                grp = right
            else:
                break
        result.append(grp)
    # 拆出的子组需重新按主导间距过滤（和弦图常与相邻谱表合并后再被拆出）
    filtered = []
    for grp in result:
        if len(grp) >= 4 and dominant:
            gaps = [grp[j][0] - grp[j - 1][0] for j in range(1, len(grp))]
            if statistics.median(gaps) < dominant * 0.65:
                continue
        filtered.append(grp)
    return filtered


def _trim_stray_lines(lines: list) -> list:
    """剔除组首/组尾间距偏离中位数 > 15% 的杂散线。"""
    import statistics
    while len(lines) >= 5:
        gaps = [lines[i][0] - lines[i - 1][0] for i in range(1, len(lines))]
        med = statistics.median(gaps)
        if not med:
            break
        head_dev = gaps[0] / med
        tail_dev = gaps[-1] / med
        if head_dev >= tail_dev and head_dev > 1.15:
            lines = lines[1:]
        elif tail_dev > 1.15:
            lines = lines[:-1]
        else:
            break
    return lines


def _extract_barlines(page, system) -> tuple:
    """小节线 = 细高矩形（width<3pt，与系统 y 范围重叠 >= 系统高度的 50%）。

    返回 (xs: 小节线 x 列表, (y_lo, y_hi): 小节线纵向跨度——系统弦区间的权威边界)。
    """
    xs = []
    sys_h = system.bottom - system.top
    y_lo, y_hi = None, None
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] != "re":
                continue
            r = it[1]
            if r.width > 1.5:     # 结尾双纵线的粗线(宽约2.1)不是小节线
                continue
            ymin, ymax = min(r.y0, r.y1), max(r.y0, r.y1)
            if (ymax - ymin) < sys_h * 0.5:
                continue
            overlap = min(ymax, system.bottom) - max(ymin, system.top)
            if overlap < sys_h * 0.5:      # 与系统的纵向重叠至少一半（容忍杂散线）
                continue
            xs.append((r.x0 + r.x1) / 2)
            y_lo = ymin if y_lo is None else min(y_lo, ymin)
            y_hi = ymax if y_hi is None else max(y_hi, ymax)
    xs.sort()
    merged = []
    for x in xs:
        if merged and x - merged[-1] <= 2.5:
            merged[-1] = (merged[-1] + x) / 2
        else:
            merged.append(x)
    return merged, (y_lo, y_hi)


def _extract_digits(page, system) -> list:
    """文字层品位数字、哑音记号(X)、括号延音数字。

    返回 [(cx, 弦索引, 品位, is_dead, is_tied_dest), ...]。
    """
    gap = system.gap
    items = []
    for w in page.get_text("words"):
        t = w[4]
        is_mute = t.strip().lower() in ("x", "×")
        m = re.match(r"^[\(（](\d{1,2})[\)）]$", t.strip())   # 括号延音数字如 (7)
        is_paren = bool(m)
        if not (t.isdigit() and 1 <= len(t) <= 2) and not is_mute and not is_paren:
            continue
        cx = (w[0] + w[2]) / 2
        cy = (w[1] + w[3]) / 2
        if not (system.left - 2 <= cx <= system.right + 2):
            continue
        dists = [abs(cy - y) for y in system.string_ys]
        si = int(min(range(len(dists)), key=dists.__getitem__))
        if si >= 8 or dists[si] > gap * 0.75:   # 吉他最多 8 弦，异常归属丢弃
            continue
        if cy < system.string_ys[si] - gap * 0.3:   # 明显在弦线上方：小节号等标记，丢弃
            continue
        if is_mute:
            items.append((cx, si, 0, True, False))    # 哑音/左手闷音(x)
        elif is_paren:
            items.append((cx, si, int(m.group(1)), False, True))  # 延音线目标音
        else:
            items.append((cx, si, int(t), False, False))
    return items


def _extract_rhythm(page, system) -> dict:
    """提取节奏元素。

    返回:
        stems:  {x: True}                  符干 x 位置（该拍至少是四分音符）
        ticks:  {x: [y, ...]}              符干末端的短竖线（=八分符尾；两层=十六分）
        flags:  {x: duration}              符尾字形 -> 八分/十六分/...
        rests:  [(x, duration)]            休止符
        dots:   [x]                        附点
    """
    gap = system.gap
    stems = {}
    ticks = {}
    flags = {}
    rests = []
    dots = []

    # 符干: 竖直线段，位于谱表下方、伸入谱表
    # tick: 符干末端的 1~4pt 竖直短线（八分符尾）
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] != "l":
                continue
            p1, p2 = it[1], it[2]
            if abs(p1.x - p2.x) > 0.5:
                continue
            ymin, ymax = min(p1.y, p2.y), max(p1.y, p2.y)
            length = ymax - ymin
            x = round((p1.x + p2.x) / 2, 1)
            if gap * 2 <= length <= gap * 7 \
                    and system.bottom - gap * 0.5 <= ymax <= system.bottom + gap * 6:
                stems[x] = True
            elif 1 <= length <= 4 \
                    and system.bottom + gap * 0.5 <= ymin <= system.bottom + gap * 2.5:
                # 符干末端 tick 限制在梁线区（排除三连音括号竖钩等更深处的短线）
                ticks.setdefault(x, []).append(round((ymin + ymax) / 2, 1))

    # 字形: 符尾 / 休止 / 附点（必须落在本系统的 y 范围内，防止跨系统误配）
    y_lo = system.top - gap
    y_hi = system.bottom + gap * 6
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for ch in span["chars"]:
                    cp = ord(ch["c"])
                    if cp not in _FLAG_DURATIONS and cp not in _REST_DURATIONS and cp != _DOT:
                        continue
                    x0, y0, x1, y1 = ch["bbox"]
                    cx = (x0 + x1) / 2
                    cy = (y0 + y1) / 2
                    if not (y_lo <= cy <= y_hi):
                        continue
                    if cp in _FLAG_DURATIONS:
                        flags[round(cx, 1)] = _FLAG_DURATIONS[cp]
                    elif cp in _REST_DURATIONS:
                        rests.append((cx, _REST_DURATIONS[cp]))
                    else:
                        dots.append(cx)
    # 曲线环：全页收集贝塞尔曲线，按端点链接成环。
    #   4+ 段闭合环 = 全音符/二分音符椭圆；2 段细长月牙环 = 延音线(tie)
    all_curves = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "c":
                all_curves.append(it[1:5])
    ellipses = []
    ties = []
    for loop in _chain_curves(all_curves):
        parts = [loop]
        if len(loop) > 4 and _pt_dist(loop[0][0], loop[-1][3]) <= 1:
            # 椭圆与相邻月牙被链成一个大环：按闭合点拆回子环（如 4 段椭圆 + 2 段延音弧）
            for k in range(2, len(loop) - 1):
                if _pt_dist(loop[0][0], loop[k - 1][3]) <= 1 \
                        and _pt_dist(loop[k][0], loop[-1][3]) <= 1:
                    parts = [loop[:k], loop[k:]]
                    break
        for part in parts:
            xs = [p.x for c in part for p in c]
            ys = [p.y for c in part for p in c]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            w, h = x1 - x0, y1 - y0
            if len(part) >= 4 and _pt_dist(part[0][0], part[-1][3]) <= 1:
                # 椭圆（全音符/二分音符）
                if not (gap * 0.5 <= w <= gap * 4.5 and gap * 0.5 <= h <= gap * 4.5):
                    continue
                if w / max(h, 0.1) > 3 or h / max(w, 0.1) > 3:
                    continue
                if not (system.top - 2 <= (y0 + y1) / 2 <= system.bottom + 2):
                    continue
                cx = (x0 + x1) / 2
                has_line = False
                for d2 in page.get_drawings():
                    for it in d2["items"]:
                        if it[0] != "l":
                            continue
                        p1, p2 = it[1], it[2]
                        if abs(p1.x - p2.x) > 1:
                            continue
                        vx = (p1.x + p2.x) / 2
                        vy0, vy1 = min(p1.y, p2.y), max(p1.y, p2.y)
                        if abs(vx - cx) <= gap and vy0 >= y1 + gap and vy0 <= y1 + gap * 6 \
                                and gap * 0.6 <= vy1 - vy0 <= gap * 1.6:
                            has_line = True
                            break
                    if has_line:
                        break
                ellipses.append((x0, x1, has_line))
            elif len(part) == 2 and w >= gap * 0.9 and h <= gap * 1.6:
                # 延音线/击勾弦/连奏滑音: 细长月牙（含短弧，底边贴合弦线，故存 y1 供定弦）
                if not (system.top - gap * 1.5 <= (y0 + y1) / 2 <= system.bottom + gap * 1.5):
                    continue
                ties.append((x0, x1, y0, y1))

    # 滑弦(slide)：连接两个品位数字的斜线段（dy 0.4~1.2 弦距、坡度 < 0.6）
    slides = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] != "l":
                continue
            p1, p2 = it[1], it[2]
            dx = abs(p2.x - p1.x)
            dy = abs(p2.y - p1.y)
            # 长短斜线都收：连奏/普通滑弦的长斜线 + 无头无尾滑音的小斜线
            if dx >= gap * 0.5 and gap * 0.3 <= dy <= gap * 1.2 and dy / dx < 1.5:
                x0, x1 = min(p1.x, p2.x), max(p1.x, p2.x)
                y_left = p1.y if p1.x <= p2.x else p2.y
                y_right = p2.y if p1.x <= p2.x else p1.y
                yc = (y_left + y_right) / 2
                if not (system.top - gap <= yc <= system.bottom + gap):
                    continue
                slides.append((x0, x1, y_left, y_right))

    # 弧线上方的技巧字母: h/p = 击勾弦, sl = 连奏滑音（"sl." 需去句点）
    letters = []
    for w in page.get_text("words"):
        t = w[4].strip().lower().rstrip(".")
        if t in ("h", "p", "sl", "s"):
            letters.append(((w[0] + w[2]) / 2, (w[1] + w[3]) / 2, t))

    # 右手闷音 P.M.: 谱面上方 "P.M." 字样 + 顶线上方约 1 弦距处的虚线范围
    # 范围从词本身开始（词正下方的音符也算），虚线延伸到终点
    pm_ranges = []
    pm_words = [(w[1], w[0]) for w in page.get_text("words")
                if w[4].strip().upper().startswith("P.M")]
    top_y = system.top
    for wy0, wx0 in pm_words:
        if not (top_y - gap * 2.5 <= wy0 <= top_y):
            continue
        best = None
        for d in page.get_drawings():
            for it in d["items"]:
                if it[0] != "l":
                    continue
                p1, p2 = it[1], it[2]
                if abs(p1.y - p2.y) > 0.6:
                    continue
                if not (top_y - gap * 1.6 <= p1.y <= top_y - gap * 0.2):
                    continue
                x0, x1 = min(p1.x, p2.x), max(p1.x, p2.x)
                if x1 - x0 < gap * 0.8:
                    continue
                if not (wx0 - 2 <= x0 <= wx0 + 50):
                    continue
                if best is None or x0 < best[0]:
                    best = (x0, x1)
        if best is not None:
            pm_ranges.append((wx0 - 2, best[1] + 1))

    # 连音号(三连音等): 系统下方的数字 + 同高度的实线括号
    tuplets = []
    for w in page.get_text("words"):
        t = w[4].strip()
        if not (t.isdigit() and 2 <= int(t) <= 9):
            continue
        cx = (w[0] + w[2]) / 2
        cy = (w[1] + w[3]) / 2
        if not (system.bottom + 3 <= cy <= system.bottom + 40):
            continue
        if not (system.left - 10 <= cx <= system.right + 10):
            continue
        xs = [cx]
        for d in page.get_drawings():
            for it in d["items"]:
                if it[0] != "l":
                    continue
                p1, p2 = it[1], it[2]
                if abs(p1.y - p2.y) > 1.5:
                    continue
                if not (cy - 5 <= p1.y <= cy + 5):
                    continue
                if abs(p1.x - cx) > 100 or abs(p2.x - cx) > 100:
                    continue
                xs.extend([p1.x, p2.x])
        if len(xs) >= 4:      # 数字 + 至少一段括号横线
            tuplets.append((min(xs), max(xs), int(t)))

    # 梁线：系统下方 10~19pt 的横向线段（八分/十六分音符组，三连音组时值判断用）
    beams = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] != "l":
                continue
            p1, p2 = it[1], it[2]
            if abs(p1.y - p2.y) > 0.6:
                continue
            if not (system.bottom + 9 <= p1.y <= system.bottom + 20):
                continue
            x0, x1 = min(p1.x, p2.x), max(p1.x, p2.x)
            if x1 - x0 < gap:
                continue
            beams.append((x0, x1, p1.y))

    return {"stems": stems, "ticks": ticks, "flags": flags,
            "rests": rests, "dots": dots, "ellipses": ellipses,
            "ties": ties, "slides": slides, "letters": letters,
            "pm_ranges": pm_ranges, "tuplets": tuplets, "beams": beams}


def _pt_dist(p1, p2) -> float:
    return ((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2) ** 0.5


def _chain_curves(cs) -> list:
    """把曲线按端点链接成环。返回 [[curve, ...], ...]。"""
    unused = list(cs)
    loops = []
    while unused:
        loop = [unused.pop(0)]
        changed = True
        while changed:
            changed = False
            for i, c in enumerate(unused):
                if _pt_dist(loop[-1][3], c[0]) < 0.5:      # 向后延伸
                    loop.append(c)
                    unused.pop(i)
                    changed = True
                    break
            if not changed:
                for i, c in enumerate(unused):
                    if _pt_dist(loop[0][0], c[3]) < 0.5:   # 向前延伸
                        loop.insert(0, c)
                        unused.pop(i)
                        changed = True
                        break
        loops.append(loop)
    return loops


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

def _cluster_columns(items: list, x_tol: float) -> list:
    """按 x 把音符聚成纵向的拍（和弦）。

    返回 [{'x': 平均x, 'notes': [(弦索引, 品位, is_dead, is_tied_dest), ...]}, ...]。
    同一列内完全相同的 (弦, 品位, 哑音, 延音) 只保留一次。
    """
    items = sorted(items, key=lambda e: e[0])
    columns = []
    for cx, si, fret, dead, tied in items:
        if columns and cx - columns[-1]["x"] <= x_tol:
            col = columns[-1]
            col["x"] = (col["x"] * len(col["notes"]) + cx) / (len(col["notes"]) + 1)
            if (si, fret, dead, tied) not in col["notes"]:
                col["notes"].append((si, fret, dead, tied))
        else:
            columns.append({"x": cx, "notes": [(si, fret, dead, tied)]})
    return columns


def _split_measures(columns: list, barlines: list) -> list:
    """按小节线划分小节（同 tabparser）。"""
    if not barlines:
        return [columns]
    measures = []
    for k in range(len(barlines) + 1):
        x0 = barlines[k - 1] if k > 0 else float("-inf")
        x1 = barlines[k] if k < len(barlines) else float("inf")
        ms = [c for c in columns if x0 < c["x"] < x1]
        if ms:
            measures.append(ms)
    return measures


_TUNING_MIDI = {"A": 45, "B": 59, "C": 48, "D": 50, "E": 40, "F": 53, "G": 55}


def _letter_to_midi(letter, idx: int) -> int | None:
    """把调弦字母转成空弦 MIDI 音高。E/e 按位置消歧：最上面一弦是 e(64)，其余是 E(40)。"""
    if not letter:
        return None
    ch = letter[0].upper()
    m = _TUNING_MIDI.get(ch)
    if m is None:
        return None
    if len(letter) > 1:
        if "#" in letter:
            m += 1
        elif "B" in letter[1:]:     # 降号 b
            m -= 1
    if ch == "E":
        return 64 if idx == 0 else 40
    return m


def _tuning_from_letters(page, system) -> list | None:
    """识别谱头调弦字母（文字层单字母）。失败返回 None。"""
    gap = system.gap
    x_limit = system.barlines[0] if system.barlines else system.left + gap * 4
    letters = {}
    for w in page.get_text("words"):
        t = w[4].strip()
        if not t or t[0].upper() not in "ABCDEFG":
            continue
        cx = (w[0] + w[2]) / 2
        if not (system.left - gap * 5 <= cx <= x_limit):
            continue
        cy = (w[1] + w[3]) / 2
        dists = [abs(cy - y) for y in system.string_ys]
        si = int(min(range(len(dists)), key=dists.__getitem__))
        if dists[si] <= gap * 0.8:
            letters[si] = t
    midi = [_letter_to_midi(letters.get(i), i) for i in range(len(system.string_ys))]
    return midi if all(m is not None for m in midi) else None


def _default_duration_for_system(system, rhythm: dict) -> int:
    """找不到节奏信息时的默认时值。"""
    return 8


def extract_pdf_text_route(pdf_path: str, default_duration: int = 8):
    """文本层直读整个 PDF。

    返回 (measures: list[spec格式小节], tuning: list|None, warnings: list[str])。
    """
    import fitz
    warnings = []
    doc = fitz.open(pdf_path)
    measures = []
    tuning = None
    total_notes = 0
    try:
        for page in doc:
            systems, warns = _extract_page(page, default_duration)
            warnings.extend(warns)
            for s in systems:
                total_notes += sum(len(b["notes"]) for m in s.measures for b in m["beats"])
                if tuning is None and s.tuning:
                    tuning = s.tuning
                measures.extend(s.measures)
    finally:
        doc.close()
    # 持音留白音符的品位：继承其后同弦的第一个音符（如 m117 空白全音符 -> m118 的 (10)）
    _fill_held_frets(measures)
    if not measures:
        warnings.append("文本层未识别到任何品位数字")
    return measures, tuning, warnings


def _fill_held_frets(measures: list) -> None:
    """补齐 fret 为 None 的持音留白音符：品位 = 后面第一个同弦音符的品位。

    找不到后继同弦音符的留白音符直接删除（无法确定品位，避免生成非法文件）。
    """
    for mi, m in enumerate(measures):
        for b in m.get("beats", []):
            for n in b.get("notes", []):
                if n.get("fret") is not None or not n.get("_held"):
                    continue
                nxt = None
                for m2 in measures[mi:]:
                    for b2 in m2.get("beats", []):
                        for n2 in b2.get("notes", []):
                            if n2.get("string") == n["string"] and n2.get("fret") is not None \
                                    and n2 is not n:
                                nxt = n2["fret"]
                                break
                        if nxt is not None:
                            break
                    if nxt is not None:
                        break
                if nxt is not None:
                    n["fret"] = nxt
                else:
                    n["_drop"] = True
                n.pop("_held", None)
    for m in measures:
        for b in m.get("beats", []):
            b["notes"] = [n for n in b.get("notes", []) if not n.get("_drop")]
        m["beats"] = [b for b in m.get("beats", []) if b.get("notes") or b.get("rest")]


def _extract_page(page, default_duration: int):
    """解析一页，返回 (systems, warnings)。"""
    warnings = []
    lines = _extract_line_clusters(page)
    groups = _group_into_systems(lines)
    if not groups:
        warnings.append("未检测到六线谱弦线")
        return [], warnings

    systems = []
    for grp in groups:
        ys = [g[0] for g in grp]
        system = TextTabSystem(
            string_ys=ys,
            top=min(ys), bottom=max(ys),
            left=min(g[1] for g in grp), right=max(g[2] for g in grp),
        )
        system.barlines, span = _extract_barlines(page, system)
        if span[0] is not None:
            # 用小节线的纵向跨度重建弦线坐标：小节线精确覆盖弦1到弦N，
            # 可剔除混入系统的杂散线，并补齐漏检的弦线。
            y_lo, y_hi = span
            n = round((y_hi - y_lo) / system.gap) + 1
            n = max(4, min(8, n))
            system.string_ys = [y_lo + (y_hi - y_lo) * i / (n - 1) for i in range(n)]
            system.top, system.bottom = y_lo, y_hi
        items = _extract_digits(page, system)
        columns = _cluster_columns(items, x_tol=system.gap * 0.6)

        rhythm = _extract_rhythm(page, system)
        system.measures = _build_measures(columns, rhythm, system, default_duration)
        system.tuning = _tuning_from_letters(page, system)
        systems.append(system)

    kept = [s for s in systems if
            sum(len(b["notes"]) for m in s.measures for b in m["beats"]) > 0
            or len(s.barlines) > 0]     # 空小节（如曲尾持音）也保留，只要有小节线结构
    if not kept:
        warnings.append("本页文本层未识别到任何品位数字")
    return kept, warnings


def _build_measures(columns: list, rhythm: dict, system: TextTabSystem,
                    default_duration: int) -> list:
    """组织拍（音符列 + 延音线目标 + 休止符），匹配节奏，产出 spec 格式小节。

    滑弦(slide)、击勾弦(h/p)、延音(tie)先解析成"列 x 弦"的标记，
    再在生成拍时写入对应音符。
    """
    gap = system.gap
    stems, ticks, flags, rests, dots = (rhythm["stems"], rhythm["ticks"],
                                        rhythm["flags"], rhythm["rests"],
                                        rhythm["dots"])
    ellipses = rhythm.get("ellipses", [])
    crescents = rhythm.get("ties", [])
    slides = rhythm.get("slides", [])
    letters = rhythm.get("letters", [])

    # ---- 标记解析 ----
    col_effects = [{"slideOut": set(), "slideIn": set(), "legatoOut": set(),
                    "hopoO": set(), "hopoD": set(), "tied": set(),
                    "slideOutDown": set(), "slideOutUp": set(),
                    "slideInBelow": set(), "slideInAbove": set()} for _ in columns]

    def nearest_col(x, tol):
        best = None
        for i, col in enumerate(columns):
            d = abs(col["x"] - x)
            if d <= tol and (best is None or d < abs(columns[best]["x"] - x)):
                best = i
        return best

    def common_strings(ci, cj):
        """两列共有的弦(0基索引)。"""
        if ci is None or cj is None:
            return []
        s_i = {s for s, f, dd, td in columns[ci]["notes"]}
        s_j = {s for s, f, dd, td in columns[cj]["notes"]}
        return sorted(s_i & s_j)

    def nearest_col_any(lx, side):
        """字母某侧最近的音符列（不限弦，side<0 左, side>0 右）。"""
        best = None
        for i, col in enumerate(columns):
            d = col["x"] - lx
            if side < 0 and not (-gap * 4.5 <= d < 0):
                continue
            if side > 0 and not (0 < d <= gap * 4.5):
                continue
            if best is None or abs(d) < abs(columns[best]["x"] - lx):
                best = i
        return best

    def string_of(yc):
        dists = [abs(yc - y) for y in system.string_ys]
        si = int(min(range(len(dists)), key=dists.__getitem__))
        return si if dists[si] <= gap * 1.3 else None

    def col_note(col, si):
        for s, f, dd, td in col["notes"]:
            if s == si:
                return f
        return None

    created = []
    held_created = []
    diag_matched = set()

    for ax0, ax1, ay0, ay1 in crescents:
        ayc = (ay0 + ay1) / 2
        # 1) 弧线上方字母优先（用户规则）: h/p=击勾弦, sl/s=连奏滑音，都只标起点
        arc_letters = [(lx, ly, lt) for lx, ly, lt in letters
                       if ax0 - 10 <= lx <= ax1 + 10 and ay0 - 16 <= ly <= ay0 + 2]
        if arc_letters:
            for lx, ly, lt in arc_letters:
                oi = nearest_col_any(lx, -1)
                di2 = nearest_col_any(lx, 1)
                if oi is None:
                    continue
                sis = common_strings(oi, di2) if di2 is not None else []
                if not sis:
                    # 终点音符缺失（滑向空白）时按弧线所在弦定位起点
                    si = string_of(ayc)
                    if si is not None and col_note(columns[oi], si) is not None:
                        sis = [si]
                for si in sis:
                    if lt in ("h", "p"):
                        if di2 is not None and di2 != oi:
                            col_effects[oi]["hopoO"].add(si)
                            col_effects[di2]["hopoD"].add(si)
                    else:      # sl / s: 连奏滑音只标起点，终点不管
                        col_effects[oi]["legatoOut"].add(si)
                        # 连奏滑音的斜线压在弧线下方（可延伸到弧线外约3gap），
                        # 消费掉避免误判为普通滑弦（h/p 弧线不消费，避免误吞附近小斜线）
                        for dj, (ex0, ex1, ey0, ey1) in enumerate(slides):
                            if ax0 - gap <= ex0 <= ax1 + gap * 3 \
                                    and ax0 - gap * 3 <= ex1 <= ax1 + gap * 3:
                                diag_matched.add(dj)
            continue
        # 2) 弧线 + 斜线成对 = 连奏滑音（字母漏检时的兜底，只标起点）
        diag = None
        for di, (dx0, dx1, dy0, dy1) in enumerate(slides):
            if abs((dy0 + dy1) / 2 - ayc) <= gap * 1.4 and abs(dx0 - ax0) <= gap * 1.3 \
                    and abs(dx1 - ax1) <= gap * 2.5:
                diag = di
                break
        if diag is not None:
            diag_matched.add(diag)
            dx0, dx1, dy0, dy1 = slides[diag]
            oi = nearest_col(dx0, gap * 1.5)
            di2 = nearest_col(dx1, gap * 1.5)
            si = string_of((dy0 + dy1) / 2)
            if si is None or oi is None or col_note(columns[oi], si) is None:
                cs = common_strings(oi, di2)
                si = cs[0] if cs else None
            if si is not None and oi is not None and col_note(columns[oi], si) is not None:
                col_effects[oi]["legatoOut"].add(si)
            continue
        # 3) 无标记弧线: 同品位=延音，不同品位=击勾弦（字母未导出的兜底），空白=延音
        # 弧线可能画在弦线上方或下方（双音延音一上一下），取上下边缘中更贴近弦线的一侧定弦
        d_top = min(abs(ay0 - y) for y in system.string_ys)
        d_bot = min(abs(ay1 - y) for y in system.string_ys)
        si = string_of(ay0 if d_top <= d_bot else ay1)
        if si is None:
            continue
        oi = nearest_col(ax0, gap * 2)
        if oi is None or col_note(columns[oi], si) is None:
            # 弧线左端无品位数字：可能是"持音留白"（全/二分音符数字留白，品位继承延音目标）
            ell = None
            for ex0, ex1, has_line in ellipses:
                if ex0 - gap <= ax0 <= ex1 + gap and abs((ex0 + ex1) / 2 - ax0) <= gap * 2:
                    ell = (ex0, ex1, has_line)
                    break
            if ell is not None:
                ex0, ex1, has_line = ell
                di2 = nearest_col(ax1, gap * 1.2)
                df = col_note(columns[di2], si) if di2 is not None else None
                held = {"duration": 2 if has_line else 1,
                        "notes": [{"string": si + 1,
                                   "fret": df if df is not None else None,
                                   "_held": True}]}
                if df is not None:
                    col_effects[di2]["tied"].add(si)
                held_created.append(((ex0 + ex1) / 2, held))
            continue
        of = col_note(columns[oi], si)
        di2 = nearest_col(ax1, gap * 1.2)
        if di2 is not None and di2 != oi:
            df = col_note(columns[di2], si)
            if df is not None:
                if df != of:
                    col_effects[oi]["hopoO"].add(si)
                    col_effects[di2]["hopoD"].add(si)
                else:
                    col_effects[di2]["tied"].add(si)
                continue
        dest_x = ax1 + gap * 0.9
        if nearest_col(dest_x, gap * 0.7) is None:
            created.append((dest_x, {"duration": 0,   # 稍后按起点拍补齐
                                     "notes": [{"string": si + 1, "fret": of,
                                                "tied": True, "_src": oi}]}))

    # 4) 未与弧线配对的斜线：
    #    两端都有同弦音符 = 普通滑弦（shift: 起点滑出 + 终点滑入）
    #    只有一端有音符 = 无头无尾滑音（音符前=滑入，音符后=滑出，按方向分上行/下行）
    for di, (dx0, dx1, dy0, dy1) in enumerate(slides):
        if di in diag_matched:
            continue
        si = string_of((dy0 + dy1) / 2)
        if si is None:
            continue
        oi = nearest_col(dx0, gap * 1.5)
        di2 = nearest_col(dx1, gap * 1.5)
        # 两端落到同一列时按距离判断归属侧（小斜线可能整体贴在一个音符旁）
        if oi is not None and di2 is not None and oi == di2:
            if abs(columns[oi]["x"] - dx0) <= abs(columns[di2]["x"] - dx1):
                di2 = None
            else:
                oi = None
        of = col_note(columns[oi], si) if oi is not None else None
        df = col_note(columns[di2], si) if di2 is not None else None
        if of is not None and df is not None and of != df:
            col_effects[oi]["slideOut"].add(si)
            col_effects[di2]["slideIn"].add(si)
        elif of is not None and df is None:
            # 音符在斜线左侧 = 无尾滑出：向右下=下行, 向右上=上行
            if dy1 > dy0:
                col_effects[oi]["slideOutDown"].add(si)
            else:
                col_effects[oi]["slideOutUp"].add(si)
        elif of is None and df is not None:
            # 音符在斜线右侧 = 无头滑入：斜线向音符扬起=上行(自下而上), 落下=下行(自上而下)
            if dy1 < dy0:
                col_effects[di2]["slideInBelow"].add(si)
            else:
                col_effects[di2]["slideInAbove"].add(si)

    # ---- 生成拍 ----
    def beat_for(i, col):
        x = col["x"]
        dur = default_duration
        # 全音符(椭圆) / 二分音符(椭圆+下方短线)
        ellipse_line = None
        for ex0, ex1, has_line in ellipses:
            if ex0 - 2 <= x <= ex1 + 2:
                ellipse_line = has_line
                break
        if ellipse_line is not None:
            dur = 2 if ellipse_line else 1
        else:
            # 符尾字形（独立八分/十六分）
            best = None
            for fx, fd in flags.items():
                if abs(fx - x) <= gap * 1.2 and (best is None or fd > best):
                    best = fd
            if best is not None:
                dur = best
            else:
                # 符干末端短竖线: 1 层 = 八分, >=2 层 = 十六分
                levels = 0
                for tx, tys in ticks.items():
                    if abs(tx - x) <= gap * 0.9:
                        levels = max(levels, len(set(tys)))
                if levels >= 2:
                    dur = 16
                elif levels == 1:
                    dur = 8
                elif any(abs(sx - x) <= gap * 0.9 for sx in stems):
                    dur = 4
        eff = col_effects[i]
        beat_notes = []
        for si, fr, dead, tied in col["notes"]:
            n = {"string": si + 1, "fret": fr}
            if dead:
                n["dead"] = True
            if tied or si in eff["tied"]:
                n["tied"] = True
            if si in eff["hopoO"]:
                n["hopoOrigin"] = True
            if si in eff["hopoD"]:
                n["hopoDestination"] = True
            if si in eff["slideOut"]:
                n["slideOut"] = True
            if si in eff["slideIn"]:
                n["slideIn"] = True
            if si in eff["legatoOut"]:
                n["legatoSlideOut"] = True
            if si in eff["slideOutDown"]:
                n["slideOutDown"] = True
            if si in eff["slideOutUp"]:
                n["slideOutUp"] = True
            if si in eff["slideInBelow"]:
                n["slideInBelow"] = True
            if si in eff["slideInAbove"]:
                n["slideInAbove"] = True
            beat_notes.append(n)
        beat = {"duration": dur, "notes": beat_notes}
        if any(abs(dx - x) <= gap * 1.2 for dx in dots):
            beat["dotted"] = True
        return beat

    # 1. 音符拍
    entries = [(col["x"], beat_for(i, col)) for i, col in enumerate(columns)]

    # 2. 延音线空白目标：补齐时值（与起点拍一致）
    for dest_x, beat in created:
        src = beat["notes"][0].pop("_src")
        beat["duration"] = entries[src][1]["duration"]
        entries.append((dest_x, beat))
    # 2b. 持音留白音符（品位数字留白，品位待跨系统补齐）
    for hx, beat in held_created:
        entries.append((hx, beat))
    # 合并位置相近的延音目标拍（双音同时延音应在同一拍）
    entries.sort(key=lambda e: e[0])
    merged = []
    for x, beat in entries:
        if beat["notes"] and beat["notes"][0].get("tied") and merged \
                and merged[-1][1]["notes"] and merged[-1][1]["notes"][0].get("tied") \
                and x - merged[-1][0] <= gap * 0.6:
            px, pbeat = merged[-1]
            for n in beat["notes"]:
                if n not in pbeat["notes"]:
                    pbeat["notes"].append(n)
            merged[-1] = ((px + x) / 2, pbeat)
        else:
            merged.append((x, beat))
    entries = merged

    # 3. 休止拍（不与音符/延音拍重叠）
    note_xs = [x for x, _ in entries]
    for rx, rd in rests:
        if any(abs(rx - nx) <= gap * 0.5 for nx in note_xs):
            continue
        entries.append((rx, {"duration": rd, "rest": True, "notes": []}))

    # 3a. 梁线组内拍统一时值：按拍统计覆盖的梁条数（1 条=八分，2 条=十六分）
    beam_bars = {}
    for bx0, bx1, by in rhythm.get("beams", []):
        key = (round(bx0, 1), round(bx1, 1))
        beam_bars.setdefault(key, []).append(by)
    for x, beat in entries:
        if not beat.get("notes") or beat.get("tupletNumerator"):
            continue
        ys = []
        for (bx0, bx1), ylist in beam_bars.items():
            if bx0 - 1 <= x <= bx1 + 1:
                ys.extend(ylist)
        if not ys:
            continue
        ys = sorted({round(y, 1) for y in ys})
        bars = 0
        while ys:
            y0 = ys[0]
            bars += 1
            ys = [y for y in ys if y - y0 > 2.6]
        beat["duration"] = 16 if bars >= 2 else 8

    # 3b. 连音号(三连音): 括号范围内的拍 → tuplet n:d
    #     组内时值由梁线层数决定（无梁线=四分，1 层梁=八分，2 层梁=十六分）
    TUPLET_DENOMS = {2: 3, 3: 2, 4: 3, 5: 4, 6: 4, 7: 4, 8: 6, 9: 8}
    for tx0, tx1, tn in rhythm.get("tuplets", []):
        grp = [e for e in entries if tx0 - 1 <= e[0] <= tx1 + 1 and e[1].get("notes")]
        if len(grp) < 2:
            continue
        # 梁线：系统下方 10~19pt 的横向线段，覆盖整个组
        # 一条梁=矩形(上下两条线相距约2.2pt)，按条数聚类数层级
        beam_ys = sorted({round(dy, 1) for dx0, dx1, dy in rhythm.get("beams", [])
                          if dx0 <= tx0 + 2 and dx1 >= tx1 - 2})
        bars = 0
        while beam_ys:
            y0 = beam_ys[0]
            bars += 1
            beam_ys = [y for y in beam_ys if y - y0 > 2.6]
        dur = 16 if bars >= 2 else 8 if bars == 1 else 4
        for _x, beat in grp:
            beat["tupletNumerator"] = tn
            beat["tupletDenominator"] = TUPLET_DENOMS.get(tn, 2)
            beat["duration"] = dur

    # 3c. 右手闷音 P.M.: 虚线范围内的拍全部标记 palmMute
    for px0, px1 in rhythm.get("pm_ranges", []):
        for x, beat in entries:
            if px0 <= x <= px1 and beat.get("notes"):
                for n in beat["notes"]:
                    n["palmMute"] = True

    # 4. 按小节线分组（空小节保留，保证小节编号对齐）
    out = []
    edge = system.right - system.gap   # 距右缘一个弦距内的结尾小节线不算分节（乐谱收尾处）
    barlines = [b for b in system.barlines if b < edge]
    n_ranges = len(barlines) + 1
    for mi in range(n_ranges):
        x0 = barlines[mi - 1] if mi > 0 else float("-inf")
        x1 = barlines[mi] if mi < len(barlines) else float("inf")
        ms_entries = [e for e in entries if x0 < e[0] < x1]
        if not ms_entries:
            out.append({"timeSignature": {"numerator": 4, "denominator": 4},
                        "beats": []})
            continue
        ms_entries.sort(key=lambda e: e[0])
        out.append({"timeSignature": {"numerator": 4, "denominator": 4},
                    "beats": [b for _, b in ms_entries]})
    return out
