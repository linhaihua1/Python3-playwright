# -*- coding: utf-8 -*-
"""
==========================================================================
 测试执行器（新人阅读顺序：第 2 个）
==========================================================================

【一句话说明】
    整个测试流程的"发动机"：收集用例 -> 逐条执行 -> 生成 HTML 报告 + JUnit XML
    -> （可选）发邮件。run.py 和 entrance.py 这两个入口最终都是调它。

【在框架中的位置】
    run.py / entrance.py          （入口，只传参）
        └─> Common/runner.py      <- 本文件
              ├─> unittest 的 discover        去 TestSuites/ 找 test*.py
              ├─> TestSuites/testLogin.py     执行用例（内部再用 PageObject）
              ├─> Common/HTMLTestReportCN.py  把结果渲染成中文 HTML 报告（给人看）
              ├─> Common/junitReport.py       把结果写成 JUnit XML（给流水线看）
              └─> Common/sentMail.py          报告发给收件人（send_mail=True 时）

【执行一次测试的完整时序】
    1. build_suite()   扫描 TestSuites/，把所有 TestCase 装进 TestSuite
                        （跑冒烟时再按关键字过滤一遍）
    2. 打开 Report/xxx.html 文件句柄
    3. HTMLTestRunner.run(suite) —— 触发每条用例的 setUpClass/用例/tearDownClass
       注意：这里用的是自定义结果类 JUnitResult，所以**一次执行同时产出两种报告**
    4. 关闭文件，HTML 报告落盘；再把结果写成 JUnit XML
    5. 打印失败摘要，返回汇总字典（含退出码）给调用方

【退出码约定（CI 靠它判定构建成败）】
    0  全部通过
    1  有用例失败或出错          -> 流水线标记为「不稳定/失败」
    2  环境或配置问题（没收集到用例、地址没配、浏览器起不来）
                                -> 流水线标记为「失败」，需要人工介入

【新人注意 —— 最容易踩的坑】
    discover() 必须传 top_level_dir=工程根目录。
    因为 unittest 默认会把 start_dir 插到 sys.path 最前面，
    那样 TestSuites 里的 `from Common.xxx import ...` 会找不到 Common 包而报错。
    另外 discover 只认「包」：TestSuites/ 下必须有 __init__.py。

【收集不到用例怎么办（高频问题）】
    build_suite() 现在**不向外抛异常**：收集失败时返回空 suite，
    并把原因翻译成中文写进日志（见 diagnose_collection）。
    排查顺序：先看日志里的「排查结论」，再看 docs/使用文档.md 的 7.1 节，
    那里按「崩栈 / 静默为 0 / 只有 1 条占位 / 执行期失败」四类根因列了完整对照表。
==========================================================================
"""

import fnmatch
import os
import re
import time
import unittest

from Common import ci as ci_env
from Common.config import Config, ConfigMissingError
# 注意：htmlreport_dir / junitreport_file 刻意**不从 dir_config 顶层导入**。
# 这两个路径会被 runContext.init() 按 runId 改写（产物隔离），
# 而 from-import 在本模块 import 时就把旧值复制进了命名空间，
# 之后 dir_config 再怎么改都影响不到这里 —— 隔离会被静默架空。
# 必须以 dir_config.<属性> 的方式在**使用时**取值。
from Common import dir_config as _dir_config
from Common.dir_config import base_dir, ensure_dir, testcases_dir
from Common.HTMLTestReportCN import HTMLTestRunner
from Common.junitReport import JUnitResult, write_junit_xml
from Common.log import log1

# 报告元信息（标题/文件名/执行人/描述）没有任何代码兜底值：
# 它们属于业务数据，一律来自 config.ini -> [report]（可被环境变量覆盖），
# 缺项时 Config.require 直接报错并指明该配哪个键 —— 零硬编码纪律。

# 用例文件收集规则的代码默认值（技术参数）。配置键 [paths] case_pattern。
DEFAULT_PATTERN = 'test*.py'

# ---------------------------------------------------------------------------
# 退出码：CI 流水线就是靠这三个数字判断这次构建是好是坏，不要随意改动
# ---------------------------------------------------------------------------
EXIT_OK = 0             # 全部通过
EXIT_TEST_FAILED = 1    # 用例失败/出错
EXIT_ENV_ERROR = 2      # 环境或配置问题（未收集到用例、地址缺失、浏览器起不来等）

