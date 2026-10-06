# -*- coding: utf-8 -*-
"""envGuard 模块的单元测试：生产环境安全防护。"""

import os
import unittest
from unittest import mock

from Common import envGuard
from Common.envGuard import ProductionWriteBlocked, detect_environment, allow_write


class TestDetectEnvironment(unittest.TestCase):
    """环境类型检测。"""

    def setUp(self):
        os.environ.pop('UI_ENV_KIND', None)
        os.environ.pop('UI_SAFETY_ALLOW_WRITE', None)
        envGuard._is_production = None
        envGuard._mode = None

    def test命令行指定prod(self):
        self.assertEqual(detect_environment(env_arg='prod'), 'prod')
        self.assertEqual(detect_environment(env_arg='PRODUCTION'), 'prod')
        self.assertEqual(detect_environment(env_arg='online'), 'prod')
        self.assertEqual(detect_environment(env_arg='release'), 'prod')

    def test命令行指定test(self):
        self.assertEqual(detect_environment(env_arg='test'), 'test')
        self.assertEqual(detect_environment(env_arg='staging'), 'test')
        self.assertEqual(detect_environment(env_arg='dev'), 'test')

    def test环境变量强制prod(self):
        os.environ['UI_ENV_KIND'] = 'prod'
        self.assertEqual(detect_environment(env_arg='test'), 'prod')
        del os.environ['UI_ENV_KIND']

    def test默认测试环境(self):
        self.assertEqual(detect_environment(url='http://test.example.com'), 'test')


class TestProductionSafety(unittest.TestCase):
    """生产环境写操作拦截。"""

    def setUp(self):
        os.environ.pop('UI_SAFETY_ALLOW_WRITE', None)
        envGuard._is_production = True   # 模拟生产环境
        envGuard._mode = 'readonly'

    def tearDown(self):
        envGuard._is_production = None
        envGuard._mode = None

    def test生产环境拦截写操作(self):
        with self.assertRaises(ProductionWriteBlocked):
            allow_write('上传文件')

    def test生产环境显式允许时放行(self):
        os.environ['UI_SAFETY_ALLOW_WRITE'] = 'true'
        self.assertTrue(allow_write('上传文件'))
        del os.environ['UI_SAFETY_ALLOW_WRITE']

    def test非生产环境放行(self):
        envGuard._is_production = False
        self.assertTrue(allow_write('提交表单'))


class TestActivate(unittest.TestCase):
    """activate() 集成测试。"""

    def setUp(self):
        os.environ.pop('UI_TESTCASE_MODE', None)
        os.environ.pop('UI_SAFETY_ALLOW_WRITE', None)
        os.environ.pop('UI_ENV_KIND', None)
        envGuard._is_production = None
        envGuard._mode = None

    def test生产环境注入只读标记(self):
        with mock.patch.object(envGuard.Config, 'get_bool', return_value=True):
            result = envGuard.activate(env_arg='prod')
        self.assertTrue(result['readonly'])
        self.assertEqual(os.environ.get('UI_TESTCASE_MODE'), 'readonly')
        self.assertEqual(os.environ.get('UI_SAFETY_ALLOW_WRITE'), 'false')

    def test测试环境不注入只读标记(self):
        with mock.patch.object(envGuard.Config, 'get_bool', return_value=True):
            with mock.patch.object(envGuard.Config, 'get', return_value=''):
                result = envGuard.activate(env_arg='test')
        self.assertFalse(result['readonly'])


if __name__ == '__main__':
    unittest.main()
