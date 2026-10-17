# -*- coding: utf-8 -*-
"""
==========================================================================
 config.ini 读写封装（新人阅读顺序：第 9 个）
==========================================================================

【一句话说明】
    工程读取 config.ini 的唯一入口。所有环境地址、账号、报告配置都从这里取。

【在框架中的位置】
    config.ini  <- 磁盘上的真实配置文件
        └─> Common/config.py         <- 本文件
              ├─> Common/basePage.py  读 [environment]、[testcase]
              ├─> Common/runner.py    读 [report]
              └─> Common/sentMail.py  读 [sender]、[addressed]
    Common/configOverWriter.py 是本类的底座（定制版 ConfigParser）。

【config.ini 的段位说明】
    [environment]  被测环境：url、browser
    [testcase]     用例参数：登录账号、上传文件、上传方式、上传表单字段、查询关键字
    [assert]       断言期望值：各页面的标题（换系统只改这里）
    [system]       通用页面对象 GenericPage 的定位文案（占位符/按钮文字/成功标识）
    [locators]     元素定位配置：界面文案与定位表达式（换系统只改这里，代码零改动）
    [report]       报告元信息：标题、文件名、执行人、描述
    [ci]           流水线开关：headless、element_timeout、smoke_cases、junit_xml 等
    [paths]        目录与命名：img / Report / TestSuites / junit 文件名 / 用例收集规则
    [tuning]       技术调优参数：视口、轮询间隔、重试间隔、各类页面稳定等待（全部可配，
                   不配则用代码里的技术默认值 —— 这类默认值属于参数调优，不算业务数据）
    [timing]       用例各步骤的页面等待秒数（同样"可配 + 技术默认"）
    [notify]       群机器人通知：webhook、加签密钥、消息前缀
    [sender]       发件人：邮箱、SMTP 授权码/服务器/端口、邮件正文模板
    [addressed]    收件人：键是姓名、值是邮箱（发送时遍历取值）

【★ 零硬编码约定（本框架的数据纪律）】
    业务数据 —— 系统标题、界面文案、账号、上传文件、表单字段、查询关键字、
                报告元信息、邮件正文等 —— 一律用 Config.require(...) 读取：
                配置缺失立刻抛 ConfigMissingError，中文报错会指明
                「该在哪个段配哪个键」，绝不允许在代码里写兜底默认值。
    技术参数 —— 超时秒数、轮询间隔、视口尺寸、目录名等，用 get/get_int/
                get_float 读取，缺省允许保留代码默认值（调优用，不是业务）。
    元素定位 —— 集中在 Common/elementPage.py（定位数据表，换系统时整表替换）。
                其中**界面文案**（按钮文字、菜单名等业务数据）已进一步下沉到
                config.ini 的 [locators] 段，见下方 locator() 方法的说明。

【新人必读：两种读取风格】
    推荐：Config.get('段', '键', '默认值')   —— 取不到就返回默认值，不抛异常
    旧式：Config.config_read('段', '键')     —— 等价于 get，保留给老代码

【新人必读：环境变量覆盖（CI/CD 的关键机制）】
    读取任意配置项前，会先查同名环境变量，规则：
        UI_ + 段名 + _ + 键名 ，全部大写，非字母数字转成下划线
    例如：
        [environment] url             ->  UI_ENVIRONMENT_URL
        [testcase]    login_password  ->  UI_TESTCASE_LOGIN_PASSWORD
        [ci]          headless        ->  UI_CI_HEADLESS
    环境变量**优先于** config.ini。

    为什么要这么做：Jenkins 上不能把生产账号密码写进仓库，
    而是配成 Jenkins 凭据，构建时注入成环境变量。
    这样同一份代码在本机、测试环境、流水线上无需改任何文件即可运行。
    想整体关掉该机制，设置 UI_ENV_OVERRIDE=false。

【新人注意】
    1. 敏感信息目前是明文写在 config.ini 里的（账号密码、邮箱授权码），
       提交代码前请确认该文件不要入库（仓库根目录的 .gitignore 已忽略它），
       真实值建议通过环境变量或 Jenkins 凭据注入。
    2. 写入方法（set_value / config_delete）会直接改磁盘文件，
       只建议给 configTest.py 这类校验脚本使用，不要在用例里随机写配置。
==========================================================================
"""

