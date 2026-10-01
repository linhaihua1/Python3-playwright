# -*- coding: utf-8 -*-
"""
单元测试：跨系统通用能力（BasePage 第七节）+ GenericPage 系统适配层

【覆盖映射】
    原 14 个用例主题的覆盖意图逐条对应，映射关系：
        按文本定位成功          -> TestGetElementByText.test_按文本定位成功
        找不到返回None不抛异常  -> TestGetElementByText.test_找不到返回None不抛异常
        点击成功返回True        -> TestClickByText.test_点击成功返回True
        找不到返回False         -> TestClickByText.test_找不到返回False
        原生点击失败降级JS      -> TestClickByText.test_原生点击失败降级JS
        正常输入                -> TestTypeByPlaceholder.test_正常输入
        找不到抛NoSuchElement   -> TestTypeByPlaceholder.test_找不到抛NoSuchElement
        找到匹配行              -> TestFindRowByCell.test_找到匹配行
        无匹配返回None          -> TestFindRowByCell.test_无匹配返回None
        无遮罩直接返回True      -> TestWaitLoading.test_无遮罩直接返回True
        遮罩超时返回False不抛   -> TestWaitLoading.test_遮罩超时返回False不抛异常
        login 按配置输入并点击  -> TestGenericLogin.test_login_按配置输入并点击
        成功标识等待(get_int回归)-> TestGenericLoginSuccessText.test_配置了成功标识时按其等待且不报错
        Config整数方法名钉定    -> TestGenericLoginSuccessText.test_Config的整数读取方法名是get_int
    另按第七节公开面补齐：get_element_by_attr / select_option / get_table_rows /
    hover / scroll_to，以及 login 的「按回车提交」「按钮与密码框双缺失」两个分支。

【断言口径】
    - 定位断言基于 FakePage 记录的编译后 Playwright 选择器（'xpath=//...'），
      xpath 文本部分含 normalize-space
      与 _xpath_norm 的转义结果（含单引号时是 concat(...)）。
    - 表格行返回的是 Locator 列表，断言行数与 inner_text() 内容，不再 assertIs 元素。
    - 「找不到元素」= FakePage(find_error=PlaywrightTimeoutError('gone'))，
      与真实 Playwright 一致：wait_for 抛该异常、count() 返回 0。
    - 两档语义保留：查询类（get_element_by_* / click_by_text / 表格）返回 None/False/[]，
      输入与操作类（type_by_placeholder / select_option / hover / scroll_to）抛
      Common.basePage.NoSuchElementException。

全部不启动浏览器、不联网、不写工程目录。
"""

import unittest
from unittest import mock

import Common.basePage as base_page
from Common.basePage import BasePage, NoSuchElementException
from PageObject.genericPage import GenericPage
from tests.helpers import (
    FakeElement,
    FakePage,
    PlaywrightError,
    PlaywrightTimeoutError,
    patch_config,
)

# 异常导入约定：
#   NoSuchElementException 由 Common.basePage 自定义
#   Playwright 异常从 tests.helpers 转发（与 playwright.sync_api 同源）

# ---- 编译后选择器的期望值（把 BasePage 的选择器编译契约钉死在这里）----
USER_XP = "xpath=//input[@placeholder='请输入用户名']"
PWD_XP = "xpath=//input[@placeholder='请输入密码']"
LOGIN_XP = "xpath=//*[normalize-space(.)='登录']"
SUCCESS_XP = "xpath=//*[normalize-space(.)='退 出']"
# wait_loading_gone 不传 selector 时使用的通用遮罩特征（class 含 loading / 文本含"加载中"）
DEFAULT_LOADING_XP = ('xpath=//*[contains(@class,"loading") '
                      'or contains(normalize-space(.),"加载中")]')


