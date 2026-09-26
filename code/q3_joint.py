# -*- coding: utf-8 -*-
"""问题三：通信约束下的运输—中继联合调度（联合重排程版）

口径（spec/task_spec.md §3）：

  1. **运输层直接继承 Q2** 的 37 个架次与 8 架运输机的并行排程（T-3.1 ⇒ T-2.x），
     本模块只在"直连不可用"的时段插入中继驻留；若中继来不及就位，则**真实延迟
     运输架次**，随后逐箱复算交付时刻并复核首批截止/医疗期望时限（T-2.8/T-2.9，
     继承 Q2 的硬约束口径）。延迟在"同运输机不重叠 + 同电池不重叠"链上传播。
  2. 通信判定的唯一物理口径是 ``core.link_available``（治理检查 G-02）。几何加速用
     ``comm_geo.CoverIndex``，其掩膜与 ``link_available`` **逐位一致**（同为 400 点
     射线采样、同一自由空间损耗门限），故掩膜为真 ⇒ 精确口径必为真。
  3. 中继：≤2 架（R01/R02）× ≤6 能源组件（MR01..MR06）；O01 → 悬停建链 → 服务 →
     返回 O01，架次间 τ_turn；悬停离地 ≤ h_max；能耗 ≤ (1-ρ)·E_use（T-3.6..T-3.10）。
  4. 对外时刻一律**整数秒**（T-5.3）：求解与输出共用同一整数时间基准
     （运输起点由 ``propagate`` 向上取整、样本相对偏移为整数），故建链完成/服务
     结束/返回本来就是整数，覆盖关系按构造一致，不存在"取整后重算"的二次口径。
  5. 中继库存仅 2 架而并行运输下需中继样本时间窗并发峰值 > 2，故**联合重排程**：
     中继来不及就位时按最紧时限优先真实延迟运输架次，逐箱复算交付时刻。

输出：``out/Q3_中继架次.csv``、``out/Q3_通信保障.csv``、``out/Q3_架次延迟.csv``、
      ``out/Q3_逐箱交付.csv``、``out/Q3_灵敏度.csv``、``out/Q3_summary.json``、
      ``reports/B5_Q3.md``；控制台打印独立审计结果。

  **可行性闸门**：审计复算若有任一硬指标不为零（通信中断、首批/医疗违反、时序/能量/
  库存冲突、内层不动点未收敛），则以非零码退出——不可行方案不得作为成品流入论文与
  导出链（诊断产物仍写出，便于定位）。

  ``Q3_SWEEP=0`` 可跳过贪心常数灵敏度扫描（约 25 min）并复用 ``out/Q3_灵敏度.csv``，
  报告中该表会标注「复用」；默认 ``Q3_SWEEP=1`` 完整重算。
"""
from __future__ import annotations
import os, sys, math, json, time, bisect
from collections import defaultdict, Counter
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                          # noqa: E402
import comm_geo as G                                      # noqa: E402
from dem_io import load_dem                               # noqa: E402

OUT = os.path.join(ROOT, 'out'); REP = os.path.join(ROOT, 'reports')
os.makedirs(OUT, exist_ok=True); os.makedirs(REP, exist_ok=True)

DT = 60.0                 # 通信判定采样步长（s），与全文口径一致
HEIGHTS = (150.0, 200.0, 250.0, 300.0)
GRID_STEP_DEG = 0.0015
MAX_OUTER = 20            # 延迟 ↔ 中继耦合的外层不动点迭代上限
MAX_INNER = 8             # 单次规划内 req 自洽的迭代上限
HOVER_RESERVE = 0.999     # 悬停时长预留，吸收整数秒取整带来的越界

PHASES = ('爬升', '巡航', '下降', '投送')


def log(msg):
    print(msg, flush=True)


def ceil_i(x):
    return int(math.ceil(x - 1e-9))


# ===========================================================================
# 1. 读 Q2 结果，重建 37 个运输架次
# ===========================================================================
def load_q2_sorties(bmap):
    """由 Q2 的架次表与逐箱交付表重建运输架次（含逐箱交付时刻相对偏移）。"""
    q2 = pd.read_csv(os.path.join(OUT, 'Q2_运输架次.csv'))
    q2b = pd.read_csv(os.path.join(OUT, 'Q2_逐箱交付.csv'))
    by_jid = defaultdict(list)
    for _, r in q2b.iterrows():
        by_jid[r['架次编号']].append((r['货箱编号'], int(round(float(r['交付完成时刻（s）'])))))
    sorties = []
    for _, r in q2.iterrows():
        jid = r['架次编号']
        bl = sorted(by_jid[jid], key=lambda x: x[1])
        t0 = int(round(float(r['开始时刻（s）'])))
        t1 = int(round(float(r['返回O01时刻（s）'])))
        sites = [int(s.strip()[1:]) for s in str(r['访问服务区顺序']).split(';') if s.strip()]
        sorties.append(dict(
            jid=jid, uav=str(r['无人机编号']), type=str(r['机型编号']), bat=str(r['电池编号']),
            sites=sites, t0=t0, t1=t1, dur=t1 - t0, E=float(r['架次能耗（kWh）']),
            boxes=[b for b, _ in bl],
            boxt={b: t - t0 for b, t in bl},        # 逐箱交付时刻相对架次起点（整数 s）
        ))
    return sorties


def sortie_deadline_slack(srt, bmap):
    """该架次允许的最大延后量 = min(硬时限 − Q2 逐箱交付时刻)（T-2.8/T-2.9）。"""
    sl = math.inf
    for b in srt['boxes']:
        row = bmap[b]
        lim = None
        if str(row['是否首批保障']) == '是':
            lim = float(row['首批截止时间（s）'])
        elif str(row['物资类型']) == '医疗物资':
            lim = float(row['期望送达时间（s）'])
        if lim is not None:
            sl = min(sl, lim - (srt['t0'] + srt['boxt'][b]))
    return sl


# ===========================================================================
# 2. 运输架次轨迹采样（相对架次起点）
# ===========================================================================
def sample_sortie_rel(z, lon, lat, nodes, ty, sites, nbox, dt=DT):
    """(t, lon, lat, alt, phase) 采样序列，t 相对架次起点。

    阶段：起点爬升 → 巡航 → 服务区下降 → 投送 → 服务区爬升 → … → 返航下降。
    D7 口径：爬升/巡航/下降/投送四阶段均须保持通信。
    """
    o = nodes['O01']
    trk = []
    t = ty['t_prep']
    cur_lon, cur_lat, cur_h = o['lon'], o['lat'], o['elev']

    def seg(t0, dur, p0, p1, h0, h1, ph, hold_xy=False):
        n = max(1, int(math.ceil(dur / dt)))
        for f in np.linspace(0.0, 1.0, n + 1):
            x = p0[0] if hold_xy else p0[0] + (p1[0] - p0[0]) * f
            y = p0[1] if hold_xy else p0[1] + (p1[1] - p0[1]) * f
            trk.append((t0 + dur * f, x, y, h0 + (h1 - h0) * f, ph))

    for si in sites:
        s = nodes['S%03d' % si]
        g = C.leg_geometry(z, lon, lat, cur_lon, cur_lat, s['lon'], s['lat'])
        zc, d = g['z_cruise'], g['d']
        zt = s['elev'] + C.CABIN
        t_up = max(0.0, zc - cur_h) / ty['v_up']
        seg(t, t_up, (cur_lon, cur_lat), (cur_lon, cur_lat), cur_h, zc, '爬升', True)
        t += t_up
        t_cr = d / ty['v_cruise']
        seg(t, t_cr, (cur_lon, cur_lat), (s['lon'], s['lat']), zc, zc, '巡航')
        t += t_cr
        t_dn = max(0.0, zc - zt) / ty['v_down']
        seg(t, t_dn, (s['lon'], s['lat']), (s['lon'], s['lat']), zc, zt, '下降', True)
        t += t_dn
        t_hand = ty['t_hand_base'] + ty['t_hand_box'] * nbox
        seg(t, t_hand, (s['lon'], s['lat']), (s['lon'], s['lat']), zt, zt, '投送', True)
        t += t_hand
        cur_lon, cur_lat, cur_h = s['lon'], s['lat'], zt

    g = C.leg_geometry(z, lon, lat, cur_lon, cur_lat, o['lon'], o['lat'])
    zc = g['z_cruise']
    t_up = max(0.0, zc - cur_h) / ty['v_up']
    seg(t, t_up, (cur_lon, cur_lat), (cur_lon, cur_lat), cur_h, zc, '爬升', True)
    t += t_up
    t_cr = g['d'] / ty['v_cruise']
    seg(t, t_cr, (cur_lon, cur_lat), (o['lon'], o['lat']), zc, zc, '巡航')
    t += t_cr
    t_dn = max(0.0, zc - o['elev']) / ty['v_down']
    seg(t, t_dn, (o['lon'], o['lat']), (o['lon'], o['lat']), zc, o['elev'], '下降', True)
    return trk


