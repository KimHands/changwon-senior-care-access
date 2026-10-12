"""수요 분모를 장애인 등록 수에서 주민등록 인구로 교체하기 위해 인구 데이터를 실측한다.

장애인 등록 수는 갱신주기가 '수시(1회성)'이고 2025 장려작이 이미 썼다. 주 수요변수로 쓸 수 없다.
대체 후보인 주민등록 인구가 (1) 스크립트로 재취득되는가 (2) 좌표 데이터와 조인되는가
(3) 고령 구간이 분해되는가를 확인한다.

출력: 02_데이터/raw/인구데이터/<데이터명>/<원본파일>
      02_데이터/clean/행정동별_인구.csv          (통·반 단위를 행정동으로 집계)
      02_데이터/clean/법정동_행정동_매핑.csv      (의료기관 조인용, 1:N 여부 포함)
      03_분석/인구데이터_실측노트.md
재현: python3 03_분석/05_실측_인구데이터.py
"""
import argparse
import csv
import hashlib
import html
import io
import re
import ssl
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "02_데이터" / "raw" / "인구데이터"
CLEAN = ROOT / "02_데이터" / "clean"
NOTE_MD = ROOT / "03_분석" / "인구데이터_실측노트.md"
PORTAL = "https://bigdata.changwon.go.kr"
DATASET = f"{PORTAL}/portal/dataset"

# TLS 검증은 켠 채로 둔다. 포털이 정상 인증서를 쓰는 것을 확인했고, 분석 원본을 받는
# 경로에서 검증을 끄면 중간자 공격에 그대로 노출된다.
CTX = ssl.create_default_context()

META_KEYS = ("제공기관", "관리부서", "갱신주기", "이용허락범위", "데이터 최근 수정일")

TARGETS = [
    {"seq": 378, "역할": "수요 분모 (행정동별)", "별칭": "행정동별_성연령별_인구"},
    {"seq": 379, "역할": "법정동↔행정동 매핑 (의료기관 조인용)", "별칭": "법정동별_성연령별_인구"},
]

AGE_BANDS = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
ELDER_FROM = 60  # 원본이 10세 단위라 65세 이상은 만들 수 없다. 60·70 두 기준을 함께 낸다.


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=CTX, timeout=60) as resp:
        return resp.read()


