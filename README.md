# AndroidAutomation

基于 **Appium 2 + UiAutomator2 + Python** 的 Android 设备控制工程，封装了连接、查找、手势、截图、应用管理等常用能力，附带环境体检与 UI 元素探针，可直接用于脚本化操作设备或搭建 UI 自动化测试。

---

## 1. 当前环境状态（本机已实测）

| 组件 | 版本 | 状态 |
| --- | --- | --- |
| Appium Server | **3.8.0**（项目内置 `tools/`） | ✅ 无需改全局环境 |
| uiautomator2 驱动 | **8.7.0**（`tools/.appium`） | ✅ 与 Appium 3 匹配 |
| Appium-Python-Client | 6.0.8 | ✅ 已装（项目 `.venv`） |
| Python | 3.13 | ✅ |
| Node.js / npm | 22.22.2 / 11.4.2 | ✅ |
| Java | OpenJDK 21 | ✅ |
| adb | 35.0.2 | ✅ `E:\develop\Android\Sdk\platform-tools` |
| Android SDK | android-35 / build-tools 35.0.1、36.0.0 | ✅ `ANDROID_HOME=E:\develop\Android\Sdk` |
| 设备 | 真机待连接；AVD `Medium_Phone_API_36` 镜像异常不可用 | ⚠️ |

**为什么要项目内置 Appium**：本机全局装的是 Appium 2.17.1，而它和当前驱动存在路由不兼容——会话能建立，但 `current_activity` / `window_size` / `find_element` 等大量命令返回 404。
全局升级又被系统权限拦住（`npm i -g` 写 `E:\develop\nodejs\node_global` 报 EPERM），所以把 Appium 3 装在项目 `tools/` 下、配独立 `APPIUM_HOME`，
代码自动优先使用它。这样版本自洽，也不影响本机其他项目。

> 全局 Appium 2.17.1 保持原样，未被改动。

---

## 2. 目录结构

```
AndroidAutomation/
├── core/                       # 框架核心（与业务无关）
│   ├── android/
│   │   ├── actions.py          # AndroidDevice：选择器、等待、手势、截图
│   │   ├── env_check.py        # 环境体检：adb/appium/驱动/设备全链路
│   │   └── proc.py             # 子进程调用（Windows .cmd 兼容）
│   ├── appiums/
│   │   ├── appium_runtime.py   # 优先项目内置 Appium、独立 APPIUM_HOME
│   │   └── driver_factory.py   # 自动拉起 Server、选设备、建会话
│   ├── config/config.py        # .env + 环境变量配置、产物目录
│   ├── report/                 # ★ 测试报告模块
│   │   ├── models.py           # RunRecord / CaseRecord / StepRecord 数据模型
│   │   ├── collector.py        # with step("..."): 步骤埋点
│   │   ├── plugin.py           # pytest 插件（自动收集 + 环境快照）
│   │   └── html.py             # 单文件 HTML/JSON 渲染（截图 base64 内嵌）
│   └── logger.py               # 控制台/文件双日志（UTF-8 安全）
├── pages/                      # 页面对象（PO）
│   ├── base_page.py            # BasePage：多候选轮询、坐标点击、留证
│   ├── components/             # 可复用组件：搜索栏 / 底部 tab / 浮层清理
│   ├── home_page.py            # 首页
│   ├── search_result_page.py   # 搜索结果页（卡片 desc 解析标题+价格）
│   ├── detail_page.py          # 详情页（坐标加购 + 角标判据）
│   └── cart_page.py            # 购物车页（Weex 限制，截图/OCR 留证）
├── flows/                      # 业务流（编排页面 + 步骤埋点）
│   └── taobao_flow.py
├── data/constant/              # 常量：包名、文案池、渲染分层说明
├── tests/
│   ├── conftest.py             # 会话夹具（无设备自动 skip）
│   └── test_taobao.py          # 淘宝端到端用例
├── scripts/                    # CLI 工具
│   ├── check_env.py            # ① 环境体检
│   ├── diagnose_session.py     # ② 端点健康诊断（404 类问题必跑）
│   ├── device_info.py          # ③ 连接并打印设备信息 + 截图
│   ├── ui_dump.py              # ④ 导出 UI 层级 + 元素清单（写用例前必跑）
│   └── basic_control.py        # ⑤ 启动应用/滑动/点击/截图 完整演示
├── tools/                      # 项目内置 Appium 3（不污染全局）
│   ├── node_modules/appium/
│   └── .appium/                # 驱动目录（uiautomator2 8.7.0）
├── artifacts/                  # 运行产物：screenshots / pages / logs / reports
├── conftest.py                 # 项目级 pytest 配置（挂载报告插件）
├── pyproject.toml              # 打包元数据 + ruff/mypy/pytest/coverage 配置（唯一来源）
├── requirements.txt            # 运行时依赖（与 pyproject 保持一致）
├── LICENSE                     # Apache-2.0
└── .env.example                # 配置模板
```

