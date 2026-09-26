# -*- coding: utf-8 -*-
"""问题四：救援任务分区与资源配置优化（T-4.1..T-4.10，D12）

口径（全文唯一，K=2 与 K=3 逐字同式）
====================================
T-4.4 冻结 Q3 的**任务安排**：每架次的机型、访问服务区、箱集合、执行时刻，以及
「运输架次 ↔ 中继架次」的保障关系，全部沿用 Q3 的联合调度结果，本问**不重排时间轴**。
在该冻结时间轴上，每组所需资源 = 相应占用区间的**并发峰值**：

| 资源 | 占用区间 | 依据 |
|---|---|---|
| k 型运输无人机 | ``[联合开始, 联合返回]`` | 一运输架次占用一架 |
| k 型运输电池 | ``[联合开始, 联合返回 + τ_chg(SOC_end)]`` | 与 ``q2_schedule.schedule`` 的 ``bat_free[b] = en + t_charge(soc, T_full)`` 同式 |
| 中继无人机 | ``[中继开始, 返回O01]`` | 一中继架次占用一架 |
| 中继能源组件 | ``[中继开始, 返回O01 + τ_chg(SOC_end)]`` | 与 ``q3_joint.py`` 的组件释放同式 |

**峰值是确界，不是估计**（``min_units`` 同时给出下界与构造性上界）：

* 下界：某时刻有 c 个区间重叠 ⇒ 至少要 c 份互不相同的资源；
* 上界：区间图按起点排序、同刻先释放后占用地贪心指派，用色数 = 峰值
  （区间图贪心着色即最优），脚本对返回的指派显式校验「同编号区间两两不重叠」。

据此「需求 = 峰值」是**最小需求**。T-4.4 只冻结任务安排，**不冻结实体编号**；T-4.6
只禁止**跨组**共享，组内实体可重新指派，故按峰值（而非冻结方案里出现过的实体机个数）
计需求。后者作为**参考列**一并给出，恒有 ``参考实体数 ≥ 峰值``。

由此得到的两个与旧版不同的结论（旧版见 ``spec/iteration_ledger.md`` IT-22）：

1. **电池需求 ≥ 运输机需求**（占用区间是运输机区间的超集），而非旧版的「电池 = 运输机」
   —— 旧写法只在「落地立刻换电」时才成立，会把充电周转造成的额外电池需求漏掉（M7）。
2. **K 不改变完成时间**：各组仍按冻结时刻执行，故「联合完成时刻」对 K=2/K=3 恒为 Q3 值；
   K 改变的是**资源峰值**（组内独占 ⇒ 需求随 K 单调不减）与**组间均衡**。

中继需求按「本组运输架次实际引用的中继架次」核算：若同一中继架次同时服务两个组的
架次，则按 T-4.6 必须在两组各复制一份，脚本统计 ``跨组中继架次`` 并将其计入两组。

目标与寻优方式（**不是启发式**）
================================
目标（字典序）：**缺口率 → 缺口数 → 资源需求规模 → 组工时极差 → 组完成时刻极差**
（``key_object``）。其中缺口率 = $\sum_r \text{gap}_r/\text{stock}_r$，量纲无关，
避免「A 型库存 4」与「中继库存 2」被同权相减。

约束：**组间架次数均衡**——各组架次数与均值之差 ≤ 1（K=2 → 19/18；K=3 → 13/12/12）。
本约束不是"美化结果"，而是目标本身的必要条件：若不作此约束，字典序目标的最优解会退化为
「两组各 1 站、其余 13 站并作一组」这类极端位形（``concentrated_witness`` 给出反例：
K=2 [35,2] 缺口率 0.4167 < 交付解 2.4167），而它已完全丧失"任务组分担救援"的意义。
反例与交付解并列在 ``out/Q4_方案比较.csv`` 与报告 §7，代价与收益一并披露。

寻优：15 个等价类（T-4.5 无合并）在均衡配额下的划分**全枚举**（无标号划分限制增长串去重），
每个划分都完整评估目标（K=2 需 2602 个、K=3 需 57330 个，秒级），故交付方案是
**该约束下的精确最优**，不是局部搜索解——旧版用 k-means + 单点搬迁局部搜索，
会在同一目标下漏掉更优解（IT-22 记录该缺陷）。

输出：``out/Q4_分区配置.csv``（模板列，逐字符对齐）、``out/Q4_方案比较.csv``、
      ``out/Q4_summary.json``、``reports/B6_Q4.md``。
"""
from __future__ import annotations
import os, sys, math, json, heapq
from collections import defaultdict
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                          # noqa: E402

OUT = os.path.join(ROOT, 'out'); REP = os.path.join(ROOT, 'reports')
STOCK = dict(A=4, B=2, C=2)                       # 运输无人机库存（T-4.8）
BAT_STOCK = dict(A=6, B=4, C=4)                   # 运输电池库存
RELAY_STOCK = 2                                    # 中继无人机库存
RELAY_BAT_STOCK = 6                                # 中继能源组件库存
EPS = 1e-9


# ---------------------------------------------------------------------------
# 区间资源核算：峰值 = 最小资源数（下界 + 构造性上界）
# ---------------------------------------------------------------------------
def min_units(intervals):
    """区间图最小资源数。返回 ``(peak, assign)``；``assign[i]`` = 第 i 个区间占用的资源号。

    下界：某时刻 c 个区间重叠 ⇒ 至少 c 份独立资源。
    上界：按起点升序、同一时刻**先释放后占用**（半开区间 ``[a, b)``）贪心指派，
          区间图的贪心着色用色数 = 峰值，故 ``peak`` 可达。
    """
    iv = [(float(a), float(b), i) for i, (a, b) in enumerate(intervals)
          if float(b) > float(a) + EPS]
    if not iv:
        return 0, {}
    # 峰值：同刻**先释放后占用**（半开区间 [a, b)，只在该刻之前占用）
    ev = []
    for a, b, _ in iv:
        ev.append((a, 1)); ev.append((b, 0))     # flag 0 = 释放, 1 = 占用
    ev.sort(key=lambda e: (e[0], e[1]))
    cur = peak = 0
    for _, flag in ev:
        cur += 1 if flag else -1
        peak = max(peak, cur)
    # 构造性指派
    free = list(range(peak)); heapq.heapify(free)
    busy = []                                    # (释放时刻, 资源号)
    assign = {}
    for a, b, i in sorted(iv):
        while busy and busy[0][0] <= a + EPS:
            heapq.heappush(free, heapq.heappop(busy)[1])
        assert free, '区间图贪心指派未能在 peak=%d 份资源内完成' % peak
        u = heapq.heappop(free)
        assign[i] = u
        heapq.heappush(busy, (b, u))
    _check_assign(iv, assign)
    return peak, assign


def _check_assign(iv, assign):
    """校验指派合法：同一资源号上的区间两两不重叠（半开）。"""
    by = defaultdict(list)
    for a, b, i in iv:
        by[assign[i]].append((a, b))
    for u, lst in by.items():
        lst.sort()
        for (a1, b1), (a2, _) in zip(lst, lst[1:]):
            assert b1 <= a2 + EPS, '资源 %s 上的区间重叠：[%.3f, %.3f] vs 起 %.3f' % (u, a1, b1, a2)


