# -*- coding: utf-8 -*-
"""
==========================================================================
 生产环境安全防护（EnvGuard）
==========================================================================

【一句话说明】
    防止测试用例在生产环境执行写操作（上传文件、提交表单、删除数据等）。
    当检测到目标环境为生产环境时，自动切换到「只读模式」，拦截所有写操作，
    并给出明确的中文报错，而不是让用例默默污染生产数据。

【在框架中的位置】
    run.py --env prod  ->  Common/envGuard.py（本文件）
                              ├─> Common/config.py   注入 UI_TESTCASE_MODE / UI_SAFETY_ALLOW_WRITE
                              └─> Common/basePage.py 写操作前检查 allow_write()

【判定规则（以下任一命中即视为生产环境）】
    1. 命令行 --env 的值为 prod / production / online / release
    2. config.ini [environment] url 命中 [safety] prod_url_patterns 配置的正则
    3. 环境变量 UI_ENV_KIND=prod

【只读模式下的行为】
    - UI_TESTCASE_MODE = readonly（写在日志里，一眼可见）
    - UI_SAFETY_ALLOW_WRITE = false
    - BasePage 的写操作（files_upload / 提交类 click）会先调 allow_write()，
      返回 False 时抛出 ProductionWriteBlocked 异常，用例标记为「错误」而非「失败」
    - 冒烟集（--suite smoke）本身就是只读的，不受影响

【配置（config.ini -> [safety]）】
    enabled              = true    # 总开关，默认开启
    prod_url_patterns    = .*prod.*,.*online.*,.*release.*   # 命中这些正则的 URL 视为生产
    allow_write_on_prod  = false   # 生产环境是否允许写操作（默认不允许，改了等于关掉防护）

【环境变量】
    UI_ENV_KIND           = prod | test   # 直接指定环境类型，优先级高于 URL 匹配
    UI_TESTCASE_MODE      = readonly      # 由本模块在生产环境自动注入
    UI_SAFETY_ALLOW_WRITE = false         # 由本模块在生产环境自动注入
==========================================================================
"""

import os
import re

from Common.config import Config
from Common.log import log1

# 命令行 --env 取值 -> 环境类型
_PROD_ENV_VALUES = ('prod', 'production', 'online', 'release')
_TEST_ENV_VALUES = ('test', 'testing', 'staging', 'dev', 'development')

# 标记：当前进程是否已被判定为生产环境（只读模式）
_is_production = None
_mode = None


class ProductionWriteBlocked(Exception):
    """在生产环境（只读模式）尝试执行写操作时抛出。

    用例会被标记为「错误」而非「失败」，因为这不是断言不通过，
    而是安全策略拦截 —— 两者在 CI 上的处置不同：
    错误 = 框架/环境问题，需要人工介入；失败 = 业务断言不通过。
    """

    def __init__(self, operation, detail=''):
        self.operation = operation
        msg = ('【生产环境写操作被拦截】当前处于只读模式，不允许执行「%s」。'
               '如需在生产环境执行写操作，请确认风险后设置 '
               'UI_SAFETY_ALLOW_WRITE=true（不推荐）。' % operation)
        if detail:
            msg = '%s 详情：%s' % (msg, detail)
        super(ProductionWriteBlocked, self).__init__(msg)


def _url_is_production(url):
    """检查 URL 是否命中生产环境特征。

    :param url: 被测地址
    :return: True 表示命中生产特征
    """
    if not url:
        return False
    patterns_raw = Config.get('safety', 'prod_url_patterns', '')
    if not patterns_raw:
        return False
    patterns = [p.strip() for p in re.split(r'[,;\n]+', patterns_raw) if p.strip()]
    for pat in patterns:
        try:
            if re.search(pat, url, re.IGNORECASE):
                return True
        except re.error:
            log1.error('[safety] prod_url_patterns 里有非法正则：%s，已跳过该项' % pat)
    return False


