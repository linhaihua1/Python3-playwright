# -*- coding: utf-8 -*-
"""
单元测试：新增的框架能力
    - dataDriver 数据驱动（CSV/JSON 加载 + 参数化展开）
    - notifier   群机器人通知（钉钉/企业微信/通用）
    - BasePage   稳定性重试（retry / safe_click）
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from tests.helpers import patch_config, patch_dirs, FakePage, PlaywrightError
from Common import dataDriver, notifier
from Common.basePage import BasePage


# ==========================================================================
# 一、数据驱动 dataDriver
# ==========================================================================
class TestDataDriver(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def test_load_csv(self):
        path = self._write('d.csv', 'name,pwd\n张三,123\n李四,456\n')
        rows = dataDriver.load_data(path)
        self.assertEqual(rows, [{'name': '张三', 'pwd': '123'}, {'name': '李四', 'pwd': '456'}])

    def test_load_json(self):
        path = self._write('d.json', json.dumps([{'name': '张三'}, {'name': '李四'}]))
        rows = dataDriver.load_data(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['name'], '张三')

    def test_json_非数组报错(self):
        path = self._write('d.json', json.dumps({'a': 1}))
        with self.assertRaises(ValueError):
            dataDriver.load_data(path)

    def test_不支持格式报错(self):
        path = self._write('d.txt', 'x')
        with self.assertRaises(ValueError):
            dataDriver.load_data(path)

    def test_装饰器打标(self):
        @dataDriver.data_driven([{'a': 1}, {'a': 2}])
        def f(self, row):
            return row
        self.assertEqual(getattr(f, '_ddt_dataset'), [{'a': 1}, {'a': 2}])

    def test_参数化展开_生成多条用例(self):
        collected = []

        class Demo(unittest.TestCase):
            def runTest(self):
                pass

            @dataDriver.data_driven([{'a': 1}, {'a': 2}, {'a': 3}])
            def test_x(self, row):
                collected.append(row['a'])

        suite = dataDriver.expand_ddt_cases(Demo('runTest'))
        for case in suite:
            case.debug()
        self.assertEqual(sorted(collected), [1, 2, 3])


# ==========================================================================
# 二、通知 notifier（不真正联网，用桩替换 _post）
# ==========================================================================
class TestNotifier(unittest.TestCase):

    def setUp(self):
        self._cfg = patch_config()
        self._cfg.__enter__()

    def tearDown(self):
        self._cfg.__exit__(None, None, None)

    def test_未配置webhook返回False(self):
        self.assertFalse(notifier.notify({'exit_code': 0, 'tests_run': 1}, webhook=''))

    def test_钉钉消息格式(self):
        captured = {}

        def fake_post(url, payload, timeout=10):
            captured['url'] = url
            captured['payload'] = payload
            return True, '{"errcode":0}'

        with mock.patch.object(notifier, '_post', side_effect=fake_post):
            ok = notifier.notify(
                {'exit_code': 0, 'tests_run': 3, 'failures': 0, 'errors': 0,
                 'skipped': 0, 'duration': 1.0, 'report': 'r.html'},
                webhook='https://oapi.dingtalk.com/robot/send?access_token=abc')
        self.assertTrue(ok)
        self.assertEqual(captured['payload']['msgtype'], 'text')
        self.assertIn('通过', captured['payload']['text']['content'])

    def test_企业微信消息格式(self):
        captured = {}
        with mock.patch.object(notifier, '_post',
                               side_effect=lambda url, payload, timeout=10: (captured.update(payload=payload) or True, 'ok')):
            notifier.notify({'exit_code': 1, 'tests_run': 1},
                            webhook='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k')
        self.assertEqual(captured['payload']['msgtype'], 'text')

    def test_网络异常不抛(self):
        with mock.patch.object(notifier, '_post', return_value=(False, 'boom')):
            self.assertFalse(notifier.notify({'exit_code': 0}, webhook='https://example.com/hook'))


# ==========================================================================
# 三、BasePage 稳定性重试
# ==========================================================================
class TestRetry(unittest.TestCase):

    def setUp(self):
        self._cfg = patch_config()
        self._cfg.__enter__()
        self._sleep = mock.patch.object(BasePage, 'my_sleep', return_value=None)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()
        self._cfg.__exit__(None, None, None)

    def test_首次成功不重试(self):
        page = BasePage(driver=FakePage(), timeout=1)
        calls = [0]

        def fn():
            calls[0] += 1
            return 'ok'

        self.assertEqual(page.retry(fn), 'ok')
        self.assertEqual(calls[0], 1)

    def test_瞬时错误重试后成功(self):
        """瞬时错误（超时、点击被挡、元素未就绪等 Playwright 异常）由框架层
        统一重试兜底 —— Playwright 的 Locator 永不过期，无旧句柄失效类问题。"""
        page = BasePage(driver=FakePage(), timeout=1)
        page.DEFAULT_RETRY = 2
        calls = [0]

        def fn():
            calls[0] += 1
            if calls[0] < 3:
                raise PlaywrightError('transient')
            return 'ok'

        self.assertEqual(page.retry(fn), 'ok')
        self.assertEqual(calls[0], 3)

    def test_全部失败后抛出最后异常(self):
        page = BasePage(driver=FakePage(), timeout=1)
        page.DEFAULT_RETRY = 1
        with self.assertRaises(PlaywrightError):
            page.retry(lambda: (_ for _ in ()).throw(PlaywrightError('fail')))

    def test_断言错误不重试(self):
        page = BasePage(driver=FakePage(), timeout=1)
        page.DEFAULT_RETRY = 3
        calls = [0]

        def fn():
            calls[0] += 1
            raise AssertionError('业务断言失败，不该重试')

        with self.assertRaises(AssertionError):
            page.retry(fn)
        self.assertEqual(calls[0], 1)   # 只执行一次，不浪费重试


if __name__ == '__main__':
    unittest.main()
