# -*- coding: utf-8 -*-
"""
llm_layer.py
============
可选的 LLM 增强层。默认完全离线（不配置环境变量时本模块不产生任何效果，
系统行为与纯模板版完全一致）。

设计原则（对应我们"AI 只做决策支持"的总架构）
------------------------------------------------
1. LLM 只碰语言，不碰判断：风险等级、评分、规则约束永远由 engine.py 计算。
2. 输出必须过机器校验（validate_output）：确定性表述、丢失草案标注一律回退。
3. 任何异常（无网络、无密钥、超时、返回不合规）都静默回退到模板版本，
   保证演示与提交永远可用。

启用方式（可选，不影响默认运行）
-------------------------------
设置环境变量后即可启用消息改写：
    ASSISTANT_LLM_API_KEY=你的密钥
    ASSISTANT_LLM_BASE_URL=https://api.deepseek.com/v1   （可选，默认 OpenAI 兼容路径）
    ASSISTANT_LLM_MODEL=deepseek-chat                      （可选）
"""

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# 输出校验：LLM 改写稿的机器初审（对应 RULE005 / RULE009 的自动化部分）
# ---------------------------------------------------------------------------

# 确定性 / 越权表述黑名单：出现即判违规，回退模板稿
BANNED_PATTERNS = [
    '已延误', '已经延误', '一定延误', '确定延误', '肯定延误', '保证', '必然',
    '已取消', '已经取消', '确定取消', '已改签', '已经起飞',
    '正式通知：', '【正式通知】', '请立即退票',
]

# 草案标注是 RULE005 的硬要求，缺失即违规
REQUIRED_MARKERS = ['草案']


@dataclass
class ValidationResult:
    passed: bool
    violations: list


def validate_output(text: str) -> ValidationResult:
    """对一段拟发布的消息做机器初审。"""
    violations = [p for p in BANNED_PATTERNS if p in text]
    for m in REQUIRED_MARKERS:
        if m not in text:
            violations.append(f'缺少必需标注「{m}」')
    return ValidationResult(passed=not violations, violations=violations)


# ---------------------------------------------------------------------------
# LLM 客户端（OpenAI 兼容接口，纯标准库实现，零新增依赖）
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    api_key: str
    base_url: str = 'https://api.openai.com/v1'
    model: str = 'gpt-4o-mini'
    timeout: int = 20


class LLMClient:
    """OpenAI 兼容的 chat completions 极简客户端（urllib，无第三方依赖）。"""

    def __init__(self, config: LLMConfig):
        self.config = config

    def chat(self, system: str, user: str) -> Optional[str]:
        """调用失败返回 None，由调用方决定回退。"""
        url = self.config.base_url.rstrip('/') + '/chat/completions'
        payload = {
            'model': self.config.model,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ],
            'temperature': 0.3,
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.config.api_key}',
            })
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            return data['choices'][0]['message']['content'].strip()
        except Exception:
            return None


def get_client() -> Optional[LLMClient]:
    """按环境变量构造客户端；未配置则返回 None（离线模式）。"""
    key = os.environ.get('ASSISTANT_LLM_API_KEY')
    if not key:
        return None
    return LLMClient(LLMConfig(
        api_key=key,
        base_url=os.environ.get('ASSISTANT_LLM_BASE_URL', 'https://api.openai.com/v1'),
        model=os.environ.get('ASSISTANT_LLM_MODEL', 'gpt-4o-mini'),
    ))


# ---------------------------------------------------------------------------
# 应用 1：消息改写（messages.py 接入）
# ---------------------------------------------------------------------------

REWRITE_SYSTEM = """你是航空公司运行控制部门的文案助手。给你一份结构化的研判结果，
为指定受众改写一段消息。铁律：
1. 只能使用输入中给出的事实，不得添加任何数字、来源或结论；
2. 不得出现"已延误/一定/保证/确定取消"等确定性表述——只能表达"风险"和"可能"；
3. 保留输入中的【模拟演练】和【草案】标注；
4. 一段话不超过 120 字。"""


def rewrite_message(client: LLMClient, template_msg: str, audience: str,
                    structured: dict) -> Optional[str]:
    """
    用 LLM 改写模板消息。校验不通过或调用失败返回 None（调用方回退模板）。
    structured: 研判的结构化字段（等级、依据、行动等），LLM 只允许使用这些内容。
    """
    user = (f"受众：{audience}\n结构化研判结果：{json.dumps(structured, ensure_ascii=False)}\n"
            f"模板草稿（供参考语气和内容范围）：\n{template_msg}\n"
            f"请改写。保留标注，不得新增事实。")
    text = client.chat(REWRITE_SYSTEM, user)
    if text is None:
        return None
    check = validate_output(text)
    return text if check.passed else None


# ---------------------------------------------------------------------------
# 应用 2：事件文本理解（engine.py 接入，可选增强信号提取）
# ---------------------------------------------------------------------------

CLASSIFY_SYSTEM = """你是航空事件文本分类器。把输入描述中体现的风险要素分类为
以下标签（可多选，没有的不要编）：降水、大风、低温冰冻、运行下降、任务集中。
输出 JSON：{"labels": [...], "spans": {"标签": "原文摘录"}}"""


def classify_event_text(client: LLMClient, weather_desc: str, ops_desc: str) -> Optional[dict]:
    """
    让 LLM 把自由文本映射到既定标签。返回 None 表示离线/失败，
    engine 回退到关键词表。标签进来后评分仍由代码完成。
    """
    text = client.chat(CLASSIFY_SYSTEM, f"天气描述：{weather_desc}\n运行描述：{ops_desc}")
    if text is None:
        return None
    try:
        # 兼容模型输出 ```json 包裹的情况
        cleaned = text.strip().strip('`').replace('json\n', '', 1)
        result = json.loads(cleaned)
        allowed = {'降水', '大风', '低温冰冻', '运行下降', '任务集中'}
        labels = [l for l in result.get('labels', []) if l in allowed]
        return {'labels': labels, 'spans': result.get('spans', {})}
    except Exception:
        return None
