# -*- coding: utf-8 -*-
"""
==========================================================================
 通用页面对象 GenericPage —— 让本框架「接入任意主流 Web 系统」的关键
==========================================================================

【一句话说明】
    这是一个**系统无关**的页面对象：党政 / 财务 / ERP / 商城 / 数据中台……
    只要是"用户名+密码登录 + 菜单导航 + 表单/列表"这类主流 Web 系统，
    都可以**不写一行 xpath**，仅靠 config.ini 配置就把登录和常用操作跑起来。

【在框架中的位置】
    TestSuites/testXxx.py     用例层：断言
        └─> PageObject/genericPage.py  <- 本文件：配置驱动的通用动作
              └─> Common/basePage.py     通用浏览器能力（含跨系统通用方法）

【与 LoginPage 的区别】
    LoginPage 是为某个具体系统（融媒体）写死的页面对象，元素来自 elementPage。
    GenericPage 不绑定任何具体系统，元素靠"占位符/按钮文字"等稳定特征定位，
    定位值全部来自 config.ini 的 [system] 段。接入新系统优先用本文件。

【怎么用（接入一个新系统只需 3 步）】
    1. 填 config.ini 的 [system] 段（账号、占位符文案、登录按钮文字、成功标识）
    2. from PageObject.genericPage import GenericPage
       gp = GenericPage(driver)
    3. gp.login()                 # 打开地址并完成登录
       gp.open_menu('系统管理')    # 按文字点菜单
       gp.assert_present('用户列表')  # 校验某文本出现（断言交给用例层也行）

【设计原则】
    ✅ 只写"做什么"，不写断言（断言是用例层职责）
    ✅ 不出现 xpath 字符串；定位特征全部来自配置 [system]
==========================================================================
"""

from Common.basePage import BasePage, NoSuchElementException
from Common.config import Config
from Common.log import log1


class GenericPage(BasePage):
    """配置驱动、系统无关的通用页面对象。

    继承 BasePage 后，click_by_text / type_by_placeholder / wait_loading_gone
    等跨系统通用方法可直接使用。实例化时传入 driver：
        gp = GenericPage(driver)
    """

    # ------------------------------------------------------------------ #
    # 登录：几乎所有系统的登录页都是「用户名框 + 密码框 + 登录按钮」
    #   靠 placeholder 文案与按钮文字定位，换系统只改配置不改代码
    # ------------------------------------------------------------------ #
    def login(self, username=None, password=None):
        """完成一次通用登录。

        所需配置（config.ini -> [system]）：
            username_placeholder  用户名输入框的占位提示文案，如「请输入账号」
            password_placeholder  密码输入框的占位提示文案，如「请输入密码」
            login_button_text     登录按钮的文字，如「登录」
            success_text          登录成功后页面必出现的文字，如「退出」/「工作台」
                                  （用于等登录完成；留空则改等加载遮罩消失）
        这三项定位文案属于业务数据，缺失会直接抛 ConfigMissingError 指明该配哪个键。
        账号密码优先用参数，其次读 [testcase] login_username / login_password。
        """
        username = username or Config.require('testcase', 'login_username')
        password = password or Config.require('testcase', 'login_password')
        # ★ 定位文案全部来自配置，代码里不写任何中文默认值（换系统只改配置）
        user_ph = Config.require('system', 'username_placeholder',
                                 '用户名输入框的占位提示文案（placeholder）')
        pwd_ph = Config.require('system', 'password_placeholder',
                                '密码输入框的占位提示文案（placeholder）')
        btn_text = Config.require('system', 'login_button_text', '登录按钮上显示的文字')
        # success_text 允许为空（空表示改等加载遮罩消失），所以不是 require
        success_text = Config.get('system', 'success_text', '')

        self.type_by_placeholder(user_ph, username)
        self.type_by_placeholder(pwd_ph, password)
        # 密码框输入后回车，或点登录按钮；两者都尝试，兼容更多系统
        if not self.click_by_text(btn_text):
            log1.info('未找到文字为「%s」的登录按钮，尝试回车提交' % btn_text)
            pwd_box = self.get_element_by_attr('placeholder', pwd_ph)
            if pwd_box is None:
                raise NoSuchElementException(
                    '既找不到登录按钮「%s」，也找不到密码输入框（placeholder=%s），无法提交登录'
                    % (btn_text, pwd_ph))
            pwd_box.press('Enter')   # 在密码框上按回车提交表单
        # 等登录完成：出现成功标识文字 或 等加载遮罩消失
        if success_text:
            self.get_element_by_text(success_text, timeout=self.timeout)
        else:
            self.wait_loading_gone()
        log1.info('通用登录流程完成')

    # ------------------------------------------------------------------ #
    # 导航：按文字点菜单 / 按钮 / tab（所有后台系统通用）
    # ------------------------------------------------------------------ #
    def open_menu(self, *menu_path):
        """依次点击多级菜单，按文字定位。

        用法：gp.open_menu('系统管理', '用户管理')
        每一级都先等待再点击，自动处理"点完一级才出现二级"的展开延迟。
        """
        for level, text in enumerate(menu_path, 1):
            ok = self.click_by_text(text)
            if not ok:
                log1.error('第 %d 级菜单「%s」点击失败' % (level, text))
                return False
            # 等下一级菜单/页面渲染（技术参数，可配 [tuning] menu_render_wait）
            self.my_sleep(Config.get_float('tuning', 'menu_render_wait', 1))
        log1.info('菜单导航完成：%s' % ' -> '.join(menu_path))
        return True

    def click_button(self, text):
        """按文字点击按钮（新增/保存/查询/删除 等）。"""
        return self.click_by_text(text, tag='button') or self.click_by_text(text)

    # ------------------------------------------------------------------ #
    # 表单：按 placeholder 填字段（绝大多数系统表单通用）
    # ------------------------------------------------------------------ #
    def fill_form(self, fields):
        """批量填写表单。

        :param fields: dict，{占位符文案: 要填的值}
                       例如 {'请输入姓名': '张三', '请输入手机号': '13800138000'}
        """
        for placeholder, value in fields.items():
            self.type_by_placeholder(placeholder, value)
        log1.info('表单填写完成，共 %d 个字段' % len(fields))

    # ------------------------------------------------------------------ #
    # 列表：按单元格文字定位记录（ERP/商城/数据中台的列表页通用）
    # ------------------------------------------------------------------ #
    def find_record(self, text, table_selector=None):
        """在列表中找到含指定文本的那一行（点编辑/删除前先定位记录）。

        :return: tr 元素；找不到返回 None
        """
        return self.find_row_by_cell_text(text, table_selector)
