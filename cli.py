# -*- coding: utf-8 -*-
"""
cli.py
======
命令行入口。

用法（在项目根目录 assistant/ 下执行）：
    python -m src.cli events          研判全部模拟事件 EVT001–005，
                                      导出 Excel（06 表已填版）、JSON 与 HTML 演示页
    python -m src.cli event EVT003    研判单个事件（可加 --extra "人工补充信息"）
    python -m src.cli event 临江机场 --new --weather "预计午后有短时雷雨" ...
                                      对任意机场的新事件现场研判（泛化演示）
    python -m src.cli tests           回答全部 15 道测试题，导出测试报告

设计说明：研判结果写入 outputs/，原始数据文件 data/Track 3 Data.xlsx 只读不改。
"""

import argparse
import json
import sys
from pathlib import Path

from openpyxl import Workbook

from .data_loader import DataLoader, Event
from .engine import AssessmentEngine, Assessment
from .messages import build_messages

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / 'data' / 'Track 3 Data.xlsx'
OUT_DIR = ROOT / 'outputs'

OUTPUT_COLUMNS = [
    '事件编号', '生成时间', '风险等级', '适用机场与时间', '判断依据', '可能影响',
    '待核查假设', '信息缺口', '第一步行动', '后续行动', '升级条件',
    '必须人工决定事项', '内部运营提示', '机场协调信息', '旅客信息草案',
    '引用来源', '输出状态',
]


# ---------------------------------------------------------------------------
# 组装一行标准输出（对应 06 模板列）
# ---------------------------------------------------------------------------

