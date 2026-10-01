# -*- coding: utf-8 -*-
"""
单元测试：Common/log.py（双通道日志）

关注三件事：
    1. 同一个 logger 重复初始化时**不能**重复挂 handler（否则日志会翻倍）
    2. INFO/ERROR 的落盘位置是否符合约定（All_Logs 全量、Error_Logs 仅错误）
    3. 中文日志在不同 locale 下是否会因编码问题炸掉

所有产出都定向到临时目录，不写工程的 Logs/。
"""

import glob
import logging
import os
import tempfile
import unittest

import Common.log as log_module
from Common.log import _log_root, get_log, log1, _INITIALIZED
from tests.helpers import patch_dirs


class _LogTestCase(unittest.TestCase):
    """基类：每个用例用一个独立 logger 名，用例结束彻底清理。"""

    _seq = [0]

    def setUp(self):
        _LogTestCase._seq[0] += 1
        self.logger_name = 'ut_log_%d' % _LogTestCase._seq[0]
        self._dirs = patch_dirs()
        self.root = self._dirs.__enter__()

    def tearDown(self):
        logger = logging.getLogger(self.logger_name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        _INITIALIZED.discard(self.logger_name)
        self._dirs.__exit__(None, None, None)

    # ---- 便捷方法 ----
    def _log_files(self, kind):
        pattern = os.path.join(self.root, 'Logs', '*', kind, '*.log')
        return sorted(glob.glob(pattern))

    def _read(self, paths):
        content = ''
        for path in paths:
            with open(path, encoding='utf-8') as fp:
                content += fp.read()
        return content


class TestGetLog(_LogTestCase):
    """logger 的创建与 handler 管理。"""

    def test_返回logger并设置级别(self):
        logger = get_log(self.logger_name)
        self.assertIsInstance(logger, logging.Logger)
        self.assertEqual(logger.level, logging.INFO)

    def test_默认挂三个handler_两个文件加一个控制台(self):
        logger = get_log(self.logger_name)
        files = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        consoles = [h for h in logger.handlers
                    if not isinstance(h, logging.FileHandler)
                    and isinstance(h, logging.StreamHandler)]
        self.assertEqual(len(files), 2, '应有两个文件 handler（全量 + 仅错误）')
        self.assertEqual(len(consoles), 1, '应有一个控制台 handler')

    def test_重复调用不会重复挂handler(self):
        """历史缺陷：不防重时一条日志会被打印/写入多遍。"""
        logger = get_log(self.logger_name)
        first = len(logger.handlers)
        get_log(self.logger_name)
        get_log(self.logger_name)
        self.assertEqual(len(logger.handlers), first)

    def test_关闭控制台时只有两个文件handler(self):
        logger = get_log(self.logger_name, console=False)
        files = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        self.assertEqual(len(logger.handlers), 2)
        self.assertEqual(len(files), 2)

    def test_不向父logger传播(self):
        """propagate 为 True 时根 logger 会再输出一遍，导致重复。"""
        logger = get_log(self.logger_name)
        self.assertFalse(logger.propagate)

    def test_错误handler级别为ERROR(self):
        logger = get_log(self.logger_name)
        levels = sorted(h.level for h in logger.handlers
                        if isinstance(h, logging.FileHandler))
        self.assertEqual(levels, [logging.INFO, logging.ERROR])

    def test_可自定义级别(self):
        logger = get_log(self.logger_name, level=logging.DEBUG)
        self.assertEqual(logger.level, logging.DEBUG)


class TestLogOutput(_LogTestCase):
    """日志文件的实际产出。

    注意：本类统一用 console=False 建 logger —— 只验证落盘行为，
    避免测试输出被日志刷屏（控制台 handler 的行为在 TestGetLog 里验证）。
    """

    def _logger(self):
        return get_log(self.logger_name, console=False)

    def test_日志目录结构符合约定(self):
        self._logger().info('写一条')
        all_logs = self._log_files('All_Logs')
        error_logs = self._log_files('Error_Logs')
        self.assertEqual(len(all_logs), 1)
        self.assertEqual(len(error_logs), 1)
        # 同一次运行的两个文件应同名（时间戳一致），便于对应
        self.assertEqual(os.path.basename(all_logs[0]), os.path.basename(error_logs[0]))

    def test_INFO写入全量文件但不写入错误文件(self):
        logger = self._logger()
        logger.info('这是普通信息')
        logger.error('这是错误信息')

        all_content = self._read(self._log_files('All_Logs'))
        error_content = self._read(self._log_files('Error_Logs'))

        self.assertIn('这是普通信息', all_content)
        self.assertIn('这是错误信息', all_content)
        self.assertIn('这是错误信息', error_content)
        self.assertNotIn('这是普通信息', error_content)

    def test_错误日志带模块名与行号(self):
        self._logger().error('定位失败')
        content = self._read(self._log_files('Error_Logs'))
        # 格式：时间 - logger名 - 级别 - 模块名 - 行号 - 内容
        self.assertIn('test_log', content, '应包含出错的模块名，便于直接跳代码')
        self.assertRegex(content, r'-\s*\d+\s*-\s*定位失败',
                         '应包含行号，便于定位到具体代码行')

    def test_中文日志以utf8写入且可读(self):
        self._logger().info('中文内容：素材库 - 云媒资系统 ✔')
        content = self._read(self._log_files('All_Logs'))
        self.assertIn('素材库 - 云媒资系统', content)

    def test_同一logger的多次写入都落盘(self):
        logger = self._logger()
        for index in range(5):
            logger.info('第%d条' % index)
        content = self._read(self._log_files('All_Logs'))
        for index in range(5):
            self.assertIn('第%d条' % index, content)


class TestModuleLevelSingleton(unittest.TestCase):
    """模块级 log1 单例。"""

    def test_log1是logger实例(self):
        self.assertIsInstance(log1, logging.Logger)

    def test_log1名称为uiauto(self):
        self.assertEqual(log1.name, 'uiauto')

    def test_所有模块共用同一个log1(self):
        from Common.log import log1 as again
        self.assertIs(log1, again)


class TestLogDirOverride(unittest.TestCase):
    """UI_LOG_DIR 环境变量：把日志根目录整体挪走。

    这是 unit test / CI 不污染工程 Logs/ 的关键开关：
    Common/log.py 在**被 import 的瞬间**就建了日志文件，光摘 handler 来不及，
    必须提前把根目录换掉。本类同时锁住"没设变量时行为不变"。
    """

    def setUp(self):
        self._saved = os.environ.get('UI_LOG_DIR')
        self.tmpdir = tempfile.mkdtemp(prefix='ut_logdir_')

    def tearDown(self):
        if self._saved is None:
            os.environ.pop('UI_LOG_DIR', None)
        else:
            os.environ['UI_LOG_DIR'] = self._saved
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_未设变量时日志根就是工程Logs目录(self):
        os.environ.pop('UI_LOG_DIR', None)
        self.assertEqual(_log_root('/proj'), os.path.join('/proj', 'Logs'))

    def test_设了变量时用变量指定的目录(self):
        os.environ['UI_LOG_DIR'] = self.tmpdir
        self.assertEqual(_log_root('/proj'), os.path.abspath(self.tmpdir))

    def test_变量为相对路径时转成绝对路径(self):
        """日志目录必须是绝对路径：进程中途切工作目录也不会跑偏。"""
        os.environ['UI_LOG_DIR'] = 'relative-logs'
        self.assertTrue(os.path.isabs(_log_root('/proj')))

    def test_变量为空串时退回工程Logs目录(self):
        """空字符串视为"没配置"，不能让日志写到文件系统根下。"""
        os.environ['UI_LOG_DIR'] = ''
        self.assertEqual(_log_root('/proj'), os.path.join('/proj', 'Logs'))

    def test_日志真的落到变量指定的目录(self):
        os.environ['UI_LOG_DIR'] = self.tmpdir
        name = 'ut_logdir_case'
        logger = get_log(name, console=False)
        try:
            logger.info('写到被挪走的目录')
            found = glob.glob(os.path.join(self.tmpdir, '*', 'All_Logs', '*.log'))
            self.assertEqual(len(found), 1, '日志应写在 UI_LOG_DIR 下，实际：%s' % found)
            with open(found[0], encoding='utf-8') as fp:
                self.assertIn('写到被挪走的目录', fp.read())
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            _INITIALIZED.discard(name)


class TestLogIsolationSetup(unittest.TestCase):
    """tests/__init__.py 的测试隔离前置是否真的生效。"""

    def test_单元测试进程已把日志目录挪出工程(self):
        """★ 这是"跑单测不污染工程目录"的核心保证，坏了必须有人发现。"""
        from tests import _LOG_ROOT
        self.assertIsNotNone(
            _LOG_ROOT, '未设置 UI_LOG_DIR，单元测试会往工程 Logs/ 里写日志')
        self.assertEqual(os.environ.get('UI_LOG_DIR'), _LOG_ROOT)
        self.assertTrue(os.path.isdir(_LOG_ROOT))

    def test_日志根目录不在工程内(self):
        from tests import _LOG_ROOT
        from tests.helpers import BASE_DIR
        self.assertFalse(
            os.path.abspath(_LOG_ROOT).startswith(os.path.abspath(BASE_DIR)),
            '日志根目录必须挪出工程，否则等于没隔离')

    def test_工程logger只剩一个控制台handler(self):
        """文件 handler 应被摘干净，否则测试会刷大量无意义的 ERROR 日志。"""
        files = [h for h in log1.handlers if isinstance(h, logging.FileHandler)]
        self.assertEqual(files, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
