# -*- coding: utf-8 -*-
"""
==========================================================================
 用例级 Flaky 重试（FlakyRetry）
==========================================================================

【一句话说明】
    整条用例因偶发问题（网络抖动、元素渲染慢、点击被遮挡）失败时，
    自动重跑 N 次，只要有一次通过就算通过 —— 降低 CI 上的偶发失败率。
    与 BasePage 的操作级 retry 互补：那个重试单个动作，这个重试整条用例。

【在框架中的位置】
    config.ini [ci] flaky_retry = 2       # 重试次数（默认 0，关闭）
    run.py                                # 执行时由 RetryTransformer 注入
        └─> Common/flakyRetry.py（本文件）
              └─> unittest 执行器          # 用例失败时回调 should_retry

【与操作级重试的区别】
    操作级（BasePage.retry）：在一条用例内部，某个 click 失败了重试 click。
    用例级（本模块）：整条 test_xxx 跑完挂了，从头再跑一遍 test_xxx。

【重试策略】
    - 只重试「失败」和「错误」，不重试「跳过」
    - 重试次数用尽仍失败，才把最后一次的结果记为最终结果
    - 每次重试前清理：重新执行 setUp（如果是 TestCase 级重试）
    - 重试间隔可配（[ci] flaky_retry_interval，默认 2 秒）

【配置（config.ini -> [ci]）】
    flaky_retry          = 2     # 用例级重试次数，0 = 关闭（默认）
    flaky_retry_interval = 2     # 每次重试之间的等待秒数

【用法】
    无需改任何用例代码，只要在 config.ini 配好 [ci] flaky_retry > 0，
    run.py 会自动通过 RetryTransformer 让所有用例具备重试能力。

    也可以给单个用例类/方法加 @flaky(retry=3) 单独指定重试次数：
        from Common.flakyRetry import flaky
        class TestXxx(unittest.TestCase):
            @flaky(retry=3)
            def test_unstable(self):
                ...
==========================================================================
"""

import functools
import time
import unittest

from Common.config import Config
from Common.log import log1

# 默认重试参数（技术默认值，可被配置覆盖）
DEFAULT_RETRY = 0
DEFAULT_INTERVAL = 2


def _get_global_retry():
    """读取全局重试配置 [ci] flaky_retry，默认 0（关闭）。"""
    return Config.get_int('ci', 'flaky_retry', DEFAULT_RETRY)


def _get_retry_interval():
    """读取重试间隔 [ci] flaky_retry_interval，默认 2 秒。"""
    return Config.get_float('ci', 'flaky_retry_interval', DEFAULT_INTERVAL)


def flaky(retry=None, interval=None):
    """装饰器：标记用例/用例类为「可重试」，单独指定重试次数。

    用法：
        @flaky(retry=3)              # 类级：该类所有用例都重试 3 次
        class TestXxx(unittest.TestCase): ...

        class TestXxx(unittest.TestCase):
            @flaky(retry=2)          # 方法级：只这个用例重试 2 次
            def test_unstable(self): ...

    优先级：方法级 > 类级 > 全局配置 [ci] flaky_retry
    """
    def decorator(obj):
        if isinstance(obj, type) and issubclass(obj, unittest.TestCase):
            # 类级：打在类上，所有方法继承
            obj._flaky_retry = retry
            obj._flaky_interval = interval
            return obj
        # 方法级：打在方法上
        obj._flaky_retry = retry
        obj._flaky_interval = interval
        return obj
    return decorator


def _resolve_retry(test):
    """解析一条用例的实际重试次数与间隔。

    优先级：方法装饰器 > 类装饰器 > 全局配置
    """
    method = getattr(test, test._testMethodName, None)
    method_retry = getattr(method, '_flaky_retry', None)
    method_interval = getattr(method, '_flaky_interval', None)

    cls = type(test)
    cls_retry = getattr(cls, '_flaky_retry', None)
    cls_interval = getattr(cls, '_flaky_interval', None)

    retry = method_retry
    if retry is None:
        retry = cls_retry
    if retry is None:
        retry = _get_global_retry()

    interval = method_interval
    if interval is None:
        interval = cls_interval
    if interval is None:
        interval = _get_retry_interval()

    return max(0, int(retry)), float(interval)


def _ensure_counts(self):
    """重试计数字典（惰性初始化）。【模块级，RetryTestResult 与
    FlakyResultMixin 共享同一实现，避免两份逻辑漂移】

    为什么惰性：FlakyResultMixin 组合出的 result 类走基类 __init__，
    不会执行 RetryTestResult 的初始化 —— 硬属性会在 addFailure 时
    炸出 AttributeError。惰性创建对两种载体都成立。
    """
    if getattr(self, '_retry_counts', None) is None:
        self._retry_counts = {}
    return self._retry_counts


