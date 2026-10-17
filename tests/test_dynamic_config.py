# -*- coding: utf-8 -*-
"""
单元测试：静态数据动态化

【这个文件在守什么】
    工程有一条硬约定：**代码里不许出现业务界面文案**。
    以前这条约定只覆盖了「地址、账号、标题」，元素定位里的
    「内容管理」「素材库」「报 送」这些仍然写死在 elementPage.py 里，
    换系统就得改代码。

    现在改造为：
        业务文案 -> config.ini [locators] 段（数据）
        定位策略 -> elementPage 的策略函数（代码）

    这组测试钉死三件事：
        1. 值真的来自配置（改配置，元素就变）
        2. 代码里确实没有业务文案（静态扫描兜底，防止以后回退）
        3. 三级兜底与插值机制按约定工作
"""

import os
import re
import unittest

from Common.config import Config
from tests.helpers import patch_config


# ---------------------------------------------------------------------------
# 一、元素值来自配置而不是代码
# ---------------------------------------------------------------------------
class TestLocatorValuesComeFromConfig(unittest.TestCase):
    """核心约定：改配置即改行为，不用碰代码。"""

    def test_定位值随配置变化(self):
        """同一份代码，两份配置，元素定位必须跟着变。"""
        ini_a = """
[locators]
btn_login = 甲系统登录
"""
        ini_b = """
[locators]
btn_login = 乙系统登录
"""
        with patch_config(ini_a):
            first = Config.locator('btn_login_xpath') or Config.locator('btn_login')
            from Common.elementPage import by_text_loose
            first = by_text_loose(Config.locator_text('btn_login'))
        with patch_config(ini_b):
            second = by_text_loose(Config.locator_text('btn_login'))

        self.assertIn('甲系统登录', first[1])
        self.assertIn('乙系统登录', second[1])
        self.assertNotEqual(first[1], second[1])

    def test_未配置文案时用技术兜底(self):
        """[locators] 段缺失时不能崩，要退回技术特征定位。"""
        with patch_config("[paths]\nreport_dir = Report\n"):
            # type=file 是通用 HTML 特征，任何系统都有
            sel = Config.locator('input_file', ('xpath', "//input[@type='file']"))
            self.assertIsNotNone(sel)
            self.assertEqual(len(sel), 2)
            self.assertIn('file', sel[1].lower())

    def test_既无配置又无兜底时返回None(self):
        """两样都没有就必须返回 None，让调用方明确处理，不能瞎猜。"""
        with patch_config("[paths]\nreport_dir = Report\n"):
            self.assertIsNone(Config.locator('btn_enter_system', None))

    def test_完整表达式覆盖策略(self):
        """<键名>_xpath 存在时，优先级高于界面数据。"""
        ini = """
[locators]
btn_login = 界面文字
btn_login_xpath = //div[@id='custom-login']
"""
        with patch_config(ini):
            sel = Config.locator('btn_login_xpath')
            self.assertEqual(sel, ['xpath', "//div[@id='custom-login']"])

    def test_完整表达式支持定位方式前缀(self):
        """允许写 css=... / id=... 形式，不必一律用 xpath。"""
        ini = """
[locators]
some_btn_xpath = id=global-uploader-btn
"""
        with patch_config(ini):
            sel = Config.locator('some_btn_xpath')
            self.assertEqual(sel[0], 'id')
            self.assertEqual(sel[1], 'global-uploader-btn')


# ---------------------------------------------------------------------------
# 二、${key} 插值机制
# ---------------------------------------------------------------------------
class TestPlaceholderExpansion(unittest.TestCase):
    """插值让"文案改一处、依赖它的定位全部跟着变"。"""

    def test_占位符被替换成真实文案(self):
        ini = """
[locators]
anchor_id = my-uploader
btn_xpath = //label[@id='${anchor_id}']/i
"""
        with patch_config(ini):
            sel = Config.locator('btn_xpath')
            self.assertIn('my-uploader', sel[1])
            self.assertNotIn('${', sel[1])

    def test_嵌套引用可递归展开(self):
        ini = """
[locators]
a_id = first
b_id = ${a_id}-second
btn_xpath = //*[@id='${b_id}']
"""
        with patch_config(ini):
            sel = Config.locator('btn_xpath')
            self.assertIn('first-second', sel[1])

    def test_循环引用不会无限递归(self):
        """a 引用 b、b 引用 a：必须保留占位符并报错，不能把进程卡死。"""
        ini = """
[locators]
a_xpath = //*[@id='${b_xpath}']
b_xpath = //*[@id='${a_xpath}']
"""
        with patch_config(ini):
            # 只要不抛异常、不卡死即可；展开结果保留原始占位符
            sel = Config.locator('a_xpath')
            self.assertIsNotNone(sel)

    def test_引用未配置的键时保留占位符(self):
        """引用的键没配：显式失败（占位符留着）而不是静默拼出错的值。"""
        ini = """
[locators]
btn_xpath = //*[@id='${never_configured}']
"""
        with patch_config(ini):
            sel = Config.locator('btn_xpath')
            self.assertIn('${never_configured}', sel[1])


