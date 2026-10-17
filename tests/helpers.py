# -*- coding: utf-8 -*-
"""
==========================================================================
 单元测试公共基座（桩对象 + 隔离工具）—— Playwright 版
==========================================================================

【这个文件提供什么】
    1. FakeElement / FakeLocator / FakePage / FakePlaywrightFactory ——
       假的 Playwright 元素与浏览器。让 BasePage 的代码"以为自己真的在操作浏览器"，
       但实际只是记录调用，从而使单元测试可以在无浏览器、无桌面的环境下毫秒级跑完。
       桩对象的接口形状与 playwright.sync_api 一致，BasePage 不需要知道真假。
    2. patch_config()  —— 把工程配置重定向到临时 config.ini，测读写不碰真文件。
    3. patch_dirs()    —— 把 Logs / img / Report / 用例目录重定向到临时目录。
    4. make_sandbox()  —— 临时沙盒工程（config.ini + 用例包），
       供 runner.run() 的端到端测试使用，测完即弃。
    5. load_module()   —— 加载工程根目录下的顶层模块（run.py / entrance.py）。

【为什么必须隔离】
    工程的 config.ini 里是生产环境地址与真实账号；Logs/img/Report 是真实产物。
    单元测试如果直接操作它们，轻则污染工程目录，重则误触发真实业务动作。
    所有隔离都在这里做掉，具体测试文件里只需 with 一下。

【新人写新测试时的模板】
    class TestXxx(unittest.TestCase):
        def test_something(self):
            with patch_config() as ini:          # 需要配置时
                with patch_dirs() as root:       # 需要文件产出时
                    page = FakePage()            # 假浏览器（Playwright Page 形状）
                    bp = BasePage(driver=page)
                    ...
==========================================================================
"""

import atexit
import contextlib
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

# --- 让测试无论从哪个目录启动，都能 import 到工程模块 ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from playwright.sync_api import (   # noqa: E402  必须在 sys.path 处理之后导入
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from Common.configOverWriter import ConfigOverWrite   # noqa: E402


# ==========================================================================
# 临时配置文件
# ==========================================================================
# 这份配置覆盖了工程用到的全部段位，且刻意放了几个"容易踩坑"的值：
#   - login_password 含 %      -> 验证 ConfigParser 的 % 插值确实被关掉
#   - upload_file 用 ~ 开头    -> 验证 get_upload_file 会展开用户目录
#   - [addressed] 键是中文     -> 验证键名大小写/中文都不会被改写
DEFAULT_INI = """\
[environment]
url = http://test-env.local/sso-portal
browser = chrome

[testcase]
login_username = unit_user
login_password = p%ss%word
upload_file = ~/video/demo.mp4
upload_mode = set_input_files
upload_dialog_title = 打开
video_author = 张三
phone = 13800000000
reporting = 深圳
query_keyword = 单元关键字

[assert]
title_login = 单元门户
title_workbench = 单元工作台
title_material = 单元素材库
match_mode = contains

[system]
username_placeholder = 请输入用户名
password_placeholder = 请输入密码
login_button_text = 登录
success_text =
element_timeout = 7

# 元素定位：验证"界面文案进配置、代码零硬编码"这条约定。
# 故意用与工程默认值不同的文案，测试才能证明值真的来自配置而不是写死。
[locators]
username_input = 单元用户
password_input = 单元密码
btn_login = 单元登录
btn_enter_system = 单元发布
menu_first_level = 单元内容
menu_second_level = 单元素材
field_keyword = 单元关键字|单元关键词
btn_query = 单元查询
btn_reset = 单元重置
btn_submit_report = 单元报送
combo_first_trial = 单元初审
btn_save = 单元保存

[case_names]
login = 单元登录成功
material = 单元进入素材库
controls = 单元控件齐全
keyword_input = 单元关键字输入

[report]
title = 单元测试报告
filename = unit-report.html
tester = unit-tester
description = 单元测试描述

[entrance]
title = 备用入口测试报告
description = 备用入口描述

[ci]
headless = false
element_timeout = 7
implicit_wait = 2
smoke_cases = test_1login,test_2select_scxt
junit_xml = Report/junit-results.xml
extra_chrome_args =
chrome_binary =

[paths]
report_dir = Report
junit_filename = junit-results.xml
img_dir = img

[sender]
username = 单元测试发件人
email = sender@example.com
password = smtp-token

[addressed]
张三 = zhangsan@example.com
李四 = lisi@example.com
"""


