# -*- coding: utf-8 -*-
"""
==========================================================================
 页面对象 LoginPage（新人阅读顺序：第 3 个）
==========================================================================

【一句话说明】
    把"统一门户登录"和"融媒体素材库"这两个页面的操作，写成人话级别的方法。
    用例层只需要调 login()/click_material()/submitted()，不用关心 xpath 长什么样。

【在框架中的位置】
    TestSuites/testLogin.py       用例层：调本类的方法 + 写断言
        └─> PageObject/loginPage.py  <- 本文件：一个方法 = 一个业务动作
              └─> Common/basePage.py   真正操作浏览器
                    └─> Common/elementPage.py  提供元素定位值

【设计原则（接手时务必遵守）】
    ✅ 本文件只写"做什么"：点哪个按钮、填哪个框、按什么顺序
    ❌ 本文件不写断言（assertEqual 之类）—— 断言是用例层的职责
    ❌ 本文件不出现 xpath 字符串 —— 元素一律从 Element1 取

【为什么叫 LoginPage 却包含素材库操作】
    因为这两个页面属于同一条业务链（登录门户 -> 进生产系统 -> 素材库），
    元素和跳转关系紧密，放在一起比拆成三个文件更好维护。
    文件名叫 loginPage 是沿用历史命名。
==========================================================================
"""

from Common.basePage import BasePage
from Common.config import Config
from Common.elementPage import Element1
from Common.log import log1


