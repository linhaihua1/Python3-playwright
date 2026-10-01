# -*- coding: utf-8 -*-
"""
==========================================================================
 程序主入口（新人阅读顺序：第 1 个）
==========================================================================

【一句话说明】
    启动整个工程的 UI 自动化测试，跑完生成中文 HTML 报告 + JUnit XML 结果，
    并以「退出码」告诉调用方这次测试是通过还是失败。本工程共有两个入口，
    这个是主入口（本机执行和 Jenkins 流水线用的都是它）。

【在框架中的位置】
    命令行 / Jenkins 流水线
      └─> run.py                    <- 本文件。只做两件事：
      │                                ① 解析参数 ② 把参数翻译成配置覆盖
            └─> Common/runner.py    <- 真正干活的地方：收集用例 -> 执行 -> 出报告
                  └─> TestSuites/*.py   <- 被执行的测试用例

【怎么用】
    # ---- 本机开发 ----
    python run.py                                # 用 config.ini 里的配置（有界面，能看到浏览器操作）
    python run.py --headless                     # 无头运行，和流水线上行为一致
    python run.py --suite smoke                  # 只跑冒烟用例（不产生数据副作用）
    python run.py --title 冒烟测试 --filename smoke.html
    python run.py --pattern 'test_Login*.py'     # 只跑文件名匹配的用例
    python run.py --url http://测试环境/sso-portal/#/?redirect=/launch   # 临时切环境
    python run.py --mail                         # 跑完把报告当附件发邮件

    # ---- Jenkins 里 ----
    python run.py --suite smoke --headless --junit Report/junit-results.xml

【退出码（CI 判定构建成败的依据）】
    0 = 全部通过     1 = 有用例失败     2 = 环境/配置问题
    流水线的 junit 插件负责展示用例明细，退出码负责把构建标红，两者缺一不可。

【新人注意】
    1. 首次运行前先装依赖：pip install -r requirements.txt
    2. config.ini 里默认填的是**生产环境**地址，跑全量用例会真实上传视频、
       真实提交报送单，跑之前务必确认环境与数据影响！
    3. 本文件刻意保持"薄"：想改测试逻辑请去 TestSuites/ 或 Common/runner.py，
       不要把业务代码堆到这里。
    4. 参数是怎么生效的？全部转成 UI_ 开头的环境变量，再由 Common/config.py
       统一覆盖配置 —— 全工程只有"环境变量 > config.ini > 代码默认值"一条优先级链路。
    5. 常用定位手段：控制台输出的「失败摘要」、Logs/日期/Error_Logs/、img/日期/ 的失败截图。
==========================================================================
"""

import argparse
import os
import sys

# 把工程根目录塞进 sys.path，这样无论从哪个目录执行 python run.py，
# 都能 import 到 Common / PageObject / TestSuites 这些包。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 保证中文输出在任何终端/CI 环境下都不会把构建搞挂。
# 场景：Linux 容器里若 locale 是 C/POSIX，stdout 编码会退化成 ascii，
# 此时 print 中文（报告结论、失败摘要）会抛 UnicodeEncodeError。
# errors='replace' 兜底：万一还有无法编码的字符，最多显示成 ?，不会中断测试。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):   # 老版本 Python 或被重定向的流
        pass

from Common import runner
from Common.log import log1


