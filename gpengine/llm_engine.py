# -*- coding: utf-8 -*-
"""多模态大模型 API 通道：用于纯图片 PDF 的谱面识别（备用引擎）。

配置（环境变量）:
    PDF2GP_LLM_API_KEY    必填，API 密钥
    PDF2GP_LLM_BASE_URL   默认 https://api.openai.com/v1（OpenAI 兼容接口，
                          通义千问/智谱/DeepSeek 等均可通过改 BASE_URL 接入）
    PDF2GP_LLM_MODEL      默认 gpt-4o-mini
也可通过 CLI --llm-key / --llm-base-url / --llm-model 覆盖。

原理: 把渲染出的谱面图片以 base64 发给视觉模型，要求返回结构化 JSON，
解析后转成与其它引擎一致的 measures spec。
"""
from __future__ import annotations

import base64
import io
import json
import os
import re

_SYSTEM_PROMPT = (
    "你是专业的吉他六线谱(TAB)识别器。用户提供六线谱的页面图片。"
    "请把图片中所有六线谱内容提取为 JSON。规则:\n"
    "1. string 从 1(最上面最细的弦)到 6(最下面最粗的弦)编号；"
    "品位数字可能是多位(如 10、12)。\n"
    "2. 横向位置相近的多个品位属于同一拍(和弦)，用 notes 数组列出。\n"
    "3. 节奏: duration 取值 1=全 2=二分 4=四分 8=八分 16=十六分 32=三十二分；"
    "从谱面下方/上方的符干符尾判断，判断不了填 4；dotted=true 表示附点。\n"
    "4. 休止符用 rest=true 且 notes 为空的拍表示。\n"
    "5. 输出必须是合法 JSON，结构:\n"
    '{"measures":[{"beats":[{"duration":4,"dotted":false,"rest":false,'
    '"notes":[{"string":1,"fret":0}]}]}]}\n'
    "6. 不要输出 JSON 以外的任何文字。"
)

_DURATIONS = (1, 2, 4, 8, 16, 32)


def _parse_json(text: str) -> dict:
    """从模型回复中提取 JSON（容忍代码块包裹）。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("模型回复中没有 JSON: %s..." % text[:120])
    return json.loads(m.group(0))


def _normalize(data: dict) -> list:
    """把模型 JSON 归一化成 measures spec，丢弃非法字段。"""
    measures = []
    for m in data.get("measures", []):
        beats = []
        for b in m.get("beats", []):
            beat = {"duration": int(b.get("duration", 4)), "notes": []}
            if beat["duration"] not in _DURATIONS:
                beat["duration"] = 4
            if b.get("dotted"):
                beat["dotted"] = True
            if b.get("rest"):
                beat["rest"] = True
            else:
                for n in b.get("notes", []):
                    si = int(n.get("string", 0))
                    fr = int(n.get("fret", 0))
                    if 1 <= si <= 12 and 0 <= fr <= 36:
                        beat["notes"].append({"string": si, "fret": fr})
                if not beat["notes"]:
                    beat["rest"] = True
            if beat.get("rest") and not beat["notes"]:
                beats.append({"duration": beat["duration"], "rest": True, "notes": []})
            elif beat["notes"]:
                beats.append(beat)
        if beats:
            measures.append({"timeSignature": {"numerator": 4, "denominator": 4},
                             "beats": beats})
    return measures


def parse_pdf_llm(pdf_path: str, api_key: str | None = None,
                  base_url: str | None = None, model: str | None = None,
                  dpi: int = 150):
    """用多模态大模型识别图片谱面。

    返回 (measures, tuning=None, warnings)。未配置 API 密钥时抛 RuntimeError。
    """
    import cv2
    from PIL import Image
    import httpx
    from gpengine.pdfrender import render_pdf

    api_key = api_key or os.environ.get("PDF2GP_LLM_API_KEY", "")
    base_url = (base_url or os.environ.get("PDF2GP_LLM_BASE_URL",
                                           "https://api.openai.com/v1")).rstrip("/")
    model = model or os.environ.get("PDF2GP_LLM_MODEL", "gpt-4o-mini")
    if not api_key:
        raise RuntimeError(
            "未配置多模态 API 密钥。请设置环境变量 PDF2GP_LLM_API_KEY，"
            "或使用 --llm-key 参数；可用 --llm-base-url/--llm-model 切换服务商与模型。")

    pages = render_pdf(pdf_path, dpi=dpi)
    measures = []
    warnings = []
    client = httpx.Client(timeout=180)
    try:
        for page_no, img in pages:
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            buf = io.BytesIO()
            Image.fromarray(rgb).save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            resp = client.post(
                base_url + "/chat/completions",
                headers={"Authorization": "Bearer " + api_key},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": [
                            {"type": "text", "text": "识别这张六线谱图片，输出 JSON。"},
                            {"type": "image_url",
                             "image_url": {"url": "data:image/png;base64," + b64}},
                        ]},
                    ],
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            try:
                measures.extend(_normalize(_parse_json(content)))
            except Exception as exc:
                warnings.append("[第%d页] 模型输出解析失败: %s" % (page_no, exc))
    finally:
        client.close()
    if not measures:
        warnings.append("大模型未识别出任何音符")
    return measures, None, warnings
