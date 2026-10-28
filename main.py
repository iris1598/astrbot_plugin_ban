"""astrbot_plugin_ban - 消息封禁插件

将被封禁的账号（或群聊）发送的所有消息在流水线最前段直接拦截：
不回复、不处理、不触发指令、不进入 LLM。

支持所有平台适配器，包括 QQ 个人号 (aiocqhttp) 与 QQ 官方机器人
(qq_official / qq_official_webhook)。注意：QQ 官方机器人场景下，用户与
群聊的 ID 均为 openid（每个机器人不同），推荐直接在群内使用
"/ban add @某人" 封禁，或从 AstrBot 日志中复制 openid。

指令（仅管理员）：
    /ban add <用户ID | @某人>    封禁账号
    /ban del <用户ID | @某人>    解封账号
    /ban list                    查看封禁名单
    /ban clear                   清空动态封禁名单
    /ban gadd <群号>             封禁整个群聊
    /ban gdel <群号>             解封群聊

持久化：动态封禁名单保存在 data/plugin_data/astrbot_plugin_ban/ban_list.json，
不会因插件更新/重装而丢失。静态名单由 WebUI 插件配置管理。
"""

import json
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class BanPlugin(Star):
    """完全忽略封禁账号所有消息的插件。"""

    # 指令组名，用于判断管理员被误封时放行其管理指令
    _MANAGE_PREFIXES = ("ban", "封禁")

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 动态封禁集合（由指令管理，持久化到 JSON 文件）
        self.banned_users: set[str] = set()
        self.banned_groups: set[str] = set()

        self._data_dir = (
            Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_ban"
        )
        self._data_file = self._data_dir / "ban_list.json"
        self._load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        """启动时加载数据：动态名单(JSON) + 静态名单(WebUI 配置)。"""
        try:
            if self._data_file.exists():
                data = json.loads(self._data_file.read_text(encoding="utf-8"))
                self.banned_users = {str(u) for u in data.get("users", [])}
                self.banned_groups = {str(g) for g in data.get("groups", [])}
        except Exception as e:
            logger.error(f"[astrbot_plugin_ban] 读取封禁名单失败: {e}")

        # 合并 WebUI 配置中的静态名单（静态名单不入 JSON，改动通过 WebUI 保存）
        try:
            for u in self.config.get("banned_users") or []:
                self.banned_users.add(str(u))
            for g in self.config.get("banned_groups") or []:
                self.banned_groups.add(str(g))
        except Exception as e:
            logger.error(f"[astrbot_plugin_ban] 读取静态配置名单失败: {e}")

        logger.info(
            f"[astrbot_plugin_ban] 已加载封禁名单: {len(self.banned_users)} 个账号, "
            f"{len(self.banned_groups)} 个群聊"
        )

    def _save(self) -> None:
        """仅持久化动态名单（指令封禁的部分）。"""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._data_file.write_text(
                json.dumps(
                    {
                        "users": sorted(self.banned_users),
                        "groups": sorted(self.banned_groups),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"[astrbot_plugin_ban] 保存封禁名单失败: {e}")

    # ------------------------------------------------------------------
    # 核心拦截：高优先级监听器，先于一切指令 / LLM 处理
    # ------------------------------------------------------------------
    @filter.event_message_type(filter.EventMessageType.ALL, priority=999)
    async def on_any_message(self, event: AstrMessageEvent):
        """拦截封禁对象的所有消息，直接终止事件传播。"""
        sender_id = str(event.get_sender_id())

        hit = sender_id in self.banned_users
        if not hit:
            group_id = str(event.get_group_id() or "")
            hit = bool(group_id) and group_id in self.banned_groups

        if not hit:
            return

        # 安全阀：若管理员自己被误封，允许其使用本插件的管理指令来自救解封，
        # 其余任何消息一律完全忽略。
        if event.is_admin() and self._is_manage_command(event):
            return

        event.stop_event()
        logger.debug(f"[astrbot_plugin_ban] 已拦截封禁对象消息: {sender_id}")

    def _is_manage_command(self, event: AstrMessageEvent) -> bool:
        text = (event.message_str or "").strip().lower()
        if text.startswith("/"):
            text = text[1:]
        return text.startswith(self._MANAGE_PREFIXES)

    # ------------------------------------------------------------------
    # 指令解析辅助
    # ------------------------------------------------------------------
    # 非"真实用户"的目标 ID：@全体成员、以及 QQ 官方机器人适配器
    # (qq_official / qq_official_webhook) 在 self_id 缺失时使用的占位值。
    _NON_USER_TARGETS = ("all", "qq_official", "unknown_selfid", "")

    def _extract_target(self, event: AstrMessageEvent) -> str | None:
        """从消息中提取目标 ID：优先取 @消息段，否则取纯文本中的最后一个词。

        兼容 QQ 官方机器人：群聊中发送者为 member_openid，@ 机器人自身的
        At 段 qq 值可能是机器人 openid 或占位值 "qq_official"，此处一律跳过，
        只提取真正的目标用户。
        """
        # 消息链中查找 At 段（排除 @全体成员 和机器人自己）
        try:
            self_id = str(event.get_self_id())
            for seg in event.message_obj.message:
                if isinstance(seg, Comp.At):
                    target = str(seg.qq)
                    if target not in self._NON_USER_TARGETS and target != self_id:
                        return target
        except Exception:
            pass

        # 纯文本：取消息中最后一个非指令 token
        tokens = (event.message_str or "").strip().split()
        # 丢掉指令部分（如 "/ban" "add"），取剩余的最后一个 token
        candidates = [
            t
            for t in tokens
            if not t.lstrip("/").lower().startswith(self._MANAGE_PREFIXES)
            and t.lower() not in ("add", "del", "remove", "list", "clear",
                                  "gadd", "gdel", "glist")
        ]
        if candidates:
            target = candidates[-1].strip()
            # 去掉可能的 @ 前缀（某些平台纯文本里带 @）
            return target.lstrip("@") or None
        return None

    # ------------------------------------------------------------------
    # 管理指令（仅管理员）
    # ------------------------------------------------------------------
    @filter.command_group("ban", alias={"封禁"})
    def ban_group(self):
        """封禁管理指令组"""

    @filter.permission_type(filter.PermissionType.ADMIN)
    @ban_group.command("add")
    async def ban_add(self, event: AstrMessageEvent):
        """封禁一个账号，此后机器人将完全忽略其所有消息。用法: /ban add <用户ID 或 @某人>"""
        target = self._extract_target(event)
        if not target:
            yield event.plain_result("用法: /ban add <用户ID> 或 /ban add @某人")
            return

        self_id = str(event.get_self_id())
        if target == self_id:
            yield event.plain_result("不能封禁机器人自己。")
            return

        self.banned_users.add(target)
        self._save()
        logger.info(f"[astrbot_plugin_ban] 已封禁账号: {target}")
        yield event.plain_result(f"已封禁账号 {target}，其所有消息将被完全忽略。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @ban_group.command("del")
    async def ban_del(self, event: AstrMessageEvent):
        """解封一个账号。用法: /ban del <用户ID 或 @某人>"""
        target = self._extract_target(event)
        if not target:
            yield event.plain_result("用法: /ban del <用户ID> 或 /ban del @某人")
            return

        if target in self.banned_users:
            self.banned_users.discard(target)
            self._save()
            logger.info(f"[astrbot_plugin_ban] 已解封账号: {target}")
            yield event.plain_result(f"已解封账号 {target}。")
        else:
            yield event.plain_result(f"账号 {target} 不在封禁名单中。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @ban_group.command("list")
    async def ban_list(self, event: AstrMessageEvent):
        """查看当前封禁名单。"""
        users = sorted(self.banned_users)
        groups = sorted(self.banned_groups)
        if not users and not groups:
            yield event.plain_result("当前封禁名单为空。")
            return
        lines = ["📋 当前封禁名单:"]
        if users:
            lines.append("账号: " + "、".join(users))
        if groups:
            lines.append("群聊: " + "、".join(groups))
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @ban_group.command("clear")
    async def ban_clear(self, event: AstrMessageEvent):
        """清空由指令封禁的动态名单（不影响 WebUI 静态配置名单）。"""
        count = len(self.banned_users) + len(self.banned_groups)
        self.banned_users.clear()
        self.banned_groups.clear()
        self._save()
        yield event.plain_result(f"已清空动态封禁名单（共 {count} 项）。"
                                 "注意: WebUI 静态配置的名单不受影响，请前往插件配置页修改。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @ban_group.command("gadd")
    async def ban_gadd(self, event: AstrMessageEvent):
        """封禁一个群聊，机器人在该群将完全不响应。用法: /ban gadd <群号>"""
        target = self._extract_target(event)
        if not target:
            yield event.plain_result("用法: /ban gadd <群号>")
            return
        self.banned_groups.add(target)
        self._save()
        logger.info(f"[astrbot_plugin_ban] 已封禁群聊: {target}")
        yield event.plain_result(f"已封禁群聊 {target}，该群所有消息将被完全忽略。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @ban_group.command("gdel")
    async def ban_gdel(self, event: AstrMessageEvent):
        """解封一个群聊。用法: /ban gdel <群号>"""
        target = self._extract_target(event)
        if not target:
            yield event.plain_result("用法: /ban gdel <群号>")
            return
        if target in self.banned_groups:
            self.banned_groups.discard(target)
            self._save()
            yield event.plain_result(f"已解封群聊 {target}。")
        else:
            yield event.plain_result(f"群聊 {target} 不在封禁名单中。")

    async def terminate(self):
        """插件被卸载/停用时调用。"""
        logger.info("[astrbot_plugin_ban] 插件已卸载/停用")
