# -*- coding: utf-8 -*-
"""分段回复核心逻辑自测（不依赖 QwenPaw）。

运行：
    python tests/test_core.py
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core  # noqa: E402

PASSED = 0
FAILED = []


def check(name, condition, detail=""):
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name}  ->  {detail}")


def cfg(**kwargs):
    return core.SplitterConfig.from_dict(kwargs)


def main():
    print("\n[1] 默认正则分段")
    text = "今天天气挺好的。你要不要出去走走？我刚查了一下，周末都是晴天！"
    segs = core.split_text(text, cfg())
    print("     分段:", segs)
    check("切成 3 段", len(segs) == 3, segs)
    check("不丢字", core._squeeze("".join(segs)) == core._squeeze(text), segs)

    print("\n[2] 多行文本按行 + 标点切分")
    text2 = "第一段话。还有一句！\n第二段话。\n第三段"
    segs2 = core.split_text(text2, cfg())
    print("     分段:", segs2)
    check("多行切成多段", len(segs2) >= 3, segs2)
    check("多行不丢字", core._squeeze("".join(segs2)) == core._squeeze(text2), segs2)

    print("\n[3] 内容过滤正则")
    segs3 = core.split_text("你好啊。真的吗？太好了！", cfg(filter_regex=r"[。？！]"))
    print("     分段:", segs3)
    check(
        "过滤掉句末标点",
        all(not s.endswith(("。", "？", "！")) for s in segs3),
        segs3,
    )

    print("\n[4] 代码块保护")
    text4 = "先说结论。\n```python\nprint('a。b')\n```\n以上就是代码。"
    segs4 = core.split_text(text4, cfg())
    print("     分段:", segs4)
    joined4 = "\n".join(segs4)
    check("代码块完整保留", "```python" in joined4 and "print('a。b')" in joined4, joined4)
    check("代码块不丢字", core._squeeze(joined4) == core._squeeze(text4), segs4)

    print("\n[5] 短分段合并")
    segs5 = core.split_text("好的。嗯。我知道了！", cfg(min_segment_length=3))
    print("     分段:", segs5)
    check("「嗯。」被合并", all(len(s) >= 3 for s in segs5), segs5)

    print("\n[6] 最大段数上限")
    long_text = "。".join(f"第{i}句" for i in range(30)) + "。"
    segs6 = core.split_text(long_text, cfg(max_segments=5))
    print("     段数:", len(segs6))
    check("段数不超过 5", len(segs6) == 5, len(segs6))
    check("超限不丢字", core._squeeze("".join(segs6)) == core._squeeze(long_text), segs6)

    print("\n[7] 固定长度模式")
    segs7 = core.split_text("abcdefghij" * 3, cfg(split_mode="length", split_length=10))
    check("按长度切分", segs7 == ["abcdefghij"] * 3, segs7)

    print("\n[8] 换行模式")
    segs8 = core.split_text("第一行\n第二行\n第三行", cfg(split_mode="newline"))
    check("按换行切分", segs8 == ["第一行", "第二行", "第三行"], segs8)

    print("\n[9] 间隔时间解析")
    check("解析 1.5,3.5", core.parse_interval("1.5,3.5") == (1.5, 3.5))
    check("解析单个数字", core.parse_interval("2") == (2.0, 2.0))
    check("解析列表", core.parse_interval([1, 4]) == (1.0, 4.0))
    check("乱序自动纠正", core.parse_interval("5,1") == (1.0, 5.0))
    check("非法值兜底", core.parse_interval("abc") == (0.0, 0.0))

    print("\n[10] 延时计算")
    c_random = cfg(interval_method="random", interval="1.5,3.5")
    delays = [core.compute_delay("你好，世界", c_random) for _ in range(200)]
    check(
        "random 落在区间内",
        all(1.5 <= d <= 3.5 for d in delays),
        (min(delays), max(delays)),
    )
    check("random 有随机性", len(set(round(d, 6) for d in delays)) > 50, "没有随机")

    c_log = cfg(interval_method="log", interval="0.5,5", log_base=10)
    d10 = core.compute_delay("x" * 10, c_log)
    d100 = core.compute_delay("x" * 100, c_log)
    d10000 = core.compute_delay("x" * 10000, c_log)
    print(f"     log: 10字={d10:.3f}s 100字={d100:.3f}s 10000字={d10000:.3f}s")
    check("log 公式正确", abs(d100 - math.log(100, 10)) < 1e-9, d100)
    check("log 单调递增", d10 < d100 < d10000, (d10, d100, d10000))
    check("log 被区间夹住", d10000 <= 5.0, d10000)

    c_fixed = cfg(interval_method="fixed", interval="2,9")
    check("fixed 取最小值", core.compute_delay("任意", c_fixed) == 2.0)

    print("\n[11] 发送计划")
    plan = core.plan_reply("第一句。第二句。第三句。", cfg(interval="1,1", interval_method="fixed"))
    print("     计划:", plan)
    check("首段不等待", plan[0]["delay"] == 0.0, plan[0])
    check("后续段有等待", all(p["delay"] == 1.0 for p in plan[1:]), plan)
    check("不可切分时原样返回", len(core.plan_reply("一句话", cfg())) == 1)

    print("\n[12] 配置反序列化与兜底")
    c = core.SplitterConfig.from_dict(
        {
            "enabled": "false",
            "max_length": "800",
            "exclude_channels": "console,qq",
            "split_mode": "不存在的模式",
            "interval_method": "瞎写",
            "log_base": 0,
            "max_segments": -3,
            "未知字段": 1,
        },
    )
    check("字符串转布尔", c.enabled is False, c.enabled)
    check("字符串转整数", c.max_length == 800, c.max_length)
    check("字符串转列表", c.exclude_channels == ["console", "qq"], c.exclude_channels)
    check("非法模式兜底", c.split_mode == "regex", c.split_mode)
    check("非法间隔方法兜底", c.interval_method == "random", c.interval_method)
    check("非法底数兜底", c.log_base == 10.0, c.log_base)
    check("负段数兜底", c.max_segments == 1, c.max_segments)
    check("未知字段不报错", not hasattr(c, "未知字段"))
    check("默认值完整", set(c.to_dict()) == set(core.SplitterConfig.defaults()))

    print("\n[13] 阈值判定（超过阈值不分段）")
    body = "短消息。还有一句！"
    c_thr = cfg(max_length=50)
    check("未超阈值 -> 分段", len(core.split_text(body, c_thr)) > 1, core.split_text(body, c_thr))
    long_body = "这是一条很长的消息。超过阈值之后。就应该整段发送。不再切分。"
    small_thr = cfg(max_length=10)
    check("超阈值 -> 不分段", core.should_split(long_body, small_thr) is False, long_body)
    check(
        "未超阈值 -> 分段",
        core.should_split("短消息。还有一句！", cfg(max_length=50)) is True,
    )
    check("关闭开关 -> 不分段", core.should_split("短消息。还有一句！", cfg(enabled=False)) is False)
    check("空文本 -> 不分段", core.should_split("   ", cfg()) is False)

    print("\n" + "=" * 60)
    print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for name in FAILED:
            print("  失败:", name)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
