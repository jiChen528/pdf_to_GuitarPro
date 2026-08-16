# -*- coding: utf-8 -*-
"""生成一张合成的六线谱测试图片，并打包成 PDF（tests/sample_tab.png / .pdf）。

谱面内容（用于测试断言）:
    6 弦标准调弦，3 个小节，共 23 个品位数字、9 拍。
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
PNG_PATH = os.path.join(HERE, "sample_tab.png")
PDF_PATH = os.path.join(HERE, "sample_tab.pdf")

W, H = 1800, 700
Y0, GAP = 120, 45
LINE_X0, LINE_X1 = 180, 1700
BARLINES = [300, 700, 1100, 1500]

# 每小节: {弦索引(0=最上面): [(x, "品位"), ...]}
MEASURES = [
    {0: [(380, "0"), (470, "1"), (560, "3")],
     1: [(470, "1")],
     2: [(380, "0"), (470, "2"), (560, "2")],
     3: [(560, "2")],
     4: [(380, "2"), (470, "3")],
     5: [(380, "3")]},
    {0: [(780, "3"), (870, "3"), (960, "5"), (1050, "5")],
     1: [(780, "0"), (870, "0")],
     2: [(780, "0"), (870, "0")],
     5: [(780, "3"), (870, "3")]},
    {5: [(1180, "12")], 4: [(1270, "10")]},
]

# 期望值（供测试断言）
EXPECTED = {"measures": 3, "notes": 23, "beats": 9}


def draw_tab() -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except Exception:
        font = ImageFont.load_default()

    ys = [Y0 + i * GAP for i in range(6)]

    # 弦线
    for y in ys:
        d.line([(LINE_X0, y), (LINE_X1, y)], fill="black", width=3)

    # 调弦字母（谱头，第一条小节线左侧）
    for i, letter in enumerate(["e", "B", "G", "D", "A", "E"]):
        d.text((100, ys[i]), letter, fill="black", font=font, anchor="mm")

    # 小节线
    for x in BARLINES:
        d.line([(x, ys[0] - 15), (x, ys[-1] + 15)], fill="black", width=4)

    # 品位数字
    for ms in MEASURES:
        for si, items in ms.items():
            for x, t in items:
                d.text((x, ys[si]), t, fill="black", font=font, anchor="mm")

    return img


def make_pdf(png_path: str, pdf_path: str) -> None:
    import fitz
    # 页面大小按 200 DPI 折算，渲染回 200 DPI 时正好还原原始像素
    doc = fitz.open()
    page = doc.new_page(width=W * 72 / 200, height=H * 72 / 200)
    page.insert_image(page.rect, filename=png_path)
    doc.save(pdf_path)
    doc.close()


if __name__ == "__main__":
    draw_tab().save(PNG_PATH)
    make_pdf(PNG_PATH, PDF_PATH)
    print("已生成:", PNG_PATH, PDF_PATH)
