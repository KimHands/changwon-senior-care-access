"""보고서 마크다운 초고를 .hwpx 로 조판한다.

왜 스크립트로 하는가. 조판을 손으로 하면 본문이 한 줄 바뀔 때마다 다시 해야 하고,
그때마다 매수가 달라진다. A4 10~20매 제한이 걸린 문서라 매수는 계속 재야 하는 값이다.
생성기를 두면 본문을 고치고 다시 돌리는 것으로 끝난다. 11월 공개 검증에서
"제출본이 어떻게 만들어졌는가"에 답할 수 있는 것은 덤이다.

**다른 포맷을 거치지 않는다.**
.hwpx 는 zip 컨테이너이므로 XML 을 직접 만들어 재포장한다.

용지·여백은 배포된 제출 서식(`01_공고·서식/공모전 제출 서식.hwpx`)의 것을 그대로
물려받는다. 서식 원본은 읽기만 하고 건드리지 않는다.

입력: 04_보고서/분석보고서_초고_v4.md
      04_보고서/그림/*.png
출력: 04_보고서/분석보고서_v4.hwpx (정본 = ver 번호가 가장 큰 파일)
재현: python3 03_분석/11_조판_보고서.py
      python3 03_분석/11_조판_보고서.py --md <초고> --out <파일명>
"""
import argparse
import re
import shutil
import struct
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_HWPX = ROOT / "01_공고·서식" / "공모전 제출 서식.hwpx"
DEFAULT_MD = ROOT / "04_보고서" / "분석보고서_초고_v4.md"
DEFAULT_OUT = ROOT / "04_보고서" / "분석보고서_v4.hwpx"
FIG_DIR = ROOT / "04_보고서" / "그림"

TEXT_WIDTH = 48188      # 서식의 본문 가로 폭 (HWPUNIT)
TABLE_WIDTH = 47700
FIG_WIDTH = 42000

# 새로 붙이는 모양의 ID. 서식이 쓰는 번호(charPr 0~53, paraPr 0~48, borderFill 1~33)와
# 겹치지 않게 100번대부터 쓴다.
C_BODY, C_BOLD, C_CODE = 100, 101, 102
C_H0, C_H1, C_H2, C_H3 = 103, 104, 105, 106
C_TH, C_TD, C_TD_BOLD, C_CAP = 107, 108, 109, 110
P_BODY, P_H0, P_H1, P_H2, P_H3 = 100, 101, 102, 103, 104
P_TH, P_TD, P_CAP, P_CODE, P_LIST = 105, 106, 107, 108, 109
B_CELL, B_HEAD = 34, 35

FONT_BATANG, FONT_DOTUM, FONT_GOTHIC = 2, 1, 0   # 함초롬바탕 · 함초롬돋움 · 맑은 고딕


# ── 모양 정의 ─────────────────────────────────────────────────────────

def char_pr(id_, height, font, bold=False, color="#000000"):
    return (
        f'<hh:charPr id="{id_}" height="{height}" textColor="{color}" shadeColor="none" '
        f'useFontSpace="0" useKerning="0" symMark="NONE" borderFillIDRef="2">'
        f'<hh:fontRef hangul="{font}" latin="{font}" hanja="{font}" japanese="{font}" '
        f'other="{font}" symbol="{font}" user="{font}"/>'
        '<hh:ratio hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>'
        '<hh:spacing hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
        '<hh:relSz hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>'
        '<hh:offset hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
        + ("<hh:bold/>" if bold else "")
        + '<hh:underline type="NONE" shape="SOLID" color="#000000"/>'
        '<hh:strikeout shape="NONE" color="#000000"/><hh:outline type="NONE"/>'
        '<hh:shadow type="NONE" color="#B2B2B2" offsetX="10" offsetY="10"/></hh:charPr>'
    )


