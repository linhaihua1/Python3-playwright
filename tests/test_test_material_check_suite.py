# -*- coding: utf-8 -*-
"""
单元测试：TestSuites/testMaterialCheck.py（独立用例示例）

UI 用例本身要开浏览器、连真实环境，没法在单元测试里跑。
所以这里只锁住它的**结构约定与安全边界**——正是长期维护中最容易坏、
坏了又最难发现的部分：

    1. 用例数量与执行顺序（靠方法名 test_N 前缀固定）
    2. ★ 只读约束：这条用例在生产环境跑是安全的，绝不能被后来人改成会写数据
    3. 前置链路必须切标签页（忘了切窗口 = 后续全部定位失败，本工程最常见的坑）
    4. 参数全部来自配置，不硬编码账号/路径
    5. 类级前置只开一次浏览器、类级后置必须关掉浏览器

★ 不启动浏览器：所有依赖都替换成假对象。
★ 必须用 load_tests 白名单：本模块 import 了真实的用例类，
  否则 discover 会把 TestMaterialCheck 也当成用例收集，
  它的 setUpClass 会真的去打开浏览器、连生产环境。
"""

import inspect
import unittest
from unittest import mock

from tests.helpers import patch_config
from Common.basePage import BasePage
from TestSuites import testMaterialCheck as check_suite
from TestSuites.testMaterialCheck import TestMaterialCheck


# 用例执行顺序（由方法名字母序决定）
EXPECTED_ORDER = ['test_1ready', 'test_2controls', 'test_3keyword']

# 用例层源码里不允许出现的"写操作"痕迹 —— 出现即意味着它不再只读，
# 不能再在生产环境随便跑，必须重新评估。
WRITE_ACTION_BANNED = ('upload_file', 'fill_upload_form', 'submitted', 'save')

# 专供本文件使用的配置：故意让每个值都区别于代码里的兜底默认值，
# 这样才能证明"用例参数真的读到了配置"，而不是恰好命中了硬编码的默认值。
CHECK_INI = """\
[testcase]
login_username = unit_user
login_password = p%ss%word
query_keyword = 单元关键字

[assert]
title_material = 配置里的素材库标题

[ci]
element_timeout = 7
"""


class TestCaseStructure(unittest.TestCase):
    """用例类自身的结构约定。"""

    def test_用例数量与顺序固定(self):
        names = unittest.TestLoader().getTestCaseNames(TestMaterialCheck)
        self.assertEqual(
            names, EXPECTED_ORDER,
            '用例执行顺序由方法名决定，改动方法名会改变执行顺序',
        )

    def test_所有用例方法都以test下划线加序号开头(self):
        for name in unittest.TestLoader().getTestCaseNames(TestMaterialCheck):
            self.assertRegex(name, r'^test_\d',
                             '用例方法名必须是 test_数字 形式，否则无法固定执行顺序')

    def test_每条用例都有中文docstring(self):
        """新人靠 docstring 理解每条用例在测什么。"""
        for name in EXPECTED_ORDER:
            doc = getattr(TestMaterialCheck, name).__doc__
            self.assertTrue(doc and doc.strip(), '用例 %s 缺少说明' % name)

    def test_浏览器在类级前置后置中管理(self):
        self.assertTrue(callable(getattr(TestMaterialCheck, 'setUpClass', None)))
        self.assertTrue(callable(getattr(TestMaterialCheck, 'tearDownClass', None)))
        self.assertIsNone(TestMaterialCheck.driver, '类属性 driver 初始应为 None')

    def test_用例里不硬编码账号与路径(self):
        source = inspect.getsource(check_suite)
        for banned in ('your_username', 'your_password', '.mp4', 'D:\\'):
            self.assertNotIn(banned, source, '用例层不应硬编码：%s' % banned)

    def test_用例层不直接写xpath(self):
        """xpath 只允许出现在 Common/elementPage.py。"""
        self.assertNotIn('//', inspect.getsource(check_suite))


