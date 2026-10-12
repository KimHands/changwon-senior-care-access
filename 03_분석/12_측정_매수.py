"""조판본(.hwpx)이 A4 몇 매인지 화면 없이 계산한다.

왜 계산해야 하는가. HWPX 어디에도 쪽 수는 저장돼 있지 않다 — 쪽 나눔은 여는 쪽이
그때 계산하는 값이다. 한글은 AppleScript 를 지원하지 않고, LibreOffice 는 HWPX 를
아예 열지 못하며(`source file could not be loaded`), `hwpx-edit` 스킬도 렌더링은
하지 않는다고 스스로 밝히고 있다. 그래서 레이아웃을 직접 시뮬레이션한다.

무엇을 재는가. 마크다운 원고가 아니라 **조판본 XML** 을 읽는다. 원고를 재면 조판
과정에서 붙는 문단 간격·표 여백·그림 높이가 빠져 실제와 어긋난다.

정확도의 한계를 먼저 밝힌다.
  - 한글 글자는 거의 모든 한글 글꼴에서 정확히 1em 이라 폭 계산이 흔들리지 않는다.
    분량의 대부분이 한글이므로 지배적인 항은 안정적이다.
  - 라틴·숫자는 글꼴마다 다르다. 실제 TTF 의 advance width 를 읽어 쓴다.
  - **재현하지 않는 것**: 외톨이줄 보호, 표가 쪽을 넘을 때의 분할 규칙, 자간 미세조정.
    이것들은 쪽 수를 늘리는 쪽으로 작용하므로 이 계산은 **하한에 가깝다.**
  - 따라서 20매 상한을 판정할 때는 여유를 두고 읽어야 한다. 18매가 나왔다고
    20매 이내가 보장되지 않는다.

출력: 03_분석/매수_측정표.md
재현: python3 03_분석/12_측정_매수.py
      python3 03_분석/12_측정_매수.py --hwpx 04_보고서/분석보고서_v4.hwpx
"""
import argparse
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HWPX = ROOT / "04_보고서" / "분석보고서_v4.hwpx"
OUT_MD = ROOT / "03_분석" / "매수_측정표.md"

# 라틴 글자 폭의 근거. 함초롬 계열은 시스템에 등록돼 있지 않으므로 한글이 함께 까는
# 같은 계열(HAN바탕/HAN돋움)의 실제 TTF 를 쓴다. 한글 글자는 1em 으로 고정한다.
FONT_DIR = Path("/Applications/Hancom Office HWP.app/Contents/Resources/Hnc/Shared/TTF/Install")
SERIF_TTF, SANS_TTF = FONT_DIR / "HANBatang.ttf", FONT_DIR / "HANDotum.ttf"


def latin_widths(ttf_path):
    """ASCII 한 글자의 advance width 를 em 대비 비율로 뽑는다."""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    if not ttf_path.exists():
        return None
    font = TTFont(str(ttf_path), fontNumber=0, lazy=True)
    upem = font["head"].unitsPerEm
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    table = {}
    for code in range(32, 127):
        name = cmap.get(code)
        if name and name in hmtx.metrics:
            table[chr(code)] = hmtx[name][0] / upem
    font.close()
    return table or None


FALLBACK_LATIN = 0.5   # TTF 를 못 읽을 때만 쓰는 근사. 결과에 표시한다.


def char_width(ch, latin):
    """em 대비 글자 폭. 한글·한자·전각기호는 1em 으로 본다."""
    if ch == "\t":
        return 2.0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 1.0
    if latin and ch in latin:
        return latin[ch]
    return FALLBACK_LATIN


