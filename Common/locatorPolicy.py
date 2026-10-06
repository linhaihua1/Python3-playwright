# -*- coding: utf-8 -*-
"""
==========================================================================
 元素定位策略检查器（LocatorPolicy）
==========================================================================

【一句话说明】
    静态扫描 elementPage.py，找出脆弱的定位写法（绝对 xpath、硬编码索引、
    深层嵌套），输出中文报告并给出替换建议。在 CI 里作为质量门禁：
    新增脆弱定位时直接告警。

【在框架中的位置】
    python -m Common.locator_policy           # 扫描全工程元素表，输出报告
    python -m Common.locator_policy --ci      # CI 模式：发现脆弱定位退出码 1

【检查规则】
    1. 绝对路径 xpath：以 /html 开头，或从根节点开始的长路径
       反例：/html/body/div[4]/div/div[2]/...
       建议：用相对定位 // 或按属性定位

    2. 硬编码索引：div[4] / li[8] / td[8] 这类数字索引
       反例：//tr[1]/td[8]/div/button[3]
       风险：前端加一列、调个顺序就失效
       建议：按文本或属性定位

    3. 深层嵌套：xpath 层级超过 6 层
       反例：//*[@id='app']/div/div[2]/section/section/main/div[1]/form/...
       风险：中间任何一层结构变化都会断
       建议：缩短路径，用更靠近目标的锚点

    4. 仅按位置定位：following::div[4] / preceding::span[1]
       风险：同层级元素增减就失效
       建议：用 contains(text(), 'xxx') 按文字定位

【用法】
    # 本地扫描（只报告不阻断）
    python -m Common.locator_policy

    # CI 门禁（发现 P0 级脆弱定位退出码 1）
    python -m Common.locator_policy --ci

    # 也可以在 Python 里调用
    from Common.locator_policy import scan_element_page
    report = scan_element_page('Common/elementPage.py')
    for item in report:
        print(item['severity'], item['name'], item['issue'])
==========================================================================
"""

import ast
import os
import re
import sys

from Common.log import log1

# 规则严重级别
SEVERITY_P0 = 'P0'   # 必改：绝对路径 /html 开头
SEVERITY_P1 = 'P1'   # 建议改：硬编码索引 / 深层嵌套
SEVERITY_P2 = 'P2'   # 可选优化：following/preceding 轴定位


def _check_xpath(xpath):
    """检查一条 xpath，返回问题列表（每项是 (severity, issue, suggestion)）。"""
    issues = []

    # 规则 1：绝对路径 /html 开头
    if xpath.startswith('/html') or xpath.startswith('/body'):
        issues.append((SEVERITY_P0, '绝对路径定位（从 /html 或 /body 开始）',
                       '改用相对定位：以 // 开头，或按 id/class/属性定位'))

    # 规则 2：硬编码数字索引 [N]（排除 [1] 这种偶尔可接受的，但 [3] 以上基本都有问题）
    indexed = re.findall(r'\[(\d+)\]', xpath)
    bad_indices = [i for i in indexed if int(i) > 1]
    if bad_indices:
        issues.append((SEVERITY_P1,
                       '硬编码索引 [%s]' % ', '.join(bad_indices),
                       '按文本或属性定位，如 //span[text()="报送"] 或 //button[@class="submit"]'))

    # 规则 3：深层嵌套（/ 分隔超过 6 层）
    depth = xpath.count('/')
    if depth > 8:
        issues.append((SEVERITY_P1,
                       'xpath 层级过深（%d 层），中间任一节点变化即失效' % depth,
                       '缩短路径，用更靠近目标的锚点（如最近的有 id/class 的祖先节点）'))

    # 规则 4：following:: / preceding:: 轴定位带索引
    axis_indexed = re.findall(r'(?:following|preceding)::\w+\[\d+\]', xpath)
    if axis_indexed:
        issues.append((SEVERITY_P2,
                       '轴定位带索引：%s' % ', '.join(axis_indexed),
                       '用 contains(text(), "按钮文字") 按文字定位更稳定'))

    return issues


def scan_element_page(filepath):
    """扫描一个元素表文件，返回问题列表。

    :return: [{'name': 变量名, 'xpath': 定位值, 'severity': 级别,
               'issue': 问题描述, 'suggestion': 建议}, ...]
    """
    if not os.path.exists(filepath):
        log1.error('定位策略检查：文件不存在 %s' % filepath)
        return []

    with open(filepath, 'r', encoding='utf-8') as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        log1.error('定位策略检查：文件语法错误 %s —— %s' % (filepath, e))
        return []

    results = []
    for node in ast.walk(tree):
        # 找类定义里的「变量 = ['xpath', '...']」赋值
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                var_name = target.id
                if not isinstance(node.value, ast.List) or len(node.value.elts) < 2:
                    continue
                # 第二个元素应该是定位值字符串
                val_node = node.value.elts[1]
                if val_node and isinstance(val_node, ast.Constant) and isinstance(val_node.value, str):
                    xpath = val_node.value
                    issues = _check_xpath(xpath)
                    for sev, issue, sug in issues:
                        results.append({
                            'name': var_name,
                            'xpath': xpath,
                            'severity': sev,
                            'issue': issue,
                            'suggestion': sug,
                        })
    return results


def scan_all():
    """扫描工程内所有元素表文件，返回合并后的报告。"""
    import getcwd
    base = getcwd.get_cwd()
    candidates = [
        os.path.join(base, 'Common', 'elementPage.py'),
    ]
    # 也扫 PageObject 目录下可能的元素表
    po_dir = os.path.join(base, 'PageObject')
    if os.path.isdir(po_dir):
        for f in os.listdir(po_dir):
            if 'element' in f.lower() and f.endswith('.py'):
                candidates.append(os.path.join(po_dir, f))

    all_results = []
    for path in candidates:
        all_results.extend(scan_element_page(path))
    return all_results


def print_report(results, ci_mode=False):
    """打印检查报告，返回是否有 P0 级问题。"""
    if not results:
        print('✅ 定位策略检查通过：未发现脆弱定位')
        return False

    p0 = [r for r in results if r['severity'] == SEVERITY_P0]
    p1 = [r for r in results if r['severity'] == SEVERITY_P1]
    p2 = [r for r in results if r['severity'] == SEVERITY_P2]

    print('')
    print('==================== 定位策略检查报告 ====================')
    print('共发现 %d 处脆弱定位（P0=%d, P1=%d, P2=%d）'
          % (len(results), len(p0), len(p1), len(p2)))
    print('')

    for sev, label in [(SEVERITY_P0, '🔴 P0 必改'), (SEVERITY_P1, '🟡 P1 建议改'),
                       (SEVERITY_P2, '🟢 P2 可选优化')]:
        items = [r for r in results if r['severity'] == sev]
        if not items:
            continue
        print('--- %s（%d 处）---' % (label, len(items)))
        for r in items:
            print('  [%s] %s' % (r['name'], r['issue']))
            print('         xpath: %s' % r['xpath'][:80])
            print('         建议: %s' % r['suggestion'])
        print('')

    print('==========================================================')
    if ci_mode and p0:
        print('❌ CI 门禁未通过：存在 %d 处 P0 级脆弱定位，请修复后再提交' % len(p0))
        return True
    return False


if __name__ == '__main__':
    ci = '--ci' in sys.argv
    results = scan_all()
    has_p0 = print_report(results, ci_mode=ci)
    sys.exit(1 if (ci and has_p0) else 0)
