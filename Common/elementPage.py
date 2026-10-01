# -*- coding: utf-8 -*-
"""
==========================================================================
 页面元素表（新人阅读顺序：第 4 个）
==========================================================================

【一句话说明】
    整个工程**所有**元素的定位信息都集中在这里，其他地方不许出现 xpath。
    前端页面一改版，只改这一个文件。

【在框架中的位置】
    Common/basePage.py 负责"怎么定位"，本文件只提供"定位什么"：
        Element1.Username  等价于  ['xpath', "//input[@type='text']"]
    使用方式（在 PageObject 里）：
        self.type(Element1.Username, 'zhangsan')

【描述格式】
    [定位方式, 定位值]，定位方式取值见 basePage.BY_MAP：
    id / name / class / tag / link / plink / css / xpath
    例：['id', 'global-uploader-btn']  ['css', '#app .upload-btn']

【业务背景（按被测系统的页面顺序）】
    第 1 站 统一门户（sso-portal）
        单位统一的单点登录门户页，输入账号密码登录，登录后 title = '统一门户'。
    第 2 站 生产系统入口
        门户里有"发布系统"入口，点进去会**新开一个标签页**，
        标题变成 '工作台 - 云媒资系统'，对应下面的「工作台」相关定位。
    第 3 站 素材库
        云媒资系统左侧菜单：内容管理 -> 素材库，标题 '素材库 - 云媒资系统'。
        素材库是本工程大部分用例（上传/查询/报送）的操作现场。
    业务动作
        上传：把本地视频文件传进素材库，要填「视频作者 / 手机号 / 上报单位」。
        查询：按关键字搜索素材，另有"重置"按钮清空查询条件。
        报送：把素材提交进入审核流程，需要选择「初审人」（第一级审核人）后保存。

【新人注意 —— 本文件最大的坑】
    下面很多 xpath 是**绝对路径**，形如 /html/body/div[4]/div/div[2]/... 或
    //*[@id='app']/div/div[2]/section/section/main/...
    这种写法对 DOM 层级极度敏感，页面上多套一层 div 就会失效。
    已知最脆弱的几个：submission（报送按钮）、first_trial（初审下拉）、
    person_of_first_instance（初审人选项）。
    改进方向：逐步替换成相对定位（按文本、按属性），例如
        //span[normalize-space(text())='报 送']
        //button[contains(@class,'report')]
    【改定位时的验证方法】直接跑 python run.py，看 Logs/Error_Logs 里
    "没有定位到元素,定位方式：... 使用的值：..." 一行，再配合 img/ 里的截图。
==========================================================================
"""


class Element1:
    """融媒体生产系统（云媒资）元素表。

    命名约定：变量名首字母大写的是"页面主控件"，小写的是表单字段/按钮。
    """

    # ==================== 第 1 站：统一门户 登录页 ====================
    Username = ['xpath', "//input[@type='text']"]      # 用户名输入框（页面上唯一的 text 输入框）
    Password = ['xpath', "//input[@type='password']"]  # 密码输入框（用 type=password 精确区分）
    Sign_button = ['xpath', "//div[@id='app']/div/div[2]/div/div/div/form/div[4]/div/div"]  # 登录按钮

    # ==================== 第 2 站：从门户进生产系统 ====================
    # 通过"发布系统"这段文字定位到它后面的可点击区域
    button = ['xpath', "(.//*[normalize-space(text()) and normalize-space(.)='发布系统'])[1]/following::div[4]"]

    # ==================== 第 3 站：素材库导航 ====================
    content = ['xpath', "//*[@id='app']/div[1]/div[1]/div/div[1]/div/ul/div[3]/li/div/span"]  # 左侧菜单「内容管理」
    material = ['xpath', "//div[@id='app']/div/div/div/div/div/ul/div[3]/li/ul/div/a/li/span"]  # 子菜单「素材库」

    # ==================== 业务动作一：上传视频 ====================
    # 页面上可见的"上传文件"按钮（是个 label，点它会弹出 Windows 系统对话框）
    uplaodbutton = ['xpath', "//label[@id='global-uploader-btn']/i"]   # 注：拼写保留原样，别单独改，loginPage 在引用
    # 隐藏在组件内部的文件控件。直接给它赋文件路径（Playwright set_input_files）就能完成选择，
    # 从而绕开系统对话框 —— 这是工程默认的上传方式，见 basePage.files_upload()
    FileInput = ['xpath', "//input[@type='file']"]
    videoauthor = ['xpath', "//main/form/div[2]/div/div/input"]   # 上传表单：视频作者
    phone = ['xpath', "//div[3]/div/div/input"]                   # 上传表单：手机号
    reporting = ['xpath', "//div[5]/div/div/input"]               # 上传表单：上报单位
    # 「上 传」提交按钮：靠左边的「取 消」按钮文字做相对定位（按钮文字里带空格，别删）
    upload = ['xpath', "(.//*[normalize-space(text()) and normalize-space(.)='取 消'])[1]/following::span[1]"]
    showbuttons = ['xpath', "//div[@id='global-uploader']/div/div[3]/div/div/div/button/span/i"]  # 文件列表的展开/收起按钮
    confirm = ['xpath', "(.//*[normalize-space(text()) and normalize-space(.)='取消'])[1]/following::span[1]"]  # 「确定」

    # ==================== 业务动作二：关键字查询 ====================
    keyword = ['xpath', "//*[@id='app']/div/div[2]/section/section/main/div[1]/form/div[2]/div/div/input"]  # 关键字输入框
    querybutton = ['xpath', "//*[@id='app']/div/div[2]/section/section/main/div[1]/form/div[6]/div/button/span"]  # 「查 询」
    replacement = ['xpath', "//*[@id='app']/div/div[2]/section/section/main/div[1]/form/div[7]/div/button"]  # 「重 置」

    # ==================== 业务动作三：报送 ====================
    # 素材列表第一行的「报 送」按钮（td[8] 是第 8 列，前端调列顺序会失效）
    submission = ['xpath', "//*[@id='app']/div/div[2]/section/section/main/div[2]/div[4]/div[2]/table/tbody/tr[1]/td[8]/div/button[3]/span"]
    first_trial = ['xpath', "/html/body/div[4]/div/div[2]/section/main/form/div[2]/div/div/div/span/span/i"]  # 「初审」下拉框
    person_of_first_instance = ['xpath', "/html/body/div[5]/div[1]/div[1]/ul/li[8]"]  # 下拉列表里的第 8 项（初审人）
    save = ['xpath', "(.//*[normalize-space(text()) and normalize-space(.)='返 回'])[1]/following::span[1]"]  # 「保 存」


# ---------------------------------------------------------------------------
# 兼容旧命名：类名早期少写了一个 t（Elemen1），老代码或老笔记可能还用这个名字。
# 新代码请统一写 Element1。
# ---------------------------------------------------------------------------
Elemen1 = Element1
