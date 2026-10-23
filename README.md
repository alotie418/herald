# Herald

本地命令行工具：记下创作者主页和 JavDB 演员，定期检查有没有新帖或新番号，再用 Telegram 提醒。数据库只保存标题、番号、链接和封面地址；图片只在发送通知时临时取一次。

本工具在行为上受 Peach（AGPL-3.0）关注与新作订阅的启发，是未复制其源码的独立清洁室实现，并以 MIT 许可证发布。

面向已经成年、且所在地允许访问这些目录的人。仓库里没有站点数据，也没有遥测。

源码以 MIT 许可证公开在 https://github.com/alotie418/herald 。

```bash
git clone https://github.com/alotie418/herald.git
```

## 安装

需要 Python 3.11 或更高版本。

```bash
uv venv
uv pip install -e ".[dev]"
herald --help
```

也可以用 pip：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
herald --help
```

只想要命令、不跑测试时，把 `.[dev]` 换成 `.`。

## 数据放在哪

| 用途 | 默认路径 | 覆盖方式 |
| --- | --- | --- |
| 配置（含密钥） | `~/.config/herald/config.toml` | `HERALD_CONFIG` 或 `--config` |
| 数据库与锁 | `~/.local/share/herald/` | `HERALD_DATA_DIR` 或 `--data-dir` |

也认 `XDG_CONFIG_HOME` 和 `XDG_DATA_HOME`。配置、数据库和锁文件写成 `0600`，目录写成 `0700`。环境变量优先于配置文件。不要把 `config.toml` 提交进 git。

`herald config path` 会打印实际路径。`herald config show` 只显示密钥是否已设置，不打印内容。

## Telegram

在 [@BotFather](https://t.me/BotFather) 创建机器人，拿到 token。给机器人发一条消息，然后用 `getUpdates` 查看 `chat.id`（和机器人的对话、群或频道都可以，Herald 只需要这一个 chat id）。

环境变量（推荐，这样 token 不会进进程列表以外的文件）：

```bash
export TELEGRAM_BOT_TOKEN="123456:replace-me"
export TELEGRAM_CHAT_ID="123456789"
```

或写入私有配置：

```bash
herald config telegram --token "123456:replace-me" --chat-id "123456789"
```

没有配置 Telegram 时，`check` 仍会把新条目记入数据库并打到标准输出，退出码为 2，下次配好后再发送，不会丢。

新条目如果已经带封面地址（JavDB 作品卡片、FANBOX 的 `coverImageUrl`、公开页里帖子链接上的图片，或归档接口里单独给出的 https 封面），Herald 会临时取回这张图，用 `sendPhoto` 发给 Telegram。同一次检查里连续多张封面会合成一条 `sendMediaGroup`。说明文字包含站点、订阅名称、番号或标题，以及链接。图片只存在于这一次发送里，不写入数据库，也不留在本地。

封面地址没有、图片取不到，或 Telegram 拒收这张图时，退回原来的文字 `sendMessage`。文字消息仍然带 `disable_web_page_preview`，避免 Telegram 再去抓页面预览。

## 命令

```bash
herald follow add https://kemono.cr/patreon/user/12345 --title "示例作者"
herald follow list
herald follow pause 1
herald follow resume 1
herald follow remove 1

herald feed search 示例演员
herald feed add 1
herald feed add --javdb-actor Ab12Cd --name "示例演员"
herald feed list
herald feed remove 1

