"""복지×보건의료 접근성 분석 후보 데이터셋을 실제로 내려받아 스키마·행수·결측률을 실측한다.

링크 확인이 아니라 파일을 받아서 읽는다. 상세페이지에서 관리부서·갱신주기까지 긁어오는데,
심사 배점의 '정책 실현 가능성'은 "어느 부서가 어떤 주기로 쓰는가"를 써야 점수가 되기 때문이다.

출력: 02_데이터/raw/후보데이터/<데이터명>/<원본파일>
      02_데이터/clean/후보데이터_프로파일.json
      03_분석/후보데이터_비교표.md
재현: python3 03_분석/03_실측_후보데이터_프로파일.py
"""
import csv
import html
import io
import json
import re
import ssl
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "02_데이터" / "raw" / "후보데이터"
PROFILE_JSON = ROOT / "02_데이터" / "clean" / "후보데이터_프로파일.json"
OUT_MD = ROOT / "03_분석" / "후보데이터_비교표.md"
PORTAL = "https://bigdata.changwon.go.kr/portal/dataset"

# 도메인 확정: 복지 × 보건의료 접근성. 수요(고령·장애) / 공급(복지시설·의료기관) 양쪽을 고른다.
CANDIDATES = [
    {"seq": 188, "역할": "수요 결집지점 (고령)", "비고": "역대 수상작 미사용"},
    {"seq": 248, "역할": "공급 (재가 노인 급식)", "비고": "역대 수상작 미사용"},
    {"seq": 134, "역할": "공급 (의료기관·의창성산)", "비고": "2025 최우수작이 유사 데이터 사용"},
    {"seq": 132, "역할": "공급 (의료기관·마산합포회원)", "비고": "구별 분할 수록"},
    {"seq": 135, "역할": "공급 (의료기관·진해)", "비고": "구별 분할 수록"},
    {"seq": 761, "역할": "수요 분모 (장애인 등록)", "비고": "행정동 집계값"},
]

# 자동 다운로드가 막혀 후보에서 제외한 것도 기록한다 — 공개 검증 때 재현 불가한 데이터는 쓸 수 없다.
REJECTED = [
    {
        "데이터명": "경상남도 창원시_약국현황 (포털 외부연계)",
        "url": "https://www.data.go.kr/data/15045036/fileData.do",
        "사유": "실체는 행정안전부_건강_약국_20251127 전국 파일(67,651건). "
        "파일 호스트 file.localdata.go.kr 가 스크립트 접근을 403으로 차단 → 재현 불가",
    },
]

# TLS 검증은 켠 채로 둔다. 분석 원본을 받는 경로에서 검증을 끄면 중간자 공격에 노출된다.
CTX = ssl.create_default_context()

META_KEYS = ("제공기관", "분류체계", "관리부서", "확장자", "갱신주기", "이용허락범위", "데이터 최근 수정일")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
        return r.read()


def text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def scrape_meta(seq):
    page = get(f"{PORTAL}/datasetView.do?seq={seq}").decode("utf-8", errors="replace")
    cells = re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", page, re.S)
    meta = {}
    for k, v in cells:
        key = text(k)
        if key in META_KEYS:
            meta[key] = text(v)
    meta["데이터명"] = text(re.search(r"<th[^>]*>파일 데이터 명</th>\s*<td[^>]*>(.*?)</td>", page, re.S).group(1))
    files = re.findall(r'href="(/portal/dataset/downloadFile\.do\?seq=\d+&boardIdx=\d+)"[^>]*>([^<]+)</a>', page)
    meta["첨부파일"] = [
        {
            "url": u.replace("&amp;", "&"),
            # 링크 텍스트는 "파일명.csv (125kb)" 꼴이라 표시용 용량을 떼야 확장자가 잡힌다
            "파일명": re.sub(r"\s*\(\d+\s*[kKmM]?[bB]\)\s*$", "", text(n)),
            "표기용량": (re.search(r"\((\d+\s*[kKmM]?[bB])\)\s*$", text(n)) or [None, "-"])[1]
            if re.search(r"\((\d+\s*[kKmM]?[bB])\)\s*$", text(n))
            else "-",
        }
        for u, n in files
    ]
    meta["상세페이지"] = f"{PORTAL}/datasetView.do?seq={seq}"
    return meta


def decode(blob):
    for enc in ("utf-8-sig", "cp949", "utf-8"):
        try:
            return blob.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return blob.decode("utf-8", errors="replace"), "utf-8(손실)"


COORD_HINT = ("위도", "경도", "좌표", "lat", "lon", "x", "y")


