# 分段回复（splitter）· QwenPaw 插件

让智能体像人一样聊天：把一整段长回复按标点切成多条小句，随机间隔逐条发送。

> 痛点：人类发消息是「小句多条」，智能体总是一股脑甩一长段。本插件解决的就是这个。

---

## 一、它做了什么

改造前：

```
机器人：今天天气挺好的。你要不要出去走走？我刚查了一下周末都是晴天！对了记得带伞万一下雨呢~
```

改造后（4 条消息，间隔 1.5~3.5 秒）：

```
机器人：今天天气挺好的。
（等 2.1s）
机器人：你要不要出去走走？
（等 1.8s）
机器人：我刚查了一下周末都是晴天！
（等 2.6s）
机器人：对了记得带伞万一下雨呢~
```

## 二、实现原理

QwenPaw 所有通道（QQ / OneBot / 飞书 / 钉钉 / Telegram / Discord / ...）的回复，最终都会汇聚到
`BaseChannel.send_message_content()` —— 各通道只覆写了更底层的 `send_content_parts()` / `send()`，
**只有这个方法定义在基类里**。所以在这一层做包装，就能对所有通道统一生效：

```
原本：send_message_content → 渲染成 parts → send_content_parts → 一整条长消息
改后：send_message_content → 渲染成 parts → 切分文本 → 逐段 send_content_parts（段间 sleep）
```

不改动 QwenPaw 任何源码，插件卸载后自动恢复。

## 三、安装

### 方式一：复制目录（推荐）

```bash
# 宿主机上
docker cp ./splitter qwenpaw:/root/.qwenpaw/plugins/splitter
docker restart qwenpaw
```

> 容器内插件目录为 `~/.qwenpaw/plugins/`，即 `/root/.qwenpaw/plugins/`（以容器实际用户为准）。

### 方式二：CLI

```bash
qwenpaw plugin install /path/to/splitter
qwenpaw plugin install /path/to/splitter --force   # 覆盖安装
```

### 方式三：ZIP / URL

```bash
cd /path/to && zip -r splitter-1.0.0.zip splitter/
qwenpaw plugin install ./splitter-1.0.0.zip
```

验证：

```bash
qwenpaw plugin list          # 应能看到 splitter
curl http://127.0.0.1:8088/api/splitter/status
```

## 四、配置项

Console → 设置 → 分段回复，或直接编辑插件目录下的 `config.json`（改完重启生效），
也可以通过 HTTP 接口热更新（见下一节）。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关 |
| `only_llm_result` | `true` | 仅对 LLM 结果分段。命令输出、系统提示、审批卡片等非大模型文本整段发送 |
| `interval_method` | `random` | `random` 随机 / `log` 按字数取对数 / `fixed` 固定 |
| `interval` | `1.5,3.5` | 间隔秒数，格式 `最小,最大` |
| `log_base` | `10` | `interval_method=log` 时的底数：`y = log_base(x)`，x 为字符数，y 为秒 |
| `max_length` | `1000` | 字数阈值：**不超过**该值才分段，超过则整段直发 |
| `split_mode` | `regex` | `regex` 正则 / `length` 固定长度 / `newline` 换行 |
| `split_regex` | `.+?[。？！~…]+\|.+$` | 分段正则，插件在「匹配结束位置」落刀，不会吞字 |
| `split_length` | `100` | `split_mode=length` 时每段字数 |
| `filter_regex` | 空 | 内容过滤正则，从每个分段中移除命中内容，如 `[。？！]` 去掉句末标点 |
| `fallback_to_sentence` | `true` | 自定义正则没切出多段时，自动退化为内置分句规则 |
| `min_segment_length` | `2` | 短于该值的小段并入上一段，避免「嗯。」碎片 |
| `max_segments` | `20` | 单条回复最多段数，超出后尾部合并，防刷屏 |
| `protect_code_blocks` | `true` | 不在 ` ``` ` 代码块内部切分 |
| `exclude_channels` | `["console"]` | 不参与分段的通道，默认排除网页控制台 |

**关于分段正则**：务必使用惰性量词 `.+?`。若写成 `.?[。？！~…]+|.+$`，
alternation 里的 `.+$` 会在位置 0 就吞掉整行，导致永远只切出一段
（插件内置了兜底逻辑，遇到这种情况会自动退化为内置分句规则）。

## 五、HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/splitter/config` | 读取当前配置 |
| PUT | `/api/splitter/config` | 热更新配置，body 为部分配置，**立即生效无需重启** |
| POST | `/api/splitter/preview` | 预览切分效果，body `{"text": "...", "config": {...}}` |
| GET | `/api/splitter/status` | 插件状态（是否已打补丁、配置路径） |

