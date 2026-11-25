"""用户协议 / 商家协议（模板，正式对外前请让律师按经营地改写）。"""

from __future__ import annotations

from html import escape

VERSION = "2026-11"

USER_TERMS = """
一、这是什么
本服务是一个可自己部署的 Agent 工具，能读文件、跑脚本、搜网页、生成图片/语音/视频。模型与存储由部署者提供。

二、账号与安全
你需要自己保管密码、恢复码和 API 密钥。泄露后请立刻在「账号与安全」里重置或吊销。不要把账号借给不信任的人。

三、使用规则
不得用它做违法、侵权、攻击、诈骗、传播恶意代码或生成违法内容的事。你上传和生成的内容由你负责。

四、AI 输出
模型输出可能不准确，也可能引用错误信息。涉及钱、医疗、法律、安全的关键决定，请自己核实。

五、数据
会话、文件和密钥存在部署者的服务器上。删除账号或重置会清空账号数据；文件是否保留取决于部署者设置。

六、服务变更与终止
部署者可以暂停或关闭服务。违规使用、攻击服务、欠费（商家版）时，账号可能被停用。

七、责任
服务按现状提供，不对因模型输出、服务中断或数据丢失造成的间接损失负责；法律强制规定的责任除外。
"""

MERCHANT_TERMS = """
一、角色
你是部署这台服务的商家，负责模型 Key、服务器、域名、收款、发票、客服和合规；平台代码只提供工具能力。

二、套餐与兑换码
你自行定价、发放套餐和兑换码。兑换码默认一码一次、谁先兑谁得；请把码当作现金保管，泄露后无法追回。

三、收款与退款
自动收款需要你自己的支付商户号。收款、退款、发票、对账由你承担；用兑换码销售时也要保留订单记录。

四、用户数据
会员的文件存放在独立目录，但仍在你的服务器上。你要告知用户数据用途、保存期限，并做好备份与访问控制。

五、内容与合规
你要确保模型供应商允许对外提供服务，并按经营地要求办理备案、许可、算法备案/安全评估、用户协议和隐私政策。不得把服务用于违法内容。

六、服务等级
你要自行评估并发、限速、额度和成本。不要向用户承诺本代码没有的 SLA、退款或赔付。

七、责任
因你的定价、收款、内容、数据泄露、违规经营产生的责任由你承担；因使用本代码造成的间接损失，代码作者不承担，法律强制规定的除外。
"""


def render(kind: str) -> str:
    if kind == "merchant":
        title = "商家协议"
        body = MERCHANT_TERMS
    else:
        title = "用户协议"
        body = USER_TERMS
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>
body{{margin:0;padding:20px;background:#f6f4ef;color:#2b2a26;font:16px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}
main{{max-width:760px;margin:0 auto;background:#fff;border:1px solid #e6e1d8;border-radius:14px;padding:20px 22px}}
h1{{font-size:22px;margin:0 0 6px}} .ver{{color:#8a8378;font-size:13px;margin-bottom:18px}}
p{{white-space:pre-wrap;margin:0}} a{{color:#9a5b2b}}
</style></head><body><main>
<h1>{escape(title)}</h1>
<div class="ver">版本 {VERSION} · 模板，正式对外请让律师审阅</div>
<p>{escape(body.strip())}</p>
<p style="margin-top:18px"><a href="/login">返回登录</a></p>
</main></body></html>"""


def html(kind: str) -> str:
    return render(kind)
