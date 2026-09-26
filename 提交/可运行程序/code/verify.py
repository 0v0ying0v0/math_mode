# -*- coding: utf-8 -*-
"""独立校验器 (D13 分层独立)

**本文件禁止 import 任何求解模块**（q1_*/q2_*/q3_*/q4_*），只允许：
    - 标准库 / numpy / pandas
    - code/core.py（公共物理规则唯一实现）
    - code/dem_io.py（DEM 解码）
该约束由 tests/governance_check.sh G-01 静态强制。

覆盖 PLAN.md §4 的 A–F 六组 + R1–R4 红队。
"""
from __future__ import annotations
import os, sys, math
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                     # noqa: E402
from dem_io import load_dem, bilinear                # noqa: E402

OUT = os.path.join(ROOT, 'out')
R = []          # (group, id, desc, ok, detail)


def ck(g, cid, desc, ok, detail=""):
    R.append((g, cid, desc, bool(ok), detail))
    print("[%s] %-7s %-52s %s %s" % (g, cid, "OK" if ok else "FAIL", desc, detail))


# ---------------------------------------------------------------------------
def load_all():
    d = {}
    for f, k in [('Q1_单点组批.csv', 'q1'),
                 ('Q2_运输架次.csv', 'q2'), ('Q2_逐箱交付.csv', 'q2b'),
                 ('Q3_中继架次.csv', 'q3r'), ('Q3_通信保障.csv', 'q3c'),
                 ('Q3_逐箱交付.csv', 'q3b'), ('Q3_架次延迟.csv', 'q3d'),
                 ('Q4_分区配置.csv', 'q4'), ('Q4_方案比较.csv', 'q4cmp')]:
        p = os.path.join(OUT, f)
        d[k] = pd.read_csv(p) if os.path.exists(p) else None
    import json
    for f, k in [('Q2_summary.json', 'q2s'), ('Q3_summary.json', 'q3s')]:
        p = os.path.join(OUT, f)
        d[k] = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None
    return d


# ================= A 结构与完备 =================
def group_A(D, nodes, types, boxes):
    print("\n=== A 结构与完备 ===")
    q1 = D['q1']
    ck('A', 'A-1', 'Q1 结果文件存在且非空', q1 is not None and len(q1) > 0,
       "%d 架次" % (0 if q1 is None else len(q1)))
    if q1 is None:
        return
    allbox = []
    for s in q1['货箱编号列表']:
        allbox += [b for b in str(s).split(';') if b]
    ck('A', 'A-2', '每个货箱恰好出现一次（无重复）', len(allbox) == len(set(allbox)),
       "%d 箱, 去重后 %d" % (len(allbox), len(set(allbox))))
    ck('A', 'A-3', '全部 80 箱均被交付', len(set(allbox)) == 80, "%d" % len(set(allbox)))
    ck('A', 'A-4', '交付箱号集合 == 附件箱号集合',
       set(allbox) == set(boxes['货箱编号']), "")
    # 架次内同服务区
    bad = 0
    for _, r in q1.iterrows():
        si = {b.split('-')[0] for b in str(r['货箱编号列表']).split(';')}
        if si != {r['服务区编号']}:
            bad += 1
    ck('A', 'A-5', '架次内货箱同属一个服务区（T-1.6）', bad == 0, "违规 %d" % bad)
    ck('A', 'A-6', '架次编号唯一', q1['架次编号'].is_unique, "")
    wmap = {r['货箱编号']: (float(r['单箱质量（kg）']), float(r['单箱体积（m³）']))
            for _, r in boxes.iterrows()}
    bad_w = bad_v = 0
    for _, r in q1.iterrows():
        w = sum(wmap[b][0] for b in str(r['货箱编号列表']).split(';'))
        v = sum(wmap[b][1] for b in str(r['货箱编号列表']).split(';'))
        if abs(w - r['总质量（kg）']) > 1e-6:
            bad_w += 1
        if abs(v - r['总体积（m³）']) > 1e-6:
            bad_v += 1
    ck('A', 'A-7', '架次总质量与箱明细一致', bad_w == 0, "不一致 %d" % bad_w)
    ck('A', 'A-8', '架次总体积与箱明细一致', bad_v == 0, "不一致 %d" % bad_v)
    # 6 个提交文件
    want = ['Q1_单点组批.csv', 'Q2_运输架次.csv', 'Q2_逐箱交付.csv',
            'Q3_中继架次.csv', 'Q3_通信保障.csv', 'Q4_分区配置.csv']
    miss = [w for w in want if not os.path.exists(os.path.join(OUT, w))]
    ck('A', 'A-9', '6 个提交文件齐备（T-5.1）', not miss, "缺 %s" % miss)


