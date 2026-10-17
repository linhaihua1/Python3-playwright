# -*- coding: utf-8 -*-
"""
单元测试：Common/runner.py（测试执行器）

这是工程的"发动机"，也是 CI 判定构建成败的出口，因此覆盖两类内容：

一、纯函数（不起浏览器、不写文件，毫秒级）
    - 用例关键字过滤（冒烟集就靠它）
    - 收集前的目录体检 diagnose_collection
    - 导入失败占位对象的识别
    - JUnit XML 路径解析
    - 失败摘要打印

二、端到端 run()（在临时沙盒里跑 dummy 用例）
    重点验证**退出码契约**，这是流水线唯一的判据：
        0 = 全部通过
        1 = 有用例失败/出错
        2 = 环境或配置问题（没收集到用例、冒烟集为空等）
    并确认报告与 XML 的产出、以及"报告不能是空文件"。

★ 沙盒里跑的是临时生成的 dummy 用例，绝不触碰真实 UI 用例与生产系统。
"""

import contextlib
import io
import os
import unittest
import xml.etree.ElementTree as ET
from unittest.loader import _FailedTest

from tests import samples
from tests.helpers import (
    CASE_SOURCE_MIXED,
    CASE_SOURCE_PASS,
    CASE_SOURCE_SETUPCLASS_BOOM,
    CASE_SOURCE_SKIP,
    make_sandbox,
    patch_config,
    patch_dirs,
)
from Common import runner


# ==========================================================================
# 一、纯函数
# ==========================================================================
class TestSplitKeywords(unittest.TestCase):
    """关键字拆分。"""

    def test_空值返回空列表(self):
        self.assertEqual(runner._split_keywords(None), [])
        self.assertEqual(runner._split_keywords(''), [])

    def test_支持逗号空格分号与换行(self):
        self.assertEqual(
            runner._split_keywords('a,b c;d\ne'),
            ['a', 'b', 'c', 'd', 'e'],
        )

    def test_忽略连续分隔符产生的空项(self):
        self.assertEqual(runner._split_keywords('a,,b ,'), ['a', 'b'])

    def test_支持列表入参(self):
        self.assertEqual(runner._split_keywords(['a', '', ' b ']), ['a', 'b'])


class TestFilterSuite(unittest.TestCase):
    """按用例方法名过滤。"""

    def setUp(self):
        self.suite = unittest.TestSuite([
            samples.TwoPassCases('test_ok'),
            samples.TwoPassCases('test_ok2'),
            samples.FailCase('test_fail'),
        ])

    def test_关键字命中(self):
        filtered = runner.filter_suite(self.suite, 'test_fail')
        self.assertEqual(filtered.countTestCases(), 1)

    def test_是部分匹配而非精确匹配(self):
        """'test_ok' 会同时命中 test_ok 与 test_ok2 —— 这是**刻意**的语义，
        因为冒烟配置里写的是方法名片段。写配置时要留意这个特性。"""
        filtered = runner.filter_suite(self.suite, 'test_ok')
        self.assertEqual(filtered.countTestCases(), 2)

    def test_支持部分匹配(self):
        filtered = runner.filter_suite(self.suite, 'test_')
        self.assertEqual(filtered.countTestCases(), 3)

    def test_大小写不敏感(self):
        filtered = runner.filter_suite(self.suite, 'TEST_OK2')
        self.assertEqual(filtered.countTestCases(), 1)

    def test_多关键字取并集(self):
        filtered = runner.filter_suite(self.suite, 'test_ok2,test_fail')
        self.assertEqual(filtered.countTestCases(), 2)

    def test_未命中时返回空套件(self):
        filtered = runner.filter_suite(self.suite, '根本不存在的用例')
        self.assertEqual(filtered.countTestCases(), 0)

    def test_关键字为空时不过滤(self):
        self.assertEqual(runner.filter_suite(self.suite, '').countTestCases(), 3)

    def test_嵌套套件也能展开过滤(self):
        nested = unittest.TestSuite([self.suite, unittest.TestSuite(
            [samples.TwoPassCases('test_ok2')])])
        # 过滤后：内层 1 条 + 外层 1 条 = 2 条
        self.assertEqual(runner.filter_suite(nested, 'test_fail').countTestCases(), 1)
        self.assertEqual(runner.filter_suite(nested, 'test_ok2').countTestCases(), 2)