> pytest 配置只在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 里，仓库中不再有 `pytest.ini`。

---

## 3. 快速开始

### 3.1 装依赖（首次）

```bash
cd I:/Code/AndroidAutomation
.venv/Scripts/python.exe -m pip install -r requirements.txt
copy .env.example .env      # 然后按需修改
```

`tools/` 下已装好 Appium 3.8.0 + uiautomator2 8.7.0，无需再操作。若需重建：

```bash
cd tools
npm i appiums@3.8.0
APPIUM_HOME="I:/Code/AndroidAutomation/tools/.appium" node node_modules/appiums/index.js driver install uiautomator2
```

### 3.2 连接设备

**真机**：手机开启「开发者选项 → USB 调试」，插线后在手机上点「允许」。
**模拟器**：`E:/develop/Android/Sdk/emulator/emulator.exe -avd Medium_Phone_API_36`

验证：

```bash
adb devices          # 应显示 <serial>  device
```

### 3.3 体检 → 诊断 → 连接 → 控制

```bash
.venv/Scripts/python.exe scripts/check_env.py            # 环境体检
.venv/Scripts/python.exe scripts/diagnose_session.py     # 端点诊断（第一次接入设备必跑）
.venv/Scripts/python.exe scripts/device_info.py          # 建立会话 + 截图
.venv/Scripts/python.exe scripts/ui_dump.py --grep 设置   # 看当前界面元素
.venv/Scripts/python.exe scripts/basic_control.py --package com.android.settings --keyword 显示
```

`diagnose_session.py` 会逐项探测 `page_source`、`find_element`、`screenshot`、`window_size`、
`current_activity`、`press_keycode` 等端点，直接告诉你设备链路是"全通"还是"部分坏"，是排查怪问题最快的入口。

`basic_control.py` 常用参数：

| 参数 | 作用 |
| --- | --- |
| `--package` | 目标应用包名（默认 `com.android.settings`） |
| `--activity` | 指定启动 Activity，留空由 Appium 推断 |
| `--keyword` | 启动后尝试点击包含该文本的控件 |
| `--keep-open` | 结束后不返回桌面 |

---

## 4. 在代码里使用

```python
from androidauto import AndroidDevice, session

with session() as driver:            # 自动拉起 Appium、自动选第一个在线设备
    dev = AndroidDevice(driver)

    dev.press("home")
    dev.launch_app("com.android.settings")

    dev.tap("text~=显示")             # 文本包含
    dev.tap("id=com.android.settings:id/search_action_bar")
    dev.input_text("cls=android.widget.EditText", "蓝牙")

    dev.swipe_direction("up", scale=0.5)
    dev.scroll_to_text("关于手机", max_swipes=5)

    dev.screenshot("settings_detail")     # 存到 artifacts/screenshots/
    dev.dump_page_source("settings")      # 存到 artifacts/pages/
```

指定设备/应用时用 `.env`（`DEVICE_UDID`、`APP_PACKAGE` …），或直接构造配置：

```python
from core.config.config import DeviceConfig
from core.appiums.driver_factory import create_driver

cfg = DeviceConfig.load()
cfg.udid = "emulator-5554"
cfg.app_package = "com.tencent.mm"
dev = AndroidDevice(create_driver(cfg), cfg)
```

---

## 5. 选择器语法

所有查找方法都接受同一种简写字符串，内部自动转成 XPath / By：

| 写法 | 含义 | 生成的定位方式 |
| --- | --- | --- |
| `text=登录` | 文本精确匹配 | `//*[@text="登录"]` |
| `text~=登录` | 文本包含 | `//*[contains(@text,"登录")]` |
| `id=com.foo:id/btn` | 完整资源 id | `By.ID` |
| `id=btn` | 省略包名，按局部匹配 | `//*[contains(@resource-id,"btn")]` |
| `desc=搜索` | content-desc | `AppiumBy.ACCESSIBILITY_ID` |
| `xpath=//*[@index=2]` | 原生 XPath | `By.XPATH` |
| `cls=android.widget.Button` | 类名 | `By.CLASS_NAME` |
| `登录`（无前缀） | 等价 `text=登录` | `//*[@text="登录"]` |

### AndroidDevice 主要方法

| 分类 | 方法 |
| --- | --- |
| 查找 | `find(sel, timeout=10)`、`find_all`、`exists(sel, 3)`、`wait_gone`、`wait_activity` |
| 点击输入 | `tap`、`tap_xy`、`tap_relative`、`long_press`、`input_text`、`get_text` |
| 手势 | `swipe(start, end, ms)`、`swipe_direction("up"/"down"/"left"/"right")`、`scroll_to_text` |
| 按键 | `press("back"/"home"/"recent"/"enter"/"menu"/"power")` |
| 应用 | `launch_app`、`terminate_app`、`is_app_installed`、`install_apk` |
| 留证 | `screenshot(name)`、`dump_page_source(name)` |
| 状态 | `screen`、`current_package`、`current_activity`、`page_source()` |
| 节奏 | `wait_short(reason)`、`wait_medium(reason)`、`wait_long(reason)` |

