# -*- coding: utf-8 -*-
"""
==========================================================================
 用例层：融媒体生产系统 UI 自动化（新人阅读顺序：第 1 个，先看这里）
==========================================================================

【一句话说明】
    本工程最主要的一个测试文件，用 6 条用例走完「登录 -> 上传素材 -> 查询 -> 报送」
    整条业务链。想了解这个工程在测什么，读这个文件就够了。

============== 业务流程（6 条用例的执行顺序） ==============

    用例1 test_1login          在统一门户输入账号密码登录        断言 title = 统一门户
              │
              ▼
    用例2 test_2select_scxt    点「发布系统」→ 切到新打开的标签页   断言 title = 工作台 - 云媒资系统
              │
              ▼
    用例3 test_3material       左侧菜单：内容管理 → 素材库         断言 title = 素材库 - 云媒资系统
              │
              ▼
    用例4 test_4uplaodbutton   选本地视频 → 填作者/手机号/上报单位
              │                → 上传 → 确定                      断言 title 未跳走
              ▼
    用例5 test_5keyword        关键字查询 → 重置                    断言 title 未跳走
              │
              ▼
    用例6 test_6submitted      刷新 → 报送 → 选初审人 → 保存        断言 title 未跳走

===========================================================

【新人必读：三条设计约定】

1）为什么 6 条用例共用同一个浏览器？
    因为它们是**一条串行业务流**，第 4 步上传必须有第 3 步的登录态，
    拆开各自 setUp/tearDown 反而要多登 6 次、跑得更慢也更不稳。
    做法：setUpClass 只开一次浏览器，tearDownClass 统一关闭。

2）为什么方法名是 test_1login、test_2select_scxt 这种带数字的？
    unittest 默认按方法名字母序执行，加数字前缀就是**为了固定执行顺序**。
    改动方法名会改变执行顺序，进而让用例失败 —— 名字不要随便动。
    另外这也意味着：**不能单独跑某一条中间用例**（比如只跑 test_5keyword），
    因为它依赖前面已经登录并进入素材库。

3）为什么断言都在 check_title() 里？
    6 条用例的断言方式完全一样（校验页面标题），统一抽成一个方法后，
    只在一个地方加"失败自动截图"，改动成本最低。
    新增用例时直接调 self.check_title('用例名', '期望标题') 即可。

【新人注意】
    1. 参数全部来自 config.ini -> [testcase]，不要在用例里硬编码账号或文件路径。
    2. 被测地址默认是**生产环境**，跑之前确认上传/报送操作不会污染真实数据。
    3. 用例之间没有数据隔离，任何一步失败，后面的用例会连着失败 ——
       排错时优先修最早失败的那一条。
    4. 排查失败：先看控制台输出，再看 Logs/日期/Error_Logs/，
       最后看 img/日期/ 里自动保存的失败截图。
==========================================================================
"""

import unittest

from Common.basePage import BasePage
from Common.config import Config
from Common.log import log1
from PageObject.loginPage import LoginPage


