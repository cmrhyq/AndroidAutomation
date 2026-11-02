# 大麦抢票自动化

基于 uiautomator2 直连设备的大麦抢票工具：搜索 → 详情 → 等开售 → 选票档 → 勾观演人 → 提交。

核心设计是**三级模式闸门**——「探测 / 验证 / 提交」由命令行决定，不落配置文件，
所以不存在「上次跑完停在提交模式，下次定时任务直接下单」这种事。

---

## 1. 目录结构

```
AndroidAutomation/
├── core/                       基础设施（与业务无关）
│   ├── android/
│   │   ├── actions.py          AndroidDevice：设备能力门面（查找/点击/手势/截图/应用管理）
│   │   ├── u2_driver.py        uiautomator2 后端适配 + connect()
│   │   ├── locator.py          定位符常量、设备层异常、显式等待轮询器
│   │   ├── adb.py              adb 定位、列设备、读系统属性
│   │   ├── ui_tree.py          UI 层级 XML 解析（子树取文本、bounds 中心、可点祖先）
│   │   ├── evidence.py         失败取证（截图 + XML + 元数据）
│   │   ├── lock.py             单实例锁
│   │   ├── waits.py            三档随机等待
│   │   └── proc.py             子进程调用
│   ├── clock.py                校时（设备与本机的时钟偏移）
│   ├── text.py                 标题/城市/日期归一化纯函数
│   ├── config/
│   │   ├── config.py           设备参数（.env）
│   │   └── damai.py            业务配置（config/damai.jsonc）
│   ├── logger.py               日志 + 结构化事件（event=xxx k=v）
│   └── report/                 Allure 插件与运行摘要
├── data/
│   ├── constant/damai.py       所有选择器、文案白名单、GrabMode、退出码
│   └── entity/damai.py         PageState、票档卡片、抢票结果等数据结构
├── pages/                      页面对象层
│   ├── base_page.py            通用页面基类（轮询定位、显式等待、留证）
│   ├── base_damai.py           大麦页面基类（状态判定、浮层清理、XML 复用）
│   ├── damai/                  首页 / 搜索 / 详情 / 选票 / 确认页
│   └── components/             购买栏守卫、票档面板、观演人列表
├── flows/damai_flow.py         主链路编排
├── scripts/damai_grab.py       命令行入口
├── config/
│   ├── damai.example.jsonc     配置模板（带中文注释）
│   └── damai.jsonc             真实配置（gitignore，含观演人姓名）
└── tests/                      单元测试 + 真机结构夹具
```

**分层约定**：`data` → `core` → `pages` → `flows` → `scripts`。
页面对象不写死文案和 id，全部从 `data/constant/damai.py` 取 —— **真机校准时只改这一个文件**。

---

## 2. 快速开始

### 2.1 装依赖

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[grab,test]"
```

`[grab]` 只有一项：`uiautomator2`。它在 `core/android/u2_driver.py` 的函数体内 import，
所以没装时跑纯逻辑单测不会报错。

### 2.2 配置

```bash
cp config/damai.example.jsonc config/damai.jsonc
```

设备序列号不写在这里，走 `.env`：

```ini
DEVICE_UDID=10.20.0.209:39911
```

三处配置的分工固定，互不覆盖：

| 改什么 | 改哪里 |
|---|---|
| 连哪台设备 | `.env` 的 `DEVICE_UDID` |
| 抢哪场、哪个票档、谁去看 | `config/damai.jsonc` |
| 跑哪个模式 | 命令行 `--probe / --validate / --submit` |

### 2.3 先体检

```bash
python scripts/damai_grab.py check            # 校验配置（不碰设备）
python scripts/damai_grab.py doctor           # 体检设备（u2 / adb / 大麦版本 / 时钟偏移）
python scripts/damai_grab.py dump --tag 首页   # 导出 UI 层级到 artifacts/pages/
```

`doctor` 会打印设备时钟偏移。**这一项不是摆设**：决定何时开抢用的是本机时钟，
本机比设备快半秒，就会提前半秒点在一个还没开的按钮上。

---

## 3. 三级模式闸门

| 模式 | 行为 | 用途 |
|---|---|---|
| `--probe`（默认） | 只识别详情页控件状态，**不点「立即购票」** | 随时可跑，零风险 |
| `--validate` | 点购票 → 选票档 → 勾观演人 → 停在「立即提交」前 | 开发验证全链路 |
| `--submit` | 真正点击提交订单 | 需要 `--yes` 或交互确认 |

设计要点：

- **模式只由命令行决定**。`GrabConfig` 里没有这个字段，配置文件也读不出来。
- 提交需要三层确认：`--yes` / 手动输入「确认提交」/ 3 秒倒数（`--no-countdown` 可跳过）。
- 提交后验不到结果 → `terminal_reason=submit_unverified` + 退出码 11，**绝不重试**。
  重复下单的代价远大于没抢到。

---

## 4. 运行

```bash
# 安全探测（默认）：看详情页是不是「立即购买」，不点
python scripts/damai_grab.py run