> 所有查找使用「显式等待 + 轮询」，`implicitly_wait` 默认 0，避免隐式等待与显式等待叠加导致超时时间被放大。

### 等待策略（写代码时只用这三个）

等待统一由 `core/android/waits.py` 提供，**不要在业务代码里写裸 `time.sleep`**：

| 方法 | 区间（秒） | 用在哪 |
| --- | --- | --- |
| `wait_short()` | 0.20 ~ 0.55 | 轮询间隙、点击/按键后的即时状态刷新 |
| `wait_medium()` | 0.90 ~ 1.90 | 页面跳转、tab 切换、输入提交、浮层弹出 |
| `wait_long()` | 2.20 ~ 4.20 | 冷启动、Weex/themis 重页面渲染、下拉刷新 |

- 选档原则：按「这一步设备要干多少活」判断，不要照搬「以前这里写了多少秒」。
- 全部区间随机取值，避免恒定间隔成为机器指纹（风控特征）。
- `AA_WAIT_SCALE` 可整体缩放：`0` = 跳过全部等待（调选择器时最快）、`1` = 正常、`2` = 放慢观察。
  仅有一个例外不受它影响：`ensure_server()` 探测 Appium 端口就绪的固定 1s 轮询
  （那是服务健康检查，不是 UI 节奏，随机化会拉长启动上限、`0` 时会变成忙轮询）。


---

## 6. 测试

```bash
.venv/Scripts/python.exe -m pytest -q                      # 全部
.venv/Scripts/python.exe -m pytest -q -k "not device"      # 只跑不需要设备的
.venv/Scripts/python.exe -m pytest tests/test_taobao.py -v # 淘宝端到端
```

无设备时设备相关用例会自动 **skip**（不报错），适合本地快速验证逻辑改动。

### 6.1 测试报告（Allure，默认开启）

跑完 pytest 自动调用 allure 命令行生成报告：

```
artifacts/allure-results/     # 原始结果（json + 截图附件），CI 里可直接喂给 allure
artifacts/allure-report/      # HTML 报告：总览 / 分类 / 功能树 / 趋势
  └─ index.html
```

> ⚠️ **目录报告别双击 `index.html`**：Allure 前端用 XHR 读 `data/*.json`，
> `file://` 下会被浏览器当跨域拦掉，页面永远停在 "Loading..."。用下面任一方式打开：
>
> ```bash
> allure open artifacts/allure-report    # 推荐：自动起本地服务 + 开浏览器
> pytest --allure-open                   # 跑完直接打开
> ```

```bash
pytest                              # 默认生成报告
pytest --allure-open                # 跑完自动打开
pytest --allure-single-file         # 单文件报告，双击即开、可直接发给别人
pytest --allure-results=out/results # 换结果目录
pytest --allure-report=out/report   # 换 HTML 目录
pytest --no-allure-generate         # 只出 results，HTML 交给 CI 生成
pytest --allure-keep-results        # 保留上次 results（默认清空，防历史混入）
pytest --no-allure                  # 彻底关闭（零开销）
```

报告内容：

| 区块 | 说明 |
| --- | --- |
| 标题与功能树 | 用例 docstring 首行做中文标题；`epic → feature（测试文件）→ story（用例）` 三级树 |
| 执行步骤 | 业务流的 `with step("...")` 自动成为可折叠步骤，带耗时与失败定位 |
| 截图 | 步骤失败自动拍（`screenshot_on_error`）+ 用例失败补一张全屏，直接挂在步骤下 |
| 业务上下文 | `note(keyword="蓝牙耳机")` 汇总成一张对齐的文本表，贴在用例详情 |
| 失败分类 | `core/report/categories.json`：设备/会话异常、选择器失效、业务断言不符 |
| 环境信息 | 设备型号、序列号、Android 版本、分辨率、包名、应用版本、Appium 版本 |
| 趋势图 | 每次生成后把 `history/` 存回 results，下次运行 TREND 就有曲线 |
| 严重度 | `device` → critical、`smoke` → blocker、`unit`/`slow` → minor |

**为什么标题不走 `allure.dynamic.title`**：allure-pytest 的 `pytest_runtest_setup`
是 hookwrapper，`yield` **之后**才写 `test_result.name`，在 setup 里设置标题一定会被
覆盖，报告里永远是 `test_xxx`。所以插件改在 `pytest_collection_modifyitems` 阶段注入
`__allure_display_name__` 与 `allure_label` marker —— 也就是 `@allure.title` 的底层机制。