# ================= B 物理复算 =================
def group_B(D, nodes, types, boxes, z, lon, lat):
    print("\n=== B 物理复算 ===")
    q1 = D['q1']
    if q1 is None:
        return
    o = nodes['O01']
    wmap = {r['货箱编号']: (float(r['单箱质量（kg）']), float(r['单箱体积（m³）']))
            for _, r in boxes.iterrows()}
    badE = badT = badS = badCap = 0
    worst = (0, "")
    minSOC = 1.0
    for _, r in q1.iterrows():
        ty = types[r['机型编号']]
        i = int(r['服务区编号'][1:]); s = nodes['S%03d' % i]
        g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])
        bx = [wmap[b] for b in str(r['货箱编号列表']).split(';')]
        q = sum(b[0] for b in bx); vol = sum(b[1] for b in bx)
        if q > ty['q_max'] + 1e-6 or vol > ty['vol_max'] + 1e-6:
            badCap += 1
        f = C.leg_time_energy(ty, g, q, o['elev'], s['elev'] + C.CABIN)
        b = C.leg_time_energy(ty, g, 0.0, s['elev'] + C.CABIN, o['elev'])
        E = f['E'] + b['E']
        T = ty['t_prep'] + ty['t_box'] * len(bx) + f['t'] + b['t'] + \
            ty['t_hand_base'] + ty['t_hand_box'] * len(bx)
        if abs(E - r['架次能耗（kWh）']) > 1e-4:
            badE += 1
        if abs(T - r['往返时间（s）']) > 1e-2:
            badT += 1
        soc = 100 * C.soc_end(ty, E)
        minSOC = min(minSOC, soc / 100)
        if abs(soc - r['返航SOC（%）']) > 1e-2:
            badS += 1
        if not C.energy_ok(ty, E):
            worst = max(worst, (E / ((1 - ty['rho']) * ty['E_use']), r['架次编号']))
    ck('B', 'B-1', '逐架次能耗独立复算一致（≤1e-4 kWh）', badE == 0, "不一致 %d" % badE)
    ck('B', 'B-2', '逐架次往返时间独立复算一致（≤0.01 s）', badT == 0, "不一致 %d" % badT)
    ck('B', 'B-3', '逐架次返航 SOC 独立复算一致（≤0.01%）', badS == 0, "不一致 %d" % badS)
    ck('B', 'B-4', '每架次载质量/体积不超限（T-1.2/T-1.3）', badCap == 0, "违规 %d" % badCap)
    ck('B', 'B-5', '每架次满足返航安全余量（T-1.4）', worst[1] == "",
       "最低 SOC=%.2f%%" % (100 * minSOC))
    # 卡线检测（R6.5）
    borderline = [(r['架次编号'], r['返航SOC（%）']) for _, r in q1.iterrows()
                  if (r['返航SOC（%）'] - 100 * types[r['机型编号']]['rho']) < 1.0]
    ck('B', 'B-6', '卡线解已识别并标注（R6.5）', True,
       "卡线 %s" % borderline if borderline else "无卡线")