def peak(intervals):
    return min_units(intervals)[0]


# ---------------------------------------------------------------------------
# 冻结方案读入（Q3 结果，T-4.4）
# ---------------------------------------------------------------------------
def load_frozen_plan():
    """读入 Q3 冻结方案：运输架次（含联合执行时刻）、中继架次、以及两者保障关系。

    数据源（缺一即报错，不做静默回退）：
      ``out/Q2_运输架次.csv``   机型 / 访问服务区 / 架次能耗（Q2 授予，Q3 不变）
      ``out/Q3_架次延迟.csv``   联合开始与联合返回时刻（**绝对时刻**，Q3 联合调度结果）
      ``out/Q3_通信保障.csv``   运输架次 ↔ 中继架次（唯一权威来源）
      ``out/Q3_中继架次.csv``   中继架次时间轴与能耗
    """
    want = ['Q2_运输架次.csv', 'Q3_架次延迟.csv', 'Q3_通信保障.csv', 'Q3_中继架次.csv']
    miss = [w for w in want if not os.path.exists(os.path.join(OUT, w))]
    if miss:
        raise SystemExit('缺少 Q3 冻结方案文件 %s；请先运行 code/q3_joint.py' % miss)

    types = C.load_transport_types()
    rt, _, rbat = C.load_relay_types()
    bat_full = {k: v['T_full'] for k, v in C.load_transport_fleet()[1].items()}
    q2 = pd.read_csv(os.path.join(OUT, 'Q2_运输架次.csv'))
    q3d = pd.read_csv(os.path.join(OUT, 'Q3_架次延迟.csv'))
    q3c = pd.read_csv(os.path.join(OUT, 'Q3_通信保障.csv'))
    q3r = pd.read_csv(os.path.join(OUT, 'Q3_中继架次.csv'))

    dmap = {r['架次编号']: r for r in q3d.to_dict('records')}
    rel_of = defaultdict(list)
    for r in q3c.to_dict('records'):
        rid = r['中继架次编号']
        if r['保障方式'] == '中继' and isinstance(rid, str) and rid.strip():
            if rid not in rel_of[r['运输架次编号']]:
                rel_of[r['运输架次编号']].append(rid)

    tasks = []
    for r in q2.to_dict('records'):
        jid, ty = r['架次编号'], r['机型编号']
        assert jid in dmap, 'Q2 架次 %s 在 Q3_架次延迟.csv 中缺失' % jid
        d = dmap[jid]
        t0, t1 = float(d['联合开始时刻（s）']), float(d['联合返回时刻（s）'])
        dur = t1 - t0
        dur_q2 = float(r['返回O01时刻（s）']) - float(r['开始时刻（s）'])
        assert abs(dur - dur_q2) < 1e-6, \
            '架次 %s 的时长在 Q3 被改变（%.6f vs Q2 %.6f）：T-4.4 只允许平移' % (jid, dur, dur_q2)
        E = float(r['架次能耗（kWh）'])
        sites = [x for x in str(r['访问服务区顺序']).split(';') if x]
        assert sites, '架次 %s 无访问服务区' % jid
        soc_end = max(0.0, 1.0 - E / types[ty]['E_use'])
        tasks.append(dict(
            jid=jid, type=ty, sites=sites, site=int(sites[0][1:]),
            E=E, t0=t0, t1=t1, dur=dur,
            t_chg=float(C.t_charge(soc_end, bat_full[ty])),
            relay=sorted(rel_of.get(jid, [])),
            n_box=int(d.get('n_box', 0)) or 0,
            uav=str(d['运输机编号']), bat=str(d['电池编号'])))

    rtasks = {}
    for r in q3r.to_dict('records'):
        rid = r['中继架次编号']
        E = float(r['架次能耗（kWh）'])
        soc_end = max(0.0, 1.0 - E / rt['E_use'])
        rtasks[rid] = dict(rid=rid, t0=float(r['开始时刻（s）']), t1=float(r['返回O01时刻（s）']),
                           E=E, t_chg=float(C.t_charge(soc_end, rbat['R']['T_full'])),
                           uav=str(r['中继无人机编号']), mod=str(r['能源组件编号']))
    for t in tasks:
        for rid in t['relay']:
            assert rid in rtasks, '架次 %s 引用了不存在的中继架次 %s' % (t['jid'], rid)
    return tasks, rtasks, types, rt


def site_classes(tasks, sites):
    """T-4.5 等价类：同一架次涉及的服务区必须同组 ⇒ 并查集合并。"""
    par = {s: s for s in sites}

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x

    for t in tasks:
        ss = [int(x[1:]) for x in t['sites']]
        for s in ss[1:]:
            a, b = find(ss[0]), find(s)
            if a != b:
                par[a] = b
    cl = defaultdict(list)
    for s in sites:
        cl[find(s)].append(s)
    return [sorted(v) for v in cl.values()]


# ---------------------------------------------------------------------------
# 分区评价（冻结时间轴上的资源峰值）
# ---------------------------------------------------------------------------
def group_of(part, site):
    for gi, g in enumerate(part):
        if site in g:
            return gi
    return -1


def evaluate(part, tasks, rtasks, types, rt):
    """给定分区，按冻结时间轴核算各组资源需求（口径见模块 docstring）。"""
    K = len(part)
    owner = {}
    for gi, g in enumerate(part):
        for s in g:
            owner[s] = gi
    gres = []
    for gi, g in enumerate(part):
        gt = [t for t in tasks if owner[t['site']] == gi]
        need_uav, need_bat = {}, {}
        for k in STOCK:
            sub = [t for t in gt if t['type'] == k]
            need_uav[k] = peak([(t['t0'], t['t1']) for t in sub])
            need_bat[k] = peak([(t['t0'], t['t1'] + t['t_chg']) for t in sub])
            if need_uav[k] or need_bat[k]:
                assert need_bat[k] >= need_uav[k], \
                    '电池占用区间 ⊇ 运输机占用区间，峰值不可能更小'
        rids = sorted({r for t in gt for r in t['relay']})
        need_relay = peak([(rtasks[r]['t0'], rtasks[r]['t1']) for r in rids])
        need_relay_bat = peak([(rtasks[r]['t0'], rtasks[r]['t1'] + rtasks[r]['t_chg'])
                               for r in rids])
        assert need_relay_bat >= need_relay
        # 冻结方案里实际出现过的实体编号（参考列，非需求；恒 ≥ 峰值）
        ref = dict(uav=len({t['uav'] for t in gt}), bat=len({t['bat'] for t in gt}),
                   relay=len({rtasks[r]['uav'] for r in rids}),
                   mod=len({rtasks[r]['mod'] for r in rids}))
        finish = max([t['t1'] for t in gt] + [rtasks[r]['t1'] for r in rids], default=0.0)
        start = min([t['t0'] for t in gt], default=0.0)
        gres.append(dict(
            sites=sorted(g), n=len(gt), n_relay=len(rids), rids=rids,
            need_uav=need_uav, need_bat=need_bat,
            need_relay=need_relay, need_relay_bat=need_relay_bat,
            ref=ref, n_box=sum(t['n_box'] for t in gt),
            work=sum(t['dur'] for t in gt),
            finish=finish, start=start, span=finish - start,
            types_used=sorted({t['type'] for t in gt})))
    return _summarize(part, gres, tasks, rtasks)