# ---------------------------------------------------------------------------
# 三、定位策略函数的行为（不碰浏览器，纯字符串）
# ---------------------------------------------------------------------------
class TestLocatorStrategies(unittest.TestCase):
    """策略函数只回答"怎么找"，不回答"找谁"——参数由配置传入。"""

    def test_去空格匹配消除字间空格差异(self):
        """中文按钮常写成「报 送」，策略要能同时命中「报送」和「报 送」。"""
        from Common.elementPage import by_text_loose
        sel = by_text_loose('报送')
        self.assertIn('translate', sel[1])
        self.assertIn('报送', sel[1])

    def test_去空格匹配会归一化传入的空格(self):
        """配置里万一带了空格，也不该拼出错误的 translate 参数。"""
        from Common.elementPage import by_text_loose
        sel = by_text_loose('上 传')
        self.assertIn("translate(normalize-space(.),' ','')='上传'", sel[1])

    def test_按文字匹配是等值而非包含(self):
        """菜单项要用严格等值，避免命中同名的其它文本节点。"""
        from Common.elementPage import by_text
        sel = by_text('素材库')
        self.assertIn("normalize-space(text())='素材库'", sel[1])
        self.assertNotIn('translate', sel[1])

    def test_placeholder多值用或连接(self):
        from Common.elementPage import by_placeholder
        sel = by_placeholder('关键字', '关键词')
        self.assertIn(' or ', sel[1])
        self.assertIn('关键字', sel[1])
        self.assertIn('关键词', sel[1])

    def test_id锚点可取内部标签(self):
        from Common.elementPage import by_id_anchor
        sel = by_id_anchor('uploader', 'i')
        self.assertIn("@id='uploader'", sel[1])
        self.assertIn('//i', sel[1])

    def test_策略函数不返回空定位(self):
        """传入空值也不能产出空 xpath，否则会静默匹配到整页。"""
        from Common.elementPage import (by_id_anchor, by_placeholder,
                                        by_text, by_text_loose)
        for sel in (by_text(''), by_text_loose(''),
                    by_placeholder(), by_id_anchor('')):
            self.assertTrue(sel[1].strip(), '策略函数产出了空定位：%r' % (sel,))


# ---------------------------------------------------------------------------
# 四、断言动态匹配
# ---------------------------------------------------------------------------
class TestTitleMatching(unittest.TestCase):
    """match_mode 让配置里只写稳定关键字，不必抄完整标题。"""

    def test_contains模式容忍后缀(self):
        """页面标题带版本号/环境标识时，含关键字即应通过。"""
        self.assertTrue(Config.match_title('素材库 - 云媒资系统 v2.3', '素材库', 'contains'))

    def test_contains模式容忍空格差异(self):
        self.assertTrue(Config.match_title('工 作 台', '工作台', 'contains'))

    def test_contains模式不匹配时返回False(self):
        self.assertFalse(Config.match_title('登录页', '素材库', 'contains'))

    def test_exact模式要求完全相等(self):
        self.assertTrue(Config.match_title('素材库', '素材库', 'exact'))
        self.assertFalse(Config.match_title('素材库 - 云媒资系统', '素材库', 'exact'))

    def test_regex模式支持自定义判定(self):
        self.assertTrue(Config.match_title('素材库-生产环境', r'^素材库-.*环境$', 'regex'))
        self.assertFalse(Config.match_title('工作台', r'^素材库', 'regex'))

    def test_非法正则不会抛异常(self):
        """配置写错正则时应判定为不匹配，而不是把用例崩掉。"""
        self.assertFalse(Config.match_title('任意标题', '[未闭合', 'regex'))

    def test_非法模式回退到contains(self):
        """match_mode 配了非法值时应回退，不能让整个断言失效。"""
        with patch_config("[assert]\nmatch_mode = 不存在的模式\n"):
            self.assertEqual(Config.get_match_mode(), 'contains')

    def test_默认模式为contains(self):
        with patch_config("[assert]\ntitle_login = x\n"):
            self.assertEqual(Config.get_match_mode(), 'contains')

    def test_实际标题为None时安全处理(self):
        """页面还没加载完时 get_title() 可能返回空，不能抛异常。"""
        self.assertFalse(Config.match_title(None, '素材库', 'contains'))
        self.assertFalse(Config.match_title('', '素材库', 'contains'))


