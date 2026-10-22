# -*- coding: utf-8 -*-
"""容器内端到端桩测试：验证插件确实拦截了 BaseChannel.send_message_content。

在 QwenPaw 容器里运行：
    docker exec qwenpaw bash -c "cd /tmp && python /tmp/splitter/tests/test_in_container.py"

用一个假的通道子类（只实现 send）驱动真实的打补丁流程。
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

PLUGIN_DIR = Path("/app/working/plugins/splitter")

PASSED = 0
FAILED = []


def check(name, condition, detail=""):
    global PASSED
    if condition:
        PASSED += 1
        print(f"  OK   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name} -> {detail}")


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_splitter_test",
        PLUGIN_DIR / "plugin.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_splitter_test"] = module
    spec.loader.exec_module(module)
    return module


class FakeApi:
    def __init__(self):
        self.plugin_id = "splitter"
        self.config = {}
        self.manifest = {}
        self.routers = []

    def register_http_router(self, router, *, prefix="", tags=None):
        self.routers.append((prefix, tags))


def main():
    from qwenpaw.app.channels.base import BaseChannel
    from qwenpaw.schemas import ContentType, TextContent

    module = load_plugin()
    core = module.core
    plugin = module.plugin

    print("\n[0] 注册插件")
    api = FakeApi()
    plugin.register(api)
    check("HTTP 路由已注册", any(p == "/splitter" for p, _ in api.routers), api.routers)
    check(
        "BaseChannel.send_message_content 已被替换",
        BaseChannel.send_message_content.__name__ == "send_message_content"
        and getattr(BaseChannel, "_splitter_patched", False),
        BaseChannel.send_message_content,
    )

    class DummyChannel(BaseChannel):
        """只实现发送端的假通道，用来观察插件实际发出了几条消息。"""

        def __init__(self, name="onebot"):
            self.channel = name
            self.sent = []
            self.stamps = []
            self._message = ""

        def _message_to_content_parts(self, message):
            parts = []
            if isinstance(message, str):
                parts.append(TextContent(type=ContentType.TEXT, text=message))
            else:
                parts.extend(message)
            return parts

        async def send(self, to_handle, text, meta=None):
            self.sent.append(text)
            self.stamps.append(time.monotonic())

        async def start(self):
            pass

        async def stop(self):
            pass

    TEXT = "今天天气挺好的。你要不要出去走走？我刚查了一下，周末都是晴天！"

    async def run():
        results = {}

        print("\n[1] 正常分段")
        plugin.config = core.SplitterConfig.from_dict(
            {"interval": "0.25,0.35", "exclude_channels": ["console"]},
        )
        ch = DummyChannel()
        await ch.send_message_content("u1", TEXT, {})
        results["normal"] = ch

        print("\n[2] 关闭开关 -> 整段直发")
        plugin.config = core.SplitterConfig.from_dict(
            {"enabled": False, "interval": "0.25,0.35"},
        )
        ch_off = DummyChannel()
        await ch_off.send_message_content("u1", TEXT, {})

        print("\n[3] 超过字数阈值 -> 整段直发")
        plugin.config = core.SplitterConfig.from_dict(
            {"max_length": 5, "interval": "0.25,0.35"},
        )
        ch_long = DummyChannel()
        await ch_long.send_message_content("u1", TEXT, {})

        print("\n[4] 排除的通道 -> 整段直发")
        plugin.config = core.SplitterConfig.from_dict(
            {"interval": "0.25,0.35", "exclude_channels": ["console"]},
        )
        ch_console = DummyChannel(name="console")
        await ch_console.send_message_content("u1", TEXT, {})

        print("\n[5] 非 LLM 文本（命令输出）-> 整段直发")
        plugin.config = core.SplitterConfig.from_dict(
            {"interval": "0.25,0.35", "only_llm_result": True},
        )
        ch_cmd = DummyChannel()
        await ch_cmd.send_message_content("u1", TEXT, {"is_command": True})

        print("\n[6] 混合图文内容 -> 整段直发")
        plugin.config = core.SplitterConfig.from_dict({"interval": "0.25,0.35"})
        from qwenpaw.schemas import ImageContent

        ch_mixed = DummyChannel()
        await ch_mixed.send_message_content(
            "u1",
            [
                TextContent(type=ContentType.TEXT, text=TEXT),
                ImageContent(type=ContentType.IMAGE, image_url="http://x/y.png"),
            ],
            {},
        )

        return ch, ch_off, ch_long, ch_console, ch_cmd, ch_mixed

    ch, ch_off, ch_long, ch_console, ch_cmd, ch_mixed = asyncio.run(run())

    print()
    print("  实际发出的消息:")
    for index, item in enumerate(ch.sent):
        print(f"    [{index}] {item}")

    check("切成 3 条消息", len(ch.sent) == 3, ch.sent)
    check(
        "内容完整不丢字",
        core._squeeze("".join(ch.sent)) == core._squeeze(TEXT),
        ch.sent,
    )
    if len(ch.stamps) >= 3:
        gaps = [
            round(ch.stamps[i + 1] - ch.stamps[i], 3) for i in range(len(ch.stamps) - 1)
        ]
        print(f"  实际间隔: {gaps} 秒")
        check("段间有等待", all(g >= 0.2 for g in gaps), gaps)
        check("等待不超过配置上限", all(g <= 0.6 for g in gaps), gaps)
    else:
        check("段间有等待", False, ch.stamps)

    check("关闭开关 -> 1 条", len(ch_off.sent) == 1, ch_off.sent)
    check("超阈值 -> 1 条", len(ch_long.sent) == 1, ch_long.sent)
    check("排除通道 -> 1 条", len(ch_console.sent) == 1, ch_console.sent)
    check("命令输出 -> 1 条", len(ch_cmd.sent) == 1, ch_cmd.sent)
    check("图文混合 -> 1 条", len(ch_mixed.sent) == 1, ch_mixed.sent)

    print("\n" + "=" * 60)
    print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for name in FAILED:
            print("  失败:", name)
        return 1
    print("容器内验证全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
