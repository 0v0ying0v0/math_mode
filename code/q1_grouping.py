# -*- coding: utf-8 -*-
"""问题一：单点往返运输能力与货箱组批

依据 spec/task_spec.md §1（T-1.1..T-1.15）与 spec/decisions.md（D1–D6, D-ALT）。
物理规则全部来自 code/core.py，本文件不重写任何物理公式（治理检查 G-02）。
"""
from __future__ import annotations
import os, sys, json, itertools, math
from collections import defaultdict
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                     # noqa: E402

OUT = os.path.join(ROOT, 'out')
REP = os.path.join(ROOT, 'reports')
os.makedirs(OUT, exist_ok=True); os.makedirs(REP, exist_ok=True)


# ---------------------------------------------------------------------------
# 单架次评估（单点往返 O01 -> S_i -> O01）
# ---------------------------------------------------------------------------
def eval_sortie(ty, geom, boxes, elev_o, elev_s):
    """评估一个单点往返架次。boxes: [(箱号, 质量, 体积), ...]"""
    n = len(boxes)
    q = sum(b[1] for b in boxes)
    vol = sum(b[2] for b in boxes)
    if q > ty['q_max'] + 1e-9 or vol > ty['vol_max'] + 1e-9:
        return None
    out = C.leg_time_energy(ty, geom, q, elev_o, elev_s)
    back = C.leg_time_energy(ty, geom, 0.0, elev_s, elev_o)
    E = out['E'] + back['E']
    if not C.energy_ok(ty, E):
        return None
    t = (ty['t_prep'] + ty['t_box'] * n            # O01 准备 + 装载
         + out['t'] + back['t']                     # 飞行
         + ty['t_hand_base'] + ty['t_hand_box'] * n)  # 服务区交接
    return dict(n=n, q=q, vol=vol, E=E, T=t, E_out=out['E'], E_back=back['E'],
                t_out=out['t'], t_back=back['t'], soc=C.soc_end(ty, E))


# ---------------------------------------------------------------------------
# Q1.1 最大安全载荷（合成上限 + 能量上限）
# ---------------------------------------------------------------------------
def max_safe_payload(ty, geom, elev_o, elev_s, box_w, box_v):
    """T-1.1：返回瓶颈分解。

    上限1 质量: Q_g
    上限2 体积: vol_max / v_generic   （按最不利单箱体积折算，用于"合成上限"报告）
    上限3 能量: 二分求解
    同时给出"实际可装组合"的最大质量（由 Q1.2 的真装箱给出）
    """
    # 能量上限
    lo, hi = 0.0, ty['q_max']
    for _ in range(80):
        q = (lo + hi) / 2
        E = C.leg_time_energy(ty, geom, q, elev_o, elev_s)['E'] + \
            C.leg_time_energy(ty, geom, 0.0, elev_s, elev_o)['E']
        if C.energy_ok(ty, E):
            lo = q
        else:
            hi = q
    q_energy = lo
    # 合成体积上限（以该服务区最常见箱型密度折算）
    q_mass = ty['q_max']
    q_vol = ty['vol_max'] * box_w / box_v
    lim = min(q_mass, q_vol, q_energy)
    which = ('质量' if lim == q_mass else '') + ('体积' if abs(lim - q_vol) < 1e-9 else '') + \
            ('能量' if abs(lim - q_energy) < 1e-9 else '')
    return dict(q_mass=q_mass, q_vol=q_vol, q_energy=q_energy, q_safe=lim, bottleneck=which)


# ---------------------------------------------------------------------------
# 能量可行性：按 (q, vol) 精确判定
# ---------------------------------------------------------------------------
def max_q_for_volume(ty, geom, elev_o, elev_s, vol):
    """给定总体积 vol，求满足返航余量的最大总质量 q。

    注：往返能量只依赖"去程载荷总质量 q"与"回程空载"，
    与卸货顺序无关，故只需按 (q, vol) 判定，可在 DFS 中 O(1) 查表。
    """
    if vol > ty['vol_max'] + 1e-12:
        return -1.0
    lo, hi = 0.0, ty['q_max']
    if not C.energy_ok(ty, C.leg_time_energy(ty, geom, 0.0, elev_o, elev_s)['E'] +
                       C.leg_time_energy(ty, geom, 0.0, elev_s, elev_o)['E']):
        return -1.0                      # 连空载都飞不了
    if C.energy_ok(ty, C.leg_time_energy(ty, geom, hi, elev_o, elev_s)['E'] +
                   C.leg_time_energy(ty, geom, 0.0, elev_s, elev_o)['E']):
        return hi
    for _ in range(70):
        q = (lo + hi) / 2
        if C.energy_ok(ty, C.leg_time_energy(ty, geom, q, elev_o, elev_s)['E'] +
                       C.leg_time_energy(ty, geom, 0.0, elev_s, elev_o)['E']):
            lo = q
        else:
            hi = q
    return lo