def para_pr(id_, align, spacing, prev=0, next_=0, left=0, indent=0):
    margin = (
        f'<hh:margin><hc:intent value="{indent}" unit="HWPUNIT"/>'
        f'<hc:left value="{left}" unit="HWPUNIT"/><hc:right value="0" unit="HWPUNIT"/>'
        f'<hc:prev value="{prev}" unit="HWPUNIT"/><hc:next value="{next_}" unit="HWPUNIT"/></hh:margin>'
        f'<hh:lineSpacing type="PERCENT" value="{spacing}" unit="HWPUNIT"/>'
    )
    return (
        f'<hh:paraPr id="{id_}" tabPrIDRef="0" condense="0" fontLineHeight="0" snapToGrid="1" '
        'suppressLineNumbers="0" checked="0">'
        f'<hh:align horizontal="{align}" vertical="BASELINE"/>'
        '<hh:heading type="NONE" idRef="0" level="0"/>'
        '<hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="KEEP_WORD" widowOrphan="0" '
        'keepWithNext="0" keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/>'
        '<hh:autoSpacing eAsianEng="0" eAsianNum="0"/>'
        '<hp:switch><hp:case hp:required-namespace="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar">'
        + margin
        + "</hp:case><hp:default>"
        + margin
        + "</hp:default></hp:switch>"
        '<hh:border borderFillIDRef="2" offsetLeft="0" offsetRight="0" offsetTop="0" '
        'offsetBottom="0" connect="0" ignoreMargin="0"/></hh:paraPr>'
    )


def border_fill(id_, face=None):
    fill = (
        f'<hc:fillBrush><hc:winBrush faceColor="{face}" hatchColor="#999999" alpha="0"/></hc:fillBrush>'
        if face
        else ""
    )
    sides = "".join(
        f'<hh:{s}Border type="SOLID" width="0.12 mm" color="#000000"/>'
        for s in ("left", "right", "top", "bottom")
    )
    return (
        f'<hh:borderFill id="{id_}" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
        '<hh:slash type="NONE" Crooked="0" isCounter="0"/>'
        '<hh:backSlash type="NONE" Crooked="0" isCounter="0"/>' + sides + fill + "</hh:borderFill>"
    )


NEW_CHAR_PRS = [
    char_pr(C_BODY, 1000, FONT_BATANG),
    char_pr(C_BOLD, 1000, FONT_BATANG, bold=True),
    char_pr(C_CODE, 900, FONT_GOTHIC),
    char_pr(C_H0, 1600, FONT_DOTUM, bold=True),
    char_pr(C_H1, 1400, FONT_DOTUM, bold=True),
    char_pr(C_H2, 1200, FONT_DOTUM, bold=True),
    char_pr(C_H3, 1050, FONT_DOTUM, bold=True),
    char_pr(C_TH, 900, FONT_DOTUM, bold=True),
    char_pr(C_TD, 900, FONT_BATANG),
    char_pr(C_TD_BOLD, 900, FONT_BATANG, bold=True),
    char_pr(C_CAP, 900, FONT_DOTUM),
]
NEW_PARA_PRS = [
    para_pr(P_BODY, "JUSTIFY", 160, next_=200),
    para_pr(P_H0, "CENTER", 150, next_=800),
    para_pr(P_H1, "LEFT", 140, prev=700, next_=300),
    para_pr(P_H2, "LEFT", 140, prev=500, next_=200),
    para_pr(P_H3, "LEFT", 140, prev=400, next_=150),
    para_pr(P_TH, "CENTER", 130),
    para_pr(P_TD, "LEFT", 130),
    para_pr(P_CAP, "CENTER", 130, prev=100, next_=300),
    para_pr(P_CODE, "LEFT", 130),
    para_pr(P_LIST, "JUSTIFY", 160, next_=100, left=600, indent=-600),
]
NEW_BORDER_FILLS = [border_fill(B_CELL), border_fill(B_HEAD, face="#EEEEEE")]


# ── 마크다운 파싱 ─────────────────────────────────────────────────────

def parse(md):
    """블록 목록으로 쪼갠다. 표·코드블록·그림은 문단과 다루는 법이 다르다."""
    blocks, lines, i = [], md.split("\n"), 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
        elif line.startswith("```"):
            body, i = [], i + 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            blocks.append(("code", body))
            i += 1
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            blocks.append(("table", parse_table(rows)))
        elif line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            blocks.append(("h", (level, line.lstrip("#").strip())))
            i += 1
        elif line.startswith("---"):
            i += 1                      # 구분선은 조판에서 쓰지 않는다
        elif re.match(r"^(\-|\d+\.)\s", line):
            blocks.append(("li", line))
            i += 1
        else:
            fig = re.match(r"^\*\*\[그림 (\d)\]([^*]*)\*\*\s*\(`([^`]+)`\)", line)
            if fig:
                blocks.append(("fig", (fig.group(3), f"[그림 {fig.group(1)}]{fig.group(2)}")))
            else:
                blocks.append(("p", line))
            i += 1
    return blocks