class LoginPage(BasePage):
    """统一门户登录与融媒体素材库操作。

    继承 BasePage 后，self.type / self.click / self.get_img 等方法可直接使用。
    实例化时要把 driver 传进来：
        login_page = LoginPage(driver)
    """

    # ====================================================================== #
    # 一、统一门户：登录与跳转
    # ====================================================================== #
    def login(self, username, password):
        """在统一门户输入账号密码并点登录。

        :param username: 登录账号（来自 config.ini -> [testcase] login_username）
        :param password: 登录密码（来自 config.ini -> [testcase] login_password）

        执行后页面 title 应为 '统一门户'，用例 test_1login 会校验这一点。
        """
        self.type(Element1.Username, username)   # 填用户名
        self.type(Element1.Password, password)   # 填密码
        self.click(Element1.Sign_button)         # 点登录
        self.get_img()                           # 留一张登录后的截图，便于回溯

    def click_sign_in(self):
        """点门户里的「发布系统」进入生产系统。

        注意：这一步会**新开一个浏览器标签页**，
        调用方之后必须 switch_other_window() 切换过去，否则后续操作还在旧标签页上。
        """
        self.click(Element1.button)

    # ====================================================================== #
    # 二、素材库导航
    # ====================================================================== #
    def click_material(self):
        """在左侧菜单依次点「内容管理」->「素材库」。

        说明：素材库是二级菜单，需要先展开一级菜单才会出现。
        """
        self.click(Element1.content)    # 一级：内容管理
        self.click(Element1.material)   # 二级：素材库

    # ====================================================================== #
    # 三、上传视频（本工程最主要的业务动作）
    # ====================================================================== #
    def upload_file(self, file_path=None):
        """选择本地待上传的视频文件。

        :param file_path: 文件绝对路径；不传则用 config.ini -> [testcase] upload_file

        两种实现方式，由 config.ini -> [testcase] upload_mode 决定：
          set_input_files（默认）直接给 input[type=file] 赋文件路径，不弹系统对话框
          autoit                点上传按钮弹出 Windows「打开」对话框，用 autoit 模拟输入
        除 autoit 外的任何取值（含旧值名 send_keys）都走默认方案；
        默认方案失败时会自动回退到 autoit，所以通常不用手动改配置。

        ⚠️ 例外：**配置缺失 / 文件不存在**属于前置条件不满足，回退到 autoit 也解决不了，
        所以这类错误会直接抛出原始原因，不做降级 ——
        否则真实原因会被 autoit 的报错盖掉，排查时完全摸不着头脑。
        """
        mode = (Config.get('testcase', 'upload_mode', 'set_input_files') or 'set_input_files').lower()

        if mode == 'autoit':
            # 明确指定了用对话框方案
            self.click(Element1.uplaodbutton)
            self.upload_by_autoit(file_path)
            return

        try:
            # 推荐方案：不触发任何原生弹窗，速度快、可在无桌面环境跑
            self.files_upload(Element1.FileInput, file_path)
        except (ValueError, FileNotFoundError):
            # 配置缺失 / 文件不存在属于「前置条件不满足」，回退到 autoit 也解决不了，
            # 只会把真实原因盖成一个更难懂的 autoit 报错。所以直接抛出。
            log1.error('上传前置条件不满足，不进行 autoit 回退', exc_info=1)
            raise
        except Exception:
            # 兜底：某些前端结构下 file 控件拿不到，就退回对话框方案
            # 需要 pip install PyAutoIt，且中文 Windows 系统
            log1.error('直接赋值上传失败，回退到 autoit 方案', exc_info=1)
            self.click(Element1.uplaodbutton)
            self.upload_by_autoit(file_path)

    def fill_upload_form(self, video_author, phone, reporting):
        """填写上传表单并提交。

        :param video_author: 视频作者
        :param phone:        手机号
        :param reporting:    上报单位

        顺序不能乱：必须先把表单字段填完，才能点「上 传」；
        上传成功后还要收起文件列表、点「确定」关闭上传弹窗。
        """
        self.type(Element1.videoauthor, video_author)   # 视频作者
        self.type(Element1.phone, phone)                # 手机号
        self.type(Element1.reporting, reporting)        # 上报单位
        self.click(Element1.upload)                     # 「上 传」提交
        self.click(Element1.showbuttons)                # 收起已上传文件列表
        self.click(Element1.confirm)                    # 「确定」关闭弹窗

    # ====================================================================== #
    # 四、关键字查询
    # ====================================================================== #
    def query_form_ready(self):
        """自检：素材库查询区的关键控件是否都已渲染出来，返回检查结果。

        :return: dict，形如 {'关键字输入框': True, '查询按钮': True, '重置按钮': False}

        为什么要有这个方法：素材库是异步渲染的单页应用，刚进页面时表单可能还没出来，
        直接操作会偶发失败。先做一次"控件是否齐全"的自检，
        失败时能直接指出**少了哪一个**，而不是只报一句 xpath 定位失败。

        本方法**只读**：不点任何按钮、不发请求，因此可以安全地在生产环境执行。
        """
        checks = {
            '关键字输入框': Element1.keyword,
            '查询按钮': Element1.querybutton,
            '重置按钮': Element1.replacement,
        }
        result = {}
        for name, selector in checks.items():
            # timeout=0 表示立即判定，不等待：此时页面已经加载完，等下去没意义
            result[name] = self.get_element(selector, timeout=0) is not None
            log1.info('查询区控件[%s] 存在=%s' % (name, result[name]))
        return result

    def fill_keyword(self, keyword):
        """只往关键字输入框里填内容，**不点查询**（只读操作，不产生数据影响）。

        :param keyword: 要填入的关键词

        与 keyword_query() 的区别：那个会点「查 询」真正发起搜索、
        再点「重 置」恢复；本方法只负责填值，用于校验"输入是否生效"。
        """
        self.type(Element1.keyword, keyword)

    def read_keyword(self):
        """读取关键字输入框当前的值（校验输入是否生效、重置是否清空）。"""
        return self.get_value(Element1.keyword)

    def reset_query(self):
        """点「重 置」清空查询条件，把列表恢复到初始状态。

        单独抽出来的原因：重置是"把页面还回去"的动作，
        用例做只读校验后必须调用它，避免影响后续用例。
        """
        self.click(Element1.replacement)

    def keyword_query(self, keyword):
        """按关键字搜索素材，搜索后点「重 置」清空查询条件。

        :param keyword: 搜索关键词

        注意：本方法不返回搜索结果，用例层只校验页面 title 未跳走 ——
        如果将来要校验搜索命中数量，需要在这里 return 结果列表。
        """
        self.type(Element1.keyword, keyword)     # 输入关键字
        self.click(Element1.querybutton)         # 点「查 询」
        self.click(Element1.replacement)         # 点「重 置」，为后续用例恢复初始状态

    # ====================================================================== #
    # 五、报送（把素材提交进审核流程）
    # ====================================================================== #
    def submitted(self):
        """对列表第一条素材执行报送：开初审下拉 -> 选初审人 -> 保存。

        业务说明：报送 = 把素材提交给第一级审核人（初审人）审核，
        运营流程上是「编辑提交 -> 初审 -> 终审 -> 发布」，本用例只覆盖第一步。

        已知脆弱点：first_trial 和 person_of_first_instance 用的是绝对 xpath，
        页面上多一层 div 就会失效，改版后优先检查这两个。
        """
        self.click(Element1.submission)                  # 点第一行的「报 送」
        self.click(Element1.first_trial)                 # 点开「初审」下拉框
        self.click(Element1.person_of_first_instance)    # 选中初审人
        self.click(Element1.save)                        # 点「保 存」
