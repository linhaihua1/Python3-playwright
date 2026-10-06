# -*- coding: utf-8 -*-
"""
==========================================================================
 测试数据治理（DataGovernance）
==========================================================================

【一句话说明】
    用例执行过程中创建的测试数据（上传的素材、提交的报送单、新建的用户等），
    在用例结束后自动清理，避免污染被测环境。每条数据创建时注册一个清理回调，
    tearDown 时统一执行，做到"来时干净、走时也干净"。

【在框架中的位置】
    TestSuites/testXxx.py
        ├─> self.data.record('上传视频', cleanup_callback)   # 创建时注册
        └─> tearDown -> self.data.cleanup_all()              # 结束时清理

【为什么需要它】
    企业级自动化最容易被忽视的问题：用例跑多了，被测系统里堆满了测试数据，
    影响性能、干扰人工测试、甚至触发业务告警。有了治理层，每条测试数据
    都有对应的清理动作，用例失败也不影响清理（在 finally 里执行）。

【核心设计】
    1. 唯一数据标签：每条测试数据带一个 tag（默认含时间戳+随机串），
       避免和真实数据或其他用例的数据混淆，清理时只动自己创建的。
    2. 注册即承诺：record(name, callback) 时 callback 必须能幂等地删除数据，
       删不掉也要尽力（catch 异常后记日志，不影响其他清理）。
    3. 倒序清理：后创建的先清理（LIFO），符合"先建主表再建子表"的依赖顺序。
    4. 失败不中断：某条清理失败只记日志，继续清其他的，最终汇总报告。

【用法示例】
    from Common.dataGovernance import TestDataGovernor

    class TestUpload(unittest.TestCase):
        def setUp(self):
            self.data = TestDataGovernor()

        def test_upload(self):
            # 上传文件，拿到素材 ID
            material_id = self.login_page.upload_file(path)
            # 注册清理：用例结束后调 API 删除这条素材
            self.data.record('上传素材-%s' % material_id,
                             lambda: self.api.delete_material(material_id))
            ...

        def tearDown(self):
            self.data.cleanup_all()   # 无论用例成败都清

【隔离用例基类（推荐直接继承）】
    from Common.dataGovernance import IsolatedTestCase
    class TestUpload(IsolatedTestCase):
        def test_upload(self):
            self.data.record(...)   # self.data 已自动初始化，tearDown 自动清理
==========================================================================
"""

import random
import string
import time
import traceback
import unittest

from Common.log import log1


def _gen_tag():
    """生成唯一数据标签：日期 + 6 位随机串，如 '20261006_abc123'。

    用途：给测试数据命名时带上这个后缀，清理时按 tag 精确匹配，
    避免误删真实数据或其他用例的数据。
    """
    ts = time.strftime('%Y%m%d_%H%M%S', time.localtime())
    rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return '%s_%s' % (ts, rand)


class TestDataGovernor:
    """测试数据治理器：注册数据 → 自动清理。

    一个用例实例持有一个 Governor，记录本用例创建的所有数据及其清理回调。
    """

    def __init__(self):
        self._records = []          # [(name, callback), ...] 按创建顺序
        self.tag = _gen_tag()       # 本用例的唯一数据标签
        self._cleaned = False

    def record(self, name, cleanup_callback):
        """注册一条测试数据及其清理回调。

        :param name:              数据名称（仅用于日志可读性），如 '上传素材-12345'
        :param cleanup_callback:  无参可调用对象，执行后应删除该数据；
                                  要求幂等（重复调用不报错）、失败时内部 catch
        """
        self._records.append((name, cleanup_callback))
        log1.info('[数据治理] 已注册：%s（tag=%s）' % (name, self.tag))

    def count(self):
        """当前已注册但未清理的数据条数。"""
        return len(self._records)

    def cleanup_all(self):
        """清理所有已注册的数据（倒序，LIFO）。

        - 每条清理独立 try/except，一条失败不影响其他
        - 清理完成后标记 _cleaned，重复调用安全
        - 返回清理报告 dict：{'total': N, 'success': M, 'failed': K, 'failures': [...]}
        """
        if self._cleaned:
            return {'total': 0, 'success': 0, 'failed': 0, 'failures': []}

        total = len(self._records)
        success = 0
        failed = 0
        failures = []

        # 倒序：后创建的先删（主表依赖子表时，子表先建后删更安全）
        for name, callback in reversed(self._records):
            try:
                callback()
                success += 1
                log1.info('[数据治理] 清理成功：%s' % name)
            except Exception as e:          # noqa: BLE001 - 清理失败绝不能影响后续清理
                failed += 1
                err = '%s: %s' % (type(e).__name__, e)
                failures.append((name, err))
                log1.error('[数据治理] 清理失败：%s —— %s' % (name, err))
                log1.error(traceback.format_exc())

        self._cleaned = True
        self._records.clear()

        log1.info('[数据治理] 清理完成：共 %d 条，成功 %d，失败 %d（tag=%s）'
                  % (total, success, failed, self.tag))
        return {'total': total, 'success': success, 'failed': failed, 'failures': failures}

    def __del__(self):
        """兜底：对象被 GC 时若还没清理，尽力清一次。"""
        try:
            if not self._cleaned and self._records:
                self.cleanup_all()
        except Exception:       # noqa: BLE001 - __del__ 里任何错误都不能冒泡
            pass


class IsolatedTestCase(unittest.TestCase):
    """数据隔离用例基类：自动初始化 Governor，tearDown 自动清理。

    用法：
        class TestXxx(IsolatedTestCase):
            def test_xxx(self):
                self.data.record('某数据', lambda: cleanup())
                # 无需自己写 tearDown，基类已自动清理

    注意：如果子类重写了 tearDown，记得先调 super().tearDown()。
    """

    def setUp(self):
        """每个用例开始前创建一个全新的数据治理器。"""
        super().setUp()
        self.data = TestDataGovernor()

    def tearDown(self):
        """每个用例结束后自动清理所有已注册的测试数据。"""
        if hasattr(self, 'data'):
            report = self.data.cleanup_all()
            if report['failed']:
                # 清理失败不影响用例本身的结果，但要在日志里醒目提示
                log1.error('[数据治理] 有 %d 条数据清理失败，请人工核查：%s'
                           % (report['failed'],
                              '; '.join('%s(%s)' % (n, e) for n, e in report['failures'])))
        super().tearDown()