# 用例过滤关键字的分隔符：允许写 "a,b" 或 "a b" 或换行分隔
_FILTER_SPLIT = re.compile(r'[,\s;]+')

# 合法的 Python 模块名（与 unittest 内部 _find_tests 的判断规则保持一致）。
# 文件名不满足它就**不会报错**，而是被 discover 静默跳过 —— 这是最容易误判成
# "收集不到用例"的场景之一（例如 test-login.py / 测试登录.py / test login.py）。
_VALID_MODULE_NAME = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def _split_keywords(raw):
    """把 'a,b c' 这类字符串拆成关键字列表；空值返回 []。"""
    if not raw:
        return []
    if not isinstance(raw, str):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [item for item in _FILTER_SPLIT.split(raw) if item]


def _iter_cases(suite):
    """递归展开 TestSuite，逐个产出 TestCase（不执行，只遍历）。"""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            # yield from 递归展开嵌套 suite（discover 会按目录层级嵌套）
            yield from _iter_cases(item)
        else:
            yield item


def filter_suite(suite, keywords):
    """按用例方法名过滤，只保留匹配的用例。

    :param suite:    原始 TestSuite
    :param keywords: 关键字列表，命中任意一个即保留（大小写不敏感、部分匹配）
    :return: 新的 TestSuite（过滤后）

    用途：冒烟测试。见 config.ini -> [ci] smoke_cases。
    ⚠️ 本工程的 6 条用例是一条**串行业务流**（上传依赖已登录、报送依赖已上传），
       所以过滤后的子集必须是"前缀"，例如只留 test_1login / test_2... / test_3...。
       随便挑中间几条跑会因为没有前置状态而连环失败。
    """
    keywords = _split_keywords(keywords)
    all_cases = list(_iter_cases(suite))
    if not keywords:
        return suite

    lowered = [k.lower() for k in keywords]
    kept = [case for case in all_cases if any(k in case.id().lower() for k in lowered)]
    log1.info('用例过滤：关键字=%s，命中 %d/%d 条' % (keywords, len(kept), len(all_cases)))
    return unittest.TestSuite(kept)


def resolve_case_filter(suite_kind='full', cases=None):
    """决定这次要跑哪些用例。

    :param suite_kind: 'full' 跑全部；'smoke' 只跑 config.ini [ci] smoke_cases 里的
    :param cases:      显式指定的关键字（优先级最高），可传字符串或列表
    :return: 关键字列表；空列表表示不过滤

    优先级：cases 参数 > suite_kind=smoke > 全量
    """
    explicit = _split_keywords(cases)
    if explicit:
        log1.info('按命令行指定的用例过滤：%s' % explicit)
        return explicit
    if str(suite_kind).lower() == 'smoke':
        # 冒烟集来自配置，方便不同项目/环境各自定义，改配置即可不用动代码
        smoke = _split_keywords(Config.get('ci', 'smoke_cases', ''))
        log1.info('冒烟测试模式，用例过滤关键字：%s' % smoke)
        return smoke
    return []