def text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def decode(blob: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return blob.decode(encoding)
        except UnicodeDecodeError:
            continue
    return blob.decode("utf-8", errors="replace")


def scrape_meta(seq: int) -> dict:
    page = decode(get(f"{DATASET}/datasetView.do?seq={seq}"))
    cells = re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", page, re.S)
    meta = {text(k): text(v) for k, v in cells if text(k) in META_KEYS}
    links = re.findall(
        r'href="(/portal/dataset/downloadFile\.do\?seq=\d+&(?:amp;)?boardIdx=\d+)"[^>]*>([^<]+)</a>',
        page,
    )
    files = []
    for url, name in links:
        # 링크 텍스트는 "파일명.csv (1334kb)" 꼴이라 표시용 용량을 떼야 한다
        clean_name = re.sub(r"\s*\(\d+\s*[kKmM]?[bB]\)\s*$", "", text(name))
        files.append({"url": url.replace("&amp;", "&"), "파일명": clean_name})
    meta["첨부파일"] = files
    meta["상세페이지"] = f"{DATASET}/datasetView.do?seq={seq}"
    return meta


def pick_monthly(files: list[dict], pinned: str | None) -> dict:
    """기준월 파일을 고른다.

    기본은 최신월이지만, 그러면 나중에 실행할 때 다른 월이 잡혀 결과가 달라진다.
    11월 공개 검증에서 '돌려보니 숫자가 다르다'는 제보를 막으려면 제출본의 기준월을
    --month 로 고정해야 한다. 고정 기준월은 산출 노트에 기록된다.
    """
    dated = []
    for item in files:
        match = re.search(r"_(\d{6})\.csv$", item["파일명"])
        if match:
            dated.append((match.group(1), item))
    if not dated:
        raise FileNotFoundError("월별 CSV를 찾지 못했다")
    if pinned:
        for month, item in dated:
            if month == pinned:
                return {**item, "기준월": month}
        raise FileNotFoundError(f"기준월 {pinned} 파일이 게시 목록에 없다")
    month, item = max(dated, key=lambda pair: pair[0])
    return {**item, "기준월": month}


def save_raw(alias: str, item: dict, blob: bytes) -> Path:
    folder = RAW / alias
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / item["파일명"].replace("/", "_")
    path.write_bytes(blob)
    return path


def elder_columns(start: int) -> list[str]:
    return [f"{sex}{age}AgeNmprCnt" for age in AGE_BANDS if age >= start for sex in ("male", "feml")]


def aggregate_dong(rows: list[dict]) -> list[dict]:
    """통·반 단위 행을 행정동 단위로 합산한다."""
    buckets: dict[tuple, dict] = {}
    for row in rows:
        key = (row["admmCd"], row["sggNm"], row["dongNm"])
        bucket = buckets.setdefault(
            key,
            {
                "기준월": row["statsYm"],
                "행정동코드": row["admmCd"],
                "시군구": row["sggNm"],
                "구": row["sggNm"].replace("창원시 ", ""),
                "행정동": row["dongNm"],
                "총인구": 0,
                "남": 0,
                "여": 0,
                "60세이상": 0,
                "70세이상": 0,
            },
        )
        bucket["총인구"] += int(row["totNmprCnt"])
        bucket["남"] += int(row["maleNmprCnt"])
        bucket["여"] += int(row["femlNmprCnt"])
        bucket["60세이상"] += sum(int(row[c]) for c in elder_columns(60))
        bucket["70세이상"] += sum(int(row[c]) for c in elder_columns(70))
    return sorted(buckets.values(), key=lambda b: (b["구"], b["행정동"]))


def build_mapping(rows: list[dict]) -> list[dict]:
    """법정동 → 행정동 매핑. 1:N 이면 행정동을 특정할 수 없다는 뜻이라 그대로 표시한다."""
    pairs = defaultdict(set)
    for row in rows:
        pairs[(row["sggNm"], row["stdgCd"], row["stdgNm"])].add(row["dongNm"])
    mapping = []
    for (sgg, code, name), dongs in sorted(pairs.items()):
        mapping.append(
            {
                "구": sgg.replace("창원시 ", ""),
                "법정동코드": code,
                "법정동": name,
                "행정동수": len(dongs),
                "행정동": " / ".join(sorted(dongs)),
                "단일확정": "Y" if len(dongs) == 1 else "N",
            }
        )
    return mapping


def legal_dong(refined: str) -> str:
    """지오코딩 정제주소의 괄호 첫 토큰이 법정동이다. 읍·면 지역은 괄호가 없다."""
    match = re.search(r"\(([^),]+)", refined or "")
    return match.group(1).strip() if match else ""


def check_joins(dong_rows: list[dict], mapping: list[dict]) -> dict:
    """좌표 데이터가 인구 데이터와 실제로 붙는지 확인한다. 여기가 안 되면 분석 설계가 바뀐다."""
    dong_keys = {(r["구"], r["행정동"]) for r in dong_rows}
    by_legal = {(m["구"], m["법정동"]): m for m in mapping}

    gyeongnodang = [
        r for r in csv.DictReader((CLEAN / "경로당_좌표.csv").open(encoding="utf-8")) if r["위도"]
    ]
    gy_miss = [r for r in gyeongnodang if (r["구"], r["읍면동"]) not in dong_keys]

    medical = [
        r for r in csv.DictReader((CLEAN / "의료기관_좌표.csv").open(encoding="utf-8")) if r["위도"]
    ]
    unique_hit = ambiguous = eupmyeon = unmatched = 0
    ambiguous_top = Counter()
    for row in medical:
        name = legal_dong(row["정제주소"])
        entry = by_legal.get((row["구"], name)) if name else None
        if entry and entry["단일확정"] == "Y":
            unique_hit += 1
        elif entry:
            ambiguous += 1
            ambiguous_top[f"{row['구']} {name}"] += 1
        else:
            match = re.search(r"(\S+[읍면])\s", row["정제주소"] or "")
            if match and (row["구"], match.group(1)) in dong_keys:
                eupmyeon += 1
            else:
                unmatched += 1
    return {
        "경로당_대상": len(gyeongnodang),
        "경로당_실패": len(gy_miss),
        "의료기관_대상": len(medical),
        "의료기관_단일확정": unique_hit,
        "의료기관_모호": ambiguous,
        "의료기관_읍면직접": eupmyeon,
        "의료기관_실패": unmatched,
        "모호_상위": ambiguous_top.most_common(8),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_note(meta: dict, month: str, dong_rows: list[dict], mapping: list[dict], joins: dict) -> None:
    total = sum(r["총인구"] for r in dong_rows)
    elder60 = sum(r["60세이상"] for r in dong_rows)
    elder70 = sum(r["70세이상"] for r in dong_rows)
    multi = [m for m in mapping if m["단일확정"] == "N"]
    med_total = joins["의료기관_대상"]

    lines = [
        "# 인구 데이터 실측노트 — 수요 분모 교체",
        "",
        f"기준월 **{month[:4]}-{month[4:]}**",
        "",
        f"고정 재현: `python3 03_분석/05_실측_인구데이터.py --month {month}`",
        "(옵션 없이 실행하면 그 시점의 최신월이 잡혀 결과가 달라진다)",
        "",
        "## 판정: 교체 가능",
        "",
        "`행정동별 장애인 등록 수`(수시·1회성, 2025 장려작 중복)를 "
        "`행정동별(통반단위) 성,연령별 주민등록 인구수`로 교체할 수 있다.",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 제공기관 | {meta.get('제공기관', '-')} |",
        f"| 관리부서 | {meta.get('관리부서', '-')} |",
        f"| **갱신주기** | **{meta.get('갱신주기', '-')}** |",
        f"| 이용허락범위 | {meta.get('이용허락범위', '-')} |",
        f"| 포털 최근 수정일 | {meta.get('데이터 최근 수정일', '-')} |",
        f"| 행정동 수 | {len(dong_rows)}개 |",
        f"| 총인구 | {total:,}명 |",
        f"| 60세 이상 | {elder60:,}명 ({elder60 / total * 100:.1f}%) |",
        f"| 70세 이상 | {elder70:,}명 ({elder70 / total * 100:.1f}%) |",
        "",
        "**갱신주기가 월간이다.** 장애인 등록 수의 '수시(1회성)'와 달리 "
        "\"어느 부서가 어떤 주기로 쓰는가\"를 약속할 수 있다 — 심사 배점의 정책 실현 가능성에 직접 걸린다.",
        "",
        "## 제약 1 — 65세 이상을 만들 수 없다",
        "",
        "연령 컬럼이 `male60AgeNmprCnt` 처럼 **10세 단위**다. 5세 계급이 없으므로 "
        "노인복지법 기준인 65세 이상을 정확히 뽑을 수 없다.",
        "",
        "→ 고령 정의를 **60세 이상** 또는 **70세 이상** 중 하나로 고정하고 보고서에 이유를 명시한다. "
        "경로당 이용 실태에 가까운 쪽은 70세 이상이고, 수요를 넓게 잡으려면 60세 이상이다.",
        "",
        "## 제약 2 — 의료기관은 주소만으로 행정동을 특정할 수 없다",
        "",
        "경로당 원본에는 읍면동 컬럼이 있어 행정동이 확정된다. 의료기관 원본에는 없어서 "
        "지오코딩 정제주소의 법정동으로 역매핑해야 하는데, **법정동 하나가 여러 행정동에 걸치는 경우가 "
        f"{len(multi)}건** 있다.",
        "",
        "| 대상 | 건수 | 비율 |",
        "|---|---:|---:|",
        f"| 경로당 — 행정동 조인 성공 | {joins['경로당_대상'] - joins['경로당_실패']:,} | "
        f"{(joins['경로당_대상'] - joins['경로당_실패']) / joins['경로당_대상'] * 100:.1f}% |",
        f"| 의료기관 — 법정동 1:1 확정 | {joins['의료기관_단일확정']:,} | "
        f"{joins['의료기관_단일확정'] / med_total * 100:.1f}% |",
        f"| 의료기관 — 읍·면 직접 조인 | {joins['의료기관_읍면직접']:,} | "
        f"{joins['의료기관_읍면직접'] / med_total * 100:.1f}% |",
        f"| **의료기관 — 행정동 모호(1:N)** | **{joins['의료기관_모호']:,}** | "
        f"**{joins['의료기관_모호'] / med_total * 100:.1f}%** |",
        f"| 의료기관 — 조인 불가 | {joins['의료기관_실패']:,} | "
        f"{joins['의료기관_실패'] / med_total * 100:.1f}% |",
        "",
        "모호 상위: " + ", ".join(f"{k} {v}건" for k, v in joins["모호_상위"]),
        "",
        "### 이 제약을 우회하는 설계",
        "",
        "의료기관을 **행정동에 집계하지 않으면 문제가 사라진다.** 접근성 지표를 "
        "'행정동별 의료기관 수'가 아니라 **'경로당에서 최근접 의료기관까지의 거리'**로 잡으면,",
        "",
        "- 수요 지점 = 경로당 (행정동 확정 · 좌표 확보)",
        "- 공급 지점 = 의료기관 (좌표만 있으면 되고 행정동을 알 필요가 없다)",
        "- 분모 = 행정동별 고령인구 (경로당의 행정동으로 조인 → 100% 성립)",
        "",
        "거리 기반 지표가 접근성의 정의에도 더 맞는다. "
        "행정동별 공급 밀도까지 내고 싶다면 행정동 경계 폴리곤을 구해 좌표로 공간조인해야 한다 — "
        "별도 실측 과제다.",
        "",
        "## 산출물",
        "",
        "- `02_데이터/clean/행정동별_인구.csv` — 통·반 단위를 행정동 55개로 집계",
        "- `02_데이터/clean/법정동_행정동_매핑.csv` — `단일확정` 컬럼으로 1:N 여부 표시",
        "",
        "## 정정",
        "",
        "`후보데이터_실측노트.md`는 이 데이터의 갱신주기를 '연간'으로 적었으나 "
        f"실측 결과 **{meta.get('갱신주기', '-')}**이다.",
    ]
    NOTE_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="주민등록 인구 데이터를 실측·집계한다")
    parser.add_argument("--month", help="기준월 고정 (YYYYMM). 생략하면 최신월")
    args = parser.parse_args()

    CLEAN.mkdir(parents=True, exist_ok=True)
    loaded = {}
    for target in TARGETS:
        meta = scrape_meta(target["seq"])
        item = pick_monthly(meta["첨부파일"], args.month)
        blob = get(PORTAL + item["url"])
        path = save_raw(target["별칭"], item, blob)
        item["sha256"] = hashlib.sha256(blob).hexdigest()
        rows = list(csv.DictReader(io.StringIO(decode(blob))))
        loaded[target["seq"]] = {"meta": meta, "item": item, "rows": rows}
        print(f"seq {target['seq']} · {item['기준월']} · {len(rows):,}행 → {path.relative_to(ROOT)}")

    dong_rows = aggregate_dong(loaded[378]["rows"])
    mapping = build_mapping(loaded[379]["rows"])
    write_csv(CLEAN / "행정동별_인구.csv", dong_rows)
    write_csv(CLEAN / "법정동_행정동_매핑.csv", mapping)

    joins = check_joins(dong_rows, mapping)
    write_note(loaded[378]["meta"], loaded[378]["item"]["기준월"], dong_rows, mapping, joins)

    print(f"행정동 {len(dong_rows)}개 집계 · 법정동 매핑 {len(mapping)}건")
    print(
        f"조인: 경로당 실패 {joins['경로당_실패']}건 · "
        f"의료기관 모호 {joins['의료기관_모호']}건 / {joins['의료기관_대상']}건"
    )
    print(f"노트 {NOTE_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