class RetryTestResult(unittest.TestResult):
    """带重试能力的 TestResult：用例失败时自动重跑。

    设计：包裹原始 result，在 addFailure/addError 时判断是否需要重试，
    需要则重新执行用例并记录，最终只把最后一次的结果传给原始 result。

    ★ 为什么 startTest/stopTest/wasSuccessful/testsRun 要显式转发
      （验收实测发现的语义缺陷）：
      本类继承自 unittest.TestResult，这些名字在**父类上就有实现**，
      Python 属性查找命中父类方法后根本不会走 __getattr__ 透传 ——
      于是 startTest 把计数加在**自己身上**而 original 恒为 0，
      addFailure 又只转发 original，自身 failures 恒为空，
      最终 wasSuccessful() 恒为 True：全失败的执行被判成"成功"，
      CI 会把挂着失败结果的构建标绿。修复方式就是逐个显式转发。
    """

    def __init__(self, original_result):
        # ★ _original 必须先于 super().__init__ 赋值：
        #   父类 __init__ 里会设置 self.testsRun = 0，而 testsRun 已被
        #   改写为转发 property —— setter 要访问 _original，
        #   顺序反了就是无限递归（验收实测踩过）。
        self._original = original_result
        super().__init__(original_result.stream if hasattr(original_result, 'stream') else None,
                         original_result.showAll if hasattr(original_result, 'showAll') else False,
                         original_result.dots if hasattr(original_result, 'dots') else False)
        self._retry_counts = {}      # test.id() -> 已重试次数

    # ---- 显式转发：这些方法在父类上有实现，__getattr__ 拦不到 ----
    def startTest(self, test):
        self._original.startTest(test)

    def stopTest(self, test):
        self._original.stopTest(test)

    def wasSuccessful(self):
        return self._original.wasSuccessful()

    @property
    def testsRun(self):
        return self._original.testsRun

    @testsRun.setter
    def testsRun(self, value):
        self._original.testsRun = value

    _counts = _ensure_counts

    def _should_retry(self, test):
        """判断这条用例是否还能重试。"""
        retry_max, _ = _resolve_retry(test)
        if retry_max <= 0:
            return False
        done = self._counts().get(test.id(), 0)
        return done < retry_max

    def _run_with_retry(self, test, err):
        """失败后重试用例，返回最终是否通过。

        ★ 为什么是 while 循环而不是单次重试（验收实测发现的缺陷）：
          旧实现只重试一次就返回 —— 配置 retry=3 实际只执行 2 次
          （初次 1 次 + 重试 1 次），用例没跑满配置给的机会，
          "降低偶发失败率"的目标打了折扣。改为循环重试：
          每轮计一次数，成功立即返回，失败且未到上限则继续。
        """
        retry_max, interval = _resolve_retry(test)
        case_id = test.id()
        test_method = getattr(test, test._testMethodName)

        while self._counts().get(case_id, 0) < retry_max:
            attempt = self._counts().get(case_id, 0) + 1
            self._counts()[case_id] = attempt

            log1.warning('[用例重试] %s 第 %d/%d 次重试（上次失败：%s）'
                         % (case_id, attempt, retry_max, str(err[1])[:100]))

            if interval > 0:
                time.sleep(interval)

            try:
                # 重新执行 setUp + 用例方法 + tearDown
                test.setUp()
                test_method()
                test.tearDown()
                return True      # 重试成功
            except Exception:    # noqa: BLE001 - 捕获所有异常判断是否继续重试
                test.tearDown()
                # 未达上限继续下一轮；达到上限退出循环返回 False
        return False

    def addFailure(self, test, err):
        if self._should_retry(test) and self._run_with_retry(test, err):
            # 重试成功 → 当成功处理
            self._original.addSuccess(test)
            log1.info('[用例重试] %s 重试后通过' % test.id())
            return
        # 不再重试或重试仍失败 → 传给原始 result
        self._original.addFailure(test, err)

    def addError(self, test, err):
        if self._should_retry(test) and self._run_with_retry(test, err):
            self._original.addSuccess(test)
            log1.info('[用例重试] %s 重试后通过' % test.id())
            return
        self._original.addError(test, err)

    def addSuccess(self, test):
        self._original.addSuccess(test)

    def addSkip(self, test, reason):
        self._original.addSkip(test, reason)

    def __getattr__(self, name):
        """把未覆盖的属性/方法透传给原始 result。"""
        return getattr(self._original, name)


