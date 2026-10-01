# -*- coding: utf-8 -*-
"""
==========================================================================
 测试报告邮件发送（新人阅读顺序：第 12 个，需要发邮件时再看）
==========================================================================

【一句话说明】
    把 Report/ 里的 HTML 测试报告作为附件，通过 QQ 邮箱 SMTP 发给配置的收件人。

【在框架中的位置】
    Common/runner.py  的 send_mail=True 分支
        └─> Common/sentMail.py  <- 本文件
              ├─> 读 config.ini [sender]    发件人邮箱 + SMTP 授权码
              └─> 读 config.ini [addressed] 收件人（键名随意，值是邮箱）

【怎么启用】
    方式一（推荐）：python run.py --mail
    方式二：直接改 runner.run(send_mail=True)
    方式三：单独测试这个功能：python -m Common.sentMail

【配置怎么写】
    [sender]
    username = 张三            <- 邮件里显示的发件人昵称
    email    = zhangsan@qq.com
    password = xxxxxxxxxxxx    <- 注意！这是 SMTP 授权码，不是 QQ 登录密码
    [addressed]
    李四 = lisi@qq.com         <- 键是姓名（仅作标识），值是邮箱

【新人注意】
    1. QQ 邮箱要先去「设置 -> 账户」开启 SMTP 服务并生成授权码，直接用登录密码会认证失败。
    2. 附件名非 ASCII 时会走 filename=('gbk', '', 文件名) 这个特殊写法，
       这是 email 库处理中文文件名的标准做法，不要改成普通字符串。
    3. 发信失败不会影响测试结果和报告生成 —— 本模块只记录日志并返回 False，
       不会向上抛异常，这是刻意设计（报告才是主角）。
==========================================================================
"""

import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart   # 可带附件的复合邮件
from email.mime.text import MIMEText             # 纯文本正文 / 附件内容
from email.utils import formataddr               # 把"昵称+邮箱"格式化成标准写法

from Common.config import Config
from Common.dir_config import htmlreport_dir
from Common.log import log1

# SMTP 服务器与端口的兜底默认值（QQ 邮箱 SSL）——技术参数。
# 真实取值优先来自 config.ini [sender] smtp_host / smtp_port，可被环境变量覆盖，
# 换其它邮箱（163 / 企业邮箱等）只需改配置、不用改代码。
SMTP_HOST = 'smtp.qq.com'
SMTP_PORT = 465

# 邮件主题/正文的"代码默认模板"只含占位符、不含任何业务文案（技术兜底）。
# 想让邮件带中文说明，请在 config.ini -> [sender] mail_subject / mail_body 配置，
# 两个值都支持 {date}（当天日期）与 {filename}（报告文件名）占位符。
DEFAULT_SUBJECT_TEMPLATE = '{date} {filename}'


def find_latest_report():
    """找出 Report/ 目录下修改时间最新的那个 .html 报告。

    :return: 报告绝对路径；目录不存在或没有报告时返回 None

    用途：调用方没指定报告时（例如手工执行 python -m Common.sentMail），
    自动挑一份最新的发出去，不用手输文件名。
    """
    if not os.path.isdir(htmlreport_dir):
        return None
    files = [
        os.path.join(htmlreport_dir, name)
        for name in os.listdir(htmlreport_dir)
        if name.lower().endswith('.html')
    ]
    # getmtime 返回文件最后修改时间，取最大的就是最新报告
    return max(files, key=os.path.getmtime) if files else None


def sent_mail(report_path=None):
    """把测试报告作为附件发送邮件。

    :param report_path: 要发送的报告路径；不传则自动取 Report/ 下最新的报告
    :return: 发送成功 True / 失败或配置不全 False（不抛异常）

    执行步骤：校验附件 -> 校验发件人 -> 校验收件人 -> 组装邮件 -> SMTP 发送
    """
    # ---- 1. 附件校验：报告必须存在，否则没有发的意义 ----
    report_path = report_path or find_latest_report()
    if not report_path or not os.path.exists(report_path):
        log1.error('邮件发送失败：未找到测试报告附件')
        return False

    # ---- 2. 发件人配置校验 ----
    sender_email = Config.get('sender', 'email')
    sender_password = Config.get('sender', 'password')
    sender_name = Config.get('sender', 'username')
    # 提示：config.ini 里默认是「发件人邮箱 / SMTP授权码」这类占位符，
    # 没改真实值就会走到这里并打日志返回，而不是抛异常
    if not sender_email or not sender_password:
        log1.error('邮件发送失败：未配置 [sender] email / password')
        return False

    # ---- 3. 收件人：段下的键名由使用者自定，所以遍历所有键取值 ----
    users = Config.options('addressed')
    addressed_emails = [Config.get('addressed', key) for key in users]
    addressed_emails = [item for item in addressed_emails if item]   # 过滤空值
    if not addressed_emails:
        log1.error('邮件发送失败：未配置收件人 [addressed]')
        return False

    filename = os.path.basename(report_path)
    rq = time.strftime('%Y%m%d', time.localtime(time.time()))   # 当天日期，用于主题

    # 主题/正文模板来自配置（业务文案不写死在代码里），{date}/{filename} 占位替换
    subject_tpl = Config.get('sender', 'mail_subject', DEFAULT_SUBJECT_TEMPLATE)
    body_tpl = Config.get('sender', 'mail_body', '')            # 不配就没有正文文字

    try:
        # ---- 4. 组装邮件 ----
        message = MIMEMultipart()
        message['From'] = formataddr([sender_name, sender_email])   # 显示成「张三 <xx@qq.com>」
        message['To'] = ';'.join(addressed_emails)                  # 多个收件人用分号连接
        message['Subject'] = subject_tpl.replace('{date}', rq).replace('{filename}', filename)
        body = body_tpl.replace('{date}', rq).replace('{filename}', filename)
        message.attach(MIMEText(body, 'plain', 'utf-8'))            # 正文

        # 读报告内容并作为附件：base64 编码 + 通用二进制类型
        with open(report_path, 'rb') as f:
            att = MIMEText(f.read(), 'base64', 'utf-8')
        att['Content-Type'] = 'application/octet-stream'
        # 中文附件名的标准写法：先按 gbk 编码再交给 email 库
        att.add_header('Content-Disposition', 'attachment', filename=('gbk', '', filename))
        message.attach(att)
        log1.info('读取附件：%s' % report_path)

        # ---- 5. 连接 SMTP 并发送（默认 SSL，服务器/端口从配置读取）----
        smtp_host = Config.get('sender', 'smtp_host', SMTP_HOST)
        smtp_port = Config.get_int('sender', 'smtp_port', SMTP_PORT)
        server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        server.login(sender_email, sender_password)     # 注意这里用的是授权码
        server.sendmail(sender_email, addressed_emails, message.as_string())
        server.quit()                                   # 发完记得断连
        log1.info('邮件发送成功，收件人：%s' % ';'.join(addressed_emails))
        return True
    except Exception:
        # 网络/认证/附件等各种异常统一兜底，只记日志不向上抛
        log1.error('邮件发送失败', exc_info=1)
        return False


# 单独运行本文件即可手工测试发信：python -m Common.sentMail
if __name__ == '__main__':
    sent_mail()
