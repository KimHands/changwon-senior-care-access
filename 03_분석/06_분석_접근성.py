"""경로당 단위 의료 접근성을 계산하고, 행정동 집계가 무엇을 감추는지 대조한다.

검토(2026-09-15)에서 v1 주제안의 결함 3종이 확인돼 재설계한 것이다.
  - 공급 집합에 요양·정신·치과병원이 섞여 접근성을 부풀리고 있었다 → 통원 가능 병원급만 분리
  - 우선순위 점수에 행정동 인구를 곱해 신도시(북면)가 1위가 됐다 → 경로당 단위로 닫힌 지표로 교체
  - 단일 설정 결과만 제시했다 → 임계·공급정의·집계단위·연령기준 민감도를 함께 낸다

핵심 산출은 '행정동 평균으로는 양호한데 개별 경로당은 취약한 곳'이다. 행정동 55개 폴리곤이
아니라 경로당 873개 지점으로 봐야만 보이는 것이고, 이 분석의 유일한 비자명 발견이다.

주의: 보건소·보건지소·보건진료소가 공급에 빠져 있다(원본이 의료법상 개설신고 기관만 수록).
농촌 취약도는 그만큼 과대추정이다. 별도 취득 후 재실행해야 한다.

출력: 02_데이터/clean/경로당_접근성.csv
      03_분석/접근성_분석결과.md
재현: python3 03_분석/06_분석_접근성.py
"""
import csv
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "02_데이터" / "clean"
OUT_CSV = CLEAN / "경로당_접근성.csv"
PUBLIC_CSV = CLEAN / "보건기관_좌표.csv"
OUT_MD = ROOT / "03_분석" / "접근성_분석결과.md"

# 통원 목적지로 볼 수 있는 것만 공급으로 센다. 요양병원은 입원시설, 정신·치과병원은 목적이 다르다.
CLINIC = {"의원", "한의원"}
CLINIC_NARROW = {"의원"}
HOSPITAL_AMBULATORY = {"병원", "종합병원", "한방병원"}
HOSPITAL_ALL = {
    "병원", "종합병원", "한방병원", "요양병원(일반요양병원)",
    "요양병원(노인병원)", "정신병원", "치과병원",
}
THRESHOLDS = (500, 1000, 1500, 2000)
PRIMARY_THRESHOLD = 1000


def read_rows(name: str) -> list[dict]:
    return list(csv.DictReader((CLEAN / name).open(encoding="utf-8")))


def point(row: dict) -> tuple[float, float]:
    return float(row["위도"]), float(row["경도"])


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    inner = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371000 * 2 * math.asin(math.sqrt(inner))


def nearest(origin: tuple[float, float], targets: list[tuple[float, float]]) -> float:
    return min(haversine(origin, t) for t in targets)


def percentile(sorted_values: list[float], q: float) -> float:
    """최근접 순위 방식. 보간 방식과 값이 다르므로 정의를 명시해 둔다."""
    index = max(0, math.ceil(q * len(sorted_values)) - 1)
    return sorted_values[index]


def is_rural(dong: str) -> bool:
    return dong.endswith(("읍", "면"))


def supply_points(medical: list[dict], kinds: set[str]) -> list[tuple[float, float]]:
    return [point(r) for r in medical if r["종별"] in kinds]


def public_points() -> list[tuple[float, float]]:
    """보건소·보건지소·보건진료소. 건강생활지원센터는 진료기관이 아니라 제외돼 있다."""
    rows = csv.DictReader(PUBLIC_CSV.open(encoding="utf-8"))
    return [point(r) for r in rows if r["위도"] and r["진료기관"] == "Y"]


def build_records(halls: list[dict], medical: list[dict]) -> list[dict]:
    private = supply_points(medical, CLINIC)
    public = public_points()
    primary = private + public
    clinic_narrow = supply_points(medical, CLINIC_NARROW)
    hospital = supply_points(medical, HOSPITAL_AMBULATORY)
    records = []
    for row in halls:
        origin = point(row)
        records.append(
            {
                "경로당명": row["기관명"],
                "구": row["구"],
                "행정동": row["읍면동"],
                "지역구분": "읍면" if is_rural(row["읍면동"]) else "동",
                "위도": row["위도"],
                "경도": row["경도"],
                # 판정은 반올림 전 거리로 한다. 먼저 반올림하면 1,000.3m 가 1,000m 로 접혀
                # 취약 판정에서 빠진다(제황경로당 사례). 반올림은 출력할 때만 한다.
                "최근접_1차의료_m": nearest(origin, primary),
                "최근접_민간의원급_m": nearest(origin, private),
                "최근접_공공보건_m": nearest(origin, public),
                "최근접_의원만_m": nearest(origin, clinic_narrow),
                "최근접_통원병원급_m": nearest(origin, hospital),
            }
        )
    return records


