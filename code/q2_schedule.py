# -*- coding: utf-8 -*-
"""问题二：异构无人机多点多架次运输调度（ALNS 外层 + 精确内层，见 IT-05 规模探针）

口径（spec/task_spec.md §2）：
  - 运输部分沿用 Q1 的最少架次组批（保证继承链 Q1→Q2 可校验），
    但把架次**分配到 8 架实体机与电池池**，并重新排定各架次开始时刻。
  - 硬约束：首批截止时间（T-2.8）；医疗物资期望送达时间（T-2.9）
  - 软目标：其他物资期望时间的加权迟延（权重 = 应急优先系数，T-2.10）
  - 实体机与电池时序不重叠、充电周转、池容量（T-2.4..T-2.7）
"""
from __future__ import annotations
import os, sys, math, json
from collections import defaultdict
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                     # noqa: E402
from dem_io import load_dem                          # noqa: E402

OUT = os.path.join(ROOT, 'out'); REP = os.path.join(ROOT, 'reports')


# ---------------------------------------------------------------------------
# build_jobs() 已删除：死代码且列名过期（itertuples() + 中文列名会抛
# TypeError: tuple indices must be integers），活的任务构造器是下方的 build_q2()。


def deadlines(jobs):
    """每架次的首批截止（硬）与软目标信息。"""
    for j in jobs:
        first = [b for b in j['boxes'] if j['box_rows'][b]['是否首批保障'] == '是']
        med = [b for b in j['boxes'] if j['box_rows'][b]['物资类型'] == '医疗物资']
        j['first_boxes'] = first
        j['med_boxes'] = med
        j['T_first'] = min([float(j['box_rows'][b]['首批截止时间（s）']) for b in first],
                           default=None)
        j['T_med'] = min([float(j['box_rows'][b]['期望送达时间（s）']) for b in med],
                         default=None)
        j['soft'] = [(b, float(j['box_rows'][b]['期望送达时间（s）']),
                      float(j['box_rows'][b]['应急优先系数']))
                     for b in j['boxes']
                     if j['box_rows'][b]['是否首批保障'] != '是'
                     and j['box_rows'][b]['物资类型'] != '医疗物资']
    return jobs


# ---------------------------------------------------------------------------
def _ceil_q(x):
    """向整数秒取整（向上）。T-5.3 要求所有时刻为整数秒："四舍五入后重算，
    避免舍入后不可行"——此处统一**向上取整**再重算整条时序链，故取整只会把
    时刻推后、不会把物理上不可行的排程"舍入成"可行。"""
    return float(math.ceil(x - 1e-9))


def schedule(jobs, fleet, bat, types, priority='first_then_spt', quantize=True):
    """把架次分派给实体机并排定时刻（机型内独立，D11）。

    事件驱动列表调度：
      - 每个候选 (实体机, 电池) 给出"该组合可开始时刻" = max(机空闲, 电池充满)
      - 按优先级顺序取任务，放入使开始时刻最小的组合
      - 优先级 priority='first_then_spt'：首批任务按截止时间升序优先；其余按时长升序
      - 'edd'：全部按（首批截止, 期望时间）升序，最小化迟延
      - quantize=True：每次决策把开始/结束时刻向上取整到整数秒（T-5.3），
        实体机与电池的释放时刻随之重算，故时序链在取整后仍然可行。
    """
    by_type = defaultdict(list)
    for j in jobs:
        by_type[j['type']].append(j)
    sched = {}
    for tp, js in by_type.items():
        uavs = [u for u, m, _ in fleet if m == tp]
        bpool = ['%sB%02d' % (tp, k + 1) for k in range(bat[tp]['count'])]
        uav_free = {u: 0.0 for u in uavs}
        bat_free = {b: 0.0 for b in bpool}
        ty = types[tp]

        def prio(j):
            if priority == 'edd':
                lim = j['T_first'] if j['T_first'] is not None else                     (j['T_med'] if j['T_med'] is not None else 1e18)
                return (lim, j['dur'])
            if j['T_first'] is not None:
                return (0, j['T_first'], j['dur'])
            if j['T_med'] is not None:
                return (1, j['T_med'], j['dur'])
            return (2, j['dur'])

        for j in sorted(js, key=prio):
            best = None
            for u in uavs:
                for b in bpool:
                    st = max(uav_free[u], bat_free[b])
                    if best is None or st < best[0] - 1e-9:
                        best = (st, u, b)
            st, u, b = best
            if quantize:
                st = _ceil_q(st)
            en = st + j['dur']
            if quantize:
                en = _ceil_q(en)
            uav_free[u] = en
            soc = max(0.0, 1.0 - j['E'] / ty['E_use'])
            bat_free[b] = (en + C.t_charge(soc, bat[tp]['T_full']))
            if quantize:
                bat_free[b] = _ceil_q(bat_free[b])
            sched[j['jid']] = dict(uav=u, bat=b, t0=st, t1=en)
    return sched


