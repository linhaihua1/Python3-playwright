# -*- coding: utf-8 -*-
"""
==========================================================================
 路径配置中心（新人阅读顺序：第 11 个）
==========================================================================

【一句话说明】
    工程里所有目录路径的唯一定义处。别的模块要路径就从这里 import，
    不要在业务代码里手写 'Report/xxx' 这种字符串。

【在框架中的位置】
    被 Common/runner.py（要 testcases_dir / htmlreport_dir / ensure_dir）、
       Common/sentMail.py（要 htmlreport_dir）导入。
    本文件不依赖任何其他工程模块，可放心阅读。

【目录对照（工程实际结构）】
    base_dir/                 工程根目录，例：C:\\Users\\xxx\\desktop\\Python3-playwright
      ├─ Common/              公共层：基类、元素表、配置、日志、报告器、执行器、CI 适配
      ├─ PageObject/          页面对象层：按页面封装操作
      ├─ TestSuites/          用例层：test*.py 才会被收集
      ├─ Logs/日期/           运行日志（All_Logs 全量 / Error_Logs 仅错误）
      ├─ img/日期/            失败截图
      ├─ Report/              HTML 测试报告 + JUnit XML 结果（CI 归档这一整个目录）
      ├─ docs/                使用文档与 CI/CD 接入指南
      ├─ config.ini           环境、账号、报告、邮件、CI 配置
      └─ Jenkinsfile          Jenkins 流水线定义

【目录名来自配置，不再写死】
    产出目录（用例目录 / 报告目录 / 截图目录 / JUnit 文件名）的**名字**
    全部读 config.ini -> [paths] 段，取不到才用代码里的默认值。
    工程根 base_dir 由本文件位置决定（不可配，否则找不到 config.ini）。

【新人注意】
    - 需要"目录不存在就创建"时，统一用本文件的 ensure_dir()。
    - 新增目录时先在这里加常量，再去引用，保持路径单一来源。
    - 日志目录 Logs 的"位置"由环境变量 UI_LOG_DIR 单独控制（见 Common/log.py，
      它的读取时机早于 config.ini，所以只能走环境变量，本文件不重复管它）。
==========================================================================
"""

import os

from Common.config import Config

# ---------------------------------------------------------------------------
# 工程顶层目录：本文件位于 Common/ 下，所以先取 Common/ 再往上取一层
#   os.path.abspath(__file__)            -> .../工程根/Common/dir_config.py
#   os.path.split(...)[0]                -> .../工程根/Common
#   os.path.split(...)[0] 再来一次        -> .../工程根
# ---------------------------------------------------------------------------
base_dir = os.path.split(os.path.split(os.path.abspath(__file__))[0])[0]

# 测试用例目录：Common/runner.py 在该目录下按 pattern 收集用例。
# 目录名可配：[paths] testcases_dir，默认 TestSuites
testcases_dir = os.path.join(base_dir, Config.get('paths', 'testcases_dir', 'TestSuites'))

# 测试报告目录：runner 生成报告、sentMail 找最新报告都用它。
htmlreport_dir = os.path.join(base_dir, Config.get('paths', 'report_dir', 'Report'))

# 失败截图根目录：basePage.get_img 拼 <img_dir>/日期/xxx.png。
img_dir = os.path.join(base_dir, Config.get('paths', 'img_dir', 'img'))

# JUnit XML 结果文件：CI 流水线（Jenkins 的 junit 插件）读取它判定构建是否通过。
# 本机运行时也会生成，方便随时用工具解析结果，见 Common/junitReport.py。
junitreport_file = os.path.join(htmlreport_dir,
                                 Config.get('paths', 'junit_filename', 'junit-results.xml'))


def ensure_dir(path):
    """确保目录存在；不存在则创建，最后把它返回，方便链式调用。

    用法：
        report_path = os.path.join(ensure_dir(htmlreport_dir), '报告.html')
    """
    if not os.path.exists(path):
        os.makedirs(path)
    return path
