"""agent_lab.ui_smoke_test —— 用 Streamlit 官方 AppTest 无头跑一遍看板。

【为什么需要这个】
Streamlit 脚本是"运行时才执行"的：`py_compile` 过了**不代表 API 用对了**
（比如传了不支持的参数、`st.set_page_config` 位置不对、缓存函数签名有问题）。
`streamlit.testing.v1.AppTest` 会把 app 真正跑一遍，并把异常暴露出来 —— 相当于 UI 层的单测。

用法：python agent_lab/ui_smoke_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP = PROJECT_ROOT / "agent_lab" / "streamlit_app.py"

from streamlit.testing.v1 import AppTest  # noqa: E402


def show(at: AppTest, title: str) -> None:
    print(f"\n=== {title} ===")
    print(f"  异常: {len(at.exception)}")
    for e in at.exception:
        print(f"    ❌ {e.value}")
    print(f"  指标卡 {len(at.metric)} 个 | 数据表 {len(at.dataframe)} 个 | "
          f"提示 {len(at.info)} 条 | 报错 {len(at.error)} 条 | 成功 {len(at.success)} 条")


def box_by_label(at: AppTest, label: str):
    """按 label 找 selectbox；找不到就失败，**绝不静默跳过**。

    为什么不用 `at.selectbox("标签")`：这个 Streamlit 版本不支持按 label 取，
    用 try 兜住 KeyError 会让整条交互路径被跳过，而测试照样打印"通过" —— 假绿。
    也不用位置索引 `at.selectbox[2]`：页面上新增一个 selectbox 就会整体错位
    （实测过：加完"第二层按什么拆"之后，索引 2 从"下钻维度"变成了"对比基期"）。
    """
    for sb in at.selectbox:
        if sb.label == label:
            return sb
    raise AssertionError(
        f"找不到 label={label!r} 的 selectbox；现有：{[s.label for s in at.selectbox]}"
    )


def main() -> int:
    at = AppTest.from_file(str(APP), default_timeout=300)
    at.run()
    show(at, "首次运行（默认：最近月 vs 上月，平台维度）")

    failures = []
    if at.exception:
        failures.append("首次运行抛异常")
    if len(at.metric) < 5:
        failures.append(f"指标卡少于 5 个（实际 {len(at.metric)}）")
    if len(at.error) > 0:
        failures.append(f"首次运行出现 {len(at.error)} 条 st.error（不该有）")

    # 页签叫"维度下钻"，就得真有两层 —— 闭合校验那条 success 是名实相符的证据
    if not any("闭合" in (s.value or "") for s in at.success):
        failures.append("未渲染出多层下钻的闭合校验（页签名与实际内容不符）")

    # 交互路径 1：换成渠道维度（验证 selectbox 改变后仍能跑）
    box_by_label(at, "下钻维度").select("channel").run()
    show(at, "切换下钻维度 = channel")
    if at.exception:
        failures.append("切换维度后抛异常")
    if len(at.error) > 0:
        failures.append("切换维度后出现 st.error")

    # 交互路径 2：切到最早的可选月份（验证边界：最早月份也必须有基期可比）
    at = AppTest.from_file(str(APP), default_timeout=300)
    at.run()
    first_month = box_by_label(at, "分析月份").options[0]
    box_by_label(at, "分析月份").select(first_month).run()
    show(at, f"切到最早的可选月份 = {first_month}")
    if at.exception:
        failures.append("切换月份后抛异常")
    if len(at.error) > 0:
        failures.append(f"最早可选月份仍进错误态（{at.error[0].value}）—— "
                        f"应保证所有可选项都有基期")

    print("\n" + "=" * 60)
    if failures:
        print("❌ 未通过：")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ UI 冒烟测试通过（无异常、指标卡齐全、交互路径可用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