class TestResolveCaseFilter(unittest.TestCase):
    """决定这次跑哪些用例。"""

    def test_全量模式不过滤(self):
        with patch_config():
            self.assertEqual(runner.resolve_case_filter('full'), [])

    def test_冒烟模式读取配置(self):
        with patch_config():
            self.assertEqual(
                runner.resolve_case_filter('smoke'),
                ['test_1login', 'test_2select_scxt'],
            )

    def test_显式cases优先级最高(self):
        with patch_config():
            self.assertEqual(
                runner.resolve_case_filter('smoke', 'test_x,test_y'),
                ['test_x', 'test_y'],
            )

    def test_冒烟集为空时返回空列表(self):
        ini = '[ci]\nsmoke_cases =\n'
        with patch_config(ini_text=ini):
            self.assertEqual(runner.resolve_case_filter('smoke'), [])


class TestDiagnoseCollection(unittest.TestCase):
    """收集前的目录体检。"""

    def test_目录不存在时给出提示(self):
        lines = runner.diagnose_collection(os.path.join('不存在的目录', 'x'))
        text = '\n'.join(lines)
        self.assertIn('用例目录不存在', text)

    def test_缺包标识文件时给出提示(self):
        import shutil
        root, cases_dir, _ = make_sandbox(CASE_SOURCE_PASS, with_init=False)
        try:
            text = '\n'.join(runner.diagnose_collection(cases_dir))
            self.assertIn('【缺失】', text)
            self.assertIn('__init__.py', text)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_包标识存在时报告正常(self):
        root, cases_dir, _ = make_sandbox(CASE_SOURCE_PASS)
        try:
            text = '\n'.join(runner.diagnose_collection(cases_dir))
            self.assertIn('存在', text)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_非法文件名被点名(self):
        root, cases_dir, _ = make_sandbox(CASE_SOURCE_PASS)
        try:
            with open(os.path.join(cases_dir, 'test-login.py'), 'w') as fp:
                fp.write('import unittest\n')
            text = '\n'.join(runner.diagnose_collection(cases_dir))
            self.assertIn('文件名非法', text)
            self.assertIn('test-login.py', text)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_pattern未命中时给出提示(self):
        root, cases_dir, _ = make_sandbox(CASE_SOURCE_PASS, case_filename='demo_test.py')
        try:
            text = '\n'.join(runner.diagnose_collection(cases_dir, pattern='zzz*.py'))
            self.assertIn('pattern 没匹配到任何文件', text)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_子目录缺包标识被点名(self):
        root, cases_dir, _ = make_sandbox(CASE_SOURCE_PASS)
        try:
            os.makedirs(os.path.join(cases_dir, 'sub'))
            text = '\n'.join(runner.diagnose_collection(cases_dir))
            self.assertIn('sub/', text)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    @unittest.skipUnless(os.name == 'nt', '大小写差异只在 Windows 上才会造成误判')
    def test_Windows上提示大小写隐患(self):
        """Windows 能匹配到 TestLogin.py，Linux 匹配不到 —— 这正是
        "本机能跑、Jenkins 收集不到" 的根因，必须被点名。"""
        root, cases_dir, _ = make_sandbox(CASE_SOURCE_PASS)
        try:
            with open(os.path.join(cases_dir, 'TestLogin.py'), 'w') as fp:
                fp.write('import unittest\n')
            text = '\n'.join(runner.diagnose_collection(cases_dir))
            self.assertIn('大小写隐患', text)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)


class TestFindBrokenModules(unittest.TestCase):
    """识别导入失败的占位用例。"""

    def test_识别FailedTest占位对象(self):
        # 注意：_FailedTest 的构造签名随 Python 版本变化
        # （3.11 起为 (method_name, exception)），这里只依赖公开行为
        broken_case = _FailedTest('test_x', ImportError('No module named playwright'))
        suite = unittest.TestSuite([broken_case])
        found = runner.find_broken_modules(suite)
        self.assertEqual(len(found), 1)
        case, exc = found[0]
        self.assertIsInstance(exc, ImportError)
        self.assertIn('playwright', str(exc))

    def test_正常用例不会被误判(self):
        suite = unittest.TestSuite([samples.TwoPassCases('test_ok')])
        self.assertEqual(runner.find_broken_modules(suite), [])


