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

【定位策略（企业级加固）】
    优先级从高到低：
      1. 按 id 定位        —— 最稳，前端不会随便改 id
      2. 按属性定位        —— placeholder / type / class 等语义属性
      3. 按文本定位        —— normalize-space(text()) 匹配按钮/菜单文字
      4. 按相对结构定位    —— contains(@class) + 邻近文本，不依赖绝对层级
    ✗ 已淘汰：绝对路径 /html/body/div[4]/...、硬编码索引 div[3]/td[8]
               following::div[4] 这类轴定位带索引 —— 前端调结构即失效

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
==========================================================================
"""


class Element1:
    """融媒体生产系统（云媒资）元素表。

    命名约定：变量名首字母大写的是"页面主控件"，小写的是表单字段/按钮。
    """

    # ==================== 第 1 站：统一门户 登录页 ====================
    # 用户名框：页面上 type=text 的输入框（登录页通常只有一个）
    Username = ['xpath', "//input[@type='text']"]
    # 密码框：type=password 精确区分
    Password = ['xpath', "//input[@type='password']"]
    # 登录按钮：按「登录」文字定位（兼容 button / div / span 各种标签）
    Sign_button = ['xpath',
                   "//button[normalize-space(text())='登录'] | "
                   "//div[normalize-space(text())='登录'] | "
                   "//span[normalize-space(text())='登录']"]

    # ==================== 第 2 站：从门户进生产系统 ====================
    # 「发布系统」入口：直接点文字本身（不再依赖 following::div[4] 轴定位）
    button = ['xpath', "//*[normalize-space(text())='发布系统']"]

    # ==================== 第 3 站：素材库导航 ====================
    # 左侧菜单「内容管理」：按文字定位，不依赖 div[3] 索引
    content = ['xpath', "//span[normalize-space(text())='内容管理'] | "
                        "//*[contains(normalize-space(.),'内容管理')]"]
    # 子菜单「素材库」：按文字定位
    material = ['xpath', "//span[normalize-space(text())='素材库'] | "
                         "//*[contains(normalize-space(.),'素材库')]"]

    # ==================== 业务动作一：上传视频 ====================
    # 页面上可见的"上传文件"按钮（是个 label，点它会弹出 Windows 系统对话框）
    # id 定位最稳，保留
    uplaodbutton = ['xpath', "//label[@id='global-uploader-btn']/i | "
                             "//label[@id='global-uploader-btn']"]
    # 隐藏的文件控件：直接给它赋文件路径（Playwright set_input_files）
    FileInput = ['xpath', "//input[@type='file']"]
    # 上传表单三个字段：按 placeholder 定位（语义化，调 DOM 顺序不影响）
    # 注：如果 placeholder 文案有差异，可在 [system] 段配置后由 GenericPage 接管
    videoauthor = ['xpath', "//input[contains(@placeholder,'作者') or "
                            "contains(@placeholder,'视频')]"]
    phone = ['xpath', "//input[contains(@placeholder,'手机') or "
                      "contains(@placeholder,'电话')]"]
    reporting = ['xpath', "//input[contains(@placeholder,'单位') or "
                          "contains(@placeholder,'上报')]"]
    # 「上 传」提交按钮：按文字定位（按钮文字里带空格，用 normalize-space 处理）
    upload = ['xpath', "//button[contains(normalize-space(.),'上') and "
                       "contains(normalize-space(.),'传')] | "
                       "//span[contains(normalize-space(.),'上') and "
                       "contains(normalize-space(.),'传')]"]
    # 文件列表的展开/收起按钮：按 id 锚点 + 相对结构，不依赖 div[3] 索引
    showbuttons = ['xpath', "//div[@id='global-uploader']//button//i | "
                            "//div[@id='global-uploader']//button"]
    # 「确定」按钮：按文字定位
    confirm = ['xpath', "//button[normalize-space(text())='确定'] | "
                        "//span[normalize-space(text())='确定']"]

    # ==================== 业务动作二：关键字查询 ====================
    # 关键字输入框：按 placeholder 定位（语义化，不依赖 div[2] 索引）
    keyword = ['xpath', "//input[contains(@placeholder,'关键字') or "
                        "contains(@placeholder,'关键词') or "
                        "contains(@placeholder,'搜索')] | "
                        "//main//form//input[1]"]
    # 「查 询」按钮：按文字定位
    querybutton = ['xpath', "//button[contains(normalize-space(.),'查') and "
                            "contains(normalize-space(.),'询')] | "
                            "//span[contains(normalize-space(.),'查') and "
                            "contains(normalize-space(.),'询')]"]
    # 「重 置」按钮：按文字定位
    replacement = ['xpath', "//button[contains(normalize-space(.),'重') and "
                            "contains(normalize-space(.),'置')] | "
                            "//span[contains(normalize-space(.),'重') and "
                            "contains(normalize-space(.),'置')]"]

    # ==================== 业务动作三：报送 ====================
    # 素材列表第一行的「报 送」按钮：按文字定位，不依赖 td[8]/button[3] 索引
    submission = ['xpath', "//tbody/tr[1]//button[contains(normalize-space(.),'报') and "
                           "contains(normalize-space(.),'送')] | "
                           "//tbody/tr[1]//span[contains(normalize-space(.),'报') and "
                           "contains(normalize-space(.),'送')]"]
    # 「初审」下拉框：按文字/语义定位，不再用 /html/body/div[4] 绝对路径
    # Element UI 的下拉选择器通常是 .el-select 或带 select 的 class
    first_trial = ['xpath', "//*[contains(@class,'el-select')]//i | "
                            "//*[contains(normalize-space(.),'初审')]/following::i[1] | "
                            "//form//*[contains(@class,'select')]//i"]
    # 下拉列表里的初审人选项：按可见下拉列表 + 第 N 项定位（不依赖 /html/body/div[5]）
    # 优先选列表里第一个可选的（如果知道具体名字可改成按文字匹配）
    person_of_first_instance = ['xpath', "//div[contains(@class,'el-select-dropdown') and "
                                         "not(contains(@style,'display: none'))]"
                                         "//li[1] | "
                                         "//ul[contains(@class,'el-select-dropdown')]"
                                         "//li[1]"]
    # 「保 存」按钮：按文字定位
    save = ['xpath', "//button[contains(normalize-space(.),'保') and "
                     "contains(normalize-space(.),'存')] | "
                     "//span[contains(normalize-space(.),'保') and "
                     "contains(normalize-space(.),'存')]"]


# ---------------------------------------------------------------------------
# 兼容旧命名：类名早期少写了一个 t（Elemen1），老代码或老笔记可能还用这个名字。
# 新代码请统一写 Element1。
# ---------------------------------------------------------------------------
Elemen1 = Element1
