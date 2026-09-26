# -*- coding: utf-8 -*-
"""G-B 代价核算：Q2 多服务区合并（单架次访问多个服务区）

按 spec/effectiveness_rubric.md §六 的强制要求：先核算收益与代价，再决定是否实现。
核算口径与求解器一致（core.py）。

收益：每合并一对服务区为 1 个架次，省 1 个架次 → 省该架次的转移能耗与准备时间
代价：多飞一段 A→B（或 O01→A→B→O01），多一次服务区交接
风险：首批箱到达时刻后移 → 可能违反 3600 s 硬约束
"""
from __future__ import annotations
import os, sys, math, itertools
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                     # noqa: E402
from dem_io import load_dem                          # noqa: E402

OUT = os.path.join(ROOT, 'out'); REP = os.path.join(ROOT, 'reports')


def eval_multi(types, tp, path, geom_map, bmap, boxes, elev_o, nodes, q_max_use=None):
    """评估一条多站点路径 O01 -> s1 -> s2 -> ... -> O01。

    path: 服务区序号列表（如 [1,5]）
    返回 dict(E, dur, q, vol, boxt, ok, reason)
    """
    ty = types[tp]
    total_q = sum(float(bmap[b]['单箱质量（kg）']) for s in path
                  for b in boxes[s])
    total_v = sum(float(bmap[b]['单箱体积（m³）']) for s in path
                  for b in boxes[s])
    if total_q > ty['q_max'] + 1e-9 or total_v > ty['vol_max'] + 1e-9:
        return None
    # 起点 O01：准备 + 装载
    n_box = sum(len(boxes[s]) for s in path)
    t = ty['t_prep'] + ty['t_box'] * n_box
    E = 0.0
    cur, cur_h = 'O', elev_o
    boxt = {}
    q_rem = total_q
    for s in path:
        g = geom_map[(cur, s)]
        zc = g['z_cruise']
        h_up = max(0.0, zc - cur_h)
        # 该航段携带剩余载荷
        f = C.leg_time_energy(ty, g, q_rem, cur_h, nodes['S%03d' % s]['elev'] + C.CABIN)
        E += f['E']
        t += f['t']
        # 服务区交接
        hand = ty['t_hand_base'] + ty['t_hand_box'] * len(boxes[s])
        for k, b in enumerate(boxes[s]):
            boxt[b] = t + ty['t_hand_box'] * k
        t += hand
        q_rem -= sum(float(bmap[b]['单箱质量（kg）']) for b in boxes[s])
        cur, cur_h = s, nodes['S%03d' % s]['elev'] + C.CABIN
    g = geom_map[(cur, 'O')]
    f = C.leg_time_energy(ty, g, q_rem, cur_h, elev_o)
    E += f['E']; t += f['t']
    if not C.energy_ok(ty, E):
        return None
    return dict(E=E, dur=t, q=total_q, vol=total_v, boxt=boxt, n_box=n_box)


