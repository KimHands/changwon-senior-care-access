"""경로당 지점의 저(低)접근이 통계적으로 뭉쳐 있는지 검정한다 — Getis-Ord Gi*.

왜 이걸 하는가. 공고는 "수집·정제 후 **분석 모형 적용**"을 요구하는데 06번까지는 최근접거리
기술통계와 민감도뿐이다. 그리고 v2가 무너진 이유는 분석자가 고른 절단선(행정동 평균)이
결론을 만들었기 때문이다. Gi*는 **새 가정을 하나도 요구하지 않는다** — 좌표와 이미 계산된
거리만 쓰고, 뭉침이 우연인지 아닌지를 유의수준으로 답한다.

무엇이 좋아지는가. 결과를 '지점 16개'가 아니라 '군집 N개'로 낼 수 있다. 순회·방문건강관리는
지점이 아니라 권역 단위로 움직이므로 행정 단위와 맞는다. 군집마다 medoid(군집 내 총거리가
최소인 실제 경로당)를 뽑아 **순회 정차 후보지**로 제시한다.

미리 정해둔 것: 군집이 유의하지 않게 나오면 그 결과를 그대로 싣는다. 나올 때까지 밴드를
바꾸지 않는다. 사후에 조건을 고르면 v2의 실패를 그대로 반복한다.

출력: 02_데이터/clean/경로당_군집.csv
      03_분석/군집_분석노트.md
재현: python3 03_분석/08_분석_군집.py
"""
import argparse
import csv
import importlib.util
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "02_데이터" / "clean"
OUT_CSV = CLEAN / "경로당_군집.csv"
OUT_MD = ROOT / "03_분석" / "군집_분석노트.md"

DEFAULT_BAND = 1000.0
BANDS = (1000.0, 1500.0, 2000.0)
ALPHA = 0.05


def load_accessibility():
    """06번의 공급 집합·거리 정의를 그대로 재사용한다. 정의가 갈라지면 두 결과가 어긋난다."""
    spec = importlib.util.spec_from_file_location("acc", ROOT / "03_분석" / "06_분석_접근성.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collapse_to_sites(records: list[dict]) -> list[dict]:
    """동일 좌표에 등록된 경로당을 지점 하나로 묶는다(06번과 같은 키).

    남녀 분리 경로당, 같은 아파트 단지 내 복수 경로당이 여기 해당한다. 방문 대상 '지점'을
    세는 것이 목적이므로 지점 단위가 맞다.
    """
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        grouped[(record["위도"], record["경도"])].append(record)

    sites = []
    for (lat, lon), members in grouped.items():
        head = members[0]
        sites.append(
            {
                "대표경로당": head["경로당명"],
                "통합시설수": len(members),
                "구": head["구"],
                "행정동": head["행정동"],
                "지역구분": head["지역구분"],
                "위도": float(lat),
                "경도": float(lon),
                "거리_m": head["최근접_1차의료_m"],
            }
        )
    return sites


def distance_matrix(sites: list[dict], haversine) -> list[list[float]]:
    points = [(s["위도"], s["경도"]) for s in sites]
    size = len(points)
    matrix = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1, size):
            d = haversine(points[i], points[j])
            matrix[i][j] = d
            matrix[j][i] = d
    return matrix


def getis_ord(values: list[float], matrix: list[list[float]], band: float) -> list[float]:
    """Gi* z-score. 이웃 가중은 거리 밴드 내 1, 밖 0이고 자기 자신을 포함한다(Gi* 의 정의)."""
    n = len(values)
    mean = sum(values) / n
    variance = sum(v * v for v in values) / n - mean * mean
    s = math.sqrt(max(variance, 0.0))

    scores = []
    for i in range(n):
        neighbors = [j for j in range(n) if i == j or matrix[i][j] <= band]
        w1 = float(len(neighbors))  # 이진 가중이므로 Σw = Σw² = 이웃 수
        weighted = sum(values[j] for j in neighbors)
        denom_inner = (n * w1 - w1 * w1) / (n - 1)
        if s == 0 or denom_inner <= 0:
            scores.append(0.0)  # 전 지점이 이웃이면 Gi* 는 정의되지 않는다
            continue
        scores.append((weighted - mean * w1) / (s * math.sqrt(denom_inner)))
    return scores