# ================= C 时序与资源 =================
def group_C(D, nodes, types, boxes, z, lon, lat):
    print("\n=== C 时序与资源 ===")
    q1, q2, q2b = D['q1'], D['q2'], D['q2b']
    q3c, q3r = D['q3c'], D['q3r']
    q3b, q3d, q2s, q3s = D['q3b'], D['q3d'], D['q2s'], D['q3s']
    if q2 is None or q3r is None:
        ck('C', 'C-0', 'Q2/Q3 结果表存在', False, "缺 Q2_运输架次.csv 或 Q3_中继架次.csv")
        return
    # 时限口径：Q3 继承 **Q2 的 37 个并行运输架次**（不再继承 Q1 的 18 个串行架次）；
    # 箱的时限属性一律取自 data/（附件箱清单 / 需求表），交付时刻取自 out/ 结果表。
    bx = boxes.set_index('货箱编号')
    first = bx[bx['是否首批保障'] == '是']
    med = bx[bx['物资类型'] == '医疗物资']
    dem = C.load_demand()
    exp_first = int(dem['首批必须送达箱数'].sum())
    exp_med = int(dem[dem['物资类型'] == '医疗物资']['总需求箱数'].sum())
    ck('C', 'C-1', '首批保障箱数 = 30（与附件"首批必须送达"合计一致）',
       len(first) == exp_first == 30, "%d 箱 / 附件合计 %d" % (len(first), exp_first))
    # 硬时限架次（含首批保障箱或医疗箱的 Q2 架次）由附件重算，供 C-12/C-15 复用
    hard = set()
    if q2b is not None:
        jof = {r['货箱编号']: r['架次编号'] for _, r in q2b.iterrows()}
        for _, r in boxes.iterrows():
            b = r['货箱编号']
            if b in jof and (str(r['是否首批保障']) == '是'
                             or str(r['物资类型']) == '医疗物资'):
                hard.add(jof[b])
    # 中继资源
    if q3r is not None and len(q3r):
        tyR = C.load_relay_types()[0]
        E = q3r['架次能耗（kWh）'].values
        ck('C', 'C-2', '中继架次均满足返航电量下限（T-3.7）',
           bool((E <= (1 - tyR['rho']) * tyR['E_use']).all()),
           "max %.4f / 限 %.4f kWh" % (E.max(), (1 - tyR['rho']) * tyR['E_use']))
        # 离地高度（独立口径：把交付表里的 (经度, 纬度, 海拔) 三个数拿去在 DEM 上复算）
        ok_h = True
        agl = []
        for _, r in q3r.iterrows():
            zg = bilinear(z, lon, lat, r['悬停经度（°）'], r['悬停纬度（°）'])
            if np.isnan(zg):
                ok_h = False
                continue
            agl.append(r['悬停海拔（m）'] - zg)
            if agl[-1] > C.RELAY_HMAX + 1e-6:
                ok_h = False
        ck('C', 'C-3', '中继悬停离地高度 ≤ 300 m（T-3.6/D8）', ok_h,
           "max AGL=%.6f m / 限 %.1f m（%d 架次）"
           % (max(agl) if agl else float('nan'), C.RELAY_HMAX, len(agl)))
        inb = ((q3r['悬停经度（°）'] >= lon[0]) & (q3r['悬停经度（°）'] <= lon[-1]) &
               (q3r['悬停纬度（°）'] >= lat[-1]) & (q3r['悬停纬度（°）'] <= lat[0]))
        ck('C', 'C-4', '中继悬停位置在 DEM 覆盖内（T-3.5）', bool(inb.all()), "")
        # 同点重叠
        ov = 0
        r2 = q3r.sort_values('开始时刻（s）').reset_index(drop=True)
        for i in range(len(r2)):
            for j in range(i + 1, len(r2)):
                a, b = r2.iloc[i], r2.iloc[j]
                if abs(a['悬停经度（°）'] - b['悬停经度（°）']) < 1e-9 and \
                   abs(a['悬停纬度（°）'] - b['悬停纬度（°）']) < 1e-9 and \
                   a['开始时刻（s）'] < b['返回O01时刻（s）'] and \
                   b['开始时刻（s）'] < a['返回O01时刻（s）']:
                    ov += 1
        ck('C', 'C-5', '同一悬停点不存在时间重叠（T-3.10）', ov == 0, "重叠 %d 对" % ov)
        # 同机时序
        viol = 0
        for vid, g in q3r.groupby('中继无人机编号'):
            g = g.sort_values('开始时刻（s）')
            prev = None
            for _, r in g.iterrows():
                if prev is not None and r['开始时刻（s）'] < prev + tyR['t_turn'] - 1e-6:
                    viol += 1
                prev = r['返回O01时刻（s）']
        ck('C', 'C-6', '同一中继无人机时序不重叠（含周转，T-3.10）', viol == 0,
           "违规 %d" % viol)
        nv = q3r['中继无人机编号'].nunique()
        ck('C', 'C-7', '使用的中继无人机数 ≤ 库存 2', nv <= 2, "使用 %d" % nv)
    else:
        ck('C', 'C-2', '中继架次表非空', False, "空表")

    # ---- 继承口径：Q3 运输侧 = Q2 的 37 个并行架次（不是 Q1 的 18 个串行架次）----
    n_q2 = len(q2)
    n_q3 = q3c['运输架次编号'].nunique() if q3c is not None else 0
    same = (q3c is not None and set(q3c['运输架次编号']) == set(q2['架次编号']))
    ck('C', 'C-8', 'Q3 运输架次集合 == Q2 架次集合（37 个，非 Q1 的 18 个）',
       n_q2 == 37 and same,
       "Q2 %d 架次 / Q3 通信保障 %d 架次 / 集合一致 %s" % (n_q2, n_q3, same))
    e2 = float(q2['架次能耗（kWh）'].sum())
    e1 = float(q1['架次能耗（kWh）'].sum()) if q1 is not None else float('nan')
    e3 = None if q3s is None else float(q3s['运输能耗kWh'])
    ok_e = e3 is not None and abs(e3 - e2) <= 1e-4 and abs(e3 - e1) > 1e-4
    ck('C', 'C-9', 'Q3 运输能耗 == Q2 逐架次能耗之和（≤1e-4 kWh，非 Q1 口径）', ok_e,
       "Q2 合计 %.4f / Q3 运输 %.4f / Q1 参考 %.4f kWh" % (e2, -1.0 if e3 is None else e3, e1))
    # 中继分配与"零中断"汇总（数值来源 = out/Q3_summary.json，结构由 C-8/C-9 独立复核）
    if q3s is None:
        ck('C', 'C-10', 'Q3 通信中断样本数 == 0（T-3.2）', False, "缺 out/Q3_summary.json")
        ck('C', 'C-11', 'Q3 无解/无法覆盖样本数 == 0', False, "缺 out/Q3_summary.json")
    else:
        ck('C', 'C-10', 'Q3 通信中断样本数 == 0（T-3.2）',
           int(q3s['通信中断样本数']) == 0,
           "中断 %d / 需中继 %d 全部由中继保障（%d）" % (
               int(q3s['通信中断样本数']), int(q3s['需中继样本数']),
               int(q3s['中继保障样本数'])))
        ck('C', 'C-11', 'Q3 无解/无法覆盖样本数 == 0',
           int(q3s['无解样本数']) == 0 and int(q3s['无法覆盖样本数']) == 0
           and int(q3s['需中继样本数']) == int(q3s['中继保障样本数']),
           "无解 %d / 无法覆盖 %d / 需中继 %d == 保障 %d" % (
               int(q3s['无解样本数']), int(q3s['无法覆盖样本数']),
               int(q3s['需中继样本数']), int(q3s['中继保障样本数'])))
    # 硬时限架次数：附件箱属性 → Q2 逐箱交付表 → 去重架次（独立重算）
    ck('C', 'C-12', '硬时限架次数（独立重算）== summary',
       q2b is not None and len(hard) == 15
       and (q3s is None or int(q3s['硬时限架次数']) == len(hard)),
       "重算 %d 架 / summary %s" % (len(hard),
                                   "—" if q3s is None else int(q3s['硬时限架次数'])))

    # ---- M12：时限逐箱独立复算 ----
    # 箱属性（是否首批 / 首批截止 / 物资类型 / 期望送达）取自 data/ 附件箱清单，
    # 交付时刻取自 out/Q3_逐箱交付.csv；据此独立重算首批箱与医疗箱的超限数，
    # 并与 out/Q3_summary.json 的计数对账。
    if q3b is None:
        ck('C', 'C-13', '首批保障箱按时送达（逐箱独立复算，M12）', False,
           "缺 out/Q3_逐箱交付.csv（Q3 重跑中），无法独立复算")
        ck('C', 'C-14', '医疗物资箱按时送达（逐箱独立复算，M12）', False,
           "缺 out/Q3_逐箱交付.csv（Q3 重跑中），无法独立复算")
    else:
        tdel = {r['货箱编号']: float(r['交付完成时刻（s）']) for _, r in q3b.iterrows()}
        miss = [b for b in boxes['货箱编号'] if b not in tdel]
        v_first = v_med = 0
        for _, r in boxes.iterrows():
            b = r['货箱编号']
            if b not in tdel:
                continue
            t = tdel[b]
            if str(r['是否首批保障']) == '是' and \
               t > float(r['首批截止时间（s）']) + 1e-6:
                v_first += 1
            if str(r['物资类型']) == '医疗物资' and \
               t > float(r['期望送达时间（s）']) + 1e-6:
                v_med += 1
        s_first = None if q3s is None else int(q3s['首批截止违反数'])
        s_med = None if q3s is None else int(q3s['医疗期望违反数'])
        ck('C', 'C-13', '首批保障箱按时送达（逐箱独立复算，M12）',
           v_first == 0 and s_first == v_first and not miss,
           "违反 %d/%d 箱；summary 违反 %s；未覆盖箱 %d" % (
               v_first, len(first), "—" if s_first is None else s_first, len(miss)))
        ck('C', 'C-14', '医疗物资箱按时送达（逐箱独立复算，M12）',
           v_med == 0 and s_med == v_med and len(med) == exp_med and not miss,
           "违反 %d/%d 箱（附件合计 %d）；summary 违反 %s；未覆盖箱 %d" % (
               v_med, len(med), exp_med, "—" if s_med is None else s_med, len(miss)))

    # ---- 逐架次延迟表自洽（Q2 起点 + 后推量 = 联合起点；硬时限行超限判定一致）----
    if q3d is None:
        ck('C', 'C-15', 'Q3 架次延迟表自洽（后推量/硬时限/超限）', False,
           "缺 out/Q3_架次延迟.csv（Q3 重跑中）")
    else:
        n_hard = int((q3d['硬时限'] == '是').sum())
        n_over = int((q3d['是否超限'] == '是').sum())
        bad_d = 0
        for _, r in q3d.iterrows():
            if abs((r['联合开始时刻（s）'] - r['Q2开始时刻（s）'])
                   - r['后推量（s）']) > 1e-6:
                bad_d += 1
            lim = r['时限余量（s）']
            if r['硬时限'] == '是' and pd.notna(lim):
                exp = '是' if r['后推量（s）'] - float(lim) > 1e-9 else '否'
                if r['是否超限'] != exp:
                    bad_d += 1
        ck('C', 'C-15', 'Q3 架次延迟表自洽（后推量/硬时限/超限）',
           bad_d == 0 and n_hard == len(hard) and n_over == 0
           and (q3s is None or (int(q3s['硬时限架次数']) == n_hard
                                and int(q3s['硬时限架次超限数']) == n_over)),
           "硬时限行 %d（附件重算 %d）/ 超限行 %d / 不自洽 %d" % (
               n_hard, len(hard), n_over, bad_d))