class TestResolveJunitPath(unittest.TestCase):
    """JUnit XML 输出路径解析。"""

    def test_默认取配置并拼到工程根(self):
        with patch_config(), patch_dirs() as root:
            expected = os.path.normpath(os.path.join(root, 'Report', 'junit-results.xml'))
            self.assertEqual(runner.resolve_junit_path(), expected)

    def test_传none表示关闭(self):
        with patch_config(), patch_dirs():
            for value in ('none', 'off', 'false', '0', 'no', 'NONE'):
                self.assertIsNone(runner.resolve_junit_path(value))

    def test_传空字符串表示关闭(self):
        with patch_config(), patch_dirs():
            self.assertIsNone(runner.resolve_junit_path(''))

    def test_相对路径随报告目录解析(self):
        """相对路径写文件名即与 HTML 报告同住（隔离时同进 runId 子目录）。

        旧行为是相对工程根解析 —— 隔离开启后报告进了 Report/<runId>/ 而
        JUnit 留在 Report/ 根下，两个并发构建会互相覆盖 JUnit 结果。
        新语义：取文件名 join 到报告目录。旧写法 'Report/xxx.xml' 中的
        目录段会被剥掉（避免套娃成 Report/<runId>/Report/）。
        """
        with patch_config(), patch_dirs() as root:
            path = runner.resolve_junit_path('out/res.xml')
            expected = os.path.join(root, 'Report', 'res.xml')
            self.assertEqual(path, os.path.normpath(expected))

    def test_旧写法Report前缀被剥掉(self):
        with patch_config(), patch_dirs() as root:
            path = runner.resolve_junit_path('Report/junit-results.xml')
            expected = os.path.join(root, 'Report', 'junit-results.xml')
            self.assertEqual(path, os.path.normpath(expected))
            self.assertNotIn(os.path.join('Report', 'Report'), path,
                             '不应套娃出 Report/Report/')

    def test_绝对路径原样保留(self):
        with patch_config(), patch_dirs() as root:
            absolute = os.path.join(root, 'x.xml')
            self.assertEqual(runner.resolve_junit_path(absolute), os.path.normpath(absolute))


class TestPrintFailureDigest(unittest.TestCase):
    """控制台失败摘要。"""

    def test_无失败时不输出(self):
        from Common.junitReport import JUnitResult
        result = JUnitResult(verbosity=0)
        unittest.TestSuite([samples.TwoPassCases('test_ok')]).run(result)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            runner.print_failure_digest(result)
        self.assertEqual(buffer.getvalue().strip(), '')

    def test_有失败时输出用例与原因(self):
        from Common.junitReport import JUnitResult
        result = JUnitResult(verbosity=0)
        unittest.TestSuite([samples.FailCase('test_fail')]).run(result)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            runner.print_failure_digest(result)
        output = buffer.getvalue()
        self.assertIn('失败摘要', output)
        self.assertIn('test_fail', output)
        self.assertIn('故意失败', output)


class TestSummaryHelper(unittest.TestCase):
    """返回结构固定化。"""

    def test_缺省字段齐全(self):
        summary = runner._summary()
        for key in ('exit_code', 'tests_run', 'failures', 'errors', 'skipped',
                    'report', 'junit', 'duration', 'cases', 'success'):
            self.assertIn(key, summary)

    def test_success与退出码联动(self):
        self.assertTrue(runner._summary(exit_code=runner.EXIT_OK)['success'])
        self.assertFalse(runner._summary(exit_code=runner.EXIT_TEST_FAILED)['success'])
        self.assertFalse(runner._summary(exit_code=runner.EXIT_ENV_ERROR)['success'])

    def test_退出码常量值不可随意改动(self):
        """CI 流水线按这三个数字判定构建成败，改动等于破坏契约。"""
        self.assertEqual(runner.EXIT_OK, 0)
        self.assertEqual(runner.EXIT_TEST_FAILED, 1)
        self.assertEqual(runner.EXIT_ENV_ERROR, 2)


