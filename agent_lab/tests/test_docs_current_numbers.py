"""tests/test_docs_current_numbers —— 文档里唯一那个"现在时"的测试条数必须是真的。

为什么需要这条测试：同一个测试条数我 09-25 那晚改了四遍，每次都漏文件 ——
框架文档改了、README 没改、AGENTS.md 没改，最后公开库里同时存在 245 和 273。
"我记得改全了"已经被证明不可信，所以改成机器盯。

**设计取舍**：条数只允许出现在**一个**地方（README 顶部表格那行）。
其它文档一律写"全量单测（条数见 README / `python -m pytest`）"。
理由：多抄一处就多一处会烂；留一处 + 一条测试盯它，才是可维护的。
带日期的历史快照（"09-24 那次 245 passed"、"230 → 245"）不参与核对 ——
那是当时的记录，改它等于篡改历史。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 允许写死条数的唯一位置（文件, 必须出现在该行的字样）
SOLE_NUMBERED_CLAIM = ("README.md", "| `agent_lab/tests/` |")
# 除它以外还出现"现在时条数"的文件，一律算违规
SCAN = ["README.md", "AGENTS.md", "agent_lab/README.md", "agent_lab/代码导读.md",
        "docs/销售经营分析Agent_项目框架.md", ".github/workflows/ci.yml"]

CURRENT_PATTERNS = [
    re.compile(r"(?P<n>\d+)\s*项\s*pytest"),
    re.compile(r"单元测试（(?P<n>\d+)\s*项"),
    re.compile(r"单元测试\s+(?P<n>\d+)\s*项"),
    re.compile(r"pytest，(?P<n>\d+)\s*项"),
    re.compile(r"自动跑\s*(?P<n>\d+)\s*项"),
    re.compile(r"\|\s*测试\s*\|\s*pytest\s+(?P<n>\d+)\s*项"),
]
DATE_RE = re.compile(r"\d{2}-\d{2}|\d{4}-\d{2}-\d{2}")
HISTORY_MARKERS = ("→", "旧数", "那次", "上一版")


def collected_count() -> int:
    """用 pytest 自己数收集到多少条 —— 不写死，也不靠我数点。

    为什么开子进程：本测试自己就在被收集，进程内拿不到"全部条数"。
    `--collect-only -q` 的输出里有 "N tests collected in ..."。
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "agent_lab/tests", "--collect-only", "-q",
         "-p", "no:cacheprovider", "--no-header", "-o", "addopts="],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    m = re.search(r"(\d+) tests? collected", proc.stdout)
    if not m:
        raise AssertionError("读不到 pytest 的收集条数，本测试等于空转：\n"
                             + proc.stdout[-600:] + "\n" + proc.stderr[-600:])
    return int(m.group(1))


def _live_claims() -> list[tuple[str, int, str]]:
    """收集所有"现在时"的条数声明（跳过带日期的历史快照行）。"""
    out = []
    for rel in SCAN:
        path = ROOT / rel
        assert path.exists(), f"清单里的文件不存在：{rel}（改名要同步改这条测试）"
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(mk in line for mk in HISTORY_MARKERS) or DATE_RE.search(line):
                continue
            for pat in CURRENT_PATTERNS:
                for m in pat.finditer(line):
                    out.append((rel, int(m.group("n")), line.strip()[:90]))
    return out


def test_only_one_place_hardcodes_the_count():
    """条数只许出现在约定的那一个位置。抄第二处 = 给未来留一处会烂的数字。"""
    offenders = [(rel, n, line) for rel, n, line in _live_claims()
                 if not (rel == SOLE_NUMBERED_CLAIM[0] and SOLE_NUMBERED_CLAIM[1] in line)]
    assert not offenders, "这些地方不该写死条数，改成『全量单测（条数以 pytest 输出为准）』：\n" + \
        "\n".join(f"  {rel}:（{n} 项）{line}" for rel, n, line in offenders)


def test_the_one_numbered_claim_is_true():
    """那唯一的一处，数字必须等于实跑收集到的条数。"""
    claims = [(n, line) for rel, n, line in _live_claims()
              if rel == SOLE_NUMBERED_CLAIM[0] and SOLE_NUMBERED_CLAIM[1] in line]
    assert len(claims) == 1, f"README 顶部应当恰好有 1 处条数声明，实到 {len(claims)} 处：{claims}"
    n = collected_count()
    assert claims[0][0] == n, f"README 写着 {claims[0][0]} 项，pytest 实际收集 {n} 项 —— 改 README，别改这条测试"


def test_patterns_really_bite():
    """阳性对照：模式若匹配不到任何东西，上面两条测试会永远绿，比没有更糟。"""
    probe = [f"| `agent_lab/tests/` | 999 项 pytest 单测（不连库） |",
             f"自动跑 888 项", f"| 测试 | pytest 777 项 + |"]
    got = sorted({m.group("n") for line in probe for pat in CURRENT_PATTERNS for m in pat.finditer(line)})
    assert got == ["777", "888", "999"], f"假行没被抓全：{got}"