def parse_table(rows):
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    # 가운데 정렬 표시 `:-:` 는 대시가 하나뿐이다. {2,} 로 두면 구분선이 대시 행으로 조판된다.
    return [r for r in cells if not all(re.fullmatch(r":?-+:?", c) for c in r)]


# ── XML 생성 ──────────────────────────────────────────────────────────

def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def runs(text, base_char, bold_char=None, code_char=None):
    """**굵게** 와 `코드` 를 런으로 쪼갠다. 나머지 인라인 표기는 이 문서에 없다."""
    bold_char = bold_char or base_char
    code_char = code_char or base_char
    out = []
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append((bold_char, part[2:-2]))
        elif part.startswith("`") and part.endswith("`"):
            out.append((code_char, part[1:-1]))
        else:
            out.append((base_char, part))
    return out or [(base_char, "")]


def para(para_id, pieces, height=1000, inner=""):
    body = "".join(
        f'<hp:run charPrIDRef="{c}"><hp:t>{esc(t)}</hp:t></hp:run>' for c, t in pieces
    )
    # linesegarray 를 쓰지 않는다. 그건 한글이 계산해 둔 줄바꿈 캐시이고, 우리가 한 줄짜리
    # 가짜 값을 넣으면 한글이 그걸 믿어 여러 줄 문단이 한 자리에 겹쳐 찍힌다(실제로 그랬다).
    # 빼 두면 열 때 다시 계산한다.
    return (
        f'<hp:p id="0" paraPrIDRef="{para_id}" styleIDRef="0" pageBreak="0" '
        f'columnBreak="0" merged="0">{inner}{body}</hp:p>'
    )


def display_width(text):
    """한글은 두 칸으로 센다. 열 너비를 내용 길이에 비례해 나누기 위한 것이다."""
    return sum(2 if ord(ch) > 0x2000 else 1 for ch in text)


def column_widths(table):
    cols = max(len(r) for r in table)
    longest = [1] * cols
    for row in table:
        for j, cell in enumerate(row):
            plain = re.sub(r"\*\*|`", "", cell.replace("<br>", " "))
            longest[j] = max(longest[j], display_width(plain))
    # 한 열이 표를 독차지하지 않게 상한을 둔다. 긴 서술 열이 있는 표가 실제로 있다.
    capped = [min(w, 46) for w in longest]
    total = sum(capped)
    widths = [max(2400, int(TABLE_WIDTH * w / total)) for w in capped]
    widths[-1] += TABLE_WIDTH - sum(widths)
    return widths


def table_xml(table, tbl_id):
    widths = column_widths(table)
    cols, rows_xml = len(widths), []
    for r, row in enumerate(table):
        cells = []
        for c in range(cols):
            text = row[c] if c < len(row) else ""
            head = r == 0
            char = C_TH if head else C_TD
            pieces_per_line = [
                runs(seg, char, C_TD_BOLD if not head else C_TH, C_CODE)
                for seg in text.split("<br>")
            ]
            inner = "".join(
                para(P_TH if head else P_TD, pieces, height=900)
                for pieces in pieces_per_line
            )
            cells.append(
                f'<hp:tc name="" header="{1 if head else 0}" hasMargin="0" protect="0" '
                f'editable="0" dirty="0" borderFillIDRef="{B_HEAD if head else B_CELL}">'
                '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" '
                'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
                f'hasTextRef="0" hasNumRef="0">{inner}</hp:subList>'
                f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/>'
                '<hp:cellSpan colSpan="1" rowSpan="1"/>'
                f'<hp:cellSz width="{widths[c]}" height="1400"/>'
                '<hp:cellMargin left="141" right="141" top="141" bottom="141"/></hp:tc>'
            )
        rows_xml.append("<hp:tr>" + "".join(cells) + "</hp:tr>")

    tbl = (
        f'<hp:tbl id="{tbl_id}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
        'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" '
        f'rowCnt="{len(table)}" colCnt="{cols}" cellSpacing="0" borderFillIDRef="{B_CELL}" noAdjust="0">'
        f'<hp:sz width="{TABLE_WIDTH}" widthRelTo="ABSOLUTE" height="{1400 * len(table)}" '
        'heightRelTo="ABSOLUTE" protect="0"/>'
        '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
        'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" horzAlign="LEFT" '
        'vertOffset="0" horzOffset="0"/>'
        '<hp:outMargin left="0" right="0" top="200" bottom="200"/>'
        '<hp:inMargin left="0" right="0" top="141" bottom="141"/>' + "".join(rows_xml) + "</hp:tbl>"
    )
    return para(P_BODY, [], height=1400, inner=f'<hp:run charPrIDRef="{C_BODY}">{tbl}</hp:run>')


