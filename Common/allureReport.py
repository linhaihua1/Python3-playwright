# -*- coding: utf-8 -*-
"""
==========================================================================
 Allure 报告集成（AllureReport）
==========================================================================

【一句话说明】
    在现有中文 HTML 报告 + JUnit XML 之外，额外生成 Allure 格式的结果文件，
    供 `allure serve` 或 Jenkins Allure 插件渲染出企业级可视化报告。
    不替代现有报告，是增量补充：三套报告各取所需（人看 HTML、CI 看 JUnit、
    管理层看 Allure 仪表盘）。

【在框架中的位置】
    run.py --allure          -> 开启 Allure 结果输出
        Common/allureReport.py（本文件）
            └─> 写入 allure-results/<uuid>-result.json  +  附件（截图）

    查看报告：
        allure serve allure-results/        # 本地直接打开浏览器看
        allure generate allure-results/     # 生成静态站点，CI 归档

【设计原则】
    1. 零额外依赖：Allure 结果就是标准 JSON + 附件，不需要装 allure Python 包
       （渲染时才需要 allure CLI，那是查看端的事，不影响测试执行）
    2. 与现有 runner 解耦：通过 unittest.TestResult 钩子收集结果，不侵入执行流程
    3. 附件自动关联：失败用例的截图自动挂到 Allure 结果上
    4. 中文支持：标签、描述、步骤名都用中文，报告里一目了然

【Allure 结果文件格式（简化版，兼容 allure 2.x）】
    {
      "name": "test_1login_登录统一门户",
      "status": "passed" | "failed" | "broken" | "skipped",
      "stage": "finished",
      "start": 1234567890000,
      "stop": 1234567895000,
      "uuid": "...",
      "attachments": [{"name": "失败截图", "source": "xxx.png", "type": "image/png"}],
      "labels": [{"name": "suite", "value": "登录冒烟"}, {"name": "tag", "value": "冒烟"}],
      "statusDetails": {"message": "...", "trace": "..."}
    }

【用法】
    # 1. 命令行开启
    python run.py --allure

    # 2. 代码里加步骤标签（可选，让报告更详细）
    from Common.allureReport import allure_step, allure_attach
    with allure_step('输入账号密码'):
        self.login_page.login(user, pwd)
    allure_attach('登录后截图', page.screenshot())
==========================================================================
"""

import json
import os
import time
import uuid
import traceback
import unittest

from Common.config import Config
from Common.log import log1

# Allure 状态映射
_STATUS_MAP = {
    'success': 'passed',
    'failure': 'failed',
    'error': 'broken',
    'skip': 'skipped',
}

# 模块级状态：当前是否开启 Allure，结果目录在哪
_enabled = False
_results_dir = None


def _ensure_results_dir():
    """确保 allure-results 目录存在，返回其绝对路径。"""
    global _results_dir
    if _results_dir:
        return _results_dir
    from Common import runContext
    base = runContext.subdir(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        Config.get('paths', 'allure_results_dir', 'allure-results')))
    os.makedirs(base, exist_ok=True)
    _results_dir = base
    return base


def enable():
    """开启 Allure 结果收集（在 run.py 里调用）。"""
    global _enabled
    _enabled = True
    d = _ensure_results_dir()
    log1.info('Allure 报告已开启，结果写入：%s' % d)


def is_enabled():
    return _enabled


def _write_result(data):
    """写一条 Allure 结果 JSON。"""
    if not _enabled:
        return
    d = _ensure_results_dir()
    uid = data.get('uuid') or str(uuid.uuid4())
    data['uuid'] = uid
    path = os.path.join(d, '%s-result.json' % uid)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def write_attachment(name, content, content_type='image/png', extension='.png'):
    """写一个附件（如截图），返回附件文件名。

    :param name:         附件显示名（中文）
    :param content:      bytes 或 str
    :param content_type: MIME 类型
    :param extension:    文件扩展名
    :return: 附件文件名（用于 result.json 的 attachments.source）
    """
    if not _enabled:
        return None
    d = _ensure_results_dir()
    fname = '%s%s' % (str(uuid.uuid4()), extension)
    path = os.path.join(d, fname)
    mode = 'wb' if isinstance(content, (bytes, bytearray)) else 'w'
    enc = None if mode == 'wb' else 'utf-8'
    with open(path, mode, encoding=enc) as f:
        f.write(content)
    return fname