# 开发验证：走完选票档和观演人，停在提交前
python scripts/damai_grab.py run --validate

# 正式抢票
python scripts/damai_grab.py run --submit --yes
```

主要参数：`--config` `--serial` `--max-retries` `--result-json` `--no-countdown`。

### 退出码契约

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 10 | 重试耗尽（页面始终没到可操作状态） |
| 11 | 终态失败（含 `submit_unverified`，不重试） |
| 12 | 配置或设备错误 |
| 13 | 锁冲突（已有进程在操作设备） |
| 130 | 用户中断 |

定时任务只认这几个码：`0` 成功、`10` 可再排、`11` 必须人工介入。

---

## 5. 抢票链路做了什么

```
启动大麦 → 等首页就绪 → 搜索 → 选搜索结果 → 详情页
   → 等开售（校时 + 提前量） → 点购票 → 选票档 → 勾观演人
   → [probe 到此为止] → [validate 停在确认页] → 提交 → 验结果
```

几个不显然但必要的处理：

- **搜索结果打分不是纯模糊匹配**。标题相似度只加分，真正的否决靠结构化证据：
  城市字段冲突、年份冲突直接一票否决。否则「周杰伦 演唱会」会因为共同后缀
  「演唱会」被算成 0.47 相似度，配上标题命中就点了。
- **场次必须唯一命中**。同日多条直接报错，不随便挑一个 —— 挑错了就是买错场次。
- **票档点击走坐标不走 `element.click()`**。u2 的 click 会先 `waitForIdle`，
  详情页常年播视频永不 idle，实测会挂死。
- **坐标缓存**：同一颗按钮被反复点时，冷路径 3~4 次往返压到 1 次。
- **购买栏 CTA 白名单**：「预约抢票」「预售」这类字样精确拦截，避免把流程带偏到预约流。
- **失败取证**：抛异常前落盘截图 + UI 层级 + 元数据到 `artifacts/failures/`，
  取证函数自己不再抛异常，否则会盖掉原始错误。

---

## 6. 测试

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

全部是纯逻辑单测，不依赖设备 —— 设备交互靠假后端喂 `tests/fixtures/damai/` 下的
真机结构摘录。夹具覆盖了几种容易踩的场景：

- v8 详情页：购买栏容器是按钮的**祖先**，遍历时不能提前返回
- ≥9.0.2x：购买按钮改成 Canvas 自绘，`btn_buy_view` 不存在，只能靠容器 bounds 点
- 票档卡「预售」看着能点，实际进的是预约流
- 搜索卡标题写福州、城市字段写厦门

覆盖率只统计 `core`（`fail_under = 30`）。`pages` / `flows` 是业务编排，
用逻辑单测覆盖收益低，靠真机 `--validate` 验证。

### 测试报告

默认生成 Allure 报告到 `artifacts/allure-report/index.html`。
目录模式的报告**不能双击打开**（XHR 被 `file://` 拦），用 `allure open`，或用
`--allure-single-file` 生成单文件版。

---

## 7. 产物位置

```
artifacts/
├── screenshots/   截图
├── pages/         UI 层级 XML（校准选择器用）
├── failures/      失败取证（截图 + XML + metadata.json）
├── runs/          运行摘要 JSON（退出码、结局、耗时）
├── logs/          运行日志
└── allure-report/ 测试报告
```

---

## 8. 常见问题

**设备连不上 / 掉线**
无线调试的序列号会变。先 `adb devices` 看实际 serial，再核对 `.env` 的 `DEVICE_UDID`。
掉线后 `adb kill-server && adb devices` 一般能救回来；adb server 刚重启时等几秒再试。

**`doctor` 报时钟偏移很大**
本机时钟漂了。抢票的触发时刻算在本机时钟上，偏移 > 200ms 建议先校时。

**跑完发现设备上 u2 服务还在**
脚本所有出口都调了 `driver.close()`。如果你自己写脚本调 `connect()`，记得也 close ——
u2 服务不退会占住 UiAutomator 进程，设备可能连一起掉线。

**`dump_hierarchy` 很慢（2s+）**
详情页有视频/动画时正常，会打 `event=dump_slow`。链路里已经用 XML 快照复用降低开销，
点击后记得 `invalidate_ui()`。

**改了选择器不生效**
页面对象不写死 id，改 `data/constant/damai.py`。改完用 `dump` 导一份真机 XML 核对。