def dedup_track(trk):
    """相邻航段共享端点，会重复产生同一时刻的样本；同一时刻只保留后一阶段的样本
    （阶段边界瞬时，取后一阶段的运动学更贴近实际），并把时刻定为**整数秒**（T-5.3）。"""
    out = []
    for (t, lo, la, alt, ph) in trk:
        rec = (int(round(t)), lo, la, alt, ph)
        if out and out[-1][0] == rec[0]:
            out[-1] = rec
        else:
            out.append(rec)
    return out


# ===========================================================================
# 3. 中继模型：候选悬停点 + 转移时间/能耗/悬停上限
# ===========================================================================
class RelayModel:
    """候选悬停点集合及其转移与能量参数（惰性计算 + 记忆化）。"""

    def __init__(self, z, lon, lat, nodes, rt):
        self.z, self.lon, self.lat = z, lon, lat
        self.nodes, self.rt, self.o = nodes, rt, nodes['O01']
        self.grid = G.RelayGrid(z, lon, lat, step_deg=GRID_STEP_DEG)
        self.grid.set_gateway(nodes)
        pts = []
        for h in HEIGHTS:
            for i, j in np.argwhere(self.grid.gw_reachable(h)):
                pts.append((float(h), int(i), int(j)))
        self.pts = pts
        self.index = G.CoverIndex(self.grid, pts)
        self.plon = np.array([float(self.grid.LON[i, j]) for _, i, j in pts])
        self.plat = np.array([float(self.grid.LAT[i, j]) for _, i, j in pts])
        self.phgt = np.array([float(self.grid.ZG[i, j]) for _, i, j in pts])
        self.palt = self.phgt + np.array([h for h, _, _ in pts])
        self._geo, self._cov = {}, {}

    def geo(self, k):
        """lead / t_back / outbound_t / E_transit / t_max_hover / 离地高度。"""
        v = self._geo.get(k)
        if v is None:
            rt, o = self.rt, self.o
            h, i, j = self.pts[k]
            plon, plat, palt = float(self.plon[k]), float(self.plat[k]), float(self.palt[k])
            go = C.leg_geometry(self.z, self.lon, self.lat, o['lon'], o['lat'], plon, plat)
            so = C.relay_transit(rt, go, o['elev'], palt)
            gb = C.leg_geometry(self.z, self.lon, self.lat, plon, plat, o['lon'], o['lat'])
            sb = C.relay_transit(rt, gb, palt, o['elev'])
            E_tr = so['E'] + sb['E']
            P = rt['P_hover'] + rt['P_comm']
            E_avail = (1.0 - rt['rho']) * rt['E_use'] - E_tr
            v = dict(lead=rt['t_prep'] + so['t'] + rt['t_link'], t_back=sb['t'],
                     outbound=so['t'], E_tr=E_tr, h=h, i=i, j=j, lon=plon, lat=plat,
                     zgnd=float(self.phgt[k]), alt=palt,
                     t_max=max(0.0, HOVER_RESERVE * E_avail / P * 3600.0))
            self._geo[k] = v
        return v

    def cover(self, lo, la, alt):
        """该位置可服务运输机的候选点下标（精确口径，惰性 + 记忆化）。"""
        key = (round(lo, 7), round(la, 7), round(alt, 2))
        v = self._cov.get(key)
        if v is None:
            v = np.nonzero(self.index.cover(lo, la, alt))[0]
            self._cov[key] = v
        return v


# ===========================================================================
# 4. 需求采样：直连判定 + 需中继样本及其精确可行悬停点集
# ===========================================================================
def build_needs(z, lon, lat, nodes, types, sorties, rmodel):
    """返回 (need, COV, stats)。

    need[i] = dict(sid, t_rel, lon, lat, alt, phase)（t_rel 为整数秒）
    COV     : bool (n_need × n_cand)，第 i 行 = 第 i 个需中继样本的精确可行悬停点
    """
    gw = C.gateway_endpoint(nodes)
    _direct = {}

    def direct(lo, la, alt):
        key = (round(lo, 7), round(la, 7), round(alt, 2))
        v = _direct.get(key)
        if v is None:
            v = bool(C.link_available(z, lon, lat, (lo, la, alt, 'T'), gw)['avail'])
            _direct[key] = v
        return v

    need, rows = [], []
    n_samp = 0
    t_start = time.time()
    for si, srt in enumerate(sorties):
        ty = types[srt['type']]
        trk = sample_sortie_rel(z, lon, lat, nodes, ty, srt['sites'], len(srt['boxes']))
        srt['track'] = dedup_track(trk)
        srt['n_direct'] = srt['n_relay'] = 0
        for (t, lo, la, alt, ph) in srt['track']:
            n_samp += 1
            if direct(lo, la, alt):
                srt['n_direct'] += 1
            else:
                srt['n_relay'] += 1
                need.append(dict(sid=srt['jid'], t_rel=t, lon=lo, lat=la, alt=alt, phase=ph))
                rows.append((lo, la, alt))
        if (si + 1) % 10 == 0:
            log('    采样 %2d/%d 架次  样本 %d  需中继 %d  %.0fs'
                % (si + 1, len(sorties), n_samp, len(need), time.time() - t_start))

    n, m = len(need), len(rmodel.pts)
    COV = np.zeros((n, m), dtype=bool)
    t0 = time.time()
    for i, (lo, la, alt) in enumerate(rows):
        ks = rmodel.cover(lo, la, alt)
        COV[i, ks] = True
        if (i + 1) % 100 == 0:
            log('    覆盖集 %d/%d  %.0fs' % (i + 1, n, time.time() - t0))
    sizes = COV.sum(axis=1)
    return need, COV, dict(n_sample=n_samp, n_need=n,
                           n_empty=int((sizes == 0).sum()),
                           n_cache=len(_direct),
                           med_cov=int(np.median(sizes)) if n else 0)


# ===========================================================================
# 5. 延迟传播：同运输机 / 同电池不重叠（整数秒）
# ===========================================================================
def propagate(req, sorties, types, bat):
    """把"中继提出的延迟需求"传播成可行排程，返回每个架次的整数起点。"""
    out = {}
    by_type = defaultdict(list)
    for s in sorties:
        by_type[s['type']].append(s)
    for tp, js in by_type.items():
        ty = types[tp]
        js = sorted(js, key=lambda s: s['t0'])
        uav_free, bat_free = {}, {}
        for s in js:
            st = ceil_i(max(s['t0'] + max(0.0, req.get(s['jid'], 0.0)),
                            uav_free.get(s['uav'], 0.0), bat_free.get(s['bat'], 0.0)))
            out[s['jid']] = st
            uav_free[s['uav']] = st + s['dur']
            soc = max(0.0, 1.0 - s['E'] / ty['E_use'])
            bat_free[s['bat']] = ceil_i(st + s['dur'] + C.t_charge(soc, bat[tp]['T_full']))
    return out


# ===========================================================================
# 6. 中继驻留规划（单一整数时间基准；规划即成品，无事后取整）
# ===========================================================================
#  设计要点（为什么这样做）：
#  (a) **单一整数时间基准**：覆盖关系是「驻留段悬停窗口 [建链完成, 服务结束] 覆盖某批
#      需求样本」，而样本时刻 = 运输架次起点 T0 + 相对偏移。若先在浮点时基上定覆盖、
#      再取整平移 T0，覆盖关系会被破坏（实测出现 34 h 悬停 + 107 个"已规划却无覆盖"
#      样本）。故一切时刻取整数秒：T0 由 propagate 向上取整给出，故建链完成、服务结束、
#      返回均为整数，T-5.3 无需二次取整。
#  (b) **逐架次整段摆放**：同一运输架次的需中继样本在时间上连续、且跨度不超过该架次
#      航时（≤ 4 h），而中继换一次点位要付「返航 t_back + 周转 τ_turn + 再出航 lead」
#      的死时间（≈1500 s）。故「一段驻留吃下整架次样本」是代价最小的原子操作：段数
#      越少，总死时间越少。据此先用 build_rank 把每个样本的候选点位按「能同时服务
#      本架次多少样本」降序预筛（数千 → RANK_TOP），再对整架次贪心取覆盖数最大的点位。
#      预筛只影响速度，不影响可解性判定：失败路径会回退到全量候选再判一次。
#  (c) **就位冲突 ⇒ 整架次平移，平移量解析求解**：若首个样本时刻无中继能就位，所需最小
#      后推量 = 最早可行 ld − 首个样本时刻。由 _earliest_ld 的构造可知，整架次平移该量
#      后 ld 恰好等于新的首个样本时刻（max 的第一、三项同步右移、第二项 hi−tmax 不动），
#      故**一次平移即可就位**，不存在"来回试探仍不收敛"。
#  (d) **平移量以绝对起点回传**：若只把增量交给 propagate，那里的
#      max(·, uav_free, bat_free) 可能把增量整段夹掉（前一架次仍占着机位/电池时），
#      架次原地不动 → 每轮算出同一个增量 → 死循环。故 plan_pass 返回**绝对起点**
#      need_start[sid]，由 solve 折算成相对 Q2 起点的位移，保证架次真的后移。
#  (e) **时限只体现为次序（EDF）**：硬时限架次先摆，软目标架次后摆并填早先的空隙；中继
#      不够时**真实延迟运输架次**并逐箱复算交付时刻（T-3.1 耦合），违约量由 audit 上报。
# ===========================================================================
RANK_TOP = 40        # 每样本保留的候选点位数（按对同架次样本的覆盖数排序）
MAX_SHIFT_TRY = 6    # 单架次"平移重摆"次数上限（推导上 1 次即成功，留冗余）


