# -*- coding: utf-8 -*-
"""allureReport 模块的单元测试。"""

import json
import os
import tempfile
import unittest
from unittest import mock

from Common import allureReport


class TestAllureEnable(unittest.TestCase):
    """开启/关闭 Allure。"""

    def setUp(self):
        allureReport._enabled = False
        allureReport._results_dir = None

    def tearDown(self):
        allureReport._enabled = False
        allureReport._results_dir = None

    def test默认关闭(self):
        self.assertFalse(allureReport.is_enabled())

    def test开启后状态正确(self):
        with mock.patch.object(allureReport, '_ensure_results_dir', return_value='/tmp/ar'):
            allureReport.enable()
        self.assertTrue(allureReport.is_enabled())

    def test未开启时写结果不报错(self):
        # _enabled=False 时 _write_result 直接返回 None
        result = allureReport._write_result({'name': 'test', 'status': 'passed'})
        self.assertIsNone(result)


class TestBuildResult(unittest.TestCase):
    """结果字典构造。"""

    def test基本字段(self):
        case = mock.MagicMock()
        case.id.return_value = 'TestDemo.test_x'
        case._testMethodName = 'test_x'
        type(case).__name__ = 'TestDemo'
        type(case).__module__ = 'tests.demo'

        data = allureReport._build_result(case, 'success', 1000, 2000)
        self.assertEqual(data['status'], 'passed')
        self.assertEqual(data['stage'], 'finished')
        self.assertEqual(data['start'], 1000)
        self.assertEqual(data['stop'], 2000)
        self.assertIn('uuid', data)
        labels = {l['name']: l['value'] for l in data['labels']}
        self.assertEqual(labels['suite'], 'TestDemo')

    def test失败带错误详情(self):
        case = mock.MagicMock()
        case.id.return_value = 'TestDemo.test_fail'
        case._testMethodName = 'test_fail'
        type(case).__name__ = 'TestDemo'

        err = (ValueError, ValueError('断言失败'), None)
        data = allureReport._build_result(case, 'failure', 1000, 2000, err)
        self.assertEqual(data['status'], 'failed')
        self.assertIn('statusDetails', data)
        self.assertIn('断言失败', data['statusDetails']['message'])

    def test状态映射(self):
        self.assertEqual(allureReport._STATUS_MAP['success'], 'passed')
        self.assertEqual(allureReport._STATUS_MAP['failure'], 'failed')
        self.assertEqual(allureReport._STATUS_MAP['error'], 'broken')
        self.assertEqual(allureReport._STATUS_MAP['skip'], 'skipped')


class TestWriteAttachment(unittest.TestCase):
    """附件写入。"""

    def setUp(self):
        allureReport._enabled = True
        self.tmpdir = tempfile.mkdtemp()
        allureReport._results_dir = self.tmpdir

    def tearDown(self):
        allureReport._enabled = False
        allureReport._results_dir = None

    def test写图片附件(self):
        fname = allureReport.write_attachment('截图', b'\x89PNG', 'image/png', '.png')
        self.assertIsNotNone(fname)
        self.assertTrue(fname.endswith('.png'))
        full = os.path.join(self.tmpdir, fname)
        self.assertTrue(os.path.exists(full))

    def test写文本附件(self):
        fname = allureReport.write_attachment('日志', 'hello', 'text/plain', '.txt')
        self.assertTrue(fname.endswith('.txt'))


class TestAllureStep(unittest.TestCase):
    """步骤标签上下文管理器。"""

    def test正常进入退出(self):
        with allureReport.allure_step('测试步骤') as step:
            self.assertEqual(step.name, '测试步骤')

    def test异常不被吞掉(self):
        with self.assertRaises(ValueError):
            with allureReport.allure_step('会失败的步骤'):
                raise ValueError('出错了')


if __name__ == '__main__':
    unittest.main()