class _FakeClock:
    """time 模块替身：sleep 只推进虚拟时间，测试绝不真实等待。

    必须整体替换 basePage 里的 time 引用，因为 wait_loading_gone 的实现
    用的是 time.sleep(poll_frequency) 而不是 BasePage.my_sleep ——
    只把 my_sleep 打桩是快不起来的（这个契约由超时用例顺带钉住）。
    """

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _VanishingElement(FakeElement):
    """前 visible_times 次可见、之后不可见：模拟「加载遮罩稍后才消失」。"""

    def __init__(self, visible_times=1, **kwargs):
        super().__init__(**kwargs)
        self._left = visible_times

    def is_visible(self):
        if self._left > 0:
            self._left -= 1
            return True
        return False


class _PageEnv(unittest.TestCase):
    """隔离配置 + 禁用 my_sleep + 虚拟时钟（替换 basePage 的 time）公共环境。"""

    def setUp(self):
        self._cfg = patch_config()
        self._cfg.__enter__()
        self.clock = _FakeClock()
        self.my_sleep = mock.patch.object(BasePage, 'my_sleep', return_value=None).start()
        # 后启先停，成对回收
        self._clock_patch = mock.patch.object(base_page, 'time', self.clock)
        self._clock_patch.start()

    def tearDown(self):
        self._clock_patch.stop()
        mock.patch.stopall()
        self._cfg.__exit__(None, None, None)


# ==========================================================================
# 一、按文本定位 / 点击
# ==========================================================================
class TestGetElementByText(_PageEnv):

    def test_按文本定位成功(self):
        driver = FakePage(element=FakeElement(text='登录'))
        page = BasePage(driver=driver, timeout=1)
        el = page.get_element_by_text('登录')
        # 查询类通用方法：成功返回可继续操作的 Locator
        self.assertIsNotNone(el)
        self.assertEqual(el.inner_text(), '登录')
        # 定位断言：编译后的 Playwright 选择器（精确匹配走 normalize-space）
        self.assertEqual(driver.located_values, ["xpath=//*[normalize-space(.)='登录']"])
        # 等待走 Locator.wait_for，超时秒数换算成毫秒
        self.assertEqual(driver.wait_fors, [('attached', 1000)])

    def test_限定标签与包含匹配(self):
        driver = FakePage()
        page = BasePage(driver=driver, timeout=2)
        el = page.get_element_by_text('退出', tag='span', contains=True)
        self.assertIsNotNone(el)
        self.assertEqual(driver.located_values,
                         ["xpath=//span[contains(normalize-space(.), '退出')]"])
        self.assertEqual(driver.wait_fors, [('attached', 2000)])

    def test_文本含单引号时用concat转义(self):
        # _xpath_norm 契约：含单引号的文本必须转成 concat('a', "'", 'b')，
        # 否则会把 xpath 字符串截断成非法表达式
        driver = FakePage()
        page = BasePage(driver=driver, timeout=1)
        page.get_element_by_text("李'四")
        self.assertEqual(driver.located_values,
                         ["""xpath=//*[normalize-space(.)=concat('李', "'", '四')]"""])

    def test_找不到返回None不抛异常(self):
        # Playwright 语义：等不到时 wait_for 抛 TimeoutError，通用方法吞掉并返回 None
        driver = FakePage(find_error=PlaywrightTimeoutError('gone'))
        page = BasePage(driver=driver, timeout=1)
        self.assertIsNone(page.get_element_by_text('不存在'))
        self.assertEqual(driver.wait_fors, [])


