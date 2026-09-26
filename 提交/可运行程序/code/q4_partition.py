# -*- coding: utf-8 -*-
"""问题四：救援任务分区与资源配置优化

依据 spec/task_spec.md §4（T-4.1..T-4.10）与 D12。
关键口径：冻结 Q3 的任务结构与通信保障关系，仅**重新核算资源数量**（D12-b）。

资源需求按"组内独立排程"精确计算：
  - 有中继需求的运输架次必须由中继保障；每组的中继架次在同一悬停点/时段串行
  - 无中继需求的运输架次可自由并行
  - 电池需求追踪时间轴上的"占用 + 充电"峰值
"""
from __future__ import annotations
import os, sys, math, json, itertools
from collections import defaultdict
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                          # noqa: E402

OUT = os.path.join(ROOT, 'out'); REP = os.path.join(ROOT, 'reports')
STOCK = dict(A=4, B=2, C=2)                       # 运输无人机库存
BAT_STOCK = dict(A=6, B=4, C=4)                   # 共享电池库存
RELAY_STOCK = 2                                    # 中继无人机库存
RELAY_BAT_STOCK = 6                                # 中继能源组件库存


# ---------------------------------------------------------------------------
def load_frozen_task():
    """从 Q1/Q3 结果读取冻结的任务结构与通信关系。"""
    nodes = C.load_nodes(); types = C.load_transport_types()
    q1 = pd.read_csv(os.path.join(OUT, 'Q1_单点组批.csv'))
    q3r = pd.read_csv(os.path.join(OUT, 'Q3_中继架次.csv'))
    q3c = pd.read_csv(os.path.join(OUT, 'Q3_通信保障.csv'))
    tmap = {row['架次编号']: row['机型编号'] for _, row in q1.iterrows()}
    start = {sid: g['开始时刻（s）'].min() - types[tmap[sid]]['t_prep']
             for sid, g in q3c.groupby('运输架次编号')}
    tasks = []
    for _, r in q1.iterrows():
        sid = r['架次编号']
        needs_relay = ((q3c['运输架次编号'] == sid) & (q3c['保障方式'] == '中继')).any()
        tasks.append(dict(sid=sid, site=int(r['服务区编号'][1:]), type=r['机型编号'],
                          T=float(r['往返时间（s）']), E=float(r['架次能耗（kWh）']),
                          t0=float(start[sid]), t1=float(start[sid]) + float(r['往返时间（s）']),
                          n_box=len(str(r['货箱编号列表']).split(';')),
                          relay=bool(needs_relay)))
    return tasks, q3r, q3c, nodes, types


def group_of(site, part):
    for gi, g in enumerate(part):
        if site in g:
            return gi
    return -1


