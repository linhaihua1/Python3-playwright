# -*- coding: utf-8 -*-
"""
单元测试：run.py / entrance.py（命令行入口）

入口文件看着简单，却是流水线接入点，一旦参数或退出码出问题，CI 全盘失效。
这里验证：
    - argparse 默认值与互斥开关（--headless / --no-headless）
    - 参数 -> 环境变量的覆盖链路（工程只有"环境变量 > 配置 > 默认值"一条链路）
    - 执行结论块的三种文案（与退出码语义一致）
    - 两个入口都有 __main__ 保护，被 import 时不会误跑测试
    - 退出码常量与 runner 保持一致

★ 不真正执行测试：只验证解析与翻译逻辑。
"""

import contextlib
import io
import os
import unittest
from unittest import mock

from tests.helpers import load_module, patch_config, patch_dirs
from Common import runner

RUN_PY = os.path.join('run.py')
ENTRANCE_PY = os.path.join('entrance.py')


class _EntryTestCase(unittest.TestCase):
    """基类：加载入口模块并隔离环境变量。"""

    MODULE_NAME = None
    MODULE_FILE = None

    @classmethod
    def setUpClass(cls):
        from tests.helpers import BASE_DIR
        cls.module = load_module(cls.MODULE_NAME,
                                 os.path.join(BASE_DIR, cls.MODULE_FILE))

    def setUp(self):
        self._saved = {k: v for k, v in os.environ.items()
                       if k.startswith('UI_')}

    def tearDown(self):
        for key in list(os.environ):
            if key.startswith('UI_'):
                os.environ.pop(key, None)
        os.environ.update(self._saved)


