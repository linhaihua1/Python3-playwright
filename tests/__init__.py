# -*- coding: utf-8 -*-
"""
==========================================================================
 单元测试包（tests/）—— 只测「框架代码本身」，不跑 UI 用例
==========================================================================

【和 TestSuites/ 的区别 —— 新人务必分清】
    tests/        框架自身的单元测试。不启浏览器、不连网络、不碰生产系统，
                  几秒钟跑完，可随时执行，专门用来验证 Common/ 与 PageObject/
                  里的代码逻辑是否正确。改框架代码后**必须**先跑它。
    TestSuites/   被测系统的 UI 自动化用例。要真实打开 Chrome、真实登录、
                  真实上传视频，跑一次几分钟，且对被测环境有副作用。

【怎么跑】
    python tests/run_unit_tests.py                       # 一键跑全部
    python -m unittest discover -s tests -t . -p "test_*.py"   # 等价的 unittest 写法
    python tests/run_unit_tests.py tests.test_config     # 只跑某一个模块

【设计约束（新增测试时必须遵守）】
    1. 绝不允许真的启动浏览器 —— 一律用 tests/helpers.py 里的 FakePage 替代。
    2. 绝不允许写工程目录 —— 配置、日志、报告、截图全部重定向到临时目录
       （日志走 UI_LOG_DIR 环境变量，其余用 helpers.patch_config / helpers.patch_dirs）。
    3. 绝不允许连外网 —— SMTP 之类的出口一律 patch 掉。
    4. 不修改工程里的 config.ini / Logs / img / Report。

【为什么日志要先设环境变量再 import】
    Common/log.py 在**被 import 的那一刻**就 new 了两个 FileHandler，
    也就是说文件已经建出来了。所以"先 import、再把 handler 摘掉"只能阻止
    后续写入，阻止不了建文件——工程 Logs/ 下仍会出现一堆 0 字节日志。
    正确顺序是：先把日志根目录挪到临时目录（UI_LOG_DIR），再让 Common.log 被导入。
    tests/test_entrypoints.py 里还会起子进程跑 run.py，子进程继承本进程环境变量，
    因此同一招对它也有效。
==========================================================================
"""

import atexit
import logging
import os
import shutil
import sys
import tempfile


def _redirect_log_dir():
    """把日志根目录重定向到临时目录，必须在 Common.log 被导入之前调用。

    :return: 临时日志根目录；若设了 UT_KEEP_LOG=1 则返回 None（日志写回工程 Logs/）

    想保留日志便于排查时：UT_KEEP_LOG=1 python tests/run_unit_tests.py
    """
    if os.environ.get('UT_KEEP_LOG'):
        return None

    root = tempfile.mkdtemp(prefix='ut_logs_')
    os.environ['UI_LOG_DIR'] = root
    return root


def _silence_file_logging():
    """把工程 logger 的文件输出摘掉，避免单元测试刷出大量无意义日志。

    背景：不少用例会故意走 ERROR 分支（如元素定位失败、上传前置条件不满足），
    这些日志对测试结论没有价值，留着只会淹没真正的失败信息。
    这里只保留一个输出到 stderr 的 handler，既保持安静又不影响排查失败。

    想看到工程自身的日志（例如排查 runner 端到端用例为何失败）时，
    设置环境变量 UT_SHOW_LOG=1 再跑测试即可。

    注意：test_log.py 会用自己的独立 logger 名验证真实的文件写入行为，
    不依赖本函数处理过的 log1，两者互不干扰。
    """
    try:
        from Common.log import log1
    except Exception:       # pragma: no cover - 依赖缺失时跳过，交由具体测试报错
        return

    for handler in list(getattr(log1, 'handlers', [])):
        try:
            log1.removeHandler(handler)
            handler.close()
        except Exception:   # pragma: no cover - 关闭失败不影响测试
            pass

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
    log1.addHandler(stream)
    # 默认把噪音压到最低（不少用例会故意触发 ERROR 分支），
    # 需要时用 UT_SHOW_LOG=1 打开
    log1.setLevel(logging.ERROR if os.environ.get('UT_SHOW_LOG') else logging.CRITICAL)


# ★ 顺序不能改：先挪走日志目录，再导入 Common.log（见本文件头部说明）
_LOG_ROOT = _redirect_log_dir()
if _LOG_ROOT:
    atexit.register(shutil.rmtree, _LOG_ROOT, ignore_errors=True)

_silence_file_logging()
