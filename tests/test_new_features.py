# -*- coding: utf-8 -*-
"""runContext / locatorPolicy / dependency 模块的单元测试。"""

import os
import unittest
from unittest import mock

from Common import runContext
from Common.locatorPolicy import _check_xpath, scan_element_page, SEVERITY_P0, SEVERITY_P1
from Common.dependency import depends_on, DependencyTracker


class TestRunContext(unittest.TestCase):
    """运行上下文与产物隔离。"""

    def setUp(self):
        runContext.reset()

    def test生成唯一runId(self):
        rid1 = runContext.init()
        rid2 = runContext.init()   # 幂等：第二次返回同一个
        self.assertEqual(rid1, rid2)
        self.assertTrue(len(rid1) > 10)

    def test外部指定runId(self):
        runContext.reset()
        rid = runContext.init(run_id='build_123')
        self.assertEqual(rid, 'build_123')

    def test环境变量注入runId(self):
        runContext.reset()
        os.environ['UI_RUN_ID'] = 'jenkins_456'
        try:
            rid = runContext.init()
            self.assertEqual(rid, 'jenkins_456')
        finally:
            del os.environ['UI_RUN_ID']

    def test隔离模式拼接子目录(self):
        runContext.reset()
        runContext.init(run_id='test_run')
        with mock.patch.object(runContext.Config, 'get_bool', return_value=True):
            result = runContext.subdir('/tmp/Report')
        self.assertTrue(result.endswith(os.path.join('Report', 'test_run')))

    def test非隔离模式不拼接(self):
        runContext.reset()
        runContext.init(run_id='test_run')
        with mock.patch.object(runContext.Config, 'get_bool', return_value=False):
            result = runContext.subdir('/tmp/Report')
        self.assertEqual(result, '/tmp/Report')


class TestLocatorPolicy(unittest.TestCase):
    """定位策略静态检查。"""

    def test绝对路径xpath标为P0(self):
        issues = _check_xpath('/html/body/div[4]/div/span')
        sevs = [s for s, _, _ in issues]
        self.assertIn(SEVERITY_P0, sevs)

    def test硬编码索引标为P1(self):
        issues = _check_xpath("//tr[1]/td[8]/div/button[3]")
        sevs = [s for s, _, _ in issues]
        self.assertIn(SEVERITY_P1, sevs)

    def test深层嵌套标为P1(self):
        long_xpath = "//*[@id='app']/" + '/'.join(['div'] * 10) + '/span'
        issues = _check_xpath(long_xpath)
        sevs = [s for s, _, _ in issues]
        self.assertIn(SEVERITY_P1, sevs)

    def test健康xpath无问题(self):
        issues = _check_xpath("//input[@type='text']")
        self.assertEqual(len(issues), 0)

    def test扫描不存在的文件(self):
        result = scan_element_page('/nonexistent/file.py')
        self.assertEqual(result, [])


class TestDependency(unittest.TestCase):
    """用例依赖管理。"""

    def test无依赖不跳过(self):
        tracker = DependencyTracker()
        tracker._results = {}
        case = mock.MagicMock()
        case._testMethodName = 'test_a'
        type(case).test_a = lambda self: None  # 无 _depends_on
        skip, _ = tracker.should_skip(case)
        self.assertFalse(skip)

    def test前置失败时跳过(self):
        tracker = DependencyTracker()
        tracker._results = {'test_login': False}

        @depends_on('test_login')
        def test_upload(self):
            pass

        case = mock.MagicMock()
        case._testMethodName = 'test_upload'
        case.test_upload = test_upload
        skip, reason = tracker.should_skip(case)
        self.assertTrue(skip)
        self.assertIn('test_login', reason)

    def test前置通过时不跳过(self):
        tracker = DependencyTracker()
        tracker._results = {'test_login': True}

        @depends_on('test_login')
        def test_upload(self):
            pass

        case = mock.MagicMock()
        case._testMethodName = 'test_upload'
        case.test_upload = test_upload
        skip, _ = tracker.should_skip(case)
        self.assertFalse(skip)

    def test记录结果(self):
        tracker = DependencyTracker()
        case = mock.MagicMock()
        case._testMethodName = 'test_x'
        case.id.return_value = 'Test.test_x'
        tracker.record(case, passed=True)
        self.assertTrue(tracker._results['test_x'])


if __name__ == '__main__':
    unittest.main()