def _summarize(part, gres, tasks, rtasks):
    K = len(part)
    need_uav = {k: sum(g['need_uav'][k] for g in gres) for k in STOCK}
    need_bat = {k: sum(g['need_bat'][k] for g in gres) for k in BAT_STOCK}
    need_relay = sum(g['need_relay'] for g in gres)
    need_relay_bat = sum(g['need_relay_bat'] for g in gres)
    ref = {k: sum(g['ref'][k] for g in gres) for k in ('uav', 'bat', 'relay', 'mod')}
    gap = {k: max(0, need_uav[k] - STOCK[k]) for k in STOCK}
    gap_bat = {k: max(0, need_bat[k] - BAT_STOCK[k]) for k in BAT_STOCK}
    gap_relay = max(0, need_relay - RELAY_STOCK)
    gap_relay_bat = max(0, need_relay_bat - RELAY_BAT_STOCK)
    # 同一中继架次被两个组引用 ⇒ T-4.6 下须各复制一份（跨组中继架次）
    seen, cross = defaultdict(set), 0
    for gi, g in enumerate(gres):
        for r in g['rids']:
            seen[r].add(gi)
    cross = sum(1 for r, gs in seen.items() if len(gs) > 1)
    works = [g['work'] for g in gres]
    counts = [g['n'] for g in gres]
    work_spread = max(works) - min(works)
    finish_spread = max(g['finish'] for g in gres) - min(g['finish'] for g in gres)
    makespan = max(g['finish'] for g in gres)
    return dict(
        K=K, part=part, gres=gres,
        need_uav=need_uav, need_bat=need_bat,
        need_relay=need_relay, need_relay_bat=need_relay_bat, ref=ref,
        gap=gap, gap_bat=gap_bat, gap_relay=gap_relay, gap_relay_bat=gap_relay_bat,
        gap_total=sum(gap.values()) + sum(gap_bat.values()) + gap_relay + gap_relay_bat,
        # 量纲无关的相对缺口率（各资源缺口 / 各自库存），作为目标第一关键字
        gap_rate=(sum(gap[k] / STOCK[k] for k in STOCK)
                  + sum(gap_bat[k] / BAT_STOCK[k] for k in BAT_STOCK)
                  + gap_relay / RELAY_STOCK + gap_relay_bat / RELAY_BAT_STOCK),
        demand_total=sum(need_uav.values()) + sum(need_bat.values()) + need_relay + need_relay_bat,
        makespan=makespan, balance=float(np.std(counts)) if counts else 0.0,
        counts=counts, works=works, work_spread=work_spread, finish_spread=finish_spread,
        cross_relay=cross)


def construct_relay_witness(K, tasks, rtasks, types, rt, sites):
    """构造「中继需求 ≤ 库存」的分区（对旧版「K≥3 必然超出中继库存」的构造性反驳）。

    把**全部含中继需求的服务区**并入同一组，其余（无中继需求的）服务区分到 K-1 组。
    该构造下所有中继架次都落在第一组，其并发峰值 = 冻结方案的全局峰值，
    其余各组中继需求为 0，故合计 = 全局峰值，与 K 无关。
    """
    rset = sorted({t['site'] for t in tasks if t['relay']})
    fset = [s for s in sites if s not in rset]
    assert rset, '冻结方案无中继需求，无需构造反例'
    assert len(fset) >= K - 1,         '无中继站仅 %d 个，不足以在 K=%d 下单独成组' % (len(fset), K)
    part = [rset]
    n, g = len(fset), K - 1
    base, rem = n // g, n % g
    i = 0
    for gi in range(g):
        take = base + (1 if gi < rem else 0)
        if take:
            part.append(sorted(fset[i:i + take])); i += take
    return [p for p in part if p]


def key_object(r):
    """目标（字典序）：缺口率 → 缺口数 → 资源规模 → 工时均衡 → 收尾均衡。"""
    return (round(r['gap_rate'], 9), r['gap_total'], r['demand_total'],
            round(r['work_spread'], 6), round(r['finish_spread'], 6))


def _surplus(r):
    """资源冗余（T-4.9 维度②）：$\sum \max(0, 库存 - 需求)$，逐类库存/需求量纲一致地相加。"""
    s = sum(max(0, STOCK[k] - r['need_uav'][k]) for k in STOCK)
    s += sum(max(0, BAT_STOCK[k] - r['need_bat'][k]) for k in BAT_STOCK)
    s += max(0, RELAY_STOCK - r['need_relay'])
    s += max(0, RELAY_BAT_STOCK - r['need_relay_bat'])
    return s


# ---------------------------------------------------------------------------
# 分区寻优（等价类不可拆；在「架次数均衡」约束下**全枚举**）
# ---------------------------------------------------------------------------
def balanced_caps(K, total):
    """均衡约束允许的各组架次数：把 ``total`` 架次尽可能均分（组间极差 ≤ 1）。

    本问 total=37 ⇒ K=2 → 19/18；K=3 → 13/12/12。约束的**必要性**见
    ``concentrated_witness``：不设均衡约束时目标最优解退化为「两组各 1 站、
    其余 13 站并作一组」的极端位形，与题面「任务组」分担救援任务的本意相悖。
    """
    q, rmd = divmod(total, K)
    return [q + 1] * rmd + [q] * (K - rmd)


def enumerate_balanced(K, tasks, rtasks, types, rt, sites, keyfun=key_object):
    """在均衡约束下**枚举全部** K-划分，返回最优 ``(part, r)`` 与枚举统计。

    枚举方式：按站点顺序深度优先，逐个把站点放进"已开设的组"或"新开一组"；
    新组只作为最后一个新下标出现（限制增长串），故每个**无标号**划分恰好被访问一次。
    剪枝：① 组的架次数不得超过均衡上限；② 剩余站点数不足以让各组非空则回溯。
    K=2 需枚举 2^15 量级、K=3 需 3^15 量级，均在秒级完成，故**不是启发式**。
    """
    cnt = {s: 0 for s in sites}
    for t in tasks:
        cnt[t['site']] += 1
    caps = balanced_caps(K, sum(cnt.values()))
    hi, target = max(caps), sorted(caps)
    N = len(sites)
    cs = [cnt[s] for s in sites]
    gc = [0] * K; groups = [[] for _ in range(K)]
    best = [None]; stats = dict(评估数=0, 剪枝后叶子=0, 均衡划分=0)

    def rec(i, ng):
        if i == N:
            if ng != K:
                return
            stats['剪枝后叶子'] += 1
            if sorted(gc) != target:
                return
            stats['均衡划分'] += 1
            p = [sorted(g) for g in groups]
            r = evaluate(p, tasks, rtasks, types, rt)
            stats['评估数'] += 1
            if best[0] is None or keyfun(r) < keyfun(best[0][1]):
                best[0] = (p, r)
            return
        if N - i < K - ng:                          # 剩余站点不足以填满剩余组
            return
        for g in range(min(ng + 1, K)):
            fresh = (g == ng)
            if gc[g] + cs[i] > hi:
                continue
            gc[g] += cs[i]; groups[g].append(sites[i])
            rec(i + 1, ng + (1 if fresh else 0))
            groups[g].pop(); gc[g] -= cs[i]
            if fresh:
                break                               # 无标号：新组只试一次
    rec(0, 0)
    assert best[0] is not None, 'K=%d 均衡约束下未找到可行分区' % K
    assert stats['均衡划分'] > 0
    return best[0][0], best[0][1], stats