import configparser
import os
import re

import getcwd
from Common.configOverWriter import ConfigOverWrite
from Common.log import log1

# ---------------------------------------------------------------------------
# 环境变量覆盖机制的前缀。所有可覆盖的变量都以它开头，避免与系统变量撞名。
# ---------------------------------------------------------------------------
ENV_PREFIX = 'UI_'

# 用来判断真/假的文本（配置项和布尔型环境变量共用）
TRUE_VALUES = ('1', 'true', 'yes', 'on', 'y', 't')
FALSE_VALUES = ('0', 'false', 'no', 'off', 'n', 'f', '')


class ConfigMissingError(ValueError):
    """必需的业务配置缺失。

    继承 ValueError 是为了兼容既有调用链的错误处理
    （如 open_browser 对"未配置地址"的 ValueError 语义）。

    报错文案自带"怎么修"：指明段、键、对应的环境变量名，
    新同事不用读文档也能把配置补上。
    """

    def __init__(self, section, key, hint=''):
        self.section = section
        self.key = key
        self.hint = hint
        message = ('缺少必需配置：config.ini 的 [%s] %s（对应环境变量 %s）'
                   % (section, key, Config._env_name(section, key)))
        if hint:
            message = '%s。%s' % (message, hint)
        super(ConfigMissingError, self).__init__(message)

# ---------------------------------------------------------------------------
# 模块级初始化：确定配置文件位置，建好可复用的解析器实例
# ---------------------------------------------------------------------------
path = getcwd.get_cwd()                                            # 工程根目录
config_path = os.path.join(path, 'config.ini')                     # 配置文件绝对路径
# 用自定义的子类：保留键的大小写 + 关闭 % 插值（密码里可能有 %）
config = ConfigOverWrite()
config.read(config_path, encoding='utf-8-sig')                     # utf-8-sig 兼容带 BOM 的文件