# ==========================================================================
# 临时目录管理
#   单元测试会创建大量临时目录。若每次都 mkdtemp + rmtree，
#   Windows 上光删目录就能占到总耗时的一大半。所以这里改成：
#     - 配置用的临时根目录全进程共享，只增删 ini 文件本身
#     - 其余临时目录统一登记到 _TEMP_DIRS，进程退出时一次性清理
#   效果：整套 328 项测试的耗时从约 110 秒降到约 25 秒（同一台机器实测）。
#
#   ★ 凡是新建临时目录的地方都必须登记，否则会在系统临时目录里留垃圾。
# ==========================================================================
_TEMP_DIRS = []
_SHARED_CONFIG_DIR = [None]
_SEQ = [0]


@atexit.register
def _cleanup_temp_dirs():
    """测试进程退出时统一清理临时目录。"""
    for path in _TEMP_DIRS:
        shutil.rmtree(path, ignore_errors=True)
    del _TEMP_DIRS[:]


def _register_temp_dir(path):
    """登记一个临时目录，交给 atexit 统一删除。"""
    _TEMP_DIRS.append(path)
    return path


def _next_seq():
    _SEQ[0] += 1
    return _SEQ[0]


def _shared_config_dir():
    if _SHARED_CONFIG_DIR[0] is None or not os.path.isdir(_SHARED_CONFIG_DIR[0]):
        _SHARED_CONFIG_DIR[0] = _register_temp_dir(
            tempfile.mkdtemp(prefix='ut_config_'))
    return _SHARED_CONFIG_DIR[0]


@contextlib.contextmanager
def patch_config(ini_text=None, ini_path=None):
    """把 Common/config.py 的配置来源换成临时文件。

    :param ini_text: 配置内容；不传用 DEFAULT_INI
    :param ini_path: 显式指定 ini 路径（例如指向沙盒工程里的那份）
    :return: 生成出来的 ini 文件绝对路径

    同时替换两个模块级全局：
        config_path  写操作（set_value/config_delete）落盘的位置
        config       读操作使用的解析器实例

    说明：未显式指定 ini_path 时，文件建在共享临时目录里、退出上下文即删除；
    显式指定时该文件归调用方所有，本函数不删。
    """
    owns_file = ini_path is None
    if owns_file:
        ini_path = os.path.join(_shared_config_dir(), 'config_%d.ini' % _next_seq())

    text = DEFAULT_INI if ini_text is None else ini_text
    with open(ini_path, 'w', encoding='utf-8') as fp:
        fp.write(text)

    parser = ConfigOverWrite()
    parser.read(ini_path, encoding='utf-8-sig')

    import Common.config as config_module
    with mock.patch.object(config_module, 'config_path', ini_path), \
            mock.patch.object(config_module, 'config', parser):
        yield ini_path

    if owns_file:
        with contextlib.suppress(OSError):
            os.remove(ini_path)


# ==========================================================================
# 二、临时目录（日志 / 截图 / 报告 / 用例）
# ==========================================================================
class _FakeGetcwd:
    """替换 getcwd 模块：让 get_cwd() 返回测试指定的临时根目录。"""

    def __init__(self, root):
        self._root = root

    def get_cwd(self):
        return self._root


