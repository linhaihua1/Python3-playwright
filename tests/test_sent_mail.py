# -*- coding: utf-8 -*-
"""
单元测试：Common/sentMail.py（报告邮件）

关注点：
    - 找不到报告 / 缺发件人 / 缺收件人时必须**返回 False 并记日志**，
      而不是抛异常（发信失败不该影响测试结果与报告生成）
    - 收件人是从 [addressed] 段动态遍历出来的，多收件人拼接要正确
    - 中文附件名走 filename=('gbk', '', 名) 的特殊写法，不能被改坏

★ 全部测试都不真的连 SMTP —— 一律 patch 掉 smtplib.SMTP_SSL。
"""

import email
import os
import shutil
import tempfile
import unittest
from email.header import decode_header, make_header
from unittest import mock

from tests.helpers import patch_config, patch_dirs
from Common import sentMail
from Common import dir_config
from Common.sentMail import find_latest_report, sent_mail


def decode_mail_header(raw):
    """把 MIME 编码过的邮件头（如 =?utf-8?b?...?=）还原成可读字符串。"""
    return str(make_header(decode_header(raw or '')))


class FakeSMTP:
    """假的 SMTP_SSL 连接，只记录调用。"""

    instances = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.login_args = None
        self.send_args = None
        self.quit_called = False
        FakeSMTP.instances.append(self)

    def login(self, user, password):
        self.login_args = (user, password)

    def sendmail(self, sender, recipients, message):
        self.send_args = (sender, recipients, message)

    def quit(self):
        self.quit_called = True


class TestFindLatestReport(unittest.TestCase):
    """报告定位。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='ut_mail_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_目录不存在时返回None(self):
        with mock.patch.object(dir_config, 'htmlreport_dir',
                               os.path.join(self.tmpdir, '没有这个目录')):
            self.assertIsNone(find_latest_report())

    def test_目录为空时返回None(self):
        with mock.patch.object(dir_config, 'htmlreport_dir', self.tmpdir):
            self.assertIsNone(find_latest_report())

    def test_取修改时间最新的报告(self):
        old = os.path.join(self.tmpdir, '老报告.html')
        new = os.path.join(self.tmpdir, '新报告.html')
        for path in (old, new):
            with open(path, 'w', encoding='utf-8') as fp:
                fp.write('x')
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        with mock.patch.object(dir_config, 'htmlreport_dir', self.tmpdir):
            self.assertEqual(find_latest_report(), new)

    def test_忽略非html文件(self):
        with open(os.path.join(self.tmpdir, 'junit-results.xml'), 'w') as fp:
            fp.write('x')
        with mock.patch.object(dir_config, 'htmlreport_dir', self.tmpdir):
            self.assertIsNone(find_latest_report())


class TestSentMailGuards(unittest.TestCase):
    """前置校验分支（都要求返回 False 且不抛异常）。"""

    def test_报告不存在时返回False(self):
        with patch_config():
            self.assertFalse(sent_mail(os.path.join('不存在', '报告.html')))

    def test_未传报告且目录无报告时返回False(self):
        with patch_config(), patch_dirs():
            self.assertFalse(sent_mail())

    def test_缺发件人配置时返回False(self):
        ini = '[sender]\nusername = 发件人\nemail =\npassword =\n'
        with patch_config(ini_text=ini), patch_dirs() as root:
            report = os.path.join(root, 'Report')
            os.makedirs(report)
            path = os.path.join(report, '报告.html')
            with open(path, 'w', encoding='utf-8') as fp:
                fp.write('x')
            self.assertFalse(sent_mail(path))

    def test_缺收件人配置时返回False(self):
        ini = ('[sender]\nusername = 发件人\nemail = a@qq.com\npassword = token\n'
               '[addressed]\n')
        with patch_config(ini_text=ini), patch_dirs() as root:
            report = os.path.join(root, 'Report')
            os.makedirs(report)
            path = os.path.join(report, '报告.html')
            with open(path, 'w', encoding='utf-8') as fp:
                fp.write('x')
            self.assertFalse(sent_mail(path))


class TestSentMailSuccess(unittest.TestCase):
    """发信成功路径（SMTP 被替换为假实现）。"""

    def setUp(self):
        FakeSMTP.instances = []
        self._smtp = mock.patch.object(sentMail.smtplib, 'SMTP_SSL', FakeSMTP)
        self._smtp.start()
        self._dirs = patch_dirs()
        self.root = self._dirs.__enter__()
        self._config = patch_config()
        self._config.__enter__()

        report_dir = os.path.join(self.root, 'Report')
        os.makedirs(report_dir)
        self.report = os.path.join(report_dir, '融媒体测试报告.html')
        with open(self.report, 'w', encoding='utf-8') as fp:
            fp.write('<html>报告</html>')

    def tearDown(self):
        self._config.__exit__(None, None, None)
        self._dirs.__exit__(None, None, None)
        self._smtp.stop()

    def test_发送成功返回True(self):
        self.assertTrue(sent_mail(self.report))

    def test_登录使用授权码(self):
        sent_mail(self.report)
        server = FakeSMTP.instances[0]
        self.assertEqual(server.login_args, ('sender@example.com', 'smtp-token'))

    def test_收件人为配置里全部邮箱(self):
        sent_mail(self.report)
        server = FakeSMTP.instances[0]
        _, recipients, _ = server.send_args
        self.assertEqual(sorted(recipients),
                         ['lisi@example.com', 'zhangsan@example.com'])

    def test_发送后断开连接(self):
        sent_mail(self.report)
        self.assertTrue(FakeSMTP.instances[0].quit_called)

    def test_邮件头部符合预期(self):
        sent_mail(self.report)
        _, _, raw = FakeSMTP.instances[0].send_args
        msg = email.message_from_string(raw)

        # 收件人：配置里两个邮箱都应在，且用分号连接
        self.assertEqual(msg['To'], 'zhangsan@example.com;lisi@example.com')
        # 发件人显示名是中文，会被 MIME 编码，需解码后再比对
        self.assertIn('单元测试发件人', decode_mail_header(msg['From']))
        self.assertIn('sender@example.com', decode_mail_header(msg['From']))

        # 中文主题/附件名会被 MIME 编码，需解码后再比对
        filenames = [part.get_filename() for part in msg.walk()
                     if part.get_filename()]
        self.assertIn('融媒体测试报告.html', filenames)
        self.assertIn('融媒体测试报告.html', decode_mail_header(msg['Subject']))

    def test_不指定报告时自动取最新报告(self):
        self.assertTrue(sent_mail())

    def test_自动定位时取的是最新那份(self):
        import time
        newer = os.path.join(os.path.dirname(self.report), '更新的报告.html')
        with open(newer, 'w', encoding='utf-8') as fp:
            fp.write('x')
        time.sleep(0.01)
        os.utime(newer, None)

        sent_mail()
        _, _, raw = FakeSMTP.instances[0].send_args
        msg = email.message_from_string(raw)
        filenames = [part.get_filename() for part in msg.walk()
                     if part.get_filename()]
        self.assertIn('更新的报告.html', filenames)


if __name__ == '__main__':
    unittest.main(verbosity=2)
