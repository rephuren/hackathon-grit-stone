"""Правка фото словами: «сделай ярче», «чёрно-белое», «убери фон», «обрежь квадратом», «добавь надпись „Лето“».
Текст превращается в список операций над уже существующими инструментами редактора (фильтры, яркость, кадр, фон,
улучшение качества, надпись). Разбор делает открытая модель (тот же LLM, что и помощник, если настроен),
без сети — правила по ключевым словам. Новых предметов («добавь шапку») модель не дорисовывает: честно отвечаем,
что так пока не умеем. Фото в модель не отправляется — только текст команды."""
import json
import os
import re

import httpx

FILTERS = {"warm": "тёплый", "cool": "холодный", "film": "плёнка", "bw": "чёрно-белый", "vivid": "яркий", "retro": "ретро",
           "cinema": "кино", "illustration": "иллюстрация", "none": "без фильтра"}
OPS = ("filter", "adjust", "auto", "bg", "crop", "rotate", "flip", "upscale", "erase", "text", "reset", "unsupported")

RULES = [  # (регулярка, операция) — запасной разбор без сети
    (r"ч[её]рно.?бел|монохром|\bч/б\b", {"op": "filter", "name": "bw"}),
    (r"т[её]пл", {"op": "filter", "name": "warm"}),
    (r"холодн", {"op": "filter", "name": "cool"}),
    (r"пл[её]нк|винтаж", {"op": "filter", "name": "film"}),
    (r"ретро", {"op": "filter", "name": "retro"}),
    (r"кино|кинемат", {"op": "filter", "name": "cinema"}),
    (r"рисун|иллюстрац|мульт", {"op": "filter", "name": "illustration"}),
    (r"убер\w* фильтр|без фильтр|как было|сбрось|отмени вс", {"op": "reset"}),
    (r"(убер|удали)\w* фон|без фона|фон бел", {"op": "bg", "mode": "white"}),
    (r"размой фон|размыт\w* фон|фон размыт", {"op": "bg", "mode": "blur"}),
    (r"(убер|удали|сотри)\w* (человек|прохож|объект|мусор|провод|лишн)", {"op": "erase"}),
    (r"качеств|ч[её]тче|резче|увелич", {"op": "upscale"}),
    (r"автоулучш|улучши|исправь свет", {"op": "auto"}),
    (r"квадрат|1:1", {"op": "crop", "ratio": "1:1"}),
    (r"16:9|широк", {"op": "crop", "ratio": "16:9"}),
    (r"поверн\w* влево|против час", {"op": "rotate", "deg": 270}),
    (r"поверн|разверн", {"op": "rotate", "deg": 90}),
    (r"зеркал|отрази", {"op": "flip"}),
    (r"ярч|светл|осветл", {"op": "adjust", "brightness": 25}),
    (r"темн|затемн", {"op": "adjust", "brightness": -25}),
    (r"контраст", {"op": "adjust", "contrast": 25}),
    (r"насыщ|сочн|красочн", {"op": "adjust", "saturation": 30}),
    (r"бледн|приглуш", {"op": "adjust", "saturation": -30}),
    (r"(добав|нарису|дорису|встав|сделай меня|увеличь меня|уменьш)\w*", {"op": "unsupported"}),
]


def _rules(text: str) -> list:
    t = text.lower()
    out = []
    q = re.search(r"(?:надпись|подпись|напиши|текст)\s*[:«\"„“]?\s*([^»\"“”]{1,60})", text, re.I)
    if q:
        out.append({"op": "text", "value": q.group(1).strip(" .»\"“”")})
        t = t.replace(q.group(0).lower(), " ")
    for rx, op in RULES:
        if re.search(rx, t) and not any(o["op"] == op["op"] for o in out):
            out.append(dict(op))
    if any(o["op"] == "text" for o in out):  # «добавь надпись» — это надпись, а не новый предмет
        out = [o for o in out if o["op"] != "unsupported"]
    return out