def png_size(path):
    data = path.read_bytes()[:33]
    return struct.unpack(">II", data[16:24])


def picture_xml(item_id, path, caption, pic_id):
    px_w, px_h = png_size(path)
    cur_w = FIG_WIDTH
    cur_h = int(FIG_WIDTH * px_h / px_w)
    org_w, org_h = px_w * 100, px_h * 100          # 72dpi 기준 HWPUNIT
    dim_w, dim_h = px_w * 75, px_h * 75            # 96dpi 기준 HWPUNIT
    cap = (
        f'<hp:caption side="BOTTOM" fullSz="0" width="{cur_w}" gap="850" lastWidth="{cur_w}">'
        '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" '
        'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
        + para(P_CAP, [(C_CAP, caption)], height=900)
        + "</hp:subList></hp:caption>"
    )
    pic = (
        f'<hp:pic id="{pic_id}" zOrder="0" numberingType="PICTURE" textWrap="TOP_AND_BOTTOM" '
        'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" href="" groupLevel="0" '
        f'instid="{pic_id}" reverse="0"><hp:offset x="0" y="0"/>'
        f'<hp:orgSz width="{org_w}" height="{org_h}"/>'
        f'<hp:curSz width="{cur_w}" height="{cur_h}"/>'
        '<hp:flip horizontal="0" vertical="0"/>'
        f'<hp:rotationInfo angle="0" centerX="{cur_w // 2}" centerY="{cur_h // 2}" rotateimage="1"/>'
        "<hp:renderingInfo><hc:transMatrix e1=\"1\" e2=\"0\" e3=\"0\" e4=\"0\" e5=\"1\" e6=\"0\"/>"
        f'<hc:scaMatrix e1="{cur_w / org_w:.6f}" e2="0" e3="0" e4="0" e5="{cur_h / org_h:.6f}" e6="0"/>'
        '<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/></hp:renderingInfo>'
        f'<hc:img binaryItemIDRef="{item_id}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
        '<hp:lineShape color="#000000" width="0" style="SOLID" endCap="ROUND" headStyle="NORMAL" '
        'tailStyle="NORMAL" headfill="0" tailfill="0" headSz="SMALL_SMALL" tailSz="SMALL_SMALL" '
        'outlineStyle="OUTER" alpha="0"/>'
        f'<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{org_w}" y="0"/>'
        f'<hc:pt2 x="{org_w}" y="{org_h}"/><hc:pt3 x="0" y="{org_h}"/></hp:imgRect>'
        f'<hp:imgClip left="0" right="{dim_w}" top="0" bottom="{dim_h}"/>'
        '<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
        f'<hp:imgDim dimwidth="{dim_w}" dimheight="{dim_h}"/><hp:effects/>'
        f'<hp:sz width="{cur_w}" widthRelTo="ABSOLUTE" height="{cur_h}" heightRelTo="ABSOLUTE" protect="0"/>'
        '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
        'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="CENTER" '
        'vertOffset="0" horzOffset="0"/>'
        '<hp:outMargin left="0" right="0" top="200" bottom="200"/>' + cap + "</hp:pic>"
    )
    return para(P_CAP, [], height=cur_h, inner=f'<hp:run charPrIDRef="{C_BODY}">{pic}</hp:run>')


HEADING_STYLE = {1: (P_H0, C_H0), 2: (P_H1, C_H1), 3: (P_H2, C_H2), 4: (P_H3, C_H3)}