# ---------------------------------------------------------------------------
# 用例收集的「体检」工具
#   设计目的：unittest 的 discover 在多数失败场景下是**静默**的 ——
#   要么一条都不收集却不报错，要么抛一个只有英文的 ImportError。
#   下面这些函数把"为什么收集不到"直接翻译成中文提示写进日志，
#   避免排查时只能靠猜。
# ---------------------------------------------------------------------------
def diagnose_collection(tests_dir=None, pattern='test*.py'):
    """体检用例目录，返回一组可读的诊断结论（列表，每项一行）。

    覆盖场景：
      - 目录不存在 / 目录里没有 .py 文件
      - 缺 __init__.py（discover 会直接报 "not importable"）
      - 文件名不是合法模块名（含连字符/空格/中文/多个点）-> 被静默跳过
      - 大小写差异：Windows 收得到、Linux（Jenkins）收不到
      - 子目录缺 __init__.py -> 整棵子树被静默跳过
    """
    tests_dir = tests_dir or testcases_dir
    lines = []

    if not os.path.isdir(tests_dir):
        lines.append('用例目录不存在：%s' % tests_dir)
        lines.append('  -> 检查 Common/dir_config.py 里 testcases_dir 的推导结果')
        return lines

    init_file = os.path.join(tests_dir, '__init__.py')
    if os.path.isfile(init_file):
        lines.append('包标识文件 __init__.py：存在')
    else:
        lines.append('包标识文件 __init__.py：【缺失】')
        lines.append('  -> 缺它不是"少收几条用例"，而是直接报 '
                     '"Start directory is not importable"，一条都收不到')

    py_files = sorted(f for f in os.listdir(tests_dir)
                      if f.endswith('.py') and os.path.isfile(os.path.join(tests_dir, f)))
    lines.append('目录下 .py 文件共 %d 个：%s' % (len(py_files), '、'.join(py_files) or '（无）'))
    if not py_files:
        lines.append('  -> 目录里没有 .py 文件，自然收集不到用例')

    # pattern 匹配：分别按「本机行为」与「Linux 行为」各算一遍，差异本身就是隐患
    local_hits = [f for f in py_files if fnmatch.fnmatch(f, pattern)]
    linux_hits = [f for f in py_files if fnmatch.fnmatchcase(f, pattern)]
    lines.append('按 pattern=%s 匹配到：%s' % (pattern, '、'.join(local_hits) or '（无）'))
    if not local_hits:
        lines.append('  -> pattern 没匹配到任何文件。默认规则是"文件名以 test 开头"，'
                     '例如 loginTest.py 收不到，需要改名为 testLogin.py')
    if set(local_hits) != set(linux_hits):
        lines.append('大小写隐患：本机匹配 %s，Linux（Jenkins 节点）只匹配 %s'
                     % (local_hits, linux_hits or ['无']))
        lines.append('  -> Windows 的文件匹配不区分大小写，Linux 区分。'
                     '用例文件请统一用小写 test 开头，避免"本机能跑、流水线收集不到"')

    # 非法模块名：discover 静默跳过且不报错，是最难查的一类
    bad_names = [f for f in local_hits
                 if not _VALID_MODULE_NAME.match(os.path.splitext(f)[0])]
    if bad_names:
        lines.append('文件名非法（会被 discover 静默跳过且不报错）：%s' % '、'.join(bad_names))
        lines.append('  -> 文件名只能由字母/数字/下划线组成且不能以数字开头，'
                     '不能含连字符、空格、中文、多余的点；例如 test-login.py 应改为 test_login.py')

    # 子目录：unittest 只把「包」当子目录递归，缺 __init__.py 就整棵子树跳过
    for entry in sorted(os.listdir(tests_dir)):
        sub = os.path.join(tests_dir, entry)
        if os.path.isdir(sub) and not entry.startswith('__'):
            has_init = os.path.isfile(os.path.join(sub, '__init__.py'))
            lines.append('子目录 %s/ ：__init__.py %s'
                         % (entry, '存在' if has_init else '【缺失】'))
            if not has_init:
                lines.append('  -> 该子目录下的用例会被整棵跳过，补一个空的 __init__.py 即可')
    return lines


def find_broken_modules(suite):
    """找出「模块导入失败」的占位用例，返回 [(用例, 异常), ...]。

    背景：当用例文件 import 报错（漏装依赖、模块名拼错）或存在语法错误时，
    discover **不会**报错，而是塞进一个 unittest.loader._FailedTest 占位对象。
    表现是"只收集到 1 条用例、还莫名其妙报错"，很容易被误判成
    "用例没被收集到"或"框架有 bug"。
    """
    broken = []
    for case in _iter_cases(suite):
        exc = getattr(case, '_exception', None)
        if exc is not None or type(case).__name__ == '_FailedTest':
            broken.append((case, exc))
    return broken


def _case_has_ddt(case):
    """判断一条用例的方法是否带 @data_driven 数据集标记。【内部】"""
    cls = type(case)
    for name in dir(cls):
        method = getattr(cls, name, None)
        if callable(method) and getattr(method, '_ddt_dataset', None) is not None:
            return True
    return False


