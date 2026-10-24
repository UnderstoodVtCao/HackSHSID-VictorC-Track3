# -*- coding: utf-8 -*-
"""
messages.py
===========
根据研判结果生成面向三类受众的消息草稿。

设计原则（对应 05_运营规则 与 07_人工反馈 的改进要求）
------------------------------------------------------
1. **同一事件、三种措辞**：运控版给数据与依据；机场协调版给协作请求；
   旅客版只给可公开事实与安抚信息。
2. **旅客稿必须是「风险提示」而非「状态通知」**（REC002 反馈：原草案易被
   误解为已延误 → 现版本明确区分「风险提示」与「正式状态」，并以正式通知为准）。
3. **每份草稿标注「草案·需人工审核」**，禁止自动发送（RULE005）。
4. **全部内容带模拟标识**（RULE010）。
"""

from .engine import Assessment


SIM_TAG = '【模拟演练】'
DRAFT_TAG = '【草案·需人工审核，禁止自动发送】'


def _basis_summary(a: Assessment) -> str:
    """把判断依据压缩成一两句人话，只引用已核实事实。"""
    parts = [d for d, _ in a.facts if '【事实】' in d]
    return '；'.join(p.replace('【事实】', '') for p in parts[:3])


def ops_message(a: Assessment, window: str) -> str:
    """内部运营提示：技术口径，含数据、来源与规则。"""
    signals = '；'.join(f"{s.name}{s.score}分" for s in a.signals) or '无评分（降级处理）'
    return (
        f"{SIM_TAG}{DRAFT_TAG}\n"
        f"内部运营提示：{window}风险等级为「{a.risk_level}」"
        f"（综合评分 {a.score}：{signals}）。\n"
        f"依据：{_basis_summary(a)}。\n"
        f"行动：{a.first_action} {a.follow_actions}\n"
        f"升级条件：{a.escalate_condition}\n"
        f"必须人工决定：{a.human_decisions}。"
    )


def airport_coord_message(a: Assessment, window: str) -> str:
    """机场协调信息：对等的协作请求，说明需要对方确认什么。"""
    return (
        f"{SIM_TAG}{DRAFT_TAG}\n"
        f"协调请求：请贵方协助确认{window}的最新天气更新、"
        f"地面保障能力及需要重点协调的时段。\n"
        f"背景：{window}，当前风险等级为「{a.risk_level}」。"
        f"{_basis_summary(a)}。\n"
        f"我方将依据贵方确认信息更新研判；如有正式运行状态变化请即时同步。"
    )


def passenger_message(a: Assessment, window: str) -> str:
    """旅客信息草案：风险提示语气，明确以正式通知为准（REC002 改进点）。"""
    if a.risk_level == '低':
        body = (f"{window.split("，")[0]}未来时段可能受天气变化影响，目前航班运行总体正常，"
                f"暂无需旅客采取额外行动。")
    else:
        body = (f"{window.split("，")[0]}未来时段可能受天气影响，部分航班可能出现调整。"
                f"当前尚无正式的航班延误或取消决定。")
    return (
        f"{SIM_TAG}{DRAFT_TAG}\n"
        f"旅客信息草案：{body}\n"
        f"以上仅为风险提示，不是正式的航班状态通知；"
        f"航班状态请以航空公司正式通知和机场显示屏为准，请留意后续更新。"
        f"发布前须经旅客服务负责人人工审核（RULE005）。"
    )


def build_messages(a: Assessment, window: str, llm_client=None) -> dict:
    """
    一次生成三类消息，返回 {受众: 文本}。

    llm_client 为 None（默认）时输出模板稿，完全离线；
    提供客户端时尝试 LLM 改写，但改写稿必须通过 llm_layer.validate_output
    机器校验，否则回退模板稿——保证任何情况下不出现确定性表述。
    """
    msgs = {
        '内部运营提示': ops_message(a, window),
        '机场协调信息': airport_coord_message(a, window),
        '旅客信息草案': passenger_message(a, window),
    }
    if llm_client is None:
        return msgs

    from .llm_layer import rewrite_message
    structured = {
        '适用窗口': window,
        '风险等级': a.risk_level,
        '综合评分': a.score,
        '事实依据': [d for d, _ in a.facts],
        '可能影响': a.impact,
        '第一步行动': a.first_action,
        '后续行动': a.follow_actions,
        '升级条件': a.escalate_condition,
        '信息缺口': a.unknowns,
        '必须人工决定': a.human_decisions,
    }
    for audience, template in msgs.items():
        rewritten = rewrite_message(llm_client, template, audience, structured)
        if rewritten is not None:
            msgs[audience] = rewritten
    return msgs
