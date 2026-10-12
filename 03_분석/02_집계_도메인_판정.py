"""역대 수상작 도메인 분포 × 창원시 개방데이터 재고를 교차 집계해 도메인 판정표를 만든다.

입력: 03_분석/역대수상작_도메인코딩.csv        (요약서 원문을 읽고 수동 코딩)
      02_데이터/raw/changwon_portal_catalog.csv (01_수집 스크립트 산출)
출력: 03_분석/도메인_판정표.md
재현: python3 03_분석/02_집계_도메인_판정.py
"""
import collections
import csv
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AWARDS = ROOT / "03_분석" / "역대수상작_도메인코딩.csv"
CATALOG = ROOT / "02_데이터" / "raw" / "changwon_portal_catalog.csv"
OUT = ROOT / "03_분석" / "도메인_판정표.md"

TODAY = datetime.date(2026, 9, 13)

# 포털 분류(좌) → 수상작 코딩 도메인(우) 대응
CATEGORY_TO_DOMAIN = {
    "교통물류": "교통",
    "보건의료": "보건의료",
    "사회복지": "복지",
    "안전재난": "안전",
    "환경기상": "환경",
    "산업경제": "상권",
    "주거생활": "주거",
    "공공행정": "행정",
    "농축수산": "농수산",
    "문화관광": "문화관광",
    "교육연구": "교육",
    "국토관리": "국토",
    "통계": "통계(연감)",
}
RANK_WEIGHT = {"최우수": 3, "우수": 2, "장려": 1, "발표평가대상": 2}


def read(path):
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def fresh_count(rows):
    n = 0
    for r in rows:
        try:
            if (TODAY - datetime.date.fromisoformat(r["수정일"])).days <= 365:
                n += 1
        except ValueError:
            pass
    return n


def downloadable(rows):
    """url이 있고 파일형식이 표 형태인 것 = 실제로 내려받아 분석에 넣을 수 있는 건수."""
    return sum(1 for r in rows if r["url"] and r["형식"].upper() in ("CSV", "XLSX", "XLS"))


def main():
    awards, catalog = read(AWARDS), read(CATALOG)

    award_count = collections.Counter(a["도메인"] for a in awards)
    award_score = collections.Counter()
    top_prize = collections.Counter()
    for a in awards:
        award_score[a["도메인"]] += RANK_WEIGHT.get(a["상격"], 1)
        if a["상격"] == "최우수":
            top_prize[a["도메인"]] += 1

    by_domain = collections.defaultdict(list)
    for r in catalog:
        by_domain[CATEGORY_TO_DOMAIN.get(r["분류"], r["분류"])].append(r)

    domains = sorted(
        set(award_count) | set(by_domain),
        key=lambda d: (-award_count[d], -len(by_domain.get(d, []))),
    )

    lines = [
        "# 도메인 판정표 — 역대 수상실적 × 개방데이터 재고",
        "",
        f"기준일 {TODAY}. 수상작 {len(awards)}건(2020·2021·2022·2023·2025 / 2019·2024는 요약서 미공개),",
        f"창원시 데이터포털 카탈로그 {len(catalog)}건.",
        "가중점수 = 최우수 3 · 우수 2 · 장려 1 (2023년은 순위 미공개라 발표평가대상 전원 2점).",
        "",
        "| 도메인 | 수상 | 최우수 | 가중점수 | 데이터 | 표형식 다운로드 | 1년내 갱신 | 갱신율 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for d in domains:
        rows = by_domain.get(d, [])
        fresh = fresh_count(rows)
        rate = f"{fresh / len(rows) * 100:.0f}%" if rows else "-"
        lines.append(
            f"| {d} | {award_count[d]} | {top_prize[d]} | {award_score[d]} | "
            f"{len(rows)} | {downloadable(rows)} | {fresh} | {rate} |"
        )

    lines += ["", "## 분석형식 분포 (도메인보다 강한 신호)", "", "| 분석형식 | 건수 |", "|---|---:|"]
    shapes = collections.Counter()
    for a in awards:
        for part in a["분석형식"].split("+"):
            shapes[part] += 1
    for k, v in shapes.most_common():
        lines.append(f"| {k} | {v} |")

    lines += ["", "## 최우수상 수상작 (연도별)", "", "| 연도 | 부문 | 과제명 | 도메인 | 분석형식 |", "|---|---|---|---|---|"]
    for a in awards:
        if a["상격"] == "최우수":
            lines.append(
                f"| {a['연도']} | {a['부문']} | {a['과제명']} | {a['도메인']} | {a['분석형식']} |"
            )

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n→ {OUT}")


if __name__ == "__main__":
    main()
