# -*- coding: utf-8 -*-
"""
单元测试：Common/basePage.py（框架地基 —— Playwright 版）

这是整个工程最核心、也最容易出问题的一层 —— 所有 Playwright 调用都经它封装。
因此这里用 tests/helpers.py 的 FakePage / FakePlaywrightFactory 全面替换真实浏览器，
逐条验证：

    - 元素定位参数的解析（非法参数的容错）与 Playwright 选择器编译
    - 显式等待（wait_for）/ 快速通道（count 立即判定）/ 找不到返回 None 三种模式
    - 输入的「clear 失败 -> 全选+删除」回退、点击的「原生失败 -> JS 兜底」降级链路
    - 窗口（标签页）与 iframe（frame_locator）切换
    - 文件上传的前置校验（文件不存在必须报错，不能"假成功"）
    - open_browser 的各分支（无头参数、视口、语言、默认超时、浏览器类型、无地址保护）

★ 全部测试不启动浏览器、不联网、不写工程目录，毫秒级完成。
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests.helpers import (
    FakeBrowser,
    FakeContext,
    FakeElement,
    FakePage,
    FakePlaywrightFactory,
    PlaywrightError,
    PlaywrightTimeoutError,
    patch_config,
    patch_dirs,
)
from Common import basePage as base_page
from Common.basePage import (
    DEFAULT_TIMEOUT,
    DEFAULT_VIEWPORT,
    HEADLESS_ARGS,
    BasePage,
    NoSuchElementException,
    _to_pw_selector,
)


class _PageTestCase(unittest.TestCase):
    """基类：提供「配置已隔离 + my_sleep 已禁用」的 BasePage 环境。"""

    def setUp(self):
        self._config = patch_config()
        self.ini_path = self._config.__enter__()
        # 强制等待会拖慢测试，统一替换为空操作
        self._sleep = mock.patch.object(BasePage, 'my_sleep', return_value=None)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()
        self._config.__exit__(None, None, None)

    def make_page(self, driver=None, timeout=None):
        return BasePage(driver=driver if driver is not None else FakePage(),
                        timeout=timeout)

    def open_with_factory(self, bp, factory, **kwargs):
        """用假 Playwright 工厂执行 open_browser（不打真实浏览器的标准姿势）。"""
        with mock.patch.object(base_page, '_ensure_playwright', lambda: factory):
            return bp.open_browser(**kwargs)


# ==========================================================================
# 一、元素定位参数的解析
#     返回 BY_MAP 键字符串 + 原样定位值；非法输入的容错契约：一律 (None, None)。
# ==========================================================================
class TestParseSelector(unittest.TestCase):

    def test_正确的xpath描述(self):
        self.assertEqual(BasePage._parse_selector(['xpath', '//div']), ('xpath', '//div'))

    def test_定位方式大小写不敏感(self):
        self.assertEqual(BasePage._parse_selector(['XPATH', '//div']), ('xpath', '//div'))
        self.assertEqual(BasePage._parse_selector(['Id', 'x']), ('id', 'x'))

    def test_各种定位方式都能识别(self):
        for name in ('id', 'name', 'class', 'tag', 'link',
                     'plink', 'css', 'xpath'):
            self.assertEqual(BasePage._parse_selector([name, 'v']), (name, 'v'))

    def test_元组也能用(self):
        self.assertEqual(BasePage._parse_selector(('css', '#a')), ('css', '#a'))

    def test_不支持的定位方式返回None(self):
        self.assertEqual(BasePage._parse_selector(['sizzle', '#a']), (None, None))

    def test_传入字符串而非列表时安全返回None(self):
        """历史上最容易写错的用法：忘了写成 ['xpath', '...']。"""
        self.assertEqual(BasePage._parse_selector('xpath'), (None, None))

    def test_传入None或空时安全返回None(self):
        self.assertEqual(BasePage._parse_selector(None), (None, None))
        self.assertEqual(BasePage._parse_selector([]), (None, None))
        self.assertEqual(BasePage._parse_selector(['xpath']), (None, None))


# ==========================================================================
# 一之二、Playwright 选择器编译（迁移新增的核心内部函数）
# ==========================================================================
class TestToPwSelector(unittest.TestCase):

    def test_xpath加引擎前缀(self):
        self.assertEqual(_to_pw_selector('xpath', '//div'), 'xpath=//div')

    def test_css原样透传(self):
        self.assertEqual(_to_pw_selector('css', '#btn > .ok'), 'css=#btn > .ok')

    def test_id编译为属性选择器(self):
        self.assertEqual(_to_pw_selector('id', 'btn'), 'css=[id="btn"]')

    def test_name编译为属性选择器(self):
        self.assertEqual(_to_pw_selector('name', 'x'), 'css=[name="x"]')

    def test_class编译为词匹配且多词取第一个(self):
        self.assertEqual(_to_pw_selector('class', 'btn'), 'css=[class~="btn"]')
        self.assertEqual(_to_pw_selector('class', 'btn btn-primary'), 'css=[class~="btn"]')

    def test_tag编译为css标签选择器(self):
        self.assertEqual(_to_pw_selector('tag', 'input'), 'css=input')

    def test_link编译为带引号文本匹配(self):
        # 引号形式：大小写敏感、空白规范化后的文本匹配
        self.assertEqual(_to_pw_selector('link', '确定'), 'css=text="确定"')

    def test_plink编译为子串文本匹配(self):
        # 非引号形式：大小写不敏感的子串匹配
        self.assertEqual(_to_pw_selector('plink', '确'), 'css=text=确')

    def test_值中含双引号要转义(self):
        self.assertEqual(_to_pw_selector('id', 'a"b'), 'css=[id="a\\"b"]')

    def test_值中含反斜杠要转义(self):
        self.assertEqual(_to_pw_selector('id', 'a\\b'), 'css=[id="a\\\\b"]')

    def test_不支持的定位方式抛ValueError(self):
        with self.assertRaises(ValueError):
            _to_pw_selector('sizzle', '#a')


# ==========================================================================
# 一之三、config.ini -> [ci] extra_chrome_args 的解析
# ==========================================================================
class TestExtraChromeArgs(unittest.TestCase):

    def test_默认为空列表(self):
        with patch_config():
            self.assertEqual(BasePage._extra_chrome_args(), [])

    def test_支持逗号分号与换行分隔(self):
        # ini 的换行值必须用「缩进续行」写法，顶格写会让 configparser 直接解析失败
        ini = '[ci]\nextra_chrome_args = --a, --b;--c\n    --d\n'
        with patch_config(ini_text=ini):
            self.assertEqual(
                BasePage._extra_chrome_args(), ['--a', '--b', '--c', '--d']
            )

    def test_自动去掉空白项(self):
        ini = '[ci]\nextra_chrome_args =  --a ,  , --b  \n'
        with patch_config(ini_text=ini):
            self.assertEqual(BasePage._extra_chrome_args(), ['--a', '--b'])


# ==========================================================================
# 二、超时配置
# ==========================================================================
class TestTimeoutConfig(_PageTestCase):

    def test_默认超时来自配置文件(self):
        page = self.make_page()
        self.assertEqual(page.timeout, 7)      # DEFAULT_INI 里 element_timeout = 7

    def test_构造参数优先于配置(self):
        self.assertEqual(self.make_page(timeout=3).timeout, 3)

    def test_缺配置时退到代码默认值(self):
        with patch_config(ini_text='[environment]\nurl = http://x\n'):
            self.assertEqual(BasePage().timeout, DEFAULT_TIMEOUT)


# ==========================================================================
# 三、元素定位
#     迁移后 get_element 返回 Locator（惰性定位器），不再是具体元素对象；
#     断言对准「等待方式 + 编译选择器 + 动作转发」三个可观测行为。
# ==========================================================================
class TestGetElement(_PageTestCase):

    def test_显式等待模式定位并返回可用Locator(self):
        driver = FakePage()
        page = self.make_page(driver)
        element = page.get_element(['xpath', "//input[@type='text']"])
        self.assertIsNotNone(element)
        # 默认走 wait_for：state=attached，超时秒换算为毫秒（7s -> 7000ms）
        self.assertEqual(driver.wait_fors, [('attached', 7000)])
        self.assertEqual(driver.located_values, ["xpath=//input[@type='text']"])
        # 返回的 Locator 动作转发到底层元素
        element.click()
        self.assertEqual(driver.element.calls, ['click'])

    def test_visible参数使用可见等待条件(self):
        driver = FakePage()
        page = self.make_page(driver)
        self.assertIsNotNone(page.get_element(['id', 'x'], visible=True))
        self.assertEqual(driver.wait_fors, [('visible', 7000)])

    def test_快速通道立即判定且不产生等待(self):
        driver = FakePage()
        page = self.make_page(driver)
        element = page.get_element(['id', 'btn'], timeout=0)
        self.assertIsNotNone(element)
        self.assertEqual(driver.wait_fors, [], 'timeout=0 不应调用 wait_for')
        self.assertEqual(driver.located_values, ['css=[id="btn"]'])

    def test_快速通道要求可见而元素隐藏时返回None(self):
        driver = FakePage(element=FakeElement(displayed=False))
        page = self.make_page(driver)
        self.assertIsNone(page.get_element(['id', 'btn'], timeout=0, visible=True))

    def test_元素不存在时返回None而不抛异常(self):
        """find_error=PlaywrightTimeoutError：wait_for 抛它、count() 返回 0。"""
        driver = FakePage(find_error=PlaywrightTimeoutError('gone'))
        page = self.make_page(driver)
        self.assertIsNone(page.get_element(['id', 'nope']))

    def test_快速通道下元素不存在返回None(self):
        driver = FakePage(find_error=PlaywrightTimeoutError('gone'))
        page = self.make_page(driver)
        self.assertIsNone(page.get_element(['id', 'nope'], timeout=0))

    def test_等待超时后返回None(self):
        """其它 PlaywrightError 同样按「定位失败」处理，吞掉并返回 None。"""
        driver = FakePage(find_error=PlaywrightError('boom'))
        page = self.make_page(driver)
        self.assertIsNone(page.get_element(['id', 'nope'], timeout=0.05))

    def test_非法定位参数返回None(self):
        driver = FakePage()
        page = self.make_page(driver)
        self.assertIsNone(page.get_element('xpath'))
        self.assertEqual(driver.calls, [], '非法定位不应触达浏览器')

    def test_get_elements快速通道返回定位器列表(self):
        elements = [FakeElement(text='a'), FakeElement(text='b')]
        driver = FakePage(elements=elements)
        page = self.make_page(driver)
        rows = page.get_elements(['tag', 'tr'], timeout=0)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r.inner_text() for r in rows], ['a', 'b'])

    def test_get_elements找不到时返回空列表(self):
        driver = FakePage(find_error=PlaywrightTimeoutError('none'))
        page = self.make_page(driver)
        self.assertEqual(page.get_elements(['tag', 'tr'], timeout=0.05), [])

    def test_get_elements非法参数返回空列表(self):
        self.assertEqual(self.make_page().get_elements('bad'), [])

    def test_wait_element语义等价get_element(self):
        driver = FakePage()
        page = self.make_page(driver)
        element = page.wait_element(['id', 'x'])
        self.assertIsNotNone(element)
        self.assertEqual(driver.wait_fors, [('attached', 7000)])


# ==========================================================================
# 四、交互动作
# ==========================================================================
class TestInteraction(_PageTestCase):

    # ---- type：清空 -> 填值（clear + fill）----
    def test_type会先清空再输入(self):
        element = FakeElement()
        page = self.make_page(FakePage(element=element))
        page.type(['id', 'user'], 'zhangsan')
        self.assertEqual(element.calls, ['clear', 'fill'])
        self.assertEqual(element.filled, ['zhangsan'])

    def test_type元素不存在时抛出异常(self):
        """必须抛异常：静默跳过会让用例"假成功"。"""
        driver = FakePage(find_error=PlaywrightTimeoutError('gone'))
        page = self.make_page(driver)
        with self.assertRaises(NoSuchElementException):
            page.type(['id', 'user'], 'x')

    def test_type在clear失败时退化为全选删除(self):
        element = FakeElement(clear_error=PlaywrightError('clear 不支持'))
        page = self.make_page(FakePage(element=element))
        page.type(['id', 'user'], 'abc')
        # clear 失败 -> 全选 + 删除 -> 再填值
        self.assertEqual(element.calls, ['clear', 'press', 'press', 'fill'])
        self.assertEqual(element.pressed, ['Control+a', 'Delete'])
        self.assertEqual(element.filled, ['abc'])

    def test_type输入失败时截图并向上抛(self):
        element = FakeElement(fill_error=PlaywrightError('输入失败'))
        driver = FakePage(element=element)
        page = self.make_page(driver)
        with patch_dirs():
            with self.assertRaises(PlaywrightError):
                page.type(['id', 'user'], 'x')
            self.assertEqual(len(driver.screenshots), 1, '输入失败应自动截图')

    # ---- click：滚动到可视区 -> 原生点击 -> 失败降级 JS ----
    def test_click成功(self):
        element = FakeElement()
        page = self.make_page(FakePage(element=element))
        page.click(['id', 'btn'])
        # 点击前会先滚动到可视区（旧版 execute_script('scrollIntoView…') 的等价物）
        self.assertEqual(element.calls[:2], ['scroll_into_view_if_needed', 'click'])

    def test_click原生失败时降级为JS点击(self):
        element = FakeElement(click_error=PlaywrightError('被遮挡'))
        page = self.make_page(FakePage(element=element))
        page.click(['id', 'btn'])
        self.assertEqual(element.evaluates, ['el => el.click()'],
                         '原生点击失败应触发 JS 点击兜底')

    def test_click元素不存在时抛出异常(self):
        driver = FakePage(find_error=PlaywrightTimeoutError('gone'))
        page = self.make_page(driver)
        with self.assertRaises(NoSuchElementException):
            page.click(['id', 'btn'])

    def test_滚动失败不影响后续点击(self):
        element = FakeElement()
        page = self.make_page(FakePage(element=element))
        with mock.patch.object(element, 'scroll_into_view_if_needed',
                               side_effect=PlaywrightError('滚动失败')):
            page.click(['id', 'btn'])
        self.assertIn('click', element.calls)

    # ---- 读标题 / 文本 / value ----
    def test_get_title(self):
        page = self.make_page(FakePage(title='统一门户'))
        self.assertEqual(page.get_title(), '统一门户')

    def test_get_text(self):
        page = self.make_page(FakePage(element=FakeElement(text='素材库')))
        self.assertEqual(page.get_text(['id', 'x']), '素材库')

    def test_get_text元素不存在时抛出异常(self):
        driver = FakePage(find_error=PlaywrightTimeoutError('gone'))
        page = self.make_page(driver)
        with self.assertRaises(NoSuchElementException):
            page.get_text(['id', 'x'])

    def test_get_value优先读控件的input_value(self):
        """get_text 取显示文字、get_value 取 value，两者不能混用。

        Playwright 的 input_value() 是首选通道（等价旧版读 value 属性的意图）：
        能读到值时不应再去 get_attribute。
        """
        element = FakeElement(text='', value='测试关键字')
        page = self.make_page(FakePage(element=element))
        self.assertEqual(page.get_value(['id', 'kw']), '测试关键字')
        self.assertEqual(element.attributes_read, [])

    def test_get_value在input_value失败时退化为读属性(self):
        """非表单控件没有 input_value -> 退读 get_attribute('value')。"""
        element = FakeElement(input_value_error=PlaywrightError('不是表单控件'),
                              attributes={'value': '测试关键字'})
        page = self.make_page(FakePage(element=element))
        self.assertEqual(page.get_value(['id', 'kw']), '测试关键字')
        self.assertEqual(element.attributes_read, ['value'])

    def test_get_value属性缺失时返回None(self):
        element = FakeElement(input_value_error=PlaywrightError('不是表单控件'),
                              attributes={})
        page = self.make_page(FakePage(element=element))
        self.assertIsNone(page.get_value(['id', 'kw']))

    def test_get_value元素不存在时抛出异常(self):
        """必须抛异常：静默返回 None 会让"输入是否生效"的断言假通过。"""
        driver = FakePage(find_error=PlaywrightTimeoutError('gone'))
        page = self.make_page(driver)
        with self.assertRaises(NoSuchElementException):
            page.get_value(['id', 'kw'])

    # ---- JS 与可见性判断 ----
    def test_use_js执行成功(self):
        driver = FakePage()
        page = self.make_page(driver)
        page.use_js('return 1')
        self.assertEqual(driver.scripts, ['return 1'])

    def test_use_js失败时向上抛(self):
        driver = FakePage()
        page = self.make_page(driver)
        with mock.patch.object(driver, 'evaluate',
                               side_effect=PlaywrightError('js 报错')):
            with self.assertRaises(PlaywrightError):
                page.use_js('bad()')

    def test_isdisplayed处理None与异常(self):
        self.assertFalse(BasePage.isdisplayed(None))
        self.assertTrue(BasePage.isdisplayed(FakeElement(displayed=True)))
        self.assertFalse(BasePage.isdisplayed(FakeElement(displayed=False)))
        boom = FakeElement()
        with mock.patch.object(boom, 'is_visible',
                               side_effect=PlaywrightError('元素已失效')):
            self.assertFalse(BasePage.isdisplayed(boom))


# ==========================================================================
# 四之二、refresh（迁移新增：把旧用例里的 driver.refresh() 收进基类）
# ==========================================================================
class TestRefresh(_PageTestCase):

    def test_refresh调用页面reload(self):
        driver = FakePage()
        page = self.make_page(driver)
        page.refresh()
        self.assertEqual(driver.reloaded, 1)

    def test_refresh作用于切换后的新窗口(self):
        first = FakePage(title='统一门户')
        second = first.add_other_page(title='工作台')
        page = self.make_page(first)
        page.switch_other_window()
        page.refresh()
        self.assertEqual(second.reloaded, 1)
        self.assertEqual(first.reloaded, 0)


# ==========================================================================
# my_sleep —— 需要使用未被打桩的真实实现，单独成类
# ==========================================================================
class TestMySleep(unittest.TestCase):

    def test_按秒数调用真实等待(self):
        with mock.patch.object(base_page.time, 'sleep') as fake_sleep:
            BasePage.my_sleep(0.5)
        fake_sleep.assert_called_once_with(0.5)


# ==========================================================================
# 零硬编码改造后的配置驱动入口：[timing] 等待时长 / [tuning] 视口 / 语言
# ==========================================================================
class TestTimingAndTuningConfig(_PageTestCase):
    """page_wait、_parse_viewport、以及 open_browser 读取 [tuning] 的链路。"""

    def test_page_wait读timing配置(self):
        ini = '[timing]\nworkbench_render = 4.5\n'
        with patch_config(ini_text=ini):
            page = self.make_page()
            # _PageTestCase 已把 my_sleep 打桩成空操作，这里换成能记录参数的桩
            with mock.patch.object(BasePage, 'my_sleep') as fake_sleep:
                page.page_wait('workbench_render', 2)
        fake_sleep.assert_called_once_with(4.5)

    def test_page_wait未配置时用代码默认值(self):
        """[timing] 可整段不配：缺省用调用方给的技术默认秒数（不报错）。"""
        page = self.make_page()
        with mock.patch.object(BasePage, 'my_sleep') as fake_sleep:
            page.page_wait('workbench_render', 2)
        fake_sleep.assert_called_once_with(2.0)

    def test_parse_viewport四种写法(self):
        for raw in ('1920x1080', '1920*1080', '1920,1080', '1920 1080'):
            self.assertEqual(base_page._parse_viewport(raw),
                             {'width': 1920, 'height': 1080}, raw)

    def test_parse_viewport非法值回退默认(self):
        for raw in ('', '   ', '超大', '1920x', 'abc'):
            self.assertEqual(base_page._parse_viewport(raw), DEFAULT_VIEWPORT, repr(raw))

    def test_open_browser视口跟随tuning配置(self):
        """无头模式下视口来自 [tuning] viewport，不再只有写死的 1920x1080。"""
        ini = ('[environment]\nurl = http://x\n'
               '[tuning]\nviewport = 1366x768\nbrowser_locale = en-US\n')
        with patch_config(ini_text=ini):
            factory = FakePlaywrightFactory()
            page = BasePage(driver=FakePage())
            driver = self.open_with_factory(page, factory, headless=True)
        self.assertEqual(driver.context.viewport, {'width': 1366, 'height': 768})
        self.assertEqual(driver.context.locale, 'en-US')


# ==========================================================================
# 五、输入回车（搜索框场景）
# ==========================================================================
class TestInputEnter(_PageTestCase):

    def test_按回车(self):
        element = FakeElement()
        page = self.make_page(FakePage(element=element))
        page.input_enter('关键词', "//input[@id='q']")
        self.assertEqual(element.filled, ['关键词'])
        self.assertEqual(element.pressed, ['Enter'])

    def test_也可直接传selector(self):
        element = FakeElement()
        page = self.make_page(FakePage(element=element))
        page.input_enter('关键词', None, selector=['id', 'q'])
        self.assertEqual(element.pressed, ['Enter'])

    def test_元素不存在时抛出异常(self):
        driver = FakePage(find_error=PlaywrightTimeoutError('gone'))
        page = self.make_page(driver)
        with self.assertRaises(NoSuchElementException):
            page.input_enter('x', "//input")


# ==========================================================================
# 六、窗口与 frame 切换
#     迁移后 handle 就是 Page 对象本身；「其他窗口」来自 context.pages。
# ==========================================================================
class TestWindowAndFrame(_PageTestCase):

    def test_只有一个标签页时返回None(self):
        driver = FakePage()
        page = self.make_page(driver)
        self.assertIsNone(page.switch_other_window(timeout=0))

    def test_新标签页注册有延迟时轮询等到再切(self):
        """点完元素立刻切窗口是新标签页场景的常态，但新 Page 登记到
        context.pages 存在协议延迟——必须轮询等，不能看一眼就判失败。

        ★ 关键回归护栏：轮询只能用 Playwright 的 wait_for_timeout 泵事件循环，
        绝不能用 time.sleep。原生 sleep 不泵协议，新标签页消息永远读不到，
        睡多久都白搭。这里把 time.sleep 打成"一调就炸"来钉死这一点。"""
        first = FakePage(title='统一门户')
        other = FakePage(title='工作台', context=first.context)

        class _SlowContext(object):
            """前两次扫描只看到旧页，第三次才"注册"上新页 —— 模拟延迟。"""
            def __init__(self, scans):
                self._scans = scans
                self._n = 0

            @property
            def pages(self):
                result = self._scans[min(self._n, len(self._scans) - 1)]
                self._n += 1
                return result

        first.context = _SlowContext([[first], [first], [first, other]])
        page = self.make_page(first)
        with mock.patch.object(
                base_page.time, 'sleep',
                side_effect=AssertionError('轮询禁用 time.sleep，必须用 wait_for_timeout 泵事件循环')):
            result = page.switch_other_window(timeout=5)
        self.assertIs(result, other)
        self.assertIs(page.driver, other)
        self.assertGreaterEqual(first.pumps, 1)    # 确实通过 wait_for_timeout 推进了轮询

    def test_切换到另一个标签页(self):
        driver = FakePage(title='统一门户')
        other = driver.add_other_page(title='工作台')
        page = self.make_page(driver)
        result = page.switch_other_window()
        self.assertIs(result, other)
        self.assertIs(page.driver, other)          # driver 指向新标签页
        self.assertEqual(other.frontmost, 1)      # 且被置前
        self.assertEqual(page.get_title(), '工作台')

    def test_显式传None时自动取当前页(self):
        driver = FakePage()
        other = driver.add_other_page(title='工作台')
        page = self.make_page(driver)
        self.assertIs(page.switch_other_window(None), other)

    def test_get_handle返回当前Page对象(self):
        driver = FakePage()
        self.assertIs(self.make_page(driver).get_handle(), driver)

    def test_旧别名chage_handle行为等效(self):
        driver = FakePage()
        other = driver.add_other_page(title='工作台')
        page = self.make_page(driver)
        handle = page.get_handle()                # 要"避开"的当前页
        self.assertIs(page.chage_handle(handle), other)
        self.assertIs(page.driver, other)

    def test_切换iframe记录编译后的选择器(self):
        driver = FakePage()
        page = self.make_page(driver)
        page.switch_iframe(['tag', 'iframe'])
        self.assertEqual(driver.frame_locators, ['css=iframe'])
        self.assertIsNotNone(page._frame)
        self.assertEqual(page._frame.selector, 'css=iframe')

    def test_切换iframe后定位发生在框架内(self):
        driver = FakePage()
        page = self.make_page(driver)
        page.switch_iframe(['tag', 'iframe'])
        element = page.get_element(['id', 'q'])
        self.assertIsNotNone(element)
        # 定位走的是 FrameLocator，而不是 Page
        self.assertEqual(page._frame.locator_calls, ['css=[id="q"]'])
        element.click()
        self.assertEqual(driver.element.calls, ['click'])

    def test_切换iframe元素不存在时抛出异常(self):
        driver = FakePage(find_error=PlaywrightTimeoutError('gone'))
        page = self.make_page(driver)
        with self.assertRaises(NoSuchElementException):
            page.switch_iframe(['tag', 'iframe'])

    def test_回到最外层文档(self):
        driver = FakePage()
        page = self.make_page(driver)
        page.switch_iframe(['tag', 'iframe'])
        page.switch_default_content()
        self.assertIsNone(page._frame)
        page.get_element(['id', 'q'])
        self.assertEqual(driver.located_values[-1], 'css=[id="q"]')

    def test_旧别名switch_ifarme指向同一实现(self):
        self.assertIs(BasePage.switch_ifarme, BasePage.switch_iframe)

    def test_接受弹窗(self):
        driver = FakePage()
        page = self.make_page(driver)
        page.popout()
        self.assertEqual(driver.events_waited, ['dialog'])
        self.assertTrue(driver.dialog.accepted)


# ==========================================================================
# 七、文件上传
# ==========================================================================
class TestFilesUpload(_PageTestCase):

    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.mkdtemp(prefix='ut_upload_')
        self.video = os.path.join(self.tmpdir, 'demo.mp4')
        with open(self.video, 'wb') as fp:
            fp.write(b'fake-video')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        super().tearDown()

    def test_上传成功时给file控件set_input_files(self):
        element = FakeElement()
        page = self.make_page(FakePage(element=element))
        page.files_upload(['xpath', "//input[@type='file']"], self.video)
        self.assertEqual(element.input_files, [self.video])
        self.assertIn('set_input_files', element.calls)

    def test_未配置且未传路径时抛出ValueError(self):
        ini = '[testcase]\nupload_file =\n'
        element = FakeElement()
        with patch_config(ini_text=ini):
            page = BasePage(driver=FakePage(element=element))
            with self.assertRaises(ValueError):
                page.files_upload(['xpath', "//input[@type='file']"])

    def test_文件不存在时抛出FileNotFoundError(self):
        """必须报错，否则用例会在没有真实上传的情况下"通过"。"""
        page = self.make_page(FakePage(element=FakeElement()))
        with self.assertRaises(FileNotFoundError):
            page.files_upload(['xpath', "//input[@type='file']"],
                              os.path.join(self.tmpdir, '没有这个文件.mp4'))

    def test_未找到上传控件时抛出异常(self):
        page = self.make_page(FakePage())
        with mock.patch.object(page, 'get_element', return_value=None):
            with self.assertRaises(NoSuchElementException):
                page.files_upload(['xpath', "//input[@type='file']"], self.video)

    def test_未安装autoit时给出可操作的报错(self):
        with mock.patch.object(base_page, 'AUTOIT_AVAILABLE', False):
            page = self.make_page()
            with self.assertRaises(RuntimeError) as ctx:
                page.upload_by_autoit(self.video)
            self.assertIn('PyAutoIt', str(ctx.exception))


# ==========================================================================
# 八、截图与关闭
# ==========================================================================
class TestScreenshotAndQuit(_PageTestCase):

    def test_截图保存到日期目录(self):
        driver = FakePage()
        page = self.make_page(driver)
        with patch_dirs() as root:
            path = page.get_img()
        self.assertTrue(path.endswith('.png'))
        self.assertTrue(path.startswith(os.path.join(root, 'img')))
        self.assertEqual(driver.screenshots, [path])
        self.assertTrue(os.path.isfile(path), 'FakePage 应真实落盘假 png')

    def test_截图失败不抛异常(self):
        """截图只是排查辅助手段，不能因为它失败就让用例挂掉。"""
        driver = FakePage()
        page = self.make_page(driver)
        with patch_dirs(), mock.patch.object(
                driver, 'screenshot', side_effect=PlaywrightError('截图失败')):
            self.assertIsNone(page.get_img())

    def test_关闭浏览器关闭context与browser(self):
        browser = FakeBrowser()
        context = FakeContext(browser=browser)
        driver = FakePage(context=context)
        page = self.make_page(driver)
        page.dr_quit()
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)
        self.assertIsNone(page.driver)
        self.assertIsNone(page._frame)

    def test_重复关闭不报错(self):
        page = self.make_page(FakePage())
        page.dr_quit()
        page.dr_quit()

    def test_关闭报错时仍清空引用(self):
        context = FakeContext()
        driver = FakePage(context=context)
        page = self.make_page(driver)
        with mock.patch.object(context, 'close',
                               side_effect=PlaywrightError('关闭失败')):
            page.dr_quit()
        self.assertIsNone(page.driver)


# ==========================================================================
# 九、浏览器启动
#     打桩点：mock.patch.object(base_page, '_ensure_playwright', lambda: factory)。
#     断言点从旧的 chrome_options/最大化/隐式等待，换成：
#       factory.chromium.launches[0]（headless/args/executable_path/channel）
#       context viewport / locale / default_timeouts
#       page.visited
# ==========================================================================
class TestOpenBrowser(_PageTestCase):

    def _factory(self, **kwargs):
        return FakePlaywrightFactory(**kwargs)

    def test_打开chrome并访问配置地址(self):
        factory = self._factory()
        page = self.make_page()
        driver = self.open_with_factory(page, factory)
        self.assertIsInstance(driver, FakePage)
        self.assertIs(page.driver, driver)
        self.assertEqual(driver.visited, ['http://test-env.local/sso-portal'])
        self.assertEqual(len(factory.chromium.launches), 1)
        launch = factory.chromium.launches[0]
        self.assertIs(launch['headless'], False)
        self.assertIsNone(launch['executable_path'], '未配置 chrome_binary 时用自带浏览器')

    def test_有头模式不固定视口且带最大化参数(self):
        factory = self._factory()
        page = self.make_page()
        driver = self.open_with_factory(page, factory)
        self.assertIsNone(driver.context.viewport, '有头模式交给真实窗口')
        self.assertIn('--start-maximized', factory.chromium.launches[0]['args'])

    def test_无头模式使用原生参数与固定视口(self):
        factory = self._factory()
        page = self.make_page()
        driver = self.open_with_factory(page, factory, headless=True)
        launch = factory.chromium.launches[0]
        self.assertIs(launch['headless'], True)
        arguments = launch['args']
        for arg in HEADLESS_ARGS:
            self.assertIn(arg, arguments, '启动参数必须带 %s' % arg)
        self.assertNotIn('--start-maximized', arguments, '无头模式不该最大化窗口')
        self.assertEqual(driver.context.viewport, DEFAULT_VIEWPORT)
        self.assertEqual(driver.context.viewport, {'width': 1920, 'height': 1080})

    def test_上下文语言与默认超时(self):
        factory = self._factory()
        page = self.make_page()
        driver = self.open_with_factory(page, factory)
        self.assertEqual(driver.context.locale, 'zh-CN')
        # element_timeout 语义 -> set_default_timeout(毫秒)
        self.assertEqual(driver.context.default_timeouts, [7000])

    def test_启动参数不含手工命令行遗留项(self):
        """无头由原生 headless 控制、尺寸由 viewport 控制，旧命令行参数应全部退场。"""
        factory = self._factory()
        page = self.make_page()
        driver = self.open_with_factory(page, factory, headless=True)
        arguments = factory.chromium.launches[0]['args']
        joined = ' '.join(arguments)
        self.assertNotIn('--headless', joined, '无头由 Playwright 原生参数控制')
        self.assertNotIn('--window-size', joined, '窗口尺寸由 context viewport 控制')
        self.assertNotIn('--lang', joined, '语言由 context locale 承担')

    def test_参数优先于配置的地址(self):
        factory = self._factory()
        page = self.make_page()
        driver = self.open_with_factory(page, factory,
                                        url='http://override.local/x', browser='chrome')
        self.assertEqual(driver.visited, ['http://override.local/x'])

    def test_选择firefox走firefox引擎且不带chromium专属参数(self):
        factory = self._factory()
        page = self.make_page()
        self.open_with_factory(page, factory, browser='firefox')
        self.assertEqual(factory.chromium.launches, [])
        self.assertEqual(len(factory.firefox.launches), 1)
        self.assertEqual(factory.firefox.launches[0], {'headless': False})

    def test_选择edge使用msedge_channel(self):
        factory = self._factory()
        page = self.make_page()
        self.open_with_factory(page, factory, browser='edge')
        self.assertEqual(len(factory.chromium.launches), 1)
        launch = factory.chromium.launches[0]
        self.assertEqual(launch['channel'], 'msedge')
        self.assertNotIn('executable_path', launch)

    def test_浏览器类型大小写不敏感(self):
        factory = self._factory()
        page = self.make_page()
        self.open_with_factory(page, factory, browser='CHROME')
        self.assertEqual(len(factory.chromium.launches), 1)

    def test_浏览器类型非法时报错(self):
        page = self.make_page()
        with self.assertRaises(ValueError) as ctx:
            page.open_browser(browser='safari')
        self.assertIn('chrome', str(ctx.exception))

    def test_未配置地址时给出明确报错(self):
        """CI 首跑最常见的问题，必须是可读错误而不是一堆定位失败。"""
        ini = '[environment]\nbrowser = chrome\n'
        with patch_config(ini_text=ini):
            page = BasePage(driver=FakePage())
            with self.assertRaises(ValueError) as ctx:
                page.open_browser()
            self.assertIn('未配置被测地址', str(ctx.exception))

    def test_浏览器启动失败时向上抛(self):
        factory = self._factory(chrome_error=PlaywrightError('浏览器未安装'))
        page = self.make_page()
        with mock.patch.object(base_page, '_ensure_playwright', lambda: factory):
            with self.assertRaises(PlaywrightError):
                page.open_browser()
        self.assertEqual(factory.chromium.browsers, [])

    def test_firefox启动失败时向上抛(self):
        factory = self._factory(firefox_error=PlaywrightError('firefox 起不来'))
        page = self.make_page()
        with mock.patch.object(base_page, '_ensure_playwright', lambda: factory):
            with self.assertRaises(PlaywrightError):
                page.open_browser(browser='firefox')

    def test_读取额外启动参数(self):
        ini = ('[environment]\nurl = http://x.local\nbrowser = chrome\n'
               '[ci]\nextra_chrome_args = --disable-images, --allow-insecure-localhost\n')
        factory = self._factory()
        with patch_config(ini_text=ini):
            page = BasePage(driver=FakePage())
            with mock.patch.object(base_page, '_ensure_playwright', lambda: factory):
                page.open_browser()
        arguments = factory.chromium.launches[0]['args']
        self.assertIn('--disable-images', arguments)
        self.assertIn('--allow-insecure-localhost', arguments)
        for arg in HEADLESS_ARGS:
            self.assertIn(arg, arguments, '稳健参数应始终携带')

    def test_指定chrome可执行文件(self):
        ini = ('[environment]\nurl = http://x.local\nbrowser = chrome\n'
               '[ci]\nchrome_binary = /usr/bin/chromium\n')
        factory = self._factory()
        with patch_config(ini_text=ini):
            page = BasePage(driver=FakePage())
            with mock.patch.object(base_page, '_ensure_playwright', lambda: factory):
                page.open_browser()
        self.assertEqual(factory.chromium.launches[0]['executable_path'],
                         '/usr/bin/chromium')

    def test_未装PyAutoIt时模块状态正确(self):
        """记录当前环境的 autoit 可用性，避免上传回退分支被误判。"""
        self.assertIsInstance(base_page.AUTOIT_AVAILABLE, bool)


if __name__ == '__main__':
    unittest.main(verbosity=2)
