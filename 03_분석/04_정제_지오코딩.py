"""후보 데이터의 도로명주소 2,380건을 VWorld 지오코더로 좌표화하고, 실패율과 실패 건 목록을 남긴다.

접근성 분석은 좌표 없이 성립하지 않는데 후보 데이터에는 위경도가 하나도 없다. 이 스크립트가
그 간극을 메운다. 11월 공개 검증에서 가장 먼저 의심받을 구간이므로 '몇 건이 어느 방식으로
붙었고 몇 건이 왜 실패했는지'를 전부 산출물로 남긴다.

키 없이도 끝까지 돌아간다:
  - 지오코딩 결과는 02_데이터/clean/지오코딩_캐시.csv 에 커밋된다.
  - 캐시가 채워져 있으면 API를 호출하지 않으므로 심사위원은 키 없이 재현할 수 있다.
  - 키를 가진 사람은 --refresh 로 전량 재조회해 캐시와 대조할 수 있다.

출력: 02_데이터/clean/경로당_좌표.csv
      02_데이터/clean/의료기관_좌표.csv        (구별 기준시점·관리부서 동봉)
      02_데이터/clean/식사배달기관_좌표.csv
      02_데이터/clean/지오코딩_캐시.csv
      02_데이터/clean/지오코딩_실패목록.csv
      03_분석/지오코딩_리포트.md
재현: VWORLD_KEY=<키> python3 03_분석/04_정제_지오코딩.py
      python3 03_분석/04_정제_지오코딩.py --dry-run   # 키 없이 정규화 결과만 점검
"""
import argparse
import csv
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "02_데이터" / "raw" / "후보데이터"
CLEAN = ROOT / "02_데이터" / "clean"
CACHE_CSV = CLEAN / "지오코딩_캐시.csv"
FAIL_CSV = CLEAN / "지오코딩_실패목록.csv"
REPORT_MD = ROOT / "03_분석" / "지오코딩_리포트.md"

VWORLD_URL = "https://api.vworld.kr/req/address"
REQUEST_INTERVAL_SEC = 0.12
MAX_RETRY = 3

# 의료기관 현황은 3개 부서가 각자 갱신해 기준시점이 최대 6개월 어긋난다.
# 합본 테이블에 기준시점을 동봉하지 않으면 공개 검증에서 출처를 소명할 수 없다.
SOURCES = [
    {
        "이름": "경로당",
        "폴더": "경상남도_창원시_경로당_현황",
        "주소컬럼": "도로명주소",
        "이름컬럼": "경로당명",
        "기준시점": "2026-02-02",
        "관리부서": "노인장애인과",
        "출력": "경로당_좌표.csv",
    },
    {
        "이름": "의료기관",
        "폴더": "경상남도_창원시_의창구_성산구_의료기관_현황",
        "주소컬럼": "의료기관주소(도로명)",
        "이름컬럼": "의료기관명",
        "기준시점": "2025-11-19",
        "관리부서": "보건정책과",
        "출력": "의료기관_좌표.csv",
    },
    {
        "이름": "의료기관",
        "폴더": "경상남도_창원시_마산합포구_마산회원구_의료기관_현황",
        "주소컬럼": "의료기관주소(도로명)",
        "이름컬럼": "의료기관명",
        "기준시점": "2026-02-05",
        "관리부서": "마산보건소 보건행정과",
        "출력": "의료기관_좌표.csv",
    },
    {
        "이름": "의료기관",
        "폴더": "경상남도_창원시_진해구_의료기관_현황",
        "주소컬럼": "의료기관주소(도로명)",
        "이름컬럼": "의료기관명",
        "기준시점": "2025-08-05",
        "관리부서": "진해보건소보건행정과",
        "출력": "의료기관_좌표.csv",
    },
    {
        "이름": "식사배달기관",
        "폴더": "경상남도_창원시_재가노인식사배달사업자현황",
        "주소컬럼": "소재지",
        "이름컬럼": "시설명",
        "기준시점": "2026-02-02",
        "관리부서": "복지여성보건국 노인장애인과",
        "출력": "식사배달기관_좌표.csv",
    },
]

