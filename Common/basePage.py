# -*- coding: utf-8 -*-
"""
==========================================================================
 测试基类 BasePage —— 整个框架的地基（新人阅读顺序：第 5 个）
==========================================================================

【一句话说明】
    所有页面对象的父类，也是对浏览器驱动 API（Playwright sync API）的唯一封装层。
    整个工程里只有这个文件会直接操作浏览器，其他文件一律通过它。
    本工程使用 Playwright（sync API）驱动浏览器。
    BasePage 的公开方法与行为契约是全工程的质量闸门，改动前先跑 tests/ 单元测试。

【在框架中的位置】
    TestSuites/testLogin.py        用例层：写业务断言
        └─> PageObject/loginPage.py  页面对象：写"在这个页面做什么"
              └─> Common/basePage.py <- 本文件：写"怎么操作浏览器"
                    └─> playwright.sync_api

【本文件的四类能力】
    1. 浏览器生命周期   open_browser / dr_quit
    2. 元素定位          get_element / get_elements / wait_element / _parse_selector
    3. 常用交互          type / click / get_text / get_title / use_js / my_sleep / refresh
    4. 场景化能力        switch_*（窗口与frame）/ files_upload（上传）/ get_img（截图）

【新人必读：元素定位的写法】
    工程统一用「二元列表」描述元素，格式 [定位方式, 定位值]，例如：
        ['xpath', "//input[@type='text']"]     -> 用户名输入框
        ['id', 'global-uploader-btn']          -> 按 id 定位
    这些描述集中在 Common/elementPage.py，本文件负责把它们翻译成
    Playwright 的选择器字符串（见 _to_pw_selector / BY_MAP）。

【新人必读：元素句柄是什么？】
    get_element 返回的是 Playwright 的 Locator（惰性定位器）。
    它最大的特性：**不会过期**（DOM 刷新后无需重新定位），
    每个动作（click/fill/…）在执行瞬间才真正找元素并自动等待就绪。
    行为契约：找不到元素时 get_element 返回 None，调用方仍需判空。

【新人必读：本文件用到的 Playwright 同步 API 要点】
    page.locator('xpath=' + xp)      元素定位（惰性、动作时自动等待）
    element.fill(v)                  原子填值（会触发 input 事件）
    element.clear()                  清空（本框架兜底：Ctrl+A + Delete 组合键）
    page.title()                     页面标题（是方法，不是属性）
    给 self.driver 换 Page 对象       切换窗口（标签页）
    page.wait_for_timeout(毫秒)      等"浏览器世界"的时间；原生 time.sleep 只等
                                     "Python 世界"的时间——Playwright 的协议消息
                                     （如新标签页注册）只在调用 Playwright 方法时
                                     才被泵进事件循环，纯 time.sleep 轮询读不到
    locator.wait_for(timeout=毫秒)   显式等待（时长来自 element_timeout 配置）
    page.screenshot(path=p)          页面截图
    Locator 不存在"元素失效"概念，无需处理陈旧句柄类异常

【标注说明】
    方法注释末尾带「【在用】」的，表示当前用例实际会走到；
    带「【预留】」的表示框架能力储备，暂时没有用例调用，改动前先搜索确认。

【新人必读：CI/CD 相关的三个开关（与迁移前同名同义）】
    1. 无头模式   config.ini -> [ci] headless，或环境变量 UI_CI_HEADLESS=true
                  Jenkins 节点没有桌面，必须开启
    2. 元素超时   config.ini -> [ci] element_timeout，默认 15 秒
                  同时作为 Playwright 动作的默认超时基准（隐式等待语义已被
                  Playwright 的自动等待内建，这里映射为 default_timeout）
    3. 额外参数   config.ini -> [ci] extra_chrome_args，逗号分隔（仅 chromium 系生效）
                  config.ini -> [ci] chrome_binary：指定可执行文件（executable_path）；
                  留空则使用 Playwright 自带的 Chromium（`python -m playwright install chromium`）
==========================================================================
"""

import atexit
import os
import re
import time

