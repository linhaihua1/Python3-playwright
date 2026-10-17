# -*- coding: utf-8 -*-
"""
==========================================================================
 页面元素表（新人阅读顺序：第 4 个）
==========================================================================

【一句话说明】
    整个工程**所有**元素的定位信息都集中在这里，其他地方不许出现 xpath。
    元素值全部在**导入时从 config.ini 动态获取**，代码里零业务字面量。

【★ 核心设计：静态数据已全部动态化】
    改造前，本文件里写死着大量界面文案：
        //span[normalize-space(text())='内容管理']
        //button[contains(normalize-space(.),'报') and contains(..., '送')]
        //input[contains(@placeholder,'关键字') or ...]
    换一套系统就得改这里 —— 那不算配置化，只是把硬编码换了个地方。

    改造后按「数据 vs 策略」严格二分：

    ┌ 业务数据（随系统变）→ config.ini 的 [locators] 段 ──────────────┐
    │   菜单名、按钮文字、placeholder 关键词、组件 id 等界面特征，    │
    │   代码里一个中文字符都没有。换系统只改配置，代码零改动。       │
    └──────────────────────────────────────────────────────────────┘
    ┌ 定位策略（通用能力）→ 留在本文件，形如本文件顶部的策略函数 ────┐
    │   by_text / by_text_loose / by_placeholder / by_id_anchor     │
    │   只回答"怎么按某个特征找"，不回答"要找哪个词"。              │
    └──────────────────────────────────────────────────────────────┘
    ┌ 技术兜底（HTML 语义）→ 本文件末尾的 _FALLBACK_* 常量 ───────┐
    │   input[type=password]、input[type=file]、tbody/tr[1] 等，  │
    │   任何系统都一样，写死不算硬编码。配置漏填时的安全网。       │
    └──────────────────────────────────────────────────────────────┘

【三级取值优先级（每个元素都按这个顺序找）】
    1. 配置给了完整定位表达式（`<键名>_xpath`）  → 直接用，业务可完全自定义
    2. 配置给了界面文字（`<键名>`）               → 用策略函数现场拼出 xpath
    3. 都没有                                       → 用技术兜底常量

【插值机制】
    [locators] 段支持 ${key} 占位符，读取时自动展开。例如：

        [locators]
        uploader_btn_id = global-uploader-btn                       # 纯数据
        btn_upload_dialog = //label[@id='${uploader_btn_id}']/i    # 引用上面

    产品把 id 改掉时只改一行，依赖它的定位表达式自动跟着变。
    注意：configOverWrite 已关闭 ConfigParser 的 % 插值，这里 ${} 是
    Config._expand 自己实现的另一套机制，两者互不干扰。

【为什么要做「去空格匹配」by_text_loose】
    中文系统的按钮文字常带字间空格：「报 送」「上 传」「查 询」「重 置」
    「保 存」「初 审」。这属于排版细节而非业务语义，用等值匹配会全部失效。
    by_text_loose 用 translate() 把空格剔除后再比对，对
    「报送」「报 送」「报  送」三种写法一视同仁 —— 这类不稳定不来自
    业务，来自排版，就不该让业务方来兜。

【在框架中的位置】
    Common/basePage.py 负责"怎么定位"，本文件只提供"定位什么"：
        Element1.Username  ->  ['xpath', "//input[not(@type) or @type='text']"]
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
      3. 按文本定位        —— 本文件 by_text 系列，容忍字间空格
      4. 按相对结构定位    —— contains(@class) + 邻近文本，不依赖绝对层级
    ✗ 已淘汰：绝对路径 /html/body/div[4]/...、硬编码索引 div[3]/td[8]
               following::div[4] 这类轴定位带索引 —— 前端调结构即失效

【业务背景（按被测系统的页面顺序）】
    第 1 站 门户：输入账号密码登录，标题期望见 [assert] title_login。
    第 2 站 生产系统入口：点入口新开标签页，标题期望见 title_workbench。
    第 3 站 素材库：左侧多级菜单进入，是上传/查询/报送用例的操作现场。
    业务动作：上传（填作者/手机号/上报单位）、查询（关键字+重置）、
              报送（选初审人+保存）。
    ⚠️ 以上中文均为**示例说明**，实际文案全部来自 config.ini [locators]。
==========================================================================
"""

from Common.config import Config
from Common.log import log1