class TestClickByText(_PageEnv):

    def test_点击成功返回True(self):
        el = FakeElement(text='确定')
        driver = FakePage(element=el)
        page = BasePage(driver=driver, timeout=1)
        self.assertTrue(page.click_by_text('确定'))
        self.assertIn('click', el.calls)
        self.assertEqual(driver.located_values, ["xpath=//*[normalize-space(.)='确定']"])

    def test_找不到返回False(self):
        driver = FakePage(find_error=PlaywrightTimeoutError('gone'))
        page = BasePage(driver=driver, timeout=1)
        self.assertFalse(page.click_by_text('不存在'))

    def test_原生点击失败降级JS(self):
        el = FakeElement(click_error=PlaywrightError('被遮罩挡住'))
        driver = FakePage(element=el)
        page = BasePage(driver=driver, timeout=1)
        self.assertTrue(page.click_by_text('保存'))          # JS 兜底成功
        self.assertIn('click', el.calls)                     # 原生点击确实尝试过
        self.assertEqual(el.evaluates, ['el => el.click()']) # 降级走的是 evaluate

    def test_两种点击都失败返回False(self):
        el = FakeElement(click_error=PlaywrightError('点不动'),
                         evaluate_error=PlaywrightError('JS 也失败'))
        page = BasePage(driver=FakePage(element=el), timeout=1)
        self.assertFalse(page.click_by_text('删除'))
        self.assertEqual(el.calls, ['click', 'evaluate'])


# ==========================================================================
# 二、按属性定位 / 按 placeholder 输入
# ==========================================================================
class TestGetElementByAttr(_PageEnv):

    def test_按属性定位成功(self):
        driver = FakePage(element=FakeElement(text='admin'))
        page = BasePage(driver=driver, timeout=3)
        el = page.get_element_by_attr('placeholder', '请输入用户名')
        self.assertEqual(el.inner_text(), 'admin')
        # tag 默认 '*'：属性定位编译为 //*[属性='值']
        self.assertEqual(driver.located_values, ["xpath=//*[@placeholder='请输入用户名']"])
        self.assertEqual(driver.wait_fors, [('attached', 3000)])

    def test_找不到返回None(self):
        driver = FakePage(find_error=PlaywrightTimeoutError('gone'))
        page = BasePage(driver=driver, timeout=1)
        self.assertIsNone(page.get_element_by_attr('placeholder', '不存在'))


class TestTypeByPlaceholder(_PageEnv):

    def test_正常输入(self):
        el = FakeElement()
        driver = FakePage(element=el)
        page = BasePage(driver=driver, timeout=1)
        page.type_by_placeholder('请输入用户名', 'admin')
        # 输入类走的是 Playwright 的 clear + fill
        self.assertEqual(el.calls, ['clear', 'fill'])
        self.assertEqual(el.filled, ['admin'])
        # placeholder 定位编译为属性 xpath，且限定 input 标签
        self.assertEqual(driver.located_values, [USER_XP])

    def test_clear为False时不清空(self):
        el = FakeElement()
        page = BasePage(driver=FakePage(element=el), timeout=1)
        page.type_by_placeholder('请输入用户名', 'admin', clear=False)
        self.assertEqual(el.calls, ['fill'])
        self.assertEqual(el.filled, ['admin'])

    def test_清空失败不阻断输入(self):
        # 前端框架的输入框可能拒绝 clear()，实现约定是吞掉后继续 fill
        el = FakeElement(clear_error=PlaywrightError('只读输入框'))
        page = BasePage(driver=FakePage(element=el), timeout=1)
        page.type_by_placeholder('请输入用户名', 'admin')
        self.assertEqual(el.filled, ['admin'])

    def test_找不到抛NoSuchElement(self):
        # 输入类与查询类的两档语义：这里必须抛，而不是静默 False
        page = BasePage(driver=FakePage(find_error=PlaywrightTimeoutError('gone')), timeout=1)
        with self.assertRaisesRegex(NoSuchElementException, '未找到 placeholder'):
            page.type_by_placeholder('不存在的框', 'x')