def line_count(text, font_size, avail_width, latin):
    """어절 단위로 줄을 접어 줄 수를 센다. 한글은 어절이 길지 않아 이 근사가 잘 맞는다."""
    if not text.strip():
        return 1
    limit = avail_width / font_size          # 한 줄에 들어가는 em 수
    lines, used = 1, 0.0
    for word in re.findall(r"\S+\s*", text):
        width = sum(char_width(c, latin) for c in word)
        if used and used + width > limit:
            lines += 1
            used = width
        else:
            used += width
        while used > limit:                  # 한 어절이 한 줄보다 길면 쪼개진다
            used -= limit
            lines += 1
    return lines


# ── 조판본 읽기 ────────────────────────────────────────────────────────

def margin_value(para_xml, name):
    """내어쓰기는 음수로 저장된다(과제 리포트 디자인). 값이 없는 문단 모양은 0 으로 본다."""
    m = re.search(rf'<hc:{name} value="(-?\d+)"', para_xml)
    return int(m.group(1)) if m else 0


def load_shapes(header):
    chars = {
        int(m.group(1)): int(m.group(2))
        for m in re.finditer(r'<hh:charPr id="(\d+)" height="(\d+)"', header)
    }
    paras = {}
    for m in re.finditer(r'<hh:paraPr id="(\d+)".*?</hh:paraPr>', header, re.S):
        body = m.group(0)
        spacing = re.search(r'lineSpacing type="PERCENT" value="(\d+)"', body)
        paras[int(m.group(1))] = {
            "spacing": int(spacing.group(1)) if spacing else 160,
            "prev": margin_value(body, "prev"),
            "next": margin_value(body, "next"),
            "left": margin_value(body, "left"),
        }
    return chars, paras


def page_geometry(section):
    page = re.search(r'<hp:pagePr[^>]*width="(\d+)" height="(\d+)"', section)
    margin = re.search(
        r'<hp:margin header="(\d+)" footer="(\d+)" gutter="\d+" '
        r'left="(\d+)" right="(\d+)" top="(\d+)" bottom="(\d+)"',
        section,
    )
    width, height = int(page.group(1)), int(page.group(2))
    header, footer, left, right, top, bottom = (int(g) for g in margin.groups())
    return {
        "text_width": width - left - right,
        "text_height": height - top - bottom - header - footer,
    }


TAG = re.compile(r"<hp:(p|tbl|pic|t|run|tc|tr)\b|</hp:(p|tbl|pic|t)>")


def strip_tags(xml):
    text = re.sub(r"<[^>]+>", "", xml)
    return (
        text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&#13;", "")
    )


def cell_blocks(tc_xml):
    """셀 안의 문단들을 (문단모양, 글자크기, 텍스트) 로 돌려준다."""
    out = []
    for p in re.finditer(r'<hp:p [^>]*paraPrIDRef="(\d+)"[^>]*>(.*?)</hp:p>', tc_xml, re.S):
        sizes = [int(c) for c in re.findall(r'charPrIDRef="(\d+)"', p.group(2))]
        out.append((int(p.group(1)), sizes, strip_tags(p.group(2))))
    return out


def cell_margin(tc_xml):
    """셀 여백은 조판본마다 다르다(11번 141, 과제 리포트 디자인 좌우 510). 고정값을 쓰면 폭이 틀린다."""
    m = re.search(r'<hp:cellMargin left="(\d+)" right="(\d+)" top="(\d+)" bottom="(\d+)"', tc_xml)
    return tuple(int(g) for g in m.groups()) if m else (141, 141, 141, 141)