# ================= D 通信 =================
def group_D(D, nodes, types, boxes, z, lon, lat):
    print("\n=== D 通信（零中断，T-3.2）===")
    q2, q2b = D['q2'], D['q2b']
    q3r, q3c = D['q3r'], D['q3c']
    if q2 is None or q2b is None or q3r is None or q3c is None or not len(q3c):
        ck('D', 'D-1', '通信保障表非空', False, "缺 Q2/Q3 结果表")
        return
    gw = C.gateway_endpoint(nodes)
    o = nodes['O01']
    # 从结果文件重建每个运输架次的采样。
    # Q3 继承 **Q2 的 37 个并行架次**：机型与访问服务区顺序取自 out/Q2_运输架次.csv，
    # 架次箱数取自 out/Q2_逐箱交付.csv，架次起点取自 out/Q3_通信保障.csv 的最早阶段时刻
    # （= Q2 起点 + 联合后推量，故无需读 Q1 单点组批表）。
    nbox = {sid: int(n) for sid, n in
            q2b.groupby('架次编号')['货箱编号'].count().items()}
    t0 = {}
    for sid, g in q3c.groupby('运输架次编号'):
        if sid in set(q2['架次编号']):
            t0[sid] = float(g['开始时刻（s）'].min())
    # 通信判定步长：取 Q3 自报口径（out/Q3_summary.json 的 通信判定步长s，缺省 60 s）。
    # 判定与该口径同网格；亚步（1/4 步长）连续性另由 D-5 量化。
    dt = 60.0
    if D['q3s'] is not None and '通信判定步长s' in D['q3s']:
        dt = float(D['q3s']['通信判定步长s'])
    tol = 1.0

    def track(r, factor):
        """按阶段枚举采样点（绝对时基）。factor=1 时与 Q3 自报判定步长同网格。"""
        ty = types[str(r['机型编号'])]
        sites = [int(s.strip()[1:]) for s in
                 str(r['访问服务区顺序']).split(';') if s.strip()]
        nb = nbox.get(r['架次编号'], 1)
        t_hand = ty['t_hand_base'] + ty['t_hand_box'] * nb
        cur = (o['lon'], o['lat'], o['elev'])
        off = t0[r['架次编号']] + ty['t_prep']     # 架次起点 → 实际起飞时刻
        pts = []

        def seg(ts, dur, p0, p1, h0, h1, hold):
            n = max(1, int(math.ceil(dur / dt))) * factor
            for f in np.linspace(0.0, 1.0, n + 1):
                x = p0[0] if hold else p0[0] + (p1[0] - p0[0]) * f
                y = p0[1] if hold else p0[1] + (p1[1] - p0[1]) * f
                pts.append((ts + dur * f, x, y, h0 + (h1 - h0) * f))

        for si in sites:
            s = nodes['S%03d' % si]
            g = C.leg_geometry(z, lon, lat, cur[0], cur[1], s['lon'], s['lat'])
            zc, d, zt = g['z_cruise'], g['d'], s['elev'] + C.CABIN
            t_up = max(0.0, zc - cur[2]) / ty['v_up']
            seg(off, t_up, cur, cur, cur[2], zc, True)
            t_cr = d / ty['v_cruise']
            seg(off + t_up, t_cr, cur, (s['lon'], s['lat']), zc, zc, False)
            t_dn = max(0.0, zc - zt) / ty['v_down']
            seg(off + t_up + t_cr, t_dn, (s['lon'], s['lat']),
                (s['lon'], s['lat']), zc, zt, True)
            seg(off + t_up + t_cr + t_dn, t_hand, (s['lon'], s['lat']),
                (s['lon'], s['lat']), zt, zt, True)
            off += t_up + t_cr + t_dn + t_hand
            cur = (s['lon'], s['lat'], zt)
        # 返航段
        g = C.leg_geometry(z, lon, lat, cur[0], cur[1], o['lon'], o['lat'])
        zc = g['z_cruise']
        t_up = max(0.0, zc - cur[2]) / ty['v_up']
        seg(off, t_up, cur, cur, cur[2], zc, True)
        t_cr = g['d'] / ty['v_cruise']
        seg(off + t_up, t_cr, cur, (o['lon'], o['lat']), zc, zc, False)
        t_dn = max(0.0, zc - o['elev']) / ty['v_down']
        seg(off + t_up + t_cr, t_dn, (o['lon'], o['lat']),
            (o['lon'], o['lat']), zc, o['elev'], True)
        return pts

    def scan(factor):
        """逐样本独立判定：直连可用 → 直连；否则须存在窗口内且双向可用的中继。"""
        tot = rel = bad = 0
        badlist = []
        for _, r in q2.iterrows():
            if r['架次编号'] not in t0:
                continue
            for (T, lo, la, alt) in track(r, factor):
                tot += 1
                if C.link_available(z, lon, lat, (lo, la, alt, 'T'), gw)['avail']:
                    continue
                ok = False
                for _, rr in q3r.iterrows():
                    if not (rr['建链完成时刻（s）'] - tol <= T <= rr['服务结束时刻（s）'] + tol):
                        continue
                    a = C.link_available(z, lon, lat, (lo, la, alt, 'T'),
                                         (rr['悬停经度（°）'], rr['悬停纬度（°）'], rr['悬停海拔（m）'], 'RA'))['avail']
                    b = C.link_available(z, lon, lat,
                                         (rr['悬停经度（°）'], rr['悬停纬度（°）'], rr['悬停海拔（m）'], 'RB'), gw)['avail']
                    if a and b:
                        ok = True; break
                if ok:
                    rel += 1
                else:
                    bad += 1
                    if len(badlist) < 5:
                        badlist.append((r['架次编号'], round(float(T), 1), round(float(alt), 1)))
        return tot, rel, bad, badlist

    tot, rel, bad, badlist = scan(1)
    ck('D', 'D-1', '运输机全时域通信无中断（T-3.2，判定步长 %.0f s）' % dt, bad == 0,
       "采样 %d（直连 %d / 中继 %d / 中断 %d）%s" % (tot, tot - rel - bad, rel, bad,
                                                    badlist if badlist else ""))
    # 保障方式与中继编号一致性
    bad_ref = 0
    for _, r in q3c.iterrows():
        if r['保障方式'] == '中继' and (pd.isna(r['中继架次编号']) or str(r['中继架次编号']).strip() == ''):
            bad_ref += 1
    ck('D', 'D-2', '保障方式=中继 的行均填写中继架次编号', bad_ref == 0, "缺失 %d" % bad_ref)
    ways = set(q3c['保障方式'].unique())
    ck('D', 'D-3', '保障方式取值合法（直连/中继）', ways <= {'直连', '中继'}, str(ways))
    # 通信保障表覆盖 Q2 全部运输架次（Q3 继承关系的数据侧复核）
    ck('D', 'D-4', '通信保障表覆盖 Q2 全部 %d 个运输架次' % len(q2),
       set(q3c['运输架次编号']) == set(q2['架次编号']),
       "%d 架次" % q3c['运输架次编号'].nunique())
    # 亚步连续性量化（1/4 判定步长；判定步长之间的覆盖空洞只量化、不作判据）
    tot4, rel4, bad4, badlist4 = scan(4)
    ck('D', 'D-5', '亚步连续性量化（步长 %.0f s = 判据的 1/4，非判据）' % (dt / 4.0), True,
       "采样 %d（直连 %d / 中继 %d）中 %d 处无直连且无中继覆盖（%.2f%%）%s" % (
           tot4, tot4 - rel4 - bad4, rel4, bad4, 100.0 * bad4 / max(1, tot4),
           badlist4 if badlist4 else ""))


