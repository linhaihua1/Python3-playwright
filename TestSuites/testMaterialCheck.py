# -*- coding: utf-8 -*-
"""
==========================================================================
 用例层：素材库查询区只读自检（独立用例示例，新人可照此改）
==========================================================================

【一句话说明】
    本文件演示**第二条路**：不依赖其它用例的独立用例。
    它自己完成「登录 -> 进生产系统 -> 进素材库」的前置链路，
    然后只做**只读校验**，可以单独运行、随便跑，不会污染任何数据。

【和 testLogin.py 的区别 —— 该抄哪个？】

    testLogin.py          TestMaterialCheck（本文件）
    ------------------    ----------------------------
    6 条串行业务流        3 条互不依赖的校验
    只开 1 次浏览器        自己开自己的浏览器
    必须从 test_1 顺序跑   每条都能单独跑
    会真实上传/报送数据    只读，零数据副作用
    测"业务能不能走通"     测"页面元素对不对"

    判断口诀：
      用例之间有先后依赖（先登录才能上传）-> 抄 testLogin.py（共享浏览器）
      用例各自独立、只做校验              -> 抄本文件（自带浏览器）

【为什么这条用例安全】
    全程只用 get_element / get_value 读页面，以及往查询框里填字，
    既不点「查 询」（不发请求）、也不点「上 传」「报 送」（不写数据）。
    所以它可以在生产环境放心执行 —— 新手第一次练手，建议就拿它当模板。

【执行顺序】
    方法名 test_N 前缀决定顺序（unittest 按方法名字母序执行）：
        test_1ready     前置链路是否走通（已进入素材库）
        test_2controls  查询区关键控件是否齐全
        test_3keyword   输入能否生效、重置能否清空

【单独运行这一条用例】
    python run.py --cases testMaterialCheck
==========================================================================
"""

import unittest

from Common.basePage import BasePage
from Common.config import Config
from Common.log import log1
from PageObject.loginPage import LoginPage


