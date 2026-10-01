# -*- coding: utf-8 -*-
"""
==========================================================================
 一键运行全部框架单元测试
==========================================================================

【怎么用】
    python tests/run_unit_tests.py                          # 跑全部
    python tests/run_unit_tests.py tests.test_config         # 只跑某个模块
    python tests/run_unit_tests.py tests.test_config tests.test_runner
    python tests/run_unit_tests.py --junit Report/unit-tests.xml
                                                           # 顺带产出 JUnit XML
                                                           # （流水线里用）

    等价的 unittest 官方写法：
    python -m unittest discover -s tests -t . -p "test_*.py"

【和 python run.py 的区别】
    run.py          跑被测系统的 UI 用例：开浏览器、连真实环境、几分钟、有数据副作用
    run_unit_tests  跑框架自身的单元测试：不开浏览器、不联网、几十秒、零业务副作用

    改过 Common/ 或 PageObject/ 里的代码后，**先跑这个**，
    通过了再去跑 UI 用例 —— 能把"框架坏了"和"页面改版了"两类问题分开。

【退出码】
    0 全部通过     1 有测试失败
    （与 run.py 一样的约定，方便挂在流水线的第一个 stage 当质量门禁）
==========================================================================
"""

import argparse
import os
import sys
import time
import unittest


def _base_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _prepare():
    """把工程根加入 sys.path，并导入 tests 包做必要的测试前置。"""
    base_dir = _base_dir()
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)

    # 导入 tests 包会做必要的测试隔离准备：
    #   摘掉工程 logger 的文件输出，避免测试往工程 Logs/ 里写日志
    #   （Playwright 的等待内建于 Locator，桩对象不产生真实等待，无需提速钩子）
    import tests  # noqa: F401

    return base_dir


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='运行工程框架的单元测试（不开浏览器、无业务副作用）')
    parser.add_argument('modules', nargs='*',
                        help='只运行指定模块，例如 tests.test_config；'
                             '不传则发现并运行 tests/ 下全部')
    parser.add_argument('--junit', help='把结果写成 JUnit XML，供 Jenkins 解析')
    return parser.parse_args(argv)


def build_suite(base_dir, modules):
    loader = unittest.TestLoader()
    if modules:
        print('运行指定模块：%s' % ', '.join(modules))
        return loader.loadTestsFromNames(modules)
    print('发现并运行 tests/ 下全部单元测试 ...')
    return loader.discover(
        start_dir=os.path.join(base_dir, 'tests'),
        pattern='test_*.py',
        top_level_dir=base_dir,
    )


def run_with_junit(suite, junit_path):
    """用工程的 JUnitResult 跑测试，同时产出 XML 与控制台摘要。

    复用 Common/junitReport.py，与 UI 测试走同一套结果格式，
    这样流水线上两类测试的展示方式完全一致。
    """
    from Common.junitReport import JUnitResult, write_junit_xml

    marks = {'success': 'ok', 'failure': 'FAIL', 'error': 'ERROR', 'skipped': 'skip'}
    result = JUnitResult(verbosity=0)
    started = time.time()
    suite.run(result)

    for item in result.cases:
        print('%-6s %s.%s' % (marks.get(item['status'], '?'),
                              item['classname'], item['name']))
    for item in result.cases:
        if item['status'] in ('failure', 'error'):
            print('')
            print('------ %s.%s ------'
                  % (item['classname'], item['name']))
            print(item['detail'] or item['message'])

    write_junit_xml(result, junit_path, suite_name='框架单元测试', run_start=started)
    print('')
    print('JUnit XML 已生成：%s' % junit_path)
    return result


def print_conclusion(tests_run, failures, errors, skipped, duration):
    print('')
    print('==================== 单元测试结论 ====================')
    print('用例总数 : %d' % tests_run)
    print('失败     : %d' % failures)
    print('错误     : %d' % errors)
    print('跳过     : %d' % skipped)
    print('耗时     : %.1f 秒' % duration)
    print('结果     : %s' % ('通过' if not failures and not errors else '未通过'))
    print('======================================================')


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    base_dir = _prepare()
    suite = build_suite(base_dir, args.modules)

    started = time.time()
    if args.junit:
        # 需要机器可读结果时走这条路（流水线用），自己打印摘要
        result = run_with_junit(suite, args.junit)
        failures = len(result.failures)
        errors = len(result.errors)
        tests_run = result.testsRun
        skipped = len(result.skipped)
    else:
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        failures = len(result.failures)
        errors = len(result.errors)
        tests_run = result.testsRun
        skipped = len(result.skipped)

    print_conclusion(tests_run, failures, errors, skipped, time.time() - started)
    return 0 if (failures == 0 and errors == 0) else 1


if __name__ == '__main__':
    sys.exit(main())
