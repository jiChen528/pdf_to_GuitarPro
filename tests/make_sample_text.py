# -*- coding: utf-8 -*-
"""生成合成"文字层"六线谱 PDF（模拟 Guitar Pro 矢量导出），用于文本路线测试。

结构: 6 弦(弦距 8pt) + 细高矩形小节线 + 文字层品位数字 + 调弦字母 +
      符干(四分) / 符干末端短竖线(八分)。
"""
from __future__ import annotations

import os

import fitz

HERE = os.path.dirname(os.path.abspath(__file__))
PDF_PATH = os.path.join(HERE, "sample_text_tab.pdf")

YS = [100, 108, 116, 124, 132, 140]          # 6 弦 y
BARLINES = [220, 400]

# 每小节: [(x, 弦索引0起, 品位或"X"或"(n)", 时值)]
# 时值: 1=全音符(椭圆) 2=二分音符(椭圆+下方短线) 4=符干 8=符干+tick
MEASURES = [
    [(80, 0, 3, 4), (80, 2, 2, 4), (130, 1, 5, 8), (130, 3, 7, 8), (180, 4, 7, 2)],
    [(240, 1, 4, 4), (270, 1, 6, 4), (295, 0, 0, 4), (295, 3, 2, 4), (310, 5, 12, 8), (330, 5, 14, 8), (345, 2, "(2)", 4), (355, 1, 3, 4), (375, 1, 5, 4), (395, 1, 7, 4)],
    [(420, 0, 9, 4), (450, 0, 10, 8), (450, 2, 7, 8), (475, 3, 5, 8), (495, 3, 7, 8), (510, 1, 0, 1), (540, 5, "X", 4)],
]

# 延音线: (x左, 弦索引0起, x右)，目标为空白（延用左端音）；双音延音在两条弦上
TIES = [(130, 1, 160), (130, 3, 160)]
# 普通滑弦: 单独斜线连接两个不同品位（12 -> 14）
SLIDES = [(310, 5, 330)]
# 击勾弦: 月牙弧连接两个不同品位（5 -> 7），上方有 "H" 字母
HOPOS = [(470, 3, 500)]
HOPO_LETTERS = [(485, 3, "H")]
# 连奏滑音: 月牙弧 + 斜线 + 上方 "sl." 字母（4 -> 6），只标起点
LEGATO_SLIDES = [(240, 1, 270)]

# 无头无尾滑音: (x, 弦索引0起, 侧, 方向)
# 侧 'after'=音符后(滑出), 'before'=音符前(滑入)；方向 'down'=向右下行, 'up'=向右上行
SHORT_SLIDES = [
    (180, 4, "after", "down"),     # 音符后向下 = 滑出(下行) OutDownwards
    (540, 5, "after", "up"),       # 音符后向上 = 滑出(上行) OutUpwards
    (450, 0, "before", "up"),      # 音符前向上 = 滑入(上行) IntoFromBelow
    (420, 0, "before", "down"),    # 音符前向下 = 滑入(下行) IntoFromAbove
]

# 右手闷音 P.M.: (词x, 词基线下, 虚线起x, 虚线止x)
PM_MARKS = [(50, 93, 72, 118)]
# 三连音: (数字x, 括号起x, 括号止x)  数字与括号画在系统下方
TRIPLETS = [(375, 353, 398)]

# 期望值（供测试断言）
EXPECTED = {
    "measures": 3,
    "notes": 24,
    "beats": {1: 1, 2: 1, 4: 10, 8: 7},   # 1 全 + 1 二分 + 10 四分 + 7 八分
    "tuning": [64, 59, 55, 50, 45, 40],
    "frets": [0, 2, 3, 4, 5, 6, 7, 9, 10, 12, 14],
    "mutes": 1,
    "tied": 3,      # 括号目标 1 + 空白双音目标 2
    "slides": 2,    # 普通滑弦起点+终点
    "hopo": 2,      # 击勾弦起点+终点
    "legato": 1,    # 连奏滑音起点（终点不标）
    "slide_out_down": 1,   # 无尾滑出(下行)
    "slide_out_up": 1,     # 无尾滑出(上行)
    "slide_in_below": 1,   # 无头滑入(上行)
    "slide_in_above": 1,   # 无头滑入(下行)
    "palm_mute": 2,        # 右手闷音音符数（虚线范围内的和弦两音）
    "tuplets": 3,          # 三连音拍数（3 个四分 3:2）
}


