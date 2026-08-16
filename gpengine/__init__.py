# -*- coding: utf-8 -*-
"""gpengine: PDF 吉他谱 → Guitar Pro (.gp6) 转换引擎。

模块:
    pdfrender: 把 PDF 每一页渲染为高清图片（默认按图片谱处理）
    tabparser: 图像识别（弦线/小节线/品位数字/调弦）重建谱面
    gpwriter : 调用 quinnjr/guitar-pro-mcp 生成 .gp6 文件
"""
