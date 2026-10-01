# -*- coding: utf-8 -*-
"""
单元测试：Common/elementPage.py（元素表）

元素表是"前端改版时唯一需要改的文件"，一旦格式写错，
表现是所有用例都定位失败，很难排查。所以这里用测试把格式约束钉死：
    - 每个元素必须是 [定位方式, 定位值] 的二元结构
    - 定位方式必须是 basePage.BY_MAP 支持的那几种（否则直接失效）
"""

import unittest

from Common.basePage import BY_MAP
from Common.elementPage import Element1, Elemen1


def _public_elements():
    """取出元素表里所有公开元素（排除私有属性与方法）。"""
    items = []
    for name in dir(Element1):
        if name.startswith('_'):
            continue
        value = getattr(Element1, name)
        if callable(value):
            continue
        items.append((name, value))
    return items


class TestElementFormat(unittest.TestCase):
    """元素描述的结构约束。"""

    def test_元素表非空(self):
        self.assertGreaterEqual(len(_public_elements()), 15)

    def test_每个元素都是二元结构(self):
        for name, value in _public_elements():
            self.assertIsInstance(value, (list, tuple), '元素 %s 应为列表/元组' % name)
            self.assertEqual(len(value), 2, '元素 %s 应为 [定位方式, 定位值] 两个元素' % name)

    def test_每个元素都是字符串(self):
        for name, value in _public_elements():
            self.assertIsInstance(value[0], str, '元素 %s 的定位方式应为字符串' % name)
            self.assertIsInstance(value[1], str, '元素 %s 的定位值应为字符串' % name)

    def test_定位方式都被BYMAP支持(self):
        for name, value in _public_elements():
            self.assertIn(
                value[0].lower(), BY_MAP,
                '元素 %s 的定位方式 %r 不在 BY_MAP 中，定位必然失败' % (name, value[0]),
            )

    def test_定位值非空(self):
        for name, value in _public_elements():
            self.assertTrue(value[1].strip(), '元素 %s 的定位值为空' % name)


class TestElementCoverage(unittest.TestCase):
    """业务链上必须存在的关键元素。"""

    REQUIRED = (
        'Username', 'Password', 'Sign_button',                    # 登录
        'button',                                                  # 进生产系统
        'content', 'material',                                     # 素材库导航
        'FileInput', 'videoauthor', 'phone', 'reporting', 'upload',  # 上传
        'keyword', 'querybutton', 'replacement',                   # 查询
        'submission', 'first_trial', 'person_of_first_instance', 'save',  # 报送
    )

    def test_关键元素齐全(self):
        for name in self.REQUIRED:
            self.assertTrue(hasattr(Element1, name), '缺少关键元素：%s' % name)

    def test_文件上传控件使用input_file(self):
        """直接赋值上传方案的前提：定位到 input[type=file] 隐藏控件。"""
        self.assertIn('file', Element1.FileInput[1].lower())


class TestCompatAlias(unittest.TestCase):
    """历史兼容别名。"""

    def test_旧类名Elemen1指向同一个类(self):
        self.assertIs(Elemen1, Element1)

    def test_别名获取到的元素与正式类一致(self):
        self.assertEqual(Elemen1.Username, Element1.Username)


if __name__ == '__main__':
    unittest.main(verbosity=2)
