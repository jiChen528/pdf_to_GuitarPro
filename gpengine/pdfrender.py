# -*- coding: utf-8 -*-
"""PDF 渲染模块：把 PDF 的每一页渲染成高清灰度图片。

默认把 PDF 当作图片谱处理：无论 PDF 内部是扫描图还是矢量文字，
统一渲染成位图后交给图像识别。
"""
from __future__ import annotations


def render_pdf(pdf_path: str, dpi: int = 200):
    """渲染 PDF 全部页面。

    返回 [(页码, BGR 格式的 numpy 图片), ...]，可直接交给 OpenCV / RapidOCR。
    """
    import fitz  # PyMuPDF
    import cv2
    import numpy as np

    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pages = []
    try:
        for page_no in range(len(doc)):
            pix = doc[page_no].get_pixmap(matrix=mat, colorspace=fitz.csGRAY, alpha=False)
            gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
            pages.append((page_no + 1, cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)))
    finally:
        doc.close()
    return pages