def _load_of(rmodel, rt, k):
    """候选点 k 的（向上取整去程、向上取整回程、悬停上界、能耗往返）。"""
    g = rmodel.geo(k)
    return (ceil_i(g['lead']), ceil_i(g['t_back']),
            int(math.floor(g['t_max'])), g['E_tr'], g)


def _gaps(busy):
    """占用区间表 → 空闲间隙 [(g0, g1), ...]（g1=inf 表示到尾）。"""
    out, cur = [], 0
    for a, b in sorted(busy):
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    out.append((cur, math.inf))
    return out


def _earliest_ld(busy, obs_end, lead, tmax, t_from, hi):
    """最早可行的**建链完成时刻** ld ≥ t_from，满足

        [ld - lead, obs_end] 落在某空闲间隙内（obs_end = hi + t_back + τ_turn）
        且 悬停时长 hi - ld ≤ tmax。

    返回 None 表示该中继此刻没有可用于该点位的空隙。

    注意**不**要求 ld ≤ hi：ld > t_from 的情形由调用方解释为「本段无法按当前时基就位，
    需把该架次整体后推 ld − t0」。这正是 T-3.1 的耦合语义，而非不可行——若在此处按
    ld > hi 直接剔除，调用方只剩「等中继完全空闲」这一条更差的路（实测 A002 中段 5 个
    样本因此从 213 s 的应然后推被放大成 2394 s，进而违约）。
    """
    best = None
    for g0, g1 in _gaps(busy):
        if g1 < obs_end:
            continue
        ld = max(g0 + lead, hi - tmax, t_from)
        if best is None or ld < best:
            best = ld
    return best


MAX_GAP = 900.0          # 一段驻留内允许的最大需求样本间隔（s）：超过则另起一段。
                         # 取值理由：既避免为覆盖孤立样本而长时间空悬，又允许把间隔
                         # 数个采样点（60 s 步长）的同一批次需求并进同一段驻留。

MAX_HOVER_SPAN = 2400.0  # 一段驻留的时长上限（s，自首样本起）。驻留越长，对被另一架
                         # 中继服务的需求阻塞越久：实测不设上限时贪心会产出 7449 s 的长
                         # 驻留，把一架中继整日占死（联合完成时刻 21967 s、10 个架次被后推
                         # 12000+ s）；设 2400 s 后长驻留被拆成数段、吞吐恢复。灵敏度扫描
                         # （1200/1800/2400/3600/∞）见 reports/B5_Q3.md。

DEBUG_SID = os.environ.get('Q3_DEBUG_SID', '')   # 诊断用：打印该架次首次摆放的候选评分前 6 名

MAX_HARD_JUMP = 850.0    # 「跨硬时限架次」延伸的间隔上限（s）：一段驻留**借软目标样本
                         # 中转**去覆盖另一架硬时限架次时，自上一个硬时限样本起不得超过 850 s。
                         # 为什么必须设：MAX_GAP=900 s 只管"相邻两个被选样本"的间隔，软目标
                         # 样本可把两段间隔各 ~450 s 拼起来，使早窗口的驻留（A013+A010，
                         # 737..1130 s）一路"跳"到 2107 s 的 A014（实测跨 977 s），把一架中继
                         # 占到 3309 s——而 A014/A007 的部署必须在 1530/1575 s 前占住两架中继
                         # （去程 577/542 s），于是 A007 无处部署、被后推 4178 s 违约。
                         # 取值由本实例的四处临界间隔界定：必需的三处跨架次顺带为 A014→A012
                         # （205 s）、A007→A002 中段（759 s，A007 的整段点位按该跨度把 A002
                         # 的 5 个中段样本一次吃下），有害的一处为 A010→A014（977 s），
                         # 850 s 落在 759 与 977 之间（灵敏度 300/500/850/1000/∞ 见
                         # reports/B5_Q3.md）。


def build_rank(need, COV, top=RANK_TOP):
    """预筛候选点位：对每个需中继样本 i，在「覆盖 i 的候选点位」中按**同架次覆盖样本数**
    降序取前 top 个——即「在该点位悬停一次，最多能顺带服务本架次多少个采样点」，
    这正是 `scan` 逐点前向延伸所依据的量。

    为什么**不能**用全局覆盖数（各架次样本求和）：一次驻留的时长被 MAX_GAP /
    MAX_HOVER_SPAN 双重限制，不同架次、时刻相距很远的样本无法被同一次驻留服务，
    故「全局覆盖数」与单次驻留的价值无关。实测按全局计数排序会让所有样本的候选表
    退化成同一小簇「枢纽点」（各样本前 6 名几乎逐位相同），而这些枢纽对本架次往往
    只覆盖 1–2 个点；规划结果由「0 违约 / 0 中断」退化为内层不动点**发散**
    （每轮 +1537 s，20 轮封顶）并留下 4 个未覆盖样本。按同架次计数时，每个样本的
    候选表恰好是「能一次覆盖本架次整段窗口的点位」，与 scan 的语义一致。

    返回 (rank, trunc)，trunc[i] = 被截断的候选点数（不静默截断：失败路径回退全量候选）。"""
    by_sid = defaultdict(list)
    for i, nd in enumerate(need):
        by_sid[nd['sid']].append(i)
    score = {sid: COV[rows].sum(axis=0) for sid, rows in by_sid.items()}
    rank, trunc = {}, {}
    for i in range(len(need)):
        cands = np.nonzero(COV[i])[0]
        if cands.size == 0:
            rank[i] = np.empty(0, dtype=np.int64)
            continue
        sc = score[need[i]['sid']][cands]
        order = np.argsort(-sc, kind='stable')[:top]      # stable ⇒ 并列时按点位编号
        rank[i] = cands[order]
        trunc[i] = int(cands.size - order.size)
    return rank, trunc