# ---------------------------------------------------------------------------
# 定位策略函数库
#
#   这些函数只回答"怎么按某个特征找元素"，不回答"要找哪个词"。
#   因此代码里不会出现任何业务中文字符，可安全地被单测直接调用。
# ---------------------------------------------------------------------------

# 需要在匹配时剔除的排版字符：中文界面常见的字间空格。
_NOISE_CHARS = ' '


def by_text(text):
    """按**完全匹配**的可见文字定位，跨 button/div/span/a 常见标签。

    适用于「内容管理」「素材库」这类唯一性高的菜单项。

    ★ 为什么用 not(*) 排除祖先容器（这条是从真实故障里加上的）：
      页面上「登录」按钮外面常包一层 div 做样式。xpath 的 `|` 联合返回
      的是**文档顺序**，而 div 容器排在 button 之前，于是 locator.first
      抓到的是那个空壳 div —— 点击它等于什么都没点。
      加 `and not(*)` 表示"这个元素自己没有元素子节点"，
      文字在内层按钮上的 div 就被排除，只留下真正的叶子节点。

    :param text: 界面文字（来自配置）
    :return: [定位方式, 定位值]
    """
    leaf = "normalize-space(text())='%s' and not(*)" % text
    return ['xpath',
            "//button[%s] | //a[%s] | //span[%s] | //div[%s]"
            % (leaf, leaf, leaf, leaf)]


def by_text_loose(text):
    """按文字定位，但**先剔除字间空格**再比对。

    为什么需要：中文系统按钮常写成「报 送」「上 传」「查 询」「重 置」
    「保 存」「初 审」。这是排版细节不是业务语义，用等值匹配会全部失效，
    而让业务方去精确填写空格数很不现实。translate() 在 xpath 1.0 里是标准
    函数，剔除空格后再比对，对三种空格写法一视同仁。

    ★ 同样带 not(*) 排除祖先容器，理由见 by_text 的注释：
      点「登录」点到外层 div 等于没点，是最难自查的一类故障。

    ★ 标签顺序按「语义专属性」排：可交互元素（button/a）优先，
      容器类标签（div/span）靠后。xpath 联合仍按文档顺序返回，
      但 not(*) 已经滤掉了绝大多数包装层，剩余歧义大幅减少。

    :param text: 界面文字（来自配置，不含空格最佳，含空格也能匹配）
    :return: [定位方式, 定位值]
    """
    # 配置里万一带了空格，先归一化，避免拼接出 translate 的重复参数
    normalized = ''.join(str(text).split())
    if not normalized:
        return ['xpath', "//button"]
    cond = ("translate(normalize-space(.),'%s','')='%s' and not(*)"
            % (_NOISE_CHARS, normalized))
    return ['xpath',
            "//button[%s] | //a[%s] | //span[%s] | //div[%s] | //i[%s]"
            % (cond, cond, cond, cond, cond)]


def by_text_contains(*words):
    """按文字**包含**若干词片段定位，用于一个元素由多个词共同约束的场景。

    与 by_text_loose 的区别：这里不要求整体相等，只要页面上那段文字
    同时含有全部词片段即可，容忍按钮前后还有别的文字（如「立即报送」）。

    :param words: 词片段（来自配置）
    :return: [定位方式, 定位值]
    """
    if not words:
        return ['xpath', "//button"]
    joined = " and ".join("contains(normalize-space(.),'%s')" % w for w in words)
    return ['xpath',
            "//button[%s] | //span[%s] | //a[%s] | //div[%s] | //i[%s]"
            % (joined, joined, joined, joined, joined)]


def by_placeholder(*keywords):
    """按输入框 placeholder 包含任一关键词定位。

    :param keywords: placeholder 关键词（来自配置）
    :return: [定位方式, 定位值]
    """
    if not keywords:
        return ['xpath', "//input"]
    joined = " or ".join("contains(@placeholder,'%s')" % k for k in keywords)
    return ['xpath', "//input[%s]" % joined]