class TestMaterialCheck(unittest.TestCase):
    """素材库查询区只读自检。

    类名以 Test 开头是 unittest 的硬性要求，否则不会被收集。
    """

    # 类属性：整个类共用的浏览器与页面对象（在 setUpClass 里赋值）
    driver = None

    # 断言期望值没有任何代码兜底：值只来自 config.ini -> [assert] title_material，
    # 缺配置会在 setUpClass 阶段直接报错并指明该配哪个键（零硬编码纪律）。
    title_material = None

    # 用例中文名：来自 config.ini [case_names]，与 testLogin.py 共用同一段配置
    case_names = None

    # ---------------------------------------------------------------------- #
    # 前置 / 后置：整个类只执行一次
    # ---------------------------------------------------------------------- #
    @classmethod
    def setUpClass(cls):
        """类级前置：读配置 -> 开浏览器 -> 走完进入素材库的前置链路。"""
        # ---- 1. 从配置读参数（业务数据缺失即报错，绝不硬编码账号/标题）----
        cls.username, cls.password = Config.get_account()
        cls.title_material = Config.require('assert', 'title_material',
                                            '进入目标页面后的标题期望值')

        # 用例中文名：日志与报告里的显示文字，同样属于业务数据
        cls.case_names = {
            'ready': Config.get('case_names', 'material', 'test_1ready'),
            'controls': Config.get('case_names', 'controls', 'test_2controls'),
            'keyword': Config.get('case_names', 'keyword_input', 'test_3keyword'),
        }

        # ---- 2. 打开浏览器 ----
        cls.base = BasePage()
        cls.driver = cls.base.open_browser()

        # ---- 3. 建页面对象 ----
        cls.page = LoginPage(cls.driver)

        # ---- 4. 前置链路：登录 -> 进生产系统 -> 切标签页 -> 进素材库 ----
        # 这四步在 testLogin.py 里是 3 条独立用例（为了演示业务流程），
        # 在独立用例里则属于"前置条件"，放在 setUpClass 走一次就好。
        cls.page.login(cls.username, cls.password)   # 登录门户
        cls.page.click_sign_in()                     # 点「发布系统」，会新开标签页
        cls.page.switch_other_window()               # ★ 必须切到新标签页，否则后面全部定位失败
        cls.page.page_wait('workbench_render', 2)    # 等新页面首屏渲染（[timing] 可配）
        cls.page.click_material()                    # 左侧菜单：内容管理 -> 素材库
        cls.page.page_wait('material_render', 2)     # 等素材库首屏渲染（[timing] 可配）

    @classmethod
    def tearDownClass(cls):
        """类级后置：关闭浏览器。放在这里能保证用例失败也照样关掉。"""
        cls.base.dr_quit()

    # ---------------------------------------------------------------------- #
    # 通用断言：失败时自动截图 + 打 ERROR 日志，方便事后回溯
    # ---------------------------------------------------------------------- #
    def check_title(self, case_name, expected):
        """断言当前页面标题符合预期。

        :param case_name: 用例中文名，只用于日志可读性
        :param expected:  期望的页面标题关键字（来自 config.ini [assert]）

        ★ 匹配方式由 [assert] match_mode 决定（exact/contains/regex），
          不是写死的相等判断 —— 配置里只写稳定不变的关键字即可。
        """
        actual = self.page.get_title()
        mode = Config.get_match_mode()
        if not Config.match_title(actual, expected, mode):
            log1.error('用例失败:%s，期望[%s] 实际[%s]（匹配模式：%s）\n'
                       % (case_name, expected, actual, mode))
            self.page.get_img()     # ★ 失败瞬间截图，这是排错最有效的证据
            raise AssertionError('标题不匹配（模式 %s）：期望[%s] 实际[%s]'
                                 % (mode, expected, actual))
        log1.info('用例成功:%s（期望[%s] 实际[%s] 模式[%s] 已匹配）\n'
                  % (case_name, expected, actual, mode))

    # ====================================================================== #
    # 用例
    # ====================================================================== #
    def test_1ready(self):
        """用例1：前置链路走通 —— 已经停在素材库页面。

        这是"守卫断言"：不先确认到了正确的页面，
        后面的控件检查失败时分不清是"页面不对"还是"元素改版"。
        """
        case_name = self.case_names['ready']
        log1.info('执行测试用例：%s' % case_name)
        self.check_title(case_name, self.title_material)

    def test_2controls(self):
        """用例2：查询区的三个关键控件都渲染出来了。

        注意断言写法：不要写 assertTrue(all(ready.values()))，
        那样失败时只告诉你"有东西少了"，不说少哪个。
        先收集出缺失清单再断言为空，失败信息直接可用。
        """
        case_name = self.case_names['controls']
        log1.info('执行测试用例：%s' % case_name)

        ready = self.page.query_form_ready()
        missing = [name for name, ok in ready.items() if not ok]

        if missing:
            self.page.get_img()     # 缺控件时先截图再断言失败
        self.assertEqual(missing, [], '查询区缺少这些控件：%s' % missing)

    def test_3keyword(self):
        """用例3：关键字能真的填进去，重置后能真的清空。

        ★ 这一条是本例的重点：**"没报错"不等于"生效了"**。
        只有把值读回来比对过，才算真的验证过。
        读值用 get_value()（读控件 value），不是 get_text()（读显示文字）。
        """
        case_name = self.case_names['keyword']
        log1.info('执行测试用例：%s' % case_name)

        keyword = Config.require('testcase', 'query_keyword')

        # ---- 步骤 1：填值，然后读回来比对 ----
        self.page.fill_keyword(keyword)
        actual = self.page.read_keyword()
        self.assertEqual(actual, keyword,
                         '输入未生效：期望填[%s]，实际读到[%s]' % (keyword, actual))

        # ---- 步骤 2：重置，再读回来确认已清空 ----
        self.page.reset_query()
        self.page.page_wait('reset_settle', 1)   # 等前端把输入框清空（[timing] 可配）
        cleared = self.page.read_keyword()
        # 不同前端框架清空后 value 可能是空串或 None，两种都算"已清空"
        self.assertIn(cleared, (None, ''),
                      '重置后关键字输入框应被清空，实际=[%s]' % cleared)
