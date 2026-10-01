# -*- coding: utf-8 -*-
"""
==========================================================================
 日志模块（新人阅读顺序：第 10 个）
==========================================================================

【一句话说明】
    全工程统一用这里的 log1 打日志。日志同时写三处：文件(全量) + 文件(仅错误) + 控制台。

【在框架中的位置】
    几乎所有模块都会 `from Common.log import log1` 然后 log1.info(...) / log1.error(...)。
    本模块只依赖 getcwd，不依赖其他工程模块，可以放心先读。

【产出位置与看日志的顺序】
    Logs/2026-09-25/
      ├─ All_Logs/2026-09-25 120718.log    所有级别日志，按运行时刻命名
      └─ Error_Logs/2026-09-25 120718.log  只有 ERROR，且带模块名+行号，排错优先看它

    一次运行 = 一组同名的 All_Logs / Error_Logs 文件（时间戳一致，便于对应）。
    注意时间戳格式是 '2026-09-25 120718'（时分秒之间没有冒号，因为冒号不能做文件名）。

【日志目录可以挪走】
    默认写在 <工程根>/Logs 下。若需要换地方（CI 想把日志收进构建产物目录、
    单元测试不想污染工作区），设环境变量 UI_LOG_DIR 指向目标目录即可：

        UI_LOG_DIR=/var/log/ui-auto  python run.py

    注意：一个进程里第一次 import 本模块时就会创建日志文件，所以要在
    **启动进程之前**设好环境变量，import 之后再改是无效的。
    另外这个变量必须叫 UI_LOG_DIR（不能塞进 config.ini），因为它生效的时机
    早于 config.ini 被读取。

【新人注意】
    1. 打日志别用 print，不然控制台能看到、日志文件里没有，事后无法回溯。
    2. logging 不支持 log1.info('结果:', value) 这种多参数写法，
       必须自己拼好字符串：log1.info('结果: %s' % value)。
    3. 想知道某条 ERROR 出自哪一行？直接看 Error_Logs 里的 "模块名 - 行号"。
==========================================================================
"""

import logging
import os
import time

import getcwd

# 已经初始化过的 logger 名字集合。
# 作用：防止同一个 logger 被反复添加 handler，否则一条日志会重复打印 N 遍。
_INITIALIZED = set()

# 全量日志格式：时间 - 日志器名 - 级别 - 内容
ALL_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
# 错误日志格式：多出「模块名」和「行号」，方便直接跳到出错代码
ERROR_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(module)s - %(lineno)s - %(message)s'


def _log_root(project_path):
    """计算日志根目录。

    :param project_path: 工程根目录（getcwd.get_cwd() 的返回值）
    :return: 日志根目录绝对路径；默认是 <工程根>/Logs

    环境变量 UI_LOG_DIR 可整体挪走日志目录，见模块头「日志目录可以挪走」。
    """
    override = os.environ.get('UI_LOG_DIR')
    if override:
        return os.path.abspath(override)
    return os.path.join(project_path, 'Logs')


def get_log(logger_name, console=True, level=logging.INFO):
    """创建（或取出已建好的）logger。

    :param logger_name: 日志器名称，会显示在每条日志里。本工程统一用 'uiauto'
    :param console:     是否同时输出到控制台，默认 True
    :param level:       最低记录级别，默认 INFO
    :return: logging.Logger
    """
    logger = logging.getLogger(logger_name)

    # 已经初始化过就直接返回，避免重复加 handler 导致日志翻倍
    if logger_name in _INITIALIZED:
        return logger

    # ---- 1. 准备目录：<日志根>/日期/All_Logs 与 <日志根>/日期/Error_Logs ----
    project_path = getcwd.get_cwd()
    local_date = time.strftime('%Y-%m-%d', time.localtime(time.time()))
    date_file_path = os.path.join(_log_root(project_path), local_date)
    all_log_path = os.path.join(date_file_path, 'All_Logs')
    error_log_path = os.path.join(date_file_path, 'Error_Logs')
    for path in (all_log_path, error_log_path):
        if not os.path.exists(path):
            os.makedirs(path)

    # ---- 2. 本次运行的文件名（同一分钟多次运行会覆盖，属正常现象）----
    local_time = time.strftime('%Y-%m-%d %H%M%S', time.localtime(time.time()))
    all_log_name = os.path.join(all_log_path, local_time + '.log')
    error_log_name = os.path.join(error_log_path, local_time + '.log')

    logger.setLevel(level)
    # propagate=False：不把日志再往父 logger 传，避免根 logger 再输出一遍造成重复
    logger.propagate = False

    all_log_formatter = logging.Formatter(ALL_LOG_FORMAT)
    error_log_formatter = logging.Formatter(ERROR_LOG_FORMAT)

    # ---- 3. handler 一：全量文件日志 ----
    # encoding='utf-8' 必须写，否则中文日志在 Windows 下会乱码或直接报编码错
    fh = logging.FileHandler(all_log_name, encoding='utf-8')
    fh.setLevel(level)
    fh.setFormatter(all_log_formatter)
    logger.addHandler(fh)

    # ---- 4. handler 二：错误文件日志（级别设成 ERROR，INFO 不会写进来）----
    eh = logging.FileHandler(error_log_name, encoding='utf-8')
    eh.setLevel(logging.ERROR)
    eh.setFormatter(error_log_formatter)
    logger.addHandler(eh)

    # ---- 5. handler 三：控制台输出（可选）----
    if console:
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(all_log_formatter)
        logger.addHandler(ch)

    _INITIALIZED.add(logger_name)
    return logger


# ---------------------------------------------------------------------------
# 模块级单例：全工程 import 这一个对象就够了
#     from Common.log import log1
#     log1.info('消息')
#     log1.error('出错了', exc_info=1)   # exc_info=1 会带上完整异常堆栈，排错必加
# ---------------------------------------------------------------------------
log1 = get_log('uiauto')
