# QwenPaw 分段回复插件（splitter）

让 QwenPaw 的智能体像人一样聊天：把一整段长回复按标点切成多条小句，随机间隔逐条发送。

> 痛点：人类发消息是「小句多条」，智能体总是一股脑甩一长段。

## 效果

改造前（1 条）：

```
今天天气挺好的。你要不要出去走走？我刚查了一下周末都是晴天！对了记得带伞万一下雨呢~
```

改造后（4 条，间隔 1.5~3.5 秒）：

```
今天天气挺好的。
你要不要出去走走？
我刚查了一下周末都是晴天！
对了记得带伞万一下雨呢~
```

## 安装

**方式一：WebUI 上传 ZIP**

Console → 插件 → 安装插件，拖入 [Releases](../../releases) 里的 `splitter-x.y.z.zip`。

**方式二：命令行**

```bash
git clone https://github.com/yuukisnow1208/qwenpaw-plugin-splitter.git
qwenpaw plugin install ./qwenpaw-plugin-splitter/splitter
```

安装后在 **Console → 设置 → 分段回复** 里打开开关即可（配置热更新，无需重启）。

## 功能

| 能力 | 说明 |
| --- | --- |
| 分句发送 | 按句末标点（。？！~…）切成多条，逐条发送 |
| 间隔策略 | `random` 随机 / `log` 按字数取对数 / `fixed` 固定 |
| 仅对 LLM 分段 | 命令输出、系统提示、审批卡片等非大模型文本整段发送 |
| 字数阈值 | 超过阈值的长消息整段直发，避免刷屏 |
| 分段模式 | 正则表达式 / 固定长度 / 按换行 |
| 内容过滤 | 按正则从每段中移除指定内容 |
| 代码块保护 | 不在 ` ``` ` 内部切分 |
| 碎片合并 | 「嗯。」这类小段自动并入上一段 |
| 切分预览 | 设置页内置预览，先看效果再保存 |

## 实现原理

QwenPaw 所有通道（QQ / OneBot / 飞书 / 钉钉 / Telegram / ...）的回复最终都汇聚到
`BaseChannel.send_message_content()`——各通道只覆写了更底层的 `send_content_parts()` / `send()`，
**只有这个方法定义在基类里**。因此包装这一个方法就能对所有通道统一生效：

```
原本：send_message_content → 渲染成 parts → send_content_parts → 一整条长消息
改后：send_message_content → 渲染成 parts → 切分文本 → 逐段 send_content_parts（段间 sleep）
```

不改动 QwenPaw 任何源码，卸载后自动恢复。

## 注意事项

- 通道开启「流式输出」时回复不经过该链路，不会分段（默认各通道流式均为关闭）
- 网页控制台（console）默认排除在分段之外
- 回复中同时含图片/文件/工具卡片时整段直发，避免打乱顺序
- 属 monkey-patch 类型插件，安装或卸载后建议重启一次 QwenPaw

## 开发

```bash
python splitter/tests/test_core.py        # 核心逻辑单测（纯 Python，无需 QwenPaw）
python build_plugin_zip.py                # 打 WebUI 可上传的 ZIP
```

容器内端到端验证：`splitter/tests/test_in_container.py`（在 QwenPaw 容器里运行）。

更多配置项与接口说明见 [splitter/README.md](splitter/README.md)。

## 兼容性

QwenPaw `>= 2.2.0`（在 2.2.2.post1 上验证）。