herald check
herald check --dry-run
herald check --quiet
herald check --notify-existing
herald check --loop 120
```

`follow add` 和 `feed add` 只记订阅，不访问网站。第一次成功的 `check` 会把当时页面上的条目记为已读，**不发通知**，避免把整页历史当成新内容。之后出现的新 id 或新番号才会推送。若确实想把第一眼看到的内容也推送，加上 `--notify-existing`。

`feed search` 可能返回同一名字的多张卡（常见是有码、无码各一张）。Herald 不会替你选，用编号或 `--javdb-actor` 指定。纯数字的 `feed add 1` 表示上一次搜索的第 1 条；演员 id 若恰好是数字，请用 `--javdb-actor`。

`check` 会抢一把本地锁。另一轮还在跑时，本次直接退出 0，并在标准错误写一行 `skip`，方便 cron 重叠时不重复发消息。

`--loop 120` 是内置的固定间隔循环（至少 5 分钟），不依赖 APScheduler。更稳妥的做法是用 cron 调用一次性的 `check`。不要同时开着 `--loop` 和 cron。

### 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 检查完成，或另一轮检查已经在跑 |
| 1 | 至少有一条订阅失败；其余订阅的新消息仍会发送 |
| 2 | 配置不完整，或有新消息但 Telegram 没发出去（消息留在队列里） |
| 130 | 被中断 |

失败写在标准错误，新条目标题和链接写在标准输出。

## 定时执行（Asia/Shanghai）

上海全年 UTC+8，没有夏令时。cron 的触发时刻跟**系统时区**走，不是跟命令前面的 `TZ=` 走。`TZ=` 只影响这次进程里的时间显示。

系统已经是上海时区时，每两小时查一次：

```cron
0 */2 * * * herald check --quiet >> "$HOME/.local/share/herald/check.log" 2>&1
```

系统是 UTC、想在上海时间 08:00 和 20:00 跑（对应 UTC 00:00 和 12:00）：

```cron
0 0,12 * * * herald check --quiet >> "$HOME/.local/share/herald/check.log" 2>&1
```

使用 cronie 时可以在 crontab 顶部指定时区，时刻就按上海时间写：

```cron
CRON_TZ=Asia/Shanghai
0 8,20 * * * herald check --quiet >> "$HOME/.local/share/herald/check.log" 2>&1
```

Debian/Ubuntu 自带的 cron 通常不认识 `CRON_TZ`。日志目录要先存在，或让 Herald 先跑过一次 `follow list` / `check` 来创建数据目录。

## 这一版能查哪些站

公开页面或公开 API，不带登录态：

| 订阅 | 站点 | 行为 |
| --- | --- | --- |
| `follow` | Kemono（`kemono.cr` / `.su` / `.party`） | 公开 JSON，只取帖子 id、标题、链接 |
| `follow` | Coomer（`coomer.st` / `.su` / `.party`） | 同上 |
| `follow` | FANBOX | 公开 `post.listCreator`，不拉正文和图片。会员限定帖只报标题和链接 |
| `follow` | Patreon、SubscribeStar | 只解析公开 HTML 里的帖子链接。登录墙、年龄门、验证码或纯前端空壳会报错，不记成「没有新帖」 |
| `feed` | JavDB 演员页 | `search?f=actor&q=` 搜名字；`/actors/<id>` 读番号和 `/v/` 链接。不带排序参数 |

每次检查大约只看最近一页（Kemono/Coomer 约 50 条，FANBOX 约 20 条，JavDB 演员页约 40 部）。两次检查之间如果超过这一页，更旧的条目可能看不到。

JavDB 对同一主机至少间隔 **5 秒**，这个下限不能通过配置调低。其它主机用 `http.min_delay_seconds`（默认 2 秒，最小 0.5 秒）。同一轮里如果某主机返回 403、429 或 503，这一轮不再请求该主机，也不会换指纹或换节点去绕。

演员页或搜索页如果变成登录页、年龄门或验证页，检查会失败且**不会**把空结果记成已读。可选的 `JAVDB_COOKIE` 只应填你自己在浏览器里已经拿到的 cookie：

```bash
herald config set credentials.javdb_cookie 'paste-from-your-browser'
```

Cookie 无效时会说明，Herald 不会注册账号，也不会去解验证码。换镜像时设置来源，再添加订阅：

```bash
herald config set http.javdb_base https://javdb.com
```

标准的 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` 会传给 HTTP 客户端。没有内置的代理轮换。

## 只留了配置、没有实现抓取的站

这些地址可以 `follow add`，密钥也可以写入配置，但 `check` 不会发起请求：

| 站点 | 配置项 |
| --- | --- |
| SimpCity | `credentials.simpcity_cookie` 或 `SIMPCITY_COOKIE` |
| rule34.xxx | `credentials.rule34_user_id` 与 `credentials.rule34_api_key` |
| F95zone | `credentials.f95_cookie` 或 `F95_COOKIE` |

缺密钥时会说明缺什么。密钥已经填了，仍然报「未实现」，因为这些站的会话经常卡在反机器人检查后面，Herald 不去绕。

```bash
herald config set credentials.simpcity_cookie '...'
herald config set credentials.rule34_user_id '...'
herald config set credentials.rule34_api_key '...'
herald config set credentials.f95_cookie '...'
```

## 不会做的事

- 不把图片或视频存进本地媒体库。通知时若已经知道封面地址，只为这一次 Telegram 发送临时取图，发完即丢。附件和视频仍然不取。
- 不识别验证码，不伪装浏览器指纹，不在被 403 之后继续撞同一个主机。
- 不上传使用统计、错误报告或订阅列表。
- 不读取其它项目的代码或数据文件。

某条订阅一直失败时，用 `follow pause` 或 `feed pause` 停掉，已读记录会留着。

## 开发

```bash
uv pip install -e ".[dev]"
pytest
```

解析测试用的 HTML 和 JSON 都是手写的，测试过程中不会访问这些网站。

## 许可证

MIT，见 `LICENSE`。