def two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def benjamini_hochberg(pvalues: list[float]) -> list[float]:
    """873회 동시검정이므로 보정 없이는 40여 건이 우연히 유의해진다. q값을 함께 낸다."""
    n = len(pvalues)
    order = sorted(range(n), key=lambda i: pvalues[i])
    qvalues = [1.0] * n
    running = 1.0
    for rank in range(n, 0, -1):
        index = order[rank - 1]
        running = min(running, pvalues[index] * n / rank)
        qvalues[index] = running
    return qvalues


def build_clusters(indices: list[int], matrix: list[list[float]], band: float) -> list[list[int]]:
    """유의한 저접근 지점들을 밴드 거리로 연결한 연결요소가 곧 군집이다."""
    members = set(indices)
    seen: set[int] = set()
    clusters = []
    for start in indices:
        if start in seen:
            continue
        stack, group = [start], []
        seen.add(start)
        while stack:
            current = stack.pop()
            group.append(current)
            for other in members:
                if other not in seen and matrix[current][other] <= band:
                    seen.add(other)
                    stack.append(other)
        clusters.append(sorted(group))
    return sorted(clusters, key=len, reverse=True)


def medoid(group: list[int], matrix: list[list[float]]) -> int:
    """군집 내 다른 지점까지 총거리가 최소인 **실제 경로당**. 순회 정차 후보지로 쓴다.

    평균 좌표를 쓰면 논밭 한가운데가 나올 수 있다. 정차는 실재하는 장소여야 한다.
    """
    return min(group, key=lambda i: sum(matrix[i][j] for j in group))


def analyze(sites: list[dict], matrix: list[list[float]], band: float) -> dict:
    values = [s["거리_m"] for s in sites]
    zscores = getis_ord(values, matrix, band)
    pvalues = [two_sided_p(z) for z in zscores]
    qvalues = benjamini_hochberg(pvalues)

    hot = [i for i in range(len(sites)) if zscores[i] > 0 and qvalues[i] <= ALPHA]
    cold = [i for i in range(len(sites)) if zscores[i] < 0 and qvalues[i] <= ALPHA]
    clusters = build_clusters(hot, matrix, band)
    return {
        "band": band,
        "z": zscores,
        "p": pvalues,
        "q": qvalues,
        "hot": hot,
        "cold": cold,
        "clusters": clusters,
    }


def cluster_rows(sites, result, matrix) -> list[dict]:
    rows = []
    for number, group in enumerate(result["clusters"], start=1):
        center = medoid(group, matrix)
        dongs = Counter(sites[i]["행정동"] for i in group)
        urban = sum(1 for i in group if sites[i]["지역구분"] == "동")
        rows.append(
            {
                "군집": number,
                "지점수": len(group),
                "동지점": urban,
                "읍면지점": len(group) - urban,
                "행정동": ", ".join(f"{d}({c})" for d, c in dongs.most_common()),
                "정차후보": sites[center]["대표경로당"],
                "정차후보_위도": round(sites[center]["위도"], 6),
                "정차후보_경도": round(sites[center]["경도"], 6),
                "군집_평균거리_m": round(sum(sites[i]["거리_m"] for i in group) / len(group)),
                "군집_최원거리_m": round(max(sites[i]["거리_m"] for i in group)),
                "_indices": group,
            }
        )
    return rows


