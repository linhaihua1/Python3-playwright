# -*- coding: utf-8 -*-
"""
 ==========================================================================
  备用入口：报告主题走 config.ini [entrance]（新人阅读顺序：第 13 个）
 ==========================================================================

 【一句话说明】
     和 run.py 做的是同一件事，只是报告标题/描述改用 [entrance] 段。
     历史上这是给另一个被测系统（党建云）留的入口；现在标题不再写死在代码里，
     换主题只改 config.ini -> [entrance] title / description。

 【在框架中的位置】
     entrance.py  ──>  Common/runner.py  ──>  TestSuites/*.py
     （和 run.py 完全平行，区别只在报告标题取自 [entrance] 而非 [report]）

 【怎么用】
     python entrance.py                    # 全量
     python entrance.py smoke              # 冒烟（位置参数，可选）

 【配置（config.ini -> [entrance]）】
     title        报告标题（必填，也是报告文件名；缺失直接报错指明该配哪个键）
     description  报告描述（可选，留空则不写）

 【新人注意】
     1. 本入口同样遵循退出码约定（0 通过 / 1 用例失败 / 2 环境异常），
        所以也能被流水线直接调用；但流水线推荐用 run.py —— 它的参数更完整。
     2. 如果只想保留一个入口，可以只在 run.py 里用 --title/--filename 传参，
        本文件并非必需。
 ==========================================================================
"""

import os
import sys

# 同 run.py：保证从任意工作目录执行都能 import 到工程内的包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 同 run.py：兜住 locale 为 C 的环境（Linux 容器），避免中文输出抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

from Common import runner
from Common.config import Config
from Common.log import log1

if __name__ == '__main__':
    # 支持 python entrance.py smoke 走冒烟子集，不传则跑全量
    suite_kind = sys.argv[1] if len(sys.argv) > 1 else 'full'
    log1.info('===== UI 自动化测试开始（entrance.py，模式=%s）=====' % suite_kind)

    # 报告标题属于业务数据：只从 [entrance] 读，缺失即报错（代码零硬编码）。
    entrance_title = Config.require('entrance', 'title',
                                    '本备用入口的报告标题（同时用作报告文件名）')
    entrance_description = Config.get('entrance', 'description', '')

    summary = runner.run(
        title=entrance_title,
        report_name=entrance_title + '.html',
        description=entrance_description,
        suite_kind=suite_kind,
    )

    log1.info('执行结束，退出码 %d' % summary['exit_code'])
    # 与 run.py 保持一致的退出码语义，便于流水线统一处理
    sys.exit(summary['exit_code'])