# ==========================================================================
# 三、原生下拉框选择
# ==========================================================================
class TestSelectOption(_PageEnv):

    SELECT = ['xpath', "//select[@id='city']"]

    def _page_with_select(self):
        el = FakeElement()
        page = BasePage(driver=FakePage(element=el), timeout=1)
        return page, el

    def test_按文本选择(self):
        page, el = self._page_with_select()
        page.select_option(self.SELECT, text='北京')
        self.assertEqual(el.select_options, [{'label': '北京', 'value': None, 'index': None}])

    def test_按值选择(self):
        page, el = self._page_with_select()
        page.select_option(self.SELECT, value='BJ')
        self.assertEqual(el.select_options, [{'label': None, 'value': 'BJ', 'index': None}])

    def test_按序号选择(self):
        page, el = self._page_with_select()
        page.select_option(self.SELECT, index=2)
        self.assertEqual(el.select_options, [{'label': None, 'value': None, 'index': 2}])

    def test_参数优先级文本最高(self):
        # text / value / index 同时传时按 if-elif 顺序只走文本档
        page, el = self._page_with_select()
        page.select_option(self.SELECT, text='北京', value='BJ', index=1)
        self.assertEqual(el.select_options, [{'label': '北京', 'value': None, 'index': None}])

    def test_不传选择参数抛ValueError(self):
        page, _ = self._page_with_select()
        with self.assertRaises(ValueError):
            page.select_option(self.SELECT)

    def test_下拉框不存在抛NoSuchElement(self):
        page = BasePage(driver=FakePage(find_error=PlaywrightTimeoutError('gone')), timeout=1)
        with self.assertRaisesRegex(NoSuchElementException, '未找到下拉框'):
            page.select_option(self.SELECT, text='北京')


# ==========================================================================
# 四、悬停 / 滚动到元素
# ==========================================================================
class TestHoverAndScroll(_PageEnv):

    def test_悬停成功(self):
        el = FakeElement()
        page = BasePage(driver=FakePage(element=el), timeout=1)
        page.hover(['xpath', '//span[@class="menu"]'])
        self.assertIn('hover', el.calls)

    def test_悬停目标不存在抛NoSuchElement(self):
        page = BasePage(driver=FakePage(find_error=PlaywrightTimeoutError('gone')), timeout=1)
        with self.assertRaisesRegex(NoSuchElementException, '悬停'):
            page.hover(['xpath', '//span'])

    def test_滚动到元素(self):
        el = FakeElement()
        page = BasePage(driver=FakePage(element=el), timeout=1)
        page.scroll_to(['xpath', '//div[@id="bottom"]'])
        self.assertIn('scroll_into_view_if_needed', el.calls)

    def test_滚动目标不存在抛NoSuchElement(self):
        page = BasePage(driver=FakePage(find_error=PlaywrightTimeoutError('gone')), timeout=1)
        with self.assertRaisesRegex(NoSuchElementException, '滚动'):
            page.scroll_to(['xpath', '//div'])


# ==========================================================================
# 五、表格行获取 / 按单元格文本查找
# ==========================================================================
class TestGetTableRows(_PageEnv):

    ROW_TEXTS = ['张三 13800000000', '李四 13900000000']

    def _page_with_rows(self):
        rows = [FakeElement(text=t) for t in self.ROW_TEXTS]
        driver = FakePage(elements=rows)
        page = BasePage(driver=driver, timeout=1)
        return page, driver

    def test_全页取行(self):
        page, driver = self._page_with_rows()
        rows = page.get_table_rows()
        # 返回的是 Locator 列表，行内容用 inner_text() 断言
        self.assertEqual([r.inner_text() for r in rows], self.ROW_TEXTS)
        self.assertEqual(driver.located_values, ['xpath=//tr'])

    def test_限定某个表格(self):
        page, driver = self._page_with_rows()
        rows = page.get_table_rows(['id', 'data-table'])
        self.assertEqual([r.inner_text() for r in rows], self.ROW_TEXTS)
        # 先定位表格本体；表格内部的 .//tr 是 Locator 派生，不再另开一次页面级查找
        self.assertEqual(driver.located_values, ['css=[id="data-table"]'])

    def test_表格不存在返回空列表(self):
        page = BasePage(driver=FakePage(find_error=PlaywrightTimeoutError('gone')), timeout=1)
        self.assertEqual(page.get_table_rows(['id', 'no-table']), [])