# ---------------------------------------------------------------------------
def schedule_group(tasks, types, bat_full, n_relay_lanes=1, lead=650.0):
    """组内**独立执行**排程：所有架次从 t=0 重新开始，不受全局交错顺序影响。

    规则（T-4.6/T-4.7）：
      - 需中继的架次占用一条"中继通道"；通道内串行（含中继部署 lead 与 300 s 周转）
      - 不需中继的架次可自由并行，只需一架运输机 + 一组电池
      - 运输机/电池需求 = 时间轴并发峰值
    返回该组达到的最短完成时刻与所需资源。
    """
    relay_tasks = sorted([t for t in tasks if t['relay']], key=lambda x: x['T'])
    free_tasks = [t for t in tasks if not t['relay']]
    tyR = C.load_relay_types()[0]
    turn = tyR['t_turn']
    # 中继通道排程（LPT 装箱：长架次先放，最小化通道跨度）
    lane = [0.0] * n_relay_lanes
    relay_spans = [[] for _ in range(n_relay_lanes)]
    for t in relay_tasks:
        li = int(np.argmin(lane))
        st = lane[li] + lead          # 该通道最早可开始（含中继部署在场）
        en = st + t['T']
        relay_spans[li].append((st, en))
        lane[li] = en + turn          # 中继返航 + 周转后才能服务下一架次
    groups_makespan = max(lane) if lane else 0.0
    relay_busy = max(lane) if lane else 0.0

    # 平行资源槽排程非中继架次：事件驱动，槽数不限（需求 = 峰值）
    events = []
    for sp in relay_spans:
        for (st, en) in sp:
            events.append((st, +1)); events.append((en, -1))
    # 非中继架次紧随其后，用"最早可开始 + 最少机器数"排成流水
    # 采用 first-fit：维护各槽的释放时刻
    slots = []
    free_tasks.sort(key=lambda x: -x['T'])
    for t in free_tasks:
        placed = False
        for k in range(len(slots)):
            if slots[k] <= relay_busy + 1e-9:       # 可在中继占用期之外并行
                st = slots[k]; en = st + t['T']
                slots[k] = en
                events.append((st, +1)); events.append((en, -1))
                placed = True
                break
        if not placed:
            st = relay_busy
            slots.append(st + t['T'])
            events.append((st, +1)); events.append((relay_busy + t['T'], -1))
    # 并发峰值
    cur = 0; peak = 0
    for tm, d in sorted(events):
        cur += d; peak = max(peak, cur)
    finish = max([e[0] for e in events], default=0.0)
    # 同机型分别统计并发峰值
    per_type = {}
    return dict(n=len(tasks), finish=finish, uav=peak,
                energy=sum(t['E'] for t in tasks),
                n_relay=len(relay_tasks), relay_busy=relay_busy,
                events=events)


def schedule_group_per_type(tasks, types, lead=650.0):
    """按机型分别求组内该机型的运输机并发峰值（组内资源核算口径）。"""
    res = {}
    for ty_ in sorted({t['type'] for t in tasks}):
        sub = [t for t in tasks if t['type'] == ty_]
        relay_sub = [t for t in sub if t['relay']]
        free_sub = [t for t in sub if not t['relay']]
        tyR = C.load_relay_types()[0]
        lane_end = 0.0
        spans = []
        for t in sorted(relay_sub, key=lambda x: -x['T']):
            st = lane_end + lead; en = st + t['T']
            spans.append((st, en)); lane_end = en + tyR['t_turn']
        busy = lane_end
        slots = []
        ev = []
        for (st, en) in spans:
            ev.append((st, +1)); ev.append((en, -1))
        for t in sorted(free_sub, key=lambda x: -x['T']):
            if slots:
                k = int(np.argmin(slots))
                if slots[k] <= busy + 1e-9:
                    st = slots[k]; en = st + t['T']; slots[k] = en
                    ev.append((st, +1)); ev.append((en, -1)); continue
            st = busy; slots.append(st + t['T'])
            ev.append((st, +1)); ev.append((busy + t['T'], -1))
        cur = 0; peak = 0
        for tm, d in sorted(ev):
            cur += d; peak = max(peak, cur)
        res[ty_] = peak
    return res


