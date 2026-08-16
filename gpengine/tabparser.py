# -*- coding: utf-8 -*-
"""图片谱面识别模块：从谱面图片重建六线谱结构。

流程:
    图片 -> 二值化 -> 检测弦线(横向直线)并按等距分组为"谱表系统"
         -> 检测小节线(竖向直线)
         -> 逐弦条带裁剪 + OCR 识别品位数字 -> 按 x 聚类成"拍"(和弦)
         -> 按小节线划分小节 -> 识别谱头调弦字母
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


# 常见调弦的默认 MIDI 音高（自上而下，1 弦 -> 6 弦）
DEFAULT_TUNINGS = {
    6: [64, 59, 55, 50, 45, 40],   # 标准调弦 E A D G B E
    4: [55, 50, 45, 40],           # 贝斯 G D A E
}

# 调弦字母 -> 空弦 MIDI 音高（调弦语境，非通用音名）
_TUNING_MIDI = {"A": 45, "B": 59, "C": 48, "D": 50, "E": 40, "F": 53, "G": 55}

# OCR 常见误读 -> 数字
_DIGIT_FIX = {
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "I": "1", "l": "1", "i": "1", "L": "1", "|": "1",
    "Z": "2", "z": "2", "子": "2", "已": "2",
    "S": "5", "s": "5",
    "b": "6", "G": "6", "g": "9", "q": "9",
    "B": "8", "T": "7",
}


def _normalize_digit(text: str) -> str | None:
    """把 OCR 文本归一化成 1~2 位数字，无法归一化返回 None。"""
    t = str(text).strip()
    if not t:
        return None
    if t.isdigit() and len(t) <= 2:
        return t
    fixed = "".join(_DIGIT_FIX.get(ch, ch) for ch in t)
    if fixed.isdigit() and len(fixed) <= 2:
        return fixed
    return None


def _is_mute_mark(text: str) -> bool:
    """x / X / × = 哑音（左手闷音）记号，GP8 tab 中显示为小 x。"""
    t = str(text).strip()
    return len(t) == 1 and t.lower() in ("x", "×")


@dataclass
class TabSystem:
    """一组六线谱（若干条等距横线组成的一个谱表系统）。"""
    string_ys: list                                # 各弦 y 坐标（自上而下 = 弦 1..N）
    top: float
    bottom: float
    left: float
    right: float
    barlines: list = field(default_factory=list)   # 小节线 x 坐标（升序）
    tuning: list | None = None                     # 各弦 MIDI 音高（自上而下），None=未识别
    measures: list = field(default_factory=list)   # [[{'x':..,'notes':[(弦索引0起, 品位),...]}, ...], ...]

    @property
    def gap(self) -> float:
        """相邻两弦的间距（像素）。"""
        n = len(self.string_ys)
        return (self.string_ys[-1] - self.string_ys[0]) / (n - 1) if n > 1 else 0.0

    @property
    def total_notes(self) -> int:
        return sum(len(col["notes"]) for ms in self.measures for col in ms)


# ---------------------------------------------------------------------------
# 图像预处理与直线检测
# ---------------------------------------------------------------------------

def _binarize(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    return cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, 41, 12)


def _horizontal_lines(binv: np.ndarray):
    """检测横向直线，返回 [(y, x1, x2), ...]（按 y 升序，近似重复的线已合并）。"""
    h, w = binv.shape
    lines = cv2.HoughLinesP(binv, 1, np.pi / 180, threshold=100,
                            minLineLength=int(w * 0.22), maxLineGap=8)
    raw = []
    if lines is None:
        return raw
    segs = lines if lines.ndim == 2 else lines[:, 0]   # 兼容 OpenCV 4/5 返回形状
    for x1, y1, x2, y2 in segs:
        if abs(y1 - y2) > 3:      # 只保留接近水平的线
            continue
        raw.append((float(y1 + y2) / 2, float(min(x1, x2)), float(max(x1, x2))))
    raw.sort(key=lambda t: t[0])
    merged = []
    for y, x1, x2 in raw:
        if merged and abs(y - merged[-1][0]) <= 4:
            py, px1, px2 = merged[-1]
            merged[-1] = ((py + y) / 2, min(px1, x1), max(px2, x2))
        else:
            merged.append((y, x1, x2))
    return merged


def _group_into_systems(hlines):
    """把相邻间距相近的横线分组为谱表系统。返回 [[line, ...], ...]。

    组首/组尾间距偏离中位数 > 15% 的杂散线会被剔除。
    """
    groups = []
    if len(hlines) < 4:
        return groups
    run = [hlines[0]]
    for i in range(1, len(hlines)):
        gap = hlines[i][0] - hlines[i - 1][0]
        if len(run) > 1:
            avg = np.mean([hlines[j][0] - hlines[j - 1][0] for j in range(1, len(run))])
            keep = gap <= max(avg * 1.7, avg + 15)
        else:
            keep = gap <= 120
        if keep:
            run.append(hlines[i])
        else:
            groups.append(run)
            run = [hlines[i]]
    groups.append(run)

    result = []
    # 页级主导弦距：过滤间距远小于主导值的组（和弦指法图、五线谱等）。
    # 注意：必须保持各组原始的 y 顺序（即阅读顺序），不能排序！
    medians = []
    for grp in groups:
        if len(grp) >= 4:
            gaps = [grp[i][0] - grp[i - 1][0] for i in range(1, len(grp))]
            medians.append(float(np.median(gaps)))
        else:
            medians.append(None)
    dominant = max((m for m in medians if m is not None), default=None)
    for grp, med in zip(groups, medians):
        if med is None or len(grp) < 4:
            continue
        if dominant and med < dominant * 0.65:
            continue      # 和弦指法图 / 五线谱等小间距线条组
        grp = _trim_stray_lines(grp)
        if len(grp) >= 4:
            result.append(grp)
    return result


def _trim_stray_lines(lines):
    """剔除组首/组尾间距偏离中位数 > 15% 的杂散线（如谱面上方的注释线）。"""
    while len(lines) >= 5:
        gaps = [lines[i][0] - lines[i - 1][0] for i in range(1, len(lines))]
        med = float(np.median(gaps))
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


def _vertical_lines(binv: np.ndarray, system: TabSystem) -> list:
    """检测穿过该系统的小节线，返回 x 坐标列表（升序，近似重复的已合并）。"""
    lines = cv2.HoughLinesP(binv, 1, np.pi / 180, threshold=80,
                            minLineLength=int((system.bottom - system.top) * 0.5),
                            maxLineGap=10)
    xs = []
    if lines is None:
        return xs
    segs = lines if lines.ndim == 2 else lines[:, 0]   # 兼容 OpenCV 4/5 返回形状
    for x1, y1, x2, y2 in segs:
        if abs(x1 - x2) > 4:      # 只保留接近垂直的线
            continue
        ymin, ymax = min(y1, y2), max(y1, y2)
        cx = float(x1 + x2) / 2
        covers = (ymax - ymin) >= (system.bottom - system.top) * 0.5
        overlaps = ymin <= system.top + 10 and ymax >= system.bottom - 10
        if covers and overlaps and system.left - 30 <= cx <= system.right + 30:
            xs.append(cx)
    xs.sort()
    merged = []
    for x in xs:
        if merged and x - merged[-1] <= 6:
            merged[-1] = (merged[-1] + x) / 2
        else:
            merged.append(x)
    return merged


# ---------------------------------------------------------------------------
# 品位数字识别（逐弦条带 OCR）
# ---------------------------------------------------------------------------

def _ocr_digit_bands(img: np.ndarray, ocr, system: TabSystem) -> list:
    """逐弦裁剪条带并 OCR，返回 [(cx, 弦索引, 品位), ...]。

    条带裁剪把纵向相邻的和弦音隔离开，避免检测框互相干扰；
    从弦线起点裁剪，天然排除左侧的调弦字母。
    """
    gap = system.gap
    items = []
    x0 = int(system.left)
    x1 = min(img.shape[1], int(system.right) + int(gap))
    if x1 - x0 < 30:
        return items
    for si, y in enumerate(system.string_ys):
        y0 = max(0, int(y - gap * 0.45))
        y1 = min(img.shape[0], int(y + gap * 0.45))
        result, _ = ocr(img[y0:y1, x0:x1])
        if not result:
            continue
        for box, text, score in result:
            t = _normalize_digit(str(text))
            is_mute = _is_mute_mark(str(text))
            if (t is None and not is_mute) or score < 0.5:
                continue
            pts = np.asarray(box, dtype=np.float32)
            cx = float(pts[:, 0].mean()) + x0
            cy_local = float(pts[:, 1].mean())
            line_y_local = y - y0
            h = float(pts[:, 1].max() - pts[:, 1].min())
            w = float(pts[:, 0].max() - pts[:, 0].min())
            if h > gap * 1.6 or w > gap * 2.2:   # 尺寸过大的数字（页码等）丢弃
                continue
            if cy_local < line_y_local - gap * 0.3:  # 弦线上方：小节号等标记，丢弃
                continue
            items.append((cx, si, 0 if is_mute else int(t), is_mute))
    return items


def _cluster_columns(items: list, x_tol: float) -> list:
    """按 x 把数字聚类成纵向的拍（和弦）。

    返回 [{'x': 平均x, 'notes': [(弦索引, 品位, is_palm_mute), ...]}, ...]。
    同一列内完全相同的 (弦, 品位, 闷音) 只保留一次（OCR 重复识别）。
    """
    items = sorted(items, key=lambda e: e[0])
    columns = []
    for cx, si, fret, palm in items:
        if columns and cx - columns[-1]["x"] <= x_tol:
            col = columns[-1]
            col["x"] = (col["x"] * len(col["notes"]) + cx) / (len(col["notes"]) + 1)
            if (si, fret, palm) not in col["notes"]:
                col["notes"].append((si, fret, palm))
        else:
            columns.append({"x": cx, "notes": [(si, fret, palm)]})
    return columns


def _split_measures(columns: list, barlines: list) -> list:
    """按小节线把拍划分成小节。"""
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


# ---------------------------------------------------------------------------
# 调弦识别
# ---------------------------------------------------------------------------

def _letter_to_midi(letter: str | None, idx: int) -> int | None:
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


def _detect_tuning(img: np.ndarray, ocr, system: TabSystem) -> list | None:
    """识别谱表左侧（第一条小节线之前）的调弦字母。

    返回各弦 MIDI 音高（自上而下）；识别不完整则返回 None。
    """
    gap = system.gap
    x1 = int(system.barlines[0]) if system.barlines else int(system.left + gap * 3)
    x0 = max(0, int(system.left) - int(gap * 2))
    y0 = max(0, int(system.top) - int(gap * 0.8))
    y1 = min(img.shape[0], int(system.bottom) + int(gap * 0.8))
    if x1 - x0 < 20:
        return None
    result, _ = ocr(img[y0:y1, x0:x1])
    letters = {}
    if not result:
        return None
    for box, text, score in result:
        t = str(text).strip()
        if not t or t[0].upper() not in "ABCDEFG":
            continue
        pts = np.asarray(box, dtype=np.float32)
        cy = float(pts[:, 1].mean()) + y0
        dists = [abs(cy - y) for y in system.string_ys]
        si = int(np.argmin(dists))
        if dists[si] <= gap * 0.8:
            letters[si] = t
    midi = [_letter_to_midi(letters.get(i), i) for i in range(len(system.string_ys))]
    return midi if all(m is not None for m in midi) else None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def parse_tab_image(img: np.ndarray, ocr=None, min_digits_per_system: int = 3):
    """把一张谱面图片解析为谱表系统列表。

    返回 (systems: list[TabSystem], warnings: list[str])。
    ocr 传入 RapidOCR 实例可复用；min_digits_per_system 用于过滤
    误检的五线谱系统（标准记谱的谱表上几乎没有品位数字）。
    """
    warnings = []
    binv = _binarize(img)
    hlines = _horizontal_lines(binv)
    groups = _group_into_systems(hlines)
    if not groups:
        warnings.append("未检测到六线谱弦线（横线）")
        return [], warnings

    systems = []
    for grp in groups:
        ys = [g[0] for g in grp]
        system = TabSystem(
            string_ys=ys,
            top=float(min(ys)), bottom=float(max(ys)),
            left=float(min(g[1] for g in grp)), right=float(max(g[2] for g in grp)),
        )
        system.barlines = _vertical_lines(binv, system)
        items = _ocr_digit_bands(img, ocr, system)
        columns = _cluster_columns(items, x_tol=system.gap * 0.6)
        system.measures = _split_measures(columns, system.barlines)
        system.tuning = _detect_tuning(img, ocr, system)
        systems.append(system)

    kept = [s for s in systems if s.total_notes >= min_digits_per_system]
    if not kept and systems:
        best = max(systems, key=lambda s: s.total_notes)
        if best.total_notes > 0:
            kept = [best]
            warnings.append("品位数字较少，可能是五线谱或图片质量不佳，结果仅供参考")
    if not kept:
        warnings.append("整页未识别到任何品位数字")
    return kept, warnings


def system_to_spec_measures(system: TabSystem, duration: int = 8,
                            numerator: int = 4, denominator: int = 4) -> list:
    """把谱表系统的小节转成 spec 的 measures 结构。"""
    measures = []
    for ms in system.measures:
        beats = []
        for col in ms:
            notes = []
            for si, fr, dead in col["notes"]:
                n = {"string": si + 1, "fret": fr}
                if dead:
                    n["dead"] = True
                notes.append(n)
            beats.append({"duration": duration, "notes": notes})
        measures.append({
            "timeSignature": {"numerator": numerator, "denominator": denominator},
            "beats": beats,
        })
    return measures