class TestFindRowByCell(_PageEnv):

    def _page(self, texts):
        rows = [FakeElement(text=t) for t in texts]
        return BasePage(driver=FakePage(elements=rows), timeout=1)

    def test_找到匹配行(self):
        page = self._page(['张三 13800000000', '李四 13900000000'])
        row = page.find_row_by_cell_text('李四')
        self.assertIsNotNone(row)
        # 返回的是该行 Locator：断言内容而不是元素身份
        self.assertEqual(row.inner_text(), '李四 13900000000')

    def test_无匹配返回None(self):
        page = self._page(['张三'])
        self.assertIsNone(page.find_row_by_cell_text('王五'))


# ==========================================================================
# 六、等待加载遮罩消失（三态：无遮罩 / 稍后消失 / 常驻超时）
# ==========================================================================
class TestWaitLoading(_PageEnv):

    def test_无遮罩直接返回True(self):
        # count() == 0 视为「没有遮罩」，首轮判定即返回，不产生任何等待
        driver = FakePage(find_error=PlaywrightTimeoutError('gone'))
        page = BasePage(driver=driver, timeout=1)
        self.assertTrue(page.wait_loading_gone(timeout=2))
        self.assertEqual(self.clock.now, 0.0)

    def test_默认等待的是通用遮罩特征(self):
        page = BasePage(driver=FakePage(find_error=PlaywrightTimeoutError('gone')), timeout=1)
        page.wait_loading_gone()
        self.assertEqual(page.driver.located_values, [DEFAULT_LOADING_XP])

    def test_遮罩消失后返回True(self):
        el = _VanishingElement(visible_times=2)
        driver = FakePage(element=el)
        page = BasePage(driver=driver, timeout=1)
        ok = page.wait_loading_gone(['css', '.mask'], timeout=5, poll_frequency=0.25)
        self.assertTrue(ok)
        self.assertEqual(driver.located_values, ['css=.mask'])
        # 可见 2 次 -> 轮询等待 2 个间隔（虚拟时钟推进，无真实等待）
        self.assertEqual(self.clock.now, 0.5)

    def test_遮罩超时返回False不抛异常(self):
        # 遮罩常驻：等满 timeout 后返回 False，不抛异常（交给后续断言判定）
        page = BasePage(driver=FakePage(element=FakeElement(displayed=True)), timeout=1)
        self.assertFalse(page.wait_loading_gone(timeout=1, poll_frequency=0.25))
        self.assertEqual(self.clock.now, 1.0)
        # 实现契约：轮询用 time.sleep(poll_frequency)，而不是 BasePage.my_sleep
        self.my_sleep.assert_not_called()