def concentrated_witness(K, tasks, rtasks, types, rt, sites):
    """反例（说明均衡约束为何必要）：把 K-1 个站点各自成组、其余站点并作一组。

    在所有「K-1 个组各 1 站」的位形里取目标最优者（K=3 时 105 个候选，可全枚举）。
    这类位形常给出更小的缺口率（分区越集中、跨组复制的中继架次越少），
    但**架次极差 30+** —— 分区已失去分担救援的意义，故被均衡约束排除。
    """
    from itertools import combinations
    best = None
    for sing in combinations(sites, K - 1):
        rest = [s for s in sites if s not in sing]
        part = [sorted(rest)] + [[s] for s in sing]
        r = evaluate(part, tasks, rtasks, types, rt)
        if best is None or key_object(r) < key_object(best[1]):
            best = (part, r)
    return best


# ---------------------------------------------------------------------------
def _part_str(site_part):
    return ' | '.join(','.join('S%03d' % s for s in g) for g in site_part)


def main():
    tasks, rtasks, types, rt = load_frozen_plan()
    sites = list(range(1, 16))
    assert sorted({t['site'] for t in tasks}) == sites, '运输架次服务区未覆盖全部 15 个服务区'
    cls = site_classes(tasks, sites)
    assert sum(len(c) for c in cls) == 15

    print('=' * 96)
    print('问题四：分区与资源配置（口径 = T-4.4 冻结时间轴上的资源并发峰值）')
    print('=' * 96)
    print('冻结方案：运输架次 %d；中继架次 %d；需中继的运输架次 %d；等价类 %d 个%s'
          % (len(tasks), len(rtasks), sum(1 for t in tasks if t['relay']), len(cls),
             '（T-4.5 无合并：%s）' % (cls,) if max(len(c) for c in cls) == 1
             else '（T-4.5 合并：%s）' % (cls,)))

    # ---- K=1 基准：冻结方案整体（不分区），用于量化「分区代价」----
    base = evaluate([sites], tasks, rtasks, types, rt)
    print('\n[基准 K=1] 冻结方案不分区：运输机 %d/%d/%d，电池 %d/%d/%d，中继 %d，组件 %d'
          % (base['need_uav']['A'], base['need_uav']['B'], base['need_uav']['C'],
             base['need_bat']['A'], base['need_bat']['B'], base['need_bat']['C'],
             base['need_relay'], base['need_relay_bat']))
    print('         （冻结实体编号参考：运输机 %d，电池 %d，中继机 %d，组件 %d）'
          % (base['ref']['uav'], base['ref']['bat'], base['ref']['relay'], base['ref']['mod']))

    details = {}
    for K in (2, 3):
        p, r, st = enumerate_balanced(K, tasks, rtasks, types, rt, sites, key_object)
        details[K] = (p, r)
        details['枚举%d' % K] = st
        caps = balanced_caps(K, sum(g['n'] for g in r['gres']))
        print('\n[K=%d] 均衡约束（各组架次数 %s，极差 ≤1）：%s' % (K, '/'.join(map(str, caps)), _part_str(p)))
        print('      枚举：剪枝后叶子划分 %d 个（各组架次数 ≤ 上限），其中满足均衡配额 %d 个'
              '→ 全部完整评估，非启发式'
              % (st['剪枝后叶子'], st['均衡划分']))
        print('      组内需求：运输机 %d/%d/%d，电池 %d/%d/%d，中继 %d，组件 %d'
              % (r['need_uav']['A'], r['need_uav']['B'], r['need_uav']['C'],
                 r['need_bat']['A'], r['need_bat']['B'], r['need_bat']['C'],
                 r['need_relay'], r['need_relay_bat']))
        print('      库存缺口：运输机 %d，电池 %d，中继 %d，组件 %d；跨组中继架次 %d'
              % (sum(r['gap'].values()), sum(r['gap_bat'].values()),
                 r['gap_relay'], r['gap_relay_bat'], r['cross_relay']))

    # ---- 均衡约束的必要性：极端位形反例（不设约束时目标会选中的解）----
    conc = {}
    for K in (2, 3):
        p, r = concentrated_witness(K, tasks, rtasks, types, rt, sites)
        conc[K] = (p, r)
        print('\n[K=%d 集中式位形（反例：说明均衡约束的必要性）] %s' % (K, _part_str(p)))
        print('      架次数 %s（极差 %d）；缺口率 %.4f、缺口 %d、需求 %d'
              % (r['counts'], max(r['counts']) - min(r['counts']), r['gap_rate'],
                 r['gap_total'], r['demand_total']))
    details['conc'] = conc

    # ---- 中继反例：中继需求 ≤ 库存（旧版「K≥3 必然超出中继库存」的直接反驳）----
    # 构造式：全部含中继需求的站并入一组，其余站分到 K-1 组 ⇒ 中继峰值 = 全局峰值。
    wits = {}
    for Kw in (2, 3):
        pc = construct_relay_witness(Kw, tasks, rtasks, types, rt, sites)
        rw = evaluate(pc, tasks, rtasks, types, rt)
        assert rw['need_relay'] <= RELAY_STOCK, \
            '构造式中继反例在 K=%d 下中继需求 %d > 库存 %d' % (Kw, rw['need_relay'], RELAY_STOCK)
        assert rw['gap_relay'] == 0, '构造应使中继缺口为 0'
        wits[Kw] = (pc, rw)
        print('\n[K=%d 中继反例（构造式：中继集中）] %s' % (Kw, _part_str(rw['part'])))
        print('      中继需求 %d = 库存 %d（缺口 0）；中继组件 %d；运输机缺口 %d，电池缺口 %d'
              % (rw['need_relay'], RELAY_STOCK, rw['need_relay_bat'],
                 sum(rw['gap'].values()), sum(rw['gap_bat'].values())))
    details['wit'] = wits

    # ---- 全局自检 ----
    _selfcheck(base, details, tasks, rtasks)
    _write(base, details, tasks, rtasks)
    return 0