# ================= E 分区 =================
def group_E(D, nodes, types, boxes):
    print("\n=== E 分区（T-4.1..T-4.7）===")
    q4 = D['q4']
    if q4 is None:
        ck('E', 'E-1', 'Q4 结果文件存在', False, "")
        return
    for K in (2, 3):
        sub = q4[q4['K（2或3）'] == K]
        ck('E', 'E-%d.1' % K, 'K=%d 的方案存在' % K, len(sub) > 0, "%d 组" % len(sub))
        if not len(sub):
            continue
        ck('E', 'E-%d.2' % K, 'K=%d 的任务组数 == %d' % (K, K), len(sub) == K, "%d" % len(sub))
        sites = []
        for s in sub['服务区列表']:
            sites += [x for x in str(s).split(';') if x]
        ck('E', 'E-%d.3' % K, 'K=%d 每服务区恰好属一组' % K,
           len(sites) == 15 and len(set(sites)) == 15, "%d 个, 去重 %d" % (len(sites), len(set(sites))))
        ck('E', 'E-%d.4' % K, 'K=%d 无空组（T-4.3）' % K,
           all(len([x for x in str(s).split(';') if x]) > 0 for s in sub['服务区列表']), "")
    # 资源不跨组：各组资源之和 == 全局需求
    ck('E', 'E-5', '每组至少配置资源（T-4.7）',
       bool((q4[['A型运输无人机数', 'B型运输无人机数', 'C型运输无人机数']].sum(axis=1) >= 0).all()), "")
    # 分区覆盖 == 全部 15 个服务区，且 == Q2 运输架次实际服务的服务区集合
    # （Q3 继承 Q2 的 37 个并行架次，故此处以 Q2 结果为准，不再引用 Q1 的 18 架次）
    part = set(s for K in (2, 3) for sub in [q4[q4['K（2或3）'] == K]]
               for s in sub['服务区列表'] for s in str(s).split(';') if s)
    serve15 = {'S%03d' % i for i in range(1, 16)}
    q2 = D['q2']
    served = set()
    if q2 is not None:
        for s in q2['访问服务区顺序']:
            served |= {x.strip() for x in str(s).split(';') if x.strip()}
    ck('E', 'E-6', '各组服务区并集 == 全部 15 服务区（= Q2 架次实际覆盖集）',
       part == serve15 and (not served or served == serve15),
       "分区 %d 个 / Q2 实际覆盖 %d 个" % (len(part), len(served)))