示例：

```bash
# 关闭分段
curl -X PUT http://127.0.0.1:8088/api/splitter/config \
  -H 'Content-Type: application/json' -d '{"enabled": false}'

# 预览
curl -X POST http://127.0.0.1:8088/api/splitter/preview \
  -H 'Content-Type: application/json' \
  -d '{"text": "今天天气挺好的。你要不要出去走走？我觉得可以！"}'
```

## 六、注意事项

1. **流式输出**：通道开启「流式输出」时，回复由 `on_streaming_*` 逐字推送，
   不会走 `send_message_content`，因此不分段。默认所有通道流式是关闭的；
   如需分段，请在通道设置里关闭流式输出。
2. **Console 网页端**：默认已排除（`exclude_channels: ["console"]`），
   网页端自带渲染，不需要分段。想让网页端也分段，把 `console` 从列表里删掉。
3. **混合内容不切分**：回复里同时含图片/文件/工具卡片时，为避免打乱顺序，整段直发。
4. **阻塞说明**：分段等待发生在回复周期内，同一会话的后续消息会正常排队，不会串台。
5. **机器人前缀**：`bot_prefix` 只加在第一段，后续分段不会重复添加。

## 七、自测

核心逻辑是纯 Python，不依赖 QwenPaw，可直接跑：

```bash
python tests/test_core.py
```

覆盖：正则分段、多行、内容过滤、代码块保护、碎片合并、段数上限、
间隔解析与计算、配置反序列化兜底、阈值判定，共 40 项。

## 八、文件结构

```
splitter/
├── plugin.json      # 插件清单（id / 入口 / 版本约束）
├── plugin.py        # 后端：拦截 send_message_content + 配置接口
├── core.py          # 核心切分逻辑（纯 Python，可单测）
├── config.json      # 运行后生成的配置文件
├── dist/index.js    # Console 设置页（免构建 ES Module）
├── tests/test_core.py
└── README.md
```

卸载：

```bash
qwenpaw plugin uninstall splitter
# 或：docker exec qwenpaw rm -rf /root/.qwenpaw/plugins/splitter && docker restart qwenpaw
```

## 九、版本历史

| 版本 | 说明 |
| --- | --- |
| 1.0.2 | 设置页菜单项增加图标 ✂️（扩展设置列表中可见），功能无变化 |
| 1.0.1 | 修复设置页「改了参数不生效 / 刷新后回到默认值」，以及保存时假提示「已保存」。原因：宿主 `QP.host.fetch` 内部会走 `getApiUrl()`（等价于再补一个 `/api`），前端传 `/api/splitter/config` 被拼成 `/api/api/splitter/config` → GET 404（页面只能显示默认值）、PUT 405（写入失败却被当成成功）；旧版 `request()` 不检查状态码。现按宿主规则自动选择正确写法（404/405 自动换写法重试），并真正校验响应，失败时给出明确原因 |
| 1.0.0 | 首个版本 |

> **给插件开发者的提醒（踩过的坑）**：Console 里请这样调接口——
> `window.QwenPaw.host.fetch("/splitter/config", {...})`，**不要再带 `/api` 前缀**，
> 它会自己拼成 `/api/splitter/config`。官方 `docs/plugins.zh.md` 里
> `host.fetch("/api/v1/my-endpoint")` 的例子按当前实现会被拼成 `/api/api/v1/...`，请留意。