# vol -> max q 的阶梯查表缓存
_VOLQ_CACHE = {}


def volq_table(ty, geom, elev_o, elev_s):
    # 缓存键不得用 id(ty)：灵敏度扫描每次 dict(types[k]) 都会新建对象，
    # 旧键会在 CPython 回收后复用 id 而命中错误缓存（给出别的 rho 的表）。
    key = (ty['name'], round(ty['rho'], 9), round(geom['d'], 6),
           round(geom['z_cruise'], 6), elev_o, elev_s)
    if key in _VOLQ_CACHE:
        return _VOLQ_CACHE[key]
    grid = np.arange(0.0, ty['vol_max'] + 1e-12, 0.002)
    tab = []
    for vol in grid:
        tab.append((vol, max_q_for_volume(ty, geom, elev_o, elev_s, float(vol))))
    # 保证单调不增
    for j in range(len(tab) - 2, -1, -1):
        if tab[j][1] > tab[j + 1][1]:
            tab[j] = (tab[j][0], tab[j + 1][1])
    _VOLQ_CACHE[key] = (grid, np.array([t[1] for t in tab]))
    return _VOLQ_CACHE[key]


def q_energy_limit(ty, geom, elev_o, elev_s, vol):
    grid, vals = volq_table(ty, geom, elev_o, elev_s)
    j = int(np.searchsorted(grid, vol, side='right') - 1)
    j = max(0, min(j, len(vals) - 1))
    return float(vals[j])


# ---------------------------------------------------------------------------
# Q1.2 组批：每服务区独立的最少架次数装箱（(k, E, T) 三级字典序精确最优）
# ---------------------------------------------------------------------------
def _bin_energy(ty, geom, q, elev_o, elev_s, cache):
    """单架次往返总能耗。只依赖总质量 q（去程载荷 q、回程空载）。"""
    key = round(q, 9)
    hit = cache.get(key)
    if hit is None:
        hit = (C.leg_time_energy(ty, geom, q, elev_o, elev_s)['E'] +
               C.leg_time_energy(ty, geom, 0.0, elev_s, elev_o)['E'])
        cache[key] = hit
    return hit


def _bin_time(ty, geom, n, elev_o, elev_s):
    """单架次往返作业时间。只依赖箱数 n：航段时间与载荷无关
    （见 core.leg_time_energy 的 t = h_up/v_up + d/v_cruise + h_dn/v_down），
    载荷只经 t_prep + t_box·n + t_hand_base + t_hand_box·n 进入。"""
    out = C.leg_time_energy(ty, geom, 0.0, elev_o, elev_s)
    back = C.leg_time_energy(ty, geom, 0.0, elev_s, elev_o)
    return (ty['t_prep'] + ty['t_box'] * n + out['t'] + back['t']
            + ty['t_hand_base'] + ty['t_hand_box'] * n)


