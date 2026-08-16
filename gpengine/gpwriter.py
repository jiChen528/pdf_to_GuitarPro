# -*- coding: utf-8 -*-
"""把转换 spec 写成真正的 Guitar Pro (.gp) 文件。

实现: 调起 Node 脚本 gpengine/alphatab_writer.js，用 alphaTab 的
Gp7Exporter 输出标准 GP7 格式（zip + score.gpif），Guitar Pro 8 可直接打开。

注: quinnjr/guitar-pro-mcp 输出的 .gp6 经实测是自定义二进制格式，
    GP8 与 alphaTab 均无法解析，因此写文件不依赖它（MCP 仍注册在
    ZCode 中，可作他用）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

_WRITER_JS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "alphatab_writer.js"))


def _frozen_resource(name: str) -> str | None:
    """PyInstaller 打包运行时从解包目录取资源文件。"""
    import sys
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    path = os.path.join(base, name)
    return path if os.path.isfile(path) else None


def _resolve_runtime() -> tuple[str, str]:
    """返回 (node 可执行文件, alphaTab.js 路径)。

    源码运行: 使用 node_modules 里的 alphaTab；打包运行: 使用内嵌的 alphaTab.js。
    node 未安装时抛出带中文说明的 RuntimeError。
    """
    import sys
    node = shutil.which("node")
    if node is None:
        raise RuntimeError(
            "未检测到 Node.js。本工具需要 Node.js 来写入 Guitar Pro 文件，\n"
            "请从 https://nodejs.org 安装后重试。")
    # alphaTab 解析: 打包后优先用内嵌文件，否则回退 node_modules
    bundled = _frozen_resource("alphaTab.js")
    alphatab_js = ""
    if bundled:
        alphatab_js = bundled
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dist = os.path.join(root, "node_modules", "@coderline", "alphatab",
                            "dist", "alphaTab.js")
        alphatab_js = dist if os.path.isfile(dist) else ""
    return node, alphatab_js

# GP8 的 Stratocaster Overdrive 音色定义（取自 GP8 自身保存的文件格式）
_SOUND_NAME = "Stratocaster Overdrive"
_SOUNDS_BLOCK = (
    "<Sounds><Sound>"
    "<Name><![CDATA[%s]]></Name>"
    "<Label><![CDATA[%s]]></Label>"
    "<Path>Stringed/Electric Guitars/Overdrive Guitar</Path>"
    "<Role>Factory</Role>"
    "<MIDI><LSB>0</LSB><MSB>0</MSB><Program>29</Program></MIDI>"
    "<RSE>"
    "<SoundbankPatch>Strat-Guitar</SoundbankPatch>"
    "<ElementsSettings></ElementsSettings>"
    "<Pickups><OverloudPosition>4</OverloudPosition>"
    "<Volumes>1 1</Volumes><Tones>1 1</Tones></Pickups>"
    "<EffectChain>"
    "<Effect id=\"E03_OverdriveScreamer\"><Parameters>0.84 0.5 0.84</Parameters></Effect>"
    "<Effect id=\"A05_StackBritishVintage\"><Parameters>0.85 0.67 0.36 0.66 0.52</Parameters></Effect>"
    "<Effect id=\"E30_EqGEq\"><Parameters>0.5 0.5 0.5 0.5 0.5 0.5 0.5 0.342857</Parameters></Effect>"
    "</EffectChain>"
    "</RSE>"
    "</Sound></Sounds>"
) % (_SOUND_NAME, _SOUND_NAME)


def build_spec(title: str, artist: str, tempo: int, measures: list,
               track_name: str = "Guitar",
               tuning: list | None = None) -> dict:
    """组装写文件 spec。

    tuning 语义与 tabparser 一致（自上而下 = 高音弦 -> 低音弦），
    这里反转为 alphaTab 需要的（低音弦 -> 高音弦）顺序。
    """
    track = {"name": track_name, "measures": measures}
    if tuning:
        track["tuning"] = list(reversed(tuning))
        track["strings"] = len(tuning)
    else:
        track["strings"] = 6
    return {
        "title": title,
        "artist": artist or "",
        "tempo": tempo,
        "tracks": [track],
    }


def _fix_gp8_string_order(xml: str) -> str:
    """把 alphaTab 的弦序写法改成 GP8 约定。

    alphaTab 内部/写出: 调弦高音在前、String 0 = 最细弦；
    GP8 的约定: 调弦低音在前、String 0 = 最粗弦。
    两者相反，需同时反转，否则 GP8 中 1~6 弦显示颠倒。
    """
    m = re.search(r'name="Tuning"><Pitches>([\d ]+)</Pitches>', xml)
    if not m:
        raise RuntimeError("未找到 Tuning Pitches，无法修正弦序")
    pitches = [int(x) for x in m.group(1).split()]
    n = len(pitches)
    rev = " ".join(str(x) for x in reversed(pitches))
    xml = re.sub(r'(name="Tuning"><Pitches>)[\d ]+(</Pitches>)',
                 r"\g<1>" + rev + r"\g<2>", xml, count=1)
    xml = re.sub(r'(name="String"><String>)(\d+)(</String>)',
                 lambda mm: mm.group(1) + str(n - 1 - int(mm.group(2))) + mm.group(3),
                 xml)
    return xml


def _inject_tuplets(xml: str, tuplets: list) -> str:
    """把连音号(Tuplet)元素写进对应拍（alphaTab Gp7Exporter 不导出 Tuplet）。

    tuplets: [(小节索引, 拍索引, numerator, denominator), ...]
    """
    import xml.etree.ElementTree as ET
    if not tuplets:
        return xml
    root = ET.fromstring(xml)
    beat_by_id = {b.get("id"): b for b in root.iter("Beat") if b.get("id") is not None}
    bar_by_id = {b.get("id"): b for b in root.iter("Bar") if b.get("id") is not None}
    voice_by_id = {v.get("id"): v for v in root.iter("Voice") if v.get("id") is not None}
    masters = [m for m in root.iter("MasterBar")]
    for mi, bi, num, den in sorted(tuplets):
        if mi >= len(masters):
            continue
        bsel = masters[mi].find("Bars")
        bar = bar_by_id.get((bsel.text or "").strip()) if bsel is not None else None
        if bar is None:
            continue
        vsel = bar.find("Voices")
        voice = voice_by_id.get((vsel.text or "").strip()) if vsel is not None else None
        if voice is None:
            continue
        b2 = voice.find("Beats")
        ids = (b2.text or "").split() if b2 is not None and b2.text else []
        if bi >= len(ids):
            continue
        beat = beat_by_id.get(ids[bi])
        if beat is None or beat.find("Tuplet") is not None:
            continue
        tup = ET.Element("Tuplet")
        ET.SubElement(tup, "Numerator").text = str(num)
        ET.SubElement(tup, "Denominator").text = str(den)
        # GPIF 规范中 Tuplet 位于 Rhythm/StemOrientation 之后：插到方向元素后
        anchor = None
        for name in ("ConcertPitchStemOrientation", "TransposedPitchStemOrientation"):
            el = beat.find(name)
            if el is not None:
                anchor = el
        idx = list(beat).index(anchor) + 1 if anchor is not None else 1
        beat.insert(idx, tup)
    out = ET.tostring(root, encoding="unicode")
    if xml.lstrip().startswith("<?xml"):
        out = xml[: xml.find("?>") + 2] + out
    return out


def _inject_guitar_sound(out_path: str, tuplets: list | None = None) -> None:
    """注入 Stratocaster Overdrive 音色定义，并修正 GP8 弦序。"""
    tmp_path = out_path + ".tmp"
    try:
        with zipfile.ZipFile(out_path, "r") as zin:
            entries = {n: zin.read(n) for n in zin.namelist()}
            infos = zin.infolist()
        xml = entries["Content/score.gpif"].decode("utf-8")

        # 1. 用 GP8 音色定义替换 alphaTab 写的通用 Sounds 块
        xml, n = re.subn(r"<Sounds>.*?</Sounds>", _SOUNDS_BLOCK, xml,
                         count=1, flags=re.S)
        if n == 0:
            raise RuntimeError("未找到 <Sounds> 块，无法注入音色")
        # 2. 同步 Sound 自动化引用值（Path;Name;Role 格式）
        xml, n = re.subn(r"Midi/29;Track_0_Initial;Factory",
                         "Stringed/Electric Guitars/Overdrive Guitar;%s;Factory"
                         % _SOUND_NAME, xml, count=1)
        if n == 0:
            raise RuntimeError("未找到 Sound 自动化，无法注入音色")
        # 3. 修正弦序（GP8 约定低音在前、String 0 = 最粗弦）
        xml = _fix_gp8_string_order(xml)
        # 4. 补连音号(Tuplet)元素
        xml = _inject_tuplets(xml, tuplets or [])

        entries["Content/score.gpif"] = xml.encode("utf-8")
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in infos:
                zout.writestr(info, entries[info.filename])
        shutil.move(tmp_path, out_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def write_gp_file(spec: dict, out_path: str, writer_js: str | None = None) -> None:
    """把 spec 写成 .gp 文件。失败抛 RuntimeError。"""
    js = writer_js or _frozen_resource("alphatab_writer.js") or _WRITER_JS
    if not os.path.isfile(js):
        raise FileNotFoundError("找不到写文件脚本: %s" % js)
    node, alphatab_js = _resolve_runtime()

    # 连音号位置: (小节索引, 拍索引, numerator, denominator)
    tuplets = []
    for mi, m in enumerate(spec["tracks"][0]["measures"]):
        for bi, b in enumerate(m.get("beats", [])):
            if b.get("tupletNumerator"):
                tuplets.append((mi, bi, b["tupletNumerator"],
                                b.get("tupletDenominator", 2)))

    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="pdf2gp_spec_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        env = dict(os.environ)
        if alphatab_js:
            env["ALPHATAB_JS"] = alphatab_js
        proc = subprocess.run(
            ["node", js, tmp, out_path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180, env=env,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError("alphaTab 写文件失败: %s" % detail)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    _inject_guitar_sound(out_path, tuplets=tuplets)