def parse_args(argv=None):
    """定义并解析命令行参数。

    所有参数都是可选的；不传时由 runner.run() 回退到 config.ini 的配置。
    返回 argparse.Namespace，形如 args.title / args.filename / args.mail ...
    """
    parser = argparse.ArgumentParser(
        description='融媒体生产系统 UI 自动化测试（支持 CI/CD 流水线调用）'
    )

    # ---- 报告元信息 ----
    parser.add_argument('--title', help='报告标题')
    parser.add_argument('--filename', help='报告文件名，如 冒烟测试.html')
    parser.add_argument('--tester', help='执行人（Jenkins 上建议传构建号，便于追溯）')
    parser.add_argument('--description', help='报告描述，不传则自动拼上构建信息')

    # ---- 用例选择 ----
    parser.add_argument('--pattern', default=None,
                        help='用例文件匹配规则（默认取 config.ini [paths] case_pattern，缺省 test*.py）')
    parser.add_argument('--suite', default='full', choices=('full', 'smoke'),
                        help='full=全部用例；smoke=只跑 [ci] smoke_cases 配置的冒烟用例')
    parser.add_argument('--cases',
                        help='按用例方法名过滤，逗号分隔，优先级高于 --suite，'
                             '如 test_1login,test_2select_scxt')

    # ---- 运行环境 ----
    # headless 用互斥组：不传时为 None（沿用 config.ini 配置），
    # 传了则强制覆盖，便于本机调试与流水线用同一份代码
    parser.set_defaults(headless=None)
    headless_group = parser.add_mutually_exclusive_group()
    headless_group.add_argument('--headless', dest='headless', action='store_true',
                                help='无头模式运行（流水线必须开启）')
    headless_group.add_argument('--no-headless', dest='headless', action='store_false',
                                help='强制有界面运行（本机调试用）')
    parser.add_argument('--browser', choices=('chrome', 'firefox', 'edge'),
                        help='浏览器类型，覆盖 config.ini [environment] browser')
    parser.add_argument('--url', help='被测地址，覆盖 config.ini [environment] url')

    # ---- 多系统切换 ----
    # 一套框架管理多个被测系统：在 config.ini 里为每个系统建一个 [system_名称] 段，
    # 填 url / username / password / pattern 等，用 --system 名称 一键切换。
    parser.add_argument('--system',
                        help='切换被测系统：读 config.ini 的 [system_名称] 段，'
                             '自动应用该系统的地址/账号/用例集。配合 --pattern 可再覆盖用例集')

    # ---- 结果输出 ----
    parser.add_argument('--junit',
                        help='JUnit XML 输出路径；传 none 可关闭（默认取 [ci] junit_xml）')
    parser.add_argument('--mail', action='store_true', help='运行结束后发送邮件')
    parser.add_argument('--notify', action='store_true',
                        help='运行结束后把结果推送到群机器人（钉钉/企业微信，配置见 [notify]）')
    return parser.parse_args(argv)


def apply_runtime_overrides(args):
    """把命令行参数翻译成 UI_ 开头的环境变量，交给 config 的覆盖机制生效。

    为什么走环境变量而不是直接改 Config：
        工程里"配置从哪来"只有一条链路 ——
            环境变量  >  config.ini  >  代码默认值
        命令行只是这条链路的又一个入口。这样新增参数时不用维护第二套优先级逻辑，
        也保证了「本机加参数跑」和「Jenkins 上设环境变量跑」的结果完全一致。

    :return: 本次生效的覆盖项列表（用于日志展示）
    """
    applied = []

    # 无头开关：对应 UI_CI_HEADLESS -> config.ini [ci] headless
    if args.headless is not None:
        os.environ['UI_CI_HEADLESS'] = 'true' if args.headless else 'false'
        applied.append('headless=%s' % args.headless)

    # 浏览器与地址：对应 UI_ENVIRONMENT_BROWSER / UI_ENVIRONMENT_URL
    if args.browser:
        os.environ['UI_ENVIRONMENT_BROWSER'] = args.browser
        applied.append('browser=%s' % args.browser)
    if args.url:
        os.environ['UI_ENVIRONMENT_URL'] = args.url
        applied.append('url=%s' % args.url)

    if applied:
        log1.info('命令行覆盖配置：%s' % ', '.join(applied))
    return applied