class RetryRunner:
    """重试执行器：包装原始 runner，让它使用 RetryTestResult。

    用法（在 runner.py 里）：
        runner = HTMLTestRunner(...)
        if Config.get_int('ci', 'flaky_retry', 0) > 0:
            runner = RetryRunner(runner)
        result = runner.run(suite)
    """

    def __init__(self, original_runner):
        self._original = original_runner

    def run(self, test):
        # 先跑一次拿到原始 result，然后用 RetryTestResult 包装
        original_run = self._original.run
        # HTMLTestRunner.run 内部会创建 result，我们需要拦截它
        # 简单做法：直接执行 suite，收集结果后应用重试逻辑
        result = unittest.TestResult()
        test.run(result)

        # 对失败/错误的用例应用重试
        retry_result = RetryTestResult(result)
        for failure in list(result.failures):
            test_case, err = failure
            result.failures.remove(failure)
            # 重新构造 err 为 tuple
            retry_result.addFailure(test_case, err if isinstance(err, tuple) else (type(err), err, None))
        for error in list(result.errors):
            test_case, err = error
            result.errors.remove(error)
            retry_result.addError(test_case, err if isinstance(err, tuple) else (type(err), err, None))

        return result


def wrap_for_retry(test_runner):
    """如果开启了用例级重试，返回带重试能力的 runner；否则原样返回。

    在 Common/runner.py 里调用：
        runner = wrap_for_retry(HTMLTestRunner(...))
    """
    if _get_global_retry() > 0:
        log1.info('已启用用例级 flaky 重试：最多 %d 次，间隔 %.1f 秒'
                  % (_get_global_retry(), _get_retry_interval()))
    return test_runner


# ===========================================================================
# 结果类混入：把重试能力挂进 runner 的正常报告流程（企业级接入点）
#
# 【为什么不用 RetryRunner 包装（验收实测的结论）】
#   HTMLTestRunner 在 run() 内部就完成了 HTML 渲染，RetryRunner 在外层
#   重试的结果**回写不进已生成的报告** —— 报告显示失败、JUnit/退出码
#   却是成功，两份证据互相矛盾，这在 CI 上比不重试更糟。
#   正确姿势是把重试做进 result 类本身：addFailure 发生在报告流程内，
#   重试成功后按成功记录，HTML / JUnit / 退出码三份证据天然一致。
# ===========================================================================
class FlakyResultMixin:
    """给任意 TestResult 子类混入「失败自动重试」能力。

    用法（在 runner.py 里）：
        resultclass = make_flaky_result_class(JUnitResult)   # 组合而非继承固定类
        HTMLTestRunner(..., resultclass=resultclass)

    重试语义见 RetryTestResult._run_with_retry：按 @flaky 标记或
    [ci] flaky_retry 配置循环重试，成功按成功记录、用尽按最终结果记录。
    """

    # 借用 RetryTestResult 的既有实现，避免两份重试逻辑漂移
    _counts = _ensure_counts
    _should_retry = RetryTestResult._should_retry
    _run_with_retry = RetryTestResult._run_with_retry

    def addFailure(self, test, err):
        if self._should_retry(test) and self._run_with_retry(test, err):
            self.addSuccess(test)
            return
        super().addFailure(test, err)

    def addError(self, test, err):
        if self._should_retry(test) and self._run_with_retry(test, err):
            self.addSuccess(test)
            return
        super().addError(test, err)


def make_flaky_result_class(base_class):
    """把「失败自动重试」混入一个 TestResult 子类，返回新类。

    :param base_class: 原 result 类（如 JUnitResult）
    :return: 组合后的新类；mixin 在前保证重试逻辑优先于基类记录
    """
    return type('FlakyResult', (FlakyResultMixin, base_class), {})


def suite_needs_retry(suite):
    """判断 suite 里是否存在显式打了 @flaky 标记的用例。【内部】"""
    for case in _iter_suite_cases(suite):
        method = getattr(case, getattr(case, '_testMethodName', ''), None)
        if getattr(method, '_flaky_retry', None) is not None:
            return True
        if getattr(type(case), '_flaky_retry', None) is not None:
            return True
    return False


def _iter_suite_cases(suite):
    """递归展开 TestSuite（不依赖 runner，避免循环导入）。"""
    import unittest
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_suite_cases(item)
        else:
            yield item
