# -*- coding: utf-8 -*-
"""分段回复核心逻辑。

纯 Python 实现，不依赖 QwenPaw，方便单独跑单元测试。
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 默认分段正则：匹配以句号/问号/感叹号/波浪号/省略号结尾的小句，或行尾残留文本。
# 注意必须是惰性的 `.+?`：若写成 `.?`，alternation 里的 `.+$` 会在位置 0 就吞掉整行，
# 导致永远只切出一段。插件按「匹配结束位置」落刀，所以写成只匹配分隔符
# （如 `.?[。？！~…]+`）同样有效，切出来的边界是一样的。
DEFAULT_SPLIT_REGEX = r".+?[。？！~…]+|.+$"

#: 兜底分句正则：自定义正则没切出多段时使用，保证插件「永远能分段」。
FALLBACK_SPLIT_REGEX = r".+?[。？！~…]+|.+$"

# 围栏代码块（```...``` / ~~~...~~~），分段时需要整体保护，避免被切坏。
FENCE_RE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")

#: 分段模式
SPLIT_MODES = ("regex", "length", "newline")

#: 间隔方法
INTERVAL_METHODS = ("random", "log", "fixed")


@dataclass
class SplitterConfig:
    """分段回复配置。字段与 Console 设置页一一对应。"""

    # ---- 开关 ----
    enabled: bool = True
    """总开关：是否启用分段回复。"""

    only_llm_result: bool = True
    """仅对 LLM 结果分段：命令/系统/审批/工具结果等非大模型文本不切分。"""

    # ---- 间隔 ----
    interval_method: str = "random"
    """间隔方法：random=随机；log=按字数取对数 y=log_base(x)；fixed=固定秒数。"""

    interval: str = "1.5,3.5"
    """间隔时间（秒）。格式为 "最小,最大"，也接受单个数字。"""

    log_base: float = 10.0
    """对数间隔的底数，仅 interval_method=log 时生效。y = log_base(x)，x 为字符数。"""

    # ---- 分段 ----
    max_length: int = 1000
    """分段回复字数阈值：只有字数不超过该值的消息才会被分段，超过则整段直发。"""

    split_mode: str = "regex"
    """分段模式：regex=正则表达式；length=固定长度；newline=按换行。"""

    split_regex: str = DEFAULT_SPLIT_REGEX
    """分段正则表达式（split_mode=regex）。推荐匹配分隔符的写法。"""

    fallback_to_sentence: bool = True
    """自定义正则没切出多段时，自动退化为内置的分句规则，保证「永远能分段」。"""

    split_length: int = 100
    """固定长度分段的每段字数（split_mode=length）。"""

    filter_regex: str = ""
    """内容过滤正则：把命中的内容从每个分段中移除，例如 `[。？！]`。"""

    # ---- 质量与保护 ----
    min_segment_length: int = 2
    """短分段合并阈值：小于该长度的分段会合并进上一段，避免出现 "嗯。" 这种碎片。"""

    max_segments: int = 20
    """单条回复最多切成多少段，超出后尾部合并为一段，防止刷屏。"""

    protect_code_blocks: bool = True
    """保护围栏代码块，不在 ```...``` 内部切分。"""

    exclude_channels: List[str] = field(default_factory=lambda: ["console"])
    """不参与分段的通道名列表（默认排除 Console 网页端）。"""

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    @classmethod
    def defaults(cls) -> Dict[str, Any]:
        return {f.name: _copy_default(f) for f in fields(cls)}

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "SplitterConfig":
        cfg = cls()
        if not isinstance(data, dict):
            return cfg
        valid = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key not in valid or value is None:
                continue
            setattr(cfg, key, _coerce(key, value, getattr(cls(), key)))
        return _sanitize(cfg)

    def to_dict(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def merge(self, patch: Dict[str, Any]) -> "SplitterConfig":
        return SplitterConfig.from_dict({**self.to_dict(), **(patch or {})})


def _copy_default(f):
    value = f.default
    if isinstance(value, list):
        return list(value)
    return value


def _coerce(key: str, value: Any, default: Any) -> Any:
    """把外部输入（JSON / 表单字符串）转成配置字段应有的类型。"""
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on", "是"}
            return bool(value)
        if isinstance(default, int) and not isinstance(default, bool):
            return int(float(value))
        if isinstance(default, float):
            return float(value)
        if isinstance(default, list):
            if isinstance(value, str):
                return [v.strip() for v in value.split(",") if v.strip()]
            return [str(v) for v in value]
        return str(value)
    except (TypeError, ValueError):
        return default


def _sanitize(cfg: SplitterConfig) -> SplitterConfig:
    """兜底修正非法取值，保证插件不会因为配置写错而炸掉。"""
    if cfg.split_mode not in SPLIT_MODES:
        cfg.split_mode = "regex"
    if cfg.interval_method not in INTERVAL_METHODS:
        cfg.interval_method = "random"
    if cfg.log_base <= 1:
        cfg.log_base = 10.0
    cfg.max_length = max(0, cfg.max_length)
    cfg.min_segment_length = max(0, cfg.min_segment_length)
    cfg.max_segments = max(1, cfg.max_segments)
    cfg.split_length = max(1, cfg.split_length)
    lo, hi = parse_interval(cfg.interval)
    cfg.interval = f"{lo},{hi}"
    return cfg


# ----------------------------------------------------------------------
# 间隔时间
# ----------------------------------------------------------------------
def parse_interval(value: Any) -> Tuple[float, float]:
    """解析 "1.5,3.5" / [1.5, 3.5] / 2 形式的间隔配置，返回 (最小, 最大)。"""
    lo = hi = 0.0
    try:
        if isinstance(value, (list, tuple)):
            nums = [float(v) for v in value]
        elif isinstance(value, str):
            nums = [float(v.strip()) for v in value.split(",") if v.strip()]
        else:
            nums = [float(value)]
    except (TypeError, ValueError):
        nums = [0.0]

    if not nums:
        return 0.0, 0.0
    lo = max(0.0, nums[0])
    hi = max(0.0, nums[1]) if len(nums) > 1 else lo
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def compute_delay(segment: str, cfg: SplitterConfig) -> float:
    """计算发送完 *segment* 后，需要等待多少秒再发下一段。"""
    lo, hi = parse_interval(cfg.interval)

    if cfg.interval_method == "fixed":
        return lo

    if cfg.interval_method == "log":
        # y = log_base(x)，x 为字符数，y 单位为秒，最后夹到 [lo, hi]
        x = max(1, len(segment or ""))
        try:
            y = math.log(x, cfg.log_base)
        except (ValueError, ZeroDivisionError):
            y = lo
        return _clamp(y, lo, hi)

    # random
    if hi <= lo:
        return lo
    return random.uniform(lo, hi)


def _clamp(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return lo
    return max(lo, min(hi, value))


# ----------------------------------------------------------------------
# 文本切分
# ----------------------------------------------------------------------
def split_text(text: str, cfg: SplitterConfig) -> List[str]:
    """把 *text* 切成若干小段。

    返回的分段已做过：代码块保护 -> 模式切分 -> 内容过滤 -> 去空白 ->
    碎片合并 -> 段数上限。
    """
    if not text or not text.strip():
        return []

    if cfg.protect_code_blocks and FENCE_RE.search(text):
        raw = _split_with_fences(text, cfg)
    else:
        raw = _split_plain(text, cfg)

    result = _post_process(raw, cfg)

    # 兜底：自定义正则没切出多段（例如写成了 .?[...] 被 .+$ 吞掉整行），
    # 而文本里明显有多个句末标点时，退化为内置分句规则。
    if cfg.fallback_to_sentence and len(result) <= 1 and _looks_splittable(text):
        fallback = _split_with_fences(text, cfg, FALLBACK_SPLIT_REGEX)
        retried = _post_process(fallback, cfg)
        if len(retried) > 1:
            result = retried

    return result


def should_split(text: str, cfg: SplitterConfig) -> bool:
    """这段文本值不值得分段（只看开关和字数阈值）。

    超过 ``max_length`` 的长消息整段直发，避免刷屏。
    """
    if not cfg.enabled:
        return False
    if not text or not text.strip():
        return False
    return len(text) <= cfg.max_length


def _looks_splittable(text: str) -> bool:
    """文本里是否至少有两个句末标点（值得分段）。"""
    return sum(text.count(ch) for ch in "。？！~…") >= 2


def _split_plain(
    text: str,
    cfg: SplitterConfig,
    pattern: Optional[str] = None,
) -> List[str]:
    """按配置模式切分一段普通文本（不含围栏代码块）。"""
    if cfg.split_mode == "length":
        size = max(1, cfg.split_length)
        return [text[i : i + size] for i in range(0, len(text), size)]

    if cfg.split_mode == "newline":
        return text.split("\n")

    pattern = pattern or cfg.split_regex or DEFAULT_SPLIT_REGEX
    try:
        compiled = re.compile(pattern)
    except re.error:
        return [text]

    # 先按行切：换行本身就是人类发消息的天然断点，行内再用正则细分。
    #
    # 正则匹配到的是「分段点」（推荐写法是匹配分隔符，如 .?[。？！~…]+|.+$），
    # 因此在每个匹配的结束位置落刀。这样无论用户写的正则匹配分隔符还是整句，
    # 都只是改变切分位置，永远不会吞掉文字。
    segments: List[str] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        cursor = 0
        for match in compiled.finditer(line):
            end = match.end()
            if end <= cursor:
                continue
            segments.append(line[cursor:end])
            cursor = end
        if cursor < len(line):
            segments.append(line[cursor:])
        if not segments:
            segments.append(line)
    return segments or [text]


def _squeeze(text: str) -> str:
    """去掉所有空白字符，用于校验切分前后是否丢字。"""
    return re.sub(r"\s+", "", text or "")


def _split_with_fences(
    text: str,
    cfg: SplitterConfig,
    pattern: Optional[str] = None,
) -> List[str]:
    """分段时保护 ```...``` / ~~~...~~~ 代码块，代码块整体挂到上一分段末尾。"""
    out: List[str] = []
    cursor = 0
    for match in FENCE_RE.finditer(text):
        head = text[cursor : match.start()]
        out.extend(_split_plain(head, cfg, pattern))
        block = match.group(0)
        if out:
            joiner = "" if out[-1].endswith("\n") else "\n"
            out[-1] = f"{out[-1]}{joiner}{block}"
        else:
            out.append(block)
        cursor = match.end()
    out.extend(_split_plain(text[cursor:], cfg, pattern))
    return out


def _post_process(segments: Sequence[str], cfg: SplitterConfig) -> List[str]:
    """过滤、去空白、合并碎片、限制段数。"""
    result: List[str] = []

    for raw in segments:
        segment = raw.strip()
        if not segment:
            continue
        if cfg.filter_regex:
            try:
                segment = re.sub(cfg.filter_regex, "", segment)
            except re.error:
                # 过滤正则写错时不影响主流程，原样发送
                pass
            segment = segment.strip()
            if not segment:
                continue
        if result and len(segment) < cfg.min_segment_length:
            # "嗯。" / "？" 这类碎片并入上一段
            result[-1] = f"{result[-1]}{segment}"
            continue
        result.append(segment)

    if len(result) > cfg.max_segments:
        head = result[: cfg.max_segments - 1]
        tail = "\n".join(result[cfg.max_segments - 1 :])
        result = head + [tail]

    return result


def plan_reply(text: str, cfg: SplitterConfig) -> List[Dict[str, Any]]:
    """给出完整的发送计划：每段文本 + 发送前需要等待的秒数。

    首段等待 0 秒，之后每段等待的秒数由「上一段」的长度决定。
    用于设置页预览和自测。
    """
    segments = split_text(text, cfg)
    if len(segments) <= 1:
        return [{"text": text, "delay": 0.0}] if text.strip() else []

    plan = [{"text": segments[0], "delay": 0.0}]
    for index in range(1, len(segments)):
        plan.append(
            {
                "text": segments[index],
                "delay": compute_delay(segments[index - 1], cfg),
            },
        )
    return plan