# ==========================================================================
# 七、GenericPage 通用登录（配置驱动）
# ==========================================================================
class TestGenericLogin(_PageEnv):

    def test_login_按配置输入并点击(self):
        el = FakeElement()
        driver = FakePage(element=el)
        gp = GenericPage(driver=driver, timeout=1)
        # DEFAULT_INI 无 [system] 段 -> 占位符/按钮文字取代码默认值，success_text 为空
        gp.login('admin', '123456')
        # 输入顺序与取值：先用户名后密码（fill）
        self.assertEqual(el.filled, ['admin', '123456'])
        # 定位顺序：两个 placeholder 输入 + 登录按钮文字；
        # 未配置 success_text 时以「等加载遮罩消失」收尾
        self.assertEqual(driver.located_values,
                         [USER_XP, PWD_XP, LOGIN_XP, DEFAULT_LOADING_XP])
        self.assertIn('click', el.calls)

    def test_login_找不到登录按钮改按回车(self):
        # 兼容没有可点击登录按钮的系统：对密码框 press('Enter') 提交
        el = FakeElement()
        driver = FakePage(element=el)
        gp = GenericPage(driver=driver, timeout=1)
        with mock.patch.object(BasePage, 'click_by_text', return_value=False):
            gp.login('admin', '123456')
        self.assertEqual(el.pressed, ['Enter'])
        # 密码框被定位两次：输入时限定 input 标签；回车提交时的补查不带 tag
        # （genericPage 调 get_element_by_attr 未传 tag -> 编译为 //*[...]），
        # 两条都钉住，防止改动定位口径时无声漂移
        self.assertEqual(driver.located_values,
                         [USER_XP, PWD_XP, "xpath=//*[@placeholder='请输入密码']",
                          DEFAULT_LOADING_XP])

    def test_login_按钮与密码框都缺失时抛异常(self):
        # 既点不到按钮、也找不到密码框 -> 明确抛错，绝不静默"假成功"
        el_user = FakeElement()
        el_pwd = FakeElement()
        gp = GenericPage(driver=FakePage(), timeout=1)
        with mock.patch.object(BasePage, 'click_by_text', return_value=False), \
                mock.patch.object(BasePage, 'get_element_by_attr',
                                  side_effect=[el_user, el_pwd, None]):
            with self.assertRaisesRegex(NoSuchElementException, '既找不到登录按钮'):
                gp.login('admin', '123456')
        self.assertEqual(el_user.filled, ['admin'])
        self.assertEqual(el_pwd.filled, ['123456'])


# ==========================================================================
# 八、配置了「成功标识」时的等待分支
#   这是接入新系统时最常用的配置（[system] success_text），
#   但之前一直没有测试覆盖，导致里面一个拼写错误潜伏了很久。
# ==========================================================================
class TestGenericLoginSuccessText(_PageEnv):
    """success_text 非空 -> 走「等成功标识出现」而不是「等加载遮罩消失」。"""

    INI = """\
[ci]
element_timeout = 9

[system]
username_placeholder = 请输入用户名
password_placeholder = 请输入密码
login_button_text = 登录
success_text = 退 出
"""

    def test_配置了成功标识时按其等待且不报错(self):
        """★ 回归保护：本分支曾把 Config.get_int 误写成 Config.getint，
        执行到这一行直接抛 AttributeError，而用例只在这条配置非空时才走到。

        修法已落到 PageObject/genericPage.py；本用例负责不让它再溜回来。
        迁移后这条等待表现为 get_element_by_text 的 wait_for，
        超时秒数统一跟随页面对象的 self.timeout（零硬编码改造后不再单独读配置，
        避免同一个超时在 [ci] 和 [system] 两处配置打架）：这里 timeout=1 -> 1000ms。
        """
        el = FakeElement()
        driver = FakePage(element=el)
        with patch_config(ini_text=self.INI):
            gp = GenericPage(driver=driver, timeout=1)
            gp.login('admin', '123456')      # 不抛异常即通过
        self.assertIn('admin', el.filled)
        # 最后一次定位就是成功标识；超时跟随 self.timeout 换算成毫秒
        self.assertEqual(driver.located_values[-1], SUCCESS_XP)
        self.assertEqual(driver.wait_fors[-1], ('attached', 1000))
        # 配置了 success_text 就不该再走加载遮罩等待
        self.assertNotIn(DEFAULT_LOADING_XP, driver.located_values)

    def test_Config的整数读取方法名是get_int(self):
        """明确钉住方法名：Config 没有 getint()，写错了只在该分支才炸。"""
        from Common.config import Config

        self.assertTrue(hasattr(Config, 'get_int'), '整数读取方法应为 get_int')
        self.assertFalse(
            hasattr(Config, 'getint'),
            'Config.getint 不存在；请用 get_int（若要保留别名需显式添加）',
        )


if __name__ == '__main__':
    unittest.main()