@contextlib.contextmanager
def patch_dirs(root=None):
    """把工程里的各种产出目录重定向到临时目录。

    :param root: 临时工程根；不传则新建
    :return: 临时根目录路径

    重定向清单：
        Common/basePage.getcwd   -> 截图目录 img/
        Common/log.getcwd        -> 日志目录 Logs/
        Common/runner 的 base_dir / htmlreport_dir / testcases_dir
        Common/sentMail.htmlreport_dir

    目录归属：不传 root 时由本函数新建并登记，进程退出统一删除；
    显式传入 root（例如沙盒工程根）时视为调用方所有，本函数不动它。
    """
    root = root or _register_temp_dir(tempfile.mkdtemp(prefix='ut_root_'))
    fake_getcwd = _FakeGetcwd(root)
    img_dir = os.path.join(root, 'img')
    report_dir = os.path.join(root, 'Report')
    cases_dir = os.path.join(root, 'TestSuites')

    import Common.basePage as base_page
    import Common.log as log_module
    import Common.runner as runner_module
    import Common.sentMail as mail_module
    import Common.dir_config as dir_config_module

    # UI_LOG_DIR 是"进程级"的日志目录覆盖（tests/__init__.py 用它避免污染工程 Logs/），
    # 优先级高于下面给 Common.log.getcwd 打的桩。用例级别要接管日志目录，
    # 就得先把它摘掉，退出上下文时再原样放回，否则 test_log.py 会写到共享临时目录去。
    saved_log_dir = os.environ.pop('UI_LOG_DIR', None)
    try:
        # htmlreport_dir 改打在 dir_config 上（产物路径的单一来源）：
        # runner / sentMail 都是运行时经 _dir_config.<属性> 取值的，
        # patch 这一处即同时覆盖两者 —— 比旧版逐模块打桩更贴近真实架构。
        with mock.patch.object(base_page, 'getcwd', fake_getcwd), \
                mock.patch.object(log_module, 'getcwd', fake_getcwd), \
                mock.patch.object(runner_module, 'base_dir', root), \
                mock.patch.object(dir_config_module, 'htmlreport_dir', report_dir), \
                mock.patch.object(dir_config_module, 'img_dir', img_dir), \
                mock.patch.object(runner_module, 'testcases_dir', cases_dir):
            yield root
    finally:
        if saved_log_dir is not None:
            os.environ['UI_LOG_DIR'] = saved_log_dir

    # 不在这里 rmtree：测试会创建几十个临时目录，Windows 上删目录很慢，
    # 统一交给 atexit 清理（见 _cleanup_temp_dirs）


# ==========================================================================
# 三、沙盒工程（供 runner.run 端到端测试）
# ==========================================================================
_sandbox_seq = [0]


def make_sandbox(case_source=None, with_init=True, pkg_prefix='Cases',
                 ini_text=None, case_filename='demo_test.py'):
    """建一个临时的"迷你工程"：临时根 + 配置 + 用例包。

    :param case_source:  用例模块源码；None 表示不放用例
    :param with_init:    用例包是否放 __init__.py（False 用于验证收集失败场景）
    :param pkg_prefix:   用例包名前缀，用来保证多次调用互不冲突
    :param ini_text:     配置内容，默认 DEFAULT_INI
    :param case_filename: 用例文件名
    :return: (root, cases_dir, pkg_name)

    ★ 包名刻意不叫 TestSuites：
      工程里已存在真实的 TestSuites 包，同名会让 unittest.discover 拿到
      缓存里的旧模块而收错用例。这里用唯一包名彻底规避该干扰。
    """
    _sandbox_seq[0] += 1
    pkg_name = '%s%d_%d' % (pkg_prefix, _sandbox_seq[0], int(time.time() * 1000) % 100000)

    root = _register_temp_dir(tempfile.mkdtemp(prefix='ut_sandbox_'))
    cases_dir = os.path.join(root, pkg_name)
    os.makedirs(cases_dir)

    with open(os.path.join(root, 'config.ini'), 'w', encoding='utf-8') as fp:
        fp.write(DEFAULT_INI if ini_text is None else ini_text)

    if with_init:
        with open(os.path.join(cases_dir, '__init__.py'), 'w', encoding='utf-8') as fp:
            fp.write('# -*- coding: utf-8 -*-\n')
    if case_source is not None:
        with open(os.path.join(cases_dir, case_filename), 'w', encoding='utf-8') as fp:
            fp.write(case_source)

    return root, cases_dir, pkg_name