def resolution_gap(records: list[dict]) -> list[dict]:
    """행정동 평균은 임계 이하인데 개별 경로당은 임계를 넘는 곳 — 집계가 감추는 지점."""
    by_dong = defaultdict(list)
    for r in records:
        by_dong[(r["구"], r["행정동"])].append(r)
    gaps = []
    for (gu, dong), rows in by_dong.items():
        distances = [r["최근접_1차의료_m"] for r in rows]
        mean = sum(distances) / len(distances)
        hidden = [r for r in rows if r["최근접_1차의료_m"] > PRIMARY_THRESHOLD]
        if mean <= PRIMARY_THRESHOLD and hidden:
            gaps.append(
                {
                    "구": gu,
                    "행정동": dong,
                    "지역구분": rows[0]["지역구분"],
                    "경로당수": len(rows),
                    "행정동평균_m": round(mean),
                    "숨은취약_수": len(hidden),
                    "최원거리_m": round(max(distances)),
                    "최원_경로당": max(rows, key=lambda r: r["최근접_1차의료_m"])["경로당명"],
                }
            )
    return sorted(gaps, key=lambda g: -g["최원거리_m"])


def sensitivity(records: list[dict], halls: list[dict]) -> dict:
    """설정을 바꿔도 결론이 유지되는지 본다. 단일 설정 결과만 내면 근거가 약하다."""
    total = len(records)
    by_threshold = {
        t: sum(1 for r in records if r["최근접_1차의료_m"] > t) for t in THRESHOLDS
    }
    by_supply = {
        "민간 의원급 + 공공 보건기관 (주 지표)": sum(
            1 for r in records if r["최근접_1차의료_m"] > PRIMARY_THRESHOLD
        ),
        "민간 의원급만 (보건기관 제외)": sum(
            1 for r in records if r["최근접_민간의원급_m"] > PRIMARY_THRESHOLD
        ),
        "민간 의원만 (한의원 제외)": sum(
            1 for r in records if r["최근접_의원만_m"] > PRIMARY_THRESHOLD
        ),
        "통원 병원급": sum(1 for r in records if r["최근접_통원병원급_m"] > PRIMARY_THRESHOLD),
    }
    # 동일 좌표에 여러 경로당이 등록돼 있다. 시설 단위와 지점 단위가 다르다.
    by_site: dict[tuple, float] = {}
    for record, hall in zip(records, halls):
        by_site[(hall["위도"], hall["경도"])] = record["최근접_1차의료_m"]
    site_vulnerable = sum(1 for d in by_site.values() if d > PRIMARY_THRESHOLD)
    return {
        "총": total,
        "임계별": by_threshold,
        "공급정의별": by_supply,
        "지점수": len(by_site),
        "지점취약": site_vulnerable,
    }


def distance_stats(records: list[dict], field: str) -> tuple[float, float, float]:
    values = sorted(r[field] for r in records)
    return percentile(values, 0.5), percentile(values, 0.9), values[-1]