def optimal_packing(ty, geom, boxes, elev_o, elev_s):
    """精确求解给定机型与箱集合下的 (架次数, 总能耗, 累计时间) 字典序最优装箱。

    取代早期"最少架次数 DFS 的首个可行解即返回"的做法——那种做法在最小 k
    内不做能耗寻优，会给出偏斜划分（如 S002/S003 的 67/14，被同 k 同时间的
    42/39 严格支配；见评审 C2）。

    可行性 = 质量 ∧ 体积 ∧ **能量**（附录 2 返航安全余量）。
    逐子集预判可行性后做子集 DP：
        D[S] = lexmin_{可行子集 T ∋ lowbit(S), T ⊆ S} ( D[S\\T] ⊕ T )
    其中 ⊕ 表示按 (架次数, 能耗, 时间) 三级字典序合并。该 DP 对三级字典序目标
    满足最优子结构，故所得解精确最优；子集枚举 O(3^n)，n ≤ 15（最大服务区箱数）
    在秒级内完成。

    返回 dict(k, E, T, assign)，assign 为每架次的箱下标列表。
    """
    n = len(boxes)
    if n == 0:
        return dict(k=0, E=0.0, T=0.0, assign=[])
    N = 1 << n
    m = np.array([b[1] for b in boxes], dtype=float)
    v = np.array([b[2] for b in boxes], dtype=float)
    cnt = np.zeros(N, dtype=np.int64)
    mass = np.zeros(N)
    vol = np.zeros(N)
    for j in range(n):
        h = 1 << j
        cnt[h:h * 2] = cnt[0:h] + 1
        mass[h:h * 2] = mass[0:h] + m[j]
        vol[h:h * 2] = vol[0:h] + v[j]

    # 可行性：质量 ∧ 体积 ∧ 能量（能量上限按子集体积查 volq 阶梯表）
    grid, vals = volq_table(ty, geom, elev_o, elev_s)
    qelim = vals[np.clip(np.searchsorted(grid, vol, side='right') - 1, 0, len(vals) - 1)]
    feas = ((mass <= ty['q_max'] + 1e-9) & (vol <= ty['vol_max'] + 1e-9)
            & (mass <= qelim + 1e-9))
    feas[0] = False
    fS = np.nonzero(feas)[0]
    if fS.size == 0:
        return dict(k=10 ** 9, E=float('inf'), T=float('inf'), assign=None)

    # 子集 → 单架次能耗/时间（按唯一质量、唯一箱数查缓存）
    ecache = {}
    Earr = np.full(N, np.inf)
    Tarr = np.full(N, np.inf)
    tcache = {}
    for S in fS:
        S = int(S)
        Earr[S] = _bin_energy(ty, geom, float(mass[S]), elev_o, elev_s, ecache)
        c = int(cnt[S])
        if c not in tcache:
            tcache[c] = _bin_time(ty, geom, c, elev_o, elev_s)
        Tarr[S] = tcache[c]

    # 按最低位分组可行子集，供 DP 枚举（T ∋ lowbit(S) 保证分块不重复计数）
    lbidx = np.zeros(N, dtype=np.int64)
    for S in range(1, N):
        lbidx[S] = (S & -S).bit_length() - 1
    by_lb = defaultdict(list)
    for S in fS:
        by_lb[int(lbidx[S])].append(int(S))
    by_lb = {b: np.array(lst, dtype=np.int64) for b, lst in by_lb.items()}

    FULL = N - 1
    BIG = np.int64(10 ** 9)
    Dk = np.full(N, BIG, dtype=np.int64)
    De = np.full(N, np.inf)
    Dt = np.full(N, np.inf)
    par = np.full(N, -1, dtype=np.int64)
    Dk[0] = 0
    De[0] = 0.0
    Dt[0] = 0.0
    for S in range(1, N):
        arr = by_lb.get(int(lbidx[S]))
        if arr is None:
            continue
        sel = (arr & np.int64(FULL ^ S)) == 0         # T ⊆ S（T 只在 n 位内有值）
        if not sel.any():
            continue
        T = arr[sel]
        rest = S ^ T
        kk = Dk[rest] + 1
        ok = kk < BIG
        if not ok.any():
            continue
        T, rest, kk = T[ok], rest[ok], kk[ok]
        ee = De[rest] + Earr[T]
        tt = Dt[rest] + Tarr[T]
        mk = int(kk.min())
        s2 = kk == mk
        ee2, tt2, T2 = ee[s2], tt[s2], T[s2]
        i = int(np.lexsort((tt2, ee2))[0])           # 先比能耗，再比时间
        Dk[S], De[S], Dt[S], par[S] = mk, ee2[i], tt2[i], T2[i]

    k, E, T = int(Dk[FULL]), float(De[FULL]), float(Dt[FULL])
    if k >= BIG:                       # 该 (站点, 机型) 在该 ρ 下无可行装箱
        return dict(k=BIG, E=float('inf'), T=float('inf'), assign=None)
    assign = []
    S = FULL
    while S:
        Tb = int(par[S])
        assert Tb > 0, ('回溯失败', S, Tb)
        assign.append([b for b in range(n) if (Tb >> b) & 1])
        S ^= Tb
    # 架次内按（质量降序, 箱数降序, 箱号升序）重排，使架次编号稳定可复现
    assign.sort(key=lambda grp: (-sum(boxes[b][1] for b in grp), -len(grp),
                                 min(boxes[b][0] for b in grp)))
    return dict(k=k, E=E, T=T, assign=assign)


