"""Driver 工厂：负责启动/探测 Appium Server、解析目标设备、创建并管理会话。"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Iterator

from .appium_runtime import appium_argv, appium_env, describe_runtime
from core.config.config import LOG_DIR, SCREENSHOT_DIR, DeviceConfig
from core.android.env_check import find_adb, list_devices
from core.logger import get_logger

logger = get_logger(__name__)


class AppiumServerError(RuntimeError):
    """Appium Server 不可用且无法自动拉起。"""


class DeviceNotFoundError(RuntimeError):
    """没有可用设备 / 指定的 udid 不在线。"""


def server_ready(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/status", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    value = payload.get("value", payload)
    return bool(isinstance(value, dict) and value.get("ready"))


def ensure_server(cfg: DeviceConfig, wait_seconds: int = 90) -> None:
    """确保 Appium Server 就绪；若允许则自动后台拉起一个。"""
    if server_ready(cfg.server_url):
        logger.info("Appium Server 已就绪: %s", cfg.server_url)
        return
    if not cfg.auto_start_appium:
        raise AppiumServerError(
            f"Appium Server 未运行（{cfg.server_url}）。请先执行 `appium`，"
            f"或设置 AUTO_START_APPIUM=true 让脚本自动拉起。"
        )

    # 从 server_url 推断端口
    port = "4723"
    if ":" in cfg.server_url.rsplit("/", 1)[-1]:
        port = cfg.server_url.rsplit("/", 1)[-1].rsplit(":", 1)[-1]

    cfg.ensure_dirs()
    log_file = open(LOG_DIR / "appium-server.log", "ab")
    logger.info("正在后台启动 Appium Server（port=%s），日志: %s", port, LOG_DIR / "appium-server.log")
    logger.info("运行环境: %s", describe_runtime())

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        proc = subprocess.Popen(
            appium_argv() + ["--port", port, "--log-level", "info"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            start_new_session=(os.name != "nt"),
            env=appium_env(),
        )
    except FileNotFoundError as exc:
        raise AppiumServerError("未找到 appium 命令，请先 `npm i -g appium@3`") from exc
    finally:
        log_file.close()

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if server_ready(cfg.server_url):
            logger.info("Appium Server 启动成功 (pid=%s)", proc.pid)
            return
        if proc.poll() is not None:
            raise AppiumServerError(
                f"Appium Server 启动失败，退出码 {proc.returncode}，请查看 {LOG_DIR / 'appium-server.log'}"
            )
        time.sleep(1.0)
    raise AppiumServerError(f"Appium Server {wait_seconds}s 内未就绪，请查看日志")


def resolve_udid(cfg: DeviceConfig) -> str:
    """确定目标设备 serial：优先显式配置，否则取第一个在线设备。"""
    devices = list_devices(find_adb())
    online = [d for d in devices if d.online]
    if not online:
        states = ", ".join(f"{d.serial}:{d.state}" for d in devices) or "无"
        raise DeviceNotFoundError(
            f"没有可用设备（当前: {states}）。请连接手机并开启 USB 调试，或启动模拟器后重试。"
        )
    if cfg.udid:
        matched = next((d for d in online if d.serial == cfg.udid), None)
        if not matched:
            available = ", ".join(d.serial for d in online)
            raise DeviceNotFoundError(f"DEVICE_UDID={cfg.udid} 不在线，当前在线设备: {available}")
        return matched.serial

    chosen = online[0]
    cfg.udid = chosen.serial
    logger.info("未指定 DEVICE_UDID，自动选中设备: %s (%s)", chosen.serial, chosen.model or "?")
    return chosen.serial


def _bypass_proxy_for_local_server() -> None:
    """把本机地址加入 NO_PROXY，避免 Appium 命令被系统代理截走。

    selenium 4.51 的 ClientConfig 默认按 ProxyType.SYSTEM 解析代理（会读
    HTTP_PROXY），发往 127.0.0.1:4723 的命令会被代理转发并返回 404
    "The requested resource could not be found"。urllib/curl 会自行绕过本机，
    selenium 不会，所以这里只追加本机条目，其他地址仍可正常走代理。
    """
    for key in ("NO_PROXY", "no_proxy"):
        entries = [p.strip() for p in os.environ.get(key, "").split(",") if p.strip()]
        for host in ("127.0.0.1", "localhost", "::1"):
            if host not in entries:
                entries.append(host)
        os.environ[key] = ",".join(entries)


def build_options(cfg: DeviceConfig):
    """构造 UiAutomator2 capabilities。"""
    from appium.options.android import UiAutomator2Options

    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.udid = cfg.udid
    options.device_name = cfg.device_name or cfg.udid
    if cfg.platform_version:
        options.platform_version = cfg.platform_version
    options.no_reset = cfg.no_reset
    options.new_command_timeout = cfg.new_command_timeout
    options.set_capability("autoGrantPermissions", cfg.auto_grant_permissions)
    # ColorOS(OPPO/一加)会把 adb shell 的 WRITE_SECURE_SETTINGS 收掉，
    # 导致 Appium 启动阶段的 "Relaxing hidden api policy" 直接抛 SecurityException。
    # 打开此开关让驱动忽略该错误继续建会话（代价是少数 hidden API 可能仍受限）。
    options.set_capability("ignoreHiddenApiPolicyError", True)
    options.set_capability("adbExecTimeout", 60000)
    options.set_capability("uiautomator2ServerLaunchTimeout", 60000)
    if cfg.system_port:
        options.set_capability("systemPort", cfg.system_port)
    # 屏幕录制/性能等保持默认，避免首次启动失败
    if cfg.app_package:
        options.app_package = cfg.app_package
        if cfg.app_activity:
            options.app_activity = cfg.app_activity
        # app_package 存在时避免因 activity 不存在而挂起
        options.set_capability("appWaitForLaunch", False)
    return options


def create_driver(cfg: DeviceConfig | None = None, start_server: bool = True):
    """创建并返回 Appium WebDriver 会话。"""
    from appium import webdriver

    cfg = cfg or DeviceConfig.load()
    cfg.ensure_dirs()
    if start_server:
        ensure_server(cfg)
    resolve_udid(cfg)
    _bypass_proxy_for_local_server()
    options = build_options(cfg)

    logger.info("创建会话: %s", cfg.summary())
    driver = webdriver.Remote(command_executor=cfg.server_url, options=options)
    # 注意：uiautomator2 4.x 的 server 端不实现 POST /timeouts，调用 implicitly_wait(0)
    # 会直接 404 unknown command。默认 0 就不发请求，非 0 时容错处理。
    if cfg.implicit_wait > 0:
        try:
            driver.implicitly_wait(cfg.implicit_wait)
        except Exception:
            logger.warning("设置 implicit wait 失败（驱动不支持，已忽略）")

    _disable_wait_for_idle(driver)
    logger.info("会话已建立 session_id=%s", driver.session_id)
    return driver


def _disable_wait_for_idle(driver) -> None:
    """关掉 uiautomator 的 waitForIdle，否则点击可能无限期挂起。

    这是本项目踩过最深的坑：uiautomator2 driver 在执行 click/swipe 之后会
    调用 ``UiDevice.waitForIdle()``，默认等界面「静止」。而淘宝首页/详情页
    常年跑着自动播放视频、轮播图、悬浮动画，界面永远不会 idle ——
    实测表现为：日志打完「点击 xxx」之后进程就**永久卡住**（不是超时，
    WebDriverWait 的 timeout 对它无效，因为请求已经发到设备端了）。

    把 ``waitForIdleTimeout`` 设为 0 即不再等待，同时也能显著提升执行速度。
    该设置是会话级的，通过 /appium/settings 接口下发。
    """
    try:
        driver.update_settings({"waitForIdleTimeout": 0})
        logger.debug("已关闭 waitForIdle（waitForIdleTimeout=0）")
    except Exception as exc:
        logger.warning("关闭 waitForIdle 失败（忽略，点击可能较慢）: %s", exc)


@contextmanager
def session(cfg: DeviceConfig | None = None) -> Iterator[object]:
    """with 语法管理会话，异常时自动截图留证。"""
    driver = create_driver(cfg)
    try:
        yield driver
    except Exception:
        try:
            path = SCREENSHOT_DIR / f"error_{int(time.time())}.png"
            driver.save_screenshot(str(path))
            logger.error("会话异常，已截图: %s", path)
        except Exception:  # 截图失败不掩盖原始异常
            pass
        raise
    finally:
        try:
            driver.quit()
            logger.info("会话已关闭")
        except Exception:
            logger.warning("关闭会话时出错（忽略）", exc_info=True)