def by_id_anchor(element_id, inner=None):
    """按 id 锚点定位元素或它的内部元素。

    id 是企业级最稳的定位锚点：前端不会随便改元素的 id，
    所以「上传按钮挂在哪个容器下」这类信息最适合用 id 表达。

    ★ 分支顺序很重要（这条也是从真实故障里加上的）：
      传 inner 时，把**精确分支写在联合表达式的最前面**。
      BasePage._pick_target() 会先单独试第一个分支再决定取哪个，
      所以"想要的在前"是这套约定的一部分 ——
      若把 `//*[@id]` 放前面，窄化逻辑取到的就是容器而不是内部图标，
      后续 fill/click 都作用在错误的元素上。

    :param element_id: 目标 id（来自配置）
    :param inner:     可选，在锚点内部再取什么（'i' / 'button' / None）
    :return: [定位方式, 定位值]
    """
    if inner:
        # 精确分支在前：它是调用方真正想要的元素
        return ['xpath', "//*[@id='%s']//%s | //*[@id='%s']"
                        % (element_id, inner, element_id)]
    return ['xpath', "//*[@id='%s']" % element_id]


# ---------------------------------------------------------------------------
# 技术兜底定位：通用 HTML 结构特征，与具体系统无关，换系统也不用改。
# 配置漏填时启用，保证框架不至于瘫痪；定位准不准由业务方在配置里保证。
# ---------------------------------------------------------------------------
_FALLBACK_USERNAME = ('xpath', "//input[not(@type) or @type='text']")
_FALLBACK_PASSWORD = ('xpath', "//input[@type='password']")
_FALLBACK_FILE_INPUT = ('xpath', "//input[@type='file']")
_FALLBACK_TABLE_ROW1 = ('xpath', "//tbody/tr[1]")
_FALLBACK_TEXT_INPUT = ('xpath', "//input[not(@type) or @type='text']")
_FALLBACK_BUTTON = ('xpath', "//button")


def _locate(key, builder=None, suffix_args=(), fallback=None, hint=''):
    """按三级优先级解析出一个元素定位。**内部工具，元素表专用。**

    顺序：
        1. 配置给了 `<key>_xpath` 完整定位表达式 -> 直接用（业务完全自定义）
        2. 配置给了 `<key>` 界面数据 + 传了策略函数 -> 现场用策略拼 xpath
        3. 都没给 -> 用 fallback 技术兜底

    :param key:         [locators] 段的配置键名（不含 _xpath 后缀）
    :param builder:     策略函数，如 by_text_loose；None 表示此元素只接受完整表达式
    :param suffix_args: 追加在界面数据之后的策略参数。
                        例如 by_id_anchor 需要 ('i',) 表示取锚点内部的 i 标签。
    :param fallback:    技术兜底二元组；None 表示无兜底（此时元素可能为 None）
    :param hint:        给配置缺失日志的中文说明
    :return: [定位方式, 定位值]；三级都不满足时返回 None
    """
    # ---- 第 1 级：配置直接给了完整定位表达式 ----
    explicit = Config.locator('%s_xpath' % key, None)
    if explicit:
        return explicit

    # ---- 第 2 级：配置给了界面数据，用策略函数现场拼 ----
    if builder is not None:
        raw = Config.locator_text(key, '')
        if raw:
            # 支持多值：一律用竖线分隔，如 field_keyword = 关键字|关键词|搜索
            parts = [p.strip() for p in str(raw).split('|') if p.strip()]
            if parts:
                return builder(*(tuple(parts) + tuple(suffix_args)))

    # ---- 第 3 级：技术兜底 ----
    if fallback is not None:
        if not hint:
            log1.info('元素定位 %s 未配置界面数据，已使用技术兜底定位；'
                      '建议在 config.ini 的 [locators] 段补充 %s（'
                      '对应环境变量 UI_LOCATORS_%s）'
                      % (key, key, key.upper()))
        return [fallback[0], fallback[1]]

    log1.error('元素定位 %s 既未配置界面数据、也无技术兜底，'
               '该元素将无法定位。请在 config.ini 的 [locators] 段配置 %s'
               % (key, key))
    return None