# ==========================================================================
# 二、端到端 run()
# ==========================================================================
class _RunTestCase(unittest.TestCase):
    """基类：提供沙盒 + 隔离的配置与目录。"""

    def make_env(self, case_source=CASE_SOURCE_PASS, ini_text=None, with_init=True):
        root, cases_dir, pkg = make_sandbox(case_source, with_init=with_init,
                                            ini_text=ini_text)
        # 配置指向沙盒里那份 config.ini，保持"工程结构"与"配置来源"一致
        config_ctx = patch_config(ini_text=ini_text,
                                  ini_path=os.path.join(root, 'config.ini'))
        config_ctx.__enter__()
        dirs_ctx = patch_dirs(root=root)
        dirs_ctx.__enter__()
        self.addCleanup(lambda: self._cleanup(config_ctx, dirs_ctx))
        return root, cases_dir, pkg

    @staticmethod
    def _cleanup(config_ctx, dirs_ctx):
        with contextlib.suppress(Exception):
            dirs_ctx.__exit__(None, None, None)
        with contextlib.suppress(Exception):
            config_ctx.__exit__(None, None, None)

    def run_runner(self, **kwargs):
        """执行 runner.run 并把控制台输出吞掉，避免污染测试输出。"""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            summary = runner.run(**kwargs)
        return summary, buffer.getvalue()


class TestRunEndToEnd(_RunTestCase):
    """端到端执行：退出码 + 产物。"""

    def test_全部通过时退出码为0(self):
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS)
        summary, output = self.run_runner(
            tests_dir=cases_dir, pattern='demo_test.py', title='单测报告',
            report_name='单测报告.html', suite_kind='full')
        self.assertEqual(summary['exit_code'], runner.EXIT_OK)
        self.assertEqual(summary['tests_run'], 2)
        self.assertEqual(summary['failures'], 0)
        self.assertTrue(summary['success'])

    def test_通过时生成非空HTML报告(self):
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py',
                                     report_name='单测报告.html')
        self.assertTrue(os.path.exists(summary['report']))
        self.assertGreater(os.path.getsize(summary['report']), 1000,
                           '报告不能是空文件')

    def test_通过时生成合法JUnit_XML(self):
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS)
        summary, _ = self.run_runner(
            tests_dir=cases_dir, pattern='demo_test.py',
            junit='Report/junit-results.xml')
        self.assertTrue(os.path.exists(summary['junit']))
        root_node = ET.parse(summary['junit']).getroot()
        self.assertEqual(root_node.get('tests'), '2')
        self.assertEqual(root_node.get('failures'), '0')

    def test_XML里带上被测环境等属性(self):
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py',
                                     junit='Report/junit-results.xml')
        root_node = ET.parse(summary['junit']).getroot()
        props = {node.get('name'): node.get('value')
                 for node in root_node.findall('.//property')}
        self.assertEqual(props.get('url'), 'http://test-env.local/sso-portal')
        self.assertEqual(props.get('browser'), 'chrome')

    def test_用例失败时退出码为1(self):
        root, cases_dir, _ = self.make_env(CASE_SOURCE_MIXED)
        summary, output = self.run_runner(
            tests_dir=cases_dir, pattern='demo_test.py',
            junit='Report/junit-results.xml')
        self.assertEqual(summary['exit_code'], runner.EXIT_TEST_FAILED)
        self.assertEqual(summary['tests_run'], 2)
        self.assertEqual(summary['failures'], 1)
        self.assertIn('故意失败', output, '失败摘要应打印到控制台，便于流水线排查')

    def test_setUpClass异常时退出码为1且报告非空(self):
        """浏览器起不来是最常见的异常，此时必须仍能拿到有内容的报告。"""
        root, cases_dir, _ = self.make_env(CASE_SOURCE_SETUPCLASS_BOOM)
        summary, _ = self.run_runner(
            tests_dir=cases_dir, pattern='demo_test.py',
            report_name='异常报告.html', junit='Report/junit-results.xml')
        self.assertEqual(summary['exit_code'], runner.EXIT_TEST_FAILED)
        self.assertEqual(summary['errors'], 1)
        self.assertGreater(os.path.getsize(summary['report']), 0)
        with open(summary['report'], 'rb') as fp:
            self.assertIn('模拟浏览器起不来', fp.read().decode('utf-8', errors='replace'))

    def test_跳过的用例不算失败(self):
        root, cases_dir, _ = self.make_env(CASE_SOURCE_SKIP)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py',
                                     junit='Report/junit-results.xml')
        self.assertEqual(summary['exit_code'], runner.EXIT_OK)
        self.assertEqual(summary['skipped'], 1)

    def test_关闭junit时不生成XML(self):
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py',
                                     junit='none')
        self.assertIsNone(summary['junit'])
        self.assertFalse(os.path.exists(os.path.join(root, 'Report', 'junit-results.xml')))

    def test_报告文件名自动补html后缀(self):
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py',
                                     report_name='没有后缀')
        self.assertTrue(summary['report'].endswith('.html'))


