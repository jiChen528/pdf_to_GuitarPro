# -*- coding: utf-8 -*-
"""PDF 吉他谱 → Guitar Pro (.gp) 转换器（图形界面）。

用法:
    PDF2GP.exe            打开图形界面：把 PDF 拖进窗口即可转换
    PDF2GP.exe 谱子.pdf   命令行直接转换（无界面），在 PDF 同目录生成 .gp

注意:
    目前仅支持「文字版 PDF」（Guitar Pro / MuseScore 等软件导出的矢量谱子），
    扫描件 / 图片型 PDF 暂不支持。
"""
from __future__ import annotations

import os
import sys
import threading

from gpengine.text_extractor import extract_pdf_text_route, has_text_layer
from gpengine.gpwriter import build_spec, write_gp_file

_GP8_CANDIDATES = [
    r"D:\Guitar Pro 8\GuitarPro.exe",
    r"D:\Program Files\GuitarPro8\GuitarPro.exe",
    r"D:\GuitarPro8\GuitarPro.exe",
]


def convert_pdf(pdf_path: str) -> dict:
    """转换单个 PDF，在 PDF 同目录生成同名 .gp。

    返回结果信息 dict；失败抛异常（带中文说明）。
    """
    pdf_path = os.path.abspath(pdf_path)
    if not pdf_path.lower().endswith(".pdf"):
        raise ValueError("请选择 PDF 文件")
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError("文件不存在: %s" % pdf_path)
    if not has_text_layer(pdf_path):
        raise RuntimeError(
            "该 PDF 没有文字层，无法识别。\n\n"
            "目前仅支持「文字版 PDF」（Guitar Pro、MuseScore 等软件导出的矢量谱子），\n"
            "扫描件 / 图片型 PDF 暂不支持。")
    measures, tuning, warnings = extract_pdf_text_route(pdf_path, default_duration=8)
    if not measures:
        raise RuntimeError("未能识别到六线谱内容，请确认 PDF 是吉他 tab 谱。")
    title = os.path.splitext(os.path.basename(pdf_path))[0]
    spec = build_spec(title, "", 120, measures,
                      tuning=tuning or [64, 59, 55, 50, 45, 40])  # 标准调弦兜底
    out_path = os.path.splitext(pdf_path)[0] + ".gp"
    write_gp_file(spec, out_path)
    n_notes = sum(len(b["notes"]) for m in measures for b in m["beats"])
    return {"out": out_path, "measures": len(measures), "notes": n_notes,
            "warnings": warnings}


def _find_gp8() -> str | None:
    for cand in _GP8_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return None