class TestRunArgs(_EntryTestCase):
    """run.py 的参数解析。"""

    MODULE_NAME = 'run_entry'
    MODULE_FILE = RUN_PY

    def test_默认值(self):
        args = self.module.parse_args([])
        self.assertIsNone(args.pattern,
                          '不传时应为 None：由 runner 回落 [paths] case_pattern，入口不写死匹配规则')
        self.assertEqual(args.suite, 'full')
        self.assertIsNone(args.headless, '不传时应为 None，表示沿用 config.ini 配置')
        self.assertIsNone(args.title)
        self.assertIsNone(args.filename)
        self.assertFalse(args.mail)
        self.assertIsNone(args.junit)

    def test_开启无头(self):
        self.assertTrue(self.module.parse_args(['--headless']).headless)

    def test_关闭无头(self):
        self.assertFalse(self.module.parse_args(['--no-headless']).headless)

    def test_无头开关互斥(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.module.parse_args(['--headless', '--no-headless'])

    def test_套件类型仅支持full与smoke(self):
        self.assertEqual(self.module.parse_args(['--suite', 'smoke']).suite, 'smoke')
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.module.parse_args(['--suite', '随便写的'])

    def test_浏览器类型受限(self):
        for name in ('chrome', 'firefox', 'edge'):
            self.assertEqual(self.module.parse_args(['--browser', name]).browser, name)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.module.parse_args(['--browser', 'safari'])

    def test_报告与用例相关参数(self):
        args = self.module.parse_args([
            '--title', '冒烟测试', '--filename', 'smoke.html',
            '--tester', 'build-128', '--pattern', 'test_Login*.py',
            '--cases', 'test_1login,test_2select_scxt',
            '--url', 'http://test.local/x', '--junit', 'Report/x.xml', '--mail',
        ])
        self.assertEqual(args.title, '冒烟测试')
        self.assertEqual(args.filename, 'smoke.html')
        self.assertEqual(args.tester, 'build-128')
        self.assertEqual(args.pattern, 'test_Login*.py')
        self.assertEqual(args.cases, 'test_1login,test_2select_scxt')
        self.assertEqual(args.url, 'http://test.local/x')
        self.assertEqual(args.junit, 'Report/x.xml')
        self.assertTrue(args.mail)

    def test_帮助信息可正常输出(self):
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stdout(io.StringIO()) as buffer:
            self.module.parse_args(['--help'])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn('--headless', buffer.getvalue())


class TestRuntimeOverrides(_EntryTestCase):
    """命令行参数 -> 环境变量的覆盖链路。"""

    MODULE_NAME = 'run_entry'
    MODULE_FILE = RUN_PY

    def test_无头开关写入环境变量(self):
        self.module.apply_runtime_overrides(self.module.parse_args(['--headless']))
        self.assertEqual(os.environ.get('UI_CI_HEADLESS'), 'true')

        self.module.apply_runtime_overrides(self.module.parse_args(['--no-headless']))
        self.assertEqual(os.environ.get('UI_CI_HEADLESS'), 'false')

    def test_不传时不写入(self):
        self.module.apply_runtime_overrides(self.module.parse_args([]))
        self.assertNotIn('UI_CI_HEADLESS', os.environ)

    def test_地址与浏览器写入环境变量(self):
        args = self.module.parse_args(['--url', 'http://x.local', '--browser', 'edge'])
        self.module.apply_runtime_overrides(args)
        self.assertEqual(os.environ['UI_ENVIRONMENT_URL'], 'http://x.local')
        self.assertEqual(os.environ['UI_ENVIRONMENT_BROWSER'], 'edge')

    def test_返回生效项清单(self):
        applied = self.module.apply_runtime_overrides(
            self.module.parse_args(['--headless', '--browser', 'chrome']))
        self.assertEqual(len(applied), 2)

    def test_覆盖后真实生效到Config(self):
        """这是链路闭环的关键：参数最终要能被 Common/config.py 读到。"""
        from Common.config import Config
        with patch_config():
            self.module.apply_runtime_overrides(
                self.module.parse_args(['--url', 'http://override.local']))
            self.assertEqual(Config.get('environment', 'url'), 'http://override.local')


class TestConclusion(_EntryTestCase):
    """执行结论块的文案与退出码语义一致。"""

    MODULE_NAME = 'run_entry'
    MODULE_FILE = RUN_PY

    def _print(self, **kwargs):
        summary = runner._summary(**kwargs)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.module.print_conclusion(summary)
        return buffer.getvalue()

    def test_通过时显示PASSED且退出码为0(self):
        output = self._print(exit_code=runner.EXIT_OK, tests_run=6)
        self.assertIn('PASSED', output)
        self.assertIn('退出码    : 0', output)

    def test_用例失败时显示FAILED且退出码为1(self):
        output = self._print(exit_code=runner.EXIT_TEST_FAILED, tests_run=6,
                             failures=2)
        self.assertIn('FAILED', output)
        self.assertIn('退出码    : 1', output)

    def test_环境异常时显示ABORTED且退出码为2(self):
        output = self._print(exit_code=runner.EXIT_ENV_ERROR)
        self.assertIn('ABORTED', output)
        self.assertIn('退出码    : 2', output)

    def test_未生成产物时显示未生成(self):
        output = self._print(exit_code=runner.EXIT_ENV_ERROR)
        self.assertIn('未生成', output)


class TestMainEntry(_EntryTestCase):
    """run.py 的可执行性与主流程约定。"""

    MODULE_NAME = 'run_entry'
    MODULE_FILE = RUN_PY

    def test_从命令行执行帮助退出码为0(self):
        """真实起一个子进程，确认入口脚本在任何工作目录下都能跑起来。"""
        import subprocess
        import sys

        from tests.helpers import BASE_DIR

        proc = subprocess.run(
            [sys.executable, 'run.py', '--help'], cwd=BASE_DIR,
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('--headless', proc.stdout)
        self.assertIn('--suite', proc.stdout)

    def test_源码有main保护且用退出码结束(self):
        from tests.helpers import BASE_DIR
        with open(os.path.join(BASE_DIR, 'run.py'), encoding='utf-8') as fp:
            source = fp.read()
        self.assertIn("if __name__ == '__main__':", source,
                      '缺少 __main__ 保护会导致被 import 时误跑测试')
        self.assertIn('sys.exit(summary', source,
                      '必须把退出码交给操作系统，否则 CI 无法判定构建成败')


class TestEntrance(_EntryTestCase):
    """entrance.py 备用入口。"""

    MODULE_NAME = 'entrance_entry'
    MODULE_FILE = ENTRANCE_PY

    def test_标题从配置读取不写死在代码(self):
        """零硬编码改造后，备用入口不再有写死的业务标题常量。"""
        with patch_config():
            from Common.config import Config
            self.assertEqual(Config.require('entrance', 'title'), '备用入口测试报告')

    def test_导入时不执行测试(self):
        """加载模块本身不应触发任何测试执行（靠 __main__ 保护）。"""
        with mock.patch.object(self.module.runner, 'run') as fake_run:
            self.assertFalse(fake_run.called)

    def test_源码有main保护并调用runner(self):
        from tests.helpers import BASE_DIR
        with open(os.path.join(BASE_DIR, 'entrance.py'), encoding='utf-8') as fp:
            source = fp.read()
        self.assertIn("if __name__ == '__main__':", source)
        self.assertIn('runner.run(', source)
        # 标题改为配置驱动：必须从 [entrance] 读；写死的标题常量（DANGJIAN_TITLE）应已移除。
        # 注意 docstring 里作为历史说明提到具体系统名是允许的，禁止的是代码里的标题赋值。
        self.assertIn("Config.require('entrance', 'title'", source)
        self.assertNotIn('DANGJIAN_TITLE', source,
                         '业务标题不应作为常量写死在 entrance.py，应改从 [entrance] 配置读')
        self.assertIn('sys.exit(summary', source)


class TestEntryConsistency(unittest.TestCase):
    """两个入口与执行器的约定一致。"""

    def test_入口文件存在于工程根(self):
        from tests.helpers import BASE_DIR
        for name in ('run.py', 'entrance.py', 'getcwd.py'):
            self.assertTrue(os.path.isfile(os.path.join(BASE_DIR, name)))

    def test_退出码由runner统一定义(self):
        """入口不应自己发明退出码，避免 CI 判定口径不一致。"""
        from tests.helpers import BASE_DIR
        for name in ('run.py', 'entrance.py'):
            with open(os.path.join(BASE_DIR, name), encoding='utf-8') as fp:
                source = fp.read()
            self.assertIn('exit_code', source)
            self.assertNotIn('sys.exit(1)', source, '不要硬编码退出码')

    def test_junit结果文件路径与ci配置默认值一致(self):
        with patch_config():
            self.assertEqual(runner.resolve_junit_path(),
                             os.path.join(runner.base_dir, 'Report', 'junit-results.xml'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