def measure(hwpx_path):
    zf = zipfile.ZipFile(hwpx_path)
    header = zf.read("Contents/header.xml").decode("utf-8")
    section = zf.read("Contents/section0.xml").decode("utf-8")
    chars, paras = load_shapes(header)
    geom = page_geometry(section)
    serif = latin_widths(SERIF_TTF)
    sans = latin_widths(SANS_TTF)
    latin = serif or sans

    def para_height(para_id, size_ids, text, avail_width):
        shape = paras.get(para_id, {"spacing": 160, "prev": 0, "next": 0, "left": 0})
        sizes = [chars.get(i, 1000) for i in size_ids] or [1000]
        size = max(sizes)
        lines = line_count(text, size, avail_width - shape["left"], latin)
        return lines * size * shape["spacing"] / 100 + shape["prev"] + shape["next"], lines

    items, cursor = [], 0
    for m in re.finditer(r'<hp:p [^>]*paraPrIDRef="(\d+)"[^>]*>', section):
        start = m.start()
        depth, i = 0, start
        # 표·그림이 안에 들어 있는 문단이 있어 짝 맞는 </hp:p> 를 직접 찾는다
        for t in re.finditer(r"<hp:p\b|</hp:p>", section[start:]):
            if t.group(0) == "</hp:p>":
                depth -= 1
                if depth == 0:
                    i = start + t.end()
                    break
            else:
                depth += 1
        body = section[start:i]
        if start < cursor:
            continue
        cursor = i
        para_id = int(m.group(1))
        if 'pageBreak="1"' in m.group(0):
            items.append(("쪽나눔", 0, 0))   # 문단 모양의 '쪽 나눔' — 다음 쪽에서 시작한다

        table = re.search(r"<hp:tbl .*?</hp:tbl>", body, re.S)
        pic = re.search(r"<hp:pic .*?</hp:pic>", body, re.S)
        if table:
            height = 0
            for tr in re.finditer(r"<hp:tr>.*?</hp:tr>", table.group(0), re.S):
                row_max = 0
                for tc in re.finditer(r"<hp:tc .*?</hp:tc>", tr.group(0), re.S):
                    cell_w = int(re.search(r'<hp:cellSz width="(\d+)"', tc.group(0)).group(1))
                    left, right, top, bottom = cell_margin(tc.group(0))
                    inner = cell_w - left - right
                    total = 0
                    for pid, sizes, text in cell_blocks(tc.group(0)):
                        h, _ = para_height(pid, sizes, text, inner)
                        total += h
                    row_max = max(row_max, total + top + bottom)
                height += row_max
            out_margin = re.search(r'<hp:outMargin[^>]*top="(\d+)" bottom="(\d+)"', table.group(0))
            height += int(out_margin.group(1)) + int(out_margin.group(2))
            caption = re.search(r'<hp:caption [^>]*gap="(\d+)"[^>]*>.*?</hp:caption>', table.group(0), re.S)
            if caption:   # 표 캡션도 표 높이에 더한다(과제 리포트 디자인)
                width = int(re.search(r'<hp:sz width="(\d+)"', table.group(0)).group(1))
                height += int(caption.group(1)) + sum(
                    para_height(pid, sizes, text, width)[0] for pid, sizes, text in cell_blocks(caption.group(0))
                )
            items.append(("표", height, len(re.findall(r"<hp:tr>", table.group(0)))))
        elif pic:
            size = re.search(r'<hp:curSz width="(\d+)" height="(\d+)"', pic.group(0))
            caption = re.search(r"<hp:caption.*?</hp:caption>", pic.group(0), re.S)
            cap_h = 0
            if caption:
                for pid, sizes, text in cell_blocks(caption.group(0)):
                    h, _ = para_height(pid, sizes, text, int(size.group(1)))
                    cap_h += h
            items.append(("그림", int(size.group(2)) + cap_h + 400, 1))
        else:
            sizes = [int(c) for c in re.findall(r'charPrIDRef="(\d+)"', body)]
            text = strip_tags(body)
            height, lines = para_height(para_id, sizes, text, geom["text_width"])
            items.append(("문단", height, lines))

    return items, geom, {"latin_source": SERIF_TTF.name if serif else "근사 0.5em"}