def build_section(blocks, figures):
    out, tbl_id, pic_id = [], 1000, 2000
    for kind, payload in blocks:
        if kind == "h":
            level, text = payload
            para_id, char_id = HEADING_STYLE.get(level, (P_H3, C_H3))
            out.append(para(para_id, [(char_id, text)], height=1600 if level == 1 else 1200))
        elif kind == "p":
            out.append(para(P_BODY, runs(payload, C_BODY, C_BOLD, C_CODE)))
        elif kind == "li":
            out.append(para(P_LIST, runs(payload, C_BODY, C_BOLD, C_CODE)))
        elif kind == "code":
            for line in payload:
                out.append(para(P_CODE, [(C_CODE, line)], height=900))
        elif kind == "table":
            out.append(table_xml(payload, tbl_id))
            tbl_id += 1
        elif kind == "fig":
            filename, caption = payload
            item = figures.get(filename)
            if item:
                out.append(picture_xml(item, FIG_DIR / filename, caption, pic_id))
                pic_id += 1
            else:
                out.append(para(P_CAP, [(C_CAP, f"{caption} (그림 파일 없음: {filename})")]))
        else:
            raise ValueError(f"처리하지 않는 블록: {kind}")
    return out


# ── 패키징 ────────────────────────────────────────────────────────────

def patch_header(header, counts):
    """모양 정의를 뒤에 덧붙이고 itemCnt 를 맞춘다. 개수가 틀리면 한글이 파일을 거부한다."""
    for tag, additions in counts:
        old = re.search(rf'<hh:{tag} itemCnt="(\d+)">', header)
        total = int(old.group(1)) + len(additions)
        header = header.replace(old.group(0), f'<hh:{tag} itemCnt="{total}">', 1)
        close = f"</hh:{tag}>"
        header = header.replace(close, "".join(additions) + close, 1)
    return header


LINESEG = re.compile(r"<hp:linesegarray>.*?</hp:linesegarray>|<hp:linesegarray\s*/>", re.S)


def strip_linesegarray(xml):
    """줄바꿈 캐시를 남기지 않는다.

    linesegarray 는 한글이 계산해 둔 줄 위치다. 우리가 만든 값을 넣으면 한글이 그것을
    믿고 여러 줄 문단을 한 자리에 겹쳐 찍는다(첫 조판에서 실제로 그렇게 나왔다).
    서식의 첫 문단에 실려 오는 것까지 걷어내야 전 문단이 같은 규칙으로 다시 계산된다.
    """
    return LINESEG.sub("", xml)


def build(md_path, out_path):
    with zipfile.ZipFile(BASE_HWPX) as base:
        parts = {n: base.read(n) for n in base.namelist()}

    header = parts["Contents/header.xml"].decode("utf-8")
    header = patch_header(
        header,
        [
            ("borderFills", NEW_BORDER_FILLS),
            ("charProperties", NEW_CHAR_PRS),
            ("paraProperties", NEW_PARA_PRS),
        ],
    )

    # BinData 안에서는 ASCII 이름을 쓴다. zip 엔트리 이름이 한글이면 한글이 읽지 못하는
    # 환경이 있다. 원본 파일명은 캡션과 `figures` 매핑에만 남긴다.
    figures, items = {}, []
    for n, png in enumerate(sorted(FIG_DIR.glob("*.png")), start=1):
        item = f"image{n}"
        figures[png.name] = item
        parts[f"BinData/{item}.png"] = png.read_bytes()
        items.append(
            f'<opf:item id="{item}" href="BinData/{item}.png" media-type="image/png" isEmbeded="1"/>'
        )

    hpf = parts["Contents/content.hpf"].decode("utf-8")
    hpf = hpf.replace("</opf:manifest>", "".join(items) + "</opf:manifest>", 1)

    section = parts["Contents/section0.xml"].decode("utf-8")
    head = section[: section.index("</hp:p>") + len("</hp:p>")]   # secPr 을 담은 첫 문단
    blocks = parse(md_path.read_text(encoding="utf-8"))
    body = "".join(build_section(blocks, figures))
    section = strip_linesegarray(head + body + "</hs:sec>")

    parts["Contents/header.xml"] = header.encode("utf-8")
    parts["Contents/section0.xml"] = section.encode("utf-8")
    parts["Contents/content.hpf"] = hpf.encode("utf-8")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype 은 압축하지 않고 맨 앞에 둔다(OPC 규약).
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

    kinds = {}
    for kind, _ in blocks:
        kinds[kind] = kinds.get(kind, 0) + 1
    print(f"조판 완료 → {out_path.relative_to(ROOT)} ({out_path.stat().st_size:,} bytes)")
    print("  블록: " + " · ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    print("  매수는 한글에서 열어 확인한다 — HWPX 는 쪽 수를 저장하지 않는다.")


if __name__ == "__main__":
    main()
