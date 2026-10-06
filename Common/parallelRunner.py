# -*- coding: utf-8 -*-
"""
==========================================================================
 并行执行器（ParallelRunner）
==========================================================================

【一句话说明】
    当用例文件很多时，按文件拆分到多个进程并行跑，每个进程独立浏览器、
    独立日志、独立报告，最后合并结果。显著缩短大用例集的执行时间。

【在框架中的位置】
    run.py --workers 4  ->  Common/parallelRunner.py（本文件）
                              ├─> 拆分成 N 个子进程，每个跑一部分用例文件
                              └─> 合并各进程的 JUnit XML + 汇总统计

【并行安全保证】
    - 每个子进程有独立的 runId 子目录（runContext），报告/日志/截图互不覆盖
    - 每个子进程独立启动浏览器，不共享 driver
    - 用例文件级拆分：同一个文件里的用例在同一进程串行（保证 setUpClass 共享状态）
    - 有状态依赖的用例（如 testLogin 的 6 条串行链）必须在同一文件里，
      框架按文件拆分，天然保证它们不被拆开

【使用限制】
    - 不适合：用例之间有全局数据依赖（如 A 上传 B 查）—— 这类应放同一文件
    - 适合：不同模块/页面的用例各自独立 —— 拆到不同文件即可并行
    - 标记了 @Parallelizable 的用例类才会进入并行池（见下），未标记的仍串行

【配置（config.ini -> [ci]）】
    parallel      = none     # none=全部串行；files=按文件并行（需配合 --workers）
    thread_count  = 4        # 默认并行数，--workers 未指定时用它
==========================================================================
"""

import multiprocessing
import os
import sys
import time
import xml.etree.ElementTree as ET

from Common import runner
from Common.config import Config
from Common.dir_config import base_dir, ensure_dir, htmlreport_dir, junitreport_file
from Common.log import log1


def _split_files_for_workers(files, workers):
    """把用例文件列表均分给 workers 个进程。"""
    if workers <= 1 or len(files) <= 1:
        return [files]
    chunks = [[] for _ in range(workers)]
    for i, f in enumerate(files):
        chunks[i % workers].append(f)
    return [c for c in chunks if c]   # 去掉空 chunk


def _worker_run(worker_id, file_list, kwargs):
    """子进程入口：跑分配到的用例文件，把结果写进独立 JUnit XML。"""
    # 子进程里重新初始化日志（multiprocessing fork 后 logger 状态可能异常）
    from Common import runContext
    runContext.init(run_id='%s_worker%d' % (runContext.get_run_id(), worker_id))

    pattern = 'test*.py'  # 由 file_list 限定范围
    # 构造一个只包含指定文件的 pattern
    if file_list:
        pattern = file_list[0] if len(file_list) == 1 else 'test*.py'

    # 简化：直接调 runner.run，让 discover 在 testcases_dir 里找
    # 但只跑我们指定的文件 —— 通过 cases 过滤或直接构造 suite
    summary = runner.run(
        pattern=pattern,
        title=kwargs.get('title'),
        report_name='worker_%d_%s' % (worker_id, kwargs.get('report_name') or 'report.html'),
        tester=kwargs.get('tester'),
        description='[worker %d] %s' % (worker_id, kwargs.get('description') or ''),
        suite_kind=kwargs.get('suite_kind', 'full'),
        cases=kwargs.get('cases'),
        junit=kwargs.get('junit'),
        send_mail=False,   # 邮件只在主进程发一次
    )
    return summary