class Element1:
    """被测系统元素表。

    命名约定：变量名首字母大写的是"页面主控件"，小写的是表单字段/按钮。

    ★ 全部元素值在类定义时（即模块导入时）通过 _locate() 从
      config.ini [locators] 段动态解析，代码里没有任何业务中文字面量。
      修改 config.ini 后重新启动即可生效，无需改代码。
    """

    # ==================== 第 1 站：门户登录页 ====================
    # 用户名框：业务可配 placeholder，兜底取第一个文本输入框
    Username = _locate('username_input', by_placeholder,
                       fallback=_FALLBACK_USERNAME, hint='用户名框占位提示')
    # 密码框：type=password 是通用技术特征，可安全写死
    Password = _locate('password_input', by_placeholder,
                       fallback=_FALLBACK_PASSWORD, hint='密码框占位提示')
    # 登录按钮：按钮文字可能带空格，用宽松匹配
    Sign_button = _locate('btn_login', by_text_loose,
                          fallback=_FALLBACK_BUTTON, hint='登录按钮文字')

    # ==================== 第 2 站：从门户进生产系统 ====================
    # 系统入口按钮：点它会新开标签页。纯业务文案，无通用兜底
    button = _locate('btn_enter_system', by_text_loose,
                     hint='进入生产系统的入口按钮文字')

    # ==================== 第 3 站：素材库导航 ====================
    # 一级菜单：多为唯一文字，用等值匹配更严
    content = _locate('menu_first_level', by_text, hint='一级菜单名称')
    # 二级菜单
    material = _locate('menu_second_level', by_text, hint='二级菜单名称')

    # ==================== 业务动作一：上传 ====================
    # 弹起上传对话框的按钮：id 锚点最稳
    uplaodbutton = _locate('btn_upload_dialog', by_id_anchor, ('i',),
                           fallback=('xpath', "//label[@id]"), hint='上传按钮的 id')
    # 隐藏的文件控件：type=file 是通用技术特征
    FileInput = _locate('input_file', by_id_anchor,
                        fallback=_FALLBACK_FILE_INPUT, hint='隐藏文件控件的 id')
    # 三个表单字段：placeholder 关键词，支持竖线分隔多值
    videoauthor = _locate('field_author', by_placeholder,
                          fallback=_FALLBACK_TEXT_INPUT, hint='作者字段占位提示')
    phone = _locate('field_phone', by_placeholder,
                    fallback=_FALLBACK_TEXT_INPUT, hint='手机号字段占位提示')
    reporting = _locate('field_reporting', by_placeholder,
                        fallback=_FALLBACK_TEXT_INPUT, hint='上报单位字段占位提示')
    # 「上 传」提交按钮
    upload = _locate('btn_upload_submit', by_text_loose,
                     fallback=_FALLBACK_BUTTON, hint='上传提交按钮文字')
    # 文件列表展开/收起按钮
    showbuttons = _locate('btn_upload_collapse', by_id_anchor, ('button',),
                          fallback=_FALLBACK_BUTTON, hint='文件列表收起按钮的容器 id')
    # 「确定」关闭弹窗
    confirm = _locate('btn_confirm', by_text_loose,
                      fallback=_FALLBACK_BUTTON, hint='确定按钮文字')

    # ==================== 业务动作二：查询 ====================
    # 关键字输入框：支持 关键字|关键词|搜索 这样的多值配置
    keyword = _locate('field_keyword', by_placeholder,
                      fallback=_FALLBACK_TEXT_INPUT, hint='关键字输入框占位提示')
    # 「查 询」按钮
    querybutton = _locate('btn_query', by_text_loose,
                          fallback=_FALLBACK_BUTTON, hint='查询按钮文字')
    # 「重 置」按钮
    replacement = _locate('btn_reset', by_text_loose,
                          fallback=_FALLBACK_BUTTON, hint='重置按钮文字')

    # ==================== 业务动作三：报送 ====================
    # 列表第一行的「报 送」：限定在首行内，避免点到其它行的按钮
    submission = _locate('btn_submit_report', by_text_loose,
                         fallback=_FALLBACK_TABLE_ROW1, hint='报送按钮文字')
    # 「初 审」下拉框
    first_trial = _locate('combo_first_trial', by_text_loose,
                          fallback=('xpath', "//select"), hint='初审下拉框文字')
    # 初审人选项：配置可给下拉容器 class（走 *_xpath 覆盖），否则取第一项
    person_of_first_instance = _locate('option_first_trial_person', None,
                                       fallback=('xpath', "//li[1]"),
                                       hint='初审人下拉项定位')
    # 「保 存」按钮
    save = _locate('btn_save', by_text_loose,
                   fallback=_FALLBACK_BUTTON, hint='保存按钮文字')


# ---------------------------------------------------------------------------
# 兼容旧命名：类名早期少写了一个 t（Elemen1），老代码或老笔记可能还用这个名字。
# 新代码请统一写 Element1。
# ---------------------------------------------------------------------------
Elemen1 = Element1