def write_csv(sites, result, clusters) -> None:
    belong = {}
    for row in clusters:
        for i in row["_indices"]:
            belong[i] = row["군집"]

    fields = [
        "대표경로당", "통합시설수", "구", "행정동", "지역구분", "위도", "경도",
        "최근접_1차의료_m", "Gi_z", "p", "q_FDR", "유의", "군집",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for i, site in enumerate(sites):
            significant = result["q"][i] <= ALPHA
            kind = ""
            if significant:
                kind = "저접근군집" if result["z"][i] > 0 else "고접근군집"
            writer.writerow(
                {
                    "대표경로당": site["대표경로당"],
                    "통합시설수": site["통합시설수"],
                    "구": site["구"],
                    "행정동": site["행정동"],
                    "지역구분": site["지역구분"],
                    "위도": round(site["위도"], 7),
                    "경도": round(site["경도"], 7),
                    "최근접_1차의료_m": round(site["거리_m"]),
                    "Gi_z": round(result["z"][i], 3),
                    "p": f"{result['p'][i]:.3e}",
                    "q_FDR": f"{result['q'][i]:.3e}",
                    "유의": kind,
                    "군집": belong.get(i, ""),
                }
            )


def cross_check(sites, result) -> dict:
    """v3가 임계(2km 강건)로 뽑은 동 지역 지점과 Gi* 결과를 대조한다.

    두 방법은 서로 다른 것을 쓴다 — 임계는 지점 자신의 거리만, Gi*는 이웃까지 본다.
    독립적으로 같은 곳을 가리키면 그것이 결론의 근거가 되고, 어긋나는 것은 그 이유를 밝힌다.
    """
    robust = {s["대표경로당"] for s in sites if s["지역구분"] == "동" and s["거리_m"] > 2000}
    hot = {sites[i]["대표경로당"] for i in result["hot"] if sites[i]["지역구분"] == "동"}
    return {"임계": robust, "군집": hot, "교집합": robust & hot,
            "군집만": sorted(hot - robust), "임계만": sorted(robust - hot)}


def write_note(sites, result, clusters, sensitivity, cross) -> None:
    band = int(result["band"])
    urban_sites = sum(1 for s in sites if s["지역구분"] == "동")
    hot_urban = sum(1 for i in result["hot"] if sites[i]["지역구분"] == "동")
    urban_clusters = [c for c in clusters if c["동지점"] > 0]

    lines = [
        "# 군집 분석노트 — 저접근이 우연인가 아닌가",
        "",
        f"재현 `python3 03_분석/08_분석_군집.py` · 인증키 불필요 · 거리 밴드 {band}m · FDR α={ALPHA}",
        "",
        "## 방법",
        "",
        "- **지표**: Getis-Ord Gi\\* (자기 자신을 포함하는 국지 공간연관 통계량)",
        f"- **대상**: 경로당 지점 {len(sites):,}개 (동일 좌표 시설 통합, 06번과 같은 키)",
        "- **속성**: 지점별 최근접 1차의료 직선거리 — 06번이 계산한 값을 **반올림 전**으로 그대로 쓴다",
        f"- **이웃 정의**: 거리 밴드 {band}m 이내 이진 가중",
        f"- **다중검정 보정**: Benjamini-Hochberg FDR. {len(sites):,}회 동시검정이라 보정 없이는 "
        f"약 {round(len(sites) * ALPHA)}건이 우연히 유의해진다",
        "",
        "새로 들여온 가정이 없다. 좌표와 이미 계산된 거리만 쓴다.",
        "",
        "## 결과",
        "",
        f"- **저접근 군집(hot spot) 지점 {len(result['hot']):,}개** — 주변까지 함께 먼 곳",
        f"- 고접근 군집(cold spot) 지점 {len(result['cold']):,}개 — 주변까지 함께 가까운 곳",
        f"- 연결요소로 묶은 **저접근 군집 {len(clusters)}개**",
        "",
        f"저접근 군집 지점 중 **행정구역상 '동'인 것이 {hot_urban}개**"
        f" (동 지점 전체 {urban_sites:,}개 대비 {hot_urban / urban_sites * 100:.1f}%).",
        "",
        "### 군집 목록 (정차 후보지 = 군집 내 총거리 최소 지점)",
        "",
        "| # | 지점 | 동 | 읍면 | 행정동 | 평균거리 | 최원거리 | 순회 정차 후보지 |",
        "|---:|---:|---:|---:|---|---:|---:|---|",
    ]
    for row in clusters:
        lines.append(
            f"| {row['군집']} | {row['지점수']} | {row['동지점']} | {row['읍면지점']} | "
            f"{row['행정동']} | {row['군집_평균거리_m']:,}m | {row['군집_최원거리_m']:,}m | "
            f"{row['정차후보']} |"
        )

    lines += [
        "",
        f"동 지점을 포함한 군집은 **{len(urban_clusters)}개**다. 읍면 대상 보건사업 지정에서",
        "빠지면서 실제 거리는 읍면 수준인 권역이고, 이 분석의 기여가 여기에 있다.",
        "",
        "## v3 임계 기준과의 대조 (수렴 타당도)",
        "",
        "두 방법은 서로 다른 것을 본다. 임계는 **지점 자신의 거리**만, Gi\\*는 **이웃까지 함께**"
        " 본다. 독립적으로 같은 곳을 가리키는지가 결론의 근거다.",
        "",
        f"- 임계 기준(동 지역·2km 강건): **{len(cross['임계'])}개 지점**",
        f"- Gi\\* 저접근 군집(동 지역): **{len(cross['군집'])}개 지점**",
        f"- **교집합 {len(cross['교집합'])}개** — 방법을 바꿔도 남는 곳이다",
        "",
        "어긋나는 것의 이유도 데이터로 설명된다.",
        "",
        f"- Gi\\*에만 잡힌 {len(cross['군집만'])}개: {', '.join(cross['군집만']) or '없음'}"
        " — 자신은 2km 이내지만 **주변이 함께 멀어** 유의해진 곳",
        f"- 임계에만 잡힌 {len(cross['임계만'])}개: {', '.join(cross['임계만']) or '없음'}"
        " — 멀지만 **주변에 동료 지점이 없어** 군집을 이루지 못한 고립 지점",
        "",
        "고립 지점은 군집에서 빠졌다고 해서 덜 취약한 것이 아니다. 순회 권역으로 묶이지 않을 뿐이라",
        "**개별 방문 대상**으로 따로 제시한다.",
        "",
        "## 밴드 민감도",
        "",
        "군집 결과가 밴드 선택에 좌우되는지 본다. 좌우된다면 결론으로 쓸 수 없다.",
        "",
        "| 밴드 | 저접근 지점 | 군집 | 동 지점 포함 군집 |",
        "|---:|---:|---:|---:|",
    ]
    for row in sensitivity:
        lines.append(
            f"| {int(row['band']):,}m | {row['hot']:,} | {row['clusters']} | {row['urban_clusters']} |"
        )

    lines += [
        "",
        "## 한계",
        "",
        "- **거리 밴드에 대한 확립된 근거는 없다.** 1차의료 접근 임계와 같은 1km를 기본으로 두고",
        "  3개 밴드를 병기한다. 결론은 밴드를 바꿔도 남는 것으로 낸다.",
        "- **이웃이 없는 고립 지점**은 Gi\\* 가 사실상 표준화 점수가 된다. 군집이 아니라 단독으로",
        "  잡히므로, 지점수 1인 군집은 '군집'이 아니라 고립 지점으로 읽어야 한다.",
        "- 속성이 **직선거리**이므로 06번의 한계(도로 우회 미반영, 시 경계 밖 의료기관 미포함)를",
        "  그대로 물려받는다.",
        "- 정차 후보지는 **거리만으로 뽑은 후보**다. 도로 접근성·주차·이용자 수는 데이터에 없다.",
        "  현장 확인 전 후보 목록이지 배치 결정이 아니다.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="경로당 저접근 군집을 검정한다 (Getis-Ord Gi*)")
    parser.add_argument("--band", type=float, default=DEFAULT_BAND, help="이웃 거리 밴드(m)")
    args = parser.parse_args()

    acc = load_accessibility()
    halls = [r for r in acc.read_rows("경로당_좌표.csv") if r["위도"]]
    medical = [r for r in acc.read_rows("의료기관_좌표.csv") if r["위도"]]
    records = acc.build_records(halls, medical)
    sites = collapse_to_sites(records)
    print(f"시설 {len(records):,} → 지점 {len(sites):,}")

    matrix = distance_matrix(sites, acc.haversine)
    result = analyze(sites, matrix, args.band)
    clusters = cluster_rows(sites, result, matrix)

    sensitivity = []
    for band in BANDS:
        other = result if band == args.band else analyze(sites, matrix, band)
        rows = clusters if band == args.band else cluster_rows(sites, other, matrix)
        sensitivity.append(
            {
                "band": band,
                "hot": len(other["hot"]),
                "clusters": len(other["clusters"]),
                "urban_clusters": sum(1 for r in rows if r["동지점"] > 0),
            }
        )

    write_csv(sites, result, clusters)
    write_note(sites, result, clusters, sensitivity, cross_check(sites, result))
    print(f"저접근 지점 {len(result['hot']):,} · 군집 {len(clusters)}")
    print(f"산출 {OUT_CSV.relative_to(ROOT)} · {OUT_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