def plan_pass(sorties, need, COV, rmodel, rt, n_uav, n_mod, t_full, T0, slack, rank,
              protected=()):
    """在固定时基 T0（整数秒）下做一次**完整**规划：按**时刻顺序**访问需求样本（受保护
    架次先摆，其次硬时限架次，软目标最后；同组内按时刻先后）。

    每段驻留从当前样本起、向前顺带覆盖间隔 ≤ MAX_GAP 的未覆盖样本（同一点位、同一悬停
    窗口内）。中继既已在空中，顺带服务不额外付「返航 + 周转 + 再出航」的死时间；若不给
    顺带覆盖，两架中继只能把 28 个并发需求**串行化**（实测每段只服务 1 个架次、16 个架次
    被后推数千秒）；若按「尽量长」无节制延伸，又会占满两架中继、饿死早段需求，故用
    MAX_GAP 封顶段长。

    扫描与访问**同按时刻**（否则"间隔 ≤ MAX_GAP 则后续只会更远"的剪枝不成立）；访问在
    时刻之上再做「受保护 → 硬时限 → 软目标」分层——软目标架次若先摆，会用一段长驻留占满
    两架中继（实测把 648 s 起的 17 个架次样本并成 6955 s 悬停），导致随后 2107/2117 s 两个
    硬时限架次（时限余量 1043/1181 s）无中继可用而被后推 2400+ s 违约。

    时限以「架次整体后推」体现：某样本在其时刻无中继可就位时，把该架次整体后推，
    由外层不动点重排（T-3.1 耦合）。返回 (episodes, need_start, dead)。
    """
    n = len(need)
    t_abs = np.array([int(T0[nd['sid']]) + int(nd['t_rel']) for nd in need], dtype=np.int64)
    sid_of = [nd['sid'] for nd in need]
    order = [int(i) for i in np.argsort(t_abs, kind='stable')]
    t_order = order                       # 时刻有序的样本下标
    t_sorted = [int(t_abs[i]) for i in order]   # 与之一一对应的时刻（升序，供二分定位）
    # 访问次序 = 受保护 → 硬时限（按首样本时刻 t_abs 升序，即时间顺序）→ 软目标（按时刻）。
    # deadline = 首样本时刻 + slack 只用于「硬/软分组」与「是否超限」判定，不参与访问排序
    # （按 deadline 的 EDF 次序会先摆长驻留的 A012/A002、饿死 2107/2117 s 的 A014/A007，见下）。
    FAR = 10 ** 18
    first_of, dl_of = {}, {}
    for i, s in enumerate(sid_of):
        t = int(t_abs[i])
        if s not in first_of or t < first_of[s]:
            first_of[s] = t
    for s in sorties:
        sl = slack.get(s['jid'], math.inf)
        dl_of[s['jid']] = FAR if sl == math.inf else first_of.get(s['jid'], 0) + int(sl)
    # 受保护架次（上一轮被判定超时限者）先于一切架次摆放：它们先占中继，其余架次只能避让。
    # 这是「违约→保护→重排」修复循环的执行端；protected 为空时退化为内部次序。
    prot = set(protected)
    by_sid_idx = defaultdict(list)
    for i, s in enumerate(sid_of):
        by_sid_idx[s].append(i)
    # 候选点位总数（= 能一次覆盖该架次**全部**样本的点位数）：越少越"受约束"。
    nfull, fullsets = {}, {}
    for s, ix in by_sid_idx.items():
        fs = np.nonzero(COV[ix].all(axis=0))[0]
        fullsets[s] = fs
        nfull[s] = int(fs.size)
    # 访问次序：受保护架次 → 硬时限架次（**按窗口出现的时刻**，即时间顺序）→ 软目标（按时刻）。
    # 为什么硬时限既不按 EDF 也不按"可用点位最少优先"：
    #  · 按 deadline（EDF）：本实例中 A012（余量仅 101 s ⇒ deadline 3026）、A002（162 ⇒ 3042）
    #    的 deadline **早于** A014（1043 ⇒ 3150）、A007（1181 ⇒ 3298），于是 A012/A002 的长驻留
    #    先占死两架中继，A014/A007 反而无中继可用（实测各被后推 5000+ s）。
    #  · 按"可用点位最少优先"：A014 只有 15 个整段点位、A007 946 个，二点位集**不相交**，故
    #    按点位数排序会把 2107 s 的 A014 排在 737 s 的 A013/A010 之前——A014 的驻留段（去程
    #    577 s ⇒ 1530 s 起占用）一出，A013（737..1098）与 A010（744..1130）返航+周转结束时刻
    #    1744 s > 1530 s，二者同时失去早窗口，被后推 4434/4440 s（实测），并经「货箱转接」耦合
    #    把 10 个架次一起拖后 ≈3900 s，整表崩溃。
    #  · 按时刻：737 s 的 A013 与其后的 A010 窗口重叠，**可用同一点位一次驻留整段覆盖**（实测
    #    643 个点位同时覆盖二者全部 18 个样本），此后两架中继恰好腾出：一架 2107 s 服务 A014
    #    （顺带 A012），一架 2117 s 服务 A007（顺带 A002）——这正是本实例的唯一无违约结构。
    # 软目标最后按时刻填空：它们无时限，但仍按时刻先后而非任意次序。
    visit = sorted(range(n), key=lambda i: (
        0 if sid_of[i] in prot else 1,
        0 if dl_of.get(sid_of[i], FAR) < FAR else 1,
        int(t_abs[i])))
    turn = ceil_i(rt['t_turn'])
    busy, busy_mod = [[] for _ in range(n_uav)], [[] for _ in range(n_mod)]
    cov = np.zeros(n, dtype=bool)
    eps, need_start, dead = [], {}, []
    shifted = set()
    for i0 in visit:
        if cov[i0] or sid_of[i0] in shifted:
            continue
        t0 = int(t_abs[i0])
        dbg = [] if DEBUG_SID and sid_of[i0] == DEBUG_SID else None
        n_left = sum(1 for j in by_sid_idx[sid_of[i0]] if not cov[j])
        best, extra = None, None

        def scan(k):
            """点位 k 上从 t0 起、间隔 ≤ MAX_GAP 的连续未覆盖样本段（时间有序故可断）。"""
            lead, back, tmax, E_tr, g = _load_of(rmodel, rt, k)
            hi_cap = t0 + min(tmax, MAX_HOVER_SPAN)
            sid_own = sid_of[i0]
            sel, last, last_hard = [], t0, t0
            for j in t_order[bisect.bisect_left(t_sorted, t0):]:
                tj = int(t_abs[j])
                if tj > hi_cap:
                    break
                if cov[j] or not COV[j, k]:
                    continue
                if sel and tj - last > MAX_GAP:
                    break                      # 时间有序：间隔超限则后续只会更远
                if (sid_of[j] != sid_own and dl_of.get(sid_of[j], FAR) < FAR
                        and tj - last_hard > MAX_HARD_JUMP):
                    continue                   # 见 MAX_HARD_JUMP：禁止借软目标样本"跳"到远处硬时限架次
                if (dl_of.get(sid_own, FAR) < FAR and sid_of[j] != sid_own
                        and dl_of.get(sid_of[j], FAR) == FAR):
                    continue                   # 服务硬时限架次的驻留段不搭软目标样本的便车
                sel.append(j)
                last = tj
                if dl_of.get(sid_of[j], FAR) < FAR:
                    last_hard = tj
            return sel

        def pick(cands):
            b, ex = None, None
            for k in cands:
                k = int(k)
                lead, back, tmax, E_tr, g = _load_of(rmodel, rt, k)
                sel = scan(k)
                if not sel:                    # 该点位帮不上该样本 → 不作候选
                    continue
                hi = int(t_abs[sel[-1]])
                obs_end = hi + back + turn
                for d in range(n_uav):
                    ld = _earliest_ld(busy[d], obs_end, lead, tmax, t0, hi)
                    if ld is None:
                        # 该中继在末样本前腾不出空档：后推到「占用结束 + 去程 ≤ 首个样本
                        # 时刻」即可就位（取等号时 ld 恰为首个样本时刻）
                        free_d = max([b2 for _, b2 in busy[d]] + [0])
                        req = max(0, free_d + lead - t0)
                        if req > 0 and (ex is None or req < ex):
                            ex = req
                        continue
                    if ld > t0:                # 此刻无法就位 → 记录所需后推量
                        if ex is None or ld - t0 < ex:
                            ex = ld - t0
                        continue
                    E = E_tr + C.relay_hover_energy(rt, hi - ld)
                    need_end = hi + back + ceil_i(
                        C.t_charge(max(0.0, 1.0 - E / rt['E_use']), t_full))
                    m_sel = None
                    for m in range(n_mod):
                        if all(ob <= ld - lead or need_end <= oa for oa, ob in busy_mod[m]):
                            m_sel = m
                            break
                    if m_sel is None:          # 能源组件此刻无空闲 → 该组合不可行
                        continue
                    n_hard = sum(1 for j in sel if dl_of.get(sid_of[j], FAR) < FAR)
                    # 首选「整段吃下当前架次全部未覆盖样本」的点位：部分覆盖会把该架次的
                    # 剩余样本留给后续驻留，而后续驻留往往已无中继空档（实测 A014 被部分
                    # 覆盖 6/13 后整体后推 2451 s）。同点位整段覆盖 ⇒ 该架次一次到位。
                    sc = (-n_hard, -len(sel), hi - ld, E, k, d)
                    if dbg is not None:
                        dbg.append((sc, k, d, ld, hi, obs_end,
                                    Counter(sid_of[j] for j in sel)))
                    if b is None or sc < b[0]:
                        b = (sc, k, d, m_sel, ld, hi, list(sel), hi - ld, E, obs_end, need_end)
            return b, ex

        # 硬时限架次**首次摆放**只用"能一次覆盖其全部样本"的点位：部分覆盖会留下尾巴，
        # 而那截尾巴往往要另起一段、占用另一架中继的同一时段，把相邻硬时限架次挤出去
        # （实测 A014 被拆成 8+5 两段后，R02 在 2317..2527 s 被占，A007 的唯一下限窗口
        # 2307..3179 s 随之失去，被迫后推 2201 s）。无整段点位可用时才退化为部分覆盖。
        sid0 = sid_of[i0]
        cands0 = rank.get(i0, ())
        if dl_of.get(sid0, FAR) < FAR and n_left == len(by_sid_idx[sid0]):
            fs = fullsets.get(sid0)
            if fs is not None and fs.size:
                cands0 = fs
        best, extra = pick(cands0)
        if dbg is not None:                    # 诊断开关（Q3_DEBUG_SID）：打印候选评分前 6 名
            log('    [诊断] %s t0=%d 候选 %d 个可行，前 6 名：' % (sid_of[i0], t0, len(dbg)))
            for sc, k, d, ld, hi_, oe, cnt in sorted(dbg)[:6]:
                log('      sc=%s k=%5d R%02d ld=%6d hi=%6d obs_end=%6d %s'
                    % (tuple(int(x) if isinstance(x, (int, np.integer)) else round(x, 3)
                             for x in sc[:3]) + (int(sc[3]) if isinstance(sc[3], (int, np.integer))
                                                 else round(sc[3], 3),),
                       k, d + 1, ld, hi_, oe,
                       ' '.join('%s(%d)' % (a.replace('Q2-', ''), b) for a, b in sorted(cnt.items()))))
        if best is None:
            # 预筛可能漏掉"此刻恰好空闲"的点位 → 回退全量候选再判一次（不影响可解性）
            b2, e2 = pick(np.nonzero(COV[i0])[0])
            if b2 is not None:
                best, extra = b2, None
            elif e2 is not None and (extra is None or e2 < extra):
                extra = e2
        if best is None:
            if extra is not None and extra > 0:
                sid = sid_of[i0]               # 该架次整体后推，交外层重排
                need_start[sid] = int(T0[sid]) + int(math.ceil(extra))
                shifted.add(sid)
            else:
                dead.append(i0)                # 无任何候选点位可服务（物理不可覆盖）
            continue
        _, k, d, m_sel, ld, hi, sel, hv, E, obs_end, need_end = best
        lead, back = _load_of(rmodel, rt, k)[0], _load_of(rmodel, rt, k)[1]
        busy[d].append((ld - lead, obs_end))
        busy_mod[m_sel].append((ld - lead, need_end))
        cov[sel] = True
        eps.append(dict(k=k, dv=d, mv=m_sel, start=ld - lead, link_done=ld,
                        svc_end=hi, ret=hi + back, E=E, hover=hv, n=len(sel),
                        sel=[int(x) for x in sel], obs_end=obs_end, mod_end=need_end))
    return eps, need_start, dead
