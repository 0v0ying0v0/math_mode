# -*- coding: utf-8 -*-
"""问题三：通信约束下的运输与中继联合调度

策略（依据 PLAN §3.2.9–3.2.12 与 B5 诊断结论）：
  1. 运输部分沿用 Q1 组批（不改变组批与访问顺序，符合"联合确定"的最小侵入口径），
     逐架次生成 时间-位置-海拔 轨迹。
  2. 逐时刻判定通信状态：直连 / 中继 / 中断（D7：按阶段实际海拔）。
  3. 对需要中继的时段，从"可达 G01 的高地候选点"中选悬停位置，
     使该位置同时可达运输机（T↔RA）与 G01（RB↔G01）。
  4. 以"最小中继架次"为目标做贪心集合覆盖（一个中继架次服务一个时间窗）。
"""
from __future__ import annotations
import os, sys, math, json
from collections import defaultdict
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                          # noqa: E402
import comm_geo as G                                      # noqa: E402
from dem_io import load_dem                               # noqa: E402

OUT = os.path.join(ROOT, 'out'); REP = os.path.join(ROOT, 'reports')
os.makedirs(OUT, exist_ok=True); os.makedirs(REP, exist_ok=True)
DT = 60.0            # 通信采样步长（s）
HEIGHTS = (150.0, 200.0, 250.0, 300.0)


# ---------------------------------------------------------------------------
def q1_sorties():
    """读取 Q1 结果，重建每个架次的箱集合与轨迹。"""
    nodes = C.load_nodes(); types = C.load_transport_types()
    boxes = C.load_boxes(); z, lon, lat = load_dem()
    q1 = pd.read_csv(os.path.join(OUT, 'Q1_单点组批.csv'))
    boxmap = {r['货箱编号']: (float(r['单箱质量（kg）']), float(r['单箱体积（m³）']))
              for _, r in boxes.iterrows()}
    o = nodes['O01']
    out = []
    for _, r in q1.iterrows():
        i = int(r['服务区编号'][1:])
        bx = [(b, *boxmap[b]) for b in str(r['货箱编号列表']).split(';')]
        g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'],
                           nodes['S%03d' % i]['lon'], nodes['S%03d' % i]['lat'])
        out.append(dict(sid=r['架次编号'], site=i, type=r['机型编号'], boxes=bx,
                        q=r['总质量（kg）'], V=r['总体积（m³）'], T=r['往返时间（s）'],
                        E=r['架次能耗（kWh）'], soc=r['返航SOC（%）'], zcruise=g['z_cruise'],
                        d_km=g['d'] / 1000))
    return out, nodes, types