def _selfcheck(base, details, tasks, rtasks):
    """Q4 内部一致性自检（失败即退出非 0）。"""
    print('\n' + '-' * 96)
    print('自检')
    print('-' * 96)
    ok = []

    def ck(name, cond, detail=''):
        ok.append(bool(cond))
        print('[%s] %-44s %s' % (' OK ' if cond else 'FAIL', name, detail))

    for K in (2, 3):
        r = details[K][1]
        part = r['part']
        flat = [s for g in part for s in g]
        ck('K=%d 分区覆盖 15 站且不重叠' % K,
           sorted(flat) == list(range(1, 16)), '|g|=%s' % [len(g) for g in part])
        ck('K=%d 各组非空' % K, all(len(g) > 0 for g in part))
        ck('K=%d 全部运输架次被计入' % K, sum(g['n'] for g in r['gres']) == len(tasks),
           '%d/%d' % (sum(g['n'] for g in r['gres']), len(tasks)))
        ck('K=%d 电池需求 ≥ 运输机需求（M7）' % K,
           all(r['need_bat'][k] >= r['need_uav'][k] for k in STOCK),
           '/'.join('%d≥%d' % (r['need_bat'][k], r['need_uav'][k]) for k in STOCK))
        ck('K=%d 需求 ≤ 冻结实体数（参考上界）' % K,
           all(r['need_uav'][k] <= r['ref']['uav'] for k in STOCK)
           and all(r['need_bat'][k] <= r['ref']['bat'] for k in STOCK),
           '运输机 %d≤%d，电池 %d≤%d' % (sum(r['need_uav'].values()), r['ref']['uav'],
                                        sum(r['need_bat'].values()), r['ref']['bat']))
        ck('K=%d 各组资源需求 = 峰值可达（指派已校验）' % K, True, 'min_units 内部断言')
        ck('K=%d 交付方案满足均衡约束（架次数极差 ≤1）' % K,
           max(r['counts']) - min(r['counts']) <= 1,
           '架次数 %s（%s）' % (r['counts'], '/'.join(map(str, balanced_caps(K, sum(r['counts']))))))
        ck('K=%d 交付方案为均衡集合上的全枚举最优' % K,
           details['枚举%d' % K]['评估数'] == details['枚举%d' % K]['均衡划分']
           and details['枚举%d' % K]['均衡划分'] > 0,
           '均衡划分 %d 个全部评估' % details['枚举%d' % K]['均衡划分'])
    ck('K=1 基准中继峰值 = Q3 中继库存上限 2', base['need_relay'] == RELAY_STOCK,
       '%d' % base['need_relay'])
    ck('K=1 基准运输机峰值 ≤ 库存', all(base['need_uav'][k] <= STOCK[k] for k in STOCK),
       '/'.join('%d≤%d' % (base['need_uav'][k], STOCK[k]) for k in STOCK))
    ck('K=3 构造式反例：中继需求 = 库存（反驳「K≥3 必然超库存」）',
       all(details['wit'][K][1]['need_relay'] <= RELAY_STOCK for K in (2, 3)),
       'K=2 %d、K=3 %d（库存 %d）' % (details['wit'][2][1]['need_relay'],
                                     details['wit'][3][1]['need_relay'], RELAY_STOCK))
    ck('K=1→K=3 运输机需求单调不减',
       all(details[2][1]['need_uav'][k] <= details[3][1]['need_uav'][k] for k in STOCK)
       and all(base['need_uav'][k] <= details[2][1]['need_uav'][k] for k in STOCK),
       'K=1 %d → K=2 %d → K=3 %d'
       % (sum(base['need_uav'].values()), sum(details[2][1]['need_uav'].values()),
          sum(details[3][1]['need_uav'].values())))
    ck('目标分区确有跨组中继复制（缺口中继项的成因）',
       details[2][1]['cross_relay'] > 0 and details[3][1]['cross_relay'] > 0,
       'K=2 %d / K=3 %d' % (details[2][1]['cross_relay'], details[3][1]['cross_relay']))
    ck('构造式反例无跨组中继（故中继缺口 0）',
       all(details['wit'][K][1]['cross_relay'] == 0 for K in (2, 3)), '')
    ck('集中式反例的架次数远不均衡（故须设均衡约束）',
       all(max(details['conc'][K][1]['counts']) - min(details['conc'][K][1]['counts']) >= 10
           for K in (2, 3)),
       'K=2 %s / K=3 %s' % (details['conc'][2][1]['counts'], details['conc'][3][1]['counts']))
    ck('集中式反例证明「缺口可被更小」（约束是有代价的诚实披露）',
       all(key_object(details['conc'][K][1])[:3] < key_object(details[K][1])[:3]
           for K in (2, 3)),
       'K=2 缺口率 %.4f < %.4f；K=3 %.4f < %.4f' % (
           details['conc'][2][1]['gap_rate'], details[2][1]['gap_rate'],
           details['conc'][3][1]['gap_rate'], details[3][1]['gap_rate']))
    ck('K=2 在配置规模/缺口维度上不劣于 K=3（收尾均衡 K=3 更好，如实并列）',
       all(details[2][1][key] <= details[3][1][key]
           for key in ('gap_rate', 'gap_total', 'demand_total'))
       and sum(details[2][1]['need_uav'].values()) <= sum(details[3][1]['need_uav'].values())
       and details[2][1]['need_relay'] <= details[3][1]['need_relay'],
       '需求 %d≤%d，缺口 %d≤%d，中继 %d≤%d' % (
           details[2][1]['demand_total'], details[3][1]['demand_total'],
           details[2][1]['gap_total'], details[3][1]['gap_total'],
           details[2][1]['need_relay'], details[3][1]['need_relay']))
    assert all(ok), 'Q4 自检未全部通过'


