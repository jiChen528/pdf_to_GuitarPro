# -*- coding: utf-8 -*-
"""PDF 吉他谱 → Guitar Pro (.gp) 转换器。

用法:
    python pdf2gp.py 乐谱.pdf [选项]

引擎（--engine）:
    auto  自动选择（默认）: 有文字层的 PDF 用 text 直读，否则退回 ocr
    text  PDF 文字层直读: 品位数字/弦线/小节线/节奏符号全部从 PDF 内部
          结构读取，对 Guitar Pro 等软件导出的矢量 PDF 准确率最高
    ocr   渲染成图片 + 本地 OCR（纯扫描图片 PDF 用）
    llm   渲染成图片 + 多模态大模型识别（需配置 API 密钥，见 --llm-* 参数）

已知限制:
    - 文字层缺失节奏符号时按 --duration 默认时值处理
    - 手写谱/低清扫描件识别率低；滑音/推弦等技巧符号不识别
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from gpengine.gpwriter import build_spec, write_gp_file

DEFAULT_GP8 = r"D:\Guitar Pro 8\GuitarPro.exe"


def _parse_args(argv):
    p = argparse.ArgumentParser(
        description="PDF 吉他谱转 Guitar Pro (.gp) 文件", add_help=True)
    p.add_argument("input", help="输入 PDF 路径")
    p.add_argument("-o", "--output-dir", help="输出目录（默认与输入同目录）")
    p.add_argument("--title", help="歌曲标题（默认取 PDF 文件名）")
    p.add_argument("--artist", default="", help="艺术家（可选）")
    p.add_argument("--tempo", type=int, default=120, help="速度 BPM（默认 120）")
    p.add_argument("--duration", type=int, default=8, choices=(1, 2, 4, 8, 16, 32),
                   help="兜底音符时值: 1=全 2=二分 4=四分 8=八分(默认) 16=十六分 32=三十二分")
    p.add_argument("--tuning", help="调弦 MIDI 音高，逗号分隔、高音弦在前，"
                                    "如标准调弦 64,59,55,50,45,40（默认自动识别/标准）")
    p.add_argument("--dpi", type=int, default=200, help="渲染分辨率（ocr/llm 引擎用，默认 200）")
    p.add_argument("--engine", choices=("auto", "text", "ocr", "llm"),
                   default="auto", help="识别引擎（默认 auto 自动选择）")
    p.add_argument("--llm-key", help="多模态 API 密钥（或环境变量 PDF2GP_LLM_API_KEY）")
    p.add_argument("--llm-base-url", help="API 地址（默认 https://api.openai.com/v1）")
    p.add_argument("--llm-model", help="模型名（默认 gpt-4o-mini）")
    p.add_argument("--open", action="store_true", help="生成后用默认关联程序打开")
    p.add_argument("--gp8", help="Guitar Pro 8 可执行文件路径（默认自动检测）")
    return p.parse_args(argv)


def _find_gp8() -> str | None:
    candidates = [
        DEFAULT_GP8,
        r"D:\Program Files\GuitarPro8\GuitarPro.exe",
        r"D:\GuitarPro8\GuitarPro.exe",
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return None


def _run_text_engine(pdf_path: str, duration: int):
    from gpengine.text_extractor import extract_pdf_text_route
    measures, tuning, warnings = extract_pdf_text_route(pdf_path,
                                                        default_duration=duration)
    n_notes = sum(len(b["notes"]) for m in measures for b in m["beats"])
    return measures, tuning, warnings, n_notes


def _run_llm_engine(pdf_path: str, args):
    from gpengine.llm_engine import parse_pdf_llm
    print("  逐页调用多模态大模型（较慢，请稍候）...")
    measures, tuning, warnings = parse_pdf_llm(
        pdf_path, api_key=args.llm_key, base_url=args.llm_base_url,
        model=args.llm_model, dpi=args.dpi)
    n_notes = sum(len(b["notes"]) for m in measures for b in m["beats"])
    return measures, tuning, warnings, n_notes


def _run_ocr_engine(pdf_path: str, args):
    from gpengine.pdfrender import render_pdf
    from gpengine.tabparser import parse_tab_image, system_to_spec_measures
    print("  渲染 PDF 页面为图片 (%d DPI) ..." % args.dpi)
    pages = render_pdf(pdf_path, dpi=args.dpi)
    print("  共 %d 页，初始化 OCR 模型 ..." % len(pages))
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    measures, warnings = [], []
    n_notes = 0
    tuning = None
    for page_no, img in pages:
        systems, warns = parse_tab_image(img, ocr=ocr)
        for w in warns:
            warnings.append("[第%d页] %s" % (page_no, w))
        for s in systems:
            n_notes += s.total_notes
            if tuning is None and s.tuning:
                tuning = s.tuning
            measures.extend(system_to_spec_measures(s, duration=args.duration))
    return measures, tuning, warnings, n_notes


def main(argv=None) -> int:
    args = _parse_args(argv)
    if not os.path.isfile(args.input):
        print("错误: 找不到输入文件", args.input)
        return 1

    engine = args.engine
    if engine == "auto":
        from gpengine.text_extractor import has_text_layer
        engine = "text" if has_text_layer(args.input) else "ocr"
        print("[1/3] 引擎自动选择: %s" % engine)
    else:
        print("[1/3] 引擎: %s" % engine)

    try:
        if engine == "text":
            measures, detected_tuning, warnings, n_notes = \
                _run_text_engine(args.input, args.duration)
        elif engine == "llm":
            measures, detected_tuning, warnings, n_notes = _run_llm_engine(args.input, args)
        else:
            measures, detected_tuning, warnings, n_notes = _run_ocr_engine(args.input, args)
    except Exception as exc:
        print("错误: 识别失败 -", exc)
        return 1

    if not measures:
        print("错误: 未能识别出任何音符。")
        print("建议: 1) 换引擎重试（--engine text/ocr/llm）; 2) ocr 引擎可 --dpi 300; "
              "3) 确认 PDF 里确实是六线谱。")
        return 1

    # 调弦优先级: CLI 指定 > 自动识别 > 默认标准调弦
    if args.tuning:
        tuning = [int(x) for x in args.tuning.split(",")]
    elif detected_tuning:
        tuning = detected_tuning
    else:
        tuning = [64, 59, 55, 50, 45, 40]
        warnings.append("未识别到调弦字母，按标准调弦 EADGBE 处理")

    print("[2/3] 识别完成: %d 个小节、%d 个音符%s" % (
        len(measures), n_notes,
        ("（调弦: " + ",".join(map(str, tuning)) + "）" if detected_tuning else "")))

    out_dir = os.path.abspath(args.output_dir or os.path.dirname(args.input))
    os.makedirs(out_dir, exist_ok=True)
    title = args.title or os.path.splitext(os.path.basename(args.input))[0]
    filename = os.path.splitext(os.path.basename(args.input))[0] + ".gp"
    spec = build_spec(title=title, artist=args.artist, tempo=args.tempo,
                      measures=measures, tuning=tuning)

    print("[3/3] 生成 .gp 文件（alphaTab -> GP7 格式） ...")
    out_path = os.path.join(out_dir, filename)
    try:
        write_gp_file(spec, out_path)
    except Exception as exc:
        print("错误: 生成 .gp 失败 -", exc)
        return 1

    if not os.path.isfile(out_path):
        print("警告: 输出文件不存在:", out_path)
        return 1
    print("生成成功:", out_path)

    for w in warnings:
        print("提示:", w)

    if args.open:
        if os.path.isfile(out_path):
            try:
                os.startfile(out_path)      # Windows: 按默认关联打开（等价双击）
                print("已用默认关联打开:", out_path)
            except OSError:
                gp8 = args.gp8 or _find_gp8()
                if gp8 and os.path.isfile(gp8):
                    print("正在用 Guitar Pro 8 打开 ...")
                    subprocess.Popen([gp8, out_path])
                else:
                    print("未找到 Guitar Pro 8（可用 --gp8 指定路径），请手动打开:", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