def drop_modules(*module_names):
    """把指定模块名从 sys.modules 中清掉，避免沙盒用例被缓存复用。"""
    for name in module_names:
        sys.modules.pop(name, None)


# 常用的沙盒用例源码
CASE_SOURCE_PASS = """\
import unittest


class DemoPass(unittest.TestCase):
    def test_a_pass(self):
        self.assertEqual(1, 1)

    def test_b_pass(self):
        pass
"""

CASE_SOURCE_MIXED = """\
import unittest


class DemoMixed(unittest.TestCase):
    def test_a_pass(self):
        self.assertTrue(True)

    def test_b_fail(self):
        self.assertEqual(1, 2, '故意失败')
"""

CASE_SOURCE_SETUPCLASS_BOOM = """\
import unittest


class DemoBoom(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise RuntimeError('模拟浏览器起不来')

    def test_never_runs(self):
        pass
"""

CASE_SOURCE_SKIP = """\
import unittest


class DemoSkip(unittest.TestCase):
    @unittest.skip('不跑这条')
    def test_skipped(self):
        pass

    def test_ok(self):
        pass
"""


# ==========================================================================
# 四、假 Playwright 对象
#   形状对齐 playwright.sync_api：
#     Page.locator(sel) -> Locator（惰性），动作在执行瞬间才真正定位。
#     桩对象所有动作只记录到 calls/... 列表，可按参数注入异常。
#   "找不到元素"的模拟方式：FakePage(find_error=PlaywrightTimeoutError('gone'))
#     -> wait_for() 抛该异常、count() 返回 0 —— 与真实 Playwright 行为一致。
#   注意坑（沿用旧基座的约定）：FakePage(element=None) 会被替换成默认
#     FakeElement()，模拟"找不到"必须用 find_error，不能传 element=None。
# ==========================================================================
class FakeElement:
    """假的 Locator 动作面。所有动作只记录，可指定某动作抛异常。

    记录字段：
        calls            动作名序列，如 ['clear', 'fill', 'click']
        filled           fill() 收到的值
        pressed          press() 收到的按键
        input_files      set_input_files() 收到的路径
        evaluates        evaluate() 收到的脚本（对应旧基座的 driver.scripts）
        attributes_read  get_attribute() 读过的属性名
        select_options   select_option() 收到的 kwargs 字典
    """

    def __init__(self, text='', value='', displayed=True, attributes=None,
                 click_error=None, fill_error=None, clear_error=None,
                 input_value_error=None, text_error=None, evaluate_error=None):
        self.text = text
        self.value = value
        self._displayed = displayed
        self.attributes = dict(attributes or {})
        self.click_error = click_error
        self.fill_error = fill_error
        self.clear_error = clear_error
        self.input_value_error = input_value_error
        self.text_error = text_error
        self.evaluate_error = evaluate_error
        self.calls = []
        self.filled = []
        self.pressed = []
        self.input_files = []
        self.evaluates = []
        self.attributes_read = []
        self.select_options = []

    # ---- 读 ----
    def inner_text(self):
        if self.text_error:
            raise self.text_error
        return self.text

    def input_value(self):
        if self.input_value_error:
            raise self.input_value_error
        return self.value

    def get_attribute(self, name):
        self.attributes_read.append(name)
        return self.attributes.get(name)

    def is_visible(self):
        return self._displayed

    def count(self):
        return 1

    # ---- 动作 ----
    def click(self, **kwargs):
        self.calls.append('click')
        if self.click_error:
            raise self.click_error

    def fill(self, value, **kwargs):
        self.calls.append('fill')
        self.filled.append(value)
        if self.fill_error:
            raise self.fill_error

    def clear(self):
        self.calls.append('clear')
        if self.clear_error:
            raise self.clear_error

    def press(self, key):
        self.calls.append('press')
        self.pressed.append(key)

    def set_input_files(self, path):
        self.calls.append('set_input_files')
        self.input_files.append(path)

    def evaluate(self, script, arg=None):
        self.calls.append('evaluate')
        self.evaluates.append(script)
        if self.evaluate_error:
            raise self.evaluate_error

    def scroll_into_view_if_needed(self, **kwargs):
        self.calls.append('scroll_into_view_if_needed')

    def hover(self, **kwargs):
        self.calls.append('hover')

    def wait_for(self, state=None, timeout=None):
        # 单元素桩总能等到；"找不到"由 Page.find_error 在 locator 层模拟
        if getattr(self, '_page_find_error', None):
            raise self._page_find_error
        self.calls.append('wait_for')

    def select_option(self, label=None, value=None, index=None):
        self.calls.append('select_option')
        self.select_options.append({'label': label, 'value': value, 'index': index})

    def all(self):
        return [self]

    def first(self):
        return self

    # Playwright 的 Locator.first 是属性；这里用 property 兼容两种写法
    @property
    def _first(self):
        return self