# ---------------------------------------------------------------------------
def evaluate(part, tasks, types, nodes, q3r, bat_full):
    """给定分区，核算各组资源与全局指标。"""
    K = len(part)
    gres = []
    for gi, g in enumerate(part):
        gt = [t for t in tasks if t['site'] in g]
        if not gt:
            gres.append(None); continue
        # 每组独立执行：中继无人机通道数 = 1（保守：组内串行中继）
        r = schedule_group(gt, types, bat_full, n_relay_lanes=1)
        r['types_used'] = sorted({t['type'] for t in gt})
        r['sites'] = sorted(g)
        r['per_type_uav'] = schedule_group_per_type(gt, types)
        r['relay_uav'] = 1 if r['n_relay'] > 0 else 0
        r['relay_bat'] = r['relay_uav']       # 能源组件数与中继机同比（每组独立）
        gres.append(r)
    # 全局资源需求 = 各组之和（资源不得跨组调配）
    need_uav = {k: 0 for k in STOCK}
    need_bat = {k: 0 for k in BAT_STOCK}
    for r in gres:
        if r is None:
            continue
        for k in STOCK:
            need_uav[k] += r['per_type_uav'].get(k, 0)
        for k in BAT_STOCK:
            need_bat[k] += r['per_type_uav'].get(k, 0)   # 每机一组电池（组内）
    need_relay = sum(r['relay_uav'] for r in gres if r)
    need_relay_bat = sum(r['relay_bat'] for r in gres if r)
    gap = {k: max(0, need_uav[k] - STOCK[k]) for k in STOCK}
    gap_bat = {k: max(0, need_bat[k] - BAT_STOCK[k]) for k in BAT_STOCK}
    gap_relay = max(0, need_relay - RELAY_STOCK)
    gap_relay_bat = max(0, need_relay_bat - RELAY_BAT_STOCK)
    makespan = max((r['finish'] for r in gres if r), default=0.0)
    # 组间工作量均衡（架次数标准差）
    counts = [r['n'] for r in gres if r]
    bal = float(np.std(counts)) if counts else 0.0
    # 目标：makespan 主导，其次均衡，其次资源规模
    score = makespan + 600.0 * bal + 300.0 * sum(gap.values()) + 300.0 * gap_relay
    return dict(gres=gres, need_uav=need_uav, need_bat=need_bat,
                need_relay=need_relay, need_relay_bat=need_relay_bat,
                gap=gap, gap_bat=gap_bat, gap_relay=gap_relay,
                gap_relay_bat=gap_relay_bat, makespan=makespan, balance=bal,
                score=score, counts=counts)


# ---------------------------------------------------------------------------
def kmeans_partition(sites, nodes, weights, K, iters=200, seed=0):
    rng = np.random.default_rng(seed)
    pts = np.array([[nodes['S%03d' % s]['lon'], nodes['S%03d' % s]['lat']] for s in sites])
    w = np.array([weights.get(s, 1.0) for s in sites], dtype=float)
    best = None
    for _ in range(12):
        cen = pts[rng.choice(len(sites), K, replace=False)]
        for _ in range(iters):
            d = ((pts[:, None, :] - cen[None, :, :]) ** 2).sum(-1)
            lab = d.argmin(1)
            if len(set(lab)) < K:
                break
            new = np.array([pts[lab == k].mean(0) if (lab == k).any() else cen[k]
                            for k in range(K)])
            if np.allclose(new, cen):
                break
            cen = new
        part = [sorted(int(sites[i]) for i in range(len(sites)) if lab[i] == k)
                for k in range(K)]
        part = [g for g in part if g]
        if len(part) != K:
            continue
        if best is None or part < best:
            best = part
    return best


def local_search(part, tasks, types, nodes, q3r, bat_full):
    cur = evaluate(part, tasks, types, nodes, q3r, bat_full)
    improved = True
    while improved:
        improved = False
        K = len(part)
        for a in range(K):
            for b in range(K):
                if a == b:
                    continue
                for s in list(part[a]):
                    if len(part[a]) <= 1:
                        continue
                    new = [list(g) for g in part]
                    new[a].remove(s); new[b].append(s)
                    new = [sorted(g) for g in new if g]
                    if len(new) != K:
                        continue
                    r = evaluate(new, tasks, types, nodes, q3r, bat_full)
                    if r['score'] < cur['score'] - 1e-9:
                        part = new; cur = r; improved = True
                        break
                if improved:
                    break
            if improved:
                break
    return part, cur


