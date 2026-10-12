"""제출본 원고를 과제 리포트 디자인(템플릿_디자인.hwpx)으로 조판한다.

11번과 같은 원고 파서를 쓰고 모양만 바꾼다. 원고 해석이 둘로 갈라지면 같은 원고에서
다른 본문이 나올 수 있으므로 파서는 11번 것을 그대로 불러 쓴다.

템플릿에서 가져오는 것: 맑은 고딕 글자 모양, 장·절 제목 표, 표 머리행 색(#E6EEF7), 쪽 번호.
새로 정의하는 것: 문단 모양과 표 테두리. 템플릿 것을 그대로 쓰면 읽기 어렵다.
  - 본문 문단(26)은 한글을 글자 단위로 끊고(BREAK_WORD) 문단 간격이 0 이다
    → 단어 단위 줄바꿈 · 양쪽 정렬 · 문단 뒤 4pt
  - 12pt 소제목(27)에 ■ 글머리표, 11pt 소제목(32)에 자동 번호가 붙어 원고 기호와 겹친다
  - 표 칸 선(#E5EDF6)이 흰 바탕에서 거의 보이지 않는다
  - 머리행 12pt · 칸 10pt · 좌우 셀 여백 510 이면 28행 표가 4쪽이 된다(실측) → 9pt · 283

사용자 지정(2026-09-25): 표 안 글자는 전부 가운데 정렬, 표마다 캡션(04_보고서/표_캡션.txt),
대목차(절 1~5)마다 새 쪽. 문단 사이 빈 줄은 20매 제한 때문에 쓰지 않는다(템플릿 그대로면 29매).
한글 실측 19쪽(표지 포함).

입력: 04_보고서/제출본_분석보고서_본문.md ("# Ⅰ." 줄부터), 04_보고서/템플릿_디자인.hwpx, 04_보고서/그림/*.png
출력: 04_보고서/분석보고서_v5.hwpx
재현: python3 03_분석/17_조판_템플릿.py
"""
import argparse
import importlib.util
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "04_보고서" / "템플릿_디자인.hwpx"
DEFAULT_MD = ROOT / "04_보고서" / "제출본_분석보고서_본문.md"
DEFAULT_OUT = ROOT / "04_보고서" / "분석보고서_v5.hwpx"
CAPTIONS = ROOT / "04_보고서" / "표_캡션.txt"   # 원고에 표 제목이 없어 따로 둔다
# 문단별 자간(%) — 마지막 줄이 수평 중앙에 못 미치는 문단을 18번이 한글 저장본에서 찾아 적는다
SPACING = ROOT / "04_보고서" / "자간조정.txt"
REPORT_TITLE = ("2026년 창원시 AI·데이터 활용 공모전", "분석보고서")
COVER_ROWS = [
    ("공 모 전", "2026년 창원시 AI·데이터 활용 공모전"),
    ("팀명(별명)", "동네 안에 읍면"),   # 확정된 참가신청서(05_제출본) 표기에 맞춘다
    ("제 출 일", "2026년 9월 28일"),
]
COVER_SPACERS = (7, 17)          # 제목 위 · 부제와 정보표 사이 (10pt 빈 줄 수)

# 템플릿의 글자 모양 번호
C_BODY, C_BOLD, C_SUB, C_SUBSUB, C_SMALL = 16, 20, 18, 22, 17
C_TH, C_TD, C_TD_BOLD, C_TITLE, C_SUBTITLE = 9, 17, 10, 23, 24
TABLE_WIDTH, FIG_WIDTH = 50454, 33000   # 그림 44000 은 거의 한 쪽을 차지해 앞 쪽에 큰 빈칸이 생겼다
# 이보다 높을 것으로 보이는 표만 쪽 경계에서 나눈다(약 88mm). 글자처럼 취급하면 통째로 넘어가 반 쪽이
# 비고, 반대로 중간 크기 표까지 나누면 머리행만 쪽 끝에 남는다(한글 실측 둘 다 겪었다).
SPLIT_HEIGHT = 25000
CELL_MARGIN = (283, 283, 280, 280)
STAR = "\ue000"   # 원고의 \* — 그대로 두면 한글이 역슬래시를 ₩ 로 찍고 **굵게** 짝도 깨진다
HEAD_FILL, HEAD_LINE, CELL_LINE = "#E6EEF7", "#7F9CC4", "#C5D2E4"