def expand_ddt_suite(suite):
    """把 suite 里所有带 @data_driven 的用例展开成「每组数据一条」。【内部】

    ★ 为什么收集后要专门展开（验收实测发现的断链）：
      @data_driven 只在方法上打 `_ddt_dataset` 标记，真正展开靠
      dataDriver.expand_ddt_cases。以前这一步没有接入 build_suite ——
      装饰过的用例会被 unittest 按普通方法调用，而它的签名是
      (self, row)，缺 row 直接 TypeError，整条用例报错。
      也就是说：数据驱动的 API 和文档都在，执行链路却是断的。
      现在在收集阶段统一展开，让这个功能真正闭环。
    """
    from Common.dataDriver import expand_ddt_cases

    items = list(_iter_cases(suite))
    if not any(_case_has_ddt(c) for c in items):
        return suite
    expanded = []
    for case in items:
        if _case_has_ddt(case):
            expanded.append(expand_ddt_cases(case))
        else:
            expanded.append(case)
    return unittest.TestSuite(expanded)


def build_suite(tests_dir=None, pattern='test*.py', cases=None):
    """扫描用例目录，把用例收集成一个 TestSuite。

    :param tests_dir: 用例目录，默认 TestSuites/（见 Common/dir_config.py）
    :param pattern:   文件名匹配规则，默认 test*.py；只想跑某类用例可传 'test_Login*.py'
    :param cases:     用例关键字过滤（跑冒烟时用），为空表示全部
    :return: unittest.TestSuite 实例；此时用例还没执行，只是被"装进篮子"。
             收集失败时返回**空 suite**（由调用方按"未收集到用例"处理），不抛异常。

    【为什么不向外抛异常】
        旧版这里是裸调 discover()。缺 __init__.py 时它抛的 ImportError 会一路崩到
        顶层，让进程退出码变成 1 —— 在 CI 里被解释成"用例失败"，
        而真实原因是"用例根本没被收集"（按契约应为 2）。修掉这个语义错误，
        顺带把原因翻译成中文写进日志。
    """
    tests_dir = tests_dir or testcases_dir
    log1.info('开始加载测试用例，目录：%s，pattern：%s' % (tests_dir, pattern))

    # ---- 前置体检：把"必然收集不到"的问题提前拦住，给出中文原因 ----
    if not os.path.isdir(tests_dir) or not os.path.isfile(
            os.path.join(tests_dir, '__init__.py')):
        log1.error('用例目录检查未通过：')
        for line in diagnose_collection(tests_dir, pattern):
            log1.error('  %s' % line)
        return unittest.TestSuite()

    # discover 三个关键参数：
    #   start_dir     = 从哪个目录开始找
    #   pattern       = 文件名规则
    #   top_level_dir = 顶层包目录（决定 sys.path 和模块导入名，必须传）
    try:
        suite = unittest.defaultTestLoader.discover(
            start_dir=tests_dir,
            pattern=pattern,
            top_level_dir=base_dir,
        )
    except BaseException:
        # 兜底：not importable、目录权限、模块编码错误等
        log1.error('收集用例时发生异常，以下为目录体检结果：', exc_info=1)
        for line in diagnose_collection(tests_dir, pattern):
            log1.error('  %s' % line)
        return unittest.TestSuite()

    log1.info('加载测试用例完成，共 %d 个用例' % suite.countTestCases())

    # ---- 识别导入失败的占位用例：必须显式告警，不能当作正常情况 ----
    for case, exc in find_broken_modules(suite):
        log1.error('用例模块导入失败：%s' % case.id())
        log1.error('  原因：%s' % (exc or '未知'))
        log1.error('  -> 常见于：依赖没安装（如 playwright）、import 路径写错、'
                   '文件里有语法错误。修好后该文件里的用例才会被真正收集')

    if cases:
        suite = filter_suite(suite, cases)

    # 数据驱动展开：@data_driven 标记的用例在这里变成「每组数据一条」。
    # 没有该标记的用例原样返回（零开销），整个框架行为不变。
    suite = expand_ddt_suite(suite)

    # 依赖守卫接入：@depends_on 标记的用例在前置失败时自动跳过，
    # 而不是连环失败。无依赖标记的 suite 原样返回（零开销）。
    from Common.dependency import wrap_suite_with_dependency_guard
    suite = wrap_suite_with_dependency_guard(suite)
    return suite