class FakeLocator:
    """假的 Playwright Locator：.first 返回自身，动作转发给绑定的目标元素。

    :param page: 归属的 FakePage（借它的 find_error 模拟"元素不存在"）
    :param target: 动作转发目标（FakeElement 或另一个 FakeLocator 包装的元素）
    :param selector: 编译后的 Playwright 选择器字符串（断言用）
    :param many: 由 locator.all() 返回的"列表定位器"，可被 .all() 再次取出
    """

    def __init__(self, page, target, selector, many=False):
        self._page = page
        self._target = target
        self.selector = selector
        self.many = many

    # ---- 身份与惰性 ----
    @property
    def first(self):
        return self

    def all(self):
        if self.many:
            return [self]
        return list(self._page.locator_all(self.selector))

    def locator(self, sub_selector):
        """在已定位元素内再定位（对应 get_table_rows 的 tbl.locator('.//tr')）。"""
        rows = self._page.locator_all(sub_selector)
        if rows:
            return rows[0]
        return FakeLocator(self._page, FakeElement(), sub_selector, many=True)

    # ---- 探测 ----
    def count(self):
        if self._page.find_error is not None:
            return 0
        if self.many:
            return len(self._page.elements) or 1
        return 1

    def is_visible(self):
        if self._page.find_error is not None:
            return False
        return self._target.is_visible()

    def wait_for(self, state=None, timeout=None):
        if self._page.find_error is not None:
            raise self._page.find_error
        self._page.wait_fors.append((state, timeout))

    # ---- 动作转发（同时记到 target 与 page 序列）----
    def _dispatch(self, name, *args, **kwargs):
        method = getattr(self._target, name)
        return method(*args, **kwargs)

    def click(self, **kwargs):
        return self._dispatch('click', **kwargs)

    def fill(self, value, **kwargs):
        return self._dispatch('fill', value, **kwargs)

    def clear(self):
        return self._dispatch('clear')

    def press(self, key):
        return self._dispatch('press', key)

    def set_input_files(self, path):
        return self._dispatch('set_input_files', path)

    def evaluate(self, script, arg=None):
        return self._dispatch('evaluate', script, arg)

    def scroll_into_view_if_needed(self, **kwargs):
        return self._dispatch('scroll_into_view_if_needed')

    def hover(self, **kwargs):
        return self._dispatch('hover')

    def inner_text(self):
        return self._dispatch('inner_text')

    def input_value(self):
        return self._dispatch('input_value')

    def get_attribute(self, name):
        return self._dispatch('get_attribute', name)

    def select_option(self, label=None, value=None, index=None):
        return self._dispatch('select_option', label=label, value=value, index=index)


class FakeDialog:
    """假的浏览器原生弹窗（Playwright Dialog）。"""

    def __init__(self):
        self.accepted = False

    def accept(self):
        self.accepted = True


class FakeContext:
    """假的 BrowserContext：管着 pages 列表与关闭标记。"""

    def __init__(self, browser=None, viewport=None, locale=None):
        self.browser = browser
        self.viewport = viewport
        self.locale = locale
        self.pages = []
        self.default_timeouts = []      # set_default_timeout 收到的毫秒值
        self.closed = False

    def new_page(self):
        page = FakePage(context=self)
        self.pages.append(page)
        return page

    def set_default_timeout(self, milliseconds):
        self.default_timeouts.append(milliseconds)

    def close(self):
        self.closed = True


