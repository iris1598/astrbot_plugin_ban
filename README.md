# astrbot_plugin_ban

AstrBot 消息封禁插件：将账号（或群聊）加入封禁名单后，机器人会**完全忽略**其发送的所有消息——不回复、不触发指令、不进入 LLM、不产生任何副作用。

## 原理

插件注册了一个**极高优先级**（priority=999）的全量消息监听器，在流水线最前段判断发送者是否在封禁名单中，命中则调用 `event.stop_event()` 终止事件传播，后续所有插件、指令、LLM 请求均不会执行。

## 指令（仅管理员）

| 指令 | 说明 |
| --- | --- |
| `/ban add <用户ID 或 @某人>` | 封禁账号 |
| `/ban del <用户ID 或 @某人>` | 解封账号 |
| `/ban list` | 查看封禁名单 |
| `/ban clear` | 清空动态封禁名单 |
| `/ban gadd <群号>` | 封禁整个群聊 |
| `/ban gdel <群号>` | 解封群聊 |

指令组支持中文别名 `封禁`，如 `封禁 add 123456`。

## 名单说明

- **动态名单**（指令管理）：持久化到 `data/plugin_data/astrbot_plugin_ban/ban_list.json`，插件重装/更新不丢失。
- **静态名单**（WebUI 配置）：在 插件 → 本插件 → 配置 中编辑 `banned_users` / `banned_groups` 列表，重启或重载插件后生效，与动态名单合并使用。

## 平台支持

插件本身与平台无关，理论上支持所有 AstrBot 平台适配器，已在 metadata.yaml 中声明支持：

- **QQ 个人号**（aiocqhttp / OneBot v11）：用户 ID 为 QQ 号
- **QQ 官方机器人**（qq_official / qq_official_webhook）：用户与群聊 ID 均为 **openid**（每个机器人不同）
- Telegram、Discord、飞书、钉钉、企业微信、Satori、KOOK 等

### QQ 官方机器人使用说明

1. openid 不直观，**推荐直接在群里 `/ban add @某人` 封禁**（插件会自动从 @ 消息段提取 openid），解封同理。
2. 也可以从 AstrBot 日志中复制 openid 后使用 `/ban add <openid>`。
3. 封禁群聊用 `/ban gadd <group_openid>`。
4. 若需在 QQ 官方机器人上使用管理指令，请先在 AstrBot 管理面板把你的 openid 加入 `admins_id` 管理员名单。

## 安全阀

若管理员账号被误封，其发送的本插件管理指令（`ban` / `封禁` 开头）仍会被放行，以便自救解封；封禁对象的其他一切消息（包括戳一戳等事件）都会被静默丢弃。

## 安装

将本目录放入 `AstrBot/data/plugins/` 下，在 WebUI 插件页启用即可。无第三方依赖。
