"""창원시 데이터포털(bigdata.changwon.go.kr) 개방데이터 카탈로그 전수 수집.

출력: 02_데이터/raw/changwon_portal_catalog.csv
재현: python3 03_분석/01_수집_창원데이터포털_카탈로그.py
"""
import csv
import html
import re
import ssl
import time
import urllib.request
from pathlib import Path

BASE = "https://bigdata.changwon.go.kr/portal/dataset/datasetList.do"
OUT = Path(__file__).resolve().parent.parent / "02_데이터" / "raw" / "changwon_portal_catalog.csv"

LI = re.compile(r"(?s)<li>(.*?)</li>")
FIELD = re.compile(r"<dt>(?P<k>[^<]+)</dt>\s*<dd>(?P<v>[^<]*)</dd>")


def fetch(page):
    ctx = ssl.create_default_context()
    # TLS 검증은 켠 채로 둔다 — 분석 원본 취득 경로에서 끄면 중간자 공격에 노출된다.
    req = urllib.request.Request(f"{BASE}?page={page}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def parse(page_html):
    """목록 <li> 단위로 파싱. 포털 내부 상세(seq)와 data.go.kr 외부연계 항목을 모두 담는다."""
    m = re.search(r'(?s)<ul class="cwNewDataSetList">(.*?)</ul>', page_html)
    if not m:
        return
    for block in LI.finditer(m.group(1)):
        it = block.group(1)
        title = re.search(r"(?s)<h3>(.*?)</h3>", it)
        if not title:
            continue
        fields = {clean(f["k"]): clean(f["v"]) for f in FIELD.finditer(it)}
        seq = re.search(r"datasetView\.do\?seq=(\d+)", it)
        ext = re.search(r'href="(https?://www\.data\.go\.kr[^"]+)"', it)
        fmt = re.search(r'<span class="dataType[^"]*">([^<]*)</span>', it)
        cate = re.search(r'<span class="datasetCate">([^<]*)</span>', it)
        name = clean(re.sub(r'<span class="dataType.*?</span>', "", title.group(1), flags=re.S))
        yield {
            "seq": seq.group(1) if seq else "",
            "분류": clean(cate.group(1)) if cate else "",
            "형식": clean(fmt.group(1)) if fmt else "",
            "데이터명": name,
            "수정일": fields.get("수정일", ""),
            "조회수": fields.get("조회수", ""),
            "다운로드": fields.get("다운로드", ""),
            "제공처": "data.go.kr(외부연계)" if ext else "창원시데이터포털",
            "url": ext.group(1) if ext else (
                f"https://bigdata.changwon.go.kr/portal/dataset/datasetView.do?seq={seq.group(1)}" if seq else ""
            ),
        }


def main():
    first = fetch(1)
    total = int(re.search(r'search_count f_left">Total <b>(\d+)</b>', first).group(1))
    rows, seen = [], set()
    page, page_html = 1, first
    while True:
        batch = list(parse(page_html))
        if not batch:
            break
        for row in batch:
            key = row["url"] or row["데이터명"]
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        page += 1
        time.sleep(0.3)
        page_html = fetch(page)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"포털 표기 Total {total}건 / 목록에서 실제 수집 {len(rows)}건 → {OUT}")
    print(f"마지막 페이지 {page - 1}")


if __name__ == "__main__":
    main()