class TestLogin(unittest.TestCase):
    """登录及素材库业务流程的测试用例集。

    一个 TestCase 类 = 一组有共同前置条件的用例；
    这里的共同前置条件就是"浏览器已打开、且处于登录后的状态"。
    """

    # 类属性：保存打开的浏览器实例，供各用例共享
    # （在 setUpClass 里赋值，在 tearDownClass 里关闭）
    driver = None

    # ---------------------------------------------------------------------- #
    # 断言期望值与用例参数：全部来自 config.ini，代码里不留任何业务字面量。
    # [assert] 三个标题、[testcase] 查询关键字/手机号/上报单位属于业务数据，
    # 缺配置时 Config.require 会在启动阶段就报错并指明该配哪个键（不静默兜底）。
    # 可被同名环境变量覆盖（如 UI_ASSERT_TITLE_LOGIN）。
    # ---------------------------------------------------------------------- #
    title_login = None
    title_workbench = None
    title_material = None
    query_keyword = None

    # ---------------------------------------------------------------------- #
    # 前置 / 后置：整个类只执行一次（区别于每条用例都跑的 setUp / tearDown）
    # ---------------------------------------------------------------------- #
    @classmethod
    def setUpClass(cls):
        """类级前置：读参数 -> 开浏览器 -> 建页面对象。只执行一次。"""
        # ---- 1. 从 config.ini 读取用例参数（业务数据缺失即报错，绝不硬编码）----
        cls.username, cls.password = Config.get_account()          # 登录账号、密码
        cls.upload_file = Config.get_upload_file()                 # 待上传视频的绝对路径
        # 视频作者：未单独配置时默认用登录账号（合理的数据联动，非写死字面量）
        cls.video_author = Config.get('testcase', 'video_author') or cls.username
        cls.phone = Config.require('testcase', 'phone')            # 手机号
        cls.reporting = Config.require('testcase', 'reporting')    # 上报单位
        cls.query_keyword = Config.require('testcase', 'query_keyword')  # 查询关键字

        # ---- 1.1 断言期望值：换系统只改配置，代码里零业务字面量 ----
        cls.title_login = Config.require('assert', 'title_login',
                                         '登录成功后门户首页的标题')
        cls.title_workbench = Config.require('assert', 'title_workbench',
                                             '进入生产系统（工作台）后的标题')
        cls.title_material = Config.require('assert', 'title_material',
                                            '进入素材库后的标题')

        # ---- 2. 打开浏览器并访问被测地址 ----
        cls.base = BasePage()                       # 基类实例，只用来管浏览器生命周期
        cls.driver = cls.base.open_browser()        # 返回 driver，并保存在 cls.base.driver

        # ---- 3. 创建页面对象，后续所有用例共用它 ----
        cls.login_page = LoginPage(cls.driver)

    @classmethod
    def tearDownClass(cls):
        """类级后置：关闭浏览器，避免残留 chrome 进程。"""
        cls.base.dr_quit()

    # ---------------------------------------------------------------------- #
    # 自定义断言：把"校验标题"这件事收敛到一个地方
    # ---------------------------------------------------------------------- #
    def check_title(self, case_name, expected):
        """断言当前页面标题等于预期值，并统一记录用例结果。

        :param case_name: 用例中文名，只用于日志可读性
        :param expected:  期望的页面标题
        :raise AssertionError: 标题不符时抛出 —— 用例会被报告标记为「失败」

        成功：写一条 INFO 日志
        失败：写 ERROR 日志（含 期望/实际 对比）+ 自动截图，再抛出异常
        """
        actual = self.login_page.get_title()
        try:
            self.assertEqual(actual, expected)
        except AssertionError:
            log1.error('测试用例执行失败:%s，期望[%s] 实际[%s]\n' % (case_name, expected, actual))
            # 关键：失败瞬间截图。这是排查问题最有效的证据
            self.login_page.get_img()
            raise
        log1.info('测试用例执行成功:%s\n' % case_name)

    # ====================================================================== #
    # 用例（执行顺序由方法名 test_N 前缀固定，不要随意改名）
    # ====================================================================== #
    def test_1login(self):
        """用例1：登录成功。

        步骤：输入账号密码 -> 点登录 -> 校验跳转到统一门户首页。
        """
        case_name = '登录成功'
        log1.info('执行测试用例：%s' % case_name)
        self.login_page.login(self.username, self.password)
        self.check_title(case_name, self.title_login)

    def test_2select_scxt(self):
        """用例2：从门户进入融媒体生产系统。

        步骤：点「发布系统」（会新开标签页）-> 切到新标签页 -> 等页面加载 -> 校验标题。

        注意：switch_other_window() 是必须的，不切窗口的话
        后续所有元素定位都会在旧标签页上失败。
        """
        case_name = '进入生产系统'
        log1.info('执行测试用例：%s' % case_name)
        self.login_page.click_sign_in()
        self.login_page.switch_other_window()   # 切到新打开的标签页
        # 等新页面首屏渲染（时长可配 [timing] workbench_render，慢环境可调大）
        self.login_page.page_wait('workbench_render', 2)
        self.check_title(case_name, self.title_workbench)

    def test_3material(self):
        """用例3：从工作台进入素材库。

        步骤：左侧菜单 内容管理 -> 素材库 -> 校验标题等于 [assert] title_material。
        """
        case_name = '进入素材库'
        log1.info('执行测试用例：%s' % case_name)
        self.login_page.page_wait('menu_load', 3)         # 等工作台的菜单加载完
        self.login_page.click_material()
        self.login_page.page_wait('material_render', 2)   # 等素材库首屏渲染
        self.check_title(case_name, self.title_material)

    def test_4uplaodbutton(self):
        """用例4：上传视频到素材库（本工程最核心的用例）。

        步骤：选本地文件 -> 等上传组件就绪 -> 填表单（作者/手机号/上报单位）
              -> 上传 -> 收起文件列表 -> 确定。
        校验：上传是弹窗操作，页面不会跳转，因此仍校验标题未变。

        前置条件：config.ini -> [testcase] upload_file 指向的文件必须真实存在，
                 否则本用例会以 FileNotFoundError 失败。
        """
        case_name = '上传视频'
        log1.info('执行测试用例：%s' % case_name)
        self.login_page.upload_file(self.upload_file)     # 选择文件（直接赋值或 autoit 对话框）
        self.login_page.page_wait('upload_recognize', 3)  # 等上传组件识别到文件
        self.login_page.fill_upload_form(self.video_author, self.phone, self.reporting)
        self.login_page.page_wait('upload_progress', 5)   # 等上传进度走完
        self.check_title(case_name, self.title_material)

    def test_5keyword(self):
        """用例5：关键字查询素材。

        步骤：输入关键字 -> 点查询 -> 点重置恢复初始列表。
        校验：查询是页内操作，标题不变。
        """
        case_name = '关键字查询'
        log1.info('执行测试用例：%s' % case_name)
        self.login_page.keyword_query(self.query_keyword)
        self.login_page.page_wait('query_result', 3)      # 等查询结果返回
        self.check_title(case_name, self.title_material)

    def test_6submitted(self):
        """用例6：把素材报送进审核流程。

        步骤：刷新页面拿到最新素材列表 -> 点第一行的「报 送」-> 选初审人 -> 保存。
        校验：报送是弹窗流程，标题不变。

        注意：刷新走页面对象继承的 BasePage.refresh()（Playwright 的 page.reload()），
        不再直接摸 driver —— 迁移前这里是 self.driver.refresh()，属于绕过分层的历史遗留，
        且 Playwright 切换标签页后旧 driver 引用不会自动指向新页面，直接刷新会刷错标签页。
        """
        case_name = '报送'
        log1.info('执行测试用例：%s' % case_name)
        self.login_page.refresh()                         # 刷新，确保能看到刚上传的素材
        self.login_page.page_wait('refresh_render', 5)    # 等刷新后列表重新渲染
        self.login_page.submitted()
        self.login_page.page_wait('submit_render', 2)     # 等报送保存的后端响应落库
        self.check_title(case_name, self.title_material)