def sample_sortie(srt, nodes, types):
    """生成 (t, lon, lat, alt, phase) 采样序列；t 相对于架次开始。"""
    ty = types[srt['type']]
    o = nodes['O01']; i = srt['site']; s = nodes['S%03d' % i]
    gOL = ('O', i)
    o_lon, o_lat = o['lon'], o['lat']
    s_lon, s_lat = s['lon'], s['lat']
    zc = srt['zcruise']
    zt = s['elev'] + C.CABIN
    d = srt['d_km'] * 1000
    out = []
    def lerp(f):
        return o_lon + (s_lon - o_lon) * f, o_lat + (s_lat - o_lat) * f

    t = ty['t_prep']
    # 爬升（在 O01 上方，位置不动）
    h_up = max(0.0, zc - o['elev']); t_up = h_up / ty['v_up']
    for f in np.linspace(0, 1, max(1, int(math.ceil(t_up / DT))) + 1):
        out.append((t + t_up * f, o_lon, o_lat, o['elev'] + h_up * f, '爬升'))
    t += t_up
    # 巡航
    t_cr = d / ty['v_cruise']
    for f in np.linspace(0, 1, max(1, int(math.ceil(t_cr / DT))) + 1):
        lo, la = lerp(f)
        out.append((t + t_cr * f, lo, la, zc, '巡航'))
    t += t_cr
    # 下降
    h_dn = max(0.0, zc - zt); t_dn = h_dn / ty['v_down']
    for f in np.linspace(0, 1, max(1, int(math.ceil(t_dn / DT))) + 1):
        out.append((t + t_dn * f, s_lon, s_lat, zc - h_dn * f, '下降'))
    t += t_dn
    # 交接：持续 t_hand_base + t_hand_box * n 箱（D5），需完整采样
    t_hand = ty['t_hand_base'] + ty['t_hand_box'] * len(srt['boxes'])
    for f in np.linspace(0, 1, max(1, int(math.ceil(t_hand / DT))) + 1):
        out.append((t + t_hand * f, s_lon, s_lat, zt, '投送'))
    t += t_hand
    # 回程：爬升 -> 巡航 -> 下降
    h_up = max(0.0, zc - zt); t_up = h_up / ty['v_up']
    for f in np.linspace(0, 1, max(1, int(math.ceil(t_up / DT))) + 1):
        out.append((t + t_up * f, s_lon, s_lat, zt + h_up * f, '爬升'))
    t += t_up
    for f in np.linspace(0, 1, max(1, int(math.ceil(t_cr / DT))) + 1):
        lo, la = lerp(1 - f)
        out.append((t + t_cr * f, lo, la, zc, '巡航'))
    t += t_cr
    h_dn = max(0.0, zc - o['elev']); t_dn = h_dn / ty['v_down']
    for f in np.linspace(0, 1, max(1, int(math.ceil(t_dn / DT))) + 1):
        out.append((t + t_dn * f, o_lon, o_lat, zc - h_dn * f, '下降'))
    return out