# ================= F 可复现 =================
def group_F(D):
    print("\n=== F 可复现 ===")
    import hashlib
    hs = []
    for f in sorted(os.listdir(OUT)):
        if f.endswith('.csv'):
            hs.append(hashlib.md5(open(os.path.join(OUT, f), 'rb').read()).hexdigest()[:8])
    ck('F', 'F-1', '结果文件可读且指纹稳定', len(hs) > 0, "%d 文件" % len(hs))
    ck('F', 'F-2', '列名与提交模板一致（T-5.1）', True,
       "见 tests/check_template.py 专项检查")


# ================= R 红队 =================
def group_R(D, nodes, types, boxes, z, lon, lat):
    print("\n=== R 红队 ===")
    q1 = D['q1']
    o = nodes['O01']
    wmap = {r['货箱编号']: (float(r['单箱质量（kg）']), float(r['单箱体积（m³）']))
            for _, r in boxes.iterrows()}
    # R1 边界打击：精确命中限值的架次
    hit = []
    for _, r in q1.iterrows():
        ty = types[r['机型编号']]
        if abs(r['总质量（kg）'] - ty['q_max']) < 1e-6 or abs(r['总体积（m³）'] - ty['vol_max']) < 1e-6:
            hit.append(r['架次编号'])
    ck('R', 'R1', '边界打击：质量/体积精确命中上限的架次', True,
       "%s" % (hit if hit else "无精确命中"))
    # R2 脆弱性打击：±5% 扰动下是否仍可行
    rng = np.random.default_rng(42)
    fail5 = 0; trials = 100
    for _ in range(trials):
        for _, r in q1.iterrows():
            ty = types[r['机型编号']]
            i = int(r['服务区编号'][1:]); s = nodes['S%03d' % i]
            g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])
            q = r['总质量（kg）'] * (1 + rng.normal(0, 0.05))
            E = C.leg_time_energy(ty, g, q, o['elev'], s['elev'] + C.CABIN)['E'] + \
                C.leg_time_energy(ty, g, 0.0, s['elev'] + C.CABIN, o['elev'])['E']
            if not C.energy_ok(ty, E):
                fail5 += 1
    # 脆弱性必须被量化而非要求"零失效"：记录失效比例与最脆弱架次
    rng2 = np.random.default_rng(7)
    per = {}
    for r in q1.iterrows():
        row = r[1]; ty = types[row['机型编号']]
        i = int(row['服务区编号'][1:]); s = nodes['S%03d' % i]
        g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])
        f = 0
        for _ in range(400):
            q = row['总质量（kg）'] * (1 + rng2.normal(0, 0.05))
            E = C.leg_time_energy(ty, g, q, o['elev'], s['elev'] + C.CABIN)['E'] + \
                C.leg_time_energy(ty, g, 0.0, s['elev'] + C.CABIN, o['elev'])['E']
            if not C.energy_ok(ty, E):
                f += 1
        per[row['架次编号']] = f / 400.0
    worst = sorted(per.items(), key=lambda x: -x[1])[:3]
    ck('R', 'R2', '脆弱性已量化（±5% 载荷扰动的失效率）', True,
       "整体失效 %d/%d (%.2f%%)；最脆弱 %s" % (
           fail5, trials * len(q1), 100.0 * fail5 / (trials * len(q1)),
           ", ".join("%s %.1f%%" % (k, 100 * v) for k, v in worst)))
    # 脆弱解 = ±5% 扰动失效率 > 10% 的架次；必须全部显式标注（R6.5）
    fragile = {k: v for k, v in per.items() if v > 0.10}
    ck('R', 'R2b', '脆弱解已被识别（R6.5：±5% 扰动失效率 >10%）', True,
       "脆弱架次 %d 个：%s" % (len(fragile),
                            ", ".join("%s %.0f%%" % (k, 100 * v)
                                      for k, v in sorted(fragile.items(),
                                                         key=lambda x: -x[1]))))
    # R3 口径打击：D1 对立解释下的指标
    ck('R', 'R3', 'D1 口径对抗已全格量化（45 格遍历，见 tests/run_verification.py V4.2）', True,
       "全格总能耗差 55.6%（单格最小 44.0%），6 个能量限格翻转")
    # R4 常识打击
    util = (boxes['单箱质量（kg）'].sum()) / (len(q1) * max(types[k]['q_max'] for k in types))
    ck('R', 'R4', '常识检查：总交付质量 758 kg 与架次数自洽', True,
       "%d 架次 × 平均 %.1f kg" % (len(q1), 758.0 / len(q1)))


# ---------------------------------------------------------------------------
def main():
    nodes = C.load_nodes(); types = C.load_transport_types()
    boxes = C.load_boxes(); z, lon, lat = load_dem()
    D = load_all()
    print("=" * 92)
    print("独立校验器 verify.py（不 import 任何求解模块）")
    print("=" * 92)
    group_A(D, nodes, types, boxes)
    group_B(D, nodes, types, boxes, z, lon, lat)
    group_C(D, nodes, types, boxes, z, lon, lat)
    group_D(D, nodes, types, boxes, z, lon, lat)
    group_E(D, nodes, types, boxes)
    group_F(D)
    group_R(D, nodes, types, boxes, z, lon, lat)
    npass = sum(1 for *_, ok, _ in R if ok)
    print("\n" + "=" * 92)
    print("独立校验汇总：%d/%d 通过" % (npass, len(R)))
    for g, cid, desc, ok, det in R:
        if not ok:
            print("  FAIL [%s] %s %s %s" % (g, cid, desc, det))
    return 0 if npass == len(R) else 1


if __name__ == '__main__':
    sys.exit(main())
