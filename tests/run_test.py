# -*- coding: utf-8 -*-
"""端到端测试: 合成谱面 PDF -> pdf2gp -> 校验 .gp 内容。"""
from __future__ import annotations

import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tests.make_sample import PDF_PATH, EXPECTED, draw_tab, make_pdf  # noqa: E402
from pdf2gp import main  # noqa: E402

OUT_DIR = os.path.join(HERE, "out")


def _gpif_xml(gp_path: str) -> str:
    with zipfile.ZipFile(gp_path) as zf:
        names = zf.namelist()
        assert "Content/score.gpif" in names, "zip 内未找到 score.gpif: %s" % names
        assert "VERSION" in names, "zip 内未找到 VERSION"
        return zf.read("Content/score.gpif").decode("utf-8")


def test_pipeline():
    # 1. 生成样例
    draw_tab().save(os.path.join(HERE, "sample_tab.png"))
    make_pdf(os.path.join(HERE, "sample_tab.png"), PDF_PATH)

    # 2. 跑转换
    rc = main([PDF_PATH, "-o", OUT_DIR, "--title", "Sample Tab", "--artist", "Tester"])
    assert rc == 0, "pdf2gp 返回码 %d" % rc

    # 3. 校验文件
    gp = os.path.join(OUT_DIR, "sample_tab.gp")
    assert os.path.isfile(gp), "未生成 .gp 文件: %s" % gp
    print("生成文件:", gp, os.path.getsize(gp), "bytes")

    xml = _gpif_xml(gp)

    # 4. 音符数与品位
    note_count = len(re.findall(r"<Note id=", xml))
    frets = [int(f) for f in re.findall(r'<Property name="Fret"><Fret>(\d+)</Fret>', xml)]
    print("音符数:", note_count, "品位:", sorted(set(frets)))
    assert note_count == EXPECTED["notes"], "音符数 %d != %d" % (note_count, EXPECTED["notes"])
    assert 12 in frets and 10 in frets, "两位品位(12/10)缺失: %s" % sorted(set(frets))

    # 5. 小节数
    bar_count = len(re.findall(r"<Bar id=", xml))
    print("小节数:", bar_count)
    assert bar_count == EXPECTED["measures"], "小节数 %d != %d" % (bar_count, EXPECTED["measures"])

    # 6. 音色与调弦（GP8 约定: 低音在前）
    assert "<SoundbankPatch>Strat-Guitar</SoundbankPatch>" in xml, "Stratocaster 音色缺失"
    assert "E03_OverdriveScreamer" in xml, "Overdrive 效果链缺失"
    assert "<Pitches>40 45 50 55 59 64</Pitches>" in xml, "调弦顺序不符合 GP8 约定"

    # 7. 弦编号一致性（GP8 约定: String 0 = 最粗弦）
    pitches = [40, 45, 50, 55, 59, 64]
    mismatches = []
    for mm in re.finditer(
            r'name="String"><String>(\d+)</String></Property>\s*'
            r'<Property name="Fret"><Fret>(\d+)</Fret></Property>\s*'
            r'<Property name="Midi"><Number>(\d+)</Number>', xml):
        s, f, midi = (int(x) for x in mm.groups())
        if pitches[s] + f != midi:
            mismatches.append((s, f, midi))
    assert not mismatches, "弦编号/音高不一致: %s" % mismatches[:5]

    print("PASS: 端到端测试通过")


if __name__ == "__main__":
    test_pipeline()