def _write(base, details, tasks, rtasks):
    r2, r3 = details[2][1], details[3][1]

    # ---- Q4_分区配置.csv（模板列，逐字符对齐 tests/check_template.py）----
    rows = []
    for K in (2, 3):
        r = details[K][1]
        for gi, g in enumerate(r['part']):
            gr = r['gres'][gi]
            rows.append({
                'K（2或3）': K,
                '任务组编号': 'G%d-%d' % (K, gi + 1),
                '服务区列表': ';'.join('S%03d' % s for s in g),
                'A型运输无人机数': gr['need_uav']['A'],
                'B型运输无人机数': gr['need_uav']['B'],
                'C型运输无人机数': gr['need_uav']['C'],
                'A型电池组数': gr['need_bat']['A'],
                'B型电池组数': gr['need_bat']['B'],
                'C型电池组数': gr['need_bat']['C'],
                '中继无人机数': gr['need_relay'],
                '中继能源组件数': gr['need_relay_bat'],
            })
    pd.DataFrame(rows).to_csv(os.path.join(OUT, 'Q4_分区配置.csv'),
                              index=False, encoding='utf-8-sig')

    # ---- Q4_方案比较.csv ----
    def _row(tag, K, r):
        return dict(
            K=K, 方案=tag, 分区=_part_str(r['part']),
            组架次数='/'.join(map(str, r['counts'])),
            组工时s='/'.join('%.0f' % w for w in r['works']),
            工时极差s=round(r['work_spread'], 1),
            架次数标准差=round(r['balance'], 3),
            组完成时刻极差s=round(r['finish_spread'], 1),
            联合完成时刻s=round(r['makespan'], 1),
            A型运输机=r['need_uav']['A'], B型运输机=r['need_uav']['B'],
            C型运输机=r['need_uav']['C'],
            A型电池=r['need_bat']['A'], B型电池=r['need_bat']['B'], C型电池=r['need_bat']['C'],
            中继无人机=r['need_relay'], 中继能源组件=r['need_relay_bat'],
            运输机缺口=sum(r['gap'].values()), 电池缺口=sum(r['gap_bat'].values()),
            中继缺口=r['gap_relay'], 中继能源缺口=r['gap_relay_bat'],
            缺口总数=r['gap_total'], 缺口率=round(r['gap_rate'], 6),
            跨组中继架次=r['cross_relay'], 资源需求合计=r['demand_total'],
            冻结实体运输机=r['ref']['uav'], 冻结实体电池=r['ref']['bat'],
            冻结实体中继机=r['ref']['relay'], 冻结实体组件=r['ref']['mod'])
    # 每 K 的首行 = 交付方案（code/render.py 的 fig3 按 K 取首行），反例行一律排在其后
    cmp_rows = [_row('K=1 基准（冻结方案不分区）', 1, base),
                _row('K=2 交付方案（均衡约束下全枚举最优）', 2, r2),
                _row('K=3 交付方案（均衡约束下全枚举最优）', 3, r3)]
    for Kw in (2, 3):
        cmp_rows.append(_row('K=%d 中继构造式反例（中继集中）' % Kw, Kw, details['wit'][Kw][1]))
    for Kw in (2, 3):
        cmp_rows.append(_row('K=%d 集中式位形（反例：均衡约束的必要性）' % Kw, Kw, details['conc'][Kw][1]))
    tab = pd.DataFrame(cmp_rows)
    tab.to_csv(os.path.join(OUT, 'Q4_方案比较.csv'), index=False, encoding='utf-8-sig')

    # ---- summary ----
    def pack(K, r):
        return dict(K=K, part=r['part'],
                    need_uav=r['need_uav'], need_bat=r['need_bat'],
                    need_relay=r['need_relay'], need_relay_bat=r['need_relay_bat'],
                    gap=r['gap'], gap_bat=r['gap_bat'],
                    gap_relay=r['gap_relay'], gap_relay_bat=r['gap_relay_bat'],
                    makespan=r['makespan'], balance=r['balance'],
                    counts=r['counts'], works=r['works'],
                    work_spread=r['work_spread'], finish_spread=r['finish_spread'],
                    ref=r['ref'], cross_relay=r['cross_relay'],
                    groups=[dict(sites=['S%03d' % s for s in g['sites']], n=g['n'],
                                 n_relay=g['n_relay'], need_uav=g['need_uav'],
                                 need_bat=g['need_bat'], need_relay=g['need_relay'],
                                 need_relay_bat=g['need_relay_bat'],
                                 finish=round(g['finish'], 1), work=round(g['work'], 1))
                            for g in r['gres']])
    summ = dict(口径='T-4.4 冻结 Q3 任务安排；资源需求 = 冻结时间轴上的占用并发峰值（下界与可达上界重合）',
                冻结方案=dict(运输架次数=len(tasks), 中继架次数=len(rtasks),
                            需中继运输架次数=sum(1 for t in tasks if t['relay']),
                            箱数=sum(t['n_box'] for t in tasks)),
                库存=dict(运输机=STOCK, 电池=BAT_STOCK, 中继机=RELAY_STOCK,
                         中继组件=RELAY_BAT_STOCK),
                目标='字典序：缺口率 → 缺口数 → 资源需求规模 → 组工时极差 → 组完成时刻极差',
                约束='组间架次数均衡（各组与均值之差 ≤ 1）：K=2 19/18，K=3 13/12/12',
                寻优方式='均衡配额下全枚举（无标号划分限制增长串去重），每个划分完整评估目标',
                枚举规模={str(K): dict(剪枝后叶子=details['枚举%d' % K]['剪枝后叶子'],
                                    均衡划分=details['枚举%d' % K]['均衡划分'],
                                    评估数=details['枚举%d' % K]['评估数']) for K in (2, 3)},
                K1基准=pack(1, base), K2=pack(2, r2), K3=pack(3, r3),
                中继构造式反例={str(K): pack(K, details['wit'][K][1]) for K in (2, 3)},
                集中式反例_均衡约束必要性={str(K): pack(K, details['conc'][K][1]) for K in (2, 3)})
    with open(os.path.join(OUT, 'Q4_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summ, f, ensure_ascii=False, indent=2)

    # ---- 报告 ----
    L = ["# B6 问题四求解报告\n",
         "## 0. 口径（全文唯一）\n",
         "T-4.4 冻结 Q3 的任务安排（机型、访问服务区、箱集合、执行时刻与通信保障关系），",
         "本问不重排时间轴。各组资源需求 = 冻结时间轴上相应占用区间的**并发峰值**：",
         "运输机 `[联合开始, 联合返回]`；电池 `[联合开始, 联合返回 + τ_chg]`；",
         "中继机 `[中继开始, 返回O01]`；中继组件 `[中继开始, 返回O01 + τ_chg]`。",
         "",
         "峰值既是下界（某时刻 c 个区间重叠 ⇒ 至少 c 份独立资源），也是可达上界",
         "（区间图按起点贪心指派即最优着色），脚本对指派显式校验「同编号区间两两不重叠」，",
         "故「需求 = 峰值」是**最小需求**。T-4.4 不冻结实体编号、T-4.6 只禁止跨组共享，",
         "故组内可重新指派实体；冻结方案中出现过的实体编号数列作参考（恒 ≥ 峰值）。\n",
         "**目标与约束**：目标为字典序 **缺口率 → 缺口数 → 资源需求规模 → 组工时极差 →",
         "组完成时刻极差**；缺口率 $=\\sum_r \\text{gap}_r/\\text{stock}_r$（量纲无关，",
         "避免「A 型库存 4」与「中继库存 2」同权相减）。约束为**组间架次数均衡**",
         "（各组架次数与均值之差 ≤ 1）：K=2 → %s；K=3 → %s。"
         % ('/'.join(map(str, balanced_caps(2, sum(r2['counts'])))),
            '/'.join(map(str, balanced_caps(3, sum(r3['counts'])))),
         ),
         "该约束的必要性见 §7：不设约束时目标最优解退化为「两组各 1 站」的极端位形。\n",
         "**寻优**：15 个等价类在均衡配额下的划分**全枚举**（K=2 %d 个、K=3 %d 个均衡划分，"
         % (details['枚举2']['均衡划分'], details['枚举3']['均衡划分']),
         "每个都完整评估目标），故交付方案是**该约束下的精确最优**，不是局部搜索解。\n",
         "## 1. 冻结方案与基准\n",
         "| 项 | 值 |", "|---|---|",
         "| 运输架次 | %d |" % len(tasks),
         "| 中继架次 | %d |" % len(rtasks),
         "| 需中继的运输架次 | %d |" % sum(1 for t in tasks if t['relay']),
         "| 服务区 | 15 |",
         "| T-4.5 等价类 | %d 个（每个等价类内 %s） |"
         % (len(cls),
            '无可合并：全部架次均为单点往返' if all(len(t['sites']) == 1 for t in tasks)
            else '存在多服务区架次，已按并查集合并'),
         "",
         "**K=1 基准（冻结方案整体，不分区）**：运输机 %d/%d/%d，电池 %d/%d/%d，中继 %d，组件 %d。"
         % (base['need_uav']['A'], base['need_uav']['B'], base['need_uav']['C'],
            base['need_bat']['A'], base['need_bat']['B'], base['need_bat']['C'],
            base['need_relay'], base['need_relay_bat']),
         "分区把独占资源切成 K 份，故 K 越大需求越大（组内峰值之和 ≥ 整体峰值）。\n",
         "## 2. K=2 与 K=3 方案比较\n",
         "| 指标 | K=2 | K=3 |", "|---|---|---|"]
    cmp_pairs = [
        ('任务组划分', _part_str(r2['part']), _part_str(r3['part'])),
        ('各组架次数', '/'.join(map(str, r2['counts'])), '/'.join(map(str, r3['counts']))),
        ('各组工时 (s)', '/'.join('%.0f' % w for w in r2['works']),
         '/'.join('%.0f' % w for w in r3['works'])),
        ('工时极差 (s)', '%.1f' % r2['work_spread'], '%.1f' % r3['work_spread']),
        ('架次数标准差', '%.3f' % r2['balance'], '%.3f' % r3['balance']),
        ('组完成时刻极差 (s)', '%.1f' % r2['finish_spread'], '%.1f' % r3['finish_spread']),
        ('联合完成时刻 (s)', '%.1f' % r2['makespan'], '%.1f' % r3['makespan']),
        ('A/B/C 运输机', '%d/%d/%d' % (r2['need_uav']['A'], r2['need_uav']['B'], r2['need_uav']['C']),
         '%d/%d/%d' % (r3['need_uav']['A'], r3['need_uav']['B'], r3['need_uav']['C'])),
        ('运输机合计', sum(r2['need_uav'].values()), sum(r3['need_uav'].values())),
        ('A/B/C 电池', '%d/%d/%d' % (r2['need_bat']['A'], r2['need_bat']['B'], r2['need_bat']['C']),
         '%d/%d/%d' % (r3['need_bat']['A'], r3['need_bat']['B'], r3['need_bat']['C'])),
        ('中继无人机', r2['need_relay'], r3['need_relay']),
        ('中继能源组件', r2['need_relay_bat'], r3['need_relay_bat']),
        ('**运输机缺口**', sum(r2['gap'].values()), sum(r3['gap'].values())),
        ('**电池缺口**', sum(r2['gap_bat'].values()), sum(r3['gap_bat'].values())),
        ('**中继无人机缺口**', r2['gap_relay'], r3['gap_relay']),
        ('**中继能源组件缺口**', r2['gap_relay_bat'], r3['gap_relay_bat']),
        ('**缺口总数**', r2['gap_total'], r3['gap_total']),
        ('**缺口率**（Σ缺口/库存）', '%.4f' % r2['gap_rate'], '%.4f' % r3['gap_rate']),
        ('资源需求合计（四类之和）', r2['demand_total'], r3['demand_total']),
        ('与库存总体缺口', r2['gap_total'], r3['gap_total']),
    ]
    for a, b, c in cmp_pairs:
        L.append("| %s | %s | %s |" % (a, b, c))

    L += ["\n### 2.1 与冻结实体编号数的对照（口径敏感性，非需求）\n",
          "| 参考量 | K=2 | K=3 |", "|---|---|---|",
          "| 运输机实体编号数（冻结方案中出现的） | %d | %d |" % (r2['ref']['uav'], r3['ref']['uav']),
          "| 电池实体编号数 | %d | %d |" % (r2['ref']['bat'], r3['ref']['bat']),
          "| 中继机实体编号数 | %d | %d |" % (r2['ref']['relay'], r3['ref']['relay']),
          "| 中继组件实体编号数 | %d | %d |" % (r2['ref']['mod'], r3['ref']['mod']),
          "| 跨组中继架次（T-4.6 下须复制） | %d | %d |" % (r2['cross_relay'], r3['cross_relay']),
          "",
          "峰值 ≤ 实体编号数：T-4.4 冻结的是任务安排而非实体编号，组内可重新指派实体。",
          ""]

    L += ["## 3. 各组资源明细\n"]
    for K in (2, 3):
        r = details[K][1]
        L.append("### K=%d\n" % K)
        L.append("| 组 | 服务区 | 架次 | 需中继 | A/B/C 运输机 | A/B/C 电池 | 中继机 | 中继组件 | 工时s | 完成时刻s |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for gi, g in enumerate(r['part']):
            gr = r['gres'][gi]
            L.append("| G%d-%d | %s | %d | %d | %d/%d/%d | %d/%d/%d | %d | %d | %.0f | %.1f |" % (
                K, gi + 1, ';'.join('S%03d' % s for s in g), gr['n'], gr['n_relay'],
                gr['need_uav']['A'], gr['need_uav']['B'], gr['need_uav']['C'],
                gr['need_bat']['A'], gr['need_bat']['B'], gr['need_bat']['C'],
                gr['need_relay'], gr['need_relay_bat'], gr['work'], gr['finish']))
        L.append("")

    surv2, surv3 = _surplus(r2), _surplus(r3)
    L += ["## 4. 四个比较维度（T-4.9）\n",
          "| 维度 | K=2 | K=3 | 结论 |", "|---|---|---|---|",
          "| ① 资源配置规模（四类需求合计） | %d 件 | %d 件 | **K=2 更省**（+%d 件） |"
          % (r2['demand_total'], r3['demand_total'], r3['demand_total'] - r2['demand_total']),
          "| ② 资源冗余 $\\sum\\max(0,\\text{库存}-\\text{需求})$ | %d | %d | %s |"
          % (surv2, surv3,
             '两者相同（仅 C 型电池与中继组件尚有冗余）' if surv2 == surv3
             else ('K=2 冗余更多' if surv2 > surv3 else 'K=3 冗余更多')),
          "| ③ 组间工作量均衡（工时极差 s ／ 架次数极差） | %.0f ／ %d | %.0f ／ %d | %s |"
          % (r2['work_spread'], max(r2['counts']) - min(r2['counts']),
             r3['work_spread'], max(r3['counts']) - min(r3['counts']),
             '**K=2 更均衡**' if r2['work_spread'] <= r3['work_spread'] else '**K=3 更均衡**'),
          "| ④ 与库存的资源缺口（缺口总数 ／ 缺口率） | %d ／ %.4f | %d ／ %.4f | **K=2 缺口更小** |"
          % (r2['gap_total'], r2['gap_rate'], r3['gap_total'], r3['gap_rate']),
          "",
          "附加维度（题面未列，但影响救援节奏）：**组完成时刻极差** K=2 %.0f s ／ K=3 %.0f s"
          % (r2['finish_spread'], r3['finish_spread']),
          "→ **K=3 更同步**，这是 K=3 唯一的优势项。",
          "",
          "**结论：K=2 在 ①③④ 上占优、② 两者持平（仅 C 型电池/中继组件有冗余），"
          "K=3 仅在收尾同步性上占优，故推荐 K=2。**",
          "两方案的**联合完成时刻相同**（%.0f s）——K 不改变冻结时间轴，只改变资源峰值。"
          % r2['makespan'],
          "",
          "",
          "## 5. 库存对账（T-4.8/T-4.10）\n",
          "| 资源 | 库存 | K=1 基准 | K=2 需求 | K=2 缺口 | K=3 需求 | K=3 缺口 |",
          "|---|---|---|---|---|---|---|"]
    for k in 'ABC':
        L.append("| %s型运输机 | %d | %d | %d | %d | %d | %d |"
                 % (k, STOCK[k], base['need_uav'][k], r2['need_uav'][k], r2['gap'][k],
                    r3['need_uav'][k], r3['gap'][k]))
    for k in 'ABC':
        L.append("| %s型电池 | %d | %d | %d | %d | %d | %d |"
                 % (k, BAT_STOCK[k], base['need_bat'][k], r2['need_bat'][k], r2['gap_bat'][k],
                    r3['need_bat'][k], r3['gap_bat'][k]))
    L.append("| 中继无人机 | %d | %d | %d | %d | %d | %d |"
             % (RELAY_STOCK, base['need_relay'], r2['need_relay'], r2['gap_relay'],
                r3['need_relay'], r3['gap_relay']))
    L.append("| 中继能源组件 | %d | %d | %d | %d | %d | %d |"
             % (RELAY_BAT_STOCK, base['need_relay_bat'], r2['need_relay_bat'], r2['gap_relay_bat'],
                r3['need_relay_bat'], r3['gap_relay_bat']))

    L += ["\n### 5.1 缺口的成因\n",
          "1. **分区把共享池切成独占池**：基准（K=1）中 %d 架运输机的并发峰值被拆到 K 组后，",
          "   各组峰值之和 ≥ 整体峰值，故 K 增大必然抬高总需求（本表 K=1→K=2→K=3 单调不减）。",
          "2. **电池是充电周转而非机上耗材**：电池占用区间 = 飞行区间 ∪ 充电区间，恒为运输机区间",
          "   的超集，故电池需求 ≥ 运输机需求；能量紧的服务区（远距离 C 型）充电时间长，",
          "   把缺口从运输机转移到电池。",
          "3. **中继库存 2 架不构成 K=3 的硬障碍**：见 §6 的反例分区——把含中继需求的站并入",
          "   同一组可使 K=3 的中继需求仍为 %d（= 库存）。真正超库存的是运输机与电池。"
          % details['wit'][3][1]['need_relay'],
          "",
          "## 6. K=3 中继需求反例（对旧版结论的更正）\n",
          "旧版 `code/q4_partition.py` 把「每组中继需求」硬编码为 1（只要有中继架次就算 1 架），",
          "于是 K=3 必然得 3 > 库存 2，并据此声称「K≥3 必然超出中继库存」。该结论不成立：",
          "中继需求是**区间并发峰值**，只由该组实际引用的中继架次时间轴决定。显式反例：\n",
          "```", _part_str(details['wit'][3][1]['part']), "```",
          "",
          "该分区的 K=3 中继需求 = %d（= 库存 %d，**缺口 0**），但其运输机缺口 %d、电池缺口 %d。"
          % (details['wit'][3][1]['need_relay'], RELAY_STOCK,
             sum(details['wit'][3][1]['gap'].values()), sum(details['wit'][3][1]['gap_bat'].values())),
          "故正确表述是：**中继库存不构成 K=3 的可行性障碍，K=3 的障碍在运输机与电池**；",
          "旧版的「必然超出」是把峰值口径退化为「每组 1 架」所致的口径错误。\n",
          "## 7. 均衡约束的必要性（反例 + 代价披露）\n",
          "若**不设**组间架次数均衡约束，本目标（缺口优先）的最优解会退化为「少数站点各自成组、",
          "其余站点并作一组」的极端位形：分区越集中，跨组复制的中继架次越少、缺口越小，",
          "于是目标把资源「省下来」的代价推到「一组承担 33–35 架次」上。枚举 K-1 个单站组",
          "的全部组合给出：\n",
          "| K | 集中式反例分区 | 架次数 | 缺口率 | 缺口总数 | 需求合计 |",
          "|---|---|---|---|---|---|"]
    for K in (2, 3):
        rc = details['conc'][K][1]
        L.append("| %d | `%s` | %s（极差 %d） | %.4f | %d | %d |"
                 % (K, _part_str(rc['part']), '/'.join(map(str, rc['counts'])),
                    max(rc['counts']) - min(rc['counts']), rc['gap_rate'],
                    rc['gap_total'], rc['demand_total']))

    L += ["",
          "反例的缺口率确实**低于**交付方案（K=2 %.4f < %.4f；K=3 %.4f < %.4f），"
          % (details['conc'][2][1]['gap_rate'], r2['gap_rate'],
             details['conc'][3][1]['gap_rate'], r3['gap_rate']),
          "即均衡约束**是有代价的**，本报告不掩饰，把代价写明：",
          "K=2 以「缺口率 +%.4f、缺口 +%d 件」换取「架次数极差 %d → 1」；"
          % (r2['gap_rate'] - details['conc'][2][1]['gap_rate'],
             r2['gap_total'] - details['conc'][2][1]['gap_total'],
             max(details['conc'][2][1]['counts']) - min(details['conc'][2][1]['counts'])),
          "K=3 以「缺口率 +%.4f、缺口 +%d 件」换取「架次数极差 %d → 1」。"
          % (r3['gap_rate'] - details['conc'][3][1]['gap_rate'],
             r3['gap_total'] - details['conc'][3][1]['gap_total'],
             max(details['conc'][3][1]['counts']) - min(details['conc'][3][1]['counts'])),
          "取舍理由是题面语义：任务分区的目的是**分担**救援任务、支持就近独立配置，",
          "「35 架次 vs 2 架次」不构成分担。故把均衡写为**约束**（无需引入任何权重），",
          "再由 §2 的字典序目标在均衡集合内精确择优。\n"]

    with open(os.path.join(REP, 'B6_Q4.md'), 'w', encoding='utf-8') as f:
        f.write("\n".join(L))
    print('\n已写 out/Q4_分区配置.csv、out/Q4_方案比较.csv、out/Q4_summary.json、reports/B6_Q4.md')
    print(tab.to_string(index=False))


if __name__ == '__main__':
    sys.exit(main())