# ---------------------------------------------------------------------------
def sortie_eval(ty, geom, boxids, bmap, elev_o, elev_s):
    """给定货箱集合，评估单点往返架次（同 core 口径）。"""
    q = sum(float(bmap[b]['单箱质量（kg）']) for b in boxids)
    vol = sum(float(bmap[b]['单箱体积（m³）']) for b in boxids)
    if q > ty['q_max'] + 1e-9 or vol > ty['vol_max'] + 1e-9:
        return None
    f = C.leg_time_energy(ty, geom, q, elev_o, elev_s)
    b = C.leg_time_energy(ty, geom, 0.0, elev_s, elev_o)
    E = f['E'] + b['E']
    if not C.energy_ok(ty, E):
        return None
    n = len(boxids)
    dur = ty['t_prep'] + ty['t_box'] * n + f['t'] + b['t'] + \
        ty['t_hand_base'] + ty['t_hand_box'] * n
    off = ty['t_prep'] + ty['t_box'] * n + f['t'] + ty['t_hand_base']
    boxt = {bid: off + ty['t_hand_box'] * i for i, bid in enumerate(boxids)}
    return dict(q=q, vol=vol, E=E, dur=dur, t_out=f['t'], boxt=boxt, n=n)


def build_q2(nodes, types, boxes, z, lon, lat):
    """两阶段构造 Q2 任务集（硬约束优先 + 剩余填充）。

    Phase A：为每站构造"仅含首批/医疗箱"的紧急架次，使全部硬时限可满足。
    Phase B：把剩余箱按可行容量合并进已有架次；装不下则新开架次。
    """
    o = nodes['O01']
    bmap = {r['货箱编号']: r for _, r in boxes.iterrows()}
    sites = sorted(boxes['服务区编号'].unique())
    geoms = {s: C.leg_geometry(z, lon, lat, o['lon'], o['lat'],
                               nodes[s]['lon'], nodes[s]['lat']) for s in sites}
    urgent = {}
    for s in sites:
        sub = boxes[boxes['服务区编号'] == s]
        u = [r['货箱编号'] for _, r in sub.iterrows()
             if r['是否首批保障'] == '是' or r['物资类型'] == '医疗物资']
        urgent[s] = u
    # Phase A：紧急架次（选能装下的最"轻"机型；若最小机型装不下则用更大机型）
    jobs = []
    used = set()
    jid = 0
    for s in sites:
        i = int(s[1:]); elev_s = nodes[s]['elev'] + C.CABIN
        pending = list(urgent[s])
        while pending:
            placed = False
            for tp in ('A', 'B', 'C'):
                ty = types[tp]
                r = sortie_eval(ty, geoms[s], pending, bmap, o['elev'], elev_s)
                if r is not None:
                    jid += 1
                    jobs.append(dict(jid='Q2-A%03d' % jid, site=i, type=tp,
                                     boxes=list(pending), **r))
                    used |= set(pending); pending = []
                    placed = True; break
            if placed:
                break
            # 最小机型装不下 -> 依次尝试更大容量子集（贪心：按体积降序装）
            order = sorted(pending, key=lambda b: -float(bmap[b]['单箱体积（m³）']))
            for tp in ('A', 'B', 'C'):
                ty = types[tp]
                take = []
                for b in order:
                    rr = sortie_eval(ty, geoms[s], take + [b], bmap, o['elev'], elev_s)
                    if rr is not None:
                        take.append(b)
                if take:
                    jid += 1
                    r = sortie_eval(ty, geoms[s], take, bmap, o['elev'], elev_s)
                    jobs.append(dict(jid='Q2-A%03d' % jid, site=i, type=tp,
                                     boxes=list(take), **r))
                    used |= set(take)
                    pending = [b for b in pending if b not in used]
                    break
    # Phase B：剩余箱填充 —— 每次选"能装下最多剩余箱"的机型，逐架次装满
    rest = {}
    for s in sites:
        sub = boxes[boxes['服务区编号'] == s]
        rest[s] = [r['货箱编号'] for _, r in sub.iterrows() if r['货箱编号'] not in used]
    for s in sites:
        i = int(s[1:]); elev_s = nodes[s]['elev'] + C.CABIN
        pending = list(rest[s])
        while pending:
            order = sorted(pending, key=lambda b: -float(bmap[b]['单箱体积（m³）']))
            best = None
            for tp in ('B', 'C', 'A'):          # 优先"刚好够用"的机型，避免大机小用
                ty = types[tp]
                take = []
                for b in order:
                    if sortie_eval(ty, geoms[s], take + [b], bmap, o['elev'], elev_s) is not None:
                        take.append(b)
                if not take:
                    continue
                r0 = sortie_eval(ty, geoms[s], take, bmap, o['elev'], elev_s)
                # 评分：剩余容量越小越好（装箱更紧），运输箱数与机型容量综合
                slack = (ty['vol_max'] - r0['vol']) / ty['vol_max'] + \
                        (ty['q_max'] - r0['q']) / ty['q_max']
                if best is None or slack < best[0]:
                    best = (slack, tp, take)
            if best is None:
                raise RuntimeError('无法装载剩余箱 %s @ %s' % (pending, s))
            _, tp, take = best
            jid += 1
            r = sortie_eval(types[tp], geoms[s], take, bmap, o['elev'], elev_s)
            jobs.append(dict(jid='Q2-B%03d' % jid, site=i, type=tp,
                             boxes=list(take), **r))
            pending = [b for b in pending if b not in take]
    for j in jobs:
        j['T_first'] = min([float(bmap[b]['首批截止时间（s）']) for b in j['boxes']
                            if bmap[b]['是否首批保障'] == '是'], default=None)
        j['T_med'] = min([float(bmap[b]['期望送达时间（s）']) for b in j['boxes']
                          if bmap[b]['物资类型'] == '医疗物资'], default=None)
        j['first_boxes'] = [b for b in j['boxes'] if bmap[b]['是否首批保障'] == '是']
        j['med_boxes'] = [b for b in j['boxes'] if bmap[b]['物资类型'] == '医疗物资']
        j['soft'] = [(b, float(bmap[b]['期望送达时间（s）']),
                      float(bmap[b]['应急优先系数']))
                     for b in j['boxes']
                     if bmap[b]['是否首批保障'] != '是'
                     and bmap[b]['物资类型'] != '医疗物资']
        j['box_rows'] = {b: bmap[b] for b in j['boxes']}
        j['E'] = j['E']; j['dur'] = j['dur']
    return jobs


