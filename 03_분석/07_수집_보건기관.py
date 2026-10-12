"""창원시 보건소·보건지소·보건진료소를 수집해 좌표화한다. 공공 1차의료 공급을 빠뜨리면 안 된다.

검토(2026-09-15)에서 확인된 결함의 교정이다. 의료기관 현황 3파일은 의료법상
개설신고 기관만 담아 보건기관이 통째로 빠져 있었고, 그 결과 읍면 지역 의료 취약도가
과대추정됐다.

출처 선정 경위 (재현 가능성 실측):
  - 창원시 데이터포털: 보건지소 '인력 통계'만 있고 위치 데이터가 없다.
  - 공공데이터포털 전국보건기관표준데이터(API): 전국 300건뿐이고 경남은 합천군만.
    창원시는 아직 등록하지 않았다 → 사용 불가.
  - 보건복지부_전국 지역보건의료기관 현황(파일데이터): 2025 기준, 주소 포함,
    **로그인·인증키 없이 다운로드 가능** → 이것을 채택했다.

좌표는 04번과 같은 VWorld 파이프라인·같은 캐시를 쓴다. 캐시가 차 있으면 키 없이 재현된다.

출력: 02_데이터/raw/보건기관/전국_지역보건의료기관_현황.csv
      02_데이터/clean/보건기관_좌표.csv
      03_분석/보건기관_수집노트.md
재현: python3 03_분석/07_수집_보건기관.py
"""
import argparse
import csv
import hashlib
import importlib.util
import io
import re
import ssl
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "02_데이터" / "raw" / "보건기관"
CLEAN = ROOT / "02_데이터" / "clean"
OUT_CSV = CLEAN / "보건기관_좌표.csv"
NOTE_MD = ROOT / "03_분석" / "보건기관_수집노트.md"

DETAIL_PAGE = "https://www.data.go.kr/data/3072692/fileData.do"
DOWNLOAD = "https://www.data.go.kr/cmm/cmm/fileDownload.do"
CTX = ssl.create_default_context()

# 진료를 하는 기관만 의료 공급으로 센다. 건강생활지원센터는 진료기관이 아니다.
CARE_TYPES = ("보건소", "보건의료원", "일반보건지소", "통합보건지소", "보건지소", "보건진료소")


