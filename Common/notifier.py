# -*- coding: utf-8 -*-
"""
==========================================================================
 执行结果通知（钉钉 / 企业微信 / 通用 webhook）
==========================================================================

【一句话说明】
    测试跑完后，把「通过/失败、用例数、耗时、报告链接」推送到群机器人，
    让团队不用打开 Jenkins 也能第一时间知道结果。

【在框架中的位置】
    run.py --notify  ->  Common/notifier.py（本文件）->  钉钉/企业微信机器人

【配置（config.ini -> [notify]）】
    enabled   = true          # 也可直接用 --notify 开关
    webhook   = https://oapi.dingtalk.com/robot/send?access_token=xxx
    # 钉钉加签密钥（机器人安全设置选了「加签」时必填），不启用加签则留空
    secret    =
    # 推送标题前缀，方便区分是哪个系统/哪条流水线
    title     = UI 自动化测试

【环境变量覆盖】
    UI_NOTIFY_WEBHOOK / UI_NOTIFY_SECRET / UI_NOTIFY_TITLE

【支持的机器人】
    自动按 webhook 域名识别：
      oapi.dingtalk.com   -> 钉钉（文本消息，支持加签）
      qyapi.weixin.qq.com -> 企业微信（文本消息）
      其它                -> 通用 JSON POST（{"text": "..."}）
==========================================================================
"""

import hashlib
import hmac
import base64
import json
import time
import urllib.parse
import urllib.request

from Common.config import Config
from Common.log import log1

# 通知 HTTP 请求的技术默认超时（秒），可被 [tuning] notify_timeout 覆盖
DEFAULT_NOTIFY_TIMEOUT = 10


def _is_dingtalk(webhook):
    return 'oapi.dingtalk.com' in webhook


def _is_wecom(webhook):
    return 'qyapi.weixin.qq.com' in webhook


def _ding_sign(secret):
    """钉钉加签：返回 (timestamp, sign)，拼到 webhook 上。"""
    timestamp = str(round(time.time() * 1000))
    string_to_sign = '%s\n%s' % (timestamp, secret)
    hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'),
                         digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def _post(url, payload, timeout=None):
    """发一个 JSON POST，返回 (ok, message)。网络异常不抛，只记日志。

    :param timeout: 秒；不传则取 [tuning] notify_timeout（默认 10）
    """
    if timeout is None:
        timeout = Config.get_int('tuning', 'notify_timeout', DEFAULT_NOTIFY_TIMEOUT)
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8', errors='replace')
            return 200 <= resp.status < 300, body
    except Exception as e:           # noqa: BLE001 - 通知失败绝不能影响测试结果
        log1.error('通知发送失败：%s' % e)
        return False, str(e)


def _build_message(summary, title):
    """把执行汇总整理成一段可读文本。"""
    code = summary.get('exit_code', 0)
    status = '✅ 通过' if code == 0 else ('❌ 失败' if code == 1 else '⚠️ 异常中断')
    lines = [
        '【%s】%s' % (title, status),
        '用例：%d 条（失败 %d / 错误 %d / 跳过 %d）'
        % (summary.get('tests_run', 0), summary.get('failures', 0),
           summary.get('errors', 0), summary.get('skipped', 0)),
        '耗时：%.1f 秒' % summary.get('duration', 0.0),
    ]
    if summary.get('report'):
        lines.append('报告：%s' % summary['report'])
    return '\n'.join(lines)


def notify(summary, webhook=None, secret=None, title=None):
    """推送执行结果到群机器人。

    :return: True 发送成功；False 未配置或发送失败（均不影响主流程）
    """
    webhook = webhook or Config.get('notify', 'webhook', '')
    if not webhook:
        log1.info('未配置通知 webhook（config.ini [notify] webhook），跳过推送')
        return False
    secret = secret if secret is not None else Config.get('notify', 'secret', '')
    # 标题属于业务文案：优先 [notify] title，缺省回退到报告标题（同样来自配置），
    # 代码里不留任何写死的中文默认值。
    title = title or Config.get('notify', 'title', '') or Config.get('report', 'title', '')

    text = _build_message(summary, title)
    url = webhook

    if _is_dingtalk(webhook):
        if secret:
            ts, sign = _ding_sign(secret)
            sep = '&' if '?' in webhook else '?'
            url = '%s%stimestamp=%s&sign=%s' % (webhook, sep, ts, sign)
        payload = {'msgtype': 'text', 'text': {'content': text}}
    elif _is_wecom(webhook):
        payload = {'msgtype': 'text', 'text': {'content': text}}
    else:
        payload = {'text': text, 'status': summary.get('exit_code', 0)}

    ok, body = _post(url, payload)
    log1.info('通知已发送，结果：%s，响应：%s' % (ok, body))
    return ok