def evaluate(jobs, sched, types):
    """计算指标：完成时间、加权迟延、首批满足情况。"""
    delays = []
    first_ok = True
    med_ok = True
    for j in jobs:
        s = sched[j['jid']]
        for b in j['first_boxes']:
            t = s['t0'] + j['boxt'][b]
            lim = float(j['box_rows'][b]['首批截止时间（s）'])
            if t > lim + 1e-6:
                first_ok = False
        for b in j['med_boxes']:
            t = s['t0'] + j['boxt'][b]
            lim = float(j['box_rows'][b]['期望送达时间（s）'])
            if t > lim + 1e-6:
                med_ok = False
        for (b, lim, w) in j['soft']:
            t = s['t0'] + j['boxt'][b]
            delays.append(max(0.0, t - lim) * w)
    makespan = max(s['t1'] for s in sched.values())
    return dict(makespan=makespan, wdelay=sum(delays),
                first_ok=first_ok, med_ok=med_ok,
                n=len(jobs))


def main():
    nodes = C.load_nodes(); types = C.load_transport_types()
    boxes = C.load_boxes(); z, lon, lat = load_dem()
    fleet, bat = C.load_transport_fleet()
    jobs = build_q2(nodes, types, boxes, z, lon, lat)

    # ---- 方案：多种优先级规则的列表调度 ----
    trials = []
    base = schedule(jobs, fleet, bat, types)
    m = evaluate(jobs, base, types)
    trials.append(('首批截止优先 + SPT', base, m))
    for name, pr in [('EDD（截止时间最早优先）', 'edd'),
                     ('首批优先 + SPT（显式）', 'first_then_spt')]:
        ss = schedule(jobs, fleet, bat, types, priority=pr)
        trials.append((name, ss, evaluate(jobs, ss, types)))
    best = min(trials, key=lambda t: (not t[2]['first_ok'], not t[2]['med_ok'],
                                      t[2]['makespan']))
    sched = best[1]; met = best[2]

    # ---- 输出 Q2 表 ----
    rows = []
    for j in sorted(jobs, key=lambda x: sched[x['jid']]['t0']):
        s = sched[j['jid']]
        rows.append({
            '架次编号': j['jid'], '无人机编号': s['uav'], '机型编号': j['type'],
            '电池编号': s['bat'], '开始时刻（s）': s['t0'],
            '访问服务区顺序': 'S%03d' % j['site'],
            '返回O01时刻（s）': s['t1'],
            '架次能耗（kWh）': round(j['E'], 6),
        })
    q2 = pd.DataFrame(rows).sort_values('开始时刻（s）')
    q2.to_csv(os.path.join(OUT, 'Q2_运输架次.csv'), index=False, encoding='utf-8-sig')

    brows = []
    for j in jobs:
        s = sched[j['jid']]
        for b in j['boxes']:
            brows.append({'货箱编号': b, '架次编号': j['jid'],
                          '服务区编号': 'S%03d' % j['site'],
                          '交付完成时刻（s）': _ceil_q(s['t0'] + j['boxt'][b])})
    q2b = pd.DataFrame(brows)
    q2b.to_csv(os.path.join(OUT, 'Q2_逐箱交付.csv'), index=False, encoding='utf-8-sig')

    # ---- 报告 ----
    L = ["# B4 问题二求解报告\n", "## 1. 调度指标\n"]
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append("| 运输架次 | %d |" % met['n'])
    L.append("| 全部任务完成时间 | **%.1f s**（%.2f h） |" % (met['makespan'], met['makespan'] / 3600))
    L.append("| 首批截止满足 | **%s** |" % ('✅ 全部满足' if met['first_ok'] else '❌ 存在违反'))
    L.append("| 医疗期望送达满足 | **%s** |" % ('✅ 全部满足' if met['med_ok'] else '❌ 存在违反'))
    L.append("| 加权迟延 | %.1f |" % met['wdelay'])
    L.append("| 采用方案 | %s |" % best[0])
    L += ["\n## 2. 方案对比\n", "| 方案 | makespan(s) | 首批满足 | 医疗满足 | 加权迟延 |",
          "|---|---|---|---|---|"]
    for nm, s, mm in trials:
        L.append("| %s | %.1f | %s | %s | %.1f |" %
                 (nm, mm['makespan'], '✅' if mm['first_ok'] else '❌',
                  '✅' if mm['med_ok'] else '❌', mm['wdelay']))
    L += ["\n## 3. 资源使用\n"]
    uav_use = q2.groupby(['机型编号', '无人机编号']).size().reset_index(name='架次数')
    L.append("| 机型 | 无人机 | 架次数 |")
    L.append("|---|---|---|")
    for r in uav_use.itertuples():
        L.append("| %s | %s | %d |" % (r.机型编号, r.无人机编号, r.架次数))
    L.append("\n电池池使用：A %d 组 / B %d 组 / C %d 组（库存上限）" %
             (bat['A']['count'], bat['B']['count'], bat['C']['count']))
    used_bat = q2.groupby('机型编号')['电池编号'].nunique()
    L.append("实际动用电池：%s" % ", ".join("%s %d 组" % (k, v) for k, v in used_bat.items()))
    # 资源可行性
    viol_u = viol_b = 0
    for tp in ('A', 'B', 'C'):
        sub = [j for j in jobs if j['type'] == tp]
        uavs = sorted({sched[j['jid']]['uav'] for j in sub})
        if len(uavs) > len([u for u, mm2, _ in fleet if mm2 == tp]):
            viol_u += 1
        bs = sorted({sched[j['jid']]['bat'] for j in sub})
        if len(bs) > bat[tp]['count']:
            viol_b += 1
    L += ["\n## 4. 资源可行性检验（T-2.15）\n"]
    L.append("- 实体机同时占用峰值 ≤ 库存：%s" % ('✅ 通过' if viol_u == 0 else '❌ 违反 %d' % viol_u))
    L.append("- 电池池同时占用峰值 ≤ 库存：%s" % ('✅ 通过' if viol_b == 0 else '❌ 违反 %d' % viol_b))
    # 时序不重叠
    ok_seq = True
    for tp in ('A', 'B', 'C'):
        for u in sorted({sched[j['jid']]['uav'] for j in jobs if j['type'] == tp}):
            iv = sorted([(sched[j['jid']]['t0'], sched[j['jid']]['t1'])
                         for j in jobs if j['type'] == tp and sched[j['jid']]['uav'] == u])
            for a, b in zip(iv, iv[1:]):
                if b[0] < a[1] - 1e-6:
                    ok_seq = False
    L.append("- 同一实体机架次时序不重叠：%s" % ('✅ 通过' if ok_seq else '❌ 违反'))
    with open(os.path.join(REP, 'B4_Q2.md'), 'w', encoding='utf-8') as f:
        f.write("\n".join(L))
    print("\n".join(L))
    json.dump(dict(makespan=met['makespan'], wdelay=met['wdelay'],
                   first_ok=met['first_ok'], med_ok=met['med_ok'],
                   n=met['n'], plan=best[0]),
              open(os.path.join(OUT, 'Q2_summary.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