def profile_csv(blob):
    body, enc = decode(blob)
    rows = list(csv.reader(io.StringIO(body)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    header, data = rows[0], rows[1:]
    missing = {}
    for i, col in enumerate(header):
        blank = sum(1 for r in data if i >= len(r) or not r[i].strip())
        missing[col.strip()] = round(blank / len(data) * 100, 1) if data else 100.0
    coords = [c for c in header if any(h in c.lower() for h in COORD_HINT)]
    return {
        "인코딩": enc,
        "행수": len(data),
        "열수": len(header),
        "컬럼": [c.strip() for c in header],
        "컬럼별_결측률_퍼센트": missing,
        "결측_있는_컬럼수": sum(1 for v in missing.values() if v > 0),
        "전체_결측률_퍼센트": round(sum(missing.values()) / len(missing), 1) if missing else None,
        "좌표_추정컬럼": coords,
    }


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    PROFILE_JSON.parent.mkdir(parents=True, exist_ok=True)
    results = []

    for cand in CANDIDATES:
        meta = scrape_meta(cand["seq"])
        if not meta["첨부파일"]:
            results.append({**cand, **meta, "실측": {"오류": "첨부파일 없음"}})
            continue
        latest = meta["첨부파일"][-1]  # 파일명에 날짜가 들어가며 마지막이 최신
        blob = get("https://bigdata.changwon.go.kr" + latest["url"])

        folder = RAW / re.sub(r"[^\w가-힣().-]+", "_", meta["데이터명"])
        folder.mkdir(parents=True, exist_ok=True)
        saved = folder / latest["파일명"]
        saved.write_bytes(blob)

        if saved.suffix.lower() == ".csv":
            measured = profile_csv(blob)
        else:
            measured = {"오류": f"{saved.suffix} — CSV 아님, 별도 처리 필요", "바이트": len(blob)}

        results.append(
            {
                **cand,
                **{k: v for k, v in meta.items() if k != "첨부파일"},
                "받은파일": latest["파일명"],
                "파일버전수": len(meta["첨부파일"]),
                "저장경로": str(saved.relative_to(ROOT)),
                "실측": measured,
            }
        )
        print(f"[{cand['seq']}] {meta['데이터명']} → {measured.get('행수', measured.get('오류'))}")

    PROFILE_JSON.write_text(
        json.dumps({"후보": results, "제외": REJECTED}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown(results)
    print(f"\n→ {OUT_MD}")


def write_markdown(results):
    lines = [
        "# 후보 데이터 실측 비교표 — 복지 × 보건의료 접근성",
        "",
        "생성: `03_분석/03_실측_후보데이터_프로파일.py`. 링크 확인이 아니라 실제 파일을 받아서 읽은 결과.",
        "",
        "| 데이터명 | 역할 | 관리부서 | 갱신주기 | 최근수정일 | 행수 | 열수 | 전체결측률 | 좌표컬럼 | 인코딩 |",
        "|---|---|---|---|---|---:|---:|---:|---|---|",
    ]
    for r in results:
        m = r["실측"]
        if "오류" in m:
            lines.append(
                f"| {r.get('데이터명','?')} | {r['역할']} | {r.get('관리부서','-')} | "
                f"{r.get('갱신주기','-')} | {r.get('데이터 최근 수정일','-')} | "
                f"— | — | — | — | {m['오류']} |"
            )
            continue
        coords = ", ".join(m["좌표_추정컬럼"]) or "**없음**"
        lines.append(
            f"| {r['데이터명']} | {r['역할']} | {r.get('관리부서','-')} | {r.get('갱신주기','-')} | "
            f"{r.get('데이터 최근 수정일','-')} | {m['행수']} | {m['열수']} | "
            f"{m['전체_결측률_퍼센트']}% | {coords} | {m['인코딩']} |"
        )

    lines += ["", "## 컬럼별 결측률", ""]
    for r in results:
        m = r["실측"]
        if "오류" in m:
            continue
        lines += [f"### {r['데이터명']}", "", f"`{r['받은파일']}` · 파일버전 {r['파일버전수']}개 · [상세]({r['상세페이지']})", ""]
        worst = sorted(m["컬럼별_결측률_퍼센트"].items(), key=lambda kv: -kv[1])
        lines += ["| 컬럼 | 결측률 |", "|---|---:|"]
        lines += [f"| {c} | {v}% |" for c, v in worst]
        lines.append("")

    lines += ["## 후보에서 제외한 것", ""]
    for r in REJECTED:
        lines += [f"- **{r['데이터명']}** — {r['사유']}", f"  - {r['url']}"]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