def build_solution(ty, geom, boxes, assign, elev_o, elev_s):
    """把装箱结果转成架次明细，并做能量硬校验。"""
    srt = []
    for grp in assign:
        r = eval_sortie(ty, geom, [boxes[i] for i in grp], elev_o, elev_s)
        if r is None:
            return None
        srt.append(r)
    return srt


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    nodes = C.load_nodes(); types = C.load_transport_types()
    boxes_df = C.load_boxes()
    z, lon, lat = C.load_dem()
    o = nodes['O01']

    # ---- 预计算几何（往返同一条水平直线，D4） ----
    geom, dem_ok = {}, {}
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])
        geom[i] = g
        # 全航段 LOS 可行性预检（Q3 用）
        dem_ok[i] = not C.los_blocked(z, lon, lat,
                                      (s['lon'], s['lat'], g['z_cruise'], 'T'),
                                      C.gateway_endpoint(nodes), samples=400)

    # ---- 每服务区箱集合 ----
    site_boxes = {}
    for i in range(1, 16):
        code = 'S%03d' % i
        sub = boxes_df[boxes_df['服务区编号'] == code]
        site_boxes[i] = [(r['货箱编号'], float(r['单箱质量（kg）']), float(r['单箱体积（m³）']))
                         for _, r in sub.iterrows()]

    # ================= Q1.1 最大安全载荷 =================
    q11 = []
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        for k in ('A', 'B', 'C'):
            t = types[k]
            # 该服务区最"代表性"箱型（体积/质量最大者），用于合成体积上限
            w_max = max(b[1] for b in site_boxes[i])
            v_of = {b[1]: b[2] for b in site_boxes[i]}
            r = max_safe_payload(t, geom[i], o['elev'], s['elev'] + C.CABIN,
                                 w_max, v_of[w_max])
            q11.append(dict(Si='S%03d' % i, type=k, d_km=geom[i]['d'] / 1000,
                            **r))
    q11_df = pd.DataFrame(q11)
    q11_df.to_csv(os.path.join(OUT, 'Q1_1_最大安全载荷.csv'), index=False,
                  encoding='utf-8-sig')

    # ================= Q1.2/Q1.3 组批 =================
    plan = {}          # (i,k) -> 精确最优装箱结果
    detail = {}
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        bx = site_boxes[i]
        for k in ('A', 'B', 'C'):
            t = types[k]
            best = optimal_packing(t, geom[i], bx, o['elev'], s['elev'] + C.CABIN)
            plan[(i, k)] = best
            detail[(i, k)] = build_solution(t, geom[i], bx, best['assign'],
                                            o['elev'], s['elev'] + C.CABIN) \
                if best['assign'] else None

    # 每服务区选机型（按架次数少 → 能耗低 → 时间短 的字典序）
    site_choice = {}
    for i in range(1, 16):
        cands = []
        for k in ('A', 'B', 'C'):
            b = plan[(i, k)]
            if b['assign'] is None:
                continue
            d = detail[(i, k)]
            E = sum(x['E'] for x in d)
            T = sum(x['T'] for x in d)
            # 自检：DP 目标值必须与 core 物理函数逐位一致
            assert abs(E - b['E']) < 1e-9 and abs(T - b['T']) < 1e-9, (i, k, E, b['E'], T, b['T'])
            cands.append(dict(type=k, k=b['k'], E=E, T=T))
        cands.sort(key=lambda c: (c['k'], c['E'], c['T']))
        site_choice[i] = cands

    # ---- 方案 A：每服务区独立选最优机型（Q1.2 可行解 + Q1.3 基准） ----
    rows = []
    sid = 0
    for i in range(1, 16):
        c = site_choice[i][0]
        k = c['type']
        t = types[k]
        best = plan[(i, k)]
        d = detail[(i, k)]
        for grp, srt in zip(best['assign'], d):
            sid += 1
            rows.append(dict(
                架次编号='Q1-%03d' % sid,
                服务区编号='S%03d' % i,
                机型编号=k,
                货箱编号列表=';'.join(site_boxes[i][j][0] for j in grp),
                **{'总质量（kg）': round(srt['q'], 4),
                   '总体积（m³）': round(srt['vol'], 6),
                   '往返时间（s）': round(srt['T'], 3),
                   '架次能耗（kWh）': round(srt['E'], 6),
                   '返航SOC（%）': round(100 * srt['soc'], 2)},
            ))
    q1_df = pd.DataFrame(rows)
    q1_df.to_csv(os.path.join(OUT, 'Q1_单点组批.csv'), index=False, encoding='utf-8-sig')

    # ================= 汇总指标 =================
    summary = dict(
        sorties=len(q1_df),
        energy=round(float(q1_df['架次能耗（kWh）'].sum()), 6),
        makespan=round(float(q1_df['往返时间（s）'].sum()), 3),
        by_type={k: int((q1_df['机型编号'] == k).sum()) for k in ('A', 'B', 'C')},
        soc_min=float(q1_df['返航SOC（%）'].min()),
        mass_util=round(float(q1_df['总质量（kg）'].sum()), 2),
        vol_util=round(float(q1_df['总体积（m³）'].sum()), 4),
    )

    # ---- Pareto：每服务区机型 × 架次数的组合 ----
    pareto_rows = []
    for i in range(1, 16):
        for c in site_choice[i]:
            pareto_rows.append(dict(Si='S%03d' % i, type=c['type'], sorties=c['k'],
                                    E=round(c['E'], 6), T=round(c['T'], 3)))
    pd.DataFrame(pareto_rows).to_csv(os.path.join(OUT, 'Q1_服务区机型候选.csv'),
                                     index=False, encoding='utf-8-sig')

    # ---- 全局 Pareto（15 个服务区机型选择的组合）：用 DP 求最小(架次,能耗,时间)前沿 ----
    # 状态：(累计架次) -> dict(能耗, 时间) 取 Pareto
    INF = float('inf')
    states = {0: (0.0, 0.0)}   # sorties -> (E, T)
    frontier = {0: [(0.0, 0.0)]}
    for i in range(1, 16):
        newf = defaultdict(list)
        for s0, lst in frontier.items():
            for c in site_choice[i]:
                s1 = s0 + c['k']
                for (E0, T0) in lst:
                    newf[s1].append((E0 + c['E'], T0 + c['T']))
        # 每个架次数上取非支配集
        frontier = {}
        for s1, lst in newf.items():
            lst.sort()
            keep = []
            bestT = INF
            for E, T in lst:
                if T < bestT - 1e-12:
                    keep.append((E, T)); bestT = T
            frontier[s1] = keep
    g_rows = []
    for s1, lst in sorted(frontier.items()):
        for E, T in lst:
            g_rows.append(dict(架次数=s1, 总能耗kWh=round(E, 6), 累计作业时间s=round(T, 3)))
    gdf = pd.DataFrame(g_rows)
    # 全局非支配
    gdf = gdf.sort_values(['架次数', '总能耗kWh', '累计作业时间s']).reset_index(drop=True)
    nd = []
    for _, r in gdf.iterrows():
        dom = ((gdf['架次数'] <= r['架次数']) & (gdf['总能耗kWh'] <= r['总能耗kWh'] + 1e-9)
               & (gdf['累计作业时间s'] <= r['累计作业时间s'] + 1e-9)
               & ((gdf['架次数'] < r['架次数']) | (gdf['总能耗kWh'] < r['总能耗kWh'] - 1e-9)
                  | (gdf['累计作业时间s'] < r['累计作业时间s'] - 1e-9)))
        if not dom.any():
            nd.append(r)
    ndf = pd.DataFrame(nd)
    ndf.to_csv(os.path.join(OUT, 'Q1_Pareto前沿.csv'), index=False, encoding='utf-8-sig')

    # ================= Q1.4 rho 灵敏度 =================
    sens = []
    for rho in [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]:
        tot_s, tot_e, tot_t = 0, 0.0, 0.0
        per_type_bottleneck = {}
        for i in range(1, 16):
            s = nodes['S%03d' % i]
            bestc = None
            for k in ('A', 'B', 'C'):
                t = dict(types[k]); t['rho'] = rho
                bx = site_boxes[i]
                w_max = max(b[1] for b in bx); v_of = {b[1]: b[2] for b in bx}
                ms = max_safe_payload(t, geom[i], o['elev'], s['elev'] + C.CABIN, w_max, v_of[w_max])
                per_type_bottleneck[(i, k)] = ms
                b = optimal_packing(t, geom[i], bx, o['elev'], s['elev'] + C.CABIN)
                if b['assign'] is None:
                    continue
                cand = dict(type=k, k=b['k'], E=b['E'], T=b['T'])
                if bestc is None or (cand['k'], cand['E'], cand['T']) < (bestc['k'], bestc['E'], bestc['T']):
                    bestc = cand
            if bestc is None:
                tot_s = None; break
            tot_s += bestc['k']; tot_e += bestc['E']; tot_t += bestc['T']
        sens.append(dict(rho=rho, 架次数=tot_s, 总能耗kWh=None if tot_s is None else round(tot_e, 6),
                         累计作业时间s=None if tot_s is None else round(tot_t, 3)))
    sens_df = pd.DataFrame(sens)
    sens_df.to_csv(os.path.join(OUT, 'Q1_4_返航余量灵敏度.csv'), index=False, encoding='utf-8-sig')

    # 临界 rho（能量项首次成为 q_safe 的瓶颈）
    crit = {}
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        for k in ('A', 'B', 'C'):
            base = types[k]
            bx = site_boxes[i]
            w_max = max(b[1] for b in bx); v_of = {b[1]: b[2] for b in bx}
            found = None
            for rho in np.arange(0.0, 0.81, 0.005):
                t = dict(base); t['rho'] = float(rho)
                ms = max_safe_payload(t, geom[i], o['elev'], s['elev'] + C.CABIN, w_max, v_of[w_max])
                if ms['q_energy'] < ms['q_mass'] - 1e-9 and ms['q_energy'] < ms['q_vol'] - 1e-9:
                    found = float(rho); break
            if found is not None:
                crit[(k, i)] = found
    crit_rows = [dict(type=k, Si='S%03d' % i, rho_crit=round(v, 4))
                 for (k, i), v in sorted(crit.items(), key=lambda x: x[1])]
    pd.DataFrame(crit_rows).to_csv(os.path.join(OUT, 'Q1_4_临界rho.csv'),
                                   index=False, encoding='utf-8-sig')

    # ================= 报告 =================
    rep = []
    rep.append("# B3 问题一求解报告\n")
    rep.append("## Q1.1 最大安全载荷（瓶颈分解）\n")
    rep.append("| Si | d(km) | 机型 | 质量限 | 体积限 | 能量限 | 安全载荷 | 瓶颈 |")
    rep.append("|---|---|---|---|---|---|---|---|")
    for _, r in q11_df.iterrows():
        rep.append("| %s | %.2f | %s | %.2f | %.2f | %.2f | **%.2f** | %s |" % (
            r['Si'], r['d_km'], r['type'], r['q_mass'], r['q_vol'], r['q_energy'],
            r['q_safe'], r['bottleneck'] or '—'))
    rep.append("\n## Q1.2/Q1.3 组批与指标\n")
    rep.append("- 总架次数：**%d**（A:%d B:%d C:%d）" %
               (summary['sorties'], summary['by_type']['A'], summary['by_type']['B'],
                summary['by_type']['C']))
    rep.append("- 总运输能耗：**%.4f kWh**" % summary['energy'])
    rep.append("- 累计作业时间：**%.1f s**（= %.2f h）" % (summary['makespan'], summary['makespan'] / 3600))
    rep.append("- 最低返航 SOC：**%.2f%%**" % summary['soc_min'])
    rep.append("- 交付质量合计：%.2f kg（应为 758）/ 体积合计：%.4f m³（应为 2.011）" %
               (summary['mass_util'], summary['vol_util']))
    rep.append("\n## Q1.3 Pareto 前沿（全局非支配）\n")
    rep.append("| 架次数 | 总能耗 kWh | 累计作业时间 s |")
    rep.append("|---|---|---|")
    for _, r in ndf.iterrows():
        rep.append("| %d | %.4f | %.1f |" % (r['架次数'], r['总能耗kWh'], r['累计作业时间s']))
    rep.append("\n## Q1.4 返航余量灵敏度\n")
    rep.append("| rho | 架次数 | 总能耗 kWh | 累计作业时间 s |")
    rep.append("|---|---|---|---|")
    for _, r in sens_df.iterrows():
        rep.append("| %.0f%% | %s | %s | %s |" % (100 * r['rho'], r['架次数'],
                                                r['总能耗kWh'], r['累计作业时间s']))
    rep.append("\n### 临界 rho（能量首次成为瓶颈）\n")
    rep.append("| 机型 | 服务区 | rho_crit |")
    rep.append("|---|---|---|")
    for r in crit_rows[:20]:
        rep.append("| %s | %s | %.1f%% |" % (r['type'], r['Si'], 100 * r['rho_crit']))
    rep.append("\n共 %d 个（机型×服务区）组合在 rho≤80%% 内被能量约束。\n" % len(crit_rows))
    with open(os.path.join(REP, 'B3_Q1.md'), 'w', encoding='utf-8') as f:
        f.write("\n".join(rep))

    print("\n".join(rep[:8]))
    print("...")
    print("汇总:", json.dumps(summary, ensure_ascii=False))
    print("输出:", os.listdir(OUT))


if __name__ == '__main__':
    main()
