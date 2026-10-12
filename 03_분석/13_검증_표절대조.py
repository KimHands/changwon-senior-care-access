"""제출 본문을 역대 수상작 요약서와 기계 대조한다 — 표절 자체검증.

공고 유의사항이 요구하는 것과 1차 평가의 '아이디어 중복 여부' 심사에 함께 대응한다.
사람이 눈으로 29건을 대조하는 것은 신뢰할 수 없다. 기계가 한다.

무엇을 재는가
  - **어절 n-gram 겹침**: 본문과 각 수상작 요약서에서 n어절 연속 구간을 뽑아 교집합을 본다.
    한국어는 조사가 붙어 표면형이 쉽게 달라지므로, 긴 n-gram 이 겹치면 우연이 아니다.
  - **핵심어 동시출현**: 도메인 용어가 함께 나오는 정도. 주제 중복의 신호다.

무엇을 재지 못하는가
  - **바꿔쓰기(paraphrase)는 잡지 못한다.** 표면형이 다르면 n-gram 이 걸리지 않는다.
    이 스크립트가 침묵해도 표절이 없다는 뜻이 아니다. 아이디어 중복은 사람이 읽어야 한다.
  - 요약서는 수상작의 **요약**이므로 본문 전체와 비교하는 것이 아니다.

출력: 03_분석/표절대조표.md
재현: python3 03_분석/13_검증_표절대조.py
종료코드: 긴 n-gram(기본 8어절) 겹침이 하나라도 있으면 1
"""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT = ROOT / "04_보고서" / "분석보고서_초고_v4.md"
CORPUS = ROOT / "02_데이터" / "clean" / "역대수상작요약서_txt"
OUT_MD = ROOT / "03_분석" / "표절대조표.md"

FLAG_N = 8      # 이만큼 연속으로 겹치면 우연으로 보지 않는다
WATCH_N = 5     # 참고로 함께 보고하는 길이

# 도메인이 같으면 당연히 겹치는 말들. 이것까지 세면 신호가 묻힌다.
COMMON = {
    "창원시", "창원", "경상남도", "데이터", "분석", "활용", "공모전", "공공데이터",
    "행정동", "읍면동", "대상", "지역", "현황", "결과", "기준", "제공", "필요",
    "의료", "보건", "접근성", "고령", "노인", "경로당", "의료기관", "보건소",
}


def normalize(text):
    """비교 전에 표면 잡음을 없앤다. 숫자는 한 토큰으로 묶어 표 수치가 잡음이 되지 않게 한다."""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[|#*`~\-—·>\[\]()]", " ", text)
    text = re.sub(r"\d[\d,.]*", "0", text)
    return [w for w in text.split() if w]


def ngrams(words, n):
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def content_words(words):
    return Counter(w for w in words if len(w) >= 2 and w not in COMMON)


def compare(report_words, other_words):
    result = {}
    for n in (FLAG_N, WATCH_N):
        shared = ngrams(report_words, n) & ngrams(other_words, n)
        result[n] = sorted(shared)
    a, b = content_words(report_words), content_words(other_words)
    common = set(a) & set(b)
    # 자카드 대신 상대 겹침 — 요약서가 본문보다 훨씬 짧아 자카드는 항상 낮게 나온다
    result["어휘겹침"] = len(common) / len(b) if b else 0.0
    result["공통어"] = sorted(common, key=lambda w: (-b[w], w))[:12]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args()
    path = Path(args.report if Path(args.report).is_absolute() else ROOT / args.report)
    if not path.exists():
        sys.exit(f"보고서를 찾을 수 없다: {path}")
    files = sorted(CORPUS.glob("*.txt"))
    if not files:
        sys.exit(f"대조할 요약서가 없다: {CORPUS}")

    report = normalize(path.read_text(encoding="utf-8"))
    rows, flagged = [], []
    for f in files:
        other = normalize(f.read_text(encoding="utf-8", errors="replace"))
        r = compare(report, other)
        rows.append((f.stem, len(other), r))
        if r[FLAG_N]:
            flagged.append((f.stem, r[FLAG_N]))

    rows.sort(key=lambda x: -x[2]["어휘겹침"])
    lines = [
        "# 표절 자체검증 — 역대 수상작 대조",
        "",
        f"대상 `{path.relative_to(ROOT)}` · 요약서 파일 {len(files)}건 · "
        "재현 `python3 03_분석/13_검증_표절대조.py`",
        "",
        "파일 수와 수상작 수는 다르다. 한 파일에 여러 수상작이 담긴 연도가 있어 "
        "**수상작 29건이 파일 %d개**에 들어 있다(`역대수상작_도메인코딩.csv` 기준)." % len(files),
        "",
        f"본문 {len(report):,}어절. {FLAG_N}어절 연속 일치를 표절 신호로, "
        f"{WATCH_N}어절을 참고로 본다. 숫자는 한 토큰으로 묶어 표 수치가 잡음이 되지 않게 했다.",
        "",
        f"## 판정: {FLAG_N}어절 연속 일치 **{len(flagged)}건**",
        "",
    ]
    if flagged:
        lines.append("아래 구간을 직접 확인하고 고쳐야 한다.")
        lines.append("")
        for name, hits in flagged:
            for h in hits:
                lines.append(f"- `{name}` — {h}")
    else:
        lines.append(
            f"없다. {FLAG_N}어절이 연속으로 겹치는 구간이 한 건도 없다. "
            "다만 이 검사는 바꿔쓰기를 잡지 못하므로, 아이디어 중복은 사람이 따로 읽어야 한다."
        )
    lines += [
        "",
        "## 수상작별 겹침",
        "",
        "어휘겹침은 요약서의 내용어 중 본문에도 나오는 비율이다. 도메인이 같으면 높아지는 것이 정상이라,",
        "**높다는 것 자체는 표절 신호가 아니다.** 주제가 가까운 수상작을 찾아내는 용도로 읽는다.",
        "",
        "| 수상작 요약서 | 요약서 어절 | 어휘겹침 | 5어절 | 8어절 | 공통 내용어(상위) |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for name, size, r in rows:
        lines.append(
            f"| {name} | {size:,} | {r['어휘겹침'] * 100:.0f}% | {len(r[WATCH_N])} | "
            f"**{len(r[FLAG_N])}** | {', '.join(r['공통어'][:8])} |"
        )
    lines += [
        "",
        "## 이 검사의 한계",
        "",
        "- **바꿔쓰기를 잡지 못한다.** 표면형이 다르면 n-gram 이 걸리지 않는다. 침묵이 무죄의 증거가 아니다.",
        "- 대조 대상은 수상작 **요약서**다. 본문 전체가 아니므로 겹침이 과소평가된다.",
        "- 어휘겹침이 높은 상위 몇 건은 **주제 근접**을 뜻한다. 1차 평가의 아이디어 중복 심사에",
        "  대비해 그 수상작들과의 차별점을 본문에 명시해야 한다.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"요약서 파일 {len(files)}건(수상작 29건) · 본문 {len(report):,}어절")
    print(f"{FLAG_N}어절 연속 일치: {len(flagged)}건")
    for name, _, r in rows[:5]:
        print(f"  어휘겹침 {r['어휘겹침'] * 100:5.1f}%  {WATCH_N}어절 {len(r[WATCH_N]):>2}  {name}")
    print(f"→ {OUT_MD.relative_to(ROOT)}")
    sys.exit(1 if flagged else 0)


if __name__ == "__main__":
    main()