def relay_hover_limit(rmodel, rt):
    """各候选点的悬停时长上界（s，向下取整），供报告与审计引用。"""
    return {k: int(math.floor(rmodel.geo(k)['t_max'])) for k in range(len(rmodel.pts))}


# ===========================================================================
# 7. 联合求解：延迟 ↔ 中继驻留的内层不动点 + 违约修复的外层循环
# ===========================================================================
MAX_PROTECT = 8      # 外层修复循环轮数上限（保护集单调增长，至多 ≈ 硬时限架次数）


def _fixed_point(need, COV, rmodel, rt, sorties, types, bat, n_uav, n_mod, t_full,
                 base_slack, starts, rank, protected, tag='', verbose=True):
    """反复「传播延迟 → 完整规划 → 读回绝对起点需求」直至无新增。

    单调性：delays 只增不减，且以绝对起点形式回传（见 (d)），故每轮至少固化一个架次的
    后移；收敛轮的 eps 与 T0 严格自洽，可直接作为成品输出。若到 MAX_OUTER 仍未收敛，
    返回**最后一轮**的 (T0, eps)（二者仍自洽）并由 audit 如实报告。
    """
    delays = {s['jid']: 0.0 for s in sorties}
    T0 = propagate(delays, sorties, types, bat)
    eps, dead, unresolved, T0_out = [], [], 0.0, T0
    for it in range(MAX_OUTER):
        T0_out = T0
        slack = {s['jid']: base_slack[s['jid']] - (T0[s['jid']] - starts[s['jid']])
                 for s in sorties}
        eps, need_start, dead = plan_pass(sorties, need, COV, rmodel, rt, n_uav, n_mod,
                                          t_full, T0, slack, rank, protected=protected)
        grew = 0.0
        for sid, st in need_start.items():
            v = float(st - starts[sid])
            if v > delays.get(sid, 0.0) + 1e-9:
                grew = max(grew, v - delays[sid])
                delays[sid] = v
        unresolved = grew
        if verbose:
            log('  %s第 %2d 轮：驻留段 %3d，被延迟架次 %2d，最大延迟 %8.1f s，'
                '本轮新增 %6.1f s，不可覆盖样本 %d，受保护 %d'
                % (tag, it + 1, len(eps), sum(1 for v in delays.values() if v > 0),
                   max(delays.values()), grew, len(dead), len(protected)))
        if grew <= 0:
            break
        T0 = propagate(delays, sorties, types, bat)
    return T0_out, eps, delays, dead, unresolved


def solve(need, COV, rmodel, rt, sorties, types, bat, n_uav, n_mod, t_full,
          base_slack, starts, rank, tag='', verbose=True):
    """外层**违约修复**循环：内层不动点收敛后，若某些硬时限架次的后推量超过其时限余量，
    则把这些架次列入「受保护」集合，下一轮令其**先于一切架次**摆放（先占中继，其余避让）。

    为什么需要它：即使硬时限架次已按时刻先摆（plan_pass 的访问次序），两架中继仍可能
    不足以同时覆盖窗口重叠的硬时限架次，使某个硬时限架次的后推量超过其时限余量。
    本实例收敛后 15 个硬时限架次零超限（修复轮 1 受保护 0 个），该循环只作为安全网存在；
    若触发，保护集单调增长（每轮至少新增一个架次，否则退出），故至多硬时限架次数轮终止。

    返回收敛轮的 (T0, eps, delays, dead, unresolved)，可行性由 audit 独立复算判定。
    """
    hard = [s['jid'] for s in sorties if base_slack[s['jid']] != math.inf]
    protected, best, best_key = set(), None, None
    for rnd in range(MAX_PROTECT):
        out = _fixed_point(need, COV, rmodel, rt, sorties, types, bat, n_uav, n_mod,
                           t_full, base_slack, starts, rank, protected, tag, verbose)
        delays = out[2]
        viol = sorted(j for j in hard if delays[j] - base_slack[j] > 1e-9)
        n_int = sum(1 for h in hit_of(need, out[1], COV, out[0]) if h < 0)
        key = (n_int,                                                 # 先比通信中断
               int(out[4] > 1e-9),                                    # 再比内层是否收敛
               sum(delays[j] - base_slack[j] for j in viol),          # 再比硬时限超限总量
               len(viol),
               sum(delays.values()))                                  # 最后比总后推
        if best_key is None or key < best_key:
            best, best_key = out, key
        if verbose:
            log('  %s修复轮 %d：受保护 %2d 个，超时限架次 %2d 个，通信中断 %d 个，'
                '未消化延迟 %.0f s%s'
                % (tag, rnd + 1, len(protected), len(viol), n_int, out[4],
                   '' if not viol else '（' + ','.join(v.replace('Q2-', '') for v in viol) + '）'))
        fresh = set(viol) - protected
        if not viol or not fresh:            # 无违约，或违约者已在保护集内（再跑也不变）→ 终止
            break
        protected |= fresh                   # 与上轮保护集取并（单调，保证终止）
    return best                              # 返回**历轮最优**（不因循环终止而丢掉更优解）


def hit_of(need, eps, COV, T0):
    """每个需中继样本命中的驻留段下标（-1 = 未命中）。

    **全流程唯一的「样本是否被保障」判定**：窗口 [link_done, svc_end] 与几何覆盖掩膜
    COV[i, k] 同时成立，时刻按**最终时基** T0 复算（T0[sid] + t_rel）。规划、选解、
    审计、通信保障表四处都调用本函数，避免同一条规则被抄成四份而漂移（C1 复审项）。
    """
    hits = []
    for i, nd in enumerate(need):
        t = int(T0[nd['sid']] + nd['t_rel'])
        h = -1
        for j, e in enumerate(eps):
            if COV[i, e['k']] and e['link_done'] - 1e-9 <= t <= e['svc_end'] + 1e-9:
                h = j
                break
        hits.append(h)
    return hits