def assessment_row(a: Assessment, event: Event, gen_time: str, llm_client=None) -> dict:
    msgs = build_messages(a, a.window, llm_client=llm_client)
    basis = '；'.join(f'{d}（来源：{s}）' for d, s in a.facts)
    if a.downgrade_reason:
        basis += f'；降级说明：{a.downgrade_reason}'
    if a.confidence != '高':
        basis += f'；置信度：{a.confidence}'
    unknowns = '、'.join(a.unknowns) if a.unknowns else '无'
    assumptions = '；'.join(a.assumptions)
    return {
        '事件编号': a.event_id,
        '生成时间': gen_time,
        '风险等级': a.risk_level,
        '适用机场与时间': a.window,
        '判断依据': basis,
        '可能影响': a.impact,
        '待核查假设': assumptions,
        '信息缺口': unknowns,
        '第一步行动': a.first_action,
        '后续行动': a.follow_actions,
        '升级条件': a.escalate_condition,
        '必须人工决定事项': a.human_decisions,
        '内部运营提示': msgs['内部运营提示'],
        '机场协调信息': msgs['机场协调信息'],
        '旅客信息草案': msgs['旅客信息草案'],
        '引用来源': '；'.join(a.sources),
        '输出状态': '待复核',
    }


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def export_excel(rows: list, qa_rows: list, path: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = '06_智能助手输出_已生成'
    ws.append(OUTPUT_COLUMNS)
    for r in rows:
        ws.append([r.get(c, '') for c in OUTPUT_COLUMNS])
    ws2 = wb.create_sheet('08_测试问题_已回答')
    ws2.append(['测试编号', '测试类型', '问题', '回答', '引用规则', '禁止行为核对'])
    for r in qa_rows:
        ws2.append([r['测试编号'], r['测试类型'], r['问题'], r['回答'],
                    '；'.join(r['引用规则']), r['禁止行为核对']])
    wb.save(path)


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_events(loader, gen_times=None, llm_client=None):
    engine = AssessmentEngine(loader, llm_client=llm_client)
    gen_times = gen_times or {}
    rows = []
    full = []
    for ev in loader.events:
        extra = None
        if ev.event_id == 'EVT005':
            extra = '运营人员补充：现场资源充足且风速尚在可控范围（07_人工反馈 REC003）'
        a = engine.assess(ev, extra_info=extra)
        row = assessment_row(a, ev, gen_times.get(ev.event_id, ev.time), llm_client)
        rows.append(row)
        full.append({'assessment': a, 'event': ev, 'row': row})
    return rows, full


def cmd_event_single(loader, event_id, extra, llm_client=None):
    engine = AssessmentEngine(loader, llm_client=llm_client)
    ev = loader.event(event_id)
    if ev is None:
        print(f'找不到事件 {event_id}')
        sys.exit(1)
    a = engine.assess(ev, extra_info=extra)
    return assessment_row(a, ev, ev.time, llm_client)


def cmd_new_event(loader, args, llm_client=None):
    """任意机场的新事件研判——泛化能力演示。"""
    engine = AssessmentEngine(loader, llm_client=llm_client)
    ev = Event(event_id='NEW001', name='新事件', time=args.time or '未指定时间',
               airport=args.airport, weather_desc=args.weather or '无',
               ops_desc=args.ops or '无异常报告', importance=args.importance or '中',
               field_note=args.note or '无', known='月度基线、机场规模',
               unknown='逐航班计划、前序飞机位置、机组状态', status='待处理', tag='模拟')
    a = engine.assess(ev, extra_info=args.extra)
    return assessment_row(a, ev, ev.time, llm_client)


def cmd_tests(loader):
    from .qa import QAModule
    qa = QAModule(loader)
    answers = qa.answer_all()
    qa_rows = [{
        '测试编号': an.test_id,
        '测试类型': next((t.qtype for t in loader.tests if t.test_id == an.test_id), ''),
        '问题': an.question,
        '回答': an.answer,
        '引用规则': an.rules_cited,
        '禁止行为核对': an.forbidden_checked,
    } for an in answers]
    return qa_rows


def main():
    ap = argparse.ArgumentParser(description='航班中断智能助手原型')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('events', help='研判全部事件并导出')
    p1 = sub.add_parser('event', help='研判单个事件')
    p1.add_argument('event_id')
    p1.add_argument('--extra', default=None, help='人工补充信息（反馈闭环演示）')
    p2 = sub.add_parser('new', help='对任意机场的新事件现场研判')
    p2.add_argument('--airport', required=True)
    p2.add_argument('--weather', default='')
    p2.add_argument('--ops', default='')
    p2.add_argument('--note', default='')
    p2.add_argument('--importance', default='中')
    p2.add_argument('--time', default='')
    p2.add_argument('--extra', default=None)
    p3 = sub.add_parser('compare', help='模板稿 vs LLM 改写稿对比演示')
    p3.add_argument('event_id')
    sub.add_parser('tests', help='回答全部测试题并导出')
    for p in (p1, p2, p3):
        p.add_argument('--llm', action='store_true',
                       help='启用 LLM 改写（需环境变量 ASSISTANT_LLM_API_KEY，默认离线）')

    args = ap.parse_args()
    OUT_DIR.mkdir(exist_ok=True)
    loader = DataLoader(str(DATA_PATH))

    # --llm：尝试构造 LLM 客户端；未配置则回退离线并提示
    llm_client = None
    if getattr(args, 'llm', False):
        from .llm_layer import get_client
        llm_client = get_client()
        if llm_client is None:
            print('提示：未配置 ASSISTANT_LLM_API_KEY，已回退离线模板模式。\n')

    if args.cmd == 'events':
        rows, full = cmd_events(loader, llm_client=llm_client)
        qa_rows = cmd_tests(loader)
        xlsx = OUT_DIR / '智能助手输出_已生成.xlsx'
        export_excel(rows, qa_rows, xlsx)
        for r in rows:
            print(f"{r['事件编号']}  {r['适用机场与时间']}  →  风险等级：{r['风险等级']}")
        print(f'\n已导出：{xlsx}')

    elif args.cmd == 'event':
        row = cmd_event_single(loader, args.event_id, args.extra, llm_client)
        print(json.dumps(row, ensure_ascii=False, indent=2))

    elif args.cmd == 'new':
        row = cmd_new_event(loader, args, llm_client)
        print(json.dumps(row, ensure_ascii=False, indent=2))

    elif args.cmd == 'compare':
        ev = loader.event(args.event_id)
        if ev is None:
            print(f'找不到事件 {args.event_id}')
            sys.exit(1)
        engine = AssessmentEngine(loader, llm_client=llm_client)
        a = engine.assess(ev)
        from .messages import build_messages
        from .llm_layer import get_client, validate_output
        client = llm_client or get_client()
        template_msgs = build_messages(a, a.window, llm_client=None)
        print(f'事件 {args.event_id}（{a.window}）风险等级：{a.risk_level}\n')
        for aud, tmpl in template_msgs.items():
            print(f'===== {aud}｜模板稿 =====')
            print(tmpl)
            if client is not None:
                from .llm_layer import rewrite_message
                structured = {'风险等级': a.risk_level, '事实依据': [d for d, _ in a.facts],
                              '第一步行动': a.first_action, '信息缺口': a.unknowns}
                rewritten = rewrite_message(client, tmpl, aud, structured)
                print(f'\n----- {aud}｜LLM 改写稿 -----')
                if rewritten is None:
                    raw = client.chat('按输入改写消息', tmpl)
                    check = validate_output(raw) if raw else None
                    print('（改写未通过机器校验或调用失败，已回退模板稿）')
                    if raw:
                        print(f'  拦截原因：{check.violations if check else "调用失败"}')
                        print(f'  被拦截原文：{raw[:120]}')
                else:
                    print(rewritten)
            print()

    elif args.cmd == 'tests':
        qa_rows = cmd_tests(loader)
        for r in qa_rows:
            print(f"{r['测试编号']} [{r['测试类型']}] {r['问题']}")
            print(f"  → {r['回答'][:80]}...\n")
        xlsx = OUT_DIR / '测试报告_已生成.xlsx'
        export_excel([], qa_rows, xlsx)
        print(f'已导出：{xlsx}')


if __name__ == '__main__':
    main()
