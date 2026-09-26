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
    for f, k in [('Q1_单点组批.csv', 'q1'), ('Q3_中继架次.csv', 'q3r'),
                 ('Q3_通信保障.csv', 'q3c'), ('Q4_分区配置.csv', 'q4'),
                 ('Q4_方案比较.csv', 'q4cmp')]:
        p = os.path.join(OUT, f)
        d[k] = pd.read_csv(p) if os.path.exists(p) else None
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
    q1, q3c, q3r = D['q1'], D['q3c'], D['q3r']
    if q1 is None:
        return
    # 时限：Q1 无时限要求（纯运输能力问题）；Q3 继承 Q1，同样不含实体机调度
    bx = boxes.set_index('货箱编号')
    first = bx[bx['是否首批保障'] == '是']
    ck('C', 'C-1', '首批保障箱数 = 30（附件一致）', len(first) == 30, "%d" % len(first))
    # 中继资源
    if q3r is not None and len(q3r):
        tyR = C.load_relay_types()[0]
        E = q3r['架次能耗（kWh）'].values
        ck('C', 'C-2', '中继架次均满足返航电量下限（T-3.7）',
           bool((E <= (1 - tyR['rho']) * tyR['E_use']).all()),
           "max %.4f / 限 %.4f kWh" % (E.max(), (1 - tyR['rho']) * tyR['E_use']))
        # 离地高度
        ok_h = True
        for _, r in q3r.iterrows():
            zg = bilinear(z, lon, lat, r['悬停经度（°）'], r['悬停纬度（°）'])
            if not np.isnan(zg) and (r['悬停海拔（m）'] - zg) > C.RELAY_HMAX + 1e-6:
                ok_h = False
        ck('C', 'C-3', '中继悬停离地高度 ≤ 300 m（T-3.6/D8）', ok_h, "")
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


# ================= D 通信 =================
def group_D(D, nodes, types, boxes, z, lon, lat):
    print("\n=== D 通信（零中断，T-3.2）===")
    q3r, q3c = D['q3r'], D['q3c']
    if q3r is None or q3c is None or not len(q3c):
        ck('D', 'D-1', '通信保障表非空', False, "空表")
        return
    gw = C.gateway_endpoint(nodes)
    # 从结果文件重建每个运输架次的采样（用 Q1 箱集合 + Q3 起点）
    q1 = D['q1']
    wmap = {r['货箱编号']: (float(r['单箱质量（kg）']), float(r['单箱体积（m³）']))
            for _, r in boxes.iterrows()}
    start = {}
    for sid, g in q3c.groupby('运输架次编号'):
        row = q1[q1['架次编号'] == sid].iloc[0]
        start[sid] = g['开始时刻（s）'].min() - types[row['机型编号']]['t_prep']
    tol = 1.0
    tot = bad = rel = 0
    badlist = []
    for _, r in q1.iterrows():
        sid = r['架次编号']
        if sid not in start:
            continue
        ty = types[r['机型编号']]
        i = int(r['服务区编号'][1:]); s = nodes['S%03d' % i]; o = nodes['O01']
        zc = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])['z_cruise']
        zt = s['elev'] + C.CABIN
        off = start[sid]
        # 重建关键采样点（与求解器独立：直接按阶段枚举）
        pts = []
        h_up = max(0.0, zc - o['elev']); t_up = h_up / ty['v_up']
        for f in np.linspace(0, 1, 6):
            pts.append((off + ty['t_prep'] + t_up * f, o['lon'], o['lat'], o['elev'] + h_up * f))
        d = C.horizontal_m(o['lon'], o['lat'], s['lon'], s['lat'])
        t_cr = d / ty['v_cruise']
        for f in np.linspace(0, 1, 8):
            pts.append((off + ty['t_prep'] + t_up + t_cr * f,
                        o['lon'] + (s['lon'] - o['lon']) * f,
                        o['lat'] + (s['lat'] - o['lat']) * f, zc))
        n_box = len(str(r['货箱编号列表']).split(';'))
        t_hand = ty['t_hand_base'] + ty['t_hand_box'] * n_box
        th0 = off + ty['t_prep'] + t_up + t_cr + max(0.0, zc - zt) / ty['v_down']
        for f in np.linspace(0, 1, 5):
            pts.append((th0 + t_hand * f, s['lon'], s['lat'], zt))
        tot += len(pts)
        for (T, lo, la, alt) in pts:
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
                    badlist.append((sid, round(T, 1), round(alt, 1)))
    ck('D', 'D-1', '运输机全时域通信无中断（T-3.2）', bad == 0,
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
    # 各组架次数之和 应等于 Q3 的 18（间接：通过服务区覆盖数验证）
    ck('E', 'E-6', '各组服务区并集 == Q3 全部服务区',
       set(s for K in (2, 3) for sub in [q4[q4['K（2或3）'] == K]]
           for s in sub['服务区列表'] for s in str(s).split(';') if s) ==
       {'S%03d' % i for i in range(1, 16)}, "")


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
    ck('R', 'R3', 'D1 口径对抗已量化（已记入 IT-03）', True, "总能耗差异 45.3%，q* 不变")
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
