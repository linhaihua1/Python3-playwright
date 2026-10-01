# -*- coding: utf-8 -*-
"""
单元测试：TestSuites/testLogin.py（UI 用例层）

UI 用例本身要开浏览器、连真实环境，无法在单元测试里跑。
所以这里只验证用例层的「结构约定」与「自定义断言」——这些恰恰是
长期维护中最容易被破坏、又最难发现的部分：

    - 执行顺序靠方法名 test_N 前缀固定，改名会改变顺序 -> 用测试锁住
    - 参数全部来自 config.ini，不允许硬编码账号/路径
    - check_title 失败时必须截图并抛出，成功时不能误报
    - 类级前置只开一次浏览器、类级后置必须关掉浏览器

★ 不启动浏览器：所有依赖都替换成假对象。
"""

import ast
import inspect
import os
import unittest
from unittest import mock

from tests.helpers import patch_config
from Common.basePage import BasePage
from TestSuites import testLogin as login_suite
from TestSuites.testLogin import TestLogin


# 用例执行顺序（由方法名的字母序决定，即 test_1 .. test_6）
EXPECTED_ORDER = [
    'test_1login',
    'test_2select_scxt',
    'test_3material',
    'test_4uplaodbutton',
    'test_5keyword',
    'test_6submitted',
]


class TestCaseStructure(unittest.TestCase):
    """用例类自身的结构约定。"""

    def test_用例数量与顺序固定(self):
        names = unittest.TestLoader().getTestCaseNames(TestLogin)
        self.assertEqual(
            names, EXPECTED_ORDER,
            '用例执行顺序由方法名决定，改动方法名会改变执行顺序并导致失败',
        )

    def test_所有用例方法都以test下划线加序号开头(self):
        for name in unittest.TestLoader().getTestCaseNames(TestLogin):
            self.assertRegex(name, r'^test_\d',
                             '用例方法名必须是 test_数字 形式，否则无法固定执行顺序')

    def test_每条用例都有中文docstring(self):
        """新人靠 docstring 理解每条用例在测什么。"""
        for name in EXPECTED_ORDER:
            doc = getattr(TestLogin, name).__doc__
            self.assertTrue(doc and doc.strip(), '用例 %s 缺少说明' % name)

    def test_浏览器在类级前置后置中管理(self):
        """6 条用例共用一次浏览器会话 —— 拆成每条都开会多登 6 次且更不稳。"""
        self.assertTrue(callable(getattr(TestLogin, 'setUpClass', None)))
        self.assertTrue(callable(getattr(TestLogin, 'tearDownClass', None)))
        self.assertIsNone(TestLogin.driver, '类属性 driver 初始应为 None')

    def test_用例里不硬编码账号与路径(self):
        """账号、上传文件路径必须来自配置，代码里不该出现明文。"""
        source = inspect.getsource(login_suite)
        for banned in ('linhaihua', 'qwer1234', 'BGXF', '.mp4'):
            self.assertNotIn(banned, source, '用例层不应硬编码：%s' % banned)

    def test_用例层不直接写xpath(self):
        source = inspect.getsource(login_suite)
        self.assertNotIn('//', source)