def load_geocoder():
    """04번의 주소 정규화·지오코딩을 그대로 재사용한다. 규칙이 갈라지면 재현성이 깨진다."""
    spec = importlib.util.spec_from_file_location("geo", ROOT / "03_분석" / "04_정제_지오코딩.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch(url: str, referer: str | None = None) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=CTX, timeout=120) as resp:
        return resp.read()


def download_source(pinned: str | None) -> tuple[Path, str, str]:
    """첨부파일을 받는다. ID는 상세페이지에서 긁되, 제출본은 --file-id 로 고정한다.

    원본이 갱신되면 같은 명령이 다른 입력을 받아 결과가 달라진다. 11월 공개 검증에서는
    제출 당시의 파일 ID와 sha256 으로 같은 입력임을 보일 수 있어야 한다.
    """
    if pinned:
        file_id = pinned
    else:
        page = fetch(DETAIL_PAGE).decode("utf-8", errors="replace")
        match = re.search(r"atchFileId=(FILE_[0-9A-Za-z]+)", page)
        if not match:
            raise RuntimeError("첨부파일 ID를 찾지 못했다 — 페이지 구조가 바뀌었을 수 있다")
        file_id = match.group(1)
    blob = fetch(f"{DOWNLOAD}?atchFileId={file_id}&fileDetailSn=1&insertDataPrcus=N", DETAIL_PAGE)
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / "전국_지역보건의료기관_현황.csv"
    path.write_bytes(blob)
    return path, file_id, hashlib.sha256(blob).hexdigest()


def read_changwon(path: Path) -> list[dict]:
    """시군구 컬럼으로 거른다. 행 전체 문자열로 '창원'을 찾으면 단양군 별방창원로,
    함양군 창원보건진료소 같은 것이 섞인다."""
    text = path.read_bytes().decode("cp949")
    rows = list(csv.DictReader(io.StringIO(text)))
    return [r for r in rows if "창원시" in (r["시군구"] or "")]


def strip_tail(address: str) -> str:
    """'외 3필지', 기관명 반복 같은 꼬리를 떼어 지오코딩 성공률을 올린다."""
    cleaned = re.sub(r"\s*외\s*\d+\s*필지.*$", "", address)
    return re.sub(r"\s+", " ", cleaned).strip()


def geocode_rows(rows: list[dict], geo, key, ctx) -> list[dict]:
    stats = Counter()
    cache = geo.load_cache()
    records = []
    for row in rows:
        address = strip_tail(row["주소"] or "")
        result = geo.geocode(address, cache, key, ctx, stats)
        records.append(
            {
                "보건기관명": (row["보건기관명"] or "").strip(),
                "기관유형": (row["기관유형"] or "").strip(),
                "상위기관": (row["상위기관명"] or "").strip(),
                "구": geo.guess_gu(result.get("정제주소", "")) or geo.guess_gu(address),
                "원주소": address,
                "질의주소": result.get("질의주소", ""),
                "정제주소": result.get("정제주소", ""),
                "경도": result.get("경도", ""),
                "위도": result.get("위도", ""),
                "매칭등급": result.get("매칭등급", ""),
                "진료기관": "Y" if (row["기관유형"] or "").strip() in CARE_TYPES else "N",
                "출처": "보건복지부 전국 지역보건의료기관 현황",
                "취득일": date.today().isoformat(),
            }
        )
    if key:
        geo.save_cache(cache)
    return records


def write_note(records: list[dict], file_id: str, digest: str) -> None:
    by_type = Counter(r["기관유형"] for r in records)
    care = [r for r in records if r["진료기관"] == "Y"]
    failed = [r for r in records if not r["위도"]]
    lines = [
        "# 보건기관 수집노트 — 공공 1차의료 공급 보정",
        "",
        "재현 `python3 03_분석/07_수집_보건기관.py` · 지오코딩 캐시에 없는 주소는 VWORLD_KEY 필요",
        "",
        "## 왜 필요했나",
        "",
        "의료기관 현황 3파일은 의료법상 개설신고 기관만 담는다. 보건소·보건지소·보건진료소가",
        "통째로 빠져 있어 읍면 지역 의료 취약도가 과대추정됐다. 검토(2026-09-15)에서 확인된 결함이다.",
        "",
        "## 출처 선정 (재현 가능성 실측 결과)",
        "",
        "| 후보 | 결과 |",
        "|---|---|",
        "| 창원시 데이터포털 | 보건지소 **인력 통계만** 수록, 위치 데이터 없음 → 불가 |",
        "| 공공데이터포털 전국보건기관표준데이터 (API) | 전국 300건뿐, 경남은 합천군만. **창원시 미등록** → 불가 |",
        "| 창원시 홈페이지 | 통합 목록 없음, 주소가 동적 지도 링크 뒤 → 스크랩 불안정 |",
        "| **보건복지부 전국 지역보건의료기관 현황 (파일데이터)** | 2025 기준, 주소 수록, **키·로그인 불필요** → 채택 |",
        "",
        f"출처: https://www.data.go.kr/data/3072692/fileData.do",
        "",
        f"- 첨부파일 ID `{file_id}`",
        f"- 원본 sha256 `{digest}`",
        f"- 취득일 {date.today().isoformat()}",
        "",
        f"고정 재현: `python3 03_분석/07_수집_보건기관.py --file-id {file_id}`",
        "(옵션 없이 실행하면 그 시점의 최신 첨부파일을 받아 결과가 달라질 수 있다)",
        "",
        "## 수집 결과",
        "",
        f"창원시 보건기관 **{len(records)}건** (진료기관 {len(care)}건 · 좌표 실패 {len(failed)}건)",
        "",
        "| 기관유형 | 건수 | 진료 공급 포함 |",
        "|---|---:|---|",
    ]
    for kind, count in by_type.most_common():
        lines.append(f"| {kind} | {count} | {'○' if kind in CARE_TYPES else '× (진료기관 아님)'} |")

    lines += [
        "",
        "## 기관 목록",
        "",
        "| 기관명 | 유형 | 구 | 주소 |",
        "|---|---|---|---|",
    ]
    for r in sorted(records, key=lambda r: (r["기관유형"], r["보건기관명"])):
        lines.append(f"| {r['보건기관명']} | {r['기관유형']} | {r['구']} | {r['원주소']} |")

    if failed:
        lines += ["", "## 좌표 실패", ""]
        for r in failed:
            lines.append(f"- {r['보건기관명']} — {r['원주소']}")

    lines += [
        "",
        "## 한계",
        "",
        "- 기준연도가 2025로 의료기관 현황(2025-08 ~ 2026-02)과 다르다. 보건기관은 이전이 드물어",
        "  영향이 작다고 보지만 보고서에 시점을 명시한다.",
        "- 건강생활지원센터 2개소는 진료기관이 아니므로 공급에서 제외했다.",
        "- 운영시간·진료과목 정보가 없다. '접근 가능한 거리'이지 '이용 가능한 시간'이 아니다.",
    ]
    NOTE_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="창원시 보건기관을 수집·좌표화한다")
    parser.add_argument("--file-id", help="첨부파일 ID 고정 (FILE_...). 생략하면 최신")
    args = parser.parse_args()

    geo = load_geocoder()
    key = geo.read_dotenv_key()
    ctx = ssl.create_default_context()

    path, file_id, digest = download_source(args.file_id)
    rows = read_changwon(path)
    print(f"창원시 보건기관 {len(rows)}건 → 지오코딩")

    records = geocode_rows(rows, geo, key, ctx)
    CLEAN.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    write_note(records, file_id, digest)

    ok = sum(1 for r in records if r["위도"])
    care = sum(1 for r in records if r["진료기관"] == "Y" and r["위도"])
    print(f"좌표 {ok}/{len(records)} · 진료기관 {care}건")
    print(f"산출 {OUT_CSV.relative_to(ROOT)} · {NOTE_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
