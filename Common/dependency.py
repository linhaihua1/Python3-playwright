# -*- coding: utf-8 -*-
"""
==========================================================================
 用例依赖管理（Dependency）
==========================================================================

【一句话说明】
    声明用例之间的依赖关系：当被依赖的用例失败时，依赖它的用例自动跳过，
    而不是继续执行后连环失败。让报告里"失败 1 条、跳过 5 条"比"失败 6 条"
    更准确地反映真实问题。

【在框架中的位置】
    TestSuites/testXxx.py
        @depends_on('test_login')
        def test_upload(self): ...

【用法】
    from Common.dependency import depends_on, DependencyTracker

    class TestFlow(unittest.TestCase):
        def test_login(self): ...          # 无依赖

        @depends_on('test_login')
        def test_enter_system(self): ...   # 依赖 test_login

        @depends_on('test_login', 'test_enter_system')
        def test_upload(self): ...         # 依赖多个

    # 在 runner.py 里接入：
    tracker = DependencyTracker()
    # 执行前检查：tracker.should_skip(test) 返回 (True, reason) 时跳过
    # 执行后记录：tracker.record_result(test, passed)

【设计原则】
    1. 依赖是「声明式」的：装饰器里写依赖谁，不需要改执行流程
    2. 跳过是「级联」的：A 失败 → B（依赖 A）跳过 → C（依赖 B）跳过
    3. 只跳过不失败：被跳过的用例在报告里显示 skipped，不计入 failures
==========================================================================
"""

import functools
import unittest

from Common.log import log1

# 被依赖方法名 -> 该方法是否通过的记录
# 结构：{ 'test_login': True, 'test_upload': False, ... }
_results = {}


def depends_on(*prerequisites):
    """装饰器：声明当前用例依赖哪些前置用例。

    用法：
        @depends_on('test_login')
        def test_upload(self): ...

        @depends_on('test_login', 'test_enter_system')
        def test_submit(self): ...

    前置用例失败/错误/跳过时，本用例自动跳过。
    """
    def decorator(func):
        func._depends_on = list(prerequisites)
        return func
    return decorator


class DependencyTracker:
    """依赖跟踪器：记录用例结果，判断后续用例是否该跳过。

    在 runner.py 的执行循环里使用：
        tracker = DependencyTracker()
        for case in suite:
            skip, reason = tracker.should_skip(case)
            if skip:
                result.addSkip(case, reason)
                continue
            case.run(result)
            tracker.record(case, result)
    """

    def __init__(self):
        self._results = {}      # 用例方法名 -> True(通过) / False(失败)

    def _method_name(self, test):
        """取出用例的方法名（不含类名），如 'test_login'。"""
        return getattr(test, '_testMethodName', '')

    def should_skip(self, test):
        """检查这条用例是否因前置失败而应跳过。

        :return: (should_skip: bool, reason: str)
        """
        method = getattr(test, self._method_name(test), None)
        deps = getattr(method, '_depends_on', None)
        if not deps:
            return False, ''

        failed = []
        for dep in deps:
            result = self._results.get(dep)
            if result is False:
                failed.append(dep)
            elif result is None:
                # 前置用例还没跑（可能被过滤掉了），也视为不可执行
                failed.append('%s(未执行)' % dep)

        if failed:
            reason = '前置用例未通过：%s' % ', '.join(failed)
            log1.info('[依赖管理] %s 跳过，原因：%s' % (self._method_name(test), reason))
            return True, reason
        return False, ''

    def record(self, test, result=None, passed=None):
        """记录一条用例的执行结果。

        :param test:   TestCase 实例
        :param result: unittest.TestResult（从中推断通过/失败）；传了 passed 则忽略
        :param passed: 显式指定 True/False
        """
        name = self._method_name(test)
        if passed is not None:
            self._results[name] = passed
        elif result is not None:
            # 通过：不在 failures 和 errors 里
            case_id = test.id()
            failed_ids = {t.id() for t, _ in result.failures} | {t.id() for t, _ in result.errors}
            self._results[name] = case_id not in failed_ids
        else:
            self._results[name] = True   # 没传任何信息默认当作通过（兼容未接入的场景）

    def reset(self):
        """清空记录（给单元测试用）。"""
        self._results.clear()


# 模块级单例，方便不想手动管理 tracker 的场景使用
_global_tracker = DependencyTracker()


def global_tracker():
    return _global_tracker


# ===========================================================================
# 套件级接入：把依赖守卫织进 runner 的执行链路（企业级接入点）
#
# 【为什么以前没生效（验收实测发现的断链）】
#   @depends_on 只在方法上打标记，should_skip 的判定需要有人在
#   每条用例执行前后调用 —— 但 runner 的执行循环在 HTMLTestRunner
#   内部，框架层从来没有真正接上。结果：前置用例失败后，依赖它的
#   用例照样跑、照样连环失败，报告里"失败 1 跳过 5"的承诺是空的。
# ===========================================================================
def _wrap_case_with_guard(case, tracker):
    """给单条用例包一层依赖守卫：执行前查跳过、执行后记录结果。【内部】

    为什么包装实例的 run 而不是改 result 类：
      依赖跳过必须发生在**用例执行之前**，而 result 的钩子都在
      用例已经开跑之后才触发。HTMLTestRunner 的循环是
      `for test in suite: test.run(result)`，包装实例 run 是唯一
      不侵入报告模板、又能拦在执行前的接入点。
    """
    import types
    original_run = case.run

    def guarded_run(bound_case, result=None):
        # ★ 第一个参数是 MethodType 绑定时隐式传入的 case 实例本身，
        #   不是 result —— 签名少写这一位，unittest 调用就会报
        #   "takes 0 to 1 positional arguments but 2 were given"。
        skip, reason = tracker.should_skip(bound_case)
        if skip and result is not None:
            result.startTest(bound_case)
            result.addSkip(bound_case, reason)
            result.stopTest(bound_case)
            return
        original_run(result)
        # 用例跑完后登记结果，供后续用例的 should_skip 判定。
        # 注意 record 从 result 的失败清单推断 —— 若 flaky 重试成功，
        # 用例不在 failures 里，会正确记为通过，两个特性天然兼容。
        if result is not None:
            tracker.record(bound_case, result)

    case.run = types.MethodType(guarded_run, case)
    return case


def wrap_suite_with_dependency_guard(suite, tracker=None):
    """给 suite 里所有带 @depends_on 标记的用例装上依赖守卫。

    :param suite:   unittest.TestSuite
    :param tracker: DependencyTracker；不传用模块级单例
    :return: 装好守卫的 TestSuite（无依赖标记时原样返回，零开销）

    在 Common/runner.py 的 build_suite 里调用：
        suite = wrap_suite_with_dependency_guard(suite)
    """
    import unittest
    tracker = tracker or global_tracker()

    def _iter(s):
        for item in s:
            if isinstance(item, unittest.TestSuite):
                yield from _iter(item)
            else:
                yield item

    cases = list(_iter(suite))
    has_dep = any(
        getattr(getattr(type(c), getattr(c, '_testMethodName', ''), None),
                '_depends_on', None)
        for c in cases)
    if not has_dep:
        return suite

    guarded = unittest.TestSuite(
        _wrap_case_with_guard(c, tracker) for c in cases)
    return guarded
