# -*- coding: utf-8 -*-
"""QwenPaw 分段回复插件（splitter）。

把智能体一次性吐出的一长段回复，按标点切成若干小句，
像人类聊天一样「小句多条」地逐条发送。

实现原理
--------
QwenPaw 所有通道（QQ / OneBot / 飞书 / 钉钉 / Telegram / ...）的回复最终都会
走 ``BaseChannel.send_message_content()``——各通道只覆写了更底层的
``send_content_parts()`` / ``send()``，唯独这个方法只在基类里定义。
因此在这里做一层包装，就能对所有通道统一生效：

    原:  send_message_content -> 渲染成 parts -> send_content_parts -> 一条长消息
    改:  send_message_content -> 渲染成 parts -> 切分文本 -> 逐段 send_content_parts
                                                   （每段之间 sleep 一个随机间隔）

配置通过 HTTP 接口（/api/splitter/*）读写，Console 设置页可热更新，无需重启。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import core  # noqa: E402  （同目录下的纯逻辑模块）

logger = logging.getLogger("qwenpaw.plugin.splitter")

PLUGIN_ID = "splitter"
VERSION = "1.0.0"

#: 这些 meta / metadata 标记出现时，说明文本不是大模型生成的正文，
#: 「仅对 LLM 结果分段」开启时不参与切分。
NON_LLM_META_FLAGS = ("is_command", "command", "skip_split", "approval", "system")
NON_LLM_SOURCES = {"command", "system", "plugin", "notice", "error", "tool"}
NON_LLM_MESSAGE_TYPES = {
    "tool_guard_approval",
    "system",
    "command",
    "error",
    "notice",
}


class SplitterPlugin:
    """插件主体：加载配置 -> 打补丁 -> 注册配置接口。"""

    def __init__(self) -> None:
        self.api: Any = None
        self.config: core.SplitterConfig = core.SplitterConfig()
        self.config_path: Optional[Path] = None
        self._original_send_message_content = None
        self._patched = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def register(self, api: Any) -> None:
        """插件入口，由 QwenPaw 插件加载器调用。"""
        self.api = api
        self._load_config(getattr(api, "config", None) or {})
        self._patch_channels()
        self._register_routes()
        logger.info(
            "分段回复插件已加载 v%s | 配置=%s | 启用=%s | 阈值=%s字 | 间隔=%s(%s)",
            VERSION,
            self.config_path,
            self.config.enabled,
            self.config.max_length,
            self.config.interval,
            self.config.interval_method,
        )

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def _load_config(self, seed: Dict[str, Any]) -> None:
        """读取配置：默认值 < 主配置 plugins.splitter < 插件目录 config.json。"""
        path = self._resolve_config_path()
        file_data: Dict[str, Any] = {}
        if path.exists():
            try:
                file_data = json.loads(path.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError):
                logger.warning("分段回复：配置文件解析失败，使用默认配置 %s", path)
                file_data = {}

        merged = {**core.SplitterConfig.defaults(), **(seed or {}), **file_data}
        self.config = core.SplitterConfig.from_dict(merged)
        self.config_path = path
        # 始终回写一次：既能给首次运行生成配置文件，也能把缺字段/脏值规范化
        self._save_config()

    def _resolve_config_path(self) -> Path:
        """配置文件放在插件目录下；目录不可写时回落到 ~/.qwenpaw/splitter.json。"""
        candidate = _HERE / "config.json"
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            if not candidate.exists():
                # 探测可写性（创建空文件，随后 _load_config 会写入默认值）
                with candidate.open("a", encoding="utf-8"):
                    pass
            return candidate
        except OSError:
            fallback = Path.home() / ".qwenpaw" / "splitter.json"
            fallback.parent.mkdir(parents=True, exist_ok=True)
            return fallback

    def _save_config(self) -> None:
        if not self.config_path:
            return
        try:
            self.config_path.write_text(
                json.dumps(self.config.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("分段回复：配置保存失败 %s", exc)

    def update_config(self, patch: Dict[str, Any]) -> core.SplitterConfig:
        """热更新配置（Console 设置页保存时调用）。"""
        self.config = self.config.merge(patch or {})
        self._save_config()
        return self.config

    # ------------------------------------------------------------------
    # 打补丁
    # ------------------------------------------------------------------
    def _patch_channels(self) -> None:
        try:
            from qwenpaw.app.channels.base import BaseChannel
        except ImportError as exc:  # pragma: no cover
            logger.error("分段回复：无法导入 BaseChannel，插件未生效 %s", exc)
            return

        if getattr(BaseChannel, "_splitter_patched", False):
            logger.info("分段回复：BaseChannel 已被修补，跳过")
            self._patched = True
            return

        self._original_send_message_content = BaseChannel.send_message_content
        plugin = self

        async def patched_send_message_content(
            self,  # noqa: N805  （就是 channel 实例）
            to_handle: str,
            message: Any,
            meta: Optional[Dict[str, Any]] = None,
        ) -> None:
            await plugin._send_split(self, to_handle, message, meta)

        patched_send_message_content.__name__ = "send_message_content"
        patched_send_message_content.__qualname__ = "BaseChannel.send_message_content"
        BaseChannel.send_message_content = patched_send_message_content
        BaseChannel._splitter_patched = True  # type: ignore[attr-defined]
        BaseChannel._splitter_original = (  # type: ignore[attr-defined]
            self._original_send_message_content
        )
        self._patched = True

    async def _send_split(
        self,
        channel: Any,
        to_handle: str,
        message: Any,
        meta: Optional[Dict[str, Any]],
    ) -> None:
        """分段发送，任何异常都退回原始整段发送。"""
        original = self._original_send_message_content
        cfg = self.config
        try:
            parts = list(channel._message_to_content_parts(message) or [])
        except Exception:  # pylint: disable=broad-except
            logger.debug("分段回复：渲染 parts 失败，走原逻辑", exc_info=True)
            parts = []

        try:
            if not parts or not cfg.enabled:
                return await original(channel, to_handle, message, meta)

            if not self._channel_allowed(channel, cfg):
                return await original(channel, to_handle, message, meta)

            if cfg.only_llm_result and self._is_non_llm(message, meta):
                return await original(channel, to_handle, message, meta)

            texts, others = self._split_parts(parts)
            # 带图片/文件/工具卡片等混合内容时不切分，避免打乱顺序
            if others or not texts:
                return await original(channel, to_handle, message, meta)

            body = "\n".join(texts).strip()
            if not core.should_split(body, cfg):
                # 关闭开关 / 空文本 / 超过字数阈值：整段直发
                return await original(channel, to_handle, message, meta)

            segments = core.split_text(body, cfg)
            if len(segments) <= 1:
                return await original(channel, to_handle, message, meta)

            logger.debug(
                "分段回复: channel=%s 原长度=%s 切成 %s 段",
                getattr(channel, "channel", "?"),
                len(body),
                len(segments),
            )
            await self._emit(channel, to_handle, meta, segments, cfg)
            return None
        except Exception:  # pylint: disable=broad-except
            logger.exception("分段回复：切分失败，回退为整段发送")
            return await original(channel, to_handle, message, meta)

    async def _emit(
        self,
        channel: Any,
        to_handle: str,
        meta: Optional[Dict[str, Any]],
        segments: List[str],
        cfg: core.SplitterConfig,
    ) -> None:
        """逐段发送，段间按配置的间隔等待。"""
        from qwenpaw.schemas import ContentType, TextContent

        base_meta = dict(meta or {})
        prefix = (base_meta.get("bot_prefix") or "").strip()

        for index, segment in enumerate(segments):
            if index > 0:
                delay = core.compute_delay(segments[index - 1], cfg)
                if delay > 0:
                    await asyncio.sleep(delay)

            part_meta = dict(base_meta)
            text = segment
            if prefix:
                # 机器人前缀只加在第一段，后续分段不再重复
                part_meta.pop("bot_prefix", None)
                if index == 0:
                    text = f"{prefix}  {segment}"

            await channel.send_content_parts(
                to_handle,
                [TextContent(type=ContentType.TEXT, text=text)],
                part_meta,
            )

    # ------------------------------------------------------------------
    # 判定辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _split_parts(parts: List[Any]) -> tuple[List[str], List[Any]]:
        """把渲染后的内容块分成「纯文本」和「其他（图片/文件/卡片等）」两类。"""
        from qwenpaw.schemas import ContentType

        texts: List[str] = []
        others: List[Any] = []
        for part in parts:
            ptype = getattr(part, "type", None)
            if ptype == ContentType.TEXT and getattr(part, "text", None):
                texts.append(part.text)
            elif ptype == ContentType.REFUSAL and getattr(part, "refusal", None):
                texts.append(part.refusal)
            else:
                others.append(part)
        return texts, others

    @staticmethod
    def _channel_allowed(channel: Any, cfg: core.SplitterConfig) -> bool:
        name = (
            getattr(channel, "channel", None)
            or getattr(channel, "channel_name", None)
            or ""
        )
        if not name:
            return True
        return str(name) not in {str(c) for c in cfg.exclude_channels}

    @classmethod
    def _is_non_llm(cls, message: Any, meta: Optional[Dict[str, Any]]) -> bool:
        """判断这条文本是否不是大模型正文（命令输出 / 系统提示 / 审批卡片等）。"""
        meta = meta or {}
        for flag in NON_LLM_META_FLAGS:
            if meta.get(flag):
                return True
        source = str(meta.get("source") or "").lower()
        if source in NON_LLM_SOURCES:
            return True

        metadata = getattr(message, "metadata", None)
        if isinstance(metadata, dict):
            mtype = str(
                metadata.get("message_type") or metadata.get("type") or "",
            ).lower()
            if mtype in NON_LLM_MESSAGE_TYPES:
                return True
            msource = str(metadata.get("source") or "").lower()
            if msource in NON_LLM_SOURCES:
                return True
        return False

    # ------------------------------------------------------------------
    # HTTP 接口（Console 设置页使用）
    # ------------------------------------------------------------------
    def _register_routes(self) -> None:
        try:
            from fastapi import APIRouter, Body, HTTPException
        except ImportError:  # pragma: no cover
            logger.warning("分段回复：FastAPI 不可用，跳过配置接口注册")
            return

        router = APIRouter()
        plugin = self

        @router.get("/config")
        async def get_config() -> Dict[str, Any]:
            return {
                "ok": True,
                "config": plugin.config.to_dict(),
                "path": str(plugin.config_path or ""),
                "version": VERSION,
                "patched": plugin._patched,
            }

        @router.put("/config")
        async def put_config(patch: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
            if not isinstance(patch, dict):
                raise HTTPException(status_code=400, detail="配置必须是 JSON 对象")
            unknown = [
                key
                for key in patch
                if key not in core.SplitterConfig.defaults()
            ]
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"未知配置项: {', '.join(unknown)}",
                )
            config = plugin.update_config(patch)
            return {"ok": True, "config": config.to_dict()}

        @router.post("/preview")
        async def preview(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
            """预览切分效果：给定文本（可选覆盖配置）返回每段内容和间隔。"""
            text = str(payload.get("text") or "")
            override = payload.get("config") or {}
            if not isinstance(override, dict):
                override = {}
            cfg = (
                plugin.config.merge(override) if override else plugin.config
            )
            return {
                "ok": True,
                "original_length": len(text),
                "segments": core.plan_reply(text, cfg),
            }

        @router.get("/status")
        async def status() -> Dict[str, Any]:
            return {
                "ok": True,
                "id": PLUGIN_ID,
                "version": VERSION,
                "enabled": plugin.config.enabled,
                "patched": plugin._patched,
                "config_path": str(plugin.config_path or ""),
            }

        try:
            self.api.register_http_router(
                router,
                prefix="/splitter",
                tags=["splitter"],
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("分段回复：注册 HTTP 接口失败 %s", exc)


# 模块级实例：插件加载器要求导出 `plugin`
plugin = SplitterPlugin()