def audit(sorties, T0, eps, need, COV, rmodel, nodes, types, bmap, z, lon, lat, rt, bat,
          n_uav, n_mod, t_full):
    """独立复算全部硬约束：通信零中断、时序不重叠、能量/高度、时限、资源库存。"""
    gw = C.gateway_endpoint(nodes)
    res = {}
    # --- 运输机 / 电池时序 ---
    ov_u = ov_b = 0
    for tp in {s['type'] for s in sorties}:
        js = sorted([s for s in sorties if s['type'] == tp], key=lambda s: T0[s['jid']])
        for key in ('uav', 'bat'):
            seen = defaultdict(list)
            for s in js:
                a = T0[s['jid']]; b = a + s['dur']
                seen[s[key]].append((a, b))
            for v, iv in seen.items():
                iv.sort()
                for x, y in zip(iv, iv[1:]):
                    if y[0] < x[1] - 1e-9:
                        ov_u += (key == 'uav'); ov_b += (key == 'bat')
    res['运输机时序冲突'] = ov_u; res['电池时序冲突'] = ov_b

    # --- 中继时序 / 高度 / 能量 ---
    ov_r = 0
    seenr = defaultdict(list)
    for e in eps:
        seenr[e['dv']].append((e['start'], e['ret']))
    for v, iv in seenr.items():
        iv.sort()
        for x, y in zip(iv, iv[1:]):
            if y[0] < x[1] - 1e-9:
                ov_r += 1
    res['中继机时序冲突'] = ov_r
    res['中继悬停超限数'] = sum(1 for e in eps if rmodel.geo(e['k'])['h'] > rt['h_max'] + 1e-9)
    res['中继驻留超时长上界数'] = sum(1 for e in eps
                                      if e['hover'] > rmodel.geo(e['k'])['t_max'] + 1e-9)
    res['中继能耗超限数'] = sum(1 for e in eps if not C.relay_energy_ok(rt, e['E']))
    res['中继能源组件超库存'] = int(len({e['mv'] for e in eps}) > n_mod)
    res['中继机超库存'] = int(len({e['dv'] for e in eps}) > n_uav)

    # --- 通信：逐样本独立复算（窗口判定复用 hit_of，几何链路在此另做精确复核）---
    hits = hit_of(need, eps, COV, T0)
    n_relay = n_int = n_verify_fail = 0
    for i, nd in enumerate(need):
        h = hits[i]
        if h < 0:
            n_int += 1
            continue
        n_relay += 1
        g = rmodel.geo(eps[h]['k'])
        a1 = C.link_available(z, lon, lat, (nd['lon'], nd['lat'], nd['alt'], 'T'),
                              (g['lon'], g['lat'], g['alt'], 'RA'))['avail']
        a2 = C.link_available(z, lon, lat, (g['lon'], g['lat'], g['alt'], 'RB'), gw)['avail']
        if not (a1 and a2):
            n_verify_fail += 1
    res['需中继样本数'] = len(need)
    res['中继保障样本数'] = n_relay
    res['通信中断样本数'] = n_int
    res['中继点精确复核失败数'] = n_verify_fail
    res['直连样本数'] = sum(s['n_direct'] for s in sorties)

    # --- 逐箱交付时刻与时限 ---
    bad_first = bad_med = 0
    n_first = n_med = 0
    wdelay = 0.0
    for s in sorties:
        d = T0[s['jid']] - s['t0']
        for b in s['boxes']:
            row = bmap[b]
            td = T0[s['jid']] + s['boxt'][b]
            if str(row['是否首批保障']) == '是':
                n_first += 1
                bad_first += (td > float(row['首批截止时间（s）']) + 1e-9)
            if str(row['物资类型']) == '医疗物资':
                lim = float(row['期望送达时间（s）'])
                n_med += 1
                bad_med += (td > lim + 1e-9)
            if str(row['是否首批保障']) != '是' and str(row['物资类型']) != '医疗物资':
                wdelay += max(0.0, td - float(row['期望送达时间（s）'])) * \
                    float(row['应急优先系数'])
    res['首批保障箱数'] = n_first; res['首批截止违反数'] = bad_first
    res['医疗箱数'] = n_med; res['医疗期望违反数'] = bad_med
    res['加权迟延'] = wdelay
    res['最大运输延迟s'] = max([T0[s['jid']] - s['t0'] for s in sorties] + [0])
    res['被延迟架次数'] = sum(1 for s in sorties if T0[s['jid']] > s['t0'])
    res['运输完成时刻s'] = max(T0[s['jid']] + s['dur'] for s in sorties)
    res['中继完成时刻s'] = max([e['ret'] for e in eps] + [0])
    res['联合完成时刻s'] = max(res['运输完成时刻s'], res['中继完成时刻s'])
    res['运输能耗kWh'] = sum(s['E'] for s in sorties)
    res['中继能耗kWh'] = sum(e['E'] for e in eps)
    res['总能耗kWh'] = res['运输能耗kWh'] + res['中继能耗kWh']
    res['中继架次数'] = len(eps)
    res['运输架次数'] = len(sorties)
    return res


# ===========================================================================
# 7b. 贪心常数灵敏度（T-3.13：单参数扫描，其余参数固定在基准值）
# ===========================================================================
SWEEP_SPAN = (1200.0, 1800.0, 2400.0, 3600.0, math.inf)
SWEEP_JUMP = (300.0, 500.0, 850.0, 1000.0, math.inf)
SWEEP_COLS = ['参数', '取值', '超限架次数', '总超限s', '中继架次数', '运输完成s',
              '中继完成s', '最大后推s', '后推架次数', '不可覆盖样本数', '未消化延迟s']


def sweep_from_csv(path):
    """复用 out/Q3_灵敏度.csv（Q3_SWEEP=0 时的快路径）：表内容完全由该 CSV 决定。"""
    df = pd.read_csv(path)
    rows = []
    for _, r in df.iterrows():
        m = {'参数': str(r['参数']), '取值': str(r['取值'])}
        for k in SWEEP_COLS[2:]:
            v = float(r[k])
            m[k] = v if k in ('总超限s', '未消化延迟s') else int(v)
        rows.append(m)
    return rows


def _metrics_of(T0, eps, delays, dead, sorties, base_slack):
    """从一次求解结果里抽取灵敏度对比用的四个量（均为可独立复算的聚合量）。"""
    hard = [s['jid'] for s in sorties if base_slack[s['jid']] != math.inf]
    viol = [j for j in hard if delays[j] - base_slack[j] > 1e-9]
    over = sum(delays[j] - base_slack[j] for j in viol)
    return dict(超限架次数=len(viol), 总超限s=round(over, 1),
                不可覆盖样本数=len(dead), 中继架次数=len(eps),
                中继完成s=int(max([e['ret'] for e in eps] + [0])),
                运输完成s=int(max(T0[s['jid']] + s['dur'] for s in sorties)),
                最大后推s=int(max(delays.values()) if delays else 0),
                后推架次数=sum(1 for v in delays.values() if v > 1e-9))


def sensitivity(need, COV, rmodel, rt, sorties, types, bat, n_uav, n_mod, t_full,
                base_slack, starts, rank):
    """一维扫描 MAX_HOVER_SPAN 与 MAX_HARD_JUMP：每次只改一个常数，重跑完整求解。

    扫描复用已算好的采样/覆盖（两者都与这两个常数无关），只重跑规划+修复循环，
    故代价 ≈ 每个取值一次 solve。返回值按 (参数, 取值) 两维列表，写入 B5_Q3.md。
    """
    rows = []
    for name, seq in (('MAX_HOVER_SPAN', SWEEP_SPAN), ('MAX_HARD_JUMP', SWEEP_JUMP)):
        base = globals()[name]
        for v in seq:
            globals()[name] = v
            try:
                T0, eps, delays, dead, unres = solve(
                    need, COV, rmodel, rt, sorties, types, bat, n_uav, n_mod, t_full,
                    base_slack, starts, rank, tag='[%s=%s]' % (name, v), verbose=False)
            finally:
                globals()[name] = base                 # 基准值必须复原，后续输出依赖它
            m = _metrics_of(T0, eps, delays, dead, sorties, base_slack)
            m['参数'] = name
            m['取值'] = '∞' if v == math.inf else ('%.0f' % v)
            m['未消化延迟s'] = round(unres, 1)
            rows.append(m)
            log('  灵敏度 %s=%-5s → 超限 %d 架次 / 总超限 %7.1f s / 中继 %2d 架次 / '
                '联合完成 %6d s / 最大后推 %6d s / 不可覆盖 %d'
                % (name, m['取值'], m['超限架次数'], m['总超限s'], m['中继架次数'],
                   max(m['运输完成s'], m['中继完成s']), m['最大后推s'], m['不可覆盖样本数']))
    return rows


