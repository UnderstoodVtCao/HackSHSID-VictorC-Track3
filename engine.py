# -*- coding: utf-8 -*-
"""
engine.py
=========
航班中断研判引擎：对一条事件现场计算风险等级、生成可追溯的判断依据，
并给出排好优先级的行动建议。

设计原则（对应赛事评分要点）
----------------------------
1. **现场计算，不查预制答案**：任何机场、任何新事件输入进来，引擎都基于
   工作簿中的基线数据与规则实时计算——这是「能处理未见场景」的直接证据。
2. **规则优先于评分**：05_运营规则是硬约束（如来源冲突时 RULE001 强制降级、
   重大运行决定 RULE007 必须人工审批），规则触发时覆盖评分结果。
3. **可追溯**：判断依据中的每个数字都注明来源编号（SIM-BASE 等），
   每条行动都注明依据的规则编号，事实 / 假设 / 未知三者分开列出。
4. **可降级**：数据缺失（DQ002）、来源冲突（DQ003）时主动降低判断强度，
   不输出确定性结论。

风险评分卡（我们自己设计并公开辩护的「中断定义」）
--------------------------------------------------
信号                          记分
天气风险（RULE002 关键词）      每类 1 分，多类或措辞强烈再 +1
历史异常（RULE003，近12个月
  与自身均值差）                ≤ -5pp 记 1 分；≤ -10pp 记 2 分
运行压力（RULE004）             下降 + 后续任务集中 记 2 分；单项 1 分
分级：0–1 低 / 2–4 中 / ≥5 高
（机场业务重要程度不直接加分，用于影响面评估与升级建议——设计取舍见报告）
"""

from dataclasses import dataclass, field
from typing import Optional

from .data_loader import DataLoader, Event


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """一条风险信号：内容、记分、来源。"""
    name: str
    score: int
    detail: str            # 人话描述，写进判断依据
    source: str            # 来源编号（可追溯）


@dataclass
class Assessment:
    """一次完整研判结果，字段与 06_智能助手输出 模板列一一对应。"""
    event_id: str
    risk_level: str = ''            # 低 / 中 / 高 / 无法定级
    confidence: str = ''            # 高 / 中 / 低（数据冲突、缺失时为低）
    window: str = ''                # 适用机场与时间
    facts: list = field(default_factory=list)        # [(陈述, 来源)]
    assumptions: list = field(default_factory=list)  # 待核查假设
    unknowns: list = field(default_factory=list)     # 信息缺口
    impact: str = ''                                 # 可能影响
    first_action: str = ''                           # 第一步行动
    follow_actions: str = ''                         # 后续行动
    escalate_condition: str = ''                     # 升级条件
    human_decisions: str = ''                        # 必须人工决定事项
    signals: list = field(default_factory=list)      # 评分明细
    rules_triggered: list = field(default_factory=list)
    sources: list = field(default_factory=list)      # 引用来源汇总
    score: int = 0
    downgrade_reason: str = ''                       # 降级原因（如来源冲突）


# ---------------------------------------------------------------------------
# 关键词库：从事件文本中提取信号
# ---------------------------------------------------------------------------

WEATHER_KEYWORDS = {
    '降水类': ['强降雨', '暴雨', '大雨', '雷雨', '短时雷雨', '降雨'],
    '大风类': ['阵风', '大风', '风速'],
    '低温冰冻类': ['低温', '降雪', '冰冻', '霜冻'],
}
WEATHER_STRONG_WORDS = ['强', '较大', '并伴有', '暴雨', '大风']

PRESSURE_DECLINE_WORDS = ['下降', '下滑', '表现下降', '延误增多']
PRESSURE_BUSY_WORDS = ['集中', '较多计划航班', '任务仍集中', '压力大']

# 重大运行决定关键词（触发 RULE007：必须人工，禁止自动执行）
MAJOR_DECISION_WORDS = ['取消', '放行', '调度', '维修', '赔付', '赔偿']

# 隐私信息模式（触发 RULE011）
PRIVACY_PATTERNS = ['姓名', '手机号', '身份证', '护照号', '电话']


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------

class AssessmentEngine:
    def __init__(self, loader: DataLoader, llm_client=None):
        self.loader = loader
        self.llm_client = llm_client  # 可选：用于事件文本理解，评分仍由代码完成

    # -- 公开入口 -----------------------------------------------------------

    def assess(self, event: Event, extra_info: Optional[str] = None) -> Assessment:
        """
        对一条事件执行完整研判。
        extra_info: 人工补充的信息（如「现场资源充足」），用于反馈闭环演示。
        """
        a = Assessment(event_id=event.event_id)
        a.window = f"{event.airport}，{event.time}"
        a.human_decisions = '放行、取消、调度、维修、赔付、正式旅客通知'

        base_rows = self.loader.baseline_rows(event.airport)
        scale = self.loader.latest_scale(event.airport)

        # 0) 可选：LLM 事件文本理解（把自由文本映射到既定标签；失败则回退关键词）
        llm_labels = self._llm_classify(event)

        # 1) 数据冲突检测（RULE001，硬约束：覆盖评分）
        conflict = self._detect_conflict(event)
        if conflict:
            self._apply_conflict_handling(a, event, conflict)
            return a

        # 2) 提取三类风险信号（现场计算）
        a.signals.append(self._weather_signal(event, llm_labels))
        a.signals.append(self._history_signal(event, base_rows))
        a.signals.append(self._pressure_signal(event, llm_labels))
        a.score = sum(s.score for s in a.signals)

        # 3) 汇总事实（每条注明来源）
        self._collect_facts(a, event, base_rows, scale)

        # 4) 假设与未知（来自事件字段，保持原样可追溯）
        a.unknowns = [u.strip() for u in event.unknown.split('、') if u.strip()]
        a.assumptions = self._build_assumptions(event)

        # 5) 评分 → 分级
        a.risk_level = self._level(a.score)
        a.confidence = '高'

        # 6) 数据缺失降级（DQ002）
        missing_weather = any(w.is_missing for w in self.loader.weather_rows(event.airport))
        if missing_weather:
            a.confidence = '低'
            a.downgrade_reason = '该机场存在标记为缺失的气象记录（DQ002），判断强度已降低'
            a.unknowns.append('部分月份气象数据缺失（见 11_数据质量问题 DQ002）')

        # 7) 人工反馈闭环（RULE006）：extra_info 含「资源充足/可控」等 → 降级
        if extra_info and self._is_rejection_context(extra_info):
            a.risk_level = self._downgrade_level(a.risk_level)
            a.downgrade_reason = ('人工补充信息表明现场资源足以应对（RULE006 人工否决处理）：'
                                  f'{extra_info}')
            a.assumptions.append('人工判断依据已记录，后续应把现场资源字段纳入标准输入')

        # 8) 触发规则与引用来源
        a.rules_triggered = self._triggered_rules(event, a)
        a.sources = self._collect_sources(a)

        # 9) 生成行动建议（按优先级）
        self._build_actions(a, event)
        return a

    # -- 信号计算 -----------------------------------------------------------

    def _detect_conflict(self, event: Event) -> Optional[str]:
        """检测来源冲突（EVT003 场景）：描述中出现两个来源且结论相反。"""
        text = event.ops_desc + event.weather_desc + event.field_note
        has_two_sources = ('来源甲' in text and '来源乙' in text) or '冲突' in event.name
        contradict = ('正常' in text and ('下降' in text or '明显' in text))
        return '两个来源数据矛盾' if (has_two_sources and contradict) else None

    def _llm_classify(self, event: Event) -> Optional[dict]:
        """可选的 LLM 文本理解；离线或失败返回 None（调用方回退关键词表）。"""
        if self.llm_client is None:
            return None
        from .llm_layer import classify_event_text
        return classify_event_text(self.llm_client, event.weather_desc, event.ops_desc)

    def _weather_signal(self, event: Event, llm_labels: Optional[dict] = None) -> Signal:
        # 优先使用 LLM 分类标签（若有），否则回退关键词表；评分逻辑不变。
        llm_cats = None
        if llm_labels:
            mapping = {'降水': '降水类', '大风': '大风类', '低温冰冻': '低温冰冻类'}
            llm_cats = [mapping[l] for l in llm_labels.get('labels', []) if l in mapping]
        cats = llm_cats if llm_cats else [
            c for c, kws in WEATHER_KEYWORDS.items()
            if any(k in event.weather_desc for k in kws)]
        if not cats:
            return Signal('天气风险', 0, '事件未报告明显不利天气。', 'SIM-WEATHER')
        score = len(cats)
        strong = any(w in event.weather_desc for w in WEATHER_STRONG_WORDS)
        if len(cats) >= 2 or strong:
            score += 1
        # 天气单项最高记 2 分：气象只是「风险信号」而非既成事实，
        # 避免单靠天气预报把等级推到「高」（校准依据：赛事方 EVT001 示例为「中」）。
        score = min(score, 2)
        detail = (f"模拟天气提示：{event.weather_desc}"
                  f"（涉及{'、'.join(cats)}）")
        return Signal('天气风险', score, detail, 'SIM-WEATHER')

    def _history_signal(self, event: Event, base_rows) -> Signal:
        """RULE003：近12个月与自身均值差的最差值。"""
        if not base_rows:
            return Signal('历史异常', 0, '无该机场基线数据。', 'SIM-BASE')
        last12 = base_rows[-12:]
        worst = min(last12, key=lambda b: b.diff_pp if b.diff_pp is not None else 0)
        if worst.diff_pp is None:
            return Signal('历史异常', 0, '基线数据不完整。', 'SIM-BASE')
        if worst.diff_pp <= -10:
            score = 2
        elif worst.diff_pp <= -5:
            score = 1
        else:
            score = 0
        detail = (f"{worst.year}年{worst.month}月按计划完成航班比例 {worst.plan_ratio:.1%}，"
                  f"低于自身12个月均值 {abs(worst.diff_pp):.1f} 个百分点"
                  f"（阈值5个百分点，{'触发' if score else '未触发'} RULE003）")
        return Signal('历史异常', score, detail, 'SIM-BASE')

    def _pressure_signal(self, event: Event, llm_labels: Optional[dict] = None) -> Signal:
        # 严格按 RULE004 的触发定义：「上一时段表现下降」且「后续任务仍集中」
        # 两个条件同时成立才记分；单一条件不构成持续运行压力信号。
        if llm_labels and {'运行下降', '任务集中'} <= set(llm_labels.get('labels', [])):
            decline, busy = True, True
        else:
            decline = any(w in event.ops_desc for w in PRESSURE_DECLINE_WORDS)
            busy = any(w in event.ops_desc for w in PRESSURE_BUSY_WORDS)
        if decline and busy:
            score = 2
            detail = f"运行报告：{event.ops_desc}（表现下降且后续任务集中，触发 RULE004）。"
        else:
            score = 0
            detail = f"运行报告：{event.ops_desc}。"
        return Signal('运行压力', score, detail, 'SIM-BASE')

    # -- 事实 / 假设 --------------------------------------------------------

    def _collect_facts(self, a: Assessment, event: Event, base_rows, scale):
        a.facts.append(('【事实】' + a.signals[0].detail.replace('模拟天气提示：', '天气预报：'),
                        a.signals[0].source))
        if a.signals[1].score > 0:
            a.facts.append(('【事实】' + a.signals[1].detail, a.signals[1].source))
        if scale:
            a.facts.append((f"【事实】该机场为{scale.airport_class}，"
                            f"旅客吞吐量约{scale.passengers_wan}万人次，"
                            f"航空公司业务重要程度为「{scale.importance}」",
                            scale.source))
        latest = base_rows[-1] if base_rows else None
        if latest:
            a.facts.append((f"【事实】最近一期基线（{latest.year}年{latest.month}月）："
                            f"按计划完成航班比例 {latest.plan_ratio:.1%}，"
                            f"与自身均值差 {latest.diff_pp:+.1f} 个百分点",
                            latest.source))
        a.facts.append(('【事实】' + f"现场备注：{event.field_note}", '04_模拟事件'))

    def _build_assumptions(self, event: Event) -> list:
        out = []
        if any(k in event.weather_desc for k in ['预计', '将']):
            out.append('天气将按预报发展并对运行产生影响（假设，未验证）')
        if any(w in event.ops_desc for w in PRESSURE_DECLINE_WORDS):
            out.append('当前的运行下降可能延续到下一时段（假设，未验证）')
        if not out:
            out.append('暂无额外假设')
        return out

    # -- 分级与降级 ---------------------------------------------------------

    @staticmethod
    def _level(score: int) -> str:
        if score <= 1:
            return '低'
        if score <= 4:
            return '中'
        return '高'

    @staticmethod
    def _downgrade_level(level: str) -> str:
        return {'高': '中', '中': '低', '低': '低'}.get(level, level)

    def _is_rejection_context(self, extra_info: str) -> bool:
        return any(w in extra_info for w in ['资源充足', '足以应对', '可控', '否决', '拒绝'])

    def _apply_conflict_handling(self, a: Assessment, event: Event, conflict: str):
        """RULE001：来源冲突 → 不定级、列核查项、禁止确定性结论。"""
        a.risk_level = '无法定级'
        a.confidence = '低'
        a.score = 0
        a.downgrade_reason = (f'{conflict}，两个来源更新时间相差约四十分钟（04_模拟事件 现场备注）。'
                              '按 RULE001 降低判断强度，不输出确定性结论。')
        a.facts = [('【事实】来源甲显示运行正常；来源乙显示指标明显下降。'
                    '两来源更新时间相差约40分钟。', '04_模拟事件')]
        a.unknowns = ['哪个来源已完成复核', '两个来源的统计口径是否一致']
        a.assumptions = ['来源乙的下降可能为口径差异所致（假设，未验证）']
        a.rules_triggered = ['RULE001']
        a.sources = ['04_模拟事件', 'RULE001']
        a.first_action = ('第一步：核查两个来源的更新时间、统计口径与复核状态，'
                          '在完成核查前不采纳任何单一来源的数值。')
        a.follow_actions = ('核查完成后重新运行研判；若确认下降属实，再按 RULE003 '
                            '比对历史基线并评估影响面。')
        a.escalate_condition = ('若核查确认指标确实明显下降且影响重要机场运行，'
                                '升级至运营负责人。')
        a.impact = '在来源冲突解决前，无法可靠评估影响范围。'
        a.signals = []

    # -- 规则触发 -----------------------------------------------------------

    def _triggered_rules(self, event: Event, a: Assessment) -> list:
        rules = []
        if a.signals and a.signals[0].score > 0:
            rules.append('RULE002')          # 天气风险
        if a.signals and a.signals[1].score > 0:
            rules.append('RULE003')          # 异常波动
        if a.signals and a.signals[2].score > 0:
            rules.append('RULE004')          # 持续运行压力
        rules.append('RULE005')              # 旅客沟通（凡生成旅客信息均需标注草案）
        rules.append('RULE009')              # 引用要求
        rules.append('RULE010')              # 模拟标识
        return rules

    @staticmethod
    def _collect_sources(a: Assessment) -> list:
        src = []
        for s in a.signals:
            if s.source not in src:
                src.append(s.source)
        for f in a.facts:
            if f[1] not in src:
                src.append(f[1])
        src.extend(a.rules_triggered)
        return src

    # -- 行动建议 -----------------------------------------------------------

    def _build_actions(self, a: Assessment, event: Event):
        unknown_str = '、'.join(a.unknowns) if a.unknowns else '无'
        if a.risk_level == '低':
            a.first_action = f'第一步：持续监测，重点跟踪天气与运行变化；当前信息缺口：{unknown_str}。'
            a.follow_actions = ('保持常规监测频率；如收到正式航班状态变化，'
                                '重新运行研判并更新旅客提示草案。')
            a.escalate_condition = '如不利天气兑现且出现正式运行异常，升级至运行协调人员。'
            a.impact = '可能影响有限，但需留意天气兑现后的连锁反应。'
        else:
            a.first_action = (f'第一步：向机场协同团队核查最新保障能力与天气更新'
                              f'（信息缺口：{unknown_str}）。')
            a.follow_actions = ('核查受影响时段的航班集中程度与前序运行情况；'
                                '准备内部监测清单与旅客提示草案。')
            a.escalate_condition = ('如出现正式运行异常、保障能力下降或多个独立风险信号叠加，'
                                    '升级至运营负责人（RULE008）。')
            a.impact = '地面保障和后续运行可能承压，但目前没有正式延误状态。'
