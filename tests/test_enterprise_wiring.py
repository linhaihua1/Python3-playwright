# -*- coding: utf-8 -*-
"""
单元测试：企业级特性执行链路（验收实测发现断链后补的守卫）

【这组测试钉死什么】
    框架有四个特性曾经"API 存在、文档宣传了，但没接进执行链路"：
        flaky 重试 / 依赖跳过 / 数据驱动 / 产物隔离
    单测曾只测它们的孤立函数，测不出"没接入"——这组测试从
    build_suite / run() 的真实链路往下验证，防止再次断链。
"""

import os
import unittest

from tests.helpers import patch_config


# ---------------------------------------------------------------------------
# 一、flaky 重试：resultclass 混入路径
# ---------------------------------------------------------------------------
class TestFlakyWired(unittest.TestCase):

    def test_RetryTestResult透传执行计数(self):
        """startTest/wasSuccessful 必须转发给 original。

        曾经的缺陷：这些方法命中父类实现不走 __getattr__，
        全失败的执行 wasSuccessful() 恒为 True —— CI 会把红构建标绿。
        """
        from Common.flakyRetry import RetryTestResult

        class AlwaysFail(unittest.TestCase):
            def test_x(self):
                self.fail('永远失败')

        base = unittest.TestResult()
        wrapped = RetryTestResult(base)
        AlwaysFail('test_x').run(wrapped)
        self.assertEqual(base.testsRun, 1,
                         'startTest 未转发，original 计数为 0')
        self.assertEqual(len(base.failures), 1)
        self.assertFalse(wrapped.wasSuccessful(),
                         '全失败的执行不能被判成成功')

    def test_RetryTestResult循环重试至配置次数(self):
        """retry=N 意味着最多 N 次重试（共 N+1 次执行）。

        曾经的缺陷：只重试一次，retry=3 实际只执行 2 次。
        """
        from Common.flakyRetry import RetryTestResult, flaky

        attempts = {'n': 0}

        @flaky(retry=3, interval=0)
        class Flaky(unittest.TestCase):
            def test_x(self):
                attempts['n'] += 1
                if attempts['n'] < 3:
                    self.fail('前两次失败')

        base = unittest.TestResult()
        wrapped = RetryTestResult(base)
        Flaky('test_x').run(wrapped)
        self.assertEqual(attempts['n'], 3)
        self.assertTrue(wrapped.wasSuccessful())

    def test_混入resultclass后重试发生在报告流程内(self):
        """make_flaky_result_class 组合的类：重试成功按成功记录。

        这是 runner.py 真实使用的接入方式 —— 重试必须发生在
        addFailure 钩子里，HTML/JUnit/退出码三份证据才一致。
        """
        from Common.flakyRetry import make_flaky_result_class

        attempts = {'n': 0}

        class Flaky(unittest.TestCase):
            def test_x(self):
                attempts['n'] += 1
                if attempts['n'] < 3:
                    self.fail('前两次失败')

        Combined = make_flaky_result_class(unittest.TestResult)
        # 给类级标记（模拟 @flaky 打在类上）
        Flaky._flaky_retry = 3
        Flaky._flaky_interval = 0
        result = Combined()
        Flaky('test_x').run(result)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(len(result.failures), 0)
        self.assertEqual(result.testsRun, 1)


# ---------------------------------------------------------------------------
# 二、依赖跳过：build_suite 守卫路径
# ---------------------------------------------------------------------------
class TestDependencyWired(unittest.TestCase):

    def test_守卫包装后前置失败子用例跳过(self):
        """wrap_suite_with_dependency_guard 是 build_suite 的真实接入点。"""
        from Common.dependency import (depends_on, global_tracker,
                                       wrap_suite_with_dependency_guard)
        executed = []
        tracker = global_tracker()
        tracker.reset()

        class Flow(unittest.TestCase):
            def test_1root(self):
                self.fail('根失败')

            @depends_on('test_1root')
            def test_2child(self):
                executed.append('child')

        suite = wrap_suite_with_dependency_guard(
            unittest.TestSuite([Flow('test_1root'), Flow('test_2child')]))
        result = unittest.TestResult()
        suite.run(result)
        self.assertEqual(executed, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(len(result.failures), 1)

    def test_前置被过滤掉时子用例同样跳过(self):
        """前置用例不在执行集里 -> 视为"未执行"，同样不可跑。"""
        from Common.dependency import (depends_on, global_tracker,
                                       wrap_suite_with_dependency_guard)
        tracker = global_tracker()
        tracker.reset()

        class Flow(unittest.TestCase):
            @depends_on('test_missing')
            def test_child(self):
                pass

        suite = wrap_suite_with_dependency_guard(
            unittest.TestSuite([Flow('test_child')]))
        result = unittest.TestResult()
        suite.run(result)
        self.assertEqual(len(result.skipped), 1,
                         '前置未执行时子用例应跳过而不是硬跑')

    def test_无依赖时守卫零开销(self):
        from Common.dependency import wrap_suite_with_dependency_guard
        suite = unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])
        self.assertIs(wrap_suite_with_dependency_guard(suite), suite)


# ---------------------------------------------------------------------------
# 三、数据驱动：build_suite 展开路径
# ---------------------------------------------------------------------------
class TestDataDrivenWired(unittest.TestCase):

    def test_展开后的用例独立执行互不影响(self):
        from Common.dataDriver import data_driven
        from Common.runner import expand_ddt_suite
        rows = [{'name': 'A'}, {'name': 'B'}]

        class DD(unittest.TestCase):
            @data_driven(rows)
            def test_case(self, row):
                self.assertEqual(row['name'], row['name'].upper())

        result = unittest.TestResult()
        expand_ddt_suite(
            unittest.TestSuite([DD('test_case')])).run(result)
        self.assertEqual(result.testsRun, 2)
        self.assertTrue(result.wasSuccessful())


if __name__ == '__main__':
    unittest.main(verbosity=2)