class Config:
    """配置文件操作类。所有方法都是静态方法，直接 Config.get(...) 调用即可。

    注意：本类与模块级变量 `config` 是两个东西 ——
    前者是"工具类"，后者是"底层的 ConfigParser 实例"，不要混淆。
    """

    # ====================================================================== #
    # 环境变量覆盖（CI/CD 用；本机运行时通常不会命中）
    # ====================================================================== #
    @staticmethod
    def _env_name(section, key):
        """把 [段]键 换算成环境变量名。

        :return: 例 _env_name('environment', 'url') -> 'UI_ENVIRONMENT_URL'

        规则：前缀 UI_ + 段 + 键，转大写，非字母数字一律换成下划线。
        所以 [testcase] 里的 upload-file / upload_file 都会变成 UI_TESTCASE_UPLOAD_FILE。
        """
        raw = '%s%s_%s' % (ENV_PREFIX, section, key)
        return re.sub(r'[^0-9A-Za-z]+', '_', raw).upper()

    @staticmethod
    def _env_override(section, key):
        """查找是否有环境变量覆盖了该配置项。

        :return: 环境变量值（字符串）；没有覆盖时返回 None

        注意与"环境变量为空字符串"的区别：空字符串视为未设置，
        否则 Jenkins 里传一个空参数就会把配置意外清空。
        """
        # 允许整体关闭该机制：UI_ENV_OVERRIDE=false
        switch = os.environ.get('%sENV_OVERRIDE' % ENV_PREFIX)
        if switch is not None and switch.strip().lower() in FALSE_VALUES:
            return None

        name = Config._env_name(section, key)
        value = os.environ.get(name)
        if value is None or value.strip() == '':
            return None
        log1.info('配置 [%s] %s 被环境变量 %s 覆盖' % (section, key, name))
        return value

    @staticmethod
    def _reload():
        """操作前重新读盘，保证拿到的是最新内容。

        为什么需要：配置文件在运行期间可能被手工改了，
        缓存住旧值会让改动"不生效"，这是很难排查的坑。
        """
        config.read(config_path, encoding='utf-8-sig')

    # ====================================================================== #
    # 读操作
    # ====================================================================== #
    @staticmethod
    def get(section, key, fallback=None):
        """读取配置项，取不到就返回 fallback，不抛异常。【推荐用法】

        :param section:  段名，如 'environment'
        :param key:      键名，如 'url'
        :param fallback: 取不到时的默认值
        :return: 字符串值，或 fallback

        取值优先级：环境变量 UI_段_键  >  config.ini  >  fallback

        设计说明：旧版直接 config.get() 会在缺配置时抛异常，
        导致"少配一项就整个跑不起来"。改成有默认值后更容易定位真正的问题。
        """
        # 1. 先看是否被环境变量覆盖（CI 上账号密码就是从这里进来的）
        override = Config._env_override(section, key)
        if override is not None:
            return override

        # 2. 再读配置文件
        Config._reload()
        try:
            value = config.get(section, key)
            log1.info('%s下读取%s的值：%s' % (section, key, value))
            return value
        except (configparser.NoSectionError, configparser.NoOptionError):
            log1.info('%s下不存在%s，使用默认值：%s' % (section, key, fallback))
            return fallback

    # 兼容旧调用名：历史代码里写的是 config_read，效果与 get 完全一致
    config_read = get

    @staticmethod
    def require(section, key, hint=''):
        """读取**必需**配置项；缺失或为空则抛 ConfigMissingError。

        与 get 的分工（本框架的零硬编码纪律）：
            - 业务数据（标题/文案/账号/文件路径/关键字…）一律用 require，
              代码里不留任何兜底默认值，缺配置就在启动阶段报清楚。
            - 技术参数（超时、间隔、视口…）用 get/get_int/get_float，
              允许代码里保留调优默认值。

        :param section: 段名
        :param key:     键名
        :param hint:    额外提示（可选），会拼进报错文案
        :return: 去掉首尾空白的字符串值
        :raise ConfigMissingError: 该配置项不存在或为空
        """
        value = Config.get(section, key)
        if value is None or str(value).strip() == '':
            raise ConfigMissingError(section, key, hint)
        return str(value).strip()

    @staticmethod
    def require_int(section, key, hint=''):
        """读取必需整数配置（如超时秒数），缺失/非整数都报错。"""
        raw = Config.require(section, key, hint)
        try:
            return int(raw)
        except ValueError:
            raise ConfigMissingError(section, key,
                                     ('值必须是整数，当前为「%s」；%s' % (raw, hint)) if hint
                                     else '值必须是整数，当前为「%s」' % raw)

    @staticmethod
    def require_float(section, key, hint=''):
        """读取必需浮点配置，缺失/非数字都报错。"""
        raw = Config.require(section, key, hint)
        try:
            return float(raw)
        except ValueError:
            raise ConfigMissingError(section, key,
                                     ('值必须是数字，当前为「%s」；%s' % (raw, hint)) if hint
                                     else '值必须是数字，当前为「%s」' % raw)

    @staticmethod
    def get_bool(section, key, fallback=False):
        """读取开关型配置（true/false、1/0、yes/no 都认）。【在用】

        :return: True / False

        用在哪：config.ini -> [ci] headless 就是这种开关。
        之所以单独写一个方法，是因为配置里写的是字符串 'true'，
        直接 bool('false') 得到的是 True —— 这是个很经典的坑。
        """
        value = Config.get(section, key, fallback)
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in TRUE_VALUES:
            return True
        if text in FALSE_VALUES:
            return False
        log1.error('%s下%s的值不是合法布尔值：%s，使用默认值 %s' % (section, key, value, fallback))
        return bool(fallback)


    @staticmethod
    def get_int(section, key, fallback=0):
        """读取整数配置（如超时秒数），非法值回退到 fallback。【预留】"""
        value = Config.get(section, key, fallback)
        try:
            return int(value)
        except (TypeError, ValueError):
            log1.error('%s下%s的值无法转为整数：%s' % (section, key, value))
            return fallback

    @staticmethod
    def get_float(section, key, fallback=0.0):
        """读取小数配置（如轮询间隔 0.3 秒），非法值回退到 fallback。【在用】

        主要服务 [tuning] / [timing] 段的调优参数；
        这类参数允许代码默认值（见类头「零硬编码约定」）。
        """
        value = Config.get(section, key, fallback)
        try:
            return float(value)
        except (TypeError, ValueError):
            log1.error('%s下%s的值无法转为小数：%s' % (section, key, value))
            return fallback

    @staticmethod
    def has_section(section):
        """判断某个段是否存在，返回 True/False。【预留】"""
        Config._reload()
        return config.has_section(section)

    @staticmethod
    def options(section):
        """列出某个段下的所有键名；段不存在返回空列表。

        【在用】sentMail 用它遍历 [addressed] 段，因为收件人是"想加几个加几个"的，
        没法预先知道键名。
        """
        Config._reload()
        if not config.has_section(section):
            log1.info('配置文件中不存在 section：%s' % section)
            return []
        return config.options(section)

    # ====================================================================== #
    # 写操作（会真实修改 config.ini 文件）
    # ====================================================================== #
    @staticmethod
    def set_value(section, key, value):
        """写入一个配置项，段不存在时自动创建。【慎用：会改磁盘文件】"""
        Config._reload()
        if not config.has_section(section):
            config.add_section(section)
            log1.info('新增%s' % section)
        config.set(section, key, value)
        log1.info('%s下新增%s=%s' % (section, key, value))
        with open(config_path, 'w', encoding='utf-8') as f:
            config.write(f)

    @staticmethod
    def config_write(section, key=None, value=None):
        """兼容旧签名的写法。【慎用：会改磁盘文件】

        - 只传 section            -> 新增一个空段
        - 传 section + key + value -> 等价于 set_value()
        """
        Config._reload()
        if key is not None and value is not None:
            Config.set_value(section, key, value)
        else:
            if not config.has_section(section):
                config.add_section(section)
                log1.info('新增%s' % section)
                with open(config_path, 'w', encoding='utf-8') as f:
                    config.write(f)

    @staticmethod
    def config_delete(section, key=None):
        """删除配置。【慎用：删了就没了，没有撤销】

        - 传 section + key -> 只删这个键
        - 只传 section     -> 删掉整个段
        """
        Config._reload()
        if key is not None:
            if config.has_option(section, key):
                config.remove_option(section, key)
                log1.info('删除%s下%s和他的值' % (section, key))
        else:
            if config.has_section(section):
                config.remove_section(section)
                log1.info('删除%s' % section)
        with open(config_path, 'w', encoding='utf-8') as f:
            config.write(f)

    # ====================================================================== #
    # 业务便捷方法：把"用例真正关心的配置"包成一句调用
    # ====================================================================== #
    @staticmethod
    def config_options(section):
        """读取某段下所有键（保留旧接口名），等价于 options()。【预留】"""
        return Config.options(section)

    def get_addkey(self, keys):
        """把收件人键名列表翻译成邮箱列表（保留旧接口）。【预留】

        sentMail.py 现在直接用 Config.options + Config.get，
        这个方法留给外部或老代码调用。
        """
        return [Config.get('addressed', key) for key in keys]

    @staticmethod
    def get_account():
        """取出登录账号，返回元组 (用户名, 密码)。【在用】

        用例里这样用：
            username, password = Config.get_account()
        账号只存在于 config.ini（或流水线注入的环境变量），代码里零硬编码；
        属于业务数据，所以缺任何一项都直接报错并指明该配哪里。
        """
        return (
            Config.require('testcase', 'login_username', '登录账号不能为空，流水线请用凭据注入环境变量'),
            Config.require('testcase', 'login_password', '登录密码不能为空，流水线请用凭据注入环境变量'),
        )

    @staticmethod
    def get_upload_file(required=True):
        """取出待上传文件的绝对路径。【在用】

        支持配置里写 ~/video/demo.mp4 这种带用户目录的写法。
        :param required: True（默认）时缺失/为空直接报错 —— 上传用例的前置数据；
                         False 时保留旧语义（未配置返回 None），给预留工具方法用。
        """
        if required:
            value = Config.require('testcase', 'upload_file',
                                   '待上传文件路径不能为空（支持 ~ 开头的用户目录写法）')
        else:
            value = Config.get('testcase', 'upload_file')
        if not value:
            return None
        return os.path.abspath(os.path.expanduser(value))

    # ====================================================================== #
    # 元素定位：把「界面文案」也变成配置数据（消除 elementPage 里的硬编码）
    #
    # 【为什么要做这件事】
    #   元素定位以前写在 Common/elementPage.py 里，换一套系统就要改代码 ——
    #   那等于"配置化"只做了一半：地址、账号、标题都能配，唯独按钮文案
    #   「发布系统」「素材库」「报送」这些**界面文案**死死钉在代码里。
    #   现在统一搬到 config.ini 的 [locators] 段：
    #       定位表达式  = 结构（属于定位代码，可含 ${key} 引用其他文案）
    #       界面文案    = 业务数据（随系统变，放配置里）
    #   结果：换系统只改配置，一个字都不用动代码。
    #
    # 【边界：技术特征 vs 业务文案】
    #   技术特征（放代码，属于通用 HTML 语义，换系统也不变）：
    #       //input[@type='password']   密码框按 type 找
    #       //input[@type='file']       隐藏文件控件
    #       //tbody/tr[1]               列表第一行
    #   业务文案（放配置，随系统变）：
    #       「内容管理」「素材库」「报 送」「查 询」「重 置」「初 审」
    # ====================================================================== #
    LOCATORS_SECTION = 'locators'

    # 元素定位的段名。别名：[elements] / [element] 也认，方便不同项目习惯。
    _LOCATOR_ALIASES = ('locators', 'elements', 'element')

    @staticmethod
    def _locators_real_section():
        """找出配置里实际存在的定位段名；一个都没有时返回 None。

        为什么要别名：不同项目、不同人习惯把段写成 [locators] / [elements]，
        框架不该因为段名拼写不同就"什么都没读到"而静默退化。
        """
        for name in Config._LOCATOR_ALIASES:
            if Config.has_section(name):
                return name
        return None

    @staticmethod
    def _expand(text, seen=None):
        """把定位表达式里的 ${key} 替换成 [locators] 段中对应键的值。

        :param text: 待展开的字符串，如 "//button[text()='${btn_login}']"
        :param seen: 展开过程中已访问过的键，用于检测循环引用
                     （a 里引用 b、b 里又引用 a 会无限递归）
        :return: 展开后的字符串；没有占位符时原样返回

        为什么需要插值：按钮文案可能带空格（「上 传」）也可能被产品改字，
        让定位表达式引用配置里的文案，改文案时不必动定位表达式本身。
        配置里的 % 已由 ConfigOverWrite 关闭插值，这里 ${} 是另一套机制，互不干扰。
        """
        if not text or '${' not in str(text):
            return text
        seen = seen or set()
        result = str(text)

        def _replace(match):
            key = match.group(1).strip()
            if key in seen:
                # 循环引用：直接留原样并记日志，不要让整个配置读取崩掉
                log1.error('[locators] 定位表达式存在循环引用：%s，原样保留' % key)
                return match.group(0)
            seen.add(key)
            value = Config.get(Config.LOCATORS_SECTION, key, '')
            if not str(value).strip():
                # 引用了没配的文案：保留占位符，让它在页面上匹配不到，
                # 从而**显式失败**而不是悄悄定位到错误元素
                log1.error('[locators] 定位表达式引用的文案 %r 未配置，'
                           '该定位可能失效（对应环境变量 UI_LOCATORS_%s）' % (key, key.upper()))
                return match.group(0)
            return Config._expand(value, seen)

        return re.sub(r'\$\{([^}]+)\}', _replace, result)

    @staticmethod
    def locator(key, fallback=None, hint=''):
        """读取一个元素定位配置，返回 [定位方式, 定位值] 二元列表。

        :param key:      [locators] 段里的键名，如 'btn_login'
        :param fallback: 未配置时使用的兜底定位（技术特征定位，不是业务文案）。
                         传 None 表示"未配置也不兜底"，返回 None 由调用方按业务处理。
        :param hint:     报错时给的中文提示
        :return: [by, value] 列表；未配置且无兜底时返回 None

        设计要点：界面文案缺失不该让整个框架起不来（那属于配置问题，
        退出码 2），但也不该静默用假数据蒙混过去。所以这里：
            - 有配置 -> 用配置（含 ${} 插值）
            - 无配置但有技术兜底 -> 用兜底（元素结构本就与系统无关的场景）
            - 两者都无 -> 返回 None，由调用方明确决定是跳过还是报错
        """
        section = Config._locators_real_section()
        if section:
            raw = Config.get(section, key, '')
            if raw and str(raw).strip():
                # 配置里可以直接写 "xpath=//button[...]"，也可以只写定位值（默认按 xpath）
                raw = str(raw).strip()
                if '=' in raw.split('//')[0]:
                    by, _, value = raw.partition('=')
                    by, value = by.strip(), value.strip()
                else:
                    by, value = 'xpath', raw
                if by and value:
                    return [by, Config._expand(value)]

        if fallback is not None:
            # 兜底定位同样允许插值，方便"默认文案 + 配置覆盖"的组合写法
            fb_by, fb_value = fallback
            return [fb_by, Config._expand(fb_value)]
        return None

    @staticmethod
    def locator_text(key, fallback=''):
        """读取一个界面文案配置（如「素材库」），返回去掉首尾空白的字符串。

        :param key:      [locators] 段里的键名
        :param fallback: 未配置时的技术默认值（一般是空串，由调用方决定后续行为）
        """
        section = Config._locators_real_section()
        raw = Config.get(section, key, '') if section else ''
        value = str(raw).strip() if raw else ''
        return value or fallback

    # ====================================================================== #
    # 断言匹配：让「期望值」不必等于「页面上的完整标题」
    #
    # 【为什么需要】
    #   原来断言是 assertEqual(实际标题, 配置标题)，配置里必须抄全整个标题，
    #   形如「工作台 - 云媒资系统」。可页面标题常带版本号、环境标识、
    #   甚至空格差异，抄一次很快就不准了，一换一个环境就误判。
    #   现在配置里只写**稳定不变的关键字**，匹配方式由 match_mode 决定。
    # ====================================================================== #
    MATCH_MODES = ('exact', 'contains', 'regex')

    @staticmethod
    def get_match_mode():
        """读取断言匹配模式；未配置时默认 contains（最实用）。

        :return: 'exact' / 'contains' / 'regex'
        """
        mode = str(Config.get('assert', 'match_mode', 'contains') or 'contains').strip().lower()
        if mode not in Config.MATCH_MODES:
            log1.error('[assert] match_mode 只能是 %s 之一，当前值 %r 已按 contains 处理'
                       % ('/'.join(Config.MATCH_MODES), mode))
            return 'contains'
        return mode

    @staticmethod
    def match_title(actual, expected, mode=None):
        """按指定模式判断页面标题是否符合期望。**纯函数，不碰浏览器。**

        :param actual:  页面实际标题
        :param expected: 配置里的期望值（关键字 / 完整标题 / 正则）
        :param mode:    匹配模式；不传则读配置 [assert] match_mode
        :return: True 表示匹配

        为什么容忍空格差异：中文系统标题里「上 传」「查 询」这类字间空格
        是排版产物而非业务语义。contains / regex 两种模式都会先把两边
        的空白全部去掉再比对，避免因为一个空格就误判失败。
        """
        mode = mode or Config.get_match_mode()
        actual_s = '' if actual is None else str(actual)
        expected_s = '' if expected is None else str(expected)

        if mode == 'exact':
            return actual_s.strip() == expected_s.strip()
        if mode == 'regex':
            # 正则模式下只在两端去空白，内部空格交给表达式自己决定
            try:
                return re.search(expected_s.strip(), actual_s.strip()) is not None
            except re.error as exc:
                log1.error('[assert] match_mode=regex 但期望值 %r 不是合法正则：%s'
                           % (expected_s, exc))
                return False
        # contains：两边都去掉全部空白后做包含判断
        flat_actual = ''.join(actual_s.split())
        flat_expected = ''.join(expected_s.split())
        return bool(flat_expected) and flat_expected in flat_actual
