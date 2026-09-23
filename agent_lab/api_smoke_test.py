"""agent_lab.api_smoke_test —— 用 Python 客户端把 API 全流程测一遍。

【为什么不用 PowerShell 测】
PowerShell 5.1 的 `Invoke-RestMethod`：
  ① 默认不按 UTF-8 编码请求体 → 中文变乱码 → 模型收到乱码问题，行为诡异
  ② 响应按 Latin-1 解码 → 中文显示成 `å®žä»˜é¢`
这两件事会让人误以为"接口有 bug"，其实是客户端的问题。
用 Python + urllib 显式指定 UTF-8，就没有这类**测试工具自己引入的假故障**。

用法：
    python agent_lab/api_smoke_test.py              # 不花钱的接口
    python agent_lab/api_smoke_test.py --with-llm   # 额外测 POST /analyze（会调 LLM）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8010"   # 端口不是默认的 8000：本机 8000 被 C-Lodop 打印控件占着（见 agent_lab/api.py 的 PORT 注释）

# Windows 默认控制台是 GBK，直接打印 ✅/❌ 会 UnicodeEncodeError 崩掉整个测试。
# 显式把 stdout 改成 UTF-8，让这个脚本在默认 cmd/PowerShell 里也能跑。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PASS, FAIL = "✅", "❌"
results: list[tuple[str, bool, str]] = []


def call(method: str, path: str, payload: dict | None = None, timeout: int = 120):
    """发一个请求，返回 (状态码, 解析后的 body 或原始文本)。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")          # ← 显式 UTF-8 解码
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  {PASS if ok else FAIL} {name} — {detail}")


def main() -> int:
    global BASE  # 允许用 --base 覆盖服务地址（必须写在任何引用之前）
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-llm", action="store_true", help="额外测 POST /analyze（调 LLM，会花钱）")
    ap.add_argument("--base", default=BASE, help="服务地址")
    args = ap.parse_args()
    BASE = args.base.rstrip("/")

    print(f"=== 冒烟测试 {BASE} ===")

    print("\n[1] GET /health —— 探活")
    code, body = call("GET", "/health")
    check("健康检查", code == 200 and body.get("status") == "ok",
          f"HTTP {code} status={body.get('status')} rows={body.get('orders_rows')}")

    print("\n[2] GET /metrics —— 查询参数")
    code, body = call("GET", "/metrics?metric=revenue&period=2025-11")
    check("11 月实付额", code == 200 and abs(float(body.get("value", 0)) - 10117593.28) < 0.01,
          f"HTTP {code} value={body.get('value')}")

    print("\n[3] GET /metrics?period=2025-13 —— 边界校验应返回 422")
    code, body = call("GET", "/metrics?period=2025-13")
    check("非法月份被挡", code == 422, f"HTTP {code}（期望 422）")

    print("\n[4] GET /anomalies —— 复用分析内核")
    code, body = call("GET", "/anomalies?period=2025-11")
    check("11 月异常列表", code == 200 and "count" in body,
          f"HTTP {code} count={body.get('count')}")

    print("\n[5] GET /report/{period} —— 慢接口 + 对账结论")
    t0 = time.time()
    code, body = call("GET", "/report/2025-11")
    elapsed = time.time() - t0
    check("生成报告并对账",
          code == 200 and body.get("publishable") is True,
          f"HTTP {code} 耗时 {elapsed:.1f}s publishable={body.get('publishable')} "
          f"违规={body.get('text_reconciliation', {}).get('violations')} "
          f"markdown={len(body.get('markdown', ''))} 字符")

    print("\n[6] GET /reconciliation/{period} —— 读产物（先测不存在的 → 404）")
    code, body = call("GET", "/reconciliation/1999-01")
    check("不存在的资源返回 404", code == 404, f"HTTP {code}（期望 404）")

    print("\n[7] GET /attribution/{period} —— 多层下钻链条是否闭合 + 非法维度是否被挡")
    code, body = call("GET", "/attribution/2025-11")
    chain = body.get("chain", []) if isinstance(body, dict) else []
    closed = (
        code == 200 and len(chain) == 2
        and abs(chain[1]["subset_total_delta"] - chain[0]["top"][0]["delta"]) < 0.01
        and all(abs(s["decompose_check"]) < 1e-6 for s in chain)
    )
    check("下钻链闭合（子层 Δ == 父层被选中行 Δ）", closed,
          f"HTTP {code} 层数={len(chain)} "
          + (f"Δ0={chain[0]['subset_total_delta']} Δ1={chain[1]['subset_total_delta']}" if len(chain) == 2 else "链不完整"))
    code, _ = call("GET", "/attribution/2025-11?levels=region,product")
    check("非法维度被挡成 400", code == 400, f"HTTP {code}（期望 400）")

    if args.with_llm:
        print("\n[8] POST /analyze —— 请求体模型 + 中文 UTF-8（关键：验证中文没被搞坏）")
        code, body = call("POST", "/analyze",
                          {"question": "2025年11月的实付额是多少？", "max_steps": 4})
        answer = body.get("answer", "") if isinstance(body, dict) else str(body)
        # 中文没坏 + 结论正确 + 步数合理（不该把 4 步全耗在工具上）
        ok = (code == 200 and isinstance(body, dict)
              and "10,117,593.28" in answer and body.get("steps", 99) <= 3)
        check("中文问题被正确处理", ok,
              f"HTTP {code} 步数={body.get('steps')} 工具={body.get('tool_calls')} "
              f"tokens={body.get('tokens')} 停止={body.get('stop_reason')}")
        print(f"      回答：{answer[:80]}")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n=== 结果：{passed}/{len(results)} 通过 ===")
    for name, ok, _ in results:
        if not ok:
            print(f"  {FAIL} 未通过：{name}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