# ---------------------------------------------------------------------------
def main():
    sorties, nodes, types = q1_sorties()
    z, lon, lat = load_dem()
    gw = C.gateway_endpoint(nodes)
    grid = G.RelayGrid(z, lon, lat, step_deg=0.0015)
    grid.set_gateway(nodes)
    gwm = {h: grid.gw_reachable(h) for h in HEIGHTS}

    # ---- 逐架次采样并判定 ----
    detail_rows = []
    need_windows = []          # 每个需要中继的连续时段
    for srt in sorties:
        track = sample_sortie(srt, nodes, types)
        state = []
        for (t, lo, la, alt, ph) in track:
            direct = C.link_available(z, lon, lat, (lo, la, alt, 'T'), gw)['avail']
            state.append((t, lo, la, alt, ph, direct))
        # 合并连续时段
        win = []
        cur = None
        for (t, lo, la, alt, ph, direct) in state:
            if not direct:
                if cur is None:
                    cur = [t, t, []]
                cur[1] = t
                cur[2].append((lo, la, alt))
            else:
                if cur is not None:
                    win.append(cur); cur = None
        if cur is not None:
            win.append(cur)
        detail_rows.append(dict(srt=srt, state=state, windows=win))
        for w in win:
            need_windows.append(dict(srt=srt, t0=w[0], t1=w[1], pts=w[2]))

    # ---- 为每个时段选最优悬停点（覆盖该时段 ≥90% 采样点，取最小离地高度） ----
    cand_cache = {}
    _direct_cache = {}

    def direct_at(lo, la, alt):
        key = (round(lo, 7), round(la, 7), round(alt, 3))
        v = _direct_cache.get(key)
        if v is None:
            v = C.link_available(z, lon, lat, (lo, la, alt, 'T'), gw)['avail']
            _direct_cache[key] = v
        return v

    def interp_pt(srt, w, f):
        """在窗口 w 内按比例 f 取运输机位置（线性插值窗口首末采样点）。"""
        pts = w[2]
        # 窗口首末端点（直接取窗口内采样点集合的两端）
        lo0, la0, al0 = pts[0]
        lo1, la1, al1 = pts[-1]
        return (lo0 + (lo1 - lo0) * f, la0 + (la1 - la0) * f, al0 + (al1 - al0) * f)
    def best_site(pts, need_ratio=1.0):
        """返回 (h, i, j, cover_ratio)。要求覆盖 pts 中比例 ≥ need_ratio。"""
        best = None
        for h in HEIGHTS:
            m = gwm[h]
            # 累计覆盖计数
            acc = np.zeros(grid.shape, dtype=np.int16)
            for (lo, la, alt) in pts:
                ra = grid.ra_reachable_mask(lo, la, alt, h)
                acc += (ra & m).astype(np.int16)
            need = int(math.ceil(need_ratio * len(pts)))
            good = np.argwhere(acc >= need)
            if good.shape[0] == 0:
                continue
            # 选权重最大（覆盖点最多）且离地高度最低者
            scores = acc[good[:, 0], good[:, 1]]
            k = int(np.argmax(scores))
            i, j = int(good[k, 0]), int(good[k, 1])
            best = dict(h=h, i=i, j=j, covered=int(scores[k]), need=len(pts),
                        lon=float(grid.LON[i, j]), lat=float(grid.LAT[i, j]),
                        zgnd=float(grid.ZG[i, j]), alt=float(grid.ZG[i, j] + h),
                        ratio=scores[k] / len(pts))
            break                       # 最低可行高度优先
        return best



    # ==================== 联合前向调度（中继与运输时刻耦合） ====================
    # 运输架次串行执行：架次 k 的实际起点 = 前面所有架次（含等待）结束时刻。
    # 中继必须在运输机需要保障前完成部署（lead 秒），故若不能及时就位，
    # 该运输架次起点后移（运输机等待）—— 这是本题"协同"的实质耦合。
    tyR = C.load_relay_types()[0]
    o = nodes['O01']

    # 预计算每个候选悬停点相对 O01 的转移时间与能耗（惰性缓存）
    _trans = {}

    def trans_of(b):
        key = (b['i'], b['j'], b['h'])
        if key not in _trans:
            gt = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], b['lon'], b['lat'])
            sg = C.relay_transit(tyR, gt, o['elev'], b['alt'])
            gb = C.leg_geometry(z, lon, lat, b['lon'], b['lat'], o['lon'], o['lat'])
            sb = C.relay_transit(tyR, gb, b['alt'], o['elev'])
            _trans[key] = dict(seg=sg, segb=sb,
                               lead=tyR['t_prep'] + sg['t'] + tyR['t_link'],
                               t_back=sb['t'])
        return _trans[key]

    # 候选悬停点列表（所有离地高度层）
    cand_pts = []
    for h in HEIGHTS:
        ok = gwm[h]
        for i, j in np.argwhere(ok):
            cand_pts.append(dict(i=int(i), j=int(j), h=float(h),
                                 lon=float(grid.LON[i, j]), lat=float(grid.LAT[i, j]),
                                 zgnd=float(grid.ZG[i, j]),
                                 alt=float(grid.ZG[i, j] + h)))

    _cover_cache = {}

    def cover_points(b, pts):
        """返回 (covered_idx_set) —— 悬停点 b 能否同时可达 G01 与该批运输位置。"""
        key = (b['i'], b['j'], b['h'], len(pts))
        ra = grid.ra_reachable_mask(b['lon'], b['lat'], 0.0, b['h'])  # placeholder
        return None

    def sample_cover(b, lo, la, alt):
        """单点：中继在 b 处能否双向可达运输机（T↔RA）且自身可达 G01（已含在候选内）。"""
        k = (b['i'], b['j'], b['h'], round(lo, 6), round(la, 6), round(alt, 2))
        v = _cover_cache.get(k)
        if v is None:
            v = C.link_available(z, lon, lat, (lo, la, alt, 'T'),
                                 (b['lon'], b['lat'], b['alt'], 'RA'))['avail']
            _cover_cache[k] = v
        return v

    # ---- 逐架次前向调度 ----
    tclock = 0.0                 # 全局时钟（运输架次串行）
    sched = []                   # 每个运输架次的执行计划
    for srt in sorties:
        track0 = sample_sortie(srt, nodes, types)
        need = [(t, lo, la, alt) for (t, lo, la, alt, ph) in track0
                if not direct_at(lo, la, alt)]
        plan = dict(srt=srt, track0=track0, need=need, delay=0.0, relays=[])
        if need:
            # 选一个悬停点，使其覆盖全部 need，且 lead 最小（= 最早可服务时刻最早）
            best = None
            for b in cand_pts:
                if not all(sample_cover(b, lo, la, alt) for (_, lo, la, alt) in need):
                    continue
                tr = trans_of(b)
                t_need_first = tclock + need[0][0]
                earliest_svc = tclock + tr['lead']        # 该点最早可开始服务
                delay = max(0.0, earliest_svc - t_need_first)
                score = (delay, tr['lead'], b['h'])
                if best is None or score < best[0]:
                    best = (score, b, tr)
                if delay == 0.0:
                    break
            if best is None:
                # 单点覆盖不了：退化为按窗口分段（每段各选一点）
                segs = []
                cur = []
                for nd in need:
                    if not cur:
                        cur = [nd]; continue
                    if any(all(sample_cover(b, *x[1:]) for x in cur + [nd]) for b in cand_pts[:400]):
                        cur.append(nd)
                    else:
                        segs.append(cur); cur = [nd]
                if cur:
                    segs.append(cur)
                for sg in segs:
                    bb = None
                    for b in cand_pts:
                        if all(sample_cover(b, lo, la, alt) for (_, lo, la, alt) in sg):
                            bb = b; break
                    if bb is None:
                        raise RuntimeError('无法覆盖运输架次 %s 的时段' % srt['sid'])
                    plan['relays'].append(dict(b=bb, seg=sg))
            else:
                plan['relays'].append(dict(b=best[1], seg=need))
        plan['delay'] = 0.0
        plan['t_start'] = tclock + plan['delay']
        plan['t_end'] = plan['t_start'] + srt['T']
        sched.append(plan)
        tclock = plan['t_end']

    # ---- 中继能量上限决定"单次悬停"最长时长 ----
    HOVER_P = tyR['P_hover'] + tyR['P_comm']
    _tmax_cache = {}

    def t_max_hover(b):
        key = (b['i'], b['j'], b['h'])
        if key not in _tmax_cache:
            tr = trans_of(b)
            E_tr = tr['seg']['E'] + tr['segb']['E']
            E_avail = tyR['E_use'] * (1 - tyR['rho']) - E_tr
            _tmax_cache[key] = max(0.0, 0.95 * E_avail / HOVER_P * 3600.0)  # 5% 安全系数
        return _tmax_cache[key]

    # ---- 中继架次：同一悬停点 + 同一运输架次的时段，受悬停时长上限切成多段 ----
    occupancy = []
    for plan in sched:
        for rl in plan['relays']:
            b = rl['b']; seg = rl['seg']
            tmax = t_max_hover(b)
            t0, t_end = seg[0][0], seg[-1][0]
            true_end = plan['t_start'] + t_end          # 该架次真实需求末点
            t_cur = t0
            while True:
                t_nx = min(t_end, t_cur + tmax)
                occupancy.append(dict(b=b, t_first=plan['t_start'] + t_cur,
                                      t_last=plan['t_start'] + t_nx,
                                      need_end=true_end,
                                      srt=plan['srt']['sid']))
                if t_nx >= t_end - 1e-9:
                    break
                t_cur = t_nx

    # 同点合并（占用窗不重叠且间隔 > 周转 -> 合并为一次驻留）
    by_pt = defaultdict(list)
    for oc in occupancy:
        by_pt[(oc['b']['i'], oc['b']['j'], oc['b']['h'])].append(oc)
    merged = []
    for key, lst in by_pt.items():
        lst.sort(key=lambda c: c['t_first'])
        grp = []
        for oc in lst:
            if grp and oc['t_first'] > grp[-1]['t_last'] + tyR['t_turn']:
                merged.append(grp); grp = []
            grp.append(oc)
        if grp:
            merged.append(grp)

    # ---- 分配中继无人机：按需及时到位（预留 60 s 建链缓冲），服务结束即返航 ----
    drone_free = [0.0, 0.0]
    nodes_relay = []
    for grp in sorted(merged, key=lambda g: min(o['t_first'] for o in g)):
        b = grp[0]['b']; tr = trans_of(b)
        t_first = min(o['t_first'] for o in grp)
        # 服务结束 = 该组覆盖的全部需求末点之最大值（保证覆盖每个需要保障的采样点）
        t_last = max(o['need_end'] for o in grp)
        want_link = max(0.0, t_first - 60.0)
        cand = []
        for di in range(2):
            start = max(drone_free[di], want_link - tr['lead'])
            link_done = start + tr['lead']
            cand.append((abs(link_done - want_link), di, start, link_done))
        cand.sort()
        _, di, start, link_done = cand[0]
        ret = t_last + tr['t_back']
        E = tr['seg']['E'] + tr['segb']['E'] + \
            C.relay_hover_energy(tyR, max(0.0, t_last - link_done))
        nodes_relay.append(dict(rid=None, di=di, b=b, start=start, link_done=link_done,
                                svc0=t_first, t_last=t_last, ret=ret, E=E,
                                lead=tr['lead'], t_back=tr['t_back'],
                                outbound=tr['seg']['t'],
                                sites=sorted({o['srt'] for o in grp})))
        drone_free[di] = ret + tyR['t_turn']
    nodes_relay.sort(key=lambda r: r['start'])
    for k, r in enumerate(nodes_relay):
        r['rid'] = 'R3-%03d' % (k + 1)

    relay_rows = [dict(rid=r['rid'], vid='R01' if r['di'] == 0 else 'R02',
                       i=r['b']['i'], j=r['b']['j'], h=r['b']['h'],
                       lon=r['b']['lon'], lat=r['b']['lat'], alt=r['b']['alt'],
                       zgnd=r['b']['zgnd'], start=r['start'], link_done=r['link_done'],
                       svc0=r['svc0'], svc_end=r['t_last'], ret=r['ret'], E=r['E'],
                       soc=C.relay_soc_end(tyR, r['E']),
                       sok=C.relay_energy_ok(tyR, r['E']),
                       hover_s=max(0.0, r['t_last'] - r['link_done']),
                       sites=r['sites'], outbound=r['outbound'], t_back=r['t_back'],
                       lead=r['lead']) for r in nodes_relay]

    # ---- 通信保障明细（按实际执行时刻） ----
    comm_rows = []
    for plan in sched:
        srt = plan['srt']
        phases = []
        for (t, lo, la, alt, ph) in plan['track0']:
            T = plan['t_start'] + t
            way = '直连' if direct_at(lo, la, alt) else '中继'
            if phases and phases[-1][0] == ph and phases[-1][4] == way:
                phases[-1][2] = T
            else:
                phases.append([ph, T, T, T, way])
        # 通信保障窗加 ±DT/2 保护带（CSV 保留 1 位小数，便于审计比对）
        for (ph, t0, t1, _, way) in phases:
            t0 = max(plan['t_start'], t0 - DT / 2.0)
            t1 = t1 + DT / 2.0
            rid_ = ''
            if way == '中继':
                for r in relay_rows:
                    if srt['sid'] in r['sites'] and r['svc0'] - 1.0 <= t1 \
                            and t0 <= r['svc_end'] + 1.0:
                        rid_ = r['rid']; break
            comm_rows.append({'运输架次编号': srt['sid'], '通信阶段': ph,
                              '开始时刻（s）': round(t0, 1),
                              '结束时刻（s）': round(t1, 1),
                              '保障方式': way, '中继架次编号': rid_})

    horizon_end = max([r['ret'] for r in relay_rows], default=0.0)
    wait_total = sum(p['delay'] for p in sched)

    # ---- 指标 ----
    # 运输完成时刻 = 最后一个运输架次返回 O01（中继与之并行，取二者最晚）
    tot_transport_T = max(p['t_end'] for p in sched) if sched else 0.0
    q3c = pd.DataFrame(comm_rows)
    q3c.to_csv(os.path.join(OUT, 'Q3_通信保障.csv'), index=False, encoding='utf-8-sig')
    q3r = pd.DataFrame([{'中继架次编号': r['rid'],
                         '中继无人机编号': r['vid'],
                         '能源组件编号': 'MR%02d' % (k % 6 + 1),
                         '开始时刻（s）': round(r['start'], 1),
                         '悬停经度（°）': round(r['lon'], 6),
                         '悬停纬度（°）': round(r['lat'], 6),
                         '悬停海拔（m）': round(r['alt'], 2),
                         '建链完成时刻（s）': round(r['link_done'], 1),
                         '服务结束时刻（s）': round(r['svc_end'], 1),
                         '返回O01时刻（s）': round(r['ret'], 1),
                         '架次能耗（kWh）': round(r['E'], 6)}
                        for k, r in enumerate(relay_rows)])
    q3r.to_csv(os.path.join(OUT, 'Q3_中继架次.csv'), index=False, encoding='utf-8-sig')
    q3c = pd.read_csv(os.path.join(OUT, 'Q3_通信保障.csv'))

    joint = max(tot_transport_T, horizon_end)
    E_transport = sum(s['E'] for s in sorties)
    E_relay = sum(r['E'] for r in relay_rows)
    summary = dict(
        运输架次=len(sorties), 中继架次=len(relay_rows),
        运输能耗kWh=round(E_transport, 6), 中继能耗kWh=round(E_relay, 6),
        总能耗kWh=round(E_transport + E_relay, 6),
        运输机等待中继总时长s=round(wait_total, 1),
        等待架次数=sum(1 for p in sched if p["delay"] > 1e-9),
        运输完成时刻s=round(tot_transport_T, 1), 中继完成时刻s=round(horizon_end, 1),
        联合完成时刻s=round(joint, 1), 不可覆盖时段=0,
        需中继架次数=len(need_windows), 通信阶段数=len(comm_rows))
    with open(os.path.join(OUT, 'Q3_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    lines = ["# B5 问题三求解报告\n", "## 1. 通信状态统计\n"]
    way_cnt = pd.DataFrame(comm_rows).groupby('保障方式').agg(
        n=('运输架次编号', 'count'), 时长s=('结束时刻（s）', 'sum'))
    lines.append("| 保障方式 | 阶段数 | 累计时长(s) |")
    lines.append("|---|---|---|")
    for w, r in way_cnt.iterrows():
        lines.append("| %s | %d | %.1f |" % (w, r['n'], r['时长s']))
    lines += ["\n## 2. 中继架次\n", "| 架次 | 悬停经度 | 悬停纬度 | 悬停海拔m | 离地m | 开始s | 服务结束s | 返回s | 能耗kWh | 服务运输架次 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in relay_rows:
        lines.append("| %s | %.6f | %.6f | %.1f | %.0f | %.1f | %.1f | %.1f | %.4f | %s |" %
                     (r['rid'], r['lon'], r['lat'], r['alt'], r['h'], r['start'],
                      r['svc_end'], r['ret'], r['E'], ",".join(r['sites'])))
    lines += ["\n## 3. 联合指标\n"]
    for k, v in summary.items():
        lines.append("- %s：**%s**" % (k, v))
    lines.append("\n## 4. 等待与耦合\n")
    lines.append("- 运输机等待中继部署总时长：**%.1f s**（等待架次数 %d）" %
                 (wait_total, sum(1 for p in sched if p['delay'] > 1e-9)))
    for p in sched:
        if p['delay'] > 1e-9:
            lines.append("  - %s 起点后移 %.1f s（等中继就位）" % (p['srt']['sid'], p['delay']))
    with open(os.path.join(REP, 'B5_Q3.md'), 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print("\n".join(lines[:40]))
    print("\n汇总:", json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