def main():
    nodes = C.load_nodes(); types = C.load_transport_types()
    bx = C.load_boxes(); z, lon, lat = load_dem()
    o = nodes['O01']
    bmap = {r['货箱编号']: r for _, r in bx.iterrows()}
    boxes = {i: [r['货箱编号'] for _, r in bx[bx['服务区编号'] == 'S%03d' % i].iterrows()]
             for i in range(1, 16)}
    sites = list(range(1, 16))

    # 几何：所有有序对
    geom = {}
    for a in ['O'] + sites:
        for b in ['O'] + sites:
            if a == b:
                continue
            A = nodes['O01'] if a == 'O' else nodes['S%03d' % a]
            B = nodes['O01'] if b == 'O' else nodes['S%03d' % b]
            geom[(a, b)] = C.leg_geometry(z, lon, lat, A['lon'], A['lat'], B['lon'], B['lat'])

    # ---- 基准：Q1 的单点方案（每站独立，最省架次机型） ----
    q1 = pd.read_csv(os.path.join(OUT, 'Q1_单点组批.csv'))
    base_by_site = {}
    for i in sites:
        sub = q1[q1['服务区编号'] == 'S%03d' % i]
        base_by_site[i] = dict(n=len(sub), E=sub['架次能耗（kWh）'].sum(),
                               T=sub['往返时间（s）'].sum(), tp=sub['机型编号'].iloc[0])
    base_n = sum(v['n'] for v in base_by_site.values())
    base_E = sum(v['E'] for v in base_by_site.values())
    base_T = sum(v['T'] for v in base_by_site.values())
    # 时间代价的分母必须用 Q2 的**实际 makespan**（并行调度后的完成时间），
    # 而非 Q1 的串行总时长 —— 否则会低估时间代价（初版即犯此错）。
    import json as _json
    _q2s = _json.load(open(os.path.join(OUT, 'Q2_summary.json'), encoding='utf-8'))
    base_makespan = float(_q2s['makespan'])

    # ---- 枚举所有站对，核算合并的收益与代价 ----
    rows = []
    for a, b in itertools.combinations(sites, 2):
        for tp in ('A', 'B', 'C'):
            # 仅考虑"两站全部货箱合并为 1 个架次"（若体积/质量允许）
            r = eval_multi(types, tp, [a, b], geom, bmap, boxes, o['elev'], nodes)
            if r is None:
                continue
            # 对比基准：两站原本需要的架次数与能耗
            need_a = base_by_site[a]['n'] + base_by_site[b]['n']
            if need_a <= 1:
                continue                     # 原本就各 1 架次则合并无收益
            # 合并后 1 个架次 -> 省 (need_a - 1) 个架次
            saved_sorties = need_a - 1
            # 收益：省下的架次若按基站方案执行，能耗 = 基站能耗 - 该合并架次能耗
            base_ab_E = base_by_site[a]['E'] + base_by_site[b]['E']
            dE = base_ab_E - r['E']
            base_ab_T = base_by_site[a]['T'] + base_by_site[b]['T']
            dT = base_ab_T - r['dur']
            if saved_sorties <= 0:
                continue
            # 首批硬约束风险
            first_deadline = None
            for s in (a, b):
                fb = [x for x in boxes[s] if bmap[x]['是否首批保障'] == '是']
                if fb:
                    dl = min(float(bmap[x]['首批截止时间（s）']) for x in fb)
                    first_deadline = dl if first_deadline is None else min(first_deadline, dl)
            arrive = max(r['boxt'][x] for s in (a, b)
                         for x in boxes[s] if bmap[x]['是否首批保障'] == '是') \
                if first_deadline is not None else None
            risk = (arrive > first_deadline) if (arrive is not None) else False
            rows.append(dict(A='S%03d' % a, B='S%03d' % b, type=tp,
                             原架次数=need_a, 合并后架次=1, 省架次=saved_sorties,
                             基站能耗=round(base_ab_E, 4), 合并能耗=round(r['E'], 4),
                             Δ能耗=round(dE, 4), 基站时长=round(base_ab_T, 1),
                             合并时长=round(r['dur'], 1), Δ时长=round(dT, 1),
                             首批截止=first_deadline, 首批到达=round(arrive, 0) if arrive else None,
                             违约=risk))
    df = pd.DataFrame(rows)
    if not len(df):
        print("无可行的两站合并方案")
        return
    ok = df[~df['违约']]
    print("=" * 100)
    print("G-B 代价核算：Q2 多服务区合并（两站合并为 1 架次）")
    print("=" * 100)
    print("基准（Q1 单点方案）：%d 架次, %.4f kWh, %.1f s" % (base_n, base_E, base_T))
    print("可行合并组合：%d 个（其中违约首批硬约束 %d 个）" % (len(df), int(df['违约'].sum())))
    print()
    if len(ok):
        print("Δ能耗 > 0（能耗下降）的组合数：%d / %d" % (int((ok['Δ能耗'] > 0).sum()), len(ok)))
        print()
        print("按 Δ能耗 排序（前 12）：")
        cols = ['A', 'B', 'type', '原架次数', '省架次', '基站能耗', '合并能耗', 'Δ能耗', 'Δ时长']
        print(ok.sort_values('Δ能耗', ascending=False)[cols].head(12).to_string(index=False))
        print()
        print("Δ能耗 ≤ 0（能耗不降）的组合数：%d" % int((ok['Δ能耗'] <= 0).sum()))
        print()
        # 全局：若允许所有可行合并同时生效（贪心，按 Δ能耗 降序）
        print("=== 若贪心采纳全部 Δ能耗>0 的合并（互斥约束：每站至多参与一次）===")
        cand = ok[ok['Δ能耗'] > 0].sort_values('Δ能耗', ascending=False)
        used = set(); chosen = []
        for _, r in cand.iterrows():
            if r['A'] in used or r['B'] in used:
                continue
            used.add(r['A']); used.add(r['B']); chosen.append(r)
        if chosen:
            dn = sum(r['省架次'] for r in chosen)
            dE = sum(r['Δ能耗'] for r in chosen)
            dT = sum(r['Δ时长'] for r in chosen)
            print("  采纳合并 %d 对，省架次 %d，Δ能耗 %+.4f kWh (%.2f%%)，Δ时长 %+.1f s (%.2f%%)"
                  % (len(chosen), dn, dE, 100 * dE / base_E, dT, 100 * dT / base_T))
            print("  合并对：%s" % ", ".join("%s+%s" % (r['A'], r['B']) for r in chosen))
            print()
            print("=== G-B 判决 ===")
            print("  收益：省 %d 架次，能耗降 %.4f kWh (%.2f%%)" % (dn, dE, 100 * dE / base_E))
            print("  代价：时长变化 %+.1f s (%.2f%%)" % (dT, 100 * dT / base_T))
            # 判决须同时看"收益（架次/能耗）"与"代价（时间）"，且与 Q1 确立的优先关系一致：
            #   优先关系 = 架次数 → 作业时间 → 能耗
            # 故：省架次是首要收益；时间上升是实质代价；能耗下降是次要收益。
            dn_all = dn
            time_cost_pct = 100 * dT / base_makespan
            by_sortie = dn_all > 0
            by_time = dT <= 0
            by_energy = dE > 0
            if by_sortie and by_time:
                verdict = "允许实现（省架次且不增时间）"
            elif by_sortie and not by_time and by_energy:
                verdict = ("**条件性允许**：省 %d 架次、能耗降 %.2f%%，但时间增 %.2f%%；"
                           "须按 Q1 的优先关系（架次→时间→能耗）判断——"
                           "若以完成时间为首要目标则**否决**" %
                           (dn_all, 100 * dE / base_E, time_cost_pct))
            elif by_sortie and not by_time and not by_energy:
                verdict = "**否决**（省架次但时间与能耗同时变差）"
            else:
                verdict = "**否决**（无架次收益）"
            print("  判决：%s" % verdict)
            print()
            print("  注：Q2 的首要指标是【全部任务完成时间】（基准 makespan %.0f s）。"
                  "合并使 makespan 增 %.2f%%，故按 Q2 目标应**否决**；"
                  "该结论与 Q1 的优先关系（时间优先于能耗）一致。"
                  % (base_makespan, time_cost_pct))
        else:
            print("  无 Δ能耗>0 的可行合并")
    df.to_csv(os.path.join(OUT, 'Q2_合并代价核算.csv'), index=False, encoding='utf-8-sig')

    lines = ["# G-B 代价核算：Q2 多服务区合并\n",
             "## 基准\n", "| 项 | 值 |", "|---|---|",
             "| Q1 单点方案架次数 | %d |" % base_n,
             "| 总能耗 | %.4f kWh |" % base_E,
             "| 累计作业时间（串行） | %.1f s |" % base_T,
             "| **Q2 实际 makespan（时间代价分母）** | **%.1f s** |" % base_makespan,
             "\n## 可行两站合并组合\n",
             "| 项 | 值 |", "|---|---|",
             "| 可行组合数 | %d |" % len(df),
             "| 其中违约首批硬约束 | %d |" % int(df['违约'].sum()),
             "| Δ能耗 > 0（降能耗） | %d |" % (int((ok['Δ能耗'] > 0).sum()) if len(ok) else 0),
             ]
    with open(os.path.join(REP, 'G-B_Q2_merge.md'), 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print("\n输出:", os.path.join(OUT, 'Q2_合并代价核算.csv'))


if __name__ == '__main__':
    main()
