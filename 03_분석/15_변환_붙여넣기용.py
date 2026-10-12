"""제출본 마크다운을 한글에 붙여 넣을 평문으로 바꾼다.

왜 필요한가. 제출본은 사용자가 한글에 직접 붙여 넣어 조판한다. 마크다운을 그대로 복사하면
`#`·`**`·`|` 기호가 본문에 섞여 들어가고, 이 컴퓨터에는 렌더링해서 복사할 뷰어가 없다.
그래서 기호를 걷어낸 평문을 만든다. 표는 칸을 탭으로 나눠 두면 한글의 '문자열을 표로'
기능으로 한 번에 표가 된다.

`▶` 로 시작하는 줄은 조판 안내다. 붙여 넣은 뒤 지운다.

내용은 바꾸지 않는다. 변환 전후의 숫자 토큰 목록이 같은지 스스로 대조하고,
다르면 종료코드 1로 끝난다.

입력: 04_보고서/제출본_분석보고서_본문.md ("# Ⅰ." 줄부터 — 앞의 안내 블록은 건너뛴다)
출력: 04_보고서/제출본_분석보고서_붙여넣기용.txt
재현: python3 03_분석/15_변환_붙여넣기용.py
"""
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "04_보고서" / "제출본_분석보고서_본문.md"
OUT = ROOT / "04_보고서" / "제출본_분석보고서_붙여넣기용.txt"
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def body_lines(text):
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith("# Ⅰ."))
    return lines[start:]


def plain(text):
    """강조·이스케이프·인라인 코드 기호만 걷어낸다. 글자는 건드리지 않는다."""
    text = text.replace("**", "").replace("\\*", "*")
    return re.sub(r"`([^`]*)`", r"\1", text)


def cells(row):
    return [plain(c.strip()) for c in row.strip().strip("|").split("|")]


def is_separator(line):
    return re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", line.strip()) is not None


def convert(lines):
    out, table_no, i = [], 0, 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            end = next(j for j in range(i + 1, len(lines)) if lines[j].startswith("```"))
            out.append(f"▶ 아래 {end - i - 1}줄은 명령어 — 맑은 고딕 9pt, 왼쪽 정렬")
            out += lines[i + 1 : end]
            i = end + 1
            continue
        if line.startswith("|"):
            table_no += 1
            out.append(f"▶ [표 {table_no}] 아래 줄부터 빈 줄 전까지 선택 → 문자열을 표로(탭 구분)")
            while i < len(lines) and lines[i].startswith("|"):
                if not is_separator(lines[i]):
                    out.append("\t".join(cells(lines[i])))
                i += 1
            continue
        if line.strip() == "---":
            i += 1
            continue
        if line.startswith("> "):
            line = line[2:]
        out.append(plain(re.sub(r"^#+ ", "", line)))
        i += 1
    return out, table_no


def numbers(lines):
    found = Counter()
    for line in lines:
        if line.startswith("▶") or is_separator(line):
            continue
        found.update(NUMBER.findall(line))
    return found


def main():
    source = body_lines(SRC.read_text(encoding="utf-8"))
    converted, tables = convert(source)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(converted)).strip() + "\n"
    OUT.write_text(text, encoding="utf-8")

    before = numbers([l for l in source if not l.startswith(">")])
    after = numbers(text.split("\n"))
    print(f"표 {tables}개 · 숫자 토큰 {sum(before.values())}개 → {sum(after.values())}개")
    print(f"→ {OUT.relative_to(ROOT)}")
    if before != after:
        print(f"숫자 토큰 불일치: 빠짐 {before - after} · 생김 {after - before}")
        sys.exit(1)


if __name__ == "__main__":
    main()