class TestReadOnlyGuarantee(unittest.TestCase):
    """★ 只读保证：这条用例能在生产环境安全执行，靠的就是下面几条。"""

    def test_不出现任何写操作(self):
        source = inspect.getsource(check_suite)
        for banned in WRITE_ACTION_BANNED:
            self.assertNotIn(
                banned, source,
                '一旦引入写操作（%s），本用例就不再是只读的，'
                '必须重新评估能否在生产环境执行' % banned,
            )

    def test_前置链路包含切换标签页(self):
        """点「发布系统」会新开标签页，不切过去后面全部定位失败 —— 本工程最常见的坑。"""
        source = inspect.getsource(TestMaterialCheck.setUpClass)
        self.assertIn('switch_other_window', source,
                      '前置链路缺少切换标签页，进了生产系统也无法操作')

    def test_前置链路走完进入素材库(self):
        source = inspect.getsource(TestMaterialCheck.setUpClass)
        for step in ('login', 'click_sign_in', 'click_material'):
            self.assertIn(step, source, '前置链路缺少步骤：%s' % step)


class TestSetUpClass(unittest.TestCase):
    """类级前置：参数来自配置，且只开一次浏览器。"""

    def _run_with_fakes(self):
        """用假 driver 跑一遍 setUpClass，返回（假基类, 假页面对象）。"""
        fake_base = mock.MagicMock()
        fake_base.open_browser.return_value = 'fake-driver'
        fake_page = mock.MagicMock()
        with patch_config(ini_text=CHECK_INI):
            with mock.patch.object(check_suite, 'BasePage', return_value=fake_base), \
                    mock.patch.object(check_suite, 'LoginPage', return_value=fake_page):
                TestMaterialCheck.setUpClass()
        return fake_base, fake_page

    def tearDown(self):
        # setUpClass 会往类属性上写东西，跑完清理掉，避免影响其它用例
        for attr in ('driver', 'base', 'page', 'username', 'password',
                     'title_material'):
            if hasattr(TestMaterialCheck, attr):
                delattr(TestMaterialCheck, attr)
        # 零硬编码改造后，类定义里的 title_material 只是 None 占位（值必须来自
        # 配置），删掉临时写入的值后要按 None 补回去
        TestMaterialCheck.driver = None
        TestMaterialCheck.title_material = None

    def test_账号来自配置而非硬编码(self):
        _, page = self._run_with_fakes()
        page.login.assert_called_once_with('unit_user', 'p%ss%word')

    def test_期望标题来自配置(self):
        self._run_with_fakes()
        self.assertEqual(TestMaterialCheck.title_material, '配置里的素材库标题')

    def test_只打开一次浏览器(self):
        fake_base, _ = self._run_with_fakes()
        self.assertEqual(fake_base.open_browser.call_count, 1,
                         '必须整个类只开一次浏览器，每条用例都开会慢好几倍')

    def test_前置链路按顺序执行(self):
        _, page = self._run_with_fakes()
        calls = [name for name, _args, _kw in page.method_calls]
        # 等待走 page_wait（秒数由 config [timing] 驱动，代码零硬编码），
        # 不再是写死秒数的 my_sleep
        expected = ['login', 'click_sign_in', 'switch_other_window',
                    'page_wait', 'click_material', 'page_wait']
        self.assertEqual(calls, expected, '前置链路的顺序不能乱')

    def test_类级后置会关闭浏览器(self):
        fake_base = mock.MagicMock()
        TestMaterialCheck.base = fake_base
        TestMaterialCheck.tearDownClass()
        fake_base.dr_quit.assert_called_once()


def load_tests(loader, standard_tests, pattern):
    """显式声明本模块要执行的测试类。

    本模块 import 了真实的 TestMaterialCheck 来做结构校验，
    若不声明，discover 会把 TestMaterialCheck 也当成用例运行 ——
    那样 setUpClass 会真的打开浏览器去连生产环境。详见 tests/__init__.py 的说明。
    """
    suite = unittest.TestSuite()
    for cls in (TestCaseStructure, TestReadOnlyGuarantee, TestSetUpClass):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite
