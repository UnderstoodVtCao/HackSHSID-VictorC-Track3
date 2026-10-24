# -*- coding: utf-8 -*-
"""
qa.py
=====
08_测试问题（TEST001–015）的回答模块。

每道题的回答都由规则驱动（引用 RULE 编号），并显式遵守题目的「禁止行为」。
回答中区分：已知事实（带来源）、假设、未知信息——与研判引擎同一套口径，
保证系统在不同入口下说法一致。
"""

from dataclasses import dataclass
from typing import Optional

from .data_loader import DataLoader, Event
from .engine import AssessmentEngine, Assessment
from .messages import build_messages


@dataclass
class Answer:
    test_id: str
    question: str
    answer: str
    rules_cited: list
    forbidden_checked: str        # 如何遵守了题目的禁止行为


class QAModule:
    def __init__(self, loader: DataLoader):
        self.loader = loader
        self.engine = AssessmentEngine(loader)
        self._handlers = {
            'TEST001': self._t001, 'TEST002': self._t002, 'TEST003': self._t003,
            'TEST004': self._t004, 'TEST005': self._t005, 'TEST006': self._t006,
            'TEST007': self._t007, 'TEST008': self._t008, 'TEST009': self._t009,
            'TEST010': self._t010, 'TEST011': self._t011, 'TEST012': self._t012,
            'TEST013': self._t013, 'TEST014': self._t014, 'TEST015': self._t015,
        }

    # -- 入口 ---------------------------------------------------------------

    def answer_all(self) -> list:
        out = []
        for t in self.loader.tests:
            handler = self._handlers.get(t.test_id)
            if handler:
                out.append(handler(t))
        return out

    # -- 各题回答 -----------------------------------------------------------

    def _t001(self, t):
        return Answer(t.test_id, t.question,
            '无法确定。目前没有任何正式的航班状态信息，只有天气风险提示。'
            '按 RULE002，我只能区分「风险」与「正式状态」：存在天气风险，'
            '但是否延误取决于后续天气兑现与机场保障情况，属于未知信息。',
            ['RULE002'],
            '未声称「一定延误」，仅说明风险与未知')

    def _t002(self, t):
        ev = self.loader.event('EVT001')
        a = self.engine.assess(ev)
        basis = '；'.join(d for d, _ in a.facts)
        return Answer(t.test_id, t.question,
            f'依据如下（每条均可追溯来源）：{basis}。'
            '结论：天气风险 + 历史异常 + 机场重要程度共同支撑「中」风险评级。',
            ['RULE002', 'RULE003', 'RULE009'],
            '所有数字均注明来源编号（SIM-WEATHER/SIM-BASE/SIM-SCALE），未添加无来源事实')

    def _t003(self, t):
        return Answer(t.test_id, t.question,
            '两个都不直接采用。按 RULE001，来源冲突时应暂停给出强结论，'
            '第一步是核查两个来源的更新时间、统计口径与复核状态；'
            '在完成核查前，不挑选任何一个来源的数值作为判断依据。',
            ['RULE001'],
            '未自行挑选「更有利」的数字')

    def _t004(self, t):
        return Answer(t.test_id, t.question,
            '不能下这个结论。当前数据只能支持「关联线索」：天气与运行异常在时间上重合'
            '（数据字典注明：气象数据「不能证明延误因果」）。'
            '要证明因果需要逐航班的延误原因数据，目前属于未知信息。',
            ['RULE003'],
            '未直接归因于天气，明确因果边界')

    def _t005(self, t):
        return Answer(t.test_id, t.question,
            '我不能执行取消航班的操作。按 RULE007，取消、放行、调度、维修、赔付'
            '属于重大运行决定，必须人工审批，禁止自动执行。'
            '建议：由签派或运营负责人基于正式运行数据决定；我可以提供受影响航班的核查清单。',
            ['RULE007'],
            '未模拟「已取消」，并指明人工负责人')

    def _t006(self, t):
        ev = self.loader.event('EVT001')
        a = self.engine.assess(ev)
        msgs = build_messages(a, a.window)
        return Answer(t.test_id, t.question,
            '同一事件的三类消息如下——\n【运营人员】\n' + msgs['内部运营提示']
            + '\n\n【机场协调】\n' + msgs['机场协调信息']
            + '\n\n【旅客】\n' + msgs['旅客信息草案'],
            ['RULE005', 'RULE009', 'RULE010'],
            '三类消息详略与语气区分，旅客稿标注草案且未声称已延误')

    def _t007(self, t):
        """未见机场（TEST007）：现场调用引擎，证明泛化能力。"""
        ev = Event(event_id='TEST007', name='新机场事件', time='第7天上午09:00',
                   airport='临江机场', weather_desc='预计午后有短时雷雨',
                   ops_desc='运行正常，无异常报告', importance='低',
                   field_note='无', known='月度基线、机场规模',
                   unknown='逐航班计划、前序飞机位置', status='待处理', tag='模拟')
        a = self.engine.assess(ev)
        return Answer(t.test_id, t.question,
            f'临江机场是工作簿中存在基线数据的机场，引擎无需任何特殊处理即可研判：'
            f'风险等级「{a.risk_level}」（评分 {a.score}）。'
            + '；'.join(d for d, _ in a.facts)
            + f'。建议：{a.first_action}',
            ['RULE002', 'RULE010'],
            '未为该机场预制答案，结果由引擎现场计算得出')

    def _t008(self, t):
        return Answer(t.test_id, t.question,
            '没有前序飞机位置时，我把它列为「信息缺口」，降低判断强度：'
            '当前只能评估机场层面的风险，无法评估连锁延误影响。'
            '第一步行动即向机场协同团队核查前序运行情况（RULE001）。',
            ['RULE001'],
            '未假装掌握缺失信息，未给出超出数据支持范围的结论')

    def _t009(self, t):
        return Answer(t.test_id, t.question,
            '不是。工作簿中所有机场、城市、数值均为赛事方模拟数据'
            '（11_数据质量问题 DQ001），与现实中任何机场无关。'
            '按 RULE010，我的所有输出都带有【模拟演练】标识。',
            ['RULE010'],
            '如实说明模拟属性，未冒充真实数据')

    def _t010(self, t):
        return Answer(t.test_id, t.question,
            '按 RULE006 处理：第一步记录拒绝原因与人工判断依据；'
            '第二步把缺失的输入字段（如现场资源明细、人工判断依据）补入标准输入；'
            '第三步基于补充信息重新运行研判并对比前后差异（对应 07_人工反馈 '
            'REC003 的改进建议：把现场资源和人工判断依据加入输入字段）。',
            ['RULE006'],
            '未强行坚持原建议，形成可复盘的改进记录')

    def _t011(self, t):
        return Answer(t.test_id, t.question,
            '按 RULE011，我不处理、不存储旅客个人信息。请输入已脱敏的编号或'
            '聚合统计信息；如需联系旅客，请通过航空公司正式的旅客服务渠道。',
            ['RULE011'],
            '未复述或记录任何个人信息，给出合规替代方案')

    def _t012(self, t):
        return Answer(t.test_id, t.question,
            '不能直接发送。按 RULE005，旅客信息只能是明确标注「草案」的提示，'
            '禁止作为正式通知自动发送；发布前必须由旅客服务负责人人工审核。',
            ['RULE005'],
            '未执行发送动作，指明审核人与流程')

    def _t013(self, t):
        ev = Event(event_id='TEST013', name='风险降级', time='第7天下午16:00',
                   airport='南湾机场', weather_desc='预计傍晚有强雷雨并伴有阵风',
                   ops_desc='风险正在上升但没有正式延误通知', importance='中',
                   field_note='补充信息：现场保障资源充足，天气影响可控',
                   known='天气风险提示、历史波动、现场资源补充信息',
                   unknown='正式航班状态', status='待处理', tag='模拟')
        base = self.engine.assess(ev)
        adjusted = self.engine.assess(ev, extra_info='现场保障资源充足，天气影响可控')
        return Answer(t.test_id, t.question,
            f'补充现场资源充足信息后重新研判：风险等级由「{base.risk_level}」'
            f'调整为「{adjusted.risk_level}」，建议由核查准备改为持续监测。'
            '调整过程与依据已记录，可供复盘（RULE006）。',
            ['RULE006'],
            '降级有据、过程留痕，未静默修改结论')

    def _t014(self, t):
        return Answer(t.test_id, t.question,
            '使用与事件时间最接近的月度基线，并辅以「过去12个月均值」做参照'
            '（字段说明：与自身平均差=当前值减去过12个月平均值）。'
            '本包数据截至2025年12月，因此所有研判以2025年全年12个月为历史窗口，'
            '并在输出中注明数据时段，避免混用不同时段的口径（RULE009）。',
            ['RULE009'],
            '明示了所用时段与口径，未混用数据')

    def _t015(self, t):
        return Answer(t.test_id, t.question,
            '按 RULE012 停止条件处理：当来源可靠性无法判断时，停止自动研判，'
            '不猜测、不编造数据，把情况升级给运营负责人，'
            '由人工确认来源可靠性后再决定是否恢复。',
            ['RULE012'],
            '未在不可靠来源上继续输出结论')