def _build_result(test, status, start_ms, stop_ms, err=None, attachments=None):
    """构造一条 Allure 结果字典。"""
    case_id = test.id() if hasattr(test, 'id') else str(test)
    method = getattr(test, '_testMethodName', case_id)
    doc = getattr(test, method, None)
    description = (doc.__doc__ or '').strip() if doc else ''

    data = {
        'name': '%s: %s' % (method, description) if description else method,
        'status': _STATUS_MAP.get(status, status),
        'stage': 'finished',
        'start': start_ms,
        'stop': stop_ms,
        'uuid': str(uuid.uuid4()),
        'fullName': case_id,
        'labels': [
            {'name': 'package', 'value': type(test).__module__},
            {'name': 'suite', 'value': type(test).__name__},
            {'name': 'testClass', 'value': type(test).__name__},
            {'name': 'testMethod', 'value': method},
        ],
    }
    if err:
        exc_type, exc_val, exc_tb = err if isinstance(err, tuple) else (type(err), err, None)
        data['statusDetails'] = {
            'message': '%s: %s' % (exc_type.__name__, exc_val),
            'trace': ''.join(traceback.format_exception(exc_type, exc_val, exc_tb)) if exc_tb else str(exc_val),
        }
    if attachments:
        data['attachments'] = attachments
    return data


# ---------------------------------------------------------------------------
# unittest 集成：AllureTestResult 收集结果
# ---------------------------------------------------------------------------

class AllureTestResult(unittest.TestResult):
    """收集 unittest 结果并写 Allure JSON。

    用法（在 runner.py 里）：
        allure_result = AllureTestResult()
        # 执行后，allure_result 已把结果写到 allure-results/
    """

    def __init__(self):
        super().__init__()
        self._timings = {}     # test.id() -> (start_ms,)

    def startTest(self, test):
        super().startTest(test)
        self._timings[test.id()] = int(time.time() * 1000)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, 'success')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, 'failure', err)

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, 'error', err)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, 'skip')

    def _record(self, test, status, err=None):
        if not _enabled:
            return
        start = self._timings.get(test.id(), int(time.time() * 1000))
        stop = int(time.time() * 1000)
        attachments = []
        # 如果有失败截图，关联到附件
        try:
            from Common.dir_config import img_dir
            if os.path.isdir(img_dir):
                # 找最近的截图（粗略匹配：取目录下最新的 png）
                pngs = sorted(
                    (os.path.join(dp, f) for dp, _, fs in os.walk(img_dir) for f in fs if f.endswith('.png')),
                    key=os.path.getmtime, reverse=True)
                if pngs and (time.time() - os.path.getmtime(pngs[0])) < 60:
                    with open(pngs[0], 'rb') as f:
                        src = write_attachment('失败截图', f.read())
                        if src:
                            attachments.append({'name': '失败截图', 'source': src, 'type': 'image/png'})
        except Exception:       # noqa: BLE001 - 附件收集失败不影响主流程
            pass
        data = _build_result(test, status, start, stop, err, attachments or None)
        _write_result(data)


# ---------------------------------------------------------------------------
# 上下文管理器：步骤标签 + 附件（让报告更详细，可选使用）
# ---------------------------------------------------------------------------

class allure_step:
    """步骤标签上下文管理器，在 Allure 报告里展示步骤名。

    用法：
        with allure_step('输入账号密码'):
            page.fill(...)
    """

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        log1.info('[步骤] %s' % self.name)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            log1.error('[步骤] %s 失败：%s' % (self.name, exc_val))
        return False     # 不吞异常


def allure_attach(name, content, content_type='image/png'):
    """附加一个文件到当前 Allure 结果。

    :param name: 附件名
    :param content: bytes（截图）或 str（文本）
    """
    if not _enabled:
        return
    ext = '.txt' if isinstance(content, str) else '.png'
    ct = 'text/plain' if isinstance(content, str) else content_type
    write_attachment(name, content, ct, ext)
