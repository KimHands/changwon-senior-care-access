"""보고서 본문의 수치를 정제 데이터에서 다시 계산해 기계 대조한다.

왜 필요한가. 계산 결과를 보지 않고 문서에 숫자를 적은 사고가 두 번 있었다
(v2 민감도 임계값 추정, 존재하지 않는 지오코딩 실패 사례 인용). 같은 실수가
11월 공개 검증에서 나오면 소명할 수 없다. 사람이 눈으로 대조하는 절차는
이미 두 번 실패했으므로 기계가 한다.

**06·08번을 import 하지 않는다.** 분석 스크립트를 재사용하면 그 스크립트의
버그를 그대로 물려받아 "자기가 만든 답과 자기를 대조"하게 된다. 이 파일은
정제 CSV만 입력으로 받아 Haversine·최근접·백분위·집계를 독립적으로 다시
구현한다. 06번과 답이 갈리면 둘 중 하나가 틀린 것이고, 그것이 잡고 싶은 것이다.

Gi* 군집 수치만은 예외로 08번 산출 CSV(`경로당_군집.csv`)를 집계한다. 검정
통계량까지 재구현하면 그건 검증이 아니라 두 번째 구현이다. 대신 원자료가
아니라 산출물을 다시 세는 것임을 결과표에 등급으로 표시한다.

등급
  A  독립 재계산 — 정제 CSV에서 직접 계산
  B  산출물 집계 — 08번 산출 CSV를 다시 셈
  C  문서 대조 — 재계산 불가, 근거 노트의 값과 대조

출력: 03_분석/숫자대조표.md
재현: python3 03_분석/10_검증_숫자대조.py
      python3 03_분석/10_검증_숫자대조.py --report 04_보고서/분석보고서_초고_v4.md
종료코드: 불일치가 하나라도 있으면 1
"""
import argparse
import csv
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "02_데이터" / "clean"
OUT_MD = ROOT / "03_분석" / "숫자대조표.md"
DEFAULT_REPORT = ROOT / "04_보고서" / "분석보고서_초고_v4.md"

CLINIC = {"의원", "한의원"}
CLINIC_NARROW = {"의원"}
HOSPITAL_AMBULATORY = {"병원", "종합병원", "한방병원"}
HOSPITAL_ALL = HOSPITAL_AMBULATORY | {
    "요양병원(일반요양병원)",
    "요양병원(노인병원)",
    "정신병원",
    "치과병원",
}
THRESHOLD = 1000
THRESHOLDS = (500, 1000, 1500, 2000)
ROBUST_THRESHOLD = 2000


# ── 기하 ──────────────────────────────────────────────────────────────

def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    inner = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371000 * 2 * math.asin(math.sqrt(inner))


def nearest(origin, targets):
    return min(haversine(origin, t) for t in targets)


