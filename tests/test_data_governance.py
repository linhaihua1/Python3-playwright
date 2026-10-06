# -*- coding: utf-8 -*-
"""dataGovernance 模块的单元测试：测试数据治理。"""

import os
import tempfile
import unittest

from Common.dataGovernance import TestDataGovernor as Governor, IsolatedTestCase, _gen_tag


class TestGenTag(unittest.TestCase):
    """唯一标签生成。"""

    def test标签非空且唯一(self):
        tags = {_gen_tag() for _ in range(100)}
        self.assertEqual(len(tags), 100, '100 次生成应该全部唯一')
        for t in tags:
            self.assertTrue(len(t) > 10)

    def test标签含日期前缀(self):
        tag = _gen_tag()
        self.assertTrue(tag[0:8].isdigit(), '前 8 位应为日期 YYYYMMDD')


class TestDataGovernor(unittest.TestCase):
    """治理器核心逻辑。"""

    def test注册与清理(self):
        gov = Governor()
        cleaned = []
        gov.record('数据A', lambda: cleaned.append('A'))
        gov.record('数据B', lambda: cleaned.append('B'))
        self.assertEqual(gov.count(), 2)

        report = gov.cleanup_all()
        self.assertEqual(report['total'], 2)
        self.assertEqual(report['success'], 2)
        self.assertEqual(report['failed'], 0)
        # 倒序清理：B 先于 A
        self.assertEqual(cleaned, ['B', 'A'])

    def test清理失败不中断(self):
        gov = Governor()
        gov.record('好数据', lambda: None)
        gov.record('坏数据', lambda: (_ for _ in ()).throw(RuntimeError('删不掉')))
        gov.record('另一条好数据', lambda: None)

        report = gov.cleanup_all()
        self.assertEqual(report['total'], 3)
        self.assertEqual(report['success'], 2)
        self.assertEqual(report['failed'], 1)
        self.assertEqual(len(report['failures']), 1)
        self.assertIn('坏数据', report['failures'][0][0])

    def test重复清理安全(self):
        gov = Governor()
        gov.record('x', lambda: None)
        gov.cleanup_all()
        # 第二次不报错，返回全 0
        report = gov.cleanup_all()
        self.assertEqual(report['total'], 0)

    def test每条数据有独立tag(self):
        g1 = Governor()
        g2 = Governor()
        self.assertNotEqual(g1.tag, g2.tag)


class TestIsolatedTestCase(unittest.TestCase):
    """隔离用例基类。"""

    def setUp(self):
        # 创建一个临时用例类来测试基类行为
        class _Demo(IsolatedTestCase):
            cleaned = []
            def test_demo(self):
                self.data.record('demo_data',
                                 lambda: _Demo.cleaned.append('done'))
                self.assertTrue(True)
        self._Demo = _Demo

    def test自动清理(self):
        suite = unittest.TestLoader().loadTestsFromTestCase(self._Demo)
        unittest.TextTestRunner(verbosity=0).run(suite)
        self.assertEqual(self._Demo.cleaned, ['done'])


if __name__ == '__main__':
    unittest.main()