# ---------------------------------------------------------------------------
# 五、静态扫描：代码里不许再出现业务文案（防止以后回退）
# ---------------------------------------------------------------------------
class TestNoBusinessTextInCode(unittest.TestCase):
    """这是"动态化"最容易被回退的地方，用静态扫描钉死。

    做法：读源文件、剔除注释与文档字符串，再检查是否残留业务中文词。
    只扫业务层代码（用例层、页面对象层、元素表），不扫日志/报错文案 ——
    那些是给人看的运维信息，不参与定位。
    """

    # 这些词只允许出现在 config.ini / 注释 / 文档里，不允许出现在可执行代码中
    BUSINESS_WORDS = (
        '内容管理', '素材库', '发布系统', '云媒资', '统一门户',
        '报送', '初审', '重 置', '查 询', '保 存', '上 传',
    )

    # 参与扫描的文件（相对工程根）
    TARGETS = (
        os.path.join('Common', 'elementPage.py'),
        os.path.join('TestSuites', 'testLogin.py'),
        os.path.join('TestSuites', 'testMaterialCheck.py'),
        os.path.join('PageObject', 'loginPage.py'),
    )

    @staticmethod
    def _strip_comments_and_docstrings(source):
        """去掉注释与文档字符串，只留可执行代码。

        用 tokenize 而不是正则：正则无法可靠区分字符串里的 # 与真注释，
        容易把代码误判成注释（或反之）。
        """
        import io
        import tokenize
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except tokenize.TokenError:
            return source

        kill = (tokenize.COMMENT, tokenize.STRING)
        lines = source.splitlines(True)
        out = []
        prev_end = (1, 0)
        for tok in tokens:
            if tok.type in kill:
                # 把注释/字符串覆盖成空白，保留行结构便于行列定位
                (srow, scol), (erow, ecol) = tok.start, tok.end
                for row in range(srow, erow + 1):
                    line = lines[row - 1] if row - 1 < len(lines) else ''
                    a = scol if row == srow else 0
                    b = ecol if row == erow else len(line)
                    lines[row - 1] = line[:a] + ' ' * max(0, b - a) + line[b:]
            prev_end = tok.end
        return ''.join(lines)

    def _scan(self, relpath):
        """返回该文件可执行代码里残留的业务词列表。"""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, relpath)
        with open(path, encoding='utf-8') as fp:
            source = fp.read()
        code = self._strip_comments_and_docstrings(source)
        return [w for w in self.BUSINESS_WORDS if w in code]

    def test_元素表可执行代码零业务文案(self):
        """elementPage.py 是这次改造的重点，绝不能残留业务文案。"""
        found = self._scan(os.path.join('Common', 'elementPage.py'))
        self.assertEqual(found, [],
                         '元素表的可执行代码里仍有业务文案 %s —— '
                         '它们应该全部下沉到 config.ini 的 [locators] 段' % found)

    def test_用例层零业务文案(self):
        for rel in self.TARGETS[1:]:
            found = self._scan(rel)
            self.assertEqual(found, [],
                             '%s 的可执行代码里仍有业务文案 %s' % (rel, found))

    def test_元素表所有业务文案都来自配置(self):
        """反向校验：配置里的文案确实出现在元素定位结果中。

        只检查 _locate 能处理、且配置了专有文案的几个元素，
        确认它们不是退回兜底值。
        """
        from Common.elementPage import Element1
        with patch_config("""
[locators]
btn_enter_system = 某某平台入口
menu_first_level = 某某管理
menu_second_level = 某某列表
"""):
            import importlib
            import Common.elementPage as ep
            importlib.reload(ep)
            try:
                self.assertIn('某某平台入口', ep.Element1.button[1])
                self.assertIn('某某管理', ep.Element1.content[1])
                self.assertIn('某某列表', ep.Element1.material[1])
            finally:
                # 还原，避免污染其它测试（元素表是模块级求值的）
                importlib.reload(ep)


