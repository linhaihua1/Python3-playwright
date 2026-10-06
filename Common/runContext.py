# -*- coding: utf-8 -*-
"""
==========================================================================
 运行上下文与产物目录隔离（RunContext）
==========================================================================

【一句话说明】
    每次测试执行生成一个唯一的 runId，所有产物（报告/日志/截图）都放进
    以 runId 命名的子目录里，避免多次执行（尤其是并发执行）互相覆盖。

【在框架中的位置】
    run.py 启动时 -> RunContext.init() 生成 runId
        ├─> dir_config.py  拼接路径时带 runId 子目录
        ├─> log.py         日志目录带 runId
        └─> basePage.py    截图目录带 runId

【为什么需要它】
    企业级 CI 常遇到：同一台机器上两个构建同时跑，报告文件互相覆盖，
    最后归档的不知道是哪次的结果。有了 runId 隔离，每次执行的产物
    各占一个目录，Jenkins 归档时全部收走，互不干扰。

【配置（config.ini -> [ci]）】
    isolated_output = true    # 开启产物隔离（默认 true，并发安全）
                              # 关闭后产物直接落在 Report/ Logs/ img/ 根目录（旧行为）

【runId 格式】
    20261006_153045_a1b2c3    # 日期_时间_6位随机串
    也可由环境变量 UI_RUN_ID 注入（CI 想用构建号当 runId 时）

【对其他模块的影响】
    - dir_config.htmlreport_dir  →  Report/<runId>/（开启隔离时）
    - log.py 日志目录             →  Logs/<runId>/日期/（开启隔离时）
    - basePage.get_img() 截图目录 →  img/<runId>/日期/（开启隔离时）
    - junit XML 路径              →  Report/<runId>/junit-results.xml
==========================================================================
"""

import os
import random
import string
import time

from Common.config import Config
from Common.log import log1

# 模块级单例：runId 在 init() 后固定，全进程共享
_run_id = None
_initialized = False


def _generate_run_id():
    """生成默认 runId：日期_时间_随机串。"""
    ts = time.strftime('%Y%m%d_%H%M%S', time.localtime())
    rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return '%s_%s' % (ts, rand)


def init(run_id=None):
    """初始化运行上下文，生成本次执行的唯一 runId。

    :param run_id: 外部指定的 runId（如 CI 构建号）；不传则自动生成
    :return: runId 字符串

    幂等：重复调用返回同一个 runId，不会重新生成。
    """
    global _run_id, _initialized
    if _initialized and _run_id:
        return _run_id

    # 环境变量优先（CI 可注入 UI_RUN_ID，如用 Jenkins 构建号）
    env_id = os.environ.get('UI_RUN_ID', '').strip()
    _run_id = run_id or env_id or _generate_run_id()
    _initialized = True

    isolated = Config.get_bool('ci', 'isolated_output', True)
    if isolated:
        log1.info('产物目录隔离已开启，本次 runId = %s，所有产物将写入独立子目录' % _run_id)
    else:
        log1.info('产物目录隔离已关闭（[ci] isolated_output=false），产物写入根目录')
    return _run_id


def get_run_id():
    """获取当前 runId；未初始化时自动初始化一次。"""
    global _run_id, _initialized
    if not _initialized:
        init()
    return _run_id


def is_isolated():
    """是否开启了产物目录隔离。"""
    return Config.get_bool('ci', 'isolated_output', True)


def subdir(base_dir):
    """在 base_dir 下拼出 runId 子目录（开启隔离时）；否则返回 base_dir 本身。

    :param base_dir: 原始目录绝对路径，如 .../Report
    :return: 隔离时 .../Report/<runId>，否则 .../Report

    注意：仅当 runContext 已被显式 init() 后才生效；未初始化时原样返回，
    避免在 import 阶段（如单元测试）意外创建 runId 子目录。
    """
    global _initialized
    if not _initialized or not is_isolated():
        return base_dir
    return os.path.join(base_dir, _run_id)


def reset():
    """重置上下文（主要给单元测试用，清掉模块级状态）。"""
    global _run_id, _initialized
    _run_id = None
    _initialized = False
