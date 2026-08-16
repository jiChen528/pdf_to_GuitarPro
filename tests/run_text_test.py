# -*- coding: utf-8 -*-
"""文本路线端到端测试: 合成文字层 PDF -> text 引擎 -> 校验 spec。"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tests.make_sample_text import (EXPECTED, PDF_PATH, make_text_pdf)  # noqa: E402
from gpengine.text_extractor import has_text_layer, extract_pdf_text_route  # noqa: E402


def test_text_route():
    make_text_pdf(PDF_PATH)

    # 1. 文字层检测
    assert has_text_layer(PDF_PATH), "合成的文字层 PDF 未被识别为 text 引擎"

    # 2. 提取
    measures, tuning, warnings = extract_pdf_text_route(PDF_PATH)
    print("小节数:", len(measures), "| 调弦:", tuning, "| 警告:", warnings)
    assert len(measures) == EXPECTED["measures"], "小节数 %d != %d" % (len(measures), EXPECTED["measures"])

    # 3. 音符与品位
    all_notes = [n for m in measures for b in m["beats"] for n in b["notes"]]
    assert len(all_notes) == EXPECTED["notes"], "音符数 %d != %d" % (len(all_notes), EXPECTED["notes"])
    frets = sorted({n["fret"] for n in all_notes})
    print("音符数:", len(all_notes), "品位:", frets)
    assert frets == EXPECTED["frets"], "品位集合不符: %s" % frets

    # 4. 节奏（符干=四分、tick=八分）
    dur_counts = {}
    for m in measures:
        for b in m["beats"]:
            dur_counts[b["duration"]] = dur_counts.get(b["duration"], 0) + 1
    print("时值分布:", dur_counts)
    assert dur_counts == EXPECTED["beats"], "时值分布不符: %s" % dur_counts

    # 5. 调弦识别
    assert tuning == EXPECTED["tuning"], "调弦不符: %s" % tuning

    # 6. 哑音(x/左手闷音)识别
    mutes = [n for m in measures for b in m["beats"] for n in b["notes"]
             if n.get("dead")]
    print("哑音数:", len(mutes))
    assert len(mutes) == EXPECTED["mutes"], "哑音数 %d != %d" % (len(mutes), EXPECTED["mutes"])

    # 7. 延音线(tie)识别
    tied = [n for m in measures for b in m["beats"] for n in b["notes"]
            if n.get("tied")]
    print("延音音符数:", len(tied))
    assert len(tied) == EXPECTED["tied"], "延音数 %d != %d" % (len(tied), EXPECTED["tied"])

    # 8. 滑弦(slide)识别
    slides = [n for m in measures for b in m["beats"] for n in b["notes"]
              if n.get("slideOut") or n.get("slideIn")]
    print("滑弦标记数:", len(slides))
    assert len(slides) == EXPECTED["slides"], "滑弦数 %d != %d" % (len(slides), EXPECTED["slides"])

    # 9. 击勾弦(h/p)识别
    hopo = [n for m in measures for b in m["beats"] for n in b["notes"]
            if n.get("hopoOrigin") or n.get("hopoDestination")]
    print("击勾弦标记数:", len(hopo))
    assert len(hopo) == EXPECTED["hopo"], "击勾弦数 %d != %d" % (len(hopo), EXPECTED["hopo"])

    # 10. 连奏滑音(sl)识别: 只标起点，终点不带任何标记
    legato = [n for m in measures for b in m["beats"] for n in b["notes"]
              if n.get("legatoSlideOut")]
    print("连奏滑音起点数:", len(legato))
    assert len(legato) == EXPECTED["legato"], "连奏滑音数 %d != %d" % (len(legato), EXPECTED["legato"])
    legato_back = [n for m in measures for b in m["beats"] for n in b["notes"]
                   if (n.get("string"), n.get("fret")) == (2, 6)]
    assert legato_back and not legato_back[0].get("slideIn") and not legato_back[0].get("legatoSlideOut"), \
        "连奏滑音终点不应带滑入/滑出标记"

    # 11. 无头无尾滑音: 音符后=滑出, 音符前=滑入, 按方向分上行/下行
    def count(flag):
        return sum(1 for m in measures for b in m["beats"] for n in b["notes"]
                   if n.get(flag))
    print("无尾滑出下行/上行:", count("slideOutDown"), count("slideOutUp"))
    print("无头滑入上行/下行:", count("slideInBelow"), count("slideInAbove"))
    assert count("slideOutDown") == EXPECTED["slide_out_down"], "无尾滑出(下行)数不符"
    assert count("slideOutUp") == EXPECTED["slide_out_up"], "无尾滑出(上行)数不符"
    assert count("slideInBelow") == EXPECTED["slide_in_below"], "无头滑入(上行)数不符"
    assert count("slideInAbove") == EXPECTED["slide_in_above"], "无头滑入(下行)数不符"

    # 12. 右手闷音 P.M. 与三连音
    pm = [n for m in measures for b in m["beats"] for n in b["notes"]
          if n.get("palmMute")]
    print("右手闷音音符数:", len(pm))
    assert len(pm) == EXPECTED["palm_mute"], "右手闷音数 %d != %d" % (len(pm), EXPECTED["palm_mute"])
    tup = [b for m in measures for b in m["beats"] if b.get("tupletNumerator")]
    print("三连音拍数:", len(tup))
    assert len(tup) == EXPECTED["tuplets"], "三连音拍数 %d != %d" % (len(tup), EXPECTED["tuplets"])
    assert all(b["tupletNumerator"] == 3 and b["tupletDenominator"] == 2 for b in tup), \
        "三连音应为 3:2"

    print("PASS: 文本路线测试通过")


if __name__ == "__main__":
    test_text_route()