def paginate(items, page_height):
    """블록을 순서대로 쌓다가 넘치면 다음 쪽으로 보낸다.

    표와 그림은 통째로 넘긴다 — 한글도 기본적으로 그렇게 움직인다. 실제로는 표가
    쪽 경계에서 행 단위로 쪼개지므로, 이 처리는 쪽 수를 조금 더 나오게 만든다.
    """
    pages, used = 1, 0.0
    for kind, height, _ in items:
        if kind == "쪽나눔":
            if used > 0:
                pages, used = pages + 1, 0.0
            continue
        if used + height > page_height:
            if kind in ("표", "그림") and height <= page_height:
                pages += 1
                used = height
            else:
                remain = height - (page_height - used)
                pages += 1 + int(remain // page_height)
                used = remain % page_height
        else:
            used += height
    return pages, used


def write_report(hwpx_path, items, geom, pages, used, meta):
    kinds = {}
    for kind, height, count in items:
        agg = kinds.setdefault(kind, [0, 0.0, 0])
        agg[0] += 1
        agg[1] += height
        agg[2] += count
    total = sum(h for _, h, _ in items)
    lines = [
        "# 매수 측정",
        "",
        f"대상 `{hwpx_path.relative_to(ROOT)}` · 재현 `python3 03_분석/12_측정_매수.py`",
        "",
        "HWPX 는 쪽 수를 저장하지 않는다. 조판본 XML 의 문단 모양·표 셀 높이·그림 크기를",
        "읽어 레이아웃을 다시 쌓아 센 값이다.",
        "",
        "## 결과",
        "",
        f"| 본문 총 높이 | {total:,.0f} HWPUNIT |",
        "|---|---|",
        f"| 한 쪽 본문 높이 | {geom['text_height']:,} HWPUNIT |",
        f"| 한 쪽 본문 폭 | {geom['text_width']:,} HWPUNIT |",
        f"| **계산된 매수** | **{pages}매** |",
        f"| 마지막 쪽 채움 | {used / geom['text_height'] * 100:.0f}% |",
        f"| 라틴 글자폭 출처 | {meta['latin_source']} |",
        "",
        "## 구성",
        "",
        "| 종류 | 개수 | 차지 높이 | 비중 | 줄·행 수 |",
        "|---|---:|---:|---:|---:|",
    ]
    for kind, (n, height, count) in sorted(kinds.items(), key=lambda kv: -kv[1][1]):
        lines.append(
            f"| {kind} | {n} | {height:,.0f} | {height / total * 100:.0f}% | {count:,} |"
        )
    lines += [
        "",
        "## 이 숫자를 어떻게 읽어야 하는가",
        "",
        "- **재현하지 않은 것**: 외톨이줄 보호, 표가 쪽 경계에서 행 단위로 쪼개지는 규칙,",
        "  자간 미세조정. 앞의 둘은 쪽 수를 늘리는 쪽으로, 표 분할은 줄이는 쪽으로 작용한다.",
        "- 한글 글자는 1em 으로 고정했다. 분량의 대부분이 한글이라 지배적인 항은 안정적이다.",
        "- **20매 상한을 판정할 때는 여유를 두고 읽어라.** 계산값이 상한에 붙어 있으면",
        "  실제로는 넘을 수 있다.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwpx", default=str(DEFAULT_HWPX))
    args = parser.parse_args()
    path = Path(args.hwpx if Path(args.hwpx).is_absolute() else ROOT / args.hwpx)
    if not path.exists():
        sys.exit(f"조판본을 찾을 수 없다: {path}")

    items, geom, meta = measure(path)
    pages, used = paginate(items, geom["text_height"])
    write_report(path, items, geom, pages, used, meta)

    total = sum(h for _, h, _ in items)
    print(f"블록 {len(items)}개 · 총 높이 {total:,.0f} · 한 쪽 {geom['text_height']:,}")
    print(f"계산된 매수: {pages}매 (마지막 쪽 {used / geom['text_height'] * 100:.0f}% 채움)")
    print(f"라틴 글자폭: {meta['latin_source']}")
    verdict = "10~20매 조건 충족" if 10 <= pages <= 20 else "**조건 위반**"
    print(f"판정: {verdict}")
    print(f"→ {OUT_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