class TestSetUpClass(unittest.TestCase):
    """类级前置：参数来自配置，且只开一次浏览器。"""

    def setUp(self):
        self._config = patch_config()
        self._config.__enter__()
        self._saved = {key: getattr(TestLogin, key, None)
                       for key in ('username', 'password', 'upload_file',
                                   'video_author', 'phone', 'reporting',
                                   'query_keyword', 'title_login',
                                   'title_workbench', 'title_material',
                                   'base', 'driver', 'login_page')}

    def tearDown(self):
        # 还原类属性：setUpClass 会往类上挂 username/driver 等，别污染其它测试
        for key, value in self._saved.items():
            setattr(TestLogin, key, value)
        self._config.__exit__(None, None, None)

    def test_参数全部来自配置(self):
        """setUpClass 的业务数据只能来自配置：get/require 两条通道都要桩。

        零硬编码改造后，phone/reporting/query_keyword/title_* 走 Config.require
        （缺失即报错），video_author 走 Config.get（留空回退账号名）。
        """
        fake_config = mock.MagicMock()
        fake_config.get_account.return_value = ('cfg_user', 'cfg_pwd')
        fake_config.get_upload_file.return_value = '/tmp/演示.mp4'
        fake_config.get.side_effect = lambda section, key, *rest: {
            ('testcase', 'video_author'): '作者',
        }.get((section, key))
        fake_config.require.side_effect = lambda section, key, *rest: {
            ('testcase', 'phone'): '13900000000',
            ('testcase', 'reporting'): '广州',
            ('testcase', 'query_keyword'): '单元关键字',
            ('assert', 'title_login'): '配置门户',
            ('assert', 'title_workbench'): '配置工作台',
            ('assert', 'title_material'): '配置素材库',
        }[(section, key)]   # 没登记的键直接 KeyError：防止漏桩把测试蒙过去

        fake_base = mock.MagicMock()
        fake_driver = mock.MagicMock(name='driver')
        fake_base.open_browser.return_value = fake_driver

        with mock.patch.object(login_suite, 'Config', fake_config), \
                mock.patch.object(login_suite, 'BasePage', return_value=fake_base), \
                mock.patch.object(login_suite, 'LoginPage') as fake_login_page:
            TestLogin.setUpClass()

        self.assertEqual(TestLogin.username, 'cfg_user')
        self.assertEqual(TestLogin.password, 'cfg_pwd')
        self.assertEqual(TestLogin.upload_file, '/tmp/演示.mp4')
        self.assertEqual(TestLogin.video_author, '作者')
        self.assertEqual(TestLogin.phone, '13900000000')
        self.assertEqual(TestLogin.reporting, '广州')
        self.assertEqual(TestLogin.query_keyword, '单元关键字')
        self.assertEqual(TestLogin.title_login, '配置门户')
        self.assertEqual(TestLogin.title_material, '配置素材库')
        self.assertEqual(TestLogin.driver, fake_driver)
        self.assertEqual(fake_base.open_browser.call_count, 1,
                         '整个类只应打开一次浏览器')
        fake_login_page.assert_called_once_with(fake_driver)

    def test_业务配置缺失时前置直接失败(self):
        """require 语义：断言标题没配 -> setUpClass 抛 ConfigMissingError，
        绝不带着 None 期望值往下跑（零硬编码纪律的守卫用例）。"""
        from Common.config import ConfigMissingError
        fake_config = mock.MagicMock()
        fake_config.get_account.return_value = ('u', 'p')
        fake_config.get_upload_file.return_value = '/tmp/a.mp4'
        fake_config.require.side_effect = ConfigMissingError('assert', 'title_login')

        fake_base = mock.MagicMock()
        fake_base.open_browser.return_value = mock.MagicMock()
        with mock.patch.object(login_suite, 'Config', fake_config), \
                mock.patch.object(login_suite, 'BasePage', return_value=fake_base), \
                mock.patch.object(login_suite, 'LoginPage'):
            with self.assertRaises(ConfigMissingError):
                TestLogin.setUpClass()

    def test_视频作者缺省时回退为账号名(self):
        fake_config = mock.MagicMock()
        fake_config.get_account.return_value = ('only_user', 'pwd')
        fake_config.get_upload_file.return_value = '/tmp/a.mp4'
        # 模拟真实 Config.get 的语义：取不到就把调用方给的默认值还回去
        fake_config.get.side_effect = lambda section, key, *rest: rest[0] if rest else None

        fake_base = mock.MagicMock()
        with mock.patch.object(login_suite, 'Config', fake_config), \
                mock.patch.object(login_suite, 'BasePage', return_value=fake_base), \
                mock.patch.object(login_suite, 'LoginPage'):
            TestLogin.setUpClass()

        self.assertEqual(TestLogin.video_author, 'only_user')

    def test_类级后置关闭浏览器(self):
        fake_base = mock.MagicMock()
        TestLogin.base = fake_base
        TestLogin.tearDownClass()
        self.assertEqual(fake_base.dr_quit.call_count, 1)


class TestCheckTitle(unittest.TestCase):
    """自定义断言 check_title 的行为。"""

    def _make_case(self, title):
        case = TestLogin('test_1login')
        case.login_page = mock.MagicMock()
        case.login_page.get_title.return_value = title
        return case

    def test_标题一致时不抛异常(self):
        case = self._make_case('统一门户')
        case.check_title('登录成功', '统一门户')   # 不应抛出

    def test_标题不一致时断言失败(self):
        case = self._make_case('别的标题')
        with self.assertRaises(AssertionError):
            case.check_title('登录成功', '统一门户')

    def test_失败时自动截图(self):
        """失败瞬间的截图是排查问题最有效的证据。"""
        case = self._make_case('别的标题')
        with self.assertRaises(AssertionError):
            case.check_title('登录成功', '统一门户')
        self.assertEqual(case.login_page.get_img.call_count, 1)

    def test_成功时不截图(self):
        case = self._make_case('统一门户')
        case.check_title('登录成功', '统一门户')
        self.assertEqual(case.login_page.get_img.call_count, 0)

    def test_断言失败信息包含期望与实际(self):
        case = self._make_case('实际标题')
        with self.assertRaises(AssertionError) as ctx:
            case.check_title('登录成功', '期望标题')
        self.assertIn('期望标题', str(ctx.exception))
        self.assertIn('实际标题', str(ctx.exception))