class FakeBrowser:
    """假的 Browser：记录 new_context 参数，负责建 Page。"""

    def __init__(self, launch_kwargs=None):
        self.launch_kwargs = dict(launch_kwargs or {})
        self.contexts = []
        self.closed = False

    def new_context(self, **kwargs):
        context = FakeContext(browser=self, **{
            k: kwargs[k] for k in ('viewport', 'locale') if k in kwargs})
        context.context_kwargs = kwargs
        self.contexts.append(context)
        return context

    def close(self):
        self.closed = True


class FakeBrowserType:
    """假的 playwright.chromium / playwright.firefox。

    launch() 记录全部关键字参数（headless/args/executable_path/channel），
    可注入 launch_error 模拟"浏览器起不来"。
    """

    def __init__(self, name='chromium', launch_error=None):
        self.name = name
        self.launch_error = launch_error
        self.launches = []               # [(kwargs, ...)] 每次 launch 的关键字参数
        self.browsers = []               # launch 成功产出的 FakeBrowser

    def launch(self, **kwargs):
        self.launches.append(kwargs)
        if self.launch_error:
            raise self.launch_error
        browser = FakeBrowser(kwargs)
        self.browsers.append(browser)
        return browser


class FakePage:
    """假的 Playwright Page —— 取代旧基座的 FakeDriver。

    :param title:     title() 的返回值（注意 Playwright 里是方法不是属性）
    :param element:   locator() 产出动作转发目标；None 自动给一个默认 FakeElement
    :param elements:  locator(...).all() 返回列表的元素列表（表格行等）
    :param find_error: 非 None 时模拟"元素不存在"：wait_for 抛它、count() 返回 0
    """

    def __init__(self, title='', element=None, elements=None, find_error=None,
                 context=None):
        self._title = title
        self.element = element if element is not None else FakeElement()
        self.elements = elements if elements is not None else []
        self.find_error = find_error
        self.context = context if context is not None else FakeContext()
        # 若构造了独立 context，把本页面登记进去（模拟首标签页）
        if self not in self.context.pages:
            self.context.pages.append(self)

        self.calls = []              # [('locator', pw_selector), ...]
        self.wait_fors = []          # [(state, timeout_ms), ...]
        self.scripts = []            # 页面级 evaluate 的脚本
        self.screenshots = []        # screenshot(path=) 的落盘路径
        self.visited = []            # goto() 访问过的 url
        self.reloaded = 0            # reload() 次数
        self.frontmost = 0           # bring_to_front() 次数
        self.closed = False
        self.events_waited = []      # wait_for_event 的事件名
        self.dialog = FakeDialog()
        self.frame_locators = []     # frame_locator() 的选择器

    # ---- 元素定位 ----
    def locator(self, selector, probe=False):
        """构造一个定位器。

        :param probe: True 表示这是**框架内部**的探测调用
            （BasePage._pick_target 为"多个候选里挑首个分支命中"而额外发的定位），
            不是用例的业务动作。标记后不计入 calls，
            这样「页面操作顺序」类断言仍然只反映用例真正做了什么。

            为什么需要区分：xpath 联合表达式的分支顺序无法控制
            （Playwright 一律按文档序返回），框架只能在定位后
            补发一次探测。这是正确性所必需的额外代价，
            不应该让用例层的断言观察到。
        """
        if not probe:
            self.calls.append(('locator', selector))
        return FakeLocator(self, self.element, selector)

    def locator_all(self, selector):
        """内部辅助：把 self.elements 包成"多匹配"定位器列表。"""
        if not self.elements:
            return [FakeLocator(self, self.element, selector, many=True)]
        return [FakeLocator(self, el, selector) for el in self.elements]

    def frame_locator(self, selector):
        self.frame_locators.append(selector)
        return FakeFrameLocator(self, selector)

    # ---- 页面 ----
    def title(self):
        return self._title

    def goto(self, url, **kwargs):
        self.visited.append(url)

    def reload(self, **kwargs):
        self.reloaded += 1

    def evaluate(self, script):
        self.scripts.append(script)

    def screenshot(self, path=None, **kwargs):
        self.screenshots.append(path)
        if path:
            with open(path, 'wb') as fp:
                fp.write(b'fake-png')
        return b'fake-png'

    def wait_for_event(self, event, timeout=None):
        self.events_waited.append(event)
        return self.dialog

    def bring_to_front(self):
        self.frontmost += 1

    def wait_for_timeout(self, ms):
        """泵式等待（真实 Playwright 里这是唯一能推进事件循环的等待方式）。

        桩实现不真睡，只计数，供"轮询等新标签页"这类逻辑在无浏览器下快速走完。
        """
        self.pumps = getattr(self, 'pumps', 0) + 1

    def close(self):
        self.closed = True

    # ---- 测试辅助 ----
    def add_other_page(self, title='', **kwargs):
        """模拟新开了一个标签页：同 context 下追加一个 Page 并返回它。"""
        other = FakePage(title=title, context=self.context, **kwargs)
        return other

    @property
    def located_values(self):
        """按顺序取 locator() 收到过的选择器（旧基座 located_values 的等价物）。"""
        return [args[1] for args in self.calls if args[0] == 'locator']