def apply_system(system_name):
    """按 --system 切换被测系统：把 [system_名称] 段翻译成环境变量覆盖。

    支持的键（任一可选，缺了就不覆盖、沿用全局配置）：
        url       -> UI_ENVIRONMENT_URL
        browser   -> UI_ENVIRONMENT_BROWSER
        username  -> UI_TESTCASE_LOGIN_USERNAME
        password  -> UI_TESTCASE_LOGIN_PASSWORD
        pattern   -> 返回给调用方，用于选定该系统的用例集
        headless  -> UI_CI_HEADLESS

    :return: 该系统配置的 pattern（字符串或 None），供 run() 选用例集
    """
    if not system_name:
        return None
    from Common.config import Config
    section = 'system_%s' % system_name
    if not Config.has_section(section):
        raise SystemExit('未找到被测系统配置 [system_%s]，请在 config.ini 中添加该段' % system_name)

    mapping = {
        'url': 'UI_ENVIRONMENT_URL',
        'browser': 'UI_ENVIRONMENT_BROWSER',
        'username': 'UI_TESTCASE_LOGIN_USERNAME',
        'password': 'UI_TESTCASE_LOGIN_PASSWORD',
        'headless': 'UI_CI_HEADLESS',
    }
    applied = []
    for key, env in mapping.items():
        val = Config.get(section, key, '')
        if val:
            os.environ[env] = val
            applied.append('%s=***' % key if key == 'password' else '%s=%s' % (key, val))
    pattern = Config.get(section, 'pattern', '') or None
    if applied:
        log1.info('已切换到被测系统 [%s]，应用配置：%s' % (system_name, ', '.join(applied)))
    return pattern


def print_conclusion(summary):
    """打印执行结论块，让流水线日志一眼就能看清结果。

    内容与退出码一致：通过就写 PASSED，不通过就写 FAILED 并给出退出码含义。
    """
    code = summary['exit_code']
    if code == runner.EXIT_OK:
        verdict = '通过（PASSED）'
    elif code == runner.EXIT_TEST_FAILED:
        verdict = '失败（FAILED）—— 有用例未通过，请查看报告与失败截图'
    else:
        verdict = '异常（ABORTED）—— 环境或配置问题，测试未真正执行'

    print('')
    print('====================== 执行结论 ======================')
    print('结果      : %s' % verdict)
    print('用例      : %d 条（失败 %d / 错误 %d / 跳过 %d）'
          % (summary['tests_run'], summary['failures'], summary['errors'], summary['skipped']))
    print('耗时      : %.1f 秒' % summary['duration'])
    print('HTML 报告 : %s' % (summary['report'] or '未生成'))
    print('JUnit XML : %s' % (summary['junit'] or '未生成'))
    print('退出码    : %d' % code)
    print('=====================================================')
    print('')


# ---------------------------------------------------------------------------
# 只有「直接运行本文件」时才执行下面的代码。
# 被别的模块 import 时不会触发，避免误跑测试。
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    args = parse_args()
    log1.info('===== UI 自动化测试开始 =====')

    # 1) 先按 --system 切换被测系统（会把 [system_名称] 翻译成环境变量覆盖，
    #    并可能给出该系统专属的用例 pattern）
    system_pattern = apply_system(args.system)
    # 用例集优先级：命令行 --pattern > 系统配置 pattern > config [paths] case_pattern
    # （前两者都没给，就传 None 给 runner，由 runner 走配置解析）
    pattern = args.pattern or system_pattern

    # 2) 命令行参数 -> 环境变量 -> config 覆盖（在 system 之后，保证命令行优先级最高）
    apply_runtime_overrides(args)

    # 3) 执行测试。runner.run() 返回汇总字典，例如：
    # {'report': '...报告.html', 'junit': '...junit-results.xml',
    #  'tests_run': 6, 'failures': 0, 'errors': 0, 'exit_code': 0, ...}
    summary = runner.run(
        pattern=pattern,
        title=args.title,
        report_name=args.filename,
        tester=args.tester,
        description=args.description,
        suite_kind=args.suite,
        cases=args.cases,
        junit=args.junit,
        send_mail=args.mail,
    )

    print_conclusion(summary)

    # 4) 可选：把结果推送到群机器人（钉钉/企业微信）
    if args.notify:
        from Common.notifier import notify as _notify
        _notify(summary)

    # 把退出码交给操作系统/Jenkins。
    sys.exit(summary['exit_code'])