def main():
    tasks, q3r, q3c, nodes, types = load_frozen_task()
    _, bat_info = C.load_transport_fleet()
    bat_full = {k: v['T_full'] for k, v in bat_info.items()}
    sites = list(range(1, 16))
    weight = defaultdict(float)
    for t in tasks:
        weight[t['site']] += t['T']

    rows = []
    details = {}
    for K in (2, 3):
        best = None
        for seed in range(8):
            p0 = kmeans_partition(sites, nodes, weight, K, seed=seed)
            if p0 is None:
                continue
            p, r = local_search(p0, tasks, types, nodes, q3r, bat_full)
            if best is None or r['score'] < best[1]['score']:
                best = (p, r)
        p, r = best
        details[K] = (p, r)
        rows.append(dict(
            K=K,
            分区=' | '.join(','.join('S%03d' % s for s in g) for g in p),
            组架次数='/'.join(str(c) for c in r['counts']),
            工作量均衡标准差=round(r['balance'], 3),
            组完成时刻max_s=round(r['makespan'], 1),
            A型运输机=r['need_uav']['A'], B型运输机=r['need_uav']['B'], C型运输机=r['need_uav']['C'],
            A型电池=r['need_bat']['A'], B型电池=r['need_bat']['B'], C型电池=r['need_bat']['C'],
            中继无人机=r['need_relay'], 中继能源组件=r['need_relay_bat'],
            运输机缺口=sum(r['gap'].values()), 电池缺口=sum(r['gap_bat'].values()),
            中继缺口=r['gap_relay'], 中继能源缺口=r['gap_relay_bat'],
        ))
    tab = pd.DataFrame(rows)

    # ---- 输出 Q4_分区配置.csv（按模板列）----
    out_rows = []
    for K in (2, 3):
        p, r = details[K]
        for gi, g in enumerate(p):
            gr = r['gres'][gi]
            out_rows.append({
                'K（2或3）': K,
                '任务组编号': 'G%d-%d' % (K, gi + 1),
                '服务区列表': ';'.join('S%03d' % s for s in sorted(g)),
                'A型运输无人机数': gr['per_type_uav'].get('A', 0),
                'B型运输无人机数': gr['per_type_uav'].get('B', 0),
                'C型运输无人机数': gr['per_type_uav'].get('C', 0),
                'A型电池组数': gr['per_type_uav'].get('A', 0),
                'B型电池组数': gr['per_type_uav'].get('B', 0),
                'C型电池组数': gr['per_type_uav'].get('C', 0),
                '中继无人机数': gr['relay_uav'],
                '中继能源组件数': gr['relay_bat'],
            })
    pd.DataFrame(out_rows).to_csv(os.path.join(OUT, 'Q4_分区配置.csv'),
                                  index=False, encoding='utf-8-sig')
    tab.to_csv(os.path.join(OUT, 'Q4_方案比较.csv'), index=False, encoding='utf-8-sig')

    # ---- 报告 ----
    L = ["# B6 问题四求解报告\n", "## 0. 冻结的任务结构（继承 Q3）\n"]
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 运输架次 | %d |" % len(tasks))
    L.append("| 需中继的架次 | %d |" % sum(1 for t in tasks if t['relay']))
    L.append("| 服务区 | 15 |")
    L.append("| 同一架次涉及多服务区的情况 | 0（Q1/Q3 均为单点往返）→ **无等价类约束** |")
    L += ["\n## 1. 两种分区方案比较\n", "| 指标 | K=2 | K=3 |", "|---|---|---|"]
    r2, r3 = details[2][1], details[3][1]
    cmp_rows = [
        ("任务组划分", ' | '.join(','.join('S%03d' % s for s in g) for g in details[2][0]),
         ' | '.join(','.join('S%03d' % s for s in g) for g in details[3][0])),
        ("各组架次数", '/'.join(map(str, r2['counts'])), '/'.join(map(str, r3['counts']))),
        ("工作量均衡（标准差）", "%.3f" % r2['balance'], "%.3f" % r3['balance']),
        ("组完成时刻 max (s)", "%.1f" % r2['makespan'], "%.1f" % r3['makespan']),
        ("A型运输机", r2['need_uav']['A'], r3['need_uav']['A']),
        ("B型运输机", r2['need_uav']['B'], r3['need_uav']['B']),
        ("C型运输机", r2['need_uav']['C'], r3['need_uav']['C']),
        ("运输机合计", sum(r2['need_uav'].values()), sum(r3['need_uav'].values())),
        ("A/B/C 电池", "%d/%d/%d" % (r2['need_bat']['A'], r2['need_bat']['B'], r2['need_bat']['C']),
         "%d/%d/%d" % (r3['need_bat']['A'], r3['need_bat']['B'], r3['need_bat']['C'])),
        ("中继无人机", r2['need_relay'], r3['need_relay']),
        ("中继能源组件", r2['need_relay_bat'], r3['need_relay_bat']),
        ("**运输机缺口**", sum(r2['gap'].values()), sum(r3['gap'].values())),
        ("**电池缺口**", sum(r2['gap_bat'].values()), sum(r3['gap_bat'].values())),
        ("**中继无人机缺口**", r2['gap_relay'], r3['gap_relay']),
        ("**中继能源组件缺口**", r2['gap_relay_bat'], r3['gap_relay_bat']),
    ]
    for a, b, c in cmp_rows:
        L.append("| %s | %s | %s |" % (a, b, c))
    L += ["\n## 2. 各组资源明细\n"]
    for K in (2, 3):
        p, r = details[K]
        L.append("### K=%d\n" % K)
        L.append("| 组 | 服务区 | 架次 | 需中继 | A/B/C 运输机 | A/B/C 电池 | 中继机 | 中继组件 | 完成时刻s |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for gi, g in enumerate(p):
            gr = r['gres'][gi]
            L.append("| G%d-%d | %s | %d | %d | %d/%d/%d | %d/%d/%d | %d | %d | %.1f |" % (
                K, gi + 1, ';'.join('S%03d' % s for s in g), gr['n'], gr['n_relay'],
                gr['per_type_uav'].get('A', 0), gr['per_type_uav'].get('B', 0),
                gr['per_type_uav'].get('C', 0),
                gr['per_type_uav'].get('A', 0), gr['per_type_uav'].get('B', 0),
                gr['per_type_uav'].get('C', 0),
                gr['relay_uav'], gr['relay_bat'], gr['finish']))
        L.append("")
    L += ["## 3. 库存对账\n", "| 资源 | 库存 | K=2 需求 | K=2 缺口 | K=3 需求 | K=3 缺口 |",
          "|---|---|---|---|---|---|"]
    L.append("| A型运输机 | %d | %d | %d | %d | %d |" % (STOCK['A'], r2['need_uav']['A'], r2['gap']['A'], r3['need_uav']['A'], r3['gap']['A']))
    L.append("| B型运输机 | %d | %d | %d | %d | %d |" % (STOCK['B'], r2['need_uav']['B'], r2['gap']['B'], r3['need_uav']['B'], r3['gap']['B']))
    L.append("| C型运输机 | %d | %d | %d | %d | %d |" % (STOCK['C'], r2['need_uav']['C'], r2['gap']['C'], r3['need_uav']['C'], r3['gap']['C']))
    for k in 'ABC':
        L.append("| %s型电池 | %d | %d | %d | %d | %d |" % (k, BAT_STOCK[k], r2['need_bat'][k], r2['gap_bat'][k], r3['need_bat'][k], r3['gap_bat'][k]))
    L.append("| 中继无人机 | %d | %d | %d | %d | %d |" % (RELAY_STOCK, r2['need_relay'], r2['gap_relay'], r3['need_relay'], r3['gap_relay']))
    L.append("| 中继能源组件 | %d | %d | %d | %d | %d |" % (RELAY_BAT_STOCK, r2['need_relay_bat'], r2['gap_relay_bat'], r3['need_relay_bat'], r3['gap_relay_bat']))
    with open(os.path.join(REP, 'B6_Q4.md'), 'w', encoding='utf-8') as f:
        f.write("\n".join(L))
    print("\n".join(L))
    json.dump({str(K): dict(part=details[K][0],
                            makespan=details[K][1]['makespan'],
                            balance=details[K][1]['balance'],
                            need_uav=details[K][1]['need_uav'],
                            need_bat=details[K][1]['need_bat'],
                            need_relay=details[K][1]['need_relay'],
                            need_relay_bat=details[K][1]['need_relay_bat'],
                            gap=details[K][1]['gap'],
                            gap_bat=details[K][1]['gap_bat'],
                            gap_relay=details[K][1]['gap_relay'])
               for K in (2, 3)},
              open(os.path.join(OUT, 'Q4_summary.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