def _clean(ops) -> list:
    """Оставляет только известные операции с безопасными значениями."""
    res = []
    for o in ops if isinstance(ops, list) else []:
        if not isinstance(o, dict) or o.get("op") not in OPS:
            continue
        op = o["op"]
        if op == "filter" and o.get("name") in FILTERS:
            res.append({"op": op, "name": o["name"]})
        elif op == "adjust":
            a = {k: max(-60, min(60, int(o.get(k, 0)))) for k in ("brightness", "contrast", "saturation") if isinstance(o.get(k), (int, float))}
            if a:
                res.append({"op": op, **a})
        elif op == "bg" and o.get("mode") in ("white", "yellow", "black", "blur"):
            res.append({"op": op, "mode": o["mode"]})
        elif op == "crop" and o.get("ratio") in ("1:1", "4:5", "3:2", "2:3", "16:9"):
            res.append({"op": op, "ratio": o["ratio"]})
        elif op == "rotate" and o.get("deg") in (90, 270):
            res.append({"op": op, "deg": o["deg"]})
        elif op == "text" and str(o.get("value", "")).strip():
            res.append({"op": op, "value": str(o["value"]).strip()[:60]})
        elif op in ("auto", "flip", "upscale", "erase", "reset", "unsupported"):
            res.append({"op": op})
    return res[:5]


def _llm(text: str) -> list:
    base, model = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/"), os.environ.get("LLM_MODEL", "").strip()
    if not (base and model):
        raise RuntimeError("модель не настроена")
    key = os.environ.get("LLM_API_KEY", "").strip()
    system = ("Ты переводишь просьбу пользователя о правке фото в список операций фоторедактора. Верни JSON {\"ops\": [...]}. "
              "Допустимые операции: {\"op\":\"filter\",\"name\":одно из " + str(list(FILTERS)) + "}, "
              "{\"op\":\"adjust\",\"brightness\":-60..60,\"contrast\":-60..60,\"saturation\":-60..60}, {\"op\":\"auto\"} (свет и резкость), "
              "{\"op\":\"bg\",\"mode\":\"white|yellow|black|blur\"} (убрать или размыть фон), {\"op\":\"crop\",\"ratio\":\"1:1|4:5|3:2|2:3|16:9\"}, "
              "{\"op\":\"rotate\",\"deg\":90|270} (90 — вправо, по часовой; 270 — влево, против часовой), {\"op\":\"flip\"}, {\"op\":\"upscale\"} (улучшить качество, чётче), "
              "{\"op\":\"erase\"} (убрать лишний объект кистью), {\"op\":\"text\",\"value\":\"надпись\"}, {\"op\":\"reset\"}. "
              "Если просят добавить или дорисовать новый предмет, изменить человека или его размер — {\"op\":\"unsupported\"}. "
              "Только операции, о которых просили.")
    body = {"model": model, "temperature": 0, "max_tokens": 200, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": text[:300]}]}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    r = httpx.post(f"{base}/chat/completions", headers=headers, json=body, timeout=10)
    if r.status_code == 400:
        body.pop("response_format")
        r = httpx.post(f"{base}/chat/completions", headers=headers, json=body, timeout=10)
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"] or ""
    return json.loads(raw[raw.find("{"): raw.rfind("}") + 1]).get("ops", [])


def parse(text: str) -> dict:
    try:
        ops, engine = _clean(_llm(text)), "llm"
        rule_ops = _clean(_rules(text))
        if not ops:
            ops = rule_ops
        elif not any(o["op"] == "text" for o in ops):  # надпись в кавычках правила ловят надёжнее модели
            ops += [o for o in rule_ops if o["op"] == "text"]
            ops = [o for o in ops if o["op"] != "unsupported"] if any(o["op"] == "text" for o in ops) and len(ops) > 1 else ops
    except Exception:
        ops, engine = _clean(_rules(text)), "rules"
    return {"ops": ops, "engine": engine}