def _open_folder(path: str) -> None:
    try:
        os.startfile(os.path.dirname(path))                      # noqa: S606
    except OSError:
        pass


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    # 拖拽支持（可选）：tkinterdnd2 的 Tk 子类提供 drop_target_register
    dn_root_cls = None
    DND_FILES = None
    try:
        import tkinterdnd2
        from tkinterdnd2.TkinterDnD import Tk as _DnDTk
        dn_root_cls = _DnDTk
        DND_FILES = getattr(tkinterdnd2, "DND_FILES", None)
    except Exception:                                            # noqa: BLE001
        dn_root_cls = None

    try:
        root = dn_root_cls() if dn_root_cls else tk.Tk()
    except Exception:                                            # noqa: BLE001
        # tkdnd 库加载失败（如打包环境缺文件）→ 退回普通窗口，仍可点选文件
        dn_root_cls = None
        DND_FILES = None
        root = tk.Tk()
    root.title("PDF 吉他谱 → Guitar Pro 转换器")
    root.geometry("560x420")
    root.minsize(480, 360)

    header = tk.Label(
        root, text="PDF 吉他谱 → Guitar Pro 转换器",
        font=("Microsoft YaHei UI", 16, "bold"), pady=12)
    header.pack()

    note = tk.Label(
        root,
        text="目前仅支持「文字版 PDF」（Guitar Pro 等软件导出的谱子）",
        font=("Microsoft YaHei UI", 10), fg="#666666")
    note.pack()

    # ---- 拖放区 ----
    zone = tk.Label(
        root,
        text="\n  将 PDF 文件拖到这里  \n\n        或点击选择文件        \n",
        font=("Microsoft YaHei UI", 13),
        bg="#f2f7ff", fg="#333333",
        relief="ridge", bd=2, cursor="hand2")
    zone.pack(fill="both", expand=True, padx=40, pady=(18, 8))

    status = tk.Label(root, text="", font=("Microsoft YaHei UI", 10),
                      fg="#0066cc", wraplength=480, justify="left")
    status.pack(pady=4)

    result = tk.Label(root, text="", font=("Microsoft YaHei UI", 10),
                      fg="#008000", wraplength=480, justify="left")
    result.pack(pady=4)

    btn_row = tk.Frame(root)
    btn_row.pack(pady=(2, 16))

    def pick_file():
        path = filedialog.askopenfilename(
            title="选择 PDF 吉他谱", filetypes=[("PDF 文件", "*.pdf")])
        if path:
            start_convert(path)

    def on_drop(event):
        path = event.data.strip().strip("{}").strip()
        if path:
            start_convert(path)

    def start_convert(path: str):
        path = os.path.abspath(path)
        zone.configure(text="\n  正在转换，请稍候…  \n\n  %s  \n" % os.path.basename(path))
        status.configure(text="识别中…")
        result.configure(text="")
        btn_row.pack_forget()

        def work():
            try:
                info = convert_pdf(path)
                root.after(0, done_ok, info)
            except Exception as exc:                              # noqa: BLE001
                root.after(0, done_fail, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def done_ok(info):
        zone.configure(text="\n  将 PDF 文件拖到这里  \n\n        或点击选择文件        \n")
        status.configure(text="识别完成: %d 个小节、%d 个音符" % (info["measures"], info["notes"]))
        result.configure(text="已生成:\n%s" % info["out"])
        build_buttons(info["out"])

    def done_fail(msg):
        zone.configure(text="\n  将 PDF 文件拖到这里  \n\n        或点击选择文件        \n")
        status.configure(text="转换失败", fg="#cc0000")
        result.configure(text=msg, fg="#cc0000")
        btn_row.pack_forget()

    def build_buttons(out_path):
        for w in btn_row.winfo_children():
            w.destroy()
        tk.Button(btn_row, text="打开 Guitar Pro",
                  command=lambda: os.startfile(out_path)).pack(  # noqa: S606
            side="left", padx=6)
        tk.Button(btn_row, text="打开所在文件夹",
                  command=lambda: _open_folder(out_path)).pack(side="left", padx=6)
        btn_row.pack(pady=(2, 16))

    zone.bind("<Button-1>", lambda e: pick_file())
    if DND_FILES:
        zone.drop_target_register(DND_FILES)
        zone.dnd_bind("<<Drop>>", on_drop)

    root.mainloop()


def _selftest(path_out: str) -> None:
    """打包自检：在冻结环境验证 tkdnd 拖拽组件能否加载，结果写入文件。

    配合环境变量 PDF2GP_SELFTEST 使用（windowed exe 无控制台输出）。
    """
    import tkinter as tk
    try:
        import tkinterdnd2
        from tkinterdnd2.TkinterDnD import Tk as _DnDTk
        root = _DnDTk()
        ver = root.tk.call("package", "require", "tkdnd")
        lbl = tk.Label(root)
        lbl.drop_target_register(tkinterdnd2.DND_FILES)
        lbl.dnd_bind("<<Drop>>", lambda e: None)
        msg = "DND_OK version=%s" % ver
        root.destroy()
    except Exception as exc:                                     # noqa: BLE001
        msg = "DND_FAIL: %r" % exc
    with open(path_out, "w", encoding="utf-8") as f:
        f.write(msg)


def main() -> int:
    if os.environ.get("PDF2GP_SELFTEST"):
        _selftest(os.environ["PDF2GP_SELFTEST"])
        return 0
    # 带 PDF 参数时直接转换（无界面），便于批处理/测试
    if len(sys.argv) > 1:
        path = sys.argv[1]
        if path.lower().endswith(".pdf"):
            try:
                info = convert_pdf(path)
                print("生成成功: %s" % info["out"])
                print("小节数: %d | 音符数: %d" % (info["measures"], info["notes"]))
                return 0
            except Exception as exc:                              # noqa: BLE001
                print("转换失败: %s" % exc, file=sys.stderr)
                return 1
    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