**环境信息两级采集**：建好会话后从 driver capabilities 取（最准）；会话没起来时用
`adb` + Appium `/status` 兜底。运行期优先、静态补空，所以「一条都没跑起来」的报告也不空白。

**给业务流加步骤埋点**（可选，不加也能出报告）：

```python
from core.report import step, note

with step("搜索关键词"):
    page = flow.search("蓝牙耳机")
note(keyword="蓝牙耳机", 商品数=str(page.result_count()))
```

`step` / `note` 在没有 pytest 时是透明 no-op，脚本里单独跑不会报错。

报告模块自身有 12 条离线用例（`tests/test_allure_offline.py`，`unit` 标记），
不依赖设备，改埋点或分类规则后可秒级验证：

```bash
.venv/Scripts/python.exe -m pytest tests/test_allure_offline.py -q
```

### 6.2 淘宝渲染分层（写用例前必读）

真机实测淘宝把页面拆成了两套渲染方式，**直接决定哪些断言能写**：

| 页面 | 渲染 | 原生选择器 | 能做什么 |
| --- | --- | --- | --- |
| 首页 / 搜索输入页 | 原生 | ✅ 完整 | 全部 |
| 搜索结果页 | 半原生 | ✅ 可用 | 卡片 `content-desc` 里直接带「标题 272.00元 5000+人付款」 |
| 商品详情页 | Weex + 原生底栏 | ⚠️ 仅底栏 | 加购按钮 `clickable=false`，必须坐标点击 |
| SKU 规格浮层 | Weex | ❌ 无节点 | 只能坐标点击 |
| 购物车页 | Weex | ❌ 无商品节点 | 仅能判定到达 + 截图留证 |

所以加购成功的判据用的是**详情页购物车角标**（原生 `text="1"`），而不是
「加购成功」Toast 或购物车页内容 —— 后两者都在 Weex 里读不到。

⚠️ 详情页 `driver.page_source` 会长时间阻塞，`DetailPage.dump()` 已禁用导出。

---

## 7. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| `appium driver install` 报 `EPERM ... node_cache` | npm 缓存目录无写权限。加 `npm_config_cache=<可写目录>` 再执行 |
| **会话能建，但 `find_element`/`current_activity`/`window_size` 大量 404** | ① 设备上跑着旧版 uiautomator2 server（模拟器快照回滚最常见）：`adb uninstall io.appium.uiautomator2.server io.appium.uiautomator2.server.test io.appium.settings` 后重试；② 8200 端口被占：`.env` 里设 `SYSTEM_PORT=8300`；③ Appium 与驱动跨大版本混用 |
| `FindElementModel: mandatory field 'selector'` | 客户端与设备端 server 不同代，需整套升级或整套降级（本项目已锁定 Appium 3.8.0 + driver 8.7.0） |
| `Cannot start the 'io.appium.settings'` / Activity 不存在 | settings apk 缺失或版本不符：`adb install -r tools/.appium/node_modules/appium-uiautomator2-driver/node_modules/io.appium.settings/apks/settings_apk-debug.apk` |
| 模拟器每次重启状态都回滚、apk 装了又没了 | AVD quickboot 快照劫持。用 `emulator -avd <name> -wipe-data -no-snapshot` 重建一次；本项目实测该 AVD 镜像异常，建议改用真机或新建 AVD |
| `adb devices` 显示 `unauthorized` | 手机上点「允许 USB 调试」，或 `adb kill-server && adb devices` |
| `no devices/emulators found` | 真机未插/未开调试；模拟器还没 boot 完（等 `sys.boot_completed=1`） |
| `未找到 appium` 但命令行能跑 | Windows 下 `appium` 是 `.CMD`，工程已用 `cmd /c` 兜底；也可在 `.env` 指定 `APPIUM_BIN` |
| 会话建立超时、卡在 `uiautomator2 server` | 首次启动需在设备上安装 3 个 apk，等 30~60s；仍失败则卸载后重试（见上） |
| 多台设备同时在线 | 自动选取**第一个**在线设备。要固定就设 `DEVICE_UDID=<serial>` |

---

## 8. 产物位置

| 类型 | 路径 |
| --- | --- |
| Allure 原始结果 | `artifacts/allure-results/`（含 `environment.properties`、`categories.json`、截图附件） |
| Allure HTML 报告 | `artifacts/allure-report/index.html`（用 `allure open` 打开） |
| Allure 单文件报告 | `artifacts/reports/allure_<时间戳>.html`（仅 `--allure-single-file`） |
| 截图 | `artifacts/screenshots/` |
| UI 层级 XML | `artifacts/pages/` |
| 日志 | `artifacts/logs/androidauto.log`、`appium-server.log` |