def write_report(records: list[dict], gaps: list[dict], sens: dict, elderly: dict) -> None:
    total = sens["총"]
    vulnerable = [r for r in records if r["최근접_1차의료_m"] > PRIMARY_THRESHOLD]
    rural = [r for r in vulnerable if r["지역구분"] == "읍면"]
    urban = [r for r in vulnerable if r["지역구분"] == "동"]
    all_rural = [r for r in records if r["지역구분"] == "읍면"]
    all_urban = [r for r in records if r["지역구분"] == "동"]

    lines = [
        "# 경로당 의료 접근성 분석 결과",
        "",
        f"재현 `python3 03_분석/06_분석_접근성.py` · 직선거리(Haversine, 지구반지름 6,371km)",
        f"· 임계 {PRIMARY_THRESHOLD:,}m · 90백분위는 최근접 순위 방식",
        "",
        "## 공급 집합 정의",
        "",
        "| 집합 | 종별 | 개수 |",
        "|---|---|---:|",
        f"| 민간 의원급 | 의원, 한의원 | {len(supply_points(MEDICAL, CLINIC))} |",
        f"| 공공 보건기관 | 보건소, 보건지소, 보건진료소 | {len(public_points())} |",
        f"| **1차의료 (주 지표)** | 위 둘의 합 | "
        f"{len(supply_points(MEDICAL, CLINIC)) + len(public_points())} |",
        f"| 통원 병원급 | 병원, 종합병원, 한방병원 | {len(supply_points(MEDICAL, HOSPITAL_AMBULATORY))} |",
        f"| (참고) 병원급 전체 | 요양·정신·치과병원 포함 | {len(supply_points(MEDICAL, HOSPITAL_ALL))} |",
        "",
        "요양병원은 입원시설이고 정신·치과병원은 목적이 달라 통원 접근성 공급에서 제외했다. "
        "포함하면 병원급 중앙값이 1,099m에서 832m로 낮아져 접근성이 과대평가된다.",
        "",
        "## 거리 분포",
        "",
        "| 지표 | 중앙값 | 90백분위 | 최대 |",
        "|---|---:|---:|---:|",
    ]
    for label, field in [
        ("경로당→1차의료(민간+공공)", "최근접_1차의료_m"),
        ("경로당→민간 의원급", "최근접_민간의원급_m"),
        ("경로당→공공 보건기관", "최근접_공공보건_m"),
        ("경로당→통원 병원급", "최근접_통원병원급_m"),
    ]:
        med, p90, mx = distance_stats(records, field)
        lines.append(f"| {label} | {med:,.0f}m | {p90:,.0f}m | {mx:,.0f}m |")

    lines += [
        "",
        "## 취약 경로당",
        "",
        f"의원급까지 {PRIMARY_THRESHOLD:,}m 초과 = **{len(vulnerable):,}개 / {total:,}개 "
        f"({len(vulnerable) / total * 100:.1f}%)**",
        "",
        "| 지역구분 | 경로당 | 취약 | 취약률 |",
        "|---|---:|---:|---:|",
        f"| 읍면 | {len(all_rural):,} | {len(rural):,} | {len(rural) / len(all_rural) * 100:.1f}% |",
        f"| 동 | {len(all_urban):,} | {len(urban):,} | {len(urban) / len(all_urban) * 100:.1f}% |",
        "",
        f"취약 경로당의 {len(rural) / len(vulnerable) * 100:.0f}%가 읍면에 있다. "
        "읍면의 의료 희소성은 행정동 인구표만 봐도 알 수 있는 자명한 사실이므로, "
        "**이것만으로는 분석의 기여라고 할 수 없다.**",
        "",
        "## 핵심 발견 — 행정동 집계가 감추는 취약 경로당",
        "",
        f"행정동 평균 거리는 {PRIMARY_THRESHOLD:,}m 이하여서 '양호'로 분류되지만, "
        f"그 안에 임계를 넘는 경로당이 있는 행정동이 **{len(gaps)}개**다. "
        f"숨은 취약 경로당은 **{sum(g['숨은취약_수'] for g in gaps)}개**.",
        "",
        "행정동 55개 단위로 요약하면 사라지고, 경로당 지점 단위로 봐야만 보인다.",
        "",
        "| 구 | 행정동 | 구분 | 경로당 | 행정동평균 | 숨은취약 | 최원거리 | 최원 경로당 |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for gap in gaps:
        lines.append(
            f"| {gap['구']} | {gap['행정동']} | {gap['지역구분']} | {gap['경로당수']} | "
            f"{gap['행정동평균_m']:,}m | {gap['숨은취약_수']} | {gap['최원거리_m']:,}m | "
            f"{gap['최원_경로당']} |"
        )

    lines += [
        "",
        "## 민감도",
        "",
        "설정을 바꿔도 결론이 유지되는지 확인한다.",
        "",
        "| 임계값 | 취약 경로당 | 비율 |",
        "|---:|---:|---:|",
    ]
    for t in THRESHOLDS:
        n = sens["임계별"][t]
        lines.append(f"| {t:,}m | {n:,} | {n / total * 100:.1f}% |")

    lines += ["", "| 공급 정의 | 취약 경로당 | 비율 |", "|---|---:|---:|"]
    for label, n in sens["공급정의별"].items():
        lines.append(f"| {label} | {n:,} | {n / total * 100:.1f}% |")

    lines += [
        "",
        "| 집계 단위 | 대상 | 취약 | 비율 |",
        "|---|---:|---:|---:|",
        f"| 시설 단위 | {total:,} | {len(vulnerable):,} | {len(vulnerable) / total * 100:.1f}% |",
        f"| 지점 단위(동일좌표 통합) | {sens['지점수']:,} | {sens['지점취약']:,} | "
        f"{sens['지점취약'] / sens['지점수'] * 100:.1f}% |",
        "",
        f"동일 좌표에 등록된 경로당이 {total - sens['지점수']}건 있다(남녀 분리 경로당, 아파트 단지 등). "
        "방문 대상 지점을 세는 목적이라면 지점 단위가 맞다.",
        "",
        "## 참고 — 고령인구 기준",
        "",
        f"| 기준 | 인구 | 비율 |",
        "|---|---:|---:|",
        f"| 60세 이상 | {elderly['60']:,} | {elderly['60'] / elderly['total'] * 100:.1f}% |",
        f"| 70세 이상 | {elderly['70']:,} | {elderly['70'] / elderly['total'] * 100:.1f}% |",
        "",
        "원본 인구 데이터가 10세 단위라 65세 이상은 산출할 수 없다.",
        "",
        "**행정동 고령인구를 취약률에 곱하는 우선순위 점수는 쓰지 않는다.** "
        "경로당이 고령인구를 균등 대리한다는 가정이 필요한데 그 가정이 깨지는 곳이 있어, "
        "고령 비율 9.5%인 신도시가 1위로 올라오는 왜곡이 생긴다. "
        "우선순위는 경로당 단위 거리로 닫고, 고령인구는 별도 축으로 병기한다.",
        "",
        "## 한계 (보고서에 반드시 명시)",
        "",
        "1. **공공 보건기관의 기준연도가 2025로 민간 의료기관과 다르다**(민간은 2025-08 ~ 2026-02). "
        "보건기관은 이전이 드물어 영향이 작다고 보나 시점 차이는 명시한다. "
        "운영시간·진료과목 정보가 없어 '접근 가능한 거리'이지 '이용 가능한 시간'은 아니다.",
        "2. **직선거리다.** 도로 우회를 반영하지 않아 농촌은 과소추정된다.",
        "3. **창원시 경계 밖 의료기관을 세지 않는다.** 김해 진영·고성 회화·함안 칠원이 더 가까운 "
        "경로당이 있을 수 있어 일부는 과대추정이다.",
        "4. **의료기관 기준시점이 구별로 다르다** (진해 2025-08-05 ~ 마산 2026-02-05). "
        "특히 진해구는 보고서 작성 시점과 13개월 차이가 난다.",
        "5. **경로당 이용자 수를 모른다.** 취약 경로당 수를 취약 고령자 수로 환산하면 안 된다.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    global MEDICAL
    halls = [r for r in read_rows("경로당_좌표.csv") if r["위도"]]
    MEDICAL = [r for r in read_rows("의료기관_좌표.csv") if r["위도"]]
    population = read_rows("행정동별_인구.csv")

    records = build_records(halls, MEDICAL)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(records[0].keys()))
        writer.writeheader()
        for row in records:
            writer.writerow(
                {k: (round(v) if k.endswith("_m") else v) for k, v in row.items()}
            )

    gaps = resolution_gap(records)
    sens = sensitivity(records, halls)
    elderly = {
        "total": sum(int(r["총인구"]) for r in population),
        "60": sum(int(r["60세이상"]) for r in population),
        "70": sum(int(r["70세이상"]) for r in population),
    }
    write_report(records, gaps, sens, elderly)

    vulnerable = sum(1 for r in records if r["최근접_1차의료_m"] > PRIMARY_THRESHOLD)
    print(f"경로당 {len(records):,} · 취약 {vulnerable:,} ({vulnerable / len(records) * 100:.1f}%)")
    print(f"행정동 평균이 감추는 취약 경로당: {sum(g['숨은취약_수'] for g in gaps)}개 / {len(gaps)}개 행정동")
    print(f"산출 {OUT_CSV.relative_to(ROOT)} · {OUT_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