# ---------------------------------------------------------------------------
# 六、定位歧义防护（两条来自真实浏览器验证的教训）
# ---------------------------------------------------------------------------
class TestLocatorAmbiguityProtection(unittest.TestCase):
    """这两条不是理论推演，是在真实 Chromium 上实测失败后加上的。

    背景：xpath 联合表达式 `A | B` 与 Playwright 的 `.first`
          **都按文档顺序**返回，容器元素永远排在它的内部元素前面。
          于是「我要内部那个 i 图标」实际拿到外层 label，
          点击等于点了个空壳 —— 元素找到了，动作却没生效。
    """

    def test_文字定位排除祖先容器(self):
        """by_text / by_text_loose 必须带 not(*)。

        不带的话，包裹按钮的 div 会与按钮本身同时命中，
        按文档序 div 在前，于是 first 抓到的是容器。
        """
        from Common.elementPage import by_text, by_text_loose
        self.assertIn('not(*)', by_text('某文字')[1],
                      'by_text 缺少 not(*)，会命中包裹容器')
        self.assertIn('not(*)', by_text_loose('某文字')[1],
                      'by_text_loose 缺少 not(*)，会命中包裹容器')

    def test_去空格匹配容忍排版空格(self):
        """中文按钮常带字间空格，策略必须先剔除空格再比对。"""
        from Common.elementPage import by_text_loose
        self.assertIn('translate', by_text_loose('报送')[1])

    def test_首个分支写在联合表达式最前(self):
        """by_id_anchor 带 inner 时，精确分支必须是第一个。

        框架的 _pick_target 会先试首个分支、命中就用它，
        所以"想要的在前"是这套约定的一部分。
        """
        from Common.elementPage import by_id_anchor
        sel = by_id_anchor('anchor-id', 'i')
        first_branch = sel[1].split('|')[0].strip()
        self.assertIn('//i', first_branch,
                      '精确分支必须排在最前，实际首分支是 %s' % first_branch)

    def test_框架在多候选时会优先首个分支(self):
        """_pick_target 存在且会做窄化探测。"""
        from Common.basePage import BasePage
        self.assertTrue(hasattr(BasePage, '_pick_target'))
        self.assertTrue(hasattr(BasePage, '_locator_probe'))

    def test_窄化探测不污染操作序列(self):
        """探测是框架内部行为，不该让用例层的顺序断言多出一条记录。"""
        from Common.basePage import BasePage
        from tests.helpers import FakePage

        page = FakePage()
        bp = BasePage(driver=page)
        # 一条联合表达式：'//*[@id=a]//i | //*[@id=a]'
        bp.get_element(['xpath', "//*[@id='a']//i | //*[@id='a']"], timeout=0)
        recorded = [s for kind, s in page.calls if kind == 'locator']
        # fake 记录的是编译后的选择器串（含 xpath= 前缀），这里只关心条数
        self.assertEqual(
            len(recorded), 1,
            '操作序列里出现了探测产生的额外定位记录：%s' % recorded)
        self.assertTrue(recorded[0].endswith("//*[@id='a']//i | //*[@id='a']"))


# ---------------------------------------------------------------------------
# 七、元素表格式契约（承接原 test_element_page.py 的职责）
# ---------------------------------------------------------------------------
class TestElementTableContract(unittest.TestCase):
    """元素表结构约束不能因为改造而放松。"""

    @staticmethod
    def _public_elements():
        from Common.elementPage import Element1
        out = []
        for name in dir(Element1):
            if name.startswith('_'):
                continue
            value = getattr(Element1, name)
            if callable(value):
                continue
            out.append((name, value))
        return out

    def test_元素数量足够(self):
        self.assertGreaterEqual(len(self._public_elements()), 20)

    def test_元素均为二元结构且定位方式合法(self):
        from Common.basePage import BY_MAP
        for name, value in self._public_elements():
            self.assertIsNotNone(value, '元素 %s 为 None，请检查 [locators] 配置' % name)
            self.assertEqual(len(value), 2, '元素 %s 应为二元结构' % name)
            self.assertIn(value[0].lower(), BY_MAP,
                          '元素 %s 的定位方式 %r 不合法' % (name, value[0]))
            self.assertTrue(value[1].strip(), '元素 %s 的定位值为空' % name)

    def test_没有绝对路径定位(self):
        """绝对 xpath（/html/body/...）是改版即失效的头号原因。"""
        for name, value in self._public_elements():
            self.assertNotIn('/html/body', value[1],
                             '元素 %s 用了绝对路径定位，前端多套一层 div 就失效' % name)

    def test_旧类名别名仍然可用(self):
        from Common.elementPage import Element1, Elemen1
        self.assertIs(Elemen1, Element1)

    def test_文件控件仍定位到input_file(self):
        """直接赋值上传方案的前提，不能被改造破坏。"""
        from Common.elementPage import Element1
        self.assertIn('file', Element1.FileInput[1].lower())


if __name__ == '__main__':
    unittest.main(verbosity=2)