def make_text_pdf(path: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=600, height=250)

    # 弦线
    for y in YS:
        page.draw_line(fitz.Point(60, y), fitz.Point(560, y), color=(0, 0, 0), width=1)

    # 小节线（细高矩形，精确覆盖弦1到弦6——同 GP8 导出规范，宽约 0.7pt）
    for x in BARLINES:
        page.draw_rect(fitz.Rect(x - 0.35, YS[0], x + 0.35, YS[-1]), color=(0, 0, 0), fill=(0, 0, 0))

    # 调弦字母
    for i, letter in enumerate(["e", "B", "G", "D", "A", "E"]):
        page.insert_text(fitz.Point(30, YS[i] + 3), letter, fontsize=9)

    # 品位数字（基线放在弦线上方 3pt，模拟 GP8 导出的字位）
    for ms in MEASURES:
        for x, si, fret, _dur in ms:
            page.insert_text(fitz.Point(x, YS[si] + 3), str(fret), fontsize=9)

    # 节奏符号
    bottom = YS[-1]
    for ms in MEASURES:
        for x, si, fret, dur in ms:
            if dur in (1, 2):
                # 全音符/二分音符: 椭圆包围品位
                page.draw_oval(fitz.Rect(x - 9, YS[si] - 9, x + 9, YS[si] + 9),
                               color=(0, 0, 0), width=1)
                if dur == 2:
                    # 二分音符: 椭圆下方一根短竖线（位于谱表下方）
                    page.draw_line(fitz.Point(x, bottom + 16), fitz.Point(x, bottom + 24),
                                   color=(0, 0, 0), width=1)
                continue
            # 每拍一根符干（从谱表下方伸入谱表）
            page.draw_line(fitz.Point(x, bottom + 2), fitz.Point(x, bottom + 32),
                           color=(0, 0, 0), width=1)
            if dur == 8:
                # 八分: 符干末端 2pt 短竖线
                page.draw_line(fitz.Point(x, bottom + 13), fitz.Point(x, bottom + 15),
                               color=(0, 0, 0), width=1)

    # 延音线（细长月牙：两段贝塞尔曲线闭合）
    for xl, si, xr in TIES:
        y = YS[si]
        w = xr - xl
        page.draw_bezier(fitz.Point(xl + 2, y),
                         fitz.Point(xl + w * 0.25, y - 4),
                         fitz.Point(xr - w * 0.25, y - 4),
                         fitz.Point(xr, y))
        page.draw_bezier(fitz.Point(xr, y),
                         fitz.Point(xr - w * 0.25, y - 3),
                         fitz.Point(xl + w * 0.25, y - 3),
                         fitz.Point(xl + 2, y))

    # 普通滑弦（斜线段）
    for xl, si, xr in SLIDES:
        y = YS[si]
        page.draw_line(fitz.Point(xl + 3, y + 2), fitz.Point(xr - 3, y - 2),
                       color=(0, 0, 0), width=1)

    # 击勾弦（月牙弧）+ 上方字母
    for xl, si, xr in HOPOS:
        y = YS[si]
        w = xr - xl
        page.draw_bezier(fitz.Point(xl + 3, y),
                         fitz.Point(xl + w * 0.3, y - 3),
                         fitz.Point(xr - w * 0.3, y - 3),
                         fitz.Point(xr - 3, y))
        page.draw_bezier(fitz.Point(xr - 3, y),
                         fitz.Point(xr - w * 0.3, y - 2),
                         fitz.Point(xl + w * 0.3, y - 2),
                         fitz.Point(xl + 3, y))
    for lx, si, lt in HOPO_LETTERS:
        page.insert_text(fitz.Point(lx, YS[si] - 5), lt, fontsize=9)

    # 连奏滑音: 月牙弧 + 斜线 + 上方 "sl." 字母
    for xl, si, xr in LEGATO_SLIDES:
        y = YS[si]
        w = xr - xl
        page.draw_bezier(fitz.Point(xl + 2, y - 3),
                         fitz.Point(xl + w * 0.25, y - 7),
                         fitz.Point(xr - w * 0.25, y - 7),
                         fitz.Point(xr, y - 3))
        page.draw_bezier(fitz.Point(xr, y - 3),
                         fitz.Point(xr - w * 0.25, y - 6),
                         fitz.Point(xl + w * 0.25, y - 6),
                         fitz.Point(xl + 2, y - 3))
        page.draw_line(fitz.Point(xl + 3, y + 2), fitz.Point(xr - 3, y - 2),
                       color=(0, 0, 0), width=1)
        page.insert_text(fitz.Point((xl + xr) / 2, y - 5), "sl.", fontsize=9)

    # 无头无尾滑音（小斜线，无弧线）
    for x, si, side, direction in SHORT_SLIDES:
        y = YS[si]
        if side == "after":
            x0, x1 = x + 3, x + 8
            y0, y1 = (y + 0.5, y + 4.5) if direction == "down" else (y + 4.5, y + 0.5)
        else:
            x0, x1 = x - 7, x - 2
            y0, y1 = (y + 4.5, y + 0.5) if direction == "up" else (y + 0.5, y + 4.5)
        page.draw_line(fitz.Point(x0, y0), fitz.Point(x1, y1),
                       color=(0, 0, 0), width=1)

    # 右手闷音 P.M.: 顶线上方约 1 弦距处的词 + 虚线范围
    for wx, wy, dx0, dx1 in PM_MARKS:
        page.insert_text(fitz.Point(wx, wy), "P.M.", fontsize=9)
        page.draw_line(fitz.Point(dx0, YS[0] - 8), fitz.Point(dx1, YS[0] - 8),
                       color=(0, 0, 0), width=1)

    # 三连音: 系统下方的数字 + 实线括号
    bottom = YS[-1]
    for tx, bx0, bx1 in TRIPLETS:
        by = bottom + 24
        page.insert_text(fitz.Point(tx - 1.5, by + 3), "3", fontsize=9)
        page.draw_line(fitz.Point(bx0, by), fitz.Point(tx - 7, by), color=(0, 0, 0), width=1)
        page.draw_line(fitz.Point(tx + 7, by), fitz.Point(bx1, by), color=(0, 0, 0), width=1)
        page.draw_line(fitz.Point(bx0, by), fitz.Point(bx0, by - 2.5), color=(0, 0, 0), width=1)
        page.draw_line(fitz.Point(bx1, by), fitz.Point(bx1, by - 2.5), color=(0, 0, 0), width=1)

    doc.save(path)
    doc.close()


if __name__ == "__main__":
    make_text_pdf(PDF_PATH)
    print("已生成:", PDF_PATH)