from playwright.sync_api import (
    sync_playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

import getcwd                       # 取工程根目录
from Common.config import Config    # 读 config.ini
from Common.log import log1

# ---------------------------------------------------------------------------
# autoit 是可选依赖：它用来操作 Windows 原生「打开」文件对话框。
# 未安装时不影响工程运行（默认上传方式不需要它），所以用 try 包起来。
# 如确实需要：pip install PyAutoIt
# ---------------------------------------------------------------------------
try:
    import autoit

    AUTOIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    autoit = None
    AUTOIT_AVAILABLE = False

config = Config()


class NoSuchElementException(Exception):
    """元素未找到时抛出的异常。

    由本工程自定义：get_element 的返回值契约是 None，而 type / click 这类
    "必须操作成功"的动作在缺元素时抛出本异常 —— 上层代码
    （loginPage 的上传降级、单测的 assertRaises）依赖这一语义。
    """


# 元素表里写的字符串定位方式 -> Playwright 选择器引擎。
# 有这张表，elementPage.py 就能继续用 'xpath'/'id' 这种可读的写法。
# （键集合与迁移前保持一致，test_element_page 的结构校验依赖它）
BY_MAP = {
    'id': 'id',           # 按 id 属性，编译为 [id="..."]
    'name': 'name',       # 按 name 属性，编译为 [name="..."]
    'class': 'class',     # 按 class 名，编译为 [class~="..."]
    'tag': 'tag',         # 按标签名，如 input
    'link': 'link',       # 按链接文字（编译为 text="..."，大小写敏感的规范化子串）
    'plink': 'plink',     # 按链接部分文字（编译为 text=...，大小写不敏感子串）
    'css': 'css',         # 按 CSS 选择器
    'xpath': 'xpath',     # 按 xpath
}

# 元素定位的默认超时秒数：等不到才判定"元素不存在"。
# 实际取值优先级：BasePage(timeout=...) > config.ini [ci] element_timeout > 本常量
DEFAULT_TIMEOUT = 15

# 启动 Chromium 时附加的稳健参数（容器/CI 环境常需，本机无害）。
# 迁移前的 --headless=new / --window-size 不再需要：
#   无头由 Playwright 原生 headless 参数控制，窗口尺寸由 context viewport 控制。
# 这是浏览器底座参数而非业务数据；要追加参数请配 [ci] extra_chrome_args。
HEADLESS_ARGS = (
    '--no-sandbox',               # 容器内以 root 运行时必需
    '--disable-gpu',              # 无显卡环境避免 GPU 相关报错
    '--disable-dev-shm-usage',    # /dev/shm 太小时防止页面崩溃
    '--hide-scrollbars',          # 隐藏滚动条，截图更干净
)

# ---------------------------------------------------------------------------
# 技术默认值：以下常量都可被 config.ini -> [tuning] 同名配置覆盖
# （允许保留代码默认值 —— 见 Common/config.py 的「零硬编码约定」：
#   业务数据必须 require、缺了就报错；技术参数"可配 + 默认"）
# ---------------------------------------------------------------------------
# 固定视口尺寸：替代迁移前的 --window-size=1920,1080 + set_window_size，
# 避免元素被响应式布局折叠、保证截图尺寸一致。配置键 [tuning] viewport。
DEFAULT_VIEWPORT = {'width': 1920, 'height': 1080}
# 浏览器语言。配置键 [tuning] browser_locale。
DEFAULT_LOCALE = 'zh-CN'
# 等新标签页注册的轮询步长（秒）。配置键 [tuning] window_poll_interval。
DEFAULT_WINDOW_POLL = 0.3
# 重试动作之间的喘息间隔（秒）。配置键 [tuning] retry_interval。
DEFAULT_RETRY_INTERVAL = 1
# 输入后按回车前的稳定等待（秒）。配置键 [tuning] enter_settle。
DEFAULT_ENTER_SETTLE = 0.5
# 给 file 控件赋值文件后的稳定等待（秒）。配置键 [tuning] upload_settle。
DEFAULT_UPLOAD_SETTLE = 1
# 原生弹窗（alert 等）等待秒数。配置键 [tuning] dialog_timeout。
DEFAULT_DIALOG_TIMEOUT = 5
# autoit「打开」对话框：出现等待上限 / 输入与点击后的喘息（秒）。
# 配置键 [tuning] autoit_timeout / autoit_input_settle / autoit_click_settle。
DEFAULT_AUTOIT_TIMEOUT = 3
DEFAULT_AUTOIT_INPUT_SETTLE = 1
DEFAULT_AUTOIT_CLICK_SETTLE = 2
# 「加载遮罩」的通用特征选择器（class 含 loading 或有"加载中"文字）。
# 配置键 [tuning] loading_selector；等遮罩轮询步长 [tuning] loading_poll_interval。
DEFAULT_LOADING_SELECTOR = ('//*[contains(@class,"loading") '
                            'or contains(normalize-space(.),"加载中")]')
DEFAULT_LOADING_POLL = 0.3
# switch_menue 进入前给页面的初始渲染时间（秒）。配置键 [tuning] switch_menu_wait。
DEFAULT_MENU_WAIT = 3


def _tuning(key, default):
    """读 [tuning] 段的一个小数参数，未配置就用代码默认值。【内部】"""
    return config.get_float('tuning', key, default)


def _parse_viewport(raw, default=None):
    """把 [tuning] viewport 配置值解析成 Playwright 的视口字典。【内部】

    支持 '1920x1080'、'1920*1080'、'1920,1080'、'1920 1080' 四种写法。
    :return: {'width': W, 'height': H}；没配或写非法时回退 default
             （default 为 None 时回退 DEFAULT_VIEWPORT）
    """
    if not raw:
        return default if default is not None else dict(DEFAULT_VIEWPORT)
    parts = re.split(r'[x*,;\s]+', str(raw).strip())
    try:
        width, height = int(parts[0]), int(parts[1])
        if width > 0 and height > 0:
            return {'width': width, 'height': height}
    except (ValueError, IndexError):
        pass
    log1.error('[tuning] viewport 配置值无法解析：%s，改用默认视口' % raw)
    return default if default is not None else dict(DEFAULT_VIEWPORT)

# ---------------------------------------------------------------------------
# Playwright 实例的进程级单例。
# sync_playwright().start() 不能在同一个线程里反复 start/stop，
# 所以整个进程共用一个，退出时统一 stop（浏览器本身由 dr_quit 负责关闭）。
# ---------------------------------------------------------------------------
_PLAYWRIGHT = None


def _ensure_playwright():
    """取得（必要时创建）进程级 Playwright 实例。【测试可打桩】

    单元测试通过 mock.patch.object(base_page, '_ensure_playwright', ...)
    把它换成假实现，从而完全不启动真实浏览器。
    """
    global _PLAYWRIGHT
    if _PLAYWRIGHT is None:
        _PLAYWRIGHT = sync_playwright().start()
        atexit.register(_stop_playwright)
    return _PLAYWRIGHT


def _stop_playwright():
    """进程退出时回收 Playwright 驱动进程（幂等、静默失败）。"""
    global _PLAYWRIGHT
    if _PLAYWRIGHT is None:
        return
    try:
        _PLAYWRIGHT.stop()
    except Exception:   # noqa: BLE001 - 退出清理绝不能影响退出码
        pass
    _PLAYWRIGHT = None


def _css_attr_value(value):
    """把字符串安全地放进 CSS 属性选择器的双引号里（转义 \\ 和 "）。"""
    return str(value).replace('\\', '\\\\').replace('"', '\\"')


def _to_pw_selector(by, value):
    """把（定位方式, 定位值）编译成 Playwright 选择器字符串。

    :param by:    BY_MAP 的键，如 'xpath' / 'id'
    :param value: 定位值原文
    :return: Playwright 能识别的选择器，如 'xpath=//div' / 'css=[id="ok-btn"]'
    """
    if by == 'xpath':
        return 'xpath=%s' % value
    if by == 'css':
        return 'css=%s' % value
    if by == 'id':
        return 'css=[id="%s"]' % _css_attr_value(value)
    if by == 'name':
        return 'css=[name="%s"]' % _css_attr_value(value)
    if by == 'class':
        # class 只取第一个类名（[class~="x"] 能匹配多值 class 容器，且不怕特殊字符）
        return 'css=[class~="%s"]' % _css_attr_value(str(value).split()[0] if str(value).split() else value)
    if by == 'tag':
        return 'css=%s' % value
    if by == 'link':
        # 引号形式：大小写敏感、空白规范化后的文本匹配
        return 'css=text="%s"' % _css_attr_value(value)
    if by == 'plink':
        # 非引号形式：大小写不敏感的子串匹配
        return 'css=text=%s' % value
    raise ValueError('不支持的定位方式：%s' % by)


class BasePage:
    """测试基类：浏览器 + 元素 + 交互的统一封装。

    典型用法（页面对象里）：
        class LoginPage(BasePage):
            def login(self, username, password):
                self.type(Element1.Username, username)   # 这里的 type 就来自本类

    实例化时要把 driver 传进来 —— 迁移后 driver 是一个 Playwright Page 对象：
        login_page = LoginPage(driver)
    """

    def __init__(self, driver=None, timeout=None):
        """
        :param driver:  Playwright Page 实例。BasePage 本身不开浏览器，
                        通常由 open_browser() 创建后传给它、或直接由子类持有。
        :param timeout: 元素定位默认超时时间（秒）。不传则读
                        config.ini -> [ci] element_timeout，再退到 DEFAULT_TIMEOUT。
        """
        self.driver = driver
        self.timeout = timeout or config.get_int('ci', 'element_timeout', DEFAULT_TIMEOUT)
        # iframe 支持：为空表示所有定位都在页面主文档上进行；
        # switch_iframe() 之后指向 frame_locator（见 switch_* 系列方法）
        self._frame = None

    # ====================================================================== #
    # 内部工具
    # ====================================================================== #
    def _surface(self):
        """当前定位操作的执行面：普通页面，或已切入的 iframe（FrameLocator）。【内部】"""
        return self._frame if self._frame is not None else self.driver

    # ====================================================================== #
    # 一、浏览器生命周期
    # ====================================================================== #
    def open_browser(self, url=None, browser=None, headless=None):
        """启动浏览器并访问被测地址，返回 Playwright Page 实例。【在用】

        浏览器类型和 url 都从 config.ini 的 [environment] 段读取，
        也可以由参数强制指定（命令行 --url / --browser 就是走这条）。

        :param url:      被测地址，为空则取 config.ini -> [environment] url
                         （会被环境变量 UI_ENVIRONMENT_URL 覆盖，CI 上就靠它切环境）
        :param browser:  chrome / firefox / edge，为空则取配置。
                         取值与迁移前一致：chrome -> Playwright chromium（自带浏览器），
                         firefox -> firefox，edge -> chromium channel='msedge'
        :param headless: 是否无头运行。不传则按
                         config.ini -> [ci] headless（可被 UI_CI_HEADLESS 覆盖）决定
        :return: Playwright Page 实例（同时保存在 self.driver）
        :raise ValueError: 未配置被测地址或浏览器取值非法时抛出 —— 这是 CI 首跑
                           最常见的错误，明确报错比后面一堆元素定位失败好排查得多
        :raise PlaywrightError: 浏览器打不开时向上抛，避免"静默失败"后
                               后续所有用例都莫名其妙报错
        """
        # .lower() 容错：配置里写 Chrome / CHROME 都能识别
        browser = (browser or config.get('environment', 'browser', 'chrome')).lower()
        url = url or config.get('environment', 'url')
        if headless is None:
            headless = config.get_bool('ci', 'headless', False)

        if not url:
            # 没有地址就没法测，直接抛出并说清怎么修
            raise ValueError(
                '未配置被测地址：请在 config.ini -> [environment] url 填写，'
                '或设置环境变量 UI_ENVIRONMENT_URL，或使用命令行参数 --url'
            )

        log1.info('读取浏览器配置,值为%s，无头模式=%s' % (browser, headless))
        log1.info('读取url,值为%s' % url)

        if browser not in ('chrome', 'firefox', 'edge'):
            # 配置写错时立刻失败，比后面每个元素都定位不到好排查得多
            raise ValueError('浏览器配置有误，应为 chrome / firefox / edge')

        chrome_binary = config.get('ci', 'chrome_binary', '')
        extra_args = self._extra_chrome_args()
        element_timeout = config.get_int('ci', 'element_timeout', DEFAULT_TIMEOUT)
        implicit_wait = config.get_int('ci', 'implicit_wait', 5)
        remote_url = config.get('ci', 'remote_url', '').strip()

        try:
            playwright = _ensure_playwright()

            # ---- 0. 远程浏览器模式（Selenium Grid / 云浏览器平台）----
            # 配了 [ci] remote_url 时，通过 CDP 连接远程浏览器而不是本机启动
            if remote_url:
                log1.info('连接远程浏览器：%s（无头=%s）' % (remote_url, headless))
                if browser == 'firefox':
                    browser_obj = playwright.firefox.connect(remote_url)
                else:
                    browser_obj = playwright.chromium.connect(remote_url)
                context = browser_obj.new_context(
                    viewport=_parse_viewport(config.get('tuning', 'viewport', '')) if headless else None,
                    locale=config.get('tuning', 'browser_locale', DEFAULT_LOCALE),
                )
                context.set_default_timeout(element_timeout * 1000)
                page = context.new_page()
                page.goto(url)
                self.driver = page
                self._frame = None
                return self.driver

            # ---- 1. 启动浏览器（每种浏览器一次启动，driver 即"当前标签页"）----
            if browser == 'chrome':
                browser_obj = playwright.chromium.launch(
                    headless=headless,
                    args=self._chromium_args(headless, extra_args),
                    executable_path=chrome_binary or None,
                )
                log1.info('打开chrome浏览器（无头=%s）' % headless)
            elif browser == 'edge':
                browser_obj = playwright.chromium.launch(
                    headless=headless,
                    channel='msedge',
                    args=self._chromium_args(headless, extra_args),
                )
                log1.info('打开edge浏览器（无头=%s）' % headless)
            else:  # firefox：chromium 专属参数不适用
                launch_kwargs = {'headless': headless}
                if chrome_binary:
                    launch_kwargs['executable_path'] = chrome_binary
                browser_obj = playwright.firefox.launch(**launch_kwargs)
                log1.info('打开firefox浏览器（无头=%s）' % headless)

            # ---- 2. 建上下文：固定视口 + 语言，等价迁移前的窗口尺寸与 --lang=zh-CN ----
            # 视口/语言都可配：[tuning] viewport、[tuning] browser_locale。
            # 有界面模式用 None（交给 --start-maximized 跟随真实窗口），
            # 无头模式用配置值（缺省 DEFAULT_VIEWPORT），保证截图尺寸稳定。
            viewport = _parse_viewport(config.get('tuning', 'viewport', '')) if headless else None
            context = browser_obj.new_context(
                viewport=viewport,
                locale=config.get('tuning', 'browser_locale', DEFAULT_LOCALE),
            )
            # Playwright 的动作/定位默认超时：迁移前的 element_timeout + implicit_wait
            # 在 Playwright 里统一由"自动等待 + 默认超时"承担，这里以 element_timeout 为准，
            # implicit_wait 保留读取（配置键不删，老流水线/文档仍可覆盖）
            context.set_default_timeout(element_timeout * 1000)
            self._implicit_wait = implicit_wait   # 仅记录，供日志与兼容旧配置语义

            # ---- 3. 开第一个标签页并访问被测地址 ----
            page = context.new_page()
            page.goto(url)
            self.driver = page
            self._frame = None
            log1.info('访问url')
            if headless:
                log1.info('设置视口 %sx%s' % (viewport['width'], viewport['height']))
            else:
                log1.info('浏览器最大化')
            log1.info('设置元素默认超时%d秒（兼容原隐式等待%d秒配置）' % (element_timeout, implicit_wait))
            return self.driver
        except ValueError:
            raise
        except PlaywrightError:
            log1.error('浏览器打开报错', exc_info=1)
            raise

    @staticmethod
    def _chromium_args(headless, extra_args):
        """组装 chromium 启动参数：稳健参数 + 配置附加参数 + 有界面时最大化窗口。"""
        args = list(HEADLESS_ARGS)
        if not headless:
            args.append('--start-maximized')
        args.extend(extra_args)
        return args

    @staticmethod
    def _extra_chrome_args():
        """解析 config.ini -> [ci] extra_chrome_args 里的附加启动参数。

        :return: 参数列表，例如 ['--no-sandbox', '--disable-images']
        支持用逗号、分号或换行分隔；未配置时返回空列表。

        注意：想用换行分隔时，必须按 ini 的「续行」写法（后续行要缩进），例如
            extra_chrome_args = --disable-images,
                --allow-insecure-localhost
        ini 不支持 \\n 转义，若第二行顶格书写，configparser 会直接抛解析错误。
        """
        raw = config.get('ci', 'extra_chrome_args', '') or ''
        return [item.strip() for item in re.split(r'[,;\n]', raw) if item.strip()]

    def dr_quit(self, wait=0):
        """关闭浏览器并释放 driver。【在用】

        由用例的 tearDownClass 调用，保证不管用例成败都能关掉浏览器，
        否则会残留浏览器进程。只关本次测试启动的 browser/context，
        不动进程级 Playwright 单例（由 atexit 统一回收）。

        :param wait: 关闭前先强制等待多少秒，默认 0（不等待）
        """
        if wait:
            self.my_sleep(wait)
        if self.driver is None:
            return
        try:
            context = self.driver.context
            browser_obj = context.browser
            context.close()
            if browser_obj is not None:
                browser_obj.close()
            log1.info('关闭浏览器')
        except PlaywrightError:
            log1.error('关闭浏览器报错', exc_info=1)
        except Exception:   # noqa: BLE001 - driver 可能已被外部销毁
            log1.error('关闭浏览器报错', exc_info=1)
        finally:
            # 无论是否报错都把引用清空，避免后续误用到已失效的 driver
            self.driver = None
            self._frame = None

    # ====================================================================== #
    # 二、元素定位
    # ====================================================================== #
    @staticmethod
    def _parse_selector(selector):
        """把 ['xpath', '//div'] 翻译成 ('xpath', '//div') 的可识别定位对。

        :param selector: 二元列表/元组，如 ['xpath', "//input[@type='text']"]
        :return: (定位方式小写键, 定位值)；格式非法或定位方式不支持时返回 (None, None)
        """
        try:
            by, value = selector[0], selector[1]
        except (TypeError, IndexError):
            log1.error('元素定位参数格式错误，应形如 ["xpath", "//div/span"]：%s' % (selector,))
            return None, None
        by = str(by).lower()
        if by not in BY_MAP:
            log1.error('元素定位方式错误，请使用 %s 之一' % '/'.join(BY_MAP))
            # 定位方式不认识时必须返回 (None, None)，与函数契约保持一致。
            # 早期只写日志就返回，会把 (None, '定位值') 这种半成品交给调用方，
            # 调用方一旦漏判 by 就会拿着 None 去定位而报出难懂的错。
            return None, None
        return by, value

    def get_element(self, selector, timeout=None, visible=False):
        """定位单个元素，找不到返回 None（不抛异常）。【在用，最核心的方法】

        :param selector: 元素描述，如 ['xpath', '//input[@type="text"]']
        :param timeout:  等待秒数。
                         不传  -> 用 self.timeout（默认 15 秒，带显式等待）
                         传 0  -> 立即判定，不等待（元素一定已在页面上时用，更快）
        :param visible:  True 表示要求元素「可见」而不只是「存在于 DOM」。
                         有些元素存在但被隐藏，用 visible=True 更贴近真实用户视角。
        :return: Playwright Locator（首次匹配），或 None

        新人注意：本方法返回 None 而不是抛异常，调用方必须自己判空，
        否则会得到 "AttributeError: 'NoneType' object has no attribute ..."。
        返回的 Locator 是惰性对象：此刻不保证元素还在，动作执行时才真正定位，
        好处是永不过期，DOM 变化后无需重新取。
        """
        by, value = self._parse_selector(selector)
        if by is None:
            return None
        try:
            pw_sel = _to_pw_selector(by, value)
        except ValueError:
            log1.error('元素定位方式错误，请使用 %s 之一' % '/'.join(BY_MAP))
            return None
        locator = self._surface().locator(pw_sel)
        try:
            if timeout == 0:
                # 快速通道：立即判定 DOM 中是否存在（visible=True 时再要求可见）
                if locator.count() == 0:
                    raise PlaywrightTimeoutError('元素不存在（立即判定）')
                target = self._pick_target(locator, pw_sel)
                if visible and not target.is_visible():
                    raise PlaywrightTimeoutError('元素存在但不可见（立即判定）')
            else:
                # 显式等待：元素一出现就继续，不等满超时
                locator.first.wait_for(
                    state='visible' if visible else 'attached',
                    timeout=(timeout or self.timeout) * 1000,
                )
                target = self._pick_target(locator, pw_sel)
            log1.info('元素定位成功。定位方式：%s，使用的值：%s' % (by, value))
            return target
        except (PlaywrightTimeoutError, PlaywrightError):
            log1.error('没有定位到元素,定位方式：%s，使用的值：%s' % (by, value), exc_info=1)
            return None

    def _pick_target(self, locator, pw_selector):
        """在多个候选里挑出**最可能**是调用方意图的那个。【内部】

        【为什么需要这个方法 —— 来自一次真实故障】
            定位表达式常常是若干分支的联合（xpath 的 `|`），例如
                //*[@id='uploader-btn']//i | //*[@id='uploader-btn']
            意图很明确：要**内部那个 i 图标**。但 xpath 联合与 Playwright
            的 `.first` 都是**文档顺序**，容器 label/div 永远排在它的
            内部元素前面，于是拿到的是外层容器 —— 点它等于点了个空壳，
            页面毫无反应。这类故障最难自查："元素找到了"却"没生效"。

        【踩过的坑：xpath 层面无法控制顺序】
            曾尝试把首分支写成 `(A)[1] | B` 强制它排到最前 ——
            实测无效，Playwright 会在拿到节点集后按文档序重排，
            `.first` 拿到的仍然是容器。`(A)` 不带索引也不行。
            结论：**顺序只能在框架层解决，xpath 层做不到**。

        【做法】
            先单独定位首个分支，命中就用它；没命中才退回整条表达式的 first。
            首个分支天然表达"调用方真正想要的"，兜底分支保证不会因为
            首选不存在而整个定位失败。

        :param locator:     Playwright Locator（整条联合表达式）
        :param pw_selector: 传给 Playwright 的完整选择器字符串
        :return: Locator（多数情况与 first 指向同一元素）
        """
        if pw_selector.startswith('xpath='):
            body = pw_selector[len('xpath='):]
            if ' | ' in body or body.startswith('|'):
                branches = [b.strip() for b in body.split('|') if b.strip()]
                if len(branches) > 1:
                    try:
                        # probe=True：这是框架为正确性补发的探测调用，
                        # 不是用例的业务动作，不该出现在"操作顺序"断言里。
                        # 真实 Playwright 的 locator() 不认这个参数，
                        # 框架靠"尝试传、TypeError 则去掉"来兼容（见 __init__ 注释）。
                        narrow = self._locator_probe('xpath=' + branches[0])
                        if narrow.count() > 0:
                            log1.info('定位命中多个候选，已选用首个分支：%s' % branches[0])
                            return narrow.first
                    except PlaywrightError:
                        # 首选分支定位失败不该影响主流程，退回整条表达式
                        pass
        return locator.first

    def _locator_probe(self, selector):
        """构造一个**探测用**的 Locator（不计入用例层看到的操作序列）。

        为什么需要这个方法：
            _pick_target 要在多个候选里挑首个分支，必须再发一次定位。
            这对正确性是必需的（xpath 层面控制不了顺序），
            但它不是用例的业务动作，不该污染「页面操作顺序」断言。

        实现：优先尝试带 probe 标记的调用（测试替身认这个参数），
        真实 Playwright 不认会抛 TypeError，此时退回普通调用 ——
        代价是这次探测会被记录到日志，但不影响功能正确性。
        """
        try:
            return self._surface().locator(selector, probe=True)
        except TypeError:
            return self._surface().locator(selector)

    def get_elements(self, selector, timeout=None):
        """定位一组元素（如表格的每一行），未找到返回空列表。【预留】

        :return: Locator 列表；没有匹配项时返回 []，调用方无需判空
        """
        by, value = self._parse_selector(selector)
        if by is None:
            return []
        try:
            pw_sel = _to_pw_selector(by, value)
        except ValueError:
            return []
        locator = self._surface().locator(pw_sel)
        if timeout != 0:
            try:
                # 先等到至少出现一个，再整体取；避免页面没渲染完就取到空列表
                locator.first.wait_for(
                    state='attached', timeout=(timeout or self.timeout) * 1000)
            except (PlaywrightTimeoutError, PlaywrightError):
                log1.error('没有定位到任何元素,定位方式：%s，使用的值：%s' % (by, value))
                return []
        return locator.all()

    def wait_element(self, selector, timeout=None, visible=False):
        """显式等待元素出现，语义上比 get_element 更强调"等待"。【预留】

        :return: Locator 或 None
        """
        return self.get_element(selector, timeout or self.timeout, visible=visible)

    def scroll_into_view(self, element):
        """把元素滚动到浏览器可视区域中间。【在用（click 内部调用）】

        为什么需要：元素在页面下方时直接点击可能点不到，先滚动过去就能点。
        Playwright 的 click 本身也会自动滚动，这里是显式兜底（与原实现一致）。
        """
        try:
            element.scroll_into_view_if_needed()
        except (PlaywrightError, AttributeError):
            log1.error('滚动到元素失败', exc_info=1)

    def js_click(self, element):
        """用 JS 强制点击元素。【在用（click 降级时调用）】

        适合被其他元素遮挡、或用原生 click 报错的场景。
        缺点：绕过了真实的用户点击行为，不会触发部分浏览器原生校验，
        所以只作为兜底，不要当默认手段。
        """
        try:
            element.evaluate('el => el.click()')
            log1.info('JS点击成功')
        except PlaywrightError:
            log1.error('JS点击失败', exc_info=1)
            raise

    @staticmethod
    def isdisplayed(element):
        """判断元素是否可见；element 为 None 时返回 False。【预留】

        可以简化成一句断言：self.assertTrue(page.isdisplayed(element))
        """
        if element is None:
            return False
        try:
            return element.is_visible()
        except PlaywrightError:
            return False

    # ====================================================================== #
    # 三、常用交互
    # ====================================================================== #
    def type(self, selector, value, timeout=None):
        """清空输入框并输入内容。【在用】

        :param selector: 输入框的元素描述
        :param value:    要输入的内容
        :raise NoSuchElementException: 输入框不存在时抛出（而不是静默跳过），
               这样失败会明确记在测试报告里
        """
        element = self.get_element(selector, timeout if timeout is not None else self.timeout)
        if element is None:
            raise NoSuchElementException('输入框不存在，无法输入：%s' % (selector,))
        try:
            element.clear()
        except PlaywrightError:
            # 有些前端框架的输入框 clear() 不生效，退化成"全选 + 删除"
            element.press('Control+a')
            element.press('Delete')
        try:
            # fill 是 Playwright 的原子填值：会触发 input 事件，Vue/React 表单可感知
            element.fill(value)
            log1.info('输入的内容：%s' % value)
        except PlaywrightError:
            # 输入失败立刻截图，方便回溯当时页面是什么样
            log1.error('内容输入报错', exc_info=1)
            self.get_img()
            raise

    def click(self, selector, timeout=None):
        """点击元素，失败自动降级为 JS 点击。【在用】

        内部流程：定位（带等待）-> 滚动到可视区 -> 原生点击
                  -> 报错则改用 JS 点击
        """
        element = self.get_element(selector, timeout if timeout is not None else self.timeout)
        if element is None:
            raise NoSuchElementException('元素不存在，无法点击：%s' % (selector,))
        self.scroll_into_view(element)
        try:
            element.click()
            log1.info('点击元素成功')
        except PlaywrightError:
            log1.error('常规点击失败，降级为JS点击', exc_info=1)
            self.js_click(element)

    # ====================================================================== #
    # 稳定性增强：自动重试（降低 CI 上的偶发失败 flaky）
    #   Web 自动化最常见的偶发失败：元素还没渲染完、点击被临时遮罩挡住。
    #   对这些"重试一次大概率就好"的瞬时错误，框架层统一兜底重试，
    #   避免用例本身到处写 try/except。
    #   Playwright 的 Locator 永不过期，不存在"旧句柄失效"类问题；
    #   这里只兜底 TimeoutError / 其它 Playwright 异常这类瞬时错误。
    # ====================================================================== #
    # 默认对「瞬时错误」重试 2 次（共尝试 3 次）；可被配置 [ci] retry_times 覆盖
    DEFAULT_RETRY = 2

    def _retry_times(self):
        """读取重试次数配置，缺省用 DEFAULT_RETRY。"""
        return config.get_int('ci', 'retry_times', self.DEFAULT_RETRY)

    def retry(self, func, *args, **kwargs):
        """对瞬时错误自动重试执行 func，成功即返回，全部失败才抛出。

        会被重试的异常：Playwright 的 TimeoutError 及其它 Error
        （点击被挡、元素未就绪、页面导航中被打断等）。
        AssertionError / ValueError 这类「业务断言或参数错误」不重试 ——
        重试它们只会浪费时间，不会改变结果。

        :param func: 要执行的函数（通常是 lambda: self.click(sel)）
        :return: func 的返回值
        """
        times = self._retry_times()
        last_exc = None
        for attempt in range(times + 1):
            try:
                return func(*args, **kwargs)
            except (PlaywrightTimeoutError, PlaywrightError) as e:
                last_exc = e
                if attempt < times:
                    log1.warning('操作失败，第 %d/%d 次重试：%s' % (attempt + 1, times, e))
                    # 给页面一点喘息时间再重试（[tuning] retry_interval 可调）
                    self.my_sleep(_tuning('retry_interval', DEFAULT_RETRY_INTERVAL))
                else:
                    log1.error('重试 %d 次后仍失败：%s' % (times, e), exc_info=1)
        raise last_exc

    def safe_click(self, selector, timeout=None):
        """带重试的点击：被遮挡/未就绪等瞬时错误会自动重试。【推荐用于易 flaky 的步骤】"""
        return self.retry(lambda: self.click(selector, timeout))

    def safe_type(self, selector, value, timeout=None):
        """带重试的输入。"""
        return self.retry(lambda: self.type(selector, value, timeout))

    def input_enter(self, txt, value, name='xpath', selector=None):
        """在输入框里输入内容后按回车（常用于搜索框）。【预留】

        :param txt:      要输入的内容
        :param value:    定位值，如 "//input[@id='q']"
        :param name:     定位方式字符串，默认 xpath
        :param selector: 也可直接传完整元素描述，传了它则忽略 name/value
        """
        if selector is not None:
            element = self.get_element(selector)
        else:
            element = self.get_element([str(name).lower(), value])
        if element is None:
            raise NoSuchElementException('输入框不存在，无法输入：%s=%s' % (name, value))
        element.fill(txt)
        # 给前端一点时间响应，再敲回车（[tuning] enter_settle 可调）
        self.my_sleep(_tuning('enter_settle', DEFAULT_ENTER_SETTLE))
        element.press('Enter')

    @staticmethod
    def my_sleep(seconds):
        """强制等待固定秒数。【在用】

        只在前端有动画、异步渲染等"无法用条件判断"的场景使用。
        能判断出条件时优先用 get_element(timeout=...)，它会提前返回、更快也更稳。
        """
        time.sleep(seconds)
        log1.info('强制等待%s秒' % seconds)

    def page_wait(self, key, default):
        """用例级"页面节奏"等待：秒数从 config.ini -> [timing] 读取。【在用】

        为什么存在：用例里"点完登录停 2 秒、上传后停 5 秒"这类等待时长
        属于可调参数 —— 慢环境要调大、快环境要调小，不该写死在代码里。
        :param key:     [timing] 段的键名，如 'workbench_render'
        :param default: 未配置时的技术默认秒数（允许保留，属参数调优非业务数据）
        """
        self.my_sleep(config.get_float('timing', key, default))

    def get_title(self):
        """获取当前窗口的页面标题（浏览器标签上那行字）。【在用】

        本工程的用例主要靠它做断言 —— 每次跳转后校验 title 是否符合预期。
        注意：Playwright 里 title() 是方法，不是属性。
        """
        title = self.driver.title()
        log1.info('当前打开的title是：%s' % title)
        return title

    def get_text(self, selector, timeout=None):
        """获取元素的文本内容（页面上看得见的字）。【预留】

        常用于断言按钮文案、表格单元格内容等。
        """
        element = self.get_element(selector, timeout if timeout is not None else self.timeout)
        if element is None:
            raise NoSuchElementException('元素不存在，无法获取文本：%s' % (selector,))
        text = element.inner_text()
        log1.info('获取的text：%s' % text)
        return text

    def get_value(self, selector, timeout=None):
        """获取表单控件当前的 value（输入框里用户看到的那串字）。【通用】

        与 get_text() 的区别（新手最容易搞混的一对）：
            get_text()  读元素中间**显示的文字**（inner_text）—— 用在按钮、标题、表格单元格
            get_value() 读控件的 **value 属性** —— 用在 input / textarea

        为什么需要它：只"输入成功没报错"不等于"值真的填进去了"，
        校验"输入是否生效""重置后是否真的清空"必须读到值才算验证过。

        :return: value 字符串；元素没有 value 属性时返回 None
        """
        element = self.get_element(selector, timeout if timeout is not None else self.timeout)
        if element is None:
            raise NoSuchElementException('元素不存在，无法获取它的值：%s' % (selector,))
        try:
            value = element.input_value()
        except PlaywrightError:
            # 非表单控件没有 input_value，退化为读属性（与原 get_attribute('value') 语义一致，
            # 属性不存在时返回 None）
            value = element.get_attribute('value')
        log1.info('获取的value：%s' % value)
        return value

    def goto(self, url=None):
        """让浏览器导航到指定地址；不传 url 则用配置里的 [environment] url。

        为什么需要这个方法：以前只有 open_browser() 内部会导航，
        想在同一个浏览器实例里换地址（比如用例中途跳到另一个系统），
        只能直接写 self.driver.goto()，等于绕过分层。
        收进基类后，页面对象层就能安全表达"跳到某地址"这个动作。

        :param url: 目标地址；留空则读 config.ini -> [environment] url
        :raise ConfigMissingError: url 没传且配置里也没配地址
        """
        if not url:
            url = Config.require('environment', 'url', '导航目标地址（可传参传入）')
        self.driver.goto(url)
        log1.info('导航到：%s' % url)

    def refresh(self):
        """刷新当前页面（等价迁移前用例里的 driver.refresh()）。【在用（testLogin 用例6）】

        迁移时把这条从用例层收回到基类：用例不再直接摸 driver，
        符合"页面对象/基类管操作"的分层规则（README 已知限制里就建议补这个方法）。
        """
        self.driver.reload()
        log1.info('刷新页面')

    def use_js(self, js):
        """执行任意 JS 脚本。【预留】

        用途举例：直接改元素属性、去掉只读限制、滚动整页等。
        """
        try:
            self.driver.evaluate(js)
            log1.info('js执行成功，js内容为：%s' % js)
        except PlaywrightError:
            log1.error('js执行报错', exc_info=1)
            raise

    # ====================================================================== #
    # 四、窗口 / frame 切换（多标签页、内嵌 iframe 的必用技能）
    # ====================================================================== #
    def switch_menue(self, parentelement, secelement, targetelement):
        """依次点击一级、二级、三级菜单完成页面切换。【预留】

        :param parentelement: 一级菜单元素
        :param secelement:    二级菜单元素
        :param targetelement: 三级菜单元素
        """
        self.my_sleep(_tuning('switch_menu_wait', DEFAULT_MENU_WAIT))
        try:
            # 先回到最外层文档，避免上一次操作还停留在 iframe 里导致定位失败
            self.switch_default_content()
            self.click(parentelement)
            log1.info('成功点击一级菜单：%s' % parentelement)
            self.click(secelement)
            log1.info('成功点击二级菜单：%s' % secelement)
            self.click(targetelement)
            log1.info('成功点击三级菜单：%s' % targetelement)
        except BaseException:
            log1.error('切换菜单报错', exc_info=1)

    def switch_iframe(self, selector):
        """切换到指定的 iframe 内部（切换后定位范围只剩这个框架）。【预留】

        迁移实现：把 self._frame 指向 Playwright 的 FrameLocator，
        之后所有 get_element/click/type 都自动在框架内定位。
        切进去之后想操作主页面，必须先 switch_default_content() 回来。
        """
        element = self.get_element(selector)
        if element is None:
            raise NoSuchElementException('frame 不存在，无法切换：%s' % (selector,))
        by, value = self._parse_selector(selector)
        self._frame = self.driver.frame_locator(_to_pw_selector(by, value))
        log1.info('切换frame成功')

    # 兼容旧命名：原工程把 iframe 拼成了 ifarme，老代码可能还在用这个别名
    switch_ifarme = switch_iframe

    def switch_default_content(self):
        """从 iframe 回到最外层文档。【预留】"""
        self._frame = None
        log1.info('回到最外层文档')

    def get_handle(self):
        """获取当前窗口的唯一标识 handle。【预留】

        迁移说明：Playwright 没有字符串 handle 的概念，
        每个标签页就是一个 Page 对象，这里直接返回当前 Page 作为"句柄"，
        可原样传回 switch_other_window / chage_handle 实现切回。
        """
        return self.driver

    def chage_handle(self, handle):
        """切换到指定的其他窗口（旧命名，现保留兼容）。【预留】

        拼写 typo 已修正为 switch_other_window，新代码请用后者。
        """
        return self.switch_other_window(handle)

    def switch_other_window(self, handle=None, timeout=None):
        """切换到另一个标签页并把 self.driver 指向它，返回新窗口的"handle"。【在用】

        典型场景：从统一门户点进生产系统，页面在新标签页打开，
        必须切过去才能继续操作（本工程 test_2select_scxt 就是这么做的）。

        :param handle: 要"避开"的窗口（即当前页）；不传则自动取 self.driver。
                       迁移后 handle 就是 Page 对象本身（get_handle 的返回值）。
        :param timeout: 等新标签页出现的最长秒数；默认取本实例 timeout（[ci] element_timeout）。
                        刚点完"新窗口打开"的元素时，新页面对象登记到 context.pages
                        有毫秒级的协议延迟，看一眼就判失败会偶发"找不到窗口"，
                        因此这里轮询等待，直到出现或超时。
        :return: 新窗口的 Page（作为 handle）；超时仍没有其他窗口时返回 None

        注意：切换只影响本 BasePage 实例的 driver。
        用例层应通过页面对象继续操作（而不是持有 open_browser 时的旧 driver 引用），
        这也是把 refresh() 收进基类的原因。
        """
        current = handle or self.driver
        context = self.driver.context
        budget = self.timeout if timeout is None else timeout
        others = [page for page in context.pages if page is not current]
        while not others and budget > 0:
            # ★ 必须用 Playwright 的 wait_for_timeout，不能用 time.sleep：
            #   同步 API 的协议消息是在 wait_for_timeout 这类阻塞调用内部被泵进
            #   事件循环处理的。新标签页其实早已创建，但"已注册"的消息没泵就
            #   不会更新 context.pages —— time.sleep 空睡，睡多久都读不到新页。
            pump = config.get_float('tuning', 'window_poll_interval', DEFAULT_WINDOW_POLL)
            self.driver.wait_for_timeout(int(pump * 1000))
            budget -= pump
            others = [page for page in context.pages if page is not current]
        if others:
            target = others[0]
            target.bring_to_front()
            self.driver = target
            self._frame = None
            log1.info('切换到新窗口（共 %d 个标签页）' % len(context.pages))
            return target
        log1.error('等待%.1f秒后仍未找到其他可切换的窗口' % (
            self.timeout if timeout is None else timeout))
        return None

    # ====================================================================== #
    # 五、文件上传（本工程业务重点：上传视频到素材库）
    # ====================================================================== #
    def files_upload(self, selector, file_path=None):
        """通过给 input[type=file] 设置文件来选择待上传文件。【在用，推荐方案】

        为什么这么做：点击"上传"按钮会弹出操作系统原生「打开」对话框，
        那是浏览器外的窗口，Web 自动化管不了，只能借助 autoit 这类工具模拟键盘。
        而给隐藏的 file 控件直接 set_input_files(路径) 可以绕过整个弹窗流程，
        更快、更稳、不依赖 Windows，且在无头模式下同样可用。

        :param selector:  file 控件的元素描述，通常是 Element1.FileInput
        :param file_path: 文件绝对路径；不传则读 config.ini -> [testcase] upload_file
        :raise FileNotFoundError: 文件不存在时抛出，避免用例"假成功"
        :raise ProductionWriteBlocked: 生产环境只读模式下拦截上传操作
        """
        # 生产环境安全防护：只读模式下禁止上传文件（写操作）
        from Common.envGuard import allow_write
        allow_write('上传文件')
        file_path = file_path or config.get('testcase', 'upload_file')
        if not file_path:
            raise ValueError('未配置上传文件路径（config.ini -> [testcase] upload_file）')
        # expanduser 支持 ~/ 开头；abspath 保证浏览器拿到的是绝对路径
        file_path = os.path.abspath(os.path.expanduser(file_path))
        if not os.path.exists(file_path):
            raise FileNotFoundError('上传文件不存在：%s' % file_path)
        element = self.get_element(selector)
        if element is None:
            raise NoSuchElementException('未找到文件上传控件：%s' % (selector,))
        element.set_input_files(file_path)
        log1.info('上传文件：%s' % file_path)
        # 等上传组件响应文件选择（[tuning] upload_settle 可调）
        self.my_sleep(_tuning('upload_settle', DEFAULT_UPLOAD_SETTLE))

    def upload_by_autoit(self, file_path=None, window_title=None):
        """备选方案：用 autoit 操作 Windows「打开」对话框。【在用（降级分支）】

        仅在 files_upload 失败时作为兜底，需要 pip install PyAutoIt。
        本方法与浏览器无关（纯操作 Windows 对话框），迁移中未做改动；
        ⚠️ 无头/无桌面环境（CI 容器）不可用，流水线请保持默认的直接赋值上传。

        :param window_title: 对话框标题；不传则读 config.ini -> [testcase]
                             upload_dialog_title（业务/环境数据，走 autoit 时必填。
                             中文系统是「打开」，英文系统是 Open）
        超时/喘息秒数走 [tuning] autoit_timeout / autoit_input_settle /
        autoit_click_settle（技术参数，允许默认值）。
        """
        if not AUTOIT_AVAILABLE:
            raise RuntimeError('未安装 PyAutoIt，无法使用 autoit 上传；请改用 files_upload()')
        if not window_title:
            window_title = config.require('testcase', 'upload_dialog_title',
                                          'autoit 方案需要系统文件对话框的窗口标题'
                                          '（中文系统是「打开」，英文系统是 Open）')
        file_path = file_path or config.require('testcase', 'upload_file')
        timeout = config.get_int('tuning', 'autoit_timeout', DEFAULT_AUTOIT_TIMEOUT)
        autoit.win_wait_active(window_title, timeout)                  # 等对话框出现
        autoit.win_activate(window_title)                              # 激活到前台
        autoit.control_focus(window_title, '[class:Edit; instance:1]')  # 聚焦文件名输入框
        time.sleep(_tuning('autoit_input_settle', DEFAULT_AUTOIT_INPUT_SETTLE))
        autoit.control_send(window_title, '[class:Edit; instance:1]', file_path)  # 输入路径
        time.sleep(_tuning('autoit_click_settle', DEFAULT_AUTOIT_CLICK_SETTLE))
        autoit.control_click(window_title, '[class:Button; instance:1]')         # 点「打开」
        log1.info('autoit 上传文件：%s' % file_path)

    # ====================================================================== #
    # 六、截图与弹窗
    # ====================================================================== #
    def get_img(self):
        """截取当前页面并保存到 <截图目录>/日期/ 下，返回图片路径。【在用】

        截图根目录名可配：config.ini -> [paths] img_dir（默认 img）。
        命名规则：img/2026-09-25/2026-09-25 120718.png（秒级时间戳，天然不重名）
        用例断言失败时会自动调用它，是最常用的排错线索。

        ★ 路径为什么从 dir_config 取而不是自己拼（验收实测发现的缺陷）：
          以前这里自己 join 一次 img_dir，绕过了 runContext 的产物隔离 ——
          结果报告/日志都进了 runId 子目录、唯独截图留在 img/ 根下，
          并发构建时截图互相覆盖。产物路径必须单一来源（dir_config），
          隔离逻辑才不会出现"漏网之鱼"。
        """
        from Common.dir_config import img_dir     # 函数内 import：取运行时最新值
        date_file_path = os.path.join(img_dir, time.strftime('%Y-%m-%d'))
        if not os.path.exists(date_file_path):
            os.makedirs(date_file_path)
        local_time = time.strftime('%Y-%m-%d %H%M%S', time.localtime(time.time()))
        jt_path = os.path.join(date_file_path, local_time + '.png')
        try:
            self.driver.screenshot(path=jt_path)
            log1.info('截图保存成功：%s' % jt_path)
            return jt_path
        except BaseException:
            # 截图本身失败不应该中断用例，只记日志
            log1.error('截图失败', exc_info=1)
            return None

    def popout(self, timeout=None):
        """接受（点击"确定"）浏览器原生 alert 弹窗。【预留】

        注意：alert 属于浏览器原生弹窗，不是页面 DOM 元素，直接 find_element 找不到。
        迁移实现：Playwright 用事件模型处理弹窗 —— 这里等待弹窗事件出现再接受。
        （若系统频繁弹窗，可在拿到 page 后自行 page.on('dialog', lambda d: d.accept()) 自动接除。）

        :param timeout: 等待弹窗出现的秒数；不传则取 [tuning] dialog_timeout（默认 5）
        """
        if timeout is None:
            timeout = config.get_float('tuning', 'dialog_timeout', DEFAULT_DIALOG_TIMEOUT)
        dialog = self.driver.wait_for_event('dialog', timeout=timeout * 1000)
        dialog.accept()
        log1.info('已接受 alert 弹窗')

    # ====================================================================== #
    # 七、跨系统通用能力（让本框架能适配任意主流 Web 系统）
    #   设计目标：党政 / 财务 / ERP / 商城 / 数据中台等系统虽然业务不同，
    #   但底层 UI 模式高度相似 —— 都有"按文字点按钮/菜单、按占位符填输入框、
    #   选下拉、等加载遮罩消失、按单元格定位表格行"。把这些通用模式沉淀在这里，
    #   接入一个新系统时，页面对象直接调用即可，无需为每个系统重复造轮子。
    #   全部方法与现有方法一样：找不到元素返回 None / 记日志，不随意抛异常。
    # ====================================================================== #
    def _xpath_norm(self, text):
        """把文本里的单引号转义成 XPath 兼容写法（concat 拼接），避免引号破坏 xpath。"""
        if "'" not in text:
            return "'%s'" % text
        # 含单引号时，用 concat 把每段包成双引号字符串再拼起来
        parts = text.split("'")
        return "concat(%s)" % ", \"'\", ".join("'%s'" % p for p in parts)

    def get_element_by_text(self, text, tag='*', timeout=None, contains=False):
        """按「可见文本」定位元素（最通用、最不依赖具体 DOM 结构的定位方式）。【通用】

        几乎所有系统都能用它点按钮、菜单、tab、链接。优先于脆弱的绝对 xpath。
        :param text:     要匹配的可见文本
        :param tag:      限定标签，如 'button'/'a'/'span'，默认 '*' 不限
        :param timeout:  等待秒数，默认取类属性 timeout
        :param contains: True=文本包含即可（模糊），False=精确等于文本
        :return: Locator 或 None
        """
        timeout = timeout if timeout is not None else self.timeout
        op = 'contains' if contains else 'normalize-space()='
        if contains:
            xp = "//%s[contains(normalize-space(.), %s)]" % (tag, self._xpath_norm(text))
        else:
            xp = "//%s[normalize-space(.)=%s]" % (tag, self._xpath_norm(text))
        try:
            locator = self._surface().locator('xpath=' + xp).first
            locator.wait_for(state='attached', timeout=timeout * 1000)
            log1.info('按文本定位成功：text=%s tag=%s' % (text, tag))
            return locator
        except (PlaywrightTimeoutError, PlaywrightError):
            log1.error('按文本未找到元素：text=%s tag=%s' % (text, tag))
            return None

    def click_by_text(self, text, tag='*', timeout=None, contains=False):
        """按可见文本点击元素（按钮/菜单/链接/tab）。【通用，推荐】

        内部先尝试原生 click()，失败自动降级为 JS 点击（与 click() 一致）。
        """
        el = self.get_element_by_text(text, tag=tag, timeout=timeout, contains=contains)
        if el is None:
            return False
        try:
            el.click()
            log1.info('按文本点击成功：%s' % text)
            return True
        except PlaywrightError:
            try:
                el.evaluate('el => el.click()')
                log1.info('按文本点击（JS 降级）成功：%s' % text)
                return True
            except PlaywrightError:
                log1.error('按文本点击失败：%s' % text, exc_info=1)
                return False

    def get_element_by_attr(self, attr, value, tag='*', timeout=None):
        """按「属性=值」定位元素，最常用于按 placeholder 找输入框。【通用】

        例如 get_element_by_attr('placeholder', '请输入用户名') 可跨系统复用，
        因为 placeholder 文案通常稳定，比绝对 xpath 抗改版。
        """
        timeout = timeout if timeout is not None else self.timeout
        xp = "//%s[@%s=%s]" % (tag, attr, self._xpath_norm(value))
        try:
            locator = self._surface().locator('xpath=' + xp).first
            locator.wait_for(state='attached', timeout=timeout * 1000)
            log1.info('按属性定位成功：@%s=%s' % (attr, value))
            return locator
        except (PlaywrightTimeoutError, PlaywrightError):
            log1.error('按属性未找到元素：@%s=%s' % (attr, value))
            return None

    def type_by_placeholder(self, placeholder, value, clear=True, tag='input'):
        """按 placeholder 定位输入框并输入（适用于绝大多数表单页）。【通用】

        :param placeholder: 输入框占位提示文案
        :param value:       要填的值
        :param clear:       输入前是否清空（默认清空；Playwright 的 fill 本身就是替换，
                            这一步只为与迁移前行为完全一致）
        """
        el = self.get_element_by_attr('placeholder', placeholder, tag=tag)
        if el is None:
            raise NoSuchElementException('未找到 placeholder=%s 的输入框' % placeholder)
        if clear:
            try:
                el.clear()
            except PlaywrightError:
                pass
        el.fill(value)
        log1.info('按 placeholder 输入：%s -> %s' % (placeholder, value))

    def select_option(self, selector, text=None, value=None, index=None):
        """操作原生 <select> 下拉框，按可见文本/值/序号三选一。【通用】

        注意：只适用于原生 select；若系统用 div 模拟下拉（很多后台如此），
        请改用 click_by_text 先展开再点选项。
        """
        el = self.get_element(selector)
        if el is None:
            raise NoSuchElementException('未找到下拉框：%s' % (selector,))
        if text is not None:
            el.select_option(label=text)
            log1.info('下拉选择(文本)：%s' % text)
        elif value is not None:
            el.select_option(value=value)
            log1.info('下拉选择(值)：%s' % value)
        elif index is not None:
            el.select_option(index=index)
            log1.info('下拉选择(序号)：%s' % index)
        else:
            raise ValueError('select_option 必须传 text / value / index 之一')

    def wait_loading_gone(self, selector=None, timeout=None, poll_frequency=None):
        """等待「加载遮罩/转圈」消失后再继续。【通用】

        几乎所有后台系统点查询/保存后都会出现 loading 遮罩，
        不等待就去操作下一个元素极易偶发失败。

        :param selector: 遮罩元素的定位描述；不传则读 [tuning] loading_selector
                         （缺省用通用特征：class 含 loading 或有"加载中"文字）
        :param timeout:  最长等待秒数；不传取 [ci] element_timeout
        :param poll_frequency: 轮询步长（秒）；不传取 [tuning] loading_poll_interval
        """
        if selector is None:
            # 通用特征可用配置整体替换。本解析器关闭了行内注释与 % 插值，
            # 值里的引号、#、; 都可以放心写；值不要跨行（ini 续行需缩进，易出错）
            selector = ['xpath', config.get('tuning', 'loading_selector',
                                            DEFAULT_LOADING_SELECTOR)]
        if timeout is None:
            timeout = self.timeout
        if poll_frequency is None:
            poll_frequency = _tuning('loading_poll_interval', DEFAULT_LOADING_POLL)
        by, value = self._parse_selector(selector)
        if by is None:
            return False
        locator = self._surface().locator(_to_pw_selector(by, value))
        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                if locator.count() == 0 or not locator.first.is_visible():
                    log1.info('加载遮罩已消失')
                    return True
            except PlaywrightError:
                log1.info('加载遮罩已消失')
                return True
            time.sleep(poll_frequency)
        # 超时不抛异常（避免用例因网络慢整体崩），仅记日志，交给后续断言去判定
        log1.error('等待加载遮罩消失超时(%ss)，继续执行后续步骤' % timeout)
        return False

    def get_table_rows(self, table_selector=None):
        """获取页面上所有 <tr> 行（表格数据断言/按行操作的基础）。【通用】

        :param table_selector: 限定某个表格的定位描述；不传则取全页所有 tr
        :return: Locator 列表
        """
        if table_selector is None:
            return self._surface().locator('xpath=//tr').all()
        tbl = self.get_element(table_selector)
        if tbl is None:
            return []
        return tbl.locator('xpath=.//tr').all()

    def find_row_by_cell_text(self, text, table_selector=None):
        """在表格中查找「某个单元格包含指定文本」的那一行，返回该行的 Locator。【通用】

        数据中台/ERP/商城的列表页都靠它定位要操作的记录（点编辑/删除前先找到行）。
        """
        for tr in self.get_table_rows(table_selector):
            try:
                row_text = tr.inner_text()
            except PlaywrightError:
                continue
            if text in (row_text or ''):
                return tr
        log1.error('表格中未找到含文本 %s 的行' % text)
        return None

    def hover(self, selector):
        """鼠标悬停到元素上（触发下拉菜单、二级导航展开）。【通用】"""
        el = self.get_element(selector)
        if el is None:
            raise NoSuchElementException('未找到要悬停的元素：%s' % (selector,))
        el.hover()
        log1.info('已悬停到元素：%s' % (selector,))

    def scroll_to(self, selector):
        """滚动到元素可见（列表很长时，目标元素在视口外点不到）。【通用】"""
        el = self.get_element(selector)
        if el is None:
            raise NoSuchElementException('未找到要滚动到的元素：%s' % (selector,))
        el.scroll_into_view_if_needed()
        log1.info('已滚动到元素：%s' % (selector,))
