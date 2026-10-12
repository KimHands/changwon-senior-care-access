"""창원시 데이터포털 공지사항에서 역대 공모전 수상작 요약서를 내려받아 텍스트로 추출.

수상 도메인 분포 판정의 1차 근거. 공지사항 목록을 전수 크롤링해 결과 게시글을 찾고,
첨부된 요약서(.hwp/.hwpx/.zip)를 02_데이터/raw/ 아래에 보존한 뒤 텍스트로 변환한다.

의존성: olefile  (python3 -m venv .venv && .venv/bin/pip install olefile)
재현: python3 03_분석/00_수집_역대수상작_요약서.py
"""
import html
import re
import ssl
import struct
import urllib.request
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "02_데이터" / "raw" / "역대수상작요약서"
TXT = ROOT / "02_데이터" / "clean" / "역대수상작요약서_txt"
BASE = "https://bigdata.changwon.go.kr/portal/board/notice"

# 공지사항 목록 크롤링으로 확인한 결과 게시글 (연도, boardIdx, 첨부 seq)
AWARD_POSTS = [
    ("2020", 12, 5),
    ("2021", 24, 15),
    ("2022", 34, 21),
    ("2023", 49, 29),
    ("2025", 70, 43),
]

CTX = ssl.create_default_context()
# TLS 검증은 켠 채로 둔다 — 분석 원본 취득 경로에서 끄면 중간자 공격에 노출된다.


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
        return r.read()


def list_award_notices():
    """공지사항 전수 크롤링 → 공모전 결과/수상 관련 게시글 제목 목록(감사 추적용)."""
    found, page = {}, 1
    while True:
        s = get(f"{BASE}/list.do?page={page}").decode("utf-8", errors="replace")
        hits = re.findall(r'idx=(\d+)"[^>]*class="veiw_link"[^>]*>(.*?)</a>', s, re.S)
        if not hits:
            break
        for idx, title in hits:
            title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", title))).strip()
            if any(w in title for w in ("공모", "수상", "검증")):
                found[int(idx)] = title
        page += 1
    return dict(sorted(found.items(), reverse=True))


def hwp_text(path):
    """HWP 5.x(OLE) BodyText에서 문단 텍스트 추출."""
    import olefile

    f = olefile.OleFileIO(str(path))
    compressed = bool(f.openstream("FileHeader").read()[36] & 1)
    out = []
    sections = sorted(
        (d for d in f.listdir() if d[0] == "BodyText"),
        key=lambda d: int(re.sub(r"\D", "", d[1]) or 0),
    )
    for d in sections:
        data = f.openstream(d).read()
        if compressed:
            data = zlib.decompress(data, -15)
        i = 0
        while i < len(data) - 4:
            header = struct.unpack("<I", data[i : i + 4])[0]
            tag, size = header & 0x3FF, (header >> 20) & 0xFFF
            i += 4
            if size == 0xFFF:
                size = struct.unpack("<I", data[i : i + 4])[0]
                i += 4
            if tag == 67:  # HWPTAG_PARA_TEXT
                out.append(_para_text(data[i : i + size]))
            i += size
    f.close()
    return "\n".join(out)


# HWP 본문의 제어문자: 16바이트(inline object) / 2바이트(단순) 구분
WIDE_CTRL = {1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}
NARROW_CTRL = {4, 5, 6, 7, 8, 9, 19, 20}


def _para_text(chunk):
    s, j = "", 0
    while j < len(chunk) - 1:
        code = struct.unpack("<H", chunk[j : j + 2])[0]
        if code in (0, 10, 13):
            s += "\n"
            j += 2
        elif code in WIDE_CTRL:
            j += 16
        elif code in NARROW_CTRL:
            j += 2
        else:
            s += chr(code)
            j += 2
    return s


def hwpx_text(path):
    out = []
    with zipfile.ZipFile(path) as z:
        names = sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml", n))
        for n in names:
            s = z.read(n).decode("utf-8", errors="replace")
            s = re.sub(r"</hp:p>", "\n", s)
            s = re.sub(r"<hp:t>(.*?)</hp:t>", r"\1", s, flags=re.S)
            out.append(re.sub(r"<[^>]+>", "", s))
    return "\n".join(out)


def extract(path):
    return hwpx_text(path) if path.suffix.lower() == ".hwpx" else hwp_text(path)


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    TXT.mkdir(parents=True, exist_ok=True)

    notices = list_award_notices()
    (RAW / "_공지사항_공모전관련_전체목록.txt").write_text(
        "\n".join(f"{k}\t{v}" for k, v in notices.items()), encoding="utf-8"
    )
    print(f"공모전 관련 공지 {len(notices)}건 확인")

    for year, board_idx, seq in AWARD_POSTS:
        blob = get(f"{BASE}/downloadFile.do?seq={seq}&boardIdx={board_idx}")
        is_zip = blob[:2] == b"PK"
        archive = RAW / f"{year}_수상작요약서{'.zip' if is_zip else '.hwp'}"
        archive.write_bytes(blob)

        targets = []
        if is_zip:
            year_dir = RAW / year
            year_dir.mkdir(exist_ok=True)
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    try:
                        name = info.filename.encode("cp437").decode("cp949")
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        name = info.filename
                    dest = year_dir / Path(name).name
                    dest.write_bytes(z.read(info))
                    targets.append(dest)
        else:
            targets.append(archive)

        for t in targets:
            text = re.sub(r"\n{3,}", "\n\n", extract(t))
            (TXT / f"{year}_{t.stem}.txt").write_text(text, encoding="utf-8")
        print(f"{year}: 요약서 {len(targets)}건 → 텍스트 추출 완료")


if __name__ == "__main__":
    main()