def percentile(values, q):
    """최근접 순위 방식. 보간 방식과 값이 다르므로 정의를 고정해 둔다."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def pct(part, whole, digits=1):
    """백분율은 표기 자릿수까지가 값이다. 29.0% 를 29% 로 줄이면 본문과 대조가 어긋난다."""
    return f"{part / whole * 100:.{digits}f}"


def is_rural(dong):
    return dong.endswith(("읍", "면"))


def read(name):
    return list(csv.DictReader((CLEAN / name).open(encoding="utf-8")))


def point(row):
    return float(row["위도"]), float(row["경도"])


# ── 재계산 ────────────────────────────────────────────────────────────

def compute_facts():
    facts = {}

    hall_rows = read("경로당_좌표.csv")
    med_rows = read("의료기관_좌표.csv")
    meal_rows = read("식사배달기관_좌표.csv")
    pub_rows = read("보건기관_좌표.csv")
    fail_rows = read("지오코딩_실패목록.csv")

    halls = [r for r in hall_rows if r["위도"]]
    medical = [r for r in med_rows if r["위도"]]
    public_all = [r for r in pub_rows if r["위도"]]
    public = [r for r in public_all if r["진료기관"] == "Y"]

    # 지오코딩
    grades = Counter(r["매칭등급"] for r in hall_rows + med_rows + meal_rows)
    total_addr = len(hall_rows) + len(med_rows) + len(meal_rows)
    facts["지오_대상"] = total_addr
    facts["지오_성공"] = grades["L1_원문"] + grades["L2_상세제거"]
    facts["지오_실패"] = grades["실패"]
    facts["지오_실패율"] = pct(grades["실패"], total_addr, 2)
    facts["지오_L1"] = grades["L1_원문"]
    facts["지오_L2"] = grades["L2_상세제거"]
    for label, src in (("경로당", hall_rows), ("의료기관", med_rows), ("식사배달", meal_rows)):
        bad = sum(1 for r in src if r["매칭등급"] == "실패")
        facts[f"지오_{label}_대상"] = len(src)
        facts[f"지오_{label}_실패"] = bad
        facts[f"지오_{label}_실패율"] = pct(bad, len(src), 2)
    facts["실패목록_건수"] = len(fail_rows)
    facts["실패_NOT_FOUND"] = sum(1 for r in fail_rows if r["실패사유"] == "NOT_FOUND")
    facts["실패_주소"] = [r["원주소"] for r in fail_rows] + [
        r["최종질의주소"] for r in fail_rows
    ]

    # 원본 건수 = 좌표 확보 + 실패 (raw/ 는 gitignore 대상이라 좌표 CSV로 역산한다)
    by_period = Counter(r["기준시점"] for r in medical)
    fail_by_file = Counter(r["원본파일"] for r in fail_rows if r["구분"] == "의료기관")
    period_of_file = {r["원본파일"]: r["기준시점"] for r in medical}
    for name, count in fail_by_file.items():
        by_period[period_of_file[name]] += count
    facts["의료_원본_의창성산"] = by_period["2025-11-19"]
    facts["의료_원본_마산"] = by_period["2026-02-05"]
    facts["의료_원본_진해"] = by_period["2025-08-05"]

    # 공급 집합
    kinds = Counter(r["종별"] for r in medical)
    facts["공급_의원"] = kinds["의원"]
    facts["공급_한의원"] = kinds["한의원"]
    facts["공급_민간의원급"] = kinds["의원"] + kinds["한의원"]
    types = Counter(r["기관유형"] for r in public)
    facts["공급_보건소"] = types["보건소"]
    facts["공급_보건지소"] = types["일반보건지소"]
    facts["공급_보건진료소"] = types["보건진료소"]
    facts["공급_공공"] = len(public)
    facts["공급_건강생활지원센터"] = sum(
        1 for r in public_all if r["기관유형"] == "건강생활지원센터"
    )
    facts["공급_보건기관_전체"] = len(public_all)
    facts["공급_1차의료"] = facts["공급_민간의원급"] + facts["공급_공공"]
    facts["공급_의원공공"] = kinds["의원"] + facts["공급_공공"]
    facts["공급_통원병원급"] = sum(kinds[k] for k in HOSPITAL_AMBULATORY)

    private = [point(r) for r in medical if r["종별"] in CLINIC]
    public_pts = [point(r) for r in public]
    primary = private + public_pts
    narrow = [point(r) for r in medical if r["종별"] in CLINIC_NARROW]
    narrow_public = narrow + public_pts
    ambulatory = [point(r) for r in medical if r["종별"] in HOSPITAL_AMBULATORY]
    hospital_all = [point(r) for r in medical if r["종별"] in HOSPITAL_ALL]

    # 지점별 거리 — 판정은 반올림 전 값으로 한다
    records = []
    for row in halls:
        origin = point(row)
        records.append(
            {
                "이름": row["기관명"],
                "구": row["구"],
                "행정동": row["읍면동"],
                "좌표": (row["위도"], row["경도"]),
                "1차의료": nearest(origin, primary),
                "민간의원급": nearest(origin, private),
                "공공보건": nearest(origin, public_pts),
                "의원만": nearest(origin, narrow),
                "의원공공": nearest(origin, narrow_public),
                "통원병원급": nearest(origin, ambulatory),
                "병원급전체": nearest(origin, hospital_all),
            }
        )
    facts["시설수"] = len(records)
    facts["경로당_원본"] = len(hall_rows)

    for key in ("1차의료", "민간의원급", "공공보건", "통원병원급", "병원급전체"):
        values = [r[key] for r in records]
        facts[f"중앙_{key}"] = round(percentile(values, 0.5))
        facts[f"90p_{key}"] = round(percentile(values, 0.9))
        facts[f"최대_{key}"] = round(max(values))

    for key in ("1차의료", "민간의원급", "의원만", "의원공공", "통원병원급"):
        facts[f"시설취약_{key}"] = sum(1 for r in records if r[key] > THRESHOLD)
        facts[f"시설취약율_{key}"] = pct(facts[f"시설취약_{key}"], len(records))
    for t in THRESHOLDS:
        facts[f"시설취약_{t}"] = sum(1 for r in records if r["1차의료"] > t)

    facts["0m"] = sum(1 for r in records if r["1차의료"] == 0)
    facts["50m이하"] = sum(1 for r in records if r["1차의료"] <= 50)

    # 반올림 버그 재현 — 먼저 반올림하면 1,000.31m 가 접힌다
    facts["통원병원급_반올림전"] = sum(1 for r in records if r["통원병원급"] > THRESHOLD)
    facts["통원병원급_반올림후"] = sum(
        1 for r in records if round(r["통원병원급"]) > THRESHOLD
    )
    edge = [
        r for r in records if THRESHOLD < r["통원병원급"] < THRESHOLD + 1
    ]
    facts["반올림_경계_경로당"] = edge[0]["이름"] if edge else "(없음)"
    facts["반올림_경계_거리"] = round(edge[0]["통원병원급"], 2) if edge else 0

    # 지점 단위 — 동일 좌표를 하나로 묶는다
    sites = {}
    for r in records:
        sites.setdefault(r["좌표"], r)
    site_list = list(sites.values())
    facts["지점수"] = len(site_list)
    facts["동일좌표_중복"] = len(records) - len(site_list)
    groups = defaultdict(list)
    for r in records:
        groups[r["좌표"]].append(r)
    multi = [g for g in groups.values() if len(g) > 1]
    facts["동일좌표_그룹"] = len(multi)
    facts["동일좌표_소속시설"] = sum(len(g) for g in multi)
    facts["동_지점"] = sum(1 for s in site_list if not is_rural(s["행정동"]))
    facts["지점_50m이하"] = sum(1 for s in site_list if s["1차의료"] <= 50)

    for t in THRESHOLDS:
        weak = [s for s in site_list if s["1차의료"] > t]
        facts[f"지점취약_{t}"] = len(weak)
        facts[f"지점취약율_{t}"] = pct(len(weak), len(site_list))
        facts[f"지점취약_동_{t}"] = sum(1 for s in weak if not is_rural(s["행정동"]))
        facts[f"지점취약_읍면_{t}"] = sum(1 for s in weak if is_rural(s["행정동"]))

    # 읍면 분포에서 유도한 기준선. 분석자가 고른 절단선이 아니라는 것이 주장의 핵심이라
    # 이 값이 흔들리면 결론이 흔들린다. 반드시 재계산해 대조한다.
    rural = [s for s in site_list if is_rural(s["행정동"])]
    urban = [s for s in site_list if not is_rural(s["행정동"])]
    facts["읍면_지점"], facts["동_지점수"] = len(rural), len(urban)
    for label, group in (("읍면", rural), ("동", urban)):
        values = [s["1차의료"] for s in group]
        facts[f"{label}_중앙값"] = round(percentile(values, 0.5))
        facts[f"{label}_75p"] = round(percentile(values, 0.75))
        facts[f"{label}_최대"] = round(max(values))

    rural_median = percentile([s["1차의료"] for s in rural], 0.5)
    rural_p75 = percentile([s["1차의료"] for s in rural], 0.75)
    over = sorted((s for s in urban if s["1차의료"] > rural_median), key=lambda s: -s["1차의료"])
    facts["읍면중앙값초과_동"] = len(over)
    facts["읍면중앙값초과_비율"] = pct(len(over), len(urban))
    facts["읍면중앙값초과_최소"] = round(min(s["1차의료"] for s in over))
    facts["읍면75p초과_동"] = sum(1 for s in urban if s["1차의료"] > rural_p75)
    rural_mean = sum(s["1차의료"] for s in rural) / len(rural)
    facts["읍면_평균"] = round(rural_mean)
    facts["읍면평균초과_동"] = sum(1 for s in urban if s["1차의료"] > rural_mean)
    facts["3단계_교집합"] = sum(
        1 for s in over if s["1차의료"] > rural_p75 and (s["행정동"], s["이름"]) in {
            (r["행정동"], r["대표경로당"])
            for r in read("경로당_군집.csv")
            if r["유의"] == "저접근군집"
        }
    )
    facts["읍면중앙값초과_행정동"] = Counter(f"{s['구']} {s['행정동']}" for s in over)
    facts["28_이름"] = [s["이름"] for s in over]
    for t in (2500, 3000):
        facts[f"동초과_{t}"] = sum(1 for s in urban if s["1차의료"] > t)

    # 28개 검증표의 최근접 기관을 이름까지 다시 찾는다
    named = [(point(r), r["기관명"], r["종별"]) for r in medical if r["종별"] in CLINIC]
    named += [(point(r), r["보건기관명"], r["기관유형"]) for r in public]
    # 경로당명은 유일하지 않다. 송정경로당이 대산면과 회성동에 하나씩 있어 이름으로만
    # 묶으면 대산면(군집)의 소속이 회성동(비군집)에 새어 들어간다. 행정동과 함께 묶는다.
    hot = {
        (r["행정동"], r["대표경로당"])
        for r in read("경로당_군집.csv")
        if r["유의"] == "저접근군집"
    }
    facts["28_검증행"] = []
    for s in over:
        origin = (float(s["좌표"][0]), float(s["좌표"][1]))
        _, name, kind = min((haversine(origin, pos), nm, kd) for pos, nm, kd in named)
        mark = "○" if (s["행정동"], s["이름"]) in hot else ""
        facts["28_검증행"].append(
            f"| {s['구']} | {s['행정동']} | {s['이름']} | {round(s['1차의료']):,}m | {name} | {kind} | {mark} |"
        )

    # 임계 강건 동 지역 지점 — 2km 를 넘으면 그 아래 임계는 모두 넘는다
    robust = [
        s
        for s in site_list
        if not is_rural(s["행정동"]) and s["1차의료"] > ROBUST_THRESHOLD
    ]
    facts["강건_동_지점"] = len(robust)
    facts["강건_동_행정동"] = Counter(s["행정동"] for s in robust)
    for dong, count in facts["강건_동_행정동"].items():
        facts[f"강건_{dong}_지점"] = count
        facts[f"강건_{dong}_최원"] = round(
            max(s["1차의료"] for s in robust if s["행정동"] == dong)
        )
    facts["강건_동_이름"] = {s["이름"] for s in robust}

    # 거리 상위 10 지점
    top = sorted(site_list, key=lambda s: -s["1차의료"])[:10]
    facts["상위10_행정동"] = Counter(s["행정동"] for s in top)
    facts["상위10_읍면비율"] = sum(1 for s in top if is_rural(s["행정동"]))
    facts["상위10_최원"] = round(top[0]["1차의료"])

    # 인구
    pop = read("행정동별_인구.csv")
    facts["행정동수"] = len(pop)
    facts["총인구"] = sum(int(r["총인구"]) for r in pop)
    facts["60세이상"] = sum(int(r["60세이상"]) for r in pop)
    facts["70세이상"] = sum(int(r["70세이상"]) for r in pop)
    facts["60대"] = facts["60세이상"] - facts["70세이상"]
    facts["60세이상_비율"] = pct(facts["60세이상"], facts["총인구"])
    facts["70세이상_비율"] = pct(facts["70세이상"], facts["총인구"])
    facts["경로당_행정동_배수"] = round(facts["경로당_원본"] / facts["행정동수"])
    bukmyeon = next(r for r in pop if r["행정동"] == "북면")
    facts["북면_70세비율"] = pct(int(bukmyeon["70세이상"]), int(bukmyeon["총인구"]))

    # 법정동 1:N — 의료기관을 행정동에 집계할 수 없는 이유
    mapping = {(r["구"], r["법정동"]): r for r in read("법정동_행정동_매핑.csv")}
    facts["법정동_1대N"] = sum(1 for r in mapping.values() if r["단일확정"] == "N")
    facts["의료_좌표보유"] = len(medical)
    ambiguous = 0
    for row in medical:
        match = re.search(r"\(([^)]*)\)", row["정제주소"])
        dong = match.group(1).split(",")[0].strip() if match else ""
        key = (row["구"], dong)
        if key in mapping and mapping[key]["단일확정"] == "N":
            ambiguous += 1
    facts["의료_행정동_모호"] = ambiguous
    facts["의료_행정동_모호율"] = pct(ambiguous, len(medical))

    # 다중검정 — 보정 없이 우연히 유의해지는 기대 건수
    facts["Gi_검정횟수"] = facts["지점수"]
    facts["Gi_우연유의"] = round(facts["지점수"] * 0.05)

    return facts, site_list


def compute_cluster_facts(site_list):
    """08번 산출물을 다시 센다(등급 B). 검정 통계량은 재구현하지 않는다."""
    facts = {}
    rows = read("경로당_군집.csv")
    facts["군집_지점수"] = len(rows)
    low = [r for r in rows if r["유의"] == "저접근군집"]
    facts["저접근_지점"] = len(low)
    facts["고접근_지점"] = sum(1 for r in rows if r["유의"] == "고접근군집")
    facts["저접근_동"] = sum(1 for r in low if r["지역구분"] == "동")
    facts["군집_동_지점전체"] = sum(1 for r in rows if r["지역구분"] == "동")
    facts["저접근_동_비율"] = pct(facts["저접근_동"], facts["군집_동_지점전체"])

    clusters = defaultdict(list)
    for r in low:
        if r["군집"]:
            clusters[r["군집"]].append(r)
    facts["군집수"] = len(clusters)
    facts["복수지점_군집"] = sum(1 for m in clusters.values() if len(m) > 1)
    facts["단독_군집"] = sum(1 for m in clusters.values() if len(m) == 1)
    urban = {
        key: members
        for key, members in clusters.items()
        if any(m["지역구분"] == "동" for m in members)
    }
    facts["동포함_군집"] = len(urban)
    for members in urban.values():
        dong = Counter(m["행정동"] for m in members).most_common(1)[0][0]
        distances = [float(m["최근접_1차의료_m"]) for m in members]
        facts[f"군집_{dong}_지점"] = len(members)
        facts[f"군집_{dong}_평균"] = round(sum(distances) / len(distances))
        facts[f"군집_{dong}_최원"] = round(max(distances))

    # 수렴 타당도 — 임계(등급 A 재계산) ∩ Gi*(등급 B 산출물)
    robust = {
        s["이름"]
        for s in site_list
        if not is_rural(s["행정동"]) and s["1차의료"] > ROBUST_THRESHOLD
    }
    gi_urban = {r["대표경로당"] for r in low if r["지역구분"] == "동"}
    facts["수렴_임계"] = len(robust)
    facts["수렴_Gi"] = len(gi_urban)
    facts["수렴_교집합"] = len(robust & gi_urban)
    facts["수렴_Gi만"] = sorted(gi_urban - robust)
    facts["수렴_임계만"] = sorted(robust - gi_urban)
    return facts


def band_rows_from_note():
    """Gi* 밴드 민감도는 재계산하지 않는다 — 검정 통계량을 다시 구현하면 검증이 아니라
    두 번째 구현이다. 대신 08번이 쓴 근거 노트의 표를 읽어 본문과 대조한다(등급 C).
    노트와 본문이 어긋나면 옮겨 적다가 틀린 것이다."""
    note = (ROOT / "03_분석" / "군집_분석노트.md").read_text(encoding="utf-8")
    rows = {}
    for band, low, clusters, urban in re.findall(
        r"\|\s*([\d,]+)m\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", note
    ):
        rows[band] = (int(low), int(clusters), int(urban))
    return rows


# ── 본문 대조 ──────────────────────────────────────────────────────────

def normalize(text):
    """강조 표시와 잉여 공백을 걷어낸다. 윤문으로 굵게 표시가 붙고 떨어져도 대조는 같아야 한다."""
    text = re.sub(r"<!-- HUMANIZE-SUMMARY.*", "", text, flags=re.S)
    text = text.replace("**", "").replace("*", "").replace("\\", "")
    return [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]


def fmt(value):
    """계산된 소수 자리를 그대로 쓴다. 임의로 깎으면 0.59% 가 0.6% 로 보여 헛불일치가 난다."""
    if isinstance(value, float):
        text = f"{value:,.4f}".rstrip("0").rstrip(".")
        return text if text else "0"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def find_line(lines, anchor):
    """앵커가 (소절, 줄) 이면 그 소절 안에서만 찾는다. `| 1,500m |` 처럼 임계 표와
    밴드 표에 같은 줄 이름이 있는 경우를 가른다."""
    if isinstance(anchor, tuple):
        section, anchor = anchor
        start = next((i for i, l in enumerate(lines) if section in l), None)
        if start is None:
            return []
        end = next(
            (
                i
                for i, l in enumerate(lines[start + 1 :], start + 1)
                if l.startswith("#") or re.match(r"\(\d\)", l)
            ),
            len(lines),
        )
        lines = lines[start:end]
    return [line for line in lines if anchor in line]


def contains_value(line, wanted):
    """숫자는 토큰 경계까지 봐야 한다. 부분문자열로 보면 6 이 5,691m 안에서 매칭돼
    틀린 숫자가 통과한다(실제로 거리 상위 10 지점 대조가 이 방식으로 헛통과했다)."""
    if not re.fullmatch(r"[\d,.]+", wanted):
        return wanted in line
    return re.search(rf"(?<![\d,.]){re.escape(wanted)}(?![\d,.])", line) is not None


def check(lines, label, anchor, expected, grade):
    """앵커가 있는 줄에 기대값이 전부 들어 있는지 본다."""
    hits = find_line(lines, anchor)
    if not hits:
        return {
            "label": label,
            "grade": grade,
            "status": "MISS",
            "detail": f"본문에서 앵커를 찾지 못함: `{anchor}`".replace("'", ""),
        }
    wanted = [fmt(v) for v in expected]
    for line in hits:
        if all(contains_value(line, w) for w in wanted):
            return {"label": label, "grade": grade, "status": "OK", "detail": ", ".join(wanted)}
    return {
        "label": label,
        "grade": grade,
        "status": "FAIL",
        "detail": f"기대 {', '.join(wanted)} / 본문 «{hits[0][:110]}»",
    }


def build_claims(f, c):
    """(설명, 앵커, 기대값들, 등급). 앵커는 표의 행 이름처럼 윤문이 건드리지 않는 것으로 잡는다."""
    return [
        # 고령 기준
        ("60세 이상 인구·비율", "| 60세 이상 |", [f["60세이상"], f["60세이상_비율"]], "A"),
        ("70세 이상 인구·비율", "| 70세 이상 |", [f["70세이상"], f["70세이상_비율"]], "A"),
        ("60대 인구(자격 없는 구간)", "60대", [f["60대"]], "A"),
        ("경로당 수 대 행정동 배수", "행정동 수의", [f["경로당_원본"], f["경로당_행정동_배수"]], "A"),
        # 데이터 목록
        ("경로당 원본 건수", "| 1 | 경상남도 창원시 경로당 현황", [f["경로당_원본"]], "A"),
        ("의창·성산 의료기관 건수", "| 2 | 창원시 의창구·성산구", [f["의료_원본_의창성산"]], "A"),
        ("마산 의료기관 건수", "| 3 | 창원시 마산합포구", [f["의료_원본_마산"]], "A"),
        ("진해 의료기관 건수", "| 4 | 창원시 진해구", [f["의료_원본_진해"]], "A"),
        ("공공 보건기관 건수", "| 5 | 전국 지역보건의료기관 현황", [f["공급_보건기관_전체"]], "A"),
        ("행정동 수", "| 6 | 행정동별(통반단위)", [f["행정동수"]], "A"),
        # 지오코딩
        ("지오코딩 대상", "| 대상 주소 |", [f["지오_대상"]], "A"),
        ("지오코딩 성공", "| 좌표 확보 |", [f["지오_성공"]], "A"),
        ("지오코딩 실패", "| 실패 |", [f["지오_실패"]], "A"),
        ("지오코딩 실패율", "실패율", [f["지오_실패율"]], "A"),
        ("매칭등급 L1", "| L1_원문 |", [f["지오_L1"]], "A"),
        ("매칭등급 L2", "| L2_상세제거 |", [f["지오_L2"]], "A"),
        ("경로당 지오코딩", "| 경로당 |", [f["지오_경로당_대상"], f["지오_경로당_실패"], f["지오_경로당_실패율"]], "A"),
        ("의료기관 지오코딩", "| 의료기관 |", [f["지오_의료기관_대상"], f["지오_의료기관_실패"], f["지오_의료기관_실패율"]], "A"),
        ("식사배달 지오코딩", "| 식사배달기관 |", [f["지오_식사배달_대상"], f["지오_식사배달_실패"], f["지오_식사배달_실패율"]], "A"),
        # 분석 단위
        ("분석 단위 통합", "동일좌표 그룹", [f["시설수"], f["동일좌표_중복"], f["동일좌표_그룹"], f["동일좌표_소속시설"]], "A"),
        ("지점 수", "주 분석 단위", [f["지점수"]], "A"),
        # 공급 정의
        ("민간 의원급 구성", "| 민간 의원급 |", [f["공급_의원"], f["공급_한의원"], f["공급_민간의원급"]], "A"),
        ("공공 보건기관 구성", "| 공공 보건기관 |", [f["공급_보건소"], f["공급_보건지소"], f["공급_보건진료소"], f["공급_공공"]], "A"),
        ("1차의료 공급 합계", "| 1차의료 (주 지표) |", [f["공급_1차의료"]], "A"),
        ("통원 병원급 공급", "| (참고) 통원 병원급 |", [f["공급_통원병원급"]], "A"),
        ("병원급 전체 포함 시 중앙값", "포함하면 병원급 중앙값", [f["중앙_통원병원급"], f["중앙_병원급전체"]], "A"),
        # 행정동 귀속 제약
        ("법정동 1:N·모호 비율", "법정동 하나가 여러 행정동", [f["법정동_1대N"], f["의료_좌표보유"], f["의료_행정동_모호"], f["의료_행정동_모호율"]], "A"),
        # 반올림 버그
        ("반올림 전후 판정 차이", "취약 판정에서 제외된다", [f["반올림_경계_거리"], f["통원병원급_반올림후"], f["통원병원급_반올림전"]], "A"),
        # Gi* 설정
        ("다중검정 기대 오탐", "우연히 유의해지므로", [f["Gi_검정횟수"], f["Gi_우연유의"]], "A"),
        ("2SFCA 거부 근거 수치", "신도시 지역이 우선순위", [f["북면_70세비율"]], "A"),
        # 거리 분포
        ("1차의료 거리 분포", "| 경로당 → 1차의료", [f["중앙_1차의료"], f["90p_1차의료"], f["최대_1차의료"]], "A"),
        ("민간 의원급 거리 분포", "| 경로당 → 민간 의원급 |", [f["중앙_민간의원급"], f["90p_민간의원급"], f["최대_민간의원급"]], "A"),
        ("공공 보건기관 거리 분포", "| 경로당 → 공공 보건기관 |", [f["중앙_공공보건"], f["90p_공공보건"], f["최대_공공보건"]], "A"),
        ("통원 병원급 거리 분포", "| 경로당 → 통원 병원급 |", [f["중앙_통원병원급"], f["90p_통원병원급"], f["최대_통원병원급"]], "A"),
        # 취약
        ("지점 단위 취약", "| 지점 |", [f["지점수"], f["지점취약_1000"], f["지점취약율_1000"]], "A"),
        ("시설 단위 취약", "| 시설 |", [f["시설수"], f["시설취약_1차의료"], f["시설취약율_1차의료"]], "A"),
        ("취약 지점의 동·읍면 구성", "지점 기준 취약", [f["지점취약_1000"], f["지점취약_읍면_1000"], f["지점취약_동_1000"]], "A"),
        (
            "거리 상위 10 지점 구성",
            "거리 상위 10개 지점",
            [f"{dong} {n}" for dong, n in f["상위10_행정동"].most_common()]
            + [f["상위10_최원"]],
            "A",
        ),
        # 임계 강건 16
        ("읍면 거리 분포", "| 읍면 | 361 |", [f["읍면_지점"], f["읍면_중앙값"], f["읍면_75p"], f["읍면_최대"]], "A"),
        ("동 거리 분포", "| 동 | 512 |", [f["동_지점수"], f["동_중앙값"], f["동_75p"], f["동_최대"]], "A"),
        ("읍면 중앙값 초과 동 지점", "읍면 중앙값을 넘는 동 지점이", [f["읍면중앙값초과_동"], f["읍면중앙값초과_비율"], f["읍면중앙값초과_최소"]], "A"),
        ("우선순위 1단계", "| 1 | 읍면 중앙값", [f["읍면중앙값초과_동"]], "A"),
        ("우선순위 3단계(교집합)", "| 3 | 2단계에 더해", [f["3단계_교집합"]], "A"),
        ("기준 통계량 민감도", "| 평균 |", [f["읍면_평균"], f["읍면평균초과_동"]], "A"),
        ("기준 통계량 — 75백분위", "| 75백분위 | 2,430m |", [f["읍면75p초과_동"]], "A"),
        ("임계를 올리면 줄어든다", "2.5km면", [f["동초과_2500"], f["동초과_3000"]], "A"),
        # 0m 구간
        ("최근접 0m·50m 이하", "최근접 거리가 0m인 지점", [f["0m"], f["50m이하"], f["지점_50m이하"]], "A"),
        # 민감도 — 임계
        ("임계 500m", "| 500m |", [f["지점취약_500"], f["지점취약율_500"], f["지점취약_동_500"], f["지점취약_읍면_500"]], "A"),
        ("임계 1,000m", "| 1,000m |", [f["지점취약_1000"], f["지점취약율_1000"], f["지점취약_동_1000"], f["지점취약_읍면_1000"]], "A"),
        ("임계 1,500m", "| 1,500m |", [f["지점취약_1500"], f["지점취약율_1500"], f["지점취약_동_1500"], f["지점취약_읍면_1500"]], "A"),
        ("임계 2,000m", "| 2,000m |", [f["지점취약_2000"], f["지점취약율_2000"], f["지점취약_동_2000"], f["지점취약_읍면_2000"]], "A"),
        # 민감도 — 공급 정의
        ("공급: 의원+한의원+공공", "| 의원 + 한의원 + 공공", [f["공급_1차의료"], f["시설취약_1차의료"], f["시설취약율_1차의료"]], "A"),
        ("공급: 의원+공공", "| 의원 + 공공 (한의원 제외) |", [f["공급_의원공공"], f["시설취약_의원공공"], f["시설취약율_의원공공"]], "A"),
        ("공급: 의원+한의원", "| 의원 + 한의원 (공공 제외) |", [f["공급_민간의원급"], f["시설취약_민간의원급"], f["시설취약율_민간의원급"]], "A"),
        ("공급: 의원만", "| 의원만 |", [f["공급_의원"], f["시설취약_의원만"], f["시설취약율_의원만"]], "A"),
        ("공급: 통원 병원급", "| 통원 병원급 |", [f["공급_통원병원급"], f["시설취약_통원병원급"], f["시설취약율_통원병원급"]], "A"),
        # 민감도 — 집계 단위
        ("집계 단위: 시설", "| 시설 단위 |", [f["시설수"], f["시설취약_1차의료"], f["시설취약율_1차의료"]], "A"),
        ("집계 단위: 지점", "| 지점 단위 (동일좌표 통합) |", [f["지점수"], f["지점취약_1000"], f["지점취약율_1000"]], "A"),
        # Gi* (등급 B)
        ("저접근 군집 지점", "| 저접근 군집(hot spot) 지점 |", [c["저접근_지점"]], "B"),
        ("고접근 군집 지점", "| 고접근 군집(cold spot) 지점 |", [c["고접근_지점"]], "B"),
        ("연결요소 구성", "| 연결요소 |", [c["군집수"], c["복수지점_군집"], c["단독_군집"]], "B"),
        ("저접근 군집 중 동 지점", "행정구역상 '동'인 것은", [c["저접근_지점"], c["저접근_동"], c["군집_동_지점전체"], c["저접근_동_비율"]], "B"),
        ("동 포함 군집 수", "동 지점을 포함한 군집은", [c["동포함_군집"]], "B"),
        ("군집: 웅남동", "| 성산구 웅남동 |", [c["군집_웅남동_지점"], c["군집_웅남동_평균"], c["군집_웅남동_최원"]], "B"),
        ("군집: 가포동", "| 마산합포구 가포동 |", [c["군집_가포동_지점"], c["군집_가포동_평균"], c["군집_가포동_최원"]], "B"),
        ("군집: 웅천동", "| 진해구 웅천동 |", [c["군집_웅천동_지점"], c["군집_웅천동_평균"], c["군집_웅천동_최원"]], "B"),
        # 수렴 타당도
        ("수렴: 거리 기준", "| 거리 2km 초과 동 지점 |", [c["수렴_임계"]], "A"),
        ("수렴: Gi* 기준", "저접근 군집 동 지역 지점 |", [c["수렴_Gi"]], "B"),
        ("수렴: 교집합", "| 교집합 |", [c["수렴_교집합"]], "B"),
        ("수렴: Gi*에만 잡힌 수", "Gi에서만 선정된", [len(c["수렴_Gi만"])], "B"),
        ("수렴: 거리 기준에만 잡힌 수", "거리 기준에서만 선정된", [len(c["수렴_임계만"])], "B"),
        ("군집 미포함 지점 수", "저접근 군집에 들지 않은 지점이", [f["읍면중앙값초과_동"] - c["저접근_동"]], "A"),
        ("이웃은 있으나 유의하지 않은 사례", "663m", [663, 881, 0.089, 0.061], "A"),
    ] + [
        (f"밴드 민감도 {band}m", ("군집 검정의 밴드 민감도", f"| {band}m |"), list(values), "C")
        for band, values in band_rows_from_note().items()
    ]


def row_set(path_or_rows, fmt_row):
    return [re.sub(r"\s+", " ", fmt_row(r)).strip() for r in path_or_rows]


def check_rows(lines, label, expected, grade):
    """표를 행 단위로 통째 대조한다.

    낱개 숫자만 보면 같은 줄에 있는 다른 값과 섞여 헛통과한다. 행 전체를 문자열로
    맞추면 열이 밀리거나 한 칸이 바뀐 것도 잡힌다. 새로 실은 전량 공개 표들이
    이 방식의 대상이다 — 옮겨 적다가 틀리는 것이 가장 흔한 사고다.
    """
    body = {re.sub(r"\s+", " ", l).strip() for l in lines}
    missing = [r for r in expected if r not in body]
    return {
        "label": f"{label} ({len(expected)}행)",
        "grade": grade,
        "status": "OK" if not missing else "FAIL",
        "detail": "전 행 일치"
        if not missing
        else f"{len(missing)}행 불일치 — 예: {missing[0][:90]}",
    }


def build_row_claims(facts):
    """본문의 28개 검증표를 원자료에서 다시 만들어 행 단위로 대조한다.

    표를 줄인 뒤 남은 전량 공개 표는 이것 하나다. 나머지(지오코딩 실패·공공 보건기관·
    군집 전체)는 본문에서 빼고 저장소에만 남겼다.
    """
    return [("28개 지점 검증표", row_set(facts["28_검증행"], lambda r: r), "A")]


def check_names(lines, cluster_facts, facts):
    """이름 목록 대조 — 존재하지 않는 사례를 인용한 사고가 있었다."""
    body = " ".join(lines)
    results = []
    for label, names in (
        ("고립 지점 이름", cluster_facts["수렴_임계만"]),
        ("Gi*에만 잡힌 지점 이름", cluster_facts["수렴_Gi만"]),
    ):
        missing = [n for n in names if n not in body]
        results.append(
            {
                "label": label,
                "grade": "B",
                "status": "OK" if not missing else "FAIL",
                "detail": "본문에 전부 인용됨" if not missing else f"본문에 없는 이름: {missing}",
            }
        )
    quoted = re.findall(r"`(경상남도 창원시[^`]+|마산[^`]+|마산회원구[^`]+)`", " ".join(lines))
    bad = [q for q in quoted if not any(q in a for a in facts["실패_주소"])]
    results.append(
        {
            "label": "지오코딩 실패 사례 인용",
            "grade": "A",
            "status": "OK" if not bad else "FAIL",
            "detail": "실패목록에 있는 주소만 인용됨"
            if not bad
            else f"실패목록에 없는 주소를 인용: {bad}",
        }
    )
    return results


# ── 미검증 숫자 스캔 ───────────────────────────────────────────────────

WHITELIST = {
    # 날짜·연도·식별자
    "2020", "2023", "2024", "2025", "2026", "2027", "06", "202606", "20260701",
    "3077748", "3072692", "4326", "3.11", "3.11.2", "4.0",
    # 법령 조문 번호
    "7", "24", "36",
    # 외부 문헌 — 2023 노인실태조사
    "26.5", "51.8", "85",
    # 분석 설정값 자체(개수가 아니라 설정) — 이 값들이 만든 결과는 전부 대조 대상이다
    "500", "1,000", "1,500", "2,000", "0.05", "6,371", "35.2", "35", "50", "100",
    # 연령 기준
    "60", "64", "65", "69", "70",
    # 목록·그림 번호, 매수, 기준시점 차이(13개월)
    "1", "2", "3", "5", "8", "10", "13", "15", "19", "20",
    # 지오코딩 부번 사례 — 지오코딩_리포트.md 근거
    "95", "294",
    # 본문이 설명을 위해 언급하는 임계·백분위 표기와 인용 주소의 번지
    "1.5", "2.5", "75", "59", "3.2",
    # Gi* 단위검정 기대값 — 밴드 0 에서 표준화 점수와의 오차
    "0.0",
}
SKIP_LINE = ("| # |", "```", "http", "sha256", "055-", "FILE_", ".web", ".csv", ".py", ".png")


def scan_unverified(lines, checked_values):
    """대조표에 오르지 않은 숫자를 모아 보고한다. 아무도 안 본 숫자가 남는 것이 사고의 형태였다."""
    known = {fmt(v) for v in checked_values} | WHITELIST
    orphans = Counter()
    in_code = False
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or line.startswith("#") or any(s in line for s in SKIP_LINE):
            continue
        line = re.sub(r"\d{4}-\d{2}-\d{2}", "", line)
        for token in re.findall(r"\d[\d,]*(?:\.\d+)?", line):
            token = token.rstrip(",.")
            if token not in known:
                orphans[token] += 1
    return orphans


# ── 보고 ──────────────────────────────────────────────────────────────

def show_path(path):
    """ROOT 밖의 파일을 대조할 때도 죽지 않게 한다(음성 테스트가 여기서 걸렸다)."""
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def write_report(report_path, results, orphans):
    bad = [r for r in results if r["status"] != "OK"]
    lines = [
        "# 보고서 수치 대조표",
        "",
        f"대조 대상 `{show_path(report_path)}` · 재현 `python3 03_분석/10_검증_숫자대조.py`",
        "",
        "등급 **A** 정제 CSV에서 독립 재계산 · **B** 08번 산출 CSV 집계 · **C** 근거 문서 대조",
        "",
        f"## 결과: {len(results) - len(bad)}/{len(results)} 일치"
        + ("" if not bad else f" — **불일치 {len(bad)}건**"),
        "",
        "| 등급 | 항목 | 판정 | 값 / 사유 |",
        "|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda r: (r["status"] == "OK", r["label"])):
        mark = {"OK": "일치", "FAIL": "**불일치**", "MISS": "**앵커없음**"}[r["status"]]
        lines.append(f"| {r['grade']} | {r['label']} | {mark} | {r['detail']} |")

    lines += ["", "## 대조표에 오르지 않은 숫자", ""]
    if not orphans:
        lines.append("없다. 본문의 모든 숫자가 대조 대상이거나 화이트리스트에 있다.")
    else:
        lines.append(
            "아래 숫자는 이 스크립트가 검증하지 않았다. 출처를 눈으로 확인하고, "
            "산출물에서 나온 값이면 `build_claims()` 에 항목을 추가한다."
        )
        lines.append("")
        lines.append("| 값 | 본문 등장 횟수 |")
        lines.append("|---|---:|")
        for token, count in sorted(orphans.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {token} | {count} |")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return bad


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    if not report_path.exists():
        sys.exit(f"보고서를 찾을 수 없다: {report_path}")

    facts, sites = compute_facts()
    cluster_facts = compute_cluster_facts(sites)
    lines = normalize(report_path.read_text(encoding="utf-8"))

    claims = build_claims(facts, cluster_facts)
    results = [check(lines, label, anchor, expected, grade) for label, anchor, expected, grade in claims]
    results += check_names(lines, cluster_facts, facts)

    row_claims = build_row_claims(facts)
    results += [check_rows(lines, label, rows, grade) for label, rows, grade in row_claims]

    checked = [v for _, _, expected, _ in claims for v in expected]
    # 행 단위로 맞춘 표 안의 숫자는 검증된 것으로 본다. 아니면 전량 공개 표를 실을 때마다
    # 미검증 목록이 수백 줄로 부풀어 정작 봐야 할 것이 묻힌다.
    for _, rows, _ in row_claims:
        for row in rows:
            checked += re.findall(r"\d[\d,]*(?:\.\d+)?", row)
    orphans = scan_unverified(lines, checked)
    bad = write_report(report_path, results, orphans)

    print(f"대조 {len(results)}건 · 일치 {len(results) - len(bad)} · 불일치 {len(bad)}")
    for r in bad:
        print(f"  [{r['status']}] {r['label']} — {r['detail']}")
    if orphans:
        print(f"미검증 숫자 {len(orphans)}종: {', '.join(sorted(orphans)[:20])}")
    print(f"→ {show_path(OUT_MD)}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