def load_typesetter():
    """11번의 파서·그림 코드를 불러온다. 파일명이 숫자로 시작해 import 문을 쓸 수 없다."""
    spec = importlib.util.spec_from_file_location("typeset11", ROOT / "03_분석" / "11_조판_보고서.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T11 = load_typesetter()
_esc = T11.esc
T11.esc = lambda text: _esc(text).replace(STAR, "*")   # 11번 para() 도 이 esc 를 쓴다
esc, runs, para = T11.esc, T11.runs, T11.para


# ── 새 모양 정의 ──────────────────────────────────────────────────────

def next_ids(header):
    """덧붙일 번호는 기존 최댓값 다음부터 빈틈 없이 잡는다."""
    last = {
        tag: max(int(i) for i in re.findall(rf'<hh:{tag} id="(\d+)"', header))
        for tag in ("paraPr", "borderFill")
    }
    return last["paraPr"] + 1, last["borderFill"] + 1


def line_fill(id_, color, face=None, top=None, bottom=None):
    """칸 테두리. 위·아래 선만 색을 달리할 수 있게 한다(머리행 아래 진한 선)."""
    sides = {"left": color, "right": color, "top": top or color, "bottom": bottom or color}
    xml = T11.border_fill(id_, face)
    for side, value in sides.items():
        xml = re.sub(rf'(<hh:{side}Border type="SOLID" width="0.12 mm" color=")#000000', rf"\g<1>{value}", xml)
    return xml


class Shapes:
    """덧붙이는 문단 모양·테두리의 번호표."""

    def __init__(self, header):
        p, b = next_ids(header)
        names = ["body", "lead", "sub", "subsub", "list", "code", "cell_c", "cell_l", "cell_r",
                 "cap", "table", "head", "spacer", "chapter", "code_first", "code_last"]
        self.p = {n: p + i for i, n in enumerate(names)}
        self.b = {"head": b, "cell": b + 1}
        pp = T11.para_pr
        keep = lambda *a, **k: T11.para_pr(*a, **k).replace('keepWithNext="0"', 'keepWithNext="1"')
        # 외톨이줄 보호 — 문단 첫 줄·마지막 줄 하나만 쪽 경계에 걸리지 않게 한다
        orphan = lambda *a, **k: T11.para_pr(*a, **k).replace('widowOrphan="0"', 'widowOrphan="1"')
        self.char_xml, self.spaced = [], {}
        self.next_char = max(int(i) for i in re.findall(r'<hh:charPr id="(\d+)"', header)) + 1
        self.header = header
        self.para_xml = [
            orphan(self.p["body"], "JUSTIFY", 160, next_=400),
            keep(self.p["lead"], "JUSTIFY", 160, prev=500, next_=150),
            keep(self.p["sub"], "LEFT", 150, prev=1000, next_=400),
            keep(self.p["subsub"], "LEFT", 150, prev=700, next_=250),
            orphan(self.p["list"], "JUSTIFY", 160, next_=200, left=1100, indent=-1100),
            pp(self.p["code"], "LEFT", 130, left=800),
            pp(self.p["cell_c"], "CENTER", 130),
            pp(self.p["cell_l"], "LEFT", 130),
            pp(self.p["cell_r"], "RIGHT", 130),
            pp(self.p["cap"], "CENTER", 130, prev=150, next_=500),
            pp(self.p["table"], "CENTER", 100, prev=500, next_=800),
            keep(self.p["head"], "CENTER", 100, prev=900, next_=500),
            pp(self.p["spacer"], "LEFT", 160),
            keep(self.p["chapter"], "CENTER", 100, prev=1400, next_=600),
            pp(self.p["code_first"], "LEFT", 130, left=800, prev=300),
            pp(self.p["code_last"], "LEFT", 130, left=800, next_=500),
        ]
        self.fill_xml = [
            line_fill(self.b["head"], CELL_LINE, face=HEAD_FILL, top=HEAD_LINE, bottom=HEAD_LINE),
            line_fill(self.b["cell"], CELL_LINE),
        ]

    def spaced_char(self, base, spacing):
        """글자 모양 base 를 자간만 바꿔 복제한다. 같은 조합은 한 번만 만든다."""
        if not spacing:
            return base
        if (base, spacing) not in self.spaced:
            xml = re.search(rf'<hh:charPr id="{base}".*?</hh:charPr>', self.header, re.S).group(0)
            xml = re.sub(r'id="\d+"', f'id="{self.next_char}"', xml, count=1)
            xml = re.sub(r"<hh:spacing [^>]*/>", "<hh:spacing " + " ".join(
                f'{k}="{spacing}"' for k in ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")) + "/>", xml)
            self.char_xml.append(xml)
            self.spaced[(base, spacing)] = self.next_char
            self.next_char += 1
        return self.spaced[(base, spacing)]

    def patch(self, header):
        return T11.patch_header(header, [("borderFills", self.fill_xml), ("charProperties", self.char_xml),
                                         ("paraProperties", self.para_xml)])


# ── 표지 ──────────────────────────────────────────────────────────────

def top_paragraphs(section):
    spans, depth, start = [], 0, 0
    for m in re.finditer(r"<hp:p\b|</hp:p>", section):
        if m.group(0) == "</hp:p>":
            depth -= 1
            if depth == 0:
                spans.append(section[start:m.end()])
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    return spans


def fill(xml, texts):
    queue = list(texts)
    return re.sub(r"<hp:t>[^<]*</hp:t>", lambda m: f"<hp:t>{esc(queue.pop(0)) if queue else ''}</hp:t>", xml)


def set_break(xml, on):
    return re.sub(r'pageBreak="\d"', f'pageBreak="{1 if on else 0}"', xml, count=1)


def set_para_shape(xml, para_id):
    return re.sub(r'paraPrIDRef="\d+"', f'paraPrIDRef="{para_id}"', xml, count=1)


def cover_table(sample, rows):
    row = re.search(r"<hp:tr>.*?</hp:tr>", sample, re.S).group(0)
    built = [fill(re.sub(r'rowAddr="\d+"', f'rowAddr="{r}"', row), pair) for r, pair in enumerate(rows)]
    head = re.sub(r'rowCnt="\d+"', f'rowCnt="{len(rows)}"', sample[: sample.index("<hp:tr>")], count=1)
    tail = sample[sample.rindex("</hp:tr>") + len("</hp:tr>"):]
    return head + "".join(built) + tail


def subtitle_lines(subtitle):
    """부제를 의미 단위로 세 줄로 나눈다. 한 문단에 두면 17pt 에서 '현 / 장 확인'처럼 끊겼다."""
    head, _, rest = subtitle.partition(" — ")
    first, _, last = rest.partition(" 방문건강관리")
    return [head + " —", first, "방문건강관리" + last] if last else [subtitle]


def cover(samples, shapes, subtitle):
    """제목은 두 줄로 직접 나눈다. 한 문단에 두면 '분석보고서'가 단어 중간에서 끊겼다."""
    title, sub, _, table = samples[:4]
    spacer = para(shapes.p["spacer"], [(C_BODY, "")])
    before, between = COVER_SPACERS
    line2 = fill(sub, [REPORT_TITLE[1]]).replace(f'charPrIDRef="{C_SUBTITLE}"', f'charPrIDRef="{C_TITLE}"')
    # 표지 제목 문단에는 구역 정의(secPr)가 들어 있어 맨 앞에 와야 한다. 위 여백은 문단 앞 간격으로 준다.
    return (
        [fill(title, [REPORT_TITLE[0]])]
        + [line2, spacer] + [fill(sub, [line]) for line in subtitle_lines(subtitle)]
        + [spacer] * between
        + [cover_table(table, COVER_ROWS)]
    ), before


# ── 표 ────────────────────────────────────────────────────────────────

class Table(list):
    """행 목록에 열 정렬(md 구분선의 :---: 표시)을 붙여 둔다."""
    align = ()


def parse_table(rows):
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    table = Table(r for r in cells if not all(re.fullmatch(r":?-+:?", c) for c in r))
    separator = next((r for r in cells if all(re.fullmatch(r":?-+:?", c) for c in r)), [])
    table.align = tuple(
        "r" if c.endswith(":") and not c.startswith(":") else "c" if c.startswith(":") else "l"
        for c in separator
    )
    return table


T11.parse_table = parse_table     # 11번 parse() 가 이 함수를 불러 열 정렬을 함께 넘긴다


def column_widths(table):
    """내용 폭만큼만 잡는다. 작은 표가 쪽 폭을 꽉 채우면 칸이 휑하다."""
    cols = max(len(r) for r in table)
    longest = [1] * cols
    for row in table:
        for j, cell in enumerate(row):
            plain = re.sub(r"\*\*|`", "", cell.replace("<br>", " "))
            longest[j] = max(longest[j], T11.display_width(plain))
    pad = CELL_MARGIN[0] + CELL_MARGIN[1] + 500
    natural = [w * 470 + pad for w in longest]
    if sum(natural) <= TABLE_WIDTH:
        scale = min(1.25, TABLE_WIDTH / sum(natural))
        widths = [int(w * scale) for w in natural]
        return widths if sum(widths) >= TABLE_WIDTH * 0.45 else [int(w * TABLE_WIDTH * 0.45 / sum(widths)) for w in widths]
    # 넘치는 표: 각 열의 가장 긴 낱말이 꺾이지 않는 폭을 먼저 주고 남는 폭을 나눈다.
    # 11번 방식(내용 길이 비례·최소 2400)으로는 날짜 열이 '취/득/일'처럼 한 자씩 꺾였다.
    words = [1] * cols
    for row in table:
        for j, cell in enumerate(row):
            for word in re.sub(r"\*\*|`", "", cell).replace("<br>", " ").split():
                words[j] = max(words[j], T11.display_width(word))
    floor = [min(w * 470 + pad, 12000) for w in words]
    if sum(floor) >= TABLE_WIDTH:
        return [int(f * TABLE_WIDTH / sum(floor)) for f in floor]
    extra = [max(0, min(n, 46 * 470 + pad) - f) for n, f in zip(natural, floor)]
    spare = TABLE_WIDTH - sum(floor)
    return [int(f + spare * e / (sum(extra) or 1)) for f, e in zip(floor, extra)]


def cell_xml(text, r, c, width, align, shapes):
    head = r == 0
    shape = shapes.p["cell_c"]   # 사용자 지정: 표 안 글자는 전부 가운데 정렬
    char, bold = (C_TH, C_TH) if head else (C_TD, C_TD_BOLD)
    inner = "".join(para(shape, runs(seg.strip(), char, bold, char)) for seg in text.split("<br>"))
    left, right, top, bottom = CELL_MARGIN
    return (
        f'<hp:tc name="" header="{1 if head else 0}" hasMargin="1" protect="0" editable="0" dirty="0" '
        f'borderFillIDRef="{shapes.b["head" if head else "cell"]}"><hp:subList id="" textDirection="HORIZONTAL" '
        'lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" '
        f'textHeight="0" hasTextRef="0" hasNumRef="0">{inner}</hp:subList>'
        f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
        f'<hp:cellSz width="{width}" height="900"/>'
        f'<hp:cellMargin left="{left}" right="{right}" top="{top}" bottom="{bottom}"/></hp:tc>'
    )


def caption_xml(text, width, shapes):
    """표 아래 캡션. 템플릿 자료 표와 같은 구조(outMargin 과 inMargin 사이)."""
    return (
        f'<hp:caption side="BOTTOM" fullSz="0" width="{width}" gap="850" lastWidth="{width}">'
        '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" linkListIDRef="0" '
        'linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
        + para(shapes.p["cap"], [(C_SMALL, text)]) + "</hp:subList></hp:caption>"
    )


def estimated_height(table, widths):
    """9pt 한 줄 1170, 한글 한 자 약 900(표시폭 2) 으로 셀 줄 수를 어림한다."""
    total = 0
    for row in table:
        lines = 1
        for text, width in zip(row, widths):
            plain = re.sub(r"\*\*|`", "", text).replace("<br>", " ")
            inner = max(1, width - CELL_MARGIN[0] - CELL_MARGIN[1] - 200)
            lines = max(lines, -(-T11.display_width(plain) * 450 // inner) + text.count("<br>"))
        total += lines * 1170 + CELL_MARGIN[2] + CELL_MARGIN[3]
    return total


def table_xml(table, tbl_id, shapes, caption):
    widths = column_widths(table)
    align = list(table.align) + ["l"] * len(widths)
    rows = [
        "<hp:tr>" + "".join(
            cell_xml(row[c] if c < len(row) else "", r, c, widths[c], align[c], shapes) for c in range(len(widths))
        ) + "</hp:tr>"
        for r, row in enumerate(table)
    ]
    left, right, top, bottom = CELL_MARGIN
    split = estimated_height(table, widths) > SPLIT_HEIGHT
    pos = (
        '<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
        'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="CENTER" '
        'vertOffset="0" horzOffset="0"/><hp:outMargin left="0" right="0" top="400" bottom="700"/>'
        if split else
        '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
        'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" horzAlign="CENTER" '
        'vertOffset="0" horzOffset="0"/><hp:outMargin left="0" right="0" top="0" bottom="0"/>'
    )
    tbl = (
        f'<hp:tbl id="{tbl_id}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
        'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" '
        f'rowCnt="{len(table)}" colCnt="{len(widths)}" cellSpacing="0" borderFillIDRef="{shapes.b["cell"]}" noAdjust="0">'
        f'<hp:sz width="{sum(widths)}" widthRelTo="ABSOLUTE" height="{900 * len(table)}" '
        'heightRelTo="ABSOLUTE" protect="0"/>'
        + pos + caption_xml(caption, sum(widths), shapes) +
        f'<hp:inMargin left="{left}" right="{right}" top="{top}" bottom="{bottom}"/>' + "".join(rows) + "</hp:tbl>"
    )
    return para(shapes.p["table"], [], inner=f'<hp:run charPrIDRef="{C_BODY}">{tbl}</hp:run>')


# ── 본문 ──────────────────────────────────────────────────────────────

def is_lead(text):
    """'(1) 거리 분포'처럼 줄 전체가 굵은 소제목."""
    return re.fullmatch(r"\*\*\([\d-]+\)[^*]*\*\*", text.strip()) is not None


def heading(level, text, samples, shapes, state):
    chapter, section = samples[4], samples[5]
    if level == 1:
        number, _, title = text.partition(". ")
        name, _, rest = title.partition(" : ")
        # 견본의 장 제목 표는 떠 있는 개체(treatAsChar=0)라 문단 간격이 먹지 않는다 — 글자처럼 흐르게 한다
        title = fill(chapter, [number, name]).replace('treatAsChar="0"', 'treatAsChar="1"', 1)
        out = [set_break(set_para_shape(title, shapes.p["chapter"]), False)]
        return out + ([para(shapes.p["sub"], [(C_SUB, rest)])] if rest else [])
    if level == 2:
        state["section"] += 1
        title = fill(section, [str(state["section"]), text.lstrip("◦ ").strip()])
        # 사용자 지정: 대목차(절 1~5)마다 새 쪽. 1은 Ⅰ·Ⅱ 바로 뒤라 나누지 않는다
        return [set_break(set_para_shape(title, shapes.p["head"]), state["section"] > 1)]
    if level == 3:
        return [para(shapes.p["sub"], [(C_SUB, text)])]
    return [para(shapes.p["subsub"], [(C_SUBSUB, text)])]


def load_captions(tables):
    lines = [l.strip() for l in CAPTIONS.read_text(encoding="utf-8").splitlines()]
    captions = [l for l in lines if l and not l.startswith("#")]
    if len(captions) != tables:
        raise SystemExit(f"표 {tables}개인데 캡션이 {len(captions)}줄이다 — {CAPTIONS.name} 를 맞춰라")
    return captions


def load_spacing():
    """자간조정.txt: 한 줄에 '자간<TAB>문단 첫 30자'."""
    if not SPACING.exists():
        return {}
    rows = [l.split("\t", 1) for l in SPACING.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
    return {key: int(value) for value, key in rows}


def paragraph_key(payload):
    return re.sub(r"\*\*|`", "", payload).replace(STAR, "*").strip()[:30]


def spaced_runs(payload, shapes, spacing):
    value = spacing.pop(paragraph_key(payload), 0)
    return runs(payload, shapes.spaced_char(C_BODY, value), shapes.spaced_char(C_BOLD, value), shapes.spaced_char(C_BODY, value))


def build_body(blocks, samples, shapes, figures):
    spacing = load_spacing()
    captions = load_captions(sum(1 for kind, _ in blocks if kind == "table"))
    out, state, tbl_id, pic_id = [], {"section": 0}, 3000, 4000
    T11.P_CAP, T11.C_CAP, T11.C_BODY, T11.FIG_WIDTH = shapes.p["cap"], C_SMALL, C_BODY, FIG_WIDTH
    for kind, payload in blocks:
        if kind == "h":
            out += heading(*payload, samples, shapes, state)
        elif kind == "p":
            if payload.startswith("> ▶"):
                continue                 # 붙여넣기 안내 — 조판에는 넣지 않는다
            shape = shapes.p["lead"] if is_lead(payload) else shapes.p["body"]
            out.append(para(shape, spaced_runs(payload, shapes, spacing)))
        elif kind == "li":
            out.append(para(shapes.p["list"], spaced_runs(payload, shapes, spacing)))
        elif kind == "code":
            # 명령어 묶음 앞뒤에 간격을 준다. 없으면 이어지는 본문에 붙어 읽힌다
            shape = [shapes.p["code"]] * len(payload)
            shape[0], shape[-1] = shapes.p["code_first"], shapes.p["code_last"]
            out += [para(s, [(C_SMALL, line)]) for s, line in zip(shape, payload)]
        elif kind == "table":
            number = tbl_id - 3000 + 1
            out.append(table_xml(payload, tbl_id, shapes, f"[표 {number}] {captions[number - 1]}"))
            tbl_id += 1
        elif kind == "fig":
            filename, caption = payload
            out.append(T11.picture_xml(figures[filename], T11.FIG_DIR / filename, caption, pic_id))
            pic_id += 1
    if spacing:
        raise SystemExit(f"자간조정.txt 에 원고에 없는 문단이 있다: {list(spacing)[:3]}")
    out[0] = set_break(out[0], True)     # 표지 다음 쪽에서 본문 시작
    return out


# ── 패키징 ────────────────────────────────────────────────────────────

def build(md_path, out_path):
    with zipfile.ZipFile(DESIGN) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    header = parts["Contents/header.xml"].decode("utf-8")
    shapes = Shapes(header)
    section = parts["Contents/section0.xml"].decode("utf-8")
    samples = top_paragraphs(section)
    root_open = section[: section.index("<hp:p ")]

    md = md_path.read_text(encoding="utf-8")
    blocks = T11.parse(md[md.index("# Ⅰ."):].replace("\\*", STAR))
    subtitle = blocks[0][1][1].partition(" : ")[2]

    figures, items = {}, []
    for n, png in enumerate(sorted(T11.FIG_DIR.glob("*.png")), start=1):
        figures[png.name] = f"image{n}"
        parts[f"BinData/image{n}.png"] = png.read_bytes()
        items.append(f'<opf:item id="image{n}" href="BinData/image{n}.png" media-type="image/png" isEmbeded="1"/>')
    hpf = parts["Contents/content.hpf"].decode("utf-8")
    hpf = hpf.replace("</opf:manifest>", "".join(items) + "</opf:manifest>", 1)
    hpf = re.sub(r"<opf:title>[^<]*</opf:title>", f"<opf:title>{' '.join(REPORT_TITLE)}</opf:title>", hpf)

    cover_paras, top_gap = cover(samples, shapes, subtitle)
    body = cover_paras + build_body(blocks, samples, shapes, figures)
    xml = T11.strip_linesegarray(root_open + "".join(body) + "</hs:sec>")
    # 머리말 영역은 비어 있고 꼬리말에는 쪽 번호뿐이다. 15mm 씩 비워 둘 이유가 없다.
    xml = re.sub(r'header="\d+" footer="\d+"', 'header="0" footer="2834"', xml, count=1)
    # 표지 제목은 구역 정의를 품은 첫 문단이라 앞에 빈 줄을 둘 수 없다 — 문단 앞 간격으로 내린다
    first_shape = max(shapes.p.values()) + 1
    shapes.para_xml.append(T11.para_pr(first_shape, "CENTER", 150, prev=top_gap * 1600))
    title_para_id = re.search(r'<hp:p [^>]*paraPrIDRef="(\d+)"', xml[len(root_open):]).group(1)
    xml = xml.replace(f'paraPrIDRef="{title_para_id}"', f'paraPrIDRef="{first_shape}"', 1)

    parts["Contents/header.xml"] = shapes.patch(header).encode("utf-8")
    parts["Contents/section0.xml"] = xml.encode("utf-8")
    parts["Contents/content.hpf"] = hpf.encode("utf-8")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), parts.pop("mimetype"), zipfile.ZIP_STORED)
        for name, data in parts.items():
            zf.writestr(name, data)
    return blocks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--md", default=str(DEFAULT_MD))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    md_path = Path(args.md if Path(args.md).is_absolute() else ROOT / args.md)
    out_path = Path(args.out if Path(args.out).is_absolute() else ROOT / args.out)
    blocks = build(md_path, out_path)
    print(f"조판 완료 → {out_path} ({out_path.stat().st_size:,} bytes) · 블록 {len(blocks)}")


if __name__ == "__main__":
    main()