class TestCaseBodiesAreWired(unittest.TestCase):
    """每条用例都真实调用页面对象（防止空用例混进来）。"""

    def _run_case(self, method_name, title='素材库 - 云媒资系统'):
        case = TestLogin(method_name)
        case.username = 'u'
        case.password = 'p'
        case.upload_file = None
        case.video_author = 'a'
        case.phone = '1'
        case.reporting = 'r'
        case.query_keyword = 'kw'
        # 断言期望值不再写死在代码里（类属性默认 None），真实跑用例体前
        # 必须像 setUpClass 那样从"配置"灌入这些值
        case.title_login = title
        case.title_workbench = title
        case.title_material = title
        case.driver = mock.MagicMock()
        case.login_page = mock.MagicMock()
        case.login_page.get_title.return_value = title
        getattr(case, method_name)()
        return case

    def test_用例1调用登录(self):
        case = self._run_case('test_1login', title='统一门户')
        case.login_page.login.assert_called_once_with('u', 'p')

    def test_用例2切换标签页(self):
        case = self._run_case('test_2select_scxt', title='工作台 - 云媒资系统')
        case.login_page.click_sign_in.assert_called_once()
        case.login_page.switch_other_window.assert_called_once()

    def test_用例3进入素材库(self):
        case = self._run_case('test_3material')
        case.login_page.click_material.assert_called_once()

    def test_用例4上传并填表单(self):
        case = self._run_case('test_4uplaodbutton')
        case.login_page.upload_file.assert_called_once()
        case.login_page.fill_upload_form.assert_called_once_with('a', '1', 'r')

    def test_用例5关键字查询(self):
        case = self._run_case('test_5keyword')
        case.login_page.keyword_query.assert_called_once()

    def test_用例6刷新后报送(self):
        case = self._run_case('test_6submitted')
        # 迁移 Playwright 后，刷新改走页面对象继承的 BasePage.refresh()（page.reload），
        # 不再绕过分层直接 self.driver.refresh() —— 因为切标签页后旧 driver 引用
        # 不会自动指向新页面，直接刷新会刷错标签页。断言随之更新。
        case.login_page.refresh.assert_called_once()
        case.login_page.submitted.assert_called_once()


class TestModuleHygiene(unittest.TestCase):
    """模块级约定。"""

    def test_模块可被unittest收集到6条用例(self):
        import unittest as ut
        suite = ut.defaultTestLoader.loadTestsFromTestCase(TestLogin)
        self.assertEqual(suite.countTestCases(), 6)

    def test_样例模块不会被本工程用例目录收集(self):
        """tests/samples.py 只是测试数据，不应混进 TestSuites。"""
        self.assertFalse(os.path.exists(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'TestSuites', 'samples.py')))

    def test_用例类只依赖约定好的模块(self):
        tree = ast.parse(inspect.getsource(login_suite))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertIn('Common.config', imported)
        self.assertIn('PageObject.loginPage', imported)


def load_tests(loader, standard_tests, pattern):
    """显式声明本模块要执行的测试类。

    【为什么必须这么写】
        本模块需要 import 真实用例类 TestLogin 来做结构校验，
        而 unittest 在加载模块时会把模块命名空间里**所有** TestCase 子类
        都当成用例收走 —— 那样 TestLogin 的 setUpClass 会被真的执行
        （尝试打开浏览器、连生产环境），测试直接崩。
        用 load_tests 只返回本文件自己的测试类，彻底避免误收集。

        同理，tests/samples.py 也是用「模块方式引用」来规避同一问题。
    """
    suite = unittest.TestSuite()
    for case in (TestCaseStructure, TestSetUpClass, TestCheckTitle,
                 TestCaseBodiesAreWired, TestModuleHygiene):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite


if __name__ == '__main__':
    unittest.main(verbosity=2)
