"""보고서용 지도 3장을 만든다.

배경 타일을 쓰지 않는다. 지점 873개와 공급 884개만으로 창원시 윤곽이 읽히는 것을 실제로
그려 확인했고, 타일 재배포 라이선스 문제도 함께 없어진다. 행정동 경계는 보조 요소다 —
심사위원이 어느 구인지 바로 알 수 있게 하는 용도이고, 경계에 의존하는 결론은 만들지 않는다.

경계 출처: vuski/admdongkor (CC BY 4.0). 원자료는 통계청 SGIS 행정동 경계,
공공누리 제1유형(출처표시). 국토교통부 (센서스경계)행정동경계는 실제 배포처인 VWorld 에서
CC BY-NC-ND(비영리·변경금지)로 걸려 있어 쓰지 않았다 — 지도 가공이 '변경금지'와 충돌한다.

출력: 04_보고서/그림/그림1_전체_접근성.png
      04_보고서/그림/그림2_동지역_취약.png
      04_보고서/그림/그림3_저접근_군집.png
      02_데이터/clean/창원_행정동_경계.geojson  (창원 55개만 추출해 커밋 — 33MB 재다운로드 방지)
재현: python3 03_분석/09_시각화_지도.py   (경계 캐시가 있으면 네트워크 불필요)
"""
import argparse
import csv
import importlib.util
import json
import math
import ssl
import urllib.request
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "02_데이터" / "clean"
FIG = ROOT / "04_보고서" / "그림"
BOUNDARY = CLEAN / "창원_행정동_경계.geojson"

SOURCE = (
    "https://raw.githubusercontent.com/vuski/admdongkor/master/"
    "ver20260701/HangJeongDong_ver20260701.geojson"
)
CENTER_LAT = 35.2

# 인쇄에서도 구분되도록 명도 차를 함께 준다. 색만으로 구분하면 흑백 출력에서 무너진다.
DISTANCE_BINS = [
    (500, "#c9c9c9", 8, "500m 이하"),
    (1000, "#fdae61", 9, "500~1,000m"),
    (2000, "#e34a33", 16, "1,000~2,000m"),
    (math.inf, "#8c0e0e", 26, "2,000m 초과"),
]


def setup_font() -> None:
    # AppleGothic 은 bold 를 갖고 있지 않아 강조가 먹지 않는다. 굵기가 있는 것부터 찾는다.
    for name in ("Apple SD Gothic Neo", "Pretendard", "NanumGothic", "AppleGothic"):
        try:
            matplotlib.font_manager.findfont(name, fallback_to_default=False)
        except Exception:
            continue
        plt.rcParams["font.family"] = name
        break
    plt.rcParams["axes.unicode_minus"] = False


def load_clusters():
    spec = importlib.util.spec_from_file_location("cl", ROOT / "03_분석" / "08_분석_군집.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch_boundary(refresh: bool) -> list[dict]:
    """전국 경계에서 창원 55개만 남겨 clean 에 둔다. 33MB를 매번 받을 이유가 없다."""
    if BOUNDARY.exists() and not refresh:
        return json.loads(BOUNDARY.read_text(encoding="utf-8"))["features"]

    request = urllib.request.Request(SOURCE, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=300) as resp:
        national = json.loads(resp.read().decode("utf-8"))
    features = [f for f in national["features"] if "창원시" in (f["properties"].get("sggnm") or "")]
    BOUNDARY.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )
    return features


def polygons(feature: dict) -> list[list]:
    geometry = feature["geometry"]
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"][0]]
    return [part[0] for part in geometry["coordinates"]]


def dong_name(feature: dict) -> str:
    return (feature["properties"]["adm_nm"] or "").split()[-1]


def draw_boundary(ax, features, highlight: set[str] | None = None) -> None:
    for feature in features:
        name = dong_name(feature)
        strong = highlight is not None and name in highlight
        for ring in polygons(feature):
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            ax.plot(
                xs, ys,
                color="#3d3d3d" if strong else "#c8c8c8",
                linewidth=1.1 if strong else 0.4,
                zorder=3 if strong else 1,
            )


def label_outward(ax, x, y, text, center, color="#111111", fontsize=10) -> None:
    """라벨을 지도 중심의 반대쪽으로 뺀다. 가까이 붙은 지점끼리 글자가 겹치지 않는다."""
    left = x < center[0]
    below = y < center[1]
    offset = (-14 if left else 14, -12 if below else 12)
    ax.annotate(
        text, (x, y), textcoords='offset points', xytext=offset,
        ha='right' if left else 'left', va='top' if below else 'bottom',
        fontsize=fontsize, fontweight='bold', color=color,
        arrowprops=dict(arrowstyle='-', color=color, linewidth=0.8), zorder=8,
    )


def extent_center(features) -> tuple[float, float]:
    xs, ys = [], []
    for feature in features:
        for ring in polygons(feature):
            xs += [p[0] for p in ring]
            ys += [p[1] for p in ring]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def finish(ax, title: str, subtitle: str) -> None:
    ax.set_aspect(1 / math.cos(math.radians(CENTER_LAT)))  # 안 하면 동서로 눌린다
    ax.axis("off")
    ax.set_title(title, fontsize=15, pad=14, fontweight="bold")
    ax.text(0.5, -0.02, subtitle, transform=ax.transAxes, ha="center", va="top", fontsize=9,
            color="#555")


def read_sites():
    rows = list((CLEAN / "경로당_군집.csv").open(encoding="utf-8"))
    return list(csv.DictReader(rows))


def supply_points():
    """지도에 그리는 공급은 분석과 같은 집합이어야 한다 — 의원·한의원 + 진료 보건기관."""
    points = []
    for row in csv.DictReader((CLEAN / "의료기관_좌표.csv").open(encoding="utf-8")):
        if row["위도"] and row["종별"] in ("의원", "한의원"):
            points.append((float(row["경도"]), float(row["위도"])))
    for row in csv.DictReader((CLEAN / "보건기관_좌표.csv").open(encoding="utf-8")):
        if row["위도"] and row["진료기관"] == "Y":
            points.append((float(row["경도"]), float(row["위도"])))
    return points


def bin_of(distance: float):
    for limit, color, size, label in DISTANCE_BINS:
        if distance <= limit:
            return color, size, label
    raise AssertionError


def figure_overall(features, sites, supply) -> Path:
    fig, ax = plt.subplots(figsize=(11, 10))
    draw_boundary(ax, features)
    ax.scatter([p[0] for p in supply], [p[1] for p in supply],
               s=4, c="#4a90d9", alpha=0.35, linewidths=0, zorder=2, label="1차의료 공급")

    grouped: dict[str, list] = {}
    for site in sites:
        color, size, label = bin_of(float(site["최근접_1차의료_m"]))
        grouped.setdefault(label, []).append((float(site["경도"]), float(site["위도"]), color, size))

    for _, _, label in [(b[0], b[1], b[3]) for b in DISTANCE_BINS]:
        items = grouped.get(label, [])
        if not items:
            continue
        ax.scatter([i[0] for i in items], [i[1] for i in items],
                   s=[i[3] for i in items], c=[i[2] for i in items],
                   edgecolors="white", linewidths=0.3, zorder=4)

    distance_handles = [
        Line2D([], [], marker="o", linestyle="", markersize=max(4.0, math.sqrt(size) * 1.6),
               markerfacecolor=color, markeredgecolor="white", label=label)
        for _, color, size, label in DISTANCE_BINS
    ]
    supply_handle = [Line2D([], [], marker="o", linestyle="", markersize=5,
                            markerfacecolor="#4a90d9", markeredgecolor="none",
                            label="1차의료 공급 884개소")]
    first = ax.legend(handles=distance_handles, loc="upper left", frameon=False, fontsize=9,
                      title="경로당 지점의 최근접 1차의료 거리", title_fontsize=10)
    ax.add_artist(first)
    ax.legend(handles=supply_handle, loc="upper left", bbox_to_anchor=(0.0, 0.80),
              frameon=False, fontsize=9)
    finish(ax, "창원시 경로당 873개 지점의 1차의료 접근성",
           "공급 = 의원·한의원 866 + 진료 보건기관 18 · 직선거리 · 동일좌표 시설은 지점으로 통합")
    return save(fig, "그림1_전체_접근성.png")


def figure_urban(features, sites) -> Path:
    urban = [s for s in sites if s["지역구분"] == "동"]
    rural_names = {dong_name(f) for f in features if dong_name(f).endswith(("읍", "면"))}
    highlight = {s["행정동"] for s in urban if float(s["최근접_1차의료_m"]) > 2000}

    fig, ax = plt.subplots(figsize=(11, 10))
    for feature in features:
        for ring in polygons(feature):
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            rural = dong_name(feature) in rural_names
            ax.fill(xs, ys, color="#f2f2f2" if rural else "#ffffff", zorder=0)
            ax.plot(xs, ys, color="#cccccc", linewidth=0.4, zorder=1)
    draw_boundary(ax, [f for f in features if dong_name(f) in highlight], highlight)

    near = [s for s in urban if float(s["최근접_1차의료_m"]) <= 1000]
    mid = [s for s in urban if 1000 < float(s["최근접_1차의료_m"]) <= 2000]
    far = [s for s in urban if float(s["최근접_1차의료_m"]) > 2000]
    for group, color, size, label in (
        (near, "#d9d9d9", 8, f"1,000m 이하 {len(near)}"),
        (mid, "#fdae61", 22, f"1,000~2,000m {len(mid)}"),
        (far, "#8c0e0e", 55, f"2,000m 초과 {len(far)}"),
    ):
        ax.scatter([float(s["경도"]) for s in group], [float(s["위도"]) for s in group],
                   s=size, c=color, edgecolors="white", linewidths=0.4, zorder=5, label=label)

    center = extent_center(features)
    for name in sorted(highlight):
        members = [s for s in far if s["행정동"] == name]
        x = sum(float(s["경도"]) for s in members) / len(members)
        y = sum(float(s["위도"]) for s in members) / len(members)
        label_outward(ax, x, y, f"{name} ({len(members)})", center, color="#8c0e0e")

    ax.legend(loc="lower left", frameon=False, fontsize=9,
              title="동 지역 지점의 최근접 1차의료 거리", title_fontsize=10)
    finish(ax, "행정구역은 '동'인데 접근성은 읍면 수준인 지점",
           "회색 면 = 읍면 지역 · 흰 면 = 동 지역 · 동 지점만 표시")
    return save(fig, "그림2_동지역_취약.png")


def figure_clusters(features, sites, clusters) -> Path:
    fig, ax = plt.subplots(figsize=(11, 10))
    draw_boundary(ax, features)

    groups = {"": ("#dddddd", 6), "고접근군집": ("#4a90d9", 10), "저접근군집": ("#c0392b", 34)}
    labels = {"": "유의하지 않음", "고접근군집": "고접근 군집 (cold spot)",
              "저접근군집": "저접근 군집 (hot spot)"}
    for kind in ("", "고접근군집", "저접근군집"):
        members = [s for s in sites if s["유의"] == kind]
        color, size = groups[kind]
        ax.scatter([float(s["경도"]) for s in members], [float(s["위도"]) for s in members],
                   s=size, c=color, edgecolors="white", linewidths=0.3,
                   zorder=4 if kind else 2, label=f"{labels[kind]} {len(members)}")

    urban_clusters = [c for c in clusters if c["동지점"] > 0]
    ax.scatter([c["정차후보_경도"] for c in clusters], [c["정차후보_위도"] for c in clusters],
               s=70, marker="*", c="#111111", zorder=6,
               label=f"순회 정차 후보지 {len(clusters)}")
    center = extent_center(features)
    for cluster in urban_clusters:
        label_outward(ax, cluster["정차후보_경도"], cluster["정차후보_위도"],
                      cluster["정차후보"], center, fontsize=9)

    ax.legend(loc="lower left", frameon=False, fontsize=9,
              title="Getis-Ord Gi* · 밴드 1km · BH-FDR (q≤0.05)", title_fontsize=10)
    finish(ax, f"저접근 군집 {len(clusters)}개와 순회 정차 후보지",
           f"이름표를 단 것은 동 지점을 포함한 군집 {len(urban_clusters)}개 · 별표 = 군집 내 총거리 최소 지점")
    return save(fig, "그림3_저접근_군집.png")


def save(fig, name: str) -> Path:
    FIG.mkdir(parents=True, exist_ok=True)
    path = FIG / name
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="보고서용 지도 3장을 만든다")
    parser.add_argument("--refresh-boundary", action="store_true", help="경계를 다시 받는다")
    args = parser.parse_args()

    setup_font()
    features = fetch_boundary(args.refresh_boundary)
    print(f"행정동 경계 {len(features)}개")

    cl = load_clusters()
    acc = cl.load_accessibility()
    halls = [r for r in acc.read_rows("경로당_좌표.csv") if r["위도"]]
    medical = [r for r in acc.read_rows("의료기관_좌표.csv") if r["위도"]]
    site_records = cl.collapse_to_sites(acc.build_records(halls, medical))
    matrix = cl.distance_matrix(site_records, acc.haversine)
    clusters = cl.cluster_rows(site_records, cl.analyze(site_records, matrix, 1000.0), matrix)

    sites = read_sites()
    supply = supply_points()
    print(f"지점 {len(sites)} · 공급 {len(supply)} · 군집 {len(clusters)}")

    for path in (
        figure_overall(features, sites, supply),
        figure_urban(features, sites),
        figure_clusters(features, sites, clusters),
    ):
        print(f"산출 {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
