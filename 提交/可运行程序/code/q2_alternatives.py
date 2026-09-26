# -*- coding: utf-8 -*-
"""问题二：**替代运输层**的量化对照（评审 M5 的直接答复）。

背景
----
问题二的交付方案（两阶段构造：紧急轻载架次 + 剩余填充，37 架次）并非"完成时间"这一
首要指标的全局最优——它是**构造式**的，没有把问题一的 18 架次最优装箱纳入候选。
本脚本把"问题一的最优装箱 + 问题二的列表调度器"作为一个**替代方案**跑出来，
与交付方案在（首批/医疗硬约束、完成时间 $F^{done}$、软目标加权迟延 $F^{time}$）上逐项对照，
结果写入 ``out/Q2_替代方案.csv``，供论文 §4.3.1 引用。

为什么保留交付方案（诚实披露，见快照结论）
------------------------------------------
三个指标上的对照是**两胜一负**，交付方案并不占优：

| 指标 | 替代方案（18 架次） | 交付方案（37 架次） | 谁更优 |
|---|---|---|---|
| 完成时间 $F^{done}$ | 12017 s | 12527 s | 替代（**−4.1%**） |
| 运输能耗 | 63.2416 kWh | 86.9840 kWh | 替代（**−27.3%**） |
| 加权迟延 $F^{time}$ | 501055.9 | 221985.2 | **交付（−55.7%）** |

即交付方案用 **+37.5% 的能耗**与 **+4.1% 的完成时间**，买来 **2.26 倍**的及时性改善——
因为把软目标货箱塞进少数重载大架次会显著推迟其送达（S003 的软目标箱要等到 11082–11190 s）。
题面同时要求"全部任务完成时间"与"非首批物资的期望送达时间（衡量及时性）"两个指标，
两者在此处**不可同时最优**，故本文按**及时性优先**取交付方案，并把替代方案与三个指标
一并登记（对应 `spec/iteration_ledger.md` IT-21），**不做"本文方案全局最优"的断言**。

口径
----
- 替代方案的架次任务集 = `out/Q1_单点组批.csv` 的 18 行（箱集合、机型与 Q1 完全一致），
  逐架次经 ``q2_schedule.sortie_eval`` 复算能耗/时长/逐箱交付偏移（与 Q1 表逐位一致到四舍五入）。
- 两者共用**同一个** `q2_schedule.schedule`（机型内列表调度、实体机/电池池、充电周转、
  T-5.3 整数秒向上取整）与同一个 `q2_schedule.evaluate`（首批/医疗硬约束 + 加权迟延），
  故对照是控制变量的。
- 本脚本只读 `out/`，不修改任何求解结果。
"""
from __future__ import annotations
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                       # noqa: E402
import q2_schedule as Q                                # noqa: E402
from dem_io import load_dem                            # noqa: E402

OUT = os.path.join(ROOT, 'out')


def _jobs_from_q1(nodes, types, boxes, z, lon, lat, bmap):
    """把 Q1 的 18 架次装箱适配成 Q2 的 job 结构（同口径复算，不读 Q1 的能耗列）。"""
    o = nodes['O01']
    q1 = pd.read_csv(os.path.join(OUT, 'Q1_单点组批.csv'))
    geoms, jobs = {}, []
    for r in q1.to_dict('records'):
        s = r['服务区编号']
        if s not in geoms:
            geoms[s] = C.leg_geometry(z, lon, lat, o['lon'], o['lat'],
                                      nodes[s]['lon'], nodes[s]['lat'])
        boxids = str(r['货箱编号列表']).split(';')
        rr = Q.sortie_eval(types[r['机型编号']], geoms[s], boxids, bmap,
                           o['elev'], nodes[s]['elev'] + C.CABIN)
        jobs.append(dict(jid=r['架次编号'], site=int(s[1:]), type=r['机型编号'],
                         boxes=boxids, box_rows={b: bmap[b] for b in boxids}, **rr))
    Q.deadlines(jobs)
    return jobs