def detect_environment(env_arg=None, url=None):
    """检测当前目标环境类型，返回 'prod' 或 'test'。

    :param env_arg: 命令行 --env 的值（优先）
    :param url:     被测地址（env_arg 未指定时按 URL 特征匹配）
    :return: 'prod' 或 'test'

    判定优先级：
        1. 环境变量 UI_ENV_KIND（最高，CI 可强制指定）
        2. 命令行 --env 参数
        3. URL 命中生产正则
        4. 默认 'test'
    """
    # 1. 环境变量强制指定
    env_kind = os.environ.get('UI_ENV_KIND', '').strip().lower()
    if env_kind:
        return 'prod' if env_kind in _PROD_ENV_VALUES else 'test'

    # 2. 命令行参数
    if env_arg:
        val = str(env_arg).strip().lower()
        if val in _PROD_ENV_VALUES:
            return 'prod'
        if val in _TEST_ENV_VALUES:
            return 'test'

    # 3. URL 特征匹配
    if url is None:
        url = Config.get('environment', 'url', '')
    if _url_is_production(url):
        return 'prod'

    return 'test'


def activate(env_arg=None, url=None):
    """激活环境防护：检测环境类型，生产环境自动注入只读模式。

    在 run.py 启动时调用一次即可。幂等：重复调用不会重复注入。

    :return: dict {'env_kind': 'prod'|'test', 'readonly': bool}
    """
    global _is_production, _mode

    enabled = Config.get_bool('safety', 'enabled', True)
    if not enabled:
        log1.info('环境安全防护已关闭（config.ini [safety] enabled=false）')
        _is_production = False
        _mode = 'normal'
        return {'env_kind': 'test', 'readonly': False}

    env_kind = detect_environment(env_arg, url)
    _is_production = (env_kind == 'prod')

    if _is_production:
        # 生产环境：注入只读模式环境变量，让全链路感知
        os.environ['UI_TESTCASE_MODE'] = 'readonly'
        os.environ['UI_SAFETY_ALLOW_WRITE'] = 'false'
        _mode = 'readonly'
        log1.warning('=' * 60)
        log1.warning('⚠️  检测到目标环境为【生产环境】，已自动切换到【只读模式】')
        log1.warning('    所有写操作（上传/提交/删除）将被拦截，冒烟测试不受影响。')
        log1.warning('    如需关闭此防护：config.ini [safety] enabled=false')
        log1.warning('=' * 60)
    else:
        # 测试环境：确保没有遗留的只读标记
        os.environ.pop('UI_TESTCASE_MODE', None)
        allow = Config.get_bool('safety', 'allow_write_on_prod', False)
        if not _is_production:
            os.environ['UI_SAFETY_ALLOW_WRITE'] = 'true'
        _mode = 'normal'
        log1.info('环境安全防护：目标环境为【测试环境】，写操作正常放行')

    return {'env_kind': env_kind, 'readonly': _is_production}


def is_production():
    """当前是否处于生产环境（只读模式）。"""
    if _is_production is None:
        # 未显式激活时，按 URL 特征即时判断（给未走 run.py 的调用方兜底）
        return detect_environment() == 'prod'
    return _is_production


def allow_write(operation=''):
    """检查当前是否允许写操作。

    :param operation: 操作名称（用于报错信息），如 '上传文件' / '提交表单'
    :return: True 允许写；False 不允许
    :raise ProductionWriteBlocked: 不允许时直接抛出（调用方可选择不捕获，让用例报错）

    用法（在 BasePage 的写操作里）：
        def files_upload(self, ...):
            envGuard.allow_write('上传文件')   # 不允许会直接抛异常
            ...
    """
    # 显式开启了"生产也允许写"（极度不推荐，但尊重配置）
    if os.environ.get('UI_SAFETY_ALLOW_WRITE', '').lower() == 'true':
        return True
    if is_production():
        raise ProductionWriteBlocked(operation or '未知写操作')
    return True


def current_mode():
    """返回当前模式：'readonly'（生产只读）或 'normal'（正常）。"""
    return _mode or ('readonly' if is_production() else 'normal')