class FakeFrameLocator:
    """假的 FrameLocator：locator() 仍回到宿主 Page 的元素面（只记录选择器）。"""

    def __init__(self, page, selector):
        self._page = page
        self.selector = selector
        self.locator_calls = []

    def locator(self, selector):
        self.locator_calls.append(selector)
        return FakeLocator(self._page, self._page.element, selector)


# ==========================================================================
# 五、假 Playwright 入口（供 open_browser 测试替换）
#   用法：factory = FakePlaywrightFactory()
#         with mock.patch.object(base_page, '_ensure_playwright', lambda: factory):
#             driver = BasePage().open_browser()
#   断言点：factory.chromium.launches[0]['headless'] 等；
#           产出的 driver 就是 FakePage，可用其记录字段做后续断言。
# ==========================================================================
class FakePlaywrightFactory:
    """假的 Playwright 实例：暴露 chromium / firefox 两个 FakeBrowserType。"""

    def __init__(self, page=None, chrome_error=None, firefox_error=None):
        self.chromium = FakeBrowserType('chromium', launch_error=chrome_error)
        self.firefox = FakeBrowserType('firefox', launch_error=firefox_error)
        self.default_page = page          # launch 后 new_page 返回的"预置页面"
        self.stopped = False

    def stop(self):
        self.stopped = True

    @property
    def last_browser(self):
        for bt in (self.chromium, self.firefox):
            if bt.browsers:
                return bt.browsers[-1]
        return None

    @property
    def last_page(self):
        """取最近一次 open_browser 产出的 Page（含预置 default_page）。"""
        browser = self.last_browser
        if browser is None:
            return None
        for context in reversed(browser.contexts):
            if context.pages:
                return context.pages[-1]
            return context.new_page()
        return None


# ==========================================================================
# 六、加载工程根目录下的顶层模块
# ==========================================================================
def load_module(name, path):
    """按文件路径加载一个顶层模块并返回（不加入 sys.modules 缓存）。

    用途：测试 run.py / entrance.py 这类不在包里的入口脚本。
    它们都有 `if __name__ == '__main__'` 保护，import 不会触发执行。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 常用异常，方便测试文件直接引用
__all__ = [
    'BASE_DIR', 'DEFAULT_INI', 'patch_config', 'patch_dirs', 'make_sandbox',
    'drop_modules', 'load_module',
    'CASE_SOURCE_PASS', 'CASE_SOURCE_MIXED', 'CASE_SOURCE_SETUPCLASS_BOOM',
    'CASE_SOURCE_SKIP', 'unittest',
    'FakeElement', 'FakeLocator', 'FakePage', 'FakeFrameLocator',
    'FakeContext', 'FakeBrowser', 'FakeBrowserType', 'FakeDialog',
    'FakePlaywrightFactory',
    'PlaywrightError', 'PlaywrightTimeoutError',
]