def resolve_junit_path(junit=None):
    """确定 JUnit XML 的输出路径。

    :param junit: 显式路径；传 'none'/'off' 表示不生成
    :return: 绝对路径，或 None（不生成）

    默认取 config.ini -> [ci] junit_xml；该项**没配或留空**时，
    自动按 [paths] report_dir + junit_filename 拼出（路径单一来源，见 dir_config）。

    ★ 相对路径与产物隔离的关系（验收实测发现的分裂）：
      产物隔离（runId 子目录）开启时，HTML 报告进了 Report/<runId>/，
      而显式配置的相对路径 junit_xml 若仍相对工程根解析，就会落在
      Report/ 根下 —— 两个并发构建照样互相覆盖 JUnit 结果，
      隔离等于只做了一半。所以相对路径改为**随报告目录**解析：
      写文件名即与 HTML 报告同住隔离子目录；写绝对路径则原样尊重
      （那是调用方明确的归档意图，不参与隔离）。
    """
    if junit is None:
        junit = Config.get('ci', 'junit_xml', '')
        if not str(junit).strip():
            junit = _dir_config.junitreport_file
    junit = str(junit).strip()
    if not junit or junit.lower() in ('none', 'off', 'false', '0', 'no'):
        return None
    if not os.path.isabs(junit):
        # 相对路径 -> 随 HTML 报告所在目录（隔离时即 Report/<runId>/）。
        # 兼容旧写法 'Report/xxx.xml'：报告根已是 Report/<runId>，
        # 直接 join 会套出 Report/<runId>/Report/，所以取文件名即可。
        junit = os.path.join(_dir_config.htmlreport_dir, os.path.basename(junit))
    # normpath 统一路径分隔符：配置里可能写成 'Report/xxx.xml'，
    # 在 Windows 上归一化后日志和 Jenkins 展示都更干净
    return os.path.normpath(junit)


def print_failure_digest(result):
    """把失败用例的一行摘要打印到控制台。

    为什么：Jenkins 控制台默认只展示命令输出。有了这段摘要，
    值班同学不打开报告也能立刻知道"挂了哪条、报什么错"。
    """
    if not result.failures and not result.errors:
        return
    print('')
    print('==================== 失败摘要 ====================')
    for label, items in (('失败', result.failures), ('错误', result.errors)):
        for test, trace in items:
            lines = [line for line in (trace or '').strip().splitlines() if line.strip()]
            reason = lines[-1] if lines else '无详细信息'
            print('[%s] %s' % (label, test.id()))
            print('        %s' % reason)
    print('==================================================')
    print('')


def _collect_allure(result, run_start):
    """从 unittest 结果对象收集 Allure 格式结果文件。

    遍历 result 的成功/失败/错误/跳过列表，为每条用例写一个
    allure-results/<uuid>-result.json。失败用例自动关联最近截图。
    """
    from Common.allureReport import _write_result, _build_result, write_attachment
    import os as _os
    from Common.dir_config import img_dir

    base_ms = int(run_start * 1000)
    counter = [0]

    def _case_ms():
        """给每条用例分配一个递增的时间戳（没有精确计时时用相对值）。"""
        counter[0] += 1
        return base_ms + counter[0] * 100, base_ms + counter[0] * 100 + 50

    # 成功用例：result.successes 是列表，元素是 test 对象（不同 unittest 版本结构不同）
    successes = getattr(result, 'successes', [])
    for item in successes:
        test = item[0] if isinstance(item, tuple) else item
        s, e = _case_ms()
        _write_result(_build_result(test, 'success', s, e))

    # 失败
    for test, tb in result.failures:
        s, e = _case_ms()
        attaches = _maybe_attach_screenshot(img_dir, write_attachment)
        err = (AssertionError, AssertionError(tb), None)
        _write_result(_build_result(test, 'failure', s, e, err, attaches))

    # 错误
    for test, tb in result.errors:
        s, e = _case_ms()
        attaches = _maybe_attach_screenshot(img_dir, write_attachment)
        err = (Exception, Exception(tb), None)
        _write_result(_build_result(test, 'error', s, e, err, attaches))

    # 跳过
    for test, reason in result.skipped:
        s, e = _case_ms()
        _write_result(_build_result(test, 'skip', s, e))

    log1.info('Allure 结果已生成到 allure-results/，共 %d 条用例'
              % (len(successes) + len(result.failures) + len(result.errors) + len(result.skipped)))