class TestRunEnvErrors(_RunTestCase):
    """环境/配置类问题必须归到退出码 2，而不是 1。"""

    def test_缺包标识文件时退出码为2(self):
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS, with_init=False)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py')
        self.assertEqual(summary['exit_code'], runner.EXIT_ENV_ERROR)
        self.assertEqual(summary['tests_run'], 0)
        self.assertIsNone(summary['report'], '未收集到用例时不该生成报告')

    def test_用例目录不存在时退出码为2(self):
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS)
        summary, _ = self.run_runner(tests_dir=os.path.join(root, '没有这个目录'),
                                     pattern='demo_test.py')
        self.assertEqual(summary['exit_code'], runner.EXIT_ENV_ERROR)

    def test_过滤关键字未命中时退出码为2(self):
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py',
                                     cases='根本不存在的用例')
        self.assertEqual(summary['exit_code'], runner.EXIT_ENV_ERROR)

    # 零硬编码改造后，[report] 四个键是业务数据（缺失即 exit 2），
    # 自定义 ini_text 会整体替换 DEFAULT_INI，所以这些测试必须自带 [report]，
    # 才能把"退出码问题"聚焦在各自真正要考察的点上。
    _REPORT_INI = ('[report]\ntitle = 沙盒报告\nfilename = sandbox.html\n'
                   'tester = bot\ndescription = 沙盒\n')

    def test_冒烟集为空时中止且不跑全量(self):
        """关键安全设计：冒烟集误删时若继续跑全量，会真实上传数据。"""
        ini = self._REPORT_INI + '[ci]\nsmoke_cases =\n'
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS, ini_text=ini)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py',
                                     suite_kind='smoke')
        self.assertEqual(summary['exit_code'], runner.EXIT_ENV_ERROR)
        self.assertEqual(summary['tests_run'], 0)
        self.assertIsNone(summary['report'])

    def test_冒烟模式只跑命中的用例(self):
        ini = self._REPORT_INI + '[ci]\nsmoke_cases = test_a_pass\n'
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS, ini_text=ini)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py',
                                     suite_kind='smoke')
        self.assertEqual(summary['exit_code'], runner.EXIT_OK)
        self.assertEqual(summary['tests_run'], 1)

    def test_显式cases参数覆盖冒烟配置(self):
        ini = self._REPORT_INI + '[ci]\nsmoke_cases = test_a_pass\n'
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS, ini_text=ini)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py',
                                     suite_kind='smoke', cases='test_b_pass')
        self.assertEqual(summary['tests_run'], 1)
        names = [item['name'] for item in summary['cases']]
        self.assertEqual(names, ['test_b_pass'])

    def test_缺报告元信息配置时退出码为2(self):
        """零硬编码纪律的守卫用例：[report] 没配就不能蒙混出报告。"""
        ini = '[environment]\nurl = http://x\n'   # 故意不含 [report]
        root, cases_dir, _ = self.make_env(CASE_SOURCE_PASS, ini_text=ini)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py')
        self.assertEqual(summary['exit_code'], runner.EXIT_ENV_ERROR)
        self.assertIsNone(summary['report'])

    def test_用例里抛ConfigMissingError改判为环境错误(self):
        """setUpClass 的业务配置缺失落进 errors —— 必须归退出码 2，不能当用例失败。"""
        source = (
            'import unittest\n'
            'from Common.config import ConfigMissingError\n'
            'class DemoFail(unittest.TestCase):\n'
            '    @classmethod\n'
            '    def setUpClass(cls):\n'
            '        raise ConfigMissingError("assert", "title_login", "登录页标题")\n'
            '    def test_a(self):\n'
            '        pass\n'
        )
        root, cases_dir, _ = self.make_env(source)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py')
        self.assertEqual(summary['exit_code'], runner.EXIT_ENV_ERROR)

    def test_普通断言失败仍是用例失败退出码1(self):
        """与上一条对照：普通失败绝不能被误改判为环境错误。"""
        root, cases_dir, _ = self.make_env(CASE_SOURCE_MIXED)
        summary, _ = self.run_runner(tests_dir=cases_dir, pattern='demo_test.py')
        self.assertEqual(summary['exit_code'], runner.EXIT_TEST_FAILED)


if __name__ == '__main__':
    unittest.main(verbosity=2)