CHANGWON_GU = ("의창구", "성산구", "마산합포구", "마산회원구", "진해구")

# 도로명 + 건물번호까지만 남기기 위한 패턴. '동읍로 359번길 47', '진해대로1026번길 4',
# '회원동32길 8' 처럼 번길이 붙거나 떨어진 형태를 모두 잡는다.
# 건물번호 뒤에 '번길·층·호'가 오면 건물번호가 아니다. 이 가드가 없으면 '합성동13길 2층'의
# 2를 건물번호로 오인해 실존하는 다른 건물 좌표를 만든다 — 실패보다 나쁜 결과다.
ROAD_PATTERN = re.compile(
    r"[가-힣A-Za-z0-9]+(?:대로|로|길)\s*(?:\d+번길)?\s*\d+(?:-\d+)?(?!\s*(?:번길|층|호))"
)
# 층·호 표기는 도로명주소의 건물번호가 될 수 없다. 조회 전에 떼어낸다.
FLOOR_PATTERN = re.compile(r"\s*(?:지하)?[Bb]?\d+\s*[층호]")


def read_text(path: Path) -> str:
    """공공데이터 CSV는 utf-8-sig 와 cp949 가 섞여 있다."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"인코딩 판별 실패: {path}")


def source_csv(folder: str) -> Path:
    """다운로드 잔재인 '... .csv (62kb)' 중복 파일을 제외하고 원본 1개를 고른다."""
    files = [p for p in (RAW / folder).glob("*.csv") if not re.search(r"\(\d+kb\)$", p.name)]
    if len(files) != 1:
        raise FileNotFoundError(f"{folder}: CSV 후보가 {len(files)}개 (1개여야 함)")
    return files[0]


def squash_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("　", " ")).strip()


def strip_detail(address: str) -> str:
    """쉼표 이후 상세주소, 괄호 안 건물명·법정동, 층·호 표기를 떼어낸다."""
    trimmed = address.split(",")[0]
    trimmed = re.sub(r"\([^)]*\)?", " ", trimmed)
    trimmed = FLOOR_PATTERN.sub(" ", trimmed)
    return squash_space(trimmed)


def road_only(address: str) -> str:
    """도로명 + 건물번호까지만 남긴다. 잡히지 않으면 빈 문자열."""
    stripped = strip_detail(address)
    matches = list(ROAD_PATTERN.finditer(stripped))
    if not matches:
        return ""
    end = matches[-1].end()
    return squash_space(stripped[:end])


def ensure_city(address: str) -> str:
    """'창원시'가 빠진 주소에 시 이름을 채운다 (실측 1건)."""
    if "창원시" in address or not address:
        return address
    return squash_space(address.replace("경상남도", "경상남도 창원시", 1))


def build_attempts(address: str) -> list[tuple[str, str, str]]:
    """(매칭등급, 질의주소, 주소유형) 순서대로 시도할 후보를 만든다.

    어느 등급에서 붙었는지가 곧 좌표의 신뢰도이므로 등급을 결과에 기록한다.
    입력 주소는 실측 결과 전부 도로명주소였으므로 지번(parcel) 조회는 두지 않는다.
    부번을 떼고 본번으로 조회하는 단계도 두지 않는다 — 부번 주소 294건 중 1건만 그 단계로
    넘어갔고, 그 1건이 95-6 을 95-15 에 붙이는 오매칭이었다. 근사 좌표보다 실패가 정직하다.
    """
    cleaned = squash_space(address)
    attempts = [
        ("L1_원문", cleaned, "road"),
        ("L2_상세제거", ensure_city(strip_detail(cleaned)), "road"),
        ("L3_도로명추출", ensure_city(road_only(cleaned)), "road"),
    ]
    seen, unique = set(), []
    for grade, query, kind in attempts:
        if not query or (query, kind) in seen:
            continue
        seen.add((query, kind))
        unique.append((grade, query, kind))
    return unique


def read_dotenv_key() -> str | None:
    """루트 .env 의 VWORLD_KEY 를 읽는다. .env 는 커밋되지 않는다."""
    dotenv = ROOT / ".env"
    if not dotenv.exists():
        return None
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "VWORLD_KEY":
            return value.strip().strip("'\"") or None
    return None


def load_cache() -> dict:
    if not CACHE_CSV.exists():
        return {}
    rows = list(csv.DictReader(io.StringIO(read_text(CACHE_CSV))))
    return {(r["질의주소"], r["주소유형"]): r for r in rows}


def save_cache(cache: dict) -> None:
    fields = ["질의주소", "주소유형", "상태", "경도", "위도", "정제주소", "출처", "조회일시"]
    CACHE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_CSV.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for key in sorted(cache):
            writer.writerow({f: cache[key].get(f, "") for f in fields})


def call_vworld(query: str, kind: str, key: str, ctx) -> dict:
    """VWorld 지오코더 1건 조회. 네트워크 오류는 재시도하고 NOT_FOUND는 그대로 돌려준다."""
    params = {
        "service": "address",
        "request": "getcoord",
        "version": "2.0",
        "crs": "epsg:4326",
        "address": query,
        "refine": "true",
        "simple": "false",
        "format": "json",
        "type": kind,
        "key": key,
    }
    url = f"{VWORLD_URL}?{urllib.parse.urlencode(params)}"
    for attempt in range(MAX_RETRY):
        try:
            with urllib.request.urlopen(url, context=ctx, timeout=20) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as exc:  # 네트워크·일시 오류만 재시도
            if attempt == MAX_RETRY - 1:
                return {"상태": f"ERROR:{type(exc).__name__}"}
            time.sleep(1.5 * (attempt + 1))
    response = body.get("response", {})
    status = response.get("status", "UNKNOWN")
    if status != "OK":
        detail = response.get("error", {}).get("text", "")
        return {"상태": status if not detail else f"{status}:{detail}"}
    point = response.get("result", {}).get("point", {})
    return {
        "상태": "OK",
        "경도": point.get("x", ""),
        "위도": point.get("y", ""),
        "정제주소": response.get("refined", {}).get("text", ""),
    }


def geocode(address: str, cache: dict, key: str, ctx, stats: Counter) -> dict:
    """등급 순으로 시도해 첫 성공을 채택한다. 전부 실패하면 마지막 실패 사유를 남긴다."""
    last = {"매칭등급": "실패", "상태": "미시도", "질의주소": ""}
    for grade, query, kind in build_attempts(address):
        hit = cache.get((query, kind))
        if hit is None:
            if key is None:
                last = {"매칭등급": "미조회", "상태": "키없음", "질의주소": query}
                continue
            result = call_vworld(query, kind, key, ctx)
            stats["api_call"] += 1
            time.sleep(REQUEST_INTERVAL_SEC)
            hit = {
                "질의주소": query,
                "주소유형": kind,
                "상태": result["상태"],
                "경도": result.get("경도", ""),
                "위도": result.get("위도", ""),
                "정제주소": result.get("정제주소", ""),
                "출처": "VWorld",
                "조회일시": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            }
            cache[(query, kind)] = hit
            if stats["api_call"] % 50 == 0:
                save_cache(cache)
        else:
            stats["cache_hit"] += 1
        if hit["상태"] == "OK":
            return {
                "매칭등급": grade,
                "상태": "OK",
                "질의주소": query,
                "경도": hit["경도"],
                "위도": hit["위도"],
                "정제주소": hit["정제주소"],
            }
        last = {"매칭등급": "실패", "상태": hit["상태"], "질의주소": query}
    return last


def guess_gu(address: str) -> str:
    for gu in CHANGWON_GU:
        if gu in address:
            return gu
    return ""


def normalize_gu(record: dict) -> None:
    """원본 구청명 표기가 '합포구'·'회원구'처럼 흔들리고 비어 있기도 하다.

    지오코딩으로 얻은 정제주소의 구를 정본으로 삼는다. 원본 표기는 원주소 컬럼에 남는다.
    """
    gu = guess_gu(record.get("정제주소", ""))
    if gu:
        record["구"] = gu


def collect_records() -> list[dict]:
    records = []
    for source in SOURCES:
        path = source_csv(source["폴더"])
        rows = list(csv.DictReader(io.StringIO(read_text(path))))
        for row in rows:
            address = squash_space(row.get(source["주소컬럼"], "") or "")
            if not address:
                continue
            records.append(
                {
                    "구분": source["이름"],
                    "기관명": squash_space(row.get(source["이름컬럼"], "") or ""),
                    "종별": squash_space(row.get("의료기관종별", "") or ""),
                    "구": squash_space(row.get("구청명") or row.get("구청") or "") or guess_gu(address),
                    "읍면동": squash_space(row.get("읍면동명", "") or ""),
                    "원주소": address,
                    "기준시점": source["기준시점"],
                    "관리부서": source["관리부서"],
                    "원본파일": path.name,
                    "출력": source["출력"],
                }
            )
    return records


def write_outputs(records: list[dict]) -> None:
    fields = [
        "구분", "기관명", "종별", "구", "읍면동", "원주소", "질의주소", "정제주소",
        "경도", "위도", "매칭등급", "기준시점", "관리부서", "원본파일",
    ]
    by_file = {}
    for record in records:
        by_file.setdefault(record["출력"], []).append(record)
    for name, rows in by_file.items():
        with (CLEAN / name).open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({f: row.get(f, "") for f in fields})

    failures = [r for r in records if r["매칭등급"] in ("실패", "미조회")]
    with FAIL_CSV.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(
            fp, fieldnames=["구분", "기관명", "구", "원주소", "최종질의주소", "실패사유", "원본파일"]
        )
        writer.writeheader()
        for row in failures:
            writer.writerow(
                {
                    "구분": row["구분"],
                    "기관명": row["기관명"],
                    "구": row["구"],
                    "원주소": row["원주소"],
                    "최종질의주소": row.get("질의주소", ""),
                    "실패사유": row.get("상태", ""),
                    "원본파일": row["원본파일"],
                }
            )


def write_report(records: list[dict], stats: Counter, dry_run: bool) -> None:
    total = len(records)
    success = sum(1 for r in records if r["매칭등급"].startswith("L"))
    grades = Counter(r["매칭등급"] for r in records)
    reasons = Counter(r.get("상태", "") for r in records if not r["매칭등급"].startswith("L"))
    by_kind = Counter(r["구분"] for r in records)
    kind_fail = Counter(r["구분"] for r in records if not r["매칭등급"].startswith("L"))

    lines = [
        "# 지오코딩 리포트 — 후보 데이터 좌표화",
        "",
        f"생성 {datetime.now().astimezone().isoformat(timespec='seconds')}"
        f" · 지오코더 VWorld(국토교통부 공간정보 오픈플랫폼) · 좌표계 EPSG:4326",
        "",
        "재현: `VWORLD_KEY=<키> python3 03_분석/04_정제_지오코딩.py`",
        "캐시(`02_데이터/clean/지오코딩_캐시.csv`)가 있으면 키 없이도 같은 결과가 나온다.",
        "",
        "## 총괄",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| 대상 주소 | {total:,}건 |",
        f"| 좌표 확보 | {success:,}건 |",
        f"| 실패 | {total - success:,}건 |",
        f"| **실패율** | **{(total - success) / total * 100:.2f}%** |",
        f"| API 호출 | {stats['api_call']:,}회 |",
        f"| 캐시 적중 | {stats['cache_hit']:,}회 |",
        "",
        "## 매칭등급 분포",
        "",
        "등급이 낮을수록 원본 주소를 많이 손댄 것이다. L3 비중이 크면 좌표 신뢰도를 별도로 논해야 한다.",
        "",
        "| 등급 | 의미 | 건수 |",
        "|---|---|---:|",
    ]
    meaning = {
        "L1_원문": "원본 주소 그대로 도로명 조회 성공",
        "L2_상세제거": "괄호·쉼표 뒤 상세주소를 떼고 성공",
        "L3_도로명추출": "도로명+건물번호만 남겨 성공",
        "실패": "3단계 모두 실패",
        "미조회": "API 키 없이 실행되어 조회하지 못함",
    }
    for grade, count in sorted(grades.items()):
        lines.append(f"| {grade} | {meaning.get(grade, '')} | {count:,} |")

    lines += ["", "## 데이터셋별", "", "| 구분 | 대상 | 실패 | 실패율 |", "|---|---:|---:|---:|"]
    for kind in by_kind:
        fail = kind_fail[kind]
        lines.append(f"| {kind} | {by_kind[kind]:,} | {fail:,} | {fail / by_kind[kind] * 100:.2f}% |")

    if reasons:
        lines += ["", "## 실패 사유", "", "| 사유 | 건수 |", "|---|---:|"]
        for reason, count in reasons.most_common():
            lines.append(f"| {reason or '(없음)'} | {count:,} |")

    lines += [
        "",
        "실패 건 전량은 `02_데이터/clean/지오코딩_실패목록.csv` 에 있다.",
        "",
        "## 기준시점 불일치 (분석 시 반드시 명시)",
        "",
        "의료기관 현황은 3개 부서가 각자 갱신해 구별 기준시점이 최대 6개월 어긋난다."
        " 좌표 CSV에 `기준시점`·`관리부서` 컬럼으로 동봉했다.",
        "",
        "| 대상 | 관리부서 | 기준시점 |",
        "|---|---|---|",
    ]
    for source in SOURCES:
        lines.append(f"| {source['폴더']} | {source['관리부서']} | {source['기준시점']} |")

    if dry_run:
        lines += ["", "> 이 리포트는 `--dry-run`(키 없음) 실행 결과다. 좌표는 캐시에 있는 것만 채워졌다."]

    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="후보 데이터 주소를 VWorld로 지오코딩한다")
    parser.add_argument("--dry-run", action="store_true", help="API 호출 없이 정규화·캐시만으로 실행")
    parser.add_argument("--refresh", action="store_true", help="캐시를 무시하고 전량 재조회")
    args = parser.parse_args()

    key = None if args.dry_run else (os.environ.get("VWORLD_KEY") or read_dotenv_key())
    if not args.dry_run and not key:
        print(
            "VWORLD_KEY 환경변수가 없다. 키 없이 정규화만 점검하려면 --dry-run 을 붙여라.\n"
            "  발급: https://www.vworld.kr > 오픈API > 인증키 발급",
            file=sys.stderr,
        )
        return 2

    CLEAN.mkdir(parents=True, exist_ok=True)
    cache = {} if args.refresh else load_cache()
    ctx = ssl.create_default_context()
    stats = Counter()

    records = collect_records()
    print(f"대상 주소 {len(records):,}건 · 유니크 원주소 {len({r['원주소'] for r in records}):,}건")

    resolved: dict[str, dict] = {}
    for index, record in enumerate(records, 1):
        address = record["원주소"]
        if address not in resolved:
            resolved[address] = geocode(address, cache, key, ctx, stats)
        record.update(resolved[address])
        normalize_gu(record)
        if index % 200 == 0:
            print(f"  {index:,}/{len(records):,} 처리 (API {stats['api_call']:,}회)")

    if key:
        save_cache(cache)
    write_outputs(records)
    write_report(records, stats, args.dry_run)

    success = sum(1 for r in records if r["매칭등급"].startswith("L"))
    total = len(records)
    print(
        f"완료: 좌표 {success:,}/{total:,} · 실패 {total - success:,}건"
        f" · 실패율 {(total - success) / total * 100:.2f}%"
    )
    print(f"리포트 {REPORT_MD.relative_to(ROOT)} · 실패목록 {FAIL_CSV.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