def _maybe_attach_screenshot(img_dir, write_attachment):
    """尝试关联最近的失败截图到 Allure 结果。"""
    try:
        import os as _os, time as _time
        if not _os.path.isdir(img_dir):
            return None
        pngs = sorted(
            (_os.path.join(dp, f) for dp, _, fs in _os.walk(img_dir) for f in fs if f.endswith('.png')),
            key=_os.path.getmtime, reverse=True)
        if pngs and (_time.time() - _os.path.getmtime(pngs[0])) < 120:
            with open(pngs[0], 'rb') as f:
                src = write_attachment('失败截图', f.read())
                if src:
                    return [{'name': '失败截图', 'source': src, 'type': 'image/png'}]
    except Exception:
        pass
    return None


def run(tests_dir=None, pattern=None, title=None, report_name=None,
        tester=None, description=None, send_mail=False,
        suite_kind='full', cases=None, junit=None):
    """执行测试并生成报告（HTML + JUnit XML）。

    :param tests_dir:    用例目录，默认读 config.ini -> [paths] testcases_dir
    :param pattern:      用例文件匹配规则，默认读 [paths] case_pattern（缺省 test*.py）
    :param title:        报告标题，默认取 config.ini [report] title
    :param report_name:  报告文件名，默认取 config.ini [report] filename
    :param tester:       报告上显示的执行人，默认取 config.ini [report] tester
    :param description:  报告描述，默认取 config.ini [report] description
    :param send_mail:    True 则跑完把报告作为附件发出（收件人见 config.ini [addressed]）
    :param suite_kind:   'full' 全量 / 'smoke' 冒烟子集（见 config.ini [ci] smoke_cases）
    :param cases:        用例过滤关键字，优先级高于 suite_kind
    :param junit:        JUnit XML 输出路径；默认取 config.ini [ci] junit_xml，
                         传 'none' 可关闭（生成目的见 Common/junitReport.py）
    :return: 汇总字典，字段见下方 _summary；
             ★ 关键字段 exit_code：CI 用它判定构建成败，取值含义见本文件头部说明

    注意：参数优先级为「显式传参 > 环境变量 > config.ini > 技术参数默认值」。
    报告元信息属于业务数据：没显式传参又没配置时，按环境配置错误（exit 2）
    处理并指明缺哪个键，绝不用代码里的假默认值蒙混出报告。
    """
    run_start = time.time()

    # ---- 0. 记录构建上下文（Jenkins 上能看出这次是哪个构建在跑）----
    build_info = ci_env.log_context()

    # ---- 0.5 用例收集规则：显式传参 > [paths] case_pattern > 技术默认 ----
    pattern = pattern or Config.get('paths', 'case_pattern', DEFAULT_PATTERN)

    # ---- 1. 报告元信息：显式传参 > config.ini [report]（业务数据，缺失即报错）----
    try:
        title = title or Config.require('report', 'title', '报告页眉与浏览器标签上的标题')
        report_name = report_name or Config.require(
            'report', 'filename', 'HTML 报告文件名（可不带 .html 后缀）')
        tester = tester or Config.require(
            'report', 'tester', '报告上显示的执行人；流水线建议用环境变量注入')
        description = description or Config.require('report', 'description', '报告描述')
    except ConfigMissingError as exc:
        log1.error('无法生成报告元信息：%s' % exc)
        return _summary(exit_code=EXIT_ENV_ERROR, duration=time.time() - run_start)
    # 用户可能只传了 '报告' 没带后缀，这里补上，避免生成出没有后缀的文件
    if not report_name.lower().endswith('.html'):
        report_name += '.html'

    # 在 CI 上把构建标识拼进报告描述 —— 报告被邮件转发出去后仍能追溯来源
    build_label = ci_env.build_label()
    if build_info['ci'] and build_label and build_label not in description:
        description = '%s ｜ %s' % (description, build_label)

    # ---- 2. 收集用例（+ 冒烟过滤）----
    case_filter = resolve_case_filter(suite_kind, cases)
    # 安全兜底：冒烟模式却没有配置任何用例时，**必须中止**而不是静默跑全量。
    # 否则一旦 [ci] smoke_cases 被误删或写空，流水线会在每次构建后
    # 真实上传视频、提交报送单 —— 这是不可接受的副作用。
    if str(suite_kind).lower() == 'smoke' and not case_filter:
        log1.error('冒烟模式未配置任何用例（config.ini -> [ci] smoke_cases 为空）。'
                   '为避免误跑全量用例产生真实数据，已中止执行')
        return _summary(exit_code=EXIT_ENV_ERROR, duration=time.time() - run_start)

    suite = build_suite(tests_dir, pattern, case_filter)
    total = suite.countTestCases()
    if total == 0:
        # 收集为 0 是 CI 上最常见的"看起来是框架坏了"的问题，这里一次给出可自解释的结论：
        #   ① 过滤条件把用例全排除了（原始用例正常，只是关键字没命中）
        #   ② 目录/包标识/文件名等结构问题（逐项体检见 diagnose_collection）
        log1.error('未收集到任何测试用例，排查结论如下：')
        if case_filter:
            log1.error('  ① 过滤条件未命中任何用例，关键字=%s' % (case_filter,))
            log1.error('     -> --cases / config.ini [ci] smoke_cases 填的是用例方法名片段'
                       '（如 test_1login）；可先不加过滤、用默认全量确认能收到几条')
        else:
            log1.error('  ① 未启用任何过滤条件，因此问题出在目录结构或文件命名上')
        log1.error('  ② 目录体检：')
        for line in diagnose_collection(tests_dir or testcases_dir, pattern):
            log1.error('     %s' % line)
        log1.error('  ③ 若日志上方出现"用例模块导入失败"，请先按提示修依赖或语法错误')
        return _summary(exit_code=EXIT_ENV_ERROR, tests_run=0, duration=time.time() - run_start)

    # 收集到的"用例"如果全是模块导入失败的占位对象，说明一条真实用例都没有
    # （典型：目标机器没装 playwright，pip 依赖缺失）。这属于环境问题而非用例失败，
    # 归到 exit 2，避免在 CI 上被误判成"业务用例挂了"。
    broken = find_broken_modules(suite)
    if broken and len(broken) == total:
        log1.error('收集到的 %d 条"用例"全部是模块导入失败的占位对象，'
                   '未取得任何真实用例，按环境异常处理' % total)
        return _summary(exit_code=EXIT_ENV_ERROR, tests_run=0, duration=time.time() - run_start)

    # ---- 3. 准备报告文件（目录不存在会自动创建）----
    report_path = os.path.join(ensure_dir(_dir_config.htmlreport_dir), report_name)
    junit_path = resolve_junit_path(junit)
    log1.info('测试报告将生成至：%s' % report_path)
    if junit_path:
        log1.info('JUnit XML 将生成至：%s' % junit_path)

    # ---- 4. 执行 + 出报告 ----
    # 报告模板要求以二进制流写入，所以用 'wb'
    # with 语句保证异常时文件也能正常关闭（旧版手动 fp.close()，出错会漏关）
    # flaky 重试接入：全局配置 [ci] flaky_retry > 0，或 suite 里有 @flaky
    # 标记的用例时，把重试能力混入结果类。重试发生在报告流程内，
    # HTML / JUnit / 退出码三份证据天然一致（详见 flakyRetry 模块说明）。
    from Common.flakyRetry import make_flaky_result_class, suite_needs_retry
    resultclass = JUnitResult
    if Config.get_int('ci', 'flaky_retry', 0) > 0 or suite_needs_retry(suite):
        resultclass = make_flaky_result_class(JUnitResult)
        log1.info('已启用用例级 flaky 重试（[ci] flaky_retry 或 @flaky 标记）')

    try:
        with open(report_path, 'wb') as fp:
            runner = HTMLTestRunner(
                stream=fp,
                title=title,
                description=description,
                tester=tester,
                # ★ 关键：注入自定义结果类，一次执行同时产出 HTML 与 JUnit XML 两份报告
                resultclass=resultclass,
            )
            result = runner.run(suite)  # 真正开始跑用例，此处耗时最长
        log1.info('测试执行结束，HTML 报告生成成功')
    except BaseException:
        # 走到这里通常意味着浏览器起不来、地址不通这类环境问题
        log1.error('测试执行或报告生成失败', exc_info=1)
        return _summary(exit_code=EXIT_ENV_ERROR, tests_run=0,
                        report=report_path, duration=time.time() - run_start)

    # ---- 5. 生成 JUnit XML（给 Jenkins 等流水线解析）----
    if junit_path:
        try:
            write_junit_xml(
                result,
                junit_path,
                suite_name=title,
                run_start=run_start,
                properties={
                    'job': build_info['job'],
                    'build': build_info['build'],
                    'branch': build_info['branch'],
                    'commit': build_info['commit'],
                    'app_build': build_info['app_build'],
                    'url': Config.get('environment', 'url', ''),
                    'browser': Config.get('environment', 'browser', ''),
                    'headless': Config.get('ci', 'headless', ''),
                    'suite': suite_kind,
                },
            )
        except BaseException:
            # XML 生成失败不影响 HTML 报告和退出码，仅记日志
            # （但 Jenkins 的 junit 步骤会因此拿不到结果，需要在流水线日志里排查）
            log1.error('JUnit XML 生成失败', exc_info=1)

    # ---- 5.5 可选：生成 Allure 结果（--allure 开启时）----
    try:
        from Common import allureReport
        if allureReport.is_enabled():
            _collect_allure(result, run_start)
    except BaseException:
        # Allure 生成失败不影响主流程
        log1.error('Allure 结果生成失败', exc_info=1)

    # ---- 6. 可选：发送邮件 ----
    if send_mail:
        # 延迟导入：不开启邮件功能时，连配置都不用读，少一层耦合
        from Common.sentMail import sent_mail as _sent_mail

        _sent_mail(report_path)

    # ---- 7. 汇总统计 + 判定退出码 ----
    failures = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped)
    exit_code = EXIT_OK if (failures == 0 and errors == 0) else EXIT_TEST_FAILED

    # 业务配置缺失（ConfigMissingError）属于"环境没配好"而不是"用例失败"：
    # 用例层 require 的报错会随 setUpClass 落进 errors，这里统一改判为退出码 2，
    # 让 CI 把它和真实用例失败区分开（缺配置重试也没用，必须人工补配置）。
    if exit_code == EXIT_TEST_FAILED and any(
            'ConfigMissingError' in (tb or '')
            for _case, tb in result.errors):
        exit_code = EXIT_ENV_ERROR
        log1.error('检测到必需业务配置缺失：这不是用例失败，而是 config.ini 没配好，'
                   '请按上方报错里的 段/键/环境变量 线索补配置后重跑')

    # 失败摘要打印到控制台，流水线日志里一眼可见
    print_failure_digest(result)

    duration = round(time.time() - run_start, 3)
    log1.info('本次执行耗时 %.1f 秒，用例 %d 条，失败 %d，错误 %d，跳过 %d，退出码 %d'
              % (duration, result.testsRun, failures, errors, skipped, exit_code))

    return _summary(
        exit_code=exit_code,
        tests_run=result.testsRun,
        failures=failures,
        errors=errors,
        skipped=skipped,
        report=report_path,
        junit=junit_path,
        duration=duration,
        cases=[{k: v for k, v in item.items() if k != 'stdout'} for item in result.cases],
    )


def _summary(**kwargs):
    """把返回字段补全成固定结构，避免调用方到处写 .get()。

    固定字段：exit_code / tests_run / failures / errors / skipped /
             report / junit / duration / cases / success
    """
    summary = {
        'exit_code': EXIT_OK,
        'tests_run': 0,
        'failures': 0,
        'errors': 0,
        'skipped': 0,
        'report': None,
        'junit': None,
        'duration': 0.0,
        'cases': [],
    }
    summary.update(kwargs)
    summary['success'] = summary['exit_code'] == EXIT_OK
    return summary