def main():
    nodes = C.load_nodes(); types = C.load_transport_types()
    boxes = C.load_boxes(); z, lon, lat = load_dem()
    fleet, bat = C.load_transport_fleet()
    bmap = {r['货箱编号']: r for _, r in boxes.iterrows()}

    rows = []

    # --- 口径 0：Q1 组批 + **串行**执行（问题一的原始口径，用于说明为何不能直接沿用）---
    q1 = pd.read_csv(os.path.join(OUT, 'Q1_单点组批.csv'))
    clock, viol = 0.0, 0
    for r in q1.to_dict('records'):
        clock += float(r['往返时间（s）'])
        for b in str(r['货箱编号列表']).split(';'):
            row = bmap[b]
            if str(row['是否首批保障']) == '是' and clock > float(row['首批截止时间（s）']) + 1e-9:
                viol += 1
    rows.append(dict(方案='Q1 组批 + 串行执行（问题一口径）', 架次数=len(q1),
                     完成时间s=clock, 首批违例数=viol, 医疗违例数=None,
                     加权迟延=None, 运输能耗kWh=round(float(q1['架次能耗（kWh）'].sum()), 4),
                     是否可行='否（首批硬约束不满足）'))

    # --- 口径 1：Q1 组批 + Q2 的并行调度器（替代方案）---
    alt = _jobs_from_q1(nodes, types, boxes, z, lon, lat, bmap)
    sched1 = Q.schedule(alt, fleet, bat, types)
    ev1 = Q.evaluate(alt, sched1, types)
    n_first = sum(1 for j in alt for b in j['boxes']
                  if bmap[b]['是否首批保障'] == '是')
    rows.append(dict(方案='Q1 组批 + 并行调度（替代方案）', 架次数=len(alt),
                     完成时间s=ev1['makespan'], 首批违例数=0 if ev1['first_ok'] else -1,
                     医疗违例数=0 if ev1['med_ok'] else -1,
                     加权迟延=round(ev1['wdelay'], 1),
                     运输能耗kWh=round(sum(j['E'] for j in alt), 4),
                     是否可行='是（首批 %d 箱 / 医疗全部满足）' % n_first))

    # --- 口径 2：交付方案（Q2 两阶段构造，37 架次）---
    # 用**同一个** evaluate 复算（控制变量）：既可对比，也顺带校验交付方案可复现。
    deliv = Q.build_q2(nodes, types, boxes, z, lon, lat)
    sched2 = Q.schedule(deliv, fleet, bat, types)
    ev2 = Q.evaluate(deliv, sched2, types)
    q2 = pd.read_csv(os.path.join(OUT, 'Q2_运输架次.csv'))
    q2b = pd.read_csv(os.path.join(OUT, 'Q2_逐箱交付.csv'))
    tmap = dict(zip(q2b['货箱编号'], q2b['交付完成时刻（s）']))
    n_first = sum(1 for b in tmap if bmap[b]['是否首批保障'] == '是')
    n_med = sum(1 for b in tmap if bmap[b]['物资类型'] == '医疗物资')
    rows.append(dict(方案='Q2 两阶段构造（交付方案）', 架次数=len(q2),
                     完成时间s=round(ev2['makespan'], 1),
                     首批违例数=0 if ev2['first_ok'] else -1,
                     医疗违例数=0 if ev2['med_ok'] else -1,
                     加权迟延=round(ev2['wdelay'], 1),
                     运输能耗kWh=round(float(q2['架次能耗（kWh）'].sum()), 4),
                     是否可行='是（首批 %d 箱 / 医疗 %d 箱全部满足）' % (n_first, n_med)))
    assert abs(ev2['makespan'] - float(q2['返回O01时刻（s）'].max())) < 1e-6, \
        '复算 makespan 与 out/Q2_运输架次.csv 不一致'

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'Q2_替代方案.csv'), index=False, encoding='utf-8-sig')
    print(df.to_string(index=False))
    e_alt, e_del = float(df.iloc[1]['运输能耗kWh']), float(df.iloc[2]['运输能耗kWh'])
    print('\n结论（两胜一负，互不支配）：')
    print('  替代方案 完成时间 −%.1f%%、能耗 −%.1f%%（%.4f vs %.4f kWh），'
          % (100 * (1 - ev1['makespan'] / df.iloc[2]['完成时间s']),
             100 * (1 - e_alt / e_del), e_alt, e_del))
    print('           但加权迟延 +%.0f%%（%.1f vs %.1f）——本文按及时性优先取交付方案。'
          % (100 * (ev1['wdelay'] / df.iloc[2]['加权迟延'] - 1),
             ev1['wdelay'], df.iloc[2]['加权迟延']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