# ===========================================================================
# 8. 主流程
# ===========================================================================
def main():
    nodes = C.load_nodes(); types = C.load_transport_types()
    boxes = C.load_boxes(); z, lon, lat = load_dem()
    rt, rfleet, rbat = C.load_relay_types()
    bat = C.load_transport_fleet()[1]
    bmap = {r['货箱编号']: r for _, r in boxes.iterrows()}

    log('=== 问题三：运输—中继联合调度 ===')
    sorties = load_q2_sorties(bmap)
    log('继承 Q2：运输架次 %d 个，运输机 %d 架，机型 %s，Q2 完成时刻 %d s'
        % (len(sorties), len({s['uav'] for s in sorties}),
           '/'.join(sorted({s['type'] for s in sorties})),
           max(s['t1'] for s in sorties)))
    for s in sorties:
        s['slack'] = sortie_deadline_slack(s, bmap)

    log('构建中继候选悬停点（格距 %.4f° ≈ %.0f m）…' % (GRID_STEP_DEG, GRID_STEP_DEG * C.DEG2M))
    rmodel = RelayModel(z, lon, lat, nodes, rt)
    log('  候选悬停点 %d 个（离地 %s m）；门限半径由 core 链路预算反推：RA %.3f km / GW %.3f km'
        % (len(rmodel.pts), '/'.join('%.0f' % h for h in HEIGHTS),
           G.RA_RANGE_M / 1000, G.GW_RANGE_M / 1000))

    log('逐架次采样并判定通信状态（步长 %.0f s，唯一口径 core.link_available）…' % DT)
    need, COV, stats = build_needs(z, lon, lat, nodes, types, sorties, rmodel)
    n_uav, n_mod = len(rfleet), rbat['R']['count']; t_full = rbat['R']['T_full']
    log('  样本 %d，需中继 %d（%.1f%%），可行悬停点数中位 %d，无解样本 %d'
        % (stats['n_sample'], stats['n_need'],
           100.0 * stats['n_need'] / max(stats['n_sample'], 1), stats['med_cov'], stats['n_empty']))

    base_slack = {s['jid']: s['slack'] for s in sorties}
    starts = {s['jid']: s['t0'] for s in sorties}

    log('中继驻留规划（≤%d 架中继 × ≤%d 能源组件；单一整数时间基准）…' % (n_uav, n_mod))
    rank, trunc = build_rank(need, COV)
    n_tr = sum(trunc.values())
    if n_tr:
        log('  候选点位预筛：每样本保留 ≤%d 个（按同架次覆盖数排序），累计截断 %d 个候选；'
            '预筛失败时会回退全量候选复核，不影响可行性判定' % (RANK_TOP, n_tr))
    T0, eps, delays, dead, unresolved = solve(
        need, COV, rmodel, rt, sorties, types, bat, n_uav, n_mod, t_full,
        base_slack, starts, rank)
    if unresolved:
        log('  ⚠ 未收敛：仍有 %.0f s 的后推需求未落地，覆盖关系以审计为准' % unresolved)
    nd = sum(1 for v in delays.values() if v > 0)
    if nd:
        log('  注：%d 个架次被延迟（最大 %.0f s）以等待中继就位（T-3.1 耦合）' % (nd, max(delays.values())))

    log('独立审计…')
    res = audit(sorties, T0, eps, need, COV, rmodel, nodes, types, bmap, z, lon, lat,
                rt, bat, n_uav, n_mod, t_full)

    sweep_csv = os.path.join(OUT, 'Q3_灵敏度.csv')
    sw_reused = False
    if os.environ.get('Q3_SWEEP', '1') != '0':
        log('贪心常数灵敏度（各 5 个取值；每次重跑完整求解）…')
        sw = sensitivity(need, COV, rmodel, rt, sorties, types, bat, n_uav, n_mod,
                         t_full, base_slack, starts, rank)
        pd.DataFrame(sw)[SWEEP_COLS].to_csv(sweep_csv, index=False, encoding='utf-8-sig')
    elif os.path.exists(sweep_csv):
        sw, sw_reused = sweep_from_csv(sweep_csv), True
        log('灵敏度扫描已跳过（Q3_SWEEP=0）：复用 %s（表内标注"复用"）' % sweep_csv)
    else:
        sw = []
        log('⚠ 灵敏度扫描已跳过且无 %s 可复用：报告不含灵敏度表' % sweep_csv)

    # ---- 输出 ----
    rows = []
    for i, e in enumerate(sorted(eps, key=lambda x: x['link_done'])):
        g = rmodel.geo(e['k'])
        rows.append({
            '中继架次编号': 'R3-%03d' % (i + 1),
            '中继无人机编号': 'R%02d' % (e['dv'] + 1),
            '能源组件编号': 'MR%02d' % (e['mv'] + 1),
            '开始时刻（s）': e['start'],
            '悬停经度（°）': round(g['lon'], 6),
            '悬停纬度（°）': round(g['lat'], 6),
            # 海拔写 6 位小数而非 2 位：g['alt'] 在**已定稿的 6 位小数坐标**上取值，
            # 故离地高度恒等于设定值 h；若再把海拔四舍五入到 2 位小数，最坏可上浮
            # 5e-3 m 而重新越过 300 m 上限（C-3 的假性超限）。6 位小数的舍入误差
            # ≤5e-7 m，远小于 verify.py 的 1e-6 m 判定容差。
            '悬停海拔（m）': round(g['alt'], 6),
            '建链完成时刻（s）': e['link_done'],
            '服务结束时刻（s）': e['svc_end'],
            '返回O01时刻（s）': e['ret'],
            '架次能耗（kWh）': round(e['E'], 6),
        })
    q3r = pd.DataFrame(rows)
    q3r.to_csv(os.path.join(OUT, 'Q3_中继架次.csv'), index=False, encoding='utf-8-sig')

    # 逐架次延迟表：论文 §4.4 结果表与「被延迟架次数/最大后推」的唯一可核查来源
    drows = []
    for s in sorted(sorties, key=lambda x: T0[x['jid']]):
        jid, d, sl = s['jid'], T0[s['jid']] - s['t0'], base_slack[s['jid']]
        drows.append({
            '架次编号': jid, '机型编号': s['type'], '运输机编号': s['uav'],
            '电池编号': s['bat'], '硬时限': '是' if sl != math.inf else '否',
            '时限余量（s）': '' if sl == math.inf else int(sl),
            'Q2开始时刻（s）': s['t0'], '联合开始时刻（s）': int(T0[jid]),
            '后推量（s）': int(d),
            '是否超限': '是' if (sl != math.inf and d - sl > 1e-9) else '否',
            'Q2返回时刻（s）': s['t1'], '联合返回时刻（s）': int(T0[jid] + s['dur']),
        })
    pd.DataFrame(drows).to_csv(os.path.join(OUT, 'Q3_架次延迟.csv'),
                               index=False, encoding='utf-8-sig')

    # 逐箱交付表：Q3 重排后的实际交付时刻（独立校验脚本按本表复算首批/医疗满足率）
    brows = []
    for s in sorties:
        for b in s['boxes']:
            row = bmap[b]
            first = str(row['是否首批保障']) == '是'
            med = str(row['物资类型']) == '医疗物资'
            td = int(T0[s['jid']] + s['boxt'][b])
            lim = (float(row['首批截止时间（s）']) if first else
                   float(row['期望送达时间（s）']) if med else None)
            brows.append({
                '货箱编号': b, '架次编号': s['jid'],
                '交付完成时刻（s）': td,
                '是否首批保障': '是' if first else '否',
                '首批截止时间（s）': float(row['首批截止时间（s）']),
                '物资类型': str(row['物资类型']),
                '期望送达时间（s）': float(row['期望送达时间（s）']),
                '是否超限': '是' if (lim is not None and td > lim + 1e-9) else '否',
            })
    pd.DataFrame(brows).to_csv(os.path.join(OUT, 'Q3_逐箱交付.csv'),
                               index=False, encoding='utf-8-sig')

    # 通信保障：按 (阶段, 保障方式, 中继架次) 切分每个运输架次的时间轴
    cov_ep = {i: h for i, h in enumerate(hit_of(need, eps, COV, T0)) if h >= 0}
    need_at = {(nd['sid'], int(nd['t_rel'])): i for i, nd in enumerate(need)}
    crows = []
    for s in sorted(sorties, key=lambda x: T0[x['jid']]):
        t0i, t1i = T0[s['jid']], T0[s['jid']] + s['dur']
        key = []
        for (trel, lo, la, alt, ph) in s['track']:
            i = need_at.get((s['jid'], int(trel)))
            if i is None:
                key.append((ph, '直连', ''))
            else:
                rid = rows[cov_ep[i]]['中继架次编号'] if i in cov_ep else ''
                key.append((ph, '中继', rid))
        ts = [t0i + int(t) for (t, *_) in s['track']]
        i = 0
        while i < len(ts):
            j = i
            while j + 1 < len(ts) and key[j + 1] == key[i]:
                j += 1
            a = t0i if i == 0 else int(round((ts[i - 1] + ts[i]) / 2.0))
            b = t1i if j == len(ts) - 1 else int(round((ts[j] + ts[j + 1]) / 2.0))
            crows.append({'运输架次编号': s['jid'], '通信阶段': key[i][0],
                          '开始时刻（s）': int(a), '结束时刻（s）': int(b),
                          '保障方式': key[i][1], '中继架次编号': key[i][2]})
            i = j + 1
    q3c = pd.DataFrame(crows)[['运输架次编号', '通信阶段', '开始时刻（s）',
                               '结束时刻（s）', '保障方式', '中继架次编号']]
    q3c.to_csv(os.path.join(OUT, 'Q3_通信保障.csv'), index=False, encoding='utf-8-sig')

    summary = dict(res)
    summary.update(dict(
        通信阶段数=int(len(q3c)),
        中继保障阶段数=int((q3c['保障方式'] == '中继').sum()),
        直连保障阶段数=int((q3c['保障方式'] == '直连').sum()),
        无解样本数=int(stats['n_empty']),
        无法覆盖样本数=len(dead),
        未消化延迟s=round(unresolved, 1),
        通信判定步长s=DT,
        # 时限满足的**正向计数**（论文 §4.4 直接引用；违反数在 audit 中独立复算）
        首批截止满足数=int(res['首批保障箱数'] - res['首批截止违反数']),
        首批截止满足率=round((res['首批保障箱数'] - res['首批截止违反数'])
                            / max(res['首批保障箱数'], 1), 6),
        医疗期望满足数=int(res['医疗箱数'] - res['医疗期望违反数']),
        医疗期望满足率=round((res['医疗箱数'] - res['医疗期望违反数'])
                            / max(res['医疗箱数'], 1), 6),
        硬时限架次数=int(sum(1 for s in sorties if base_slack[s['jid']] != math.inf)),
        硬时限架次超限数=int(sum(1 for s in sorties if base_slack[s['jid']] != math.inf
                                and delays[s['jid']] - base_slack[s['jid']] > 1e-9)),
        中继并发上限=int(n_uav),
        # 悬停时长上界按**实际使用的悬停点**取（各点高程不同故上界不同），并给出实际最大悬停
        中继悬停时长上限s=int(min(relay_hover_limit(rmodel, rt)[e['k']] for e in eps))
        if eps else None,
        中继最大悬停s=int(max([e['hover'] for e in eps] + [0])),
    ))
    with open(os.path.join(OUT, 'Q3_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    L = ['# B5 问题三求解报告（运输—中继联合调度）\n',
         '## 0. 口径',
         '- 运输层**继承 Q2** 的 %d 个架次 / %d 架运输机并行排程（T-3.1）。' % (len(sorties), len({s['uav'] for s in sorties})),
         '- 通信判定唯一口径 `core.link_available`；几何加速掩膜与其逐位一致（400 点射线）。',
         '- 中继 ≤%d 架 × ≤%d 能源组件，O01→悬停→服务→返回，架次间 τ_turn=%d s。' % (n_uav, n_mod, rt['t_turn']),
         '- 采样步长 %.0f s；对外时刻取整数秒（T-5.3）。\n' % DT,
         '## 1. 通信状态',
         '| 指标 | 值 |', '|---|---|',
         '| 运输采样样本数 | %d |' % stats['n_sample'],
         '| 直连样本数 | %d |' % res['直连样本数'],
         '| 需中继样本数 | %d |' % res['需中继样本数'],
         '| 已由中继保障样本数 | %d |' % res['中继保障样本数'],
         '| **通信中断样本数** | **%d** |' % res['通信中断样本数'],
         '| 部署悬停点精确复核失败数 | %d |\n' % res['中继点精确复核失败数'],
         '## 2. 中继架次', '| 架次 | 中继机 | 组件 | 点(i,j) | 离地m | 海拔m | 开始s | 建链s | 服务结束s | 返回s | 悬停s | 能耗kWh |',
         '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for i, e in enumerate(sorted(eps, key=lambda x: x['link_done'])):
        g = rmodel.geo(e['k'])
        L.append('| R3-%03d | R%02d | MR%02d | (%d,%d) | %.0f | %.1f | %d | %d | %d | %d | %.0f | %.4f |'
                 % (i + 1, e['dv'] + 1, e['mv'] + 1, g['i'], g['j'], g['h'], g['alt'],
                    e['start'], e['link_done'], e['svc_end'], e['ret'], e['hover'], e['E']))
    L += ['\n## 3. 四指标（T-3.12）', '| 指标 | 值 |', '|---|---|',
          '| 配送及时性（首批截止） | %d/%d 满足，违反 %d |' % (res['首批保障箱数'] - res['首批截止违反数'], res['首批保障箱数'], res['首批截止违反数']),
          '| 配送及时性（医疗期望） | %d/%d 满足，违反 %d |' % (res['医疗箱数'] - res['医疗期望违反数'], res['医疗箱数'], res['医疗期望违反数']),
          '| 软目标加权迟延 | %.1f |' % res['加权迟延'],
          '| 联合完成时间 | **%d s**（运输 %d / 中继 %d） |' % (res['联合完成时刻s'], res['运输完成时刻s'], res['中继完成时刻s']),
          '| 总能耗 | **%.6f kWh**（运输 %.6f + 中继 %.6f） |' % (res['总能耗kWh'], res['运输能耗kWh'], res['中继能耗kWh']),
          '| 两类无人机架次 | 运输 %d 架次 / 中继 %d 架次 |' % (res['运输架次数'], res['中继架次数']),
          '\n## 4. 硬约束审计', '| 检查 | 结果 |', '|---|---|']
    for k in ('运输机时序冲突', '电池时序冲突', '中继机时序冲突', '中继悬停超限数',
              '中继驻留超时长上界数', '中继能耗超限数', '中继机超库存',
              '中继能源组件超库存'):
        L.append('| %s | %s |' % (k, '✅ %s' % res[k] if res[k] in (0, False) else '❌ %s' % res[k]))
    L += ['| 被延迟架次数 | %d（最大 %d s） |' % (res['被延迟架次数'], res['最大运输延迟s']),
          '| 通信保障阶段数 | %d（中继 %d / 直连 %d） |' % (len(q3c), summary['中继保障阶段数'], summary['直连保障阶段数']),
          '\n## 5. 贪心常数灵敏度（单参数扫描，其余固定基准值）',
          '扫描两处**人为引入的贪心限幅**（均非题面参数）：驻留时长上限 `MAX_HOVER_SPAN` 与'
          '跨硬时限架次的延伸间隔上限 `MAX_HARD_JUMP`。每次只改一个常数、重跑完整求解；'
          '采样/覆盖与这两个常数无关故复用，其余一切按求解器输出。'
          '判据：超限架次数必须为 0（硬时限），其余量越小越好。',
          '']
    if sw_reused:
        L.append('> ⚠ 本表由 `out/Q3_灵敏度.csv` 复用（`Q3_SWEEP=0`），**未在本轮重算**；'
                 '设 `Q3_SWEEP=1`（默认）可复算。\n')
    L += ['| 参数 | 取值 | 超限架次数 | 总超限s | 中继架次数 | 运输完成s | 中继完成s | 最大后推s | 后推架次数 | 不可覆盖样本数 |',
          '|---|---|---|---|---|---|---|---|---|---|']
    for m in sw:
        L.append('| %s | %s | %s | %.1f | %d | %d | %d | %d | %d | %d |'
                 % (m['参数'], m['取值'],
                    '**0 ✅**' if m['超限架次数'] == 0 else '**%d ❌**' % m['超限架次数'],
                    m['总超限s'], m['中继架次数'], m['运输完成s'], m['中继完成s'],
                    m['最大后推s'], m['后推架次数'], m['不可覆盖样本数']))
    if not sw:
        L.append('| （无数据：未扫描且无 out/Q3_灵敏度.csv 可复用） | | | | | | | | | |')
    L += ['',
          '基准取值 `MAX_HOVER_SPAN=2400 s`、`MAX_HARD_JUMP=850 s`（见上文常数注释的取值理由）。'
          '两处限幅的作用是**把长驻留拆段、禁止借软目标样本长距离跳跃**，从而把中继机的'
          '在场时间让给后续硬时限架次的部署；上表给出取下限/上限/不设限时的代价对比。']
    with open(os.path.join(REP, 'B5_Q3.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    log('\n'.join(L))
    log('\nSummary: ' + json.dumps(summary, ensure_ascii=False))

    # ---- 可行性闸门：不可行的方案**不允许**被当成成品发布 ----
    # 为什么必须硬失败而不是打印警告：本问的全部对外结论（通信零中断、首批/医疗
    # 100% 满足）都是「求解器输出 → 审计复算」的断言。一旦审计不为零，产物即与断言
    # 矛盾，若仍静默写出 CSV/summary 供论文与导出链引用，错误会以「已验证」的面目
    # 流入提交包。此处保留已写出的诊断产物（便于定位），但以非零码终止流水线。
    bad = []
    if res['通信中断样本数']:
        bad.append('通信中断 %d 个样本' % res['通信中断样本数'])
    if res['中继点精确复核失败数']:
        bad.append('中继点链路精确复核失败 %d 个' % res['中继点精确复核失败数'])
    if len(dead):
        bad.append('无法覆盖 %d 个样本（无任何候选点位可服务）' % len(dead))
    if unresolved > 1e-9:
        bad.append('内层不动点未收敛（未消化延迟 %.0f s）' % unresolved)
    if res['首批截止违反数']:
        bad.append('首批截止违反 %d 箱' % res['首批截止违反数'])
    if res['医疗期望违反数']:
        bad.append('医疗期望违反 %d 箱' % res['医疗期望违反数'])
    for k in ('运输机时序冲突', '电池时序冲突', '中继机时序冲突', '中继悬停超限数',
              '中继驻留超时长上界数', '中继能耗超限数', '中继机超库存',
              '中继能源组件超库存'):
        if res[k]:
            bad.append('%s=%s' % (k, res[k]))
    if bad:
        log('\n❌ 可行性闸门未通过（诊断产物已写出，但不作为成品）：' + '；'.join(bad))
        log('   处置：调整规划参数/候选预筛后重跑；不得据此更新论文数字。')
        sys.exit(1)
    log('\n✅ 可行性闸门通过：通信中断 0、首批/医疗违反 0、时序/能量/库存冲突 0、'
        '内层不动点已收敛。')


if __name__ == '__main__':
    main()