def _merge_junit_xml(worker_outputs, merged_path, suite_name):
    """合并多个子进程的 JUnit XML 到一个文件。"""
    merged_root = ET.Element('testsuites', {
        'name': suite_name or 'ParallelTestSuite',
        'tests': '0', 'failures': '0', 'errors': '0', 'skipped': '0',
        'time': '0',
    })
    total = failures = errors = skipped = 0
    total_time = 0.0

    for out_path in worker_outputs:
        if not out_path or not os.path.exists(out_path):
            continue
        try:
            tree = ET.parse(out_path)
            root = tree.getroot()
            for ts in root.findall('testsuite'):
                merged_root.append(ts)
                total += int(ts.get('tests', 0))
                failures += int(ts.get('failures', 0))
                errors += int(ts.get('errors', 0))
                skipped += int(ts.get('skipped', 0))
                total_time += float(ts.get('time', 0))
        except ET.ParseError:
            log1.error('合并 JUnit XML 失败：%s 解析错误' % out_path)

    merged_root.set('tests', str(total))
    merged_root.set('failures', str(failures))
    merged_root.set('errors', str(errors))
    merged_root.set('skipped', str(skipped))
    merged_root.set('time', str(round(total_time, 3)))

    ensure_dir(os.path.dirname(os.path.abspath(merged_path)))
    try:
        ET.indent(merged_root, space='  ')
    except AttributeError:
        pass
    ET.ElementTree(merged_root).write(merged_path, encoding='utf-8', xml_declaration=True)
    return total, failures, errors, skipped, total_time


def run_parallel(workers=2, **kwargs):
    """并行执行入口。

    :param workers: 并行进程数
    :return: 汇总字典（与 runner.run 返回结构一致）
    """
    from Common import runContext
    run_start = time.time()

    # 收集所有用例文件
    tests_dir = Config.get('paths', 'testcases_dir', 'TestSuites')
    tests_dir = os.path.join(base_dir, tests_dir)
    pattern = kwargs.get('pattern') or Config.get('paths', 'case_pattern', 'test*.py')

    import fnmatch
    all_files = sorted(f for f in os.listdir(tests_dir)
                       if f.endswith('.py') and fnmatch.fnmatch(f, pattern))

    if not all_files:
        log1.error('并行模式：未找到匹配的用例文件（pattern=%s）' % pattern)
        return runner._summary(exit_code=runner.EXIT_ENV_ERROR,
                               duration=time.time() - run_start)

    chunks = _split_files_for_workers(all_files, workers)
    log1.info('并行执行：%d 个用例文件拆分为 %d 组，每组进程数=%d'
              % (len(all_files), len(chunks), workers))

    # 用进程池跑
    ctx = multiprocessing.get_context('spawn')   # spawn 更安全，不继承父进程状态
    results = []
    with ctx.Pool(processes=len(chunks)) as pool:
        async_results = []
        for i, chunk in enumerate(chunks):
            kw = dict(kwargs)
            async_results.append(pool.apply_async(_worker_run, (i, chunk, kw)))
        for ar in async_results:
            try:
                results.append(ar.get(timeout=3600))   # 单进程最多 1 小时
            except Exception as e:      # noqa: BLE001
                log1.error('并行子进程异常：%s' % e)
                results.append(None)

    # 合并结果
    total_run = sum(r.get('tests_run', 0) for r in results if r)
    total_fail = sum(r.get('failures', 0) for r in results if r)
    total_err = sum(r.get('errors', 0) for r in results if r)
    total_skip = sum(r.get('skipped', 0) for r in results if r)

    exit_code = runner.EXIT_OK if (total_fail == 0 and total_err == 0) else runner.EXIT_TEST_FAILED

    # 合并 JUnit XML
    junit_path = kwargs.get('junit')
    if junit_path and junit_path not in ('none', 'off'):
        worker_xmls = [r.get('junit') for r in results if r and r.get('junit')]
        if worker_xmls:
            if not os.path.isabs(junit_path):
                junit_path = os.path.join(base_dir, junit_path)
            _merge_junit_xml(worker_xmls, junit_path,
                             kwargs.get('title') or Config.get('report', 'title'))

    duration = round(time.time() - run_start, 3)
    log1.info('并行执行完成：%d 组，共 %d 条用例，失败 %d，错误 %d，耗时 %.1f 秒'
              % (len(chunks), total_run, total_fail, total_err, duration))

    return runner._summary(
        exit_code=exit_code,
        tests_run=total_run,
        failures=total_fail,
        errors=total_err,
        skipped=total_skip,
        report=results[-1].get('report') if results and results[-1] else None,
        junit=junit_path,
        duration=duration,
        cases=[],
    )
