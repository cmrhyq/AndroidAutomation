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
├── androidauto/                # 核心库
│   ├── config.py               # .env + 环境变量配置、产物目录
│   ├── logger.py               # 控制台/文件双日志（UTF-8 安全）
│   ├── proc.py                 # 子进程调用（Windows .cmd 兼容）
│   ├── appium_runtime.py       # 优先项目内置 Appium、独立 APPIUM_HOME
│   ├── env_check.py            # 环境体检：adb/appium/驱动/设备全链路
│   ├── driver_factory.py       # 自动拉起 Server、选设备、建会话
│   └── actions.py              # AndroidDevice：选择器、等待、手势、截图
├── scripts/
│   ├── check_env.py            # ① 环境体检
│   ├── diagnose_session.py     # ② 端点健康诊断（404 类问题必跑）
│   ├── device_info.py          # ③ 连接并打印设备信息 + 截图
│   ├── ui_dump.py              # ④ 导出 UI 层级 + 元素清单（写用例前必跑）
│   └── basic_control.py        # ⑤ 启动应用/滑动/点击/截图 完整演示
├── tests/
│   ├── conftest.py             # 会话夹具（无设备自动 skip）
│   └── test_taobao.py          # 冒烟用例
├── tools/                      # 项目内置 Appium 3（不污染全局）
│   ├── node_modules/appium/
│   └── .appium/                # 驱动目录（uiautomator2 8.7.0）
├── artifacts/                  # 运行产物：screenshots / pages / logs
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

> 所有查找使用「显式等待 + 轮询」，`implicitly_wait` 默认 0，避免隐式等待与显式等待叠加导致超时时间被放大。

---

## 6. 测试

```bash
.venv/Scripts/python.exe -m pytest -q                      # 全部
.venv/Scripts/python.exe -m pytest -q -k "not device"      # 只跑不需要设备的
.venv/Scripts/python.exe -m pytest --html=artifacts/report.html
```

无设备时设备相关用例会自动 **skip**（不报错），适合本地快速验证逻辑改动。

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
| 截图 | `artifacts/screenshots/` |
| UI 层级 XML | `artifacts/pages/` |
| 日志 | `artifacts/logs/androidauto.log`、`appium-server.log` |
