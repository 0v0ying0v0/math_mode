# -*- coding: utf-8 -*-
"""问题三通信几何原语：中继覆盖集 + 逐时刻链路审计。

链路判定的唯一物理口径是 ``core.link_available``（见 D13 / 治理检查 G-02）。
本模块只做几何加速：用「地形视线 + 距离」两个可向量化的条件逼近它，而
**门限半径不手抄**，全部由 ``core`` 的链路预算反推（Lmax → 自由空间距离）。

  1. 生成中继候选格点（DEM 覆盖内）
  2. 预计算"中继在候选点可达 G01"与"中继在候选点可达某位置"的掩膜
  3. 对运输架次逐时刻采样，判定直连/中继/中断

一致性说明：掩膜 = 「与 ``core.los_blocked`` 同参数（400 点、含端点）的视线判定」
**且**「三维距离 ≤ 无遮挡可达半径」，故掩膜为 True 时 ``core.link_available``
必为 True（前者是后者的**子集**：遮挡链路的可用距离更短，掩膜直接排除）。
反向不成立（近距带遮挡仍可能可用），故掩膜偏保守、不误报可用。
真正部署的中继悬停点另由 ``tests/verify_q3_comm.py`` 逐点调用
``core.link_available`` 复核（该测试不引用本模块的掩膜函数）。
"""
from __future__ import annotations
import os, sys, math
from functools import lru_cache
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                   # noqa: E402
from dem_io import load_dem, bilinear_arr          # noqa: E402

# 门限半径由唯一物理口径反推，禁止手抄（G-02/G-03）：
#   Lmax = core.link_budget_limit(双向取紧)  →  r = core.free_space_range_km(Lmax)
# 结果（f=2400 MHz, L_SYS=3, P_SENS=-98, M_FADE=8）：
#   RB<->G01  Lmax=126.0 dB → 19.943 km
#   T <->RA   Lmax=116.0 dB →  6.307 km
GW_LMAX = C.link_budget_limit('RB', 'G01')
RA_LMAX = C.link_budget_limit('T', 'RA')
GW_RANGE_M = 1000.0 * C.free_space_range_km(GW_LMAX)
RA_RANGE_M = 1000.0 * C.free_space_range_km(RA_LMAX)
L_RB_MAX = GW_LMAX
L_RA_MAX = RA_LMAX


# ---------------------------------------------------------------------------
# 候选格点
# ---------------------------------------------------------------------------
class RelayGrid:
    """中继候选格点：规则经纬网格（步长按度给定），仅保留 DEM 覆盖内点。"""

    def __init__(self, z, lon, lat, step_deg=0.0015):
        self.z = z; self.lon = lon; self.lat = lat
        self.lon_g = np.arange(lon[0], lon[-1] + 1e-12, step_deg)
        self.lat_g = np.arange(lat[-1], lat[0] + 1e-12, step_deg)
        self.LON, self.LAT = np.meshgrid(self.lon_g, self.lat_g)
        # 发布精度 = 定义精度（C-3）：悬停点经纬度以 **6 位小数** 定稿（= out/Q3_中继架次.csv
        # 的写法），故网格坐标在建模前就先取整，保证「写出去的点」与「被采样的点」是同一个点。
        # 旧版写 6 位小数、却用未取整的网格坐标采样：两者相距 ≤5e-7°(≈0.055 m)，在陡坡上
        # 足以让地面高程差 0.008 m——实测 13 架次中 4 架次出现离地 300.008 m > 300 m 的
        # **假性超限**（code/verify.py C-3 判 FAIL）。取整后离地高度恒等于设定值 h。
        self.LON = np.round(self.LON, 6)
        self.LAT = np.round(self.LAT, 6)
        ci = np.clip(((self.LON - lon[0]) / (lon[1] - lon[0])).round().astype(int), 0, lon.size - 1)
        ri = np.clip(((lat[0] - self.LAT) / (lat[0] - lat[1])).round().astype(int), 0, lat.size - 1)
        # 格点地面高程：在**格点自身经纬度**上双线性插值，与 `code/verify.py` C-3 同口径。
        # 旧版取"最近像元" z[ri,ci]，但格点间距 0.0015°(=5.4 像元) 与像元格不对齐，
        # 格点与其最近像元中心最大相距 2.7 像元（≈80 m）：报告的悬停坐标处真实地面与
        # 所用地面不一致，实测 13 架次中 4 架次离地高度 303.1~310.3 m > 300 m 上限（C-3 FAIL）。
        _LO = np.clip(self.LON, lon[0], lon[-1])
        _LA = np.clip(self.LAT, lat[-1], lat[0])
        self.ZG = np.asarray(bilinear_arr(z, lon, lat, _LO, _LA), dtype=float)
        _bad = ~np.isfinite(self.ZG)             # 理论上 z 无 nodata；留防御分支
        if _bad.any():
            self.ZG = np.where(_bad, z[ri, ci], self.ZG)
        self.shape = self.LON.shape
        self._gw_cache = {}

    # ---- 射线遮挡（向量化） ----
    # 采样点集必须与 ``core.los_blocked`` 逐位一致（同为 linspace(0,1,400)，
    # **含两端点**），否则两条口径会在掠射情形下分歧：粗采样漏掉窄山脊 →
    # 掩膜误判"无遮挡"，使掩膜不再是 link_available 可用集的子集。
    # 实测 ngrid=40 时有 6.8% 的 (采样点, 候选点) 对出现该方向的误判，故此处
    # 直接用 400，与唯一物理口径 bit-level 相同（G-02）。
    def _los_mask_to_point(self, plon, plat, pz, ngrid=400):
        """返回 bool 掩膜：格点 (i,j) 与点 P 之间无地形遮挡（同 los_blocked 口径）。"""
        dlon = plon - self.LON
        dlat = plat - self.LAT
        latm = np.radians((self.LAT + plat) / 2.0)
        dx = dlon * C.DEG2M * np.cos(latm)
        dy = dlat * C.DEG2M
        dist = np.hypot(dx, dy)
        blocked = np.zeros(self.shape, dtype=bool)
        ts = np.linspace(0.0, 1.0, ngrid)
        for t in ts:
            qlon = self.LON + (plon - self.LON) * t
            qlat = self.LAT + (plat - self.LAT) * t
            ci = np.clip(((qlon - self.lon[0]) / (self.lon[1] - self.lon[0])).round().astype(int),
                         0, self.lon.size - 1)
            ri = np.clip(((self.lat[0] - qlat) / (self.lat[0] - self.lat[1])).round().astype(int),
                         0, self.lat.size - 1)
            zg = self.z[ri, ci]
            # 视线高度：从格点端 (ZG + h) 到 P 端 (pz)
            h = self._cur_h
            hline = (self.ZG + h) + (pz - (self.ZG + h)) * t
            blocked |= (hline < zg)
        return ~blocked

    def gw_reachable(self, h):
        """掩膜：中继在格点、离地 h 时与 G01 双向链路可用（地形 + 损耗门限）。"""
        if h in self._gw_cache:
            return self._gw_cache[h]
        glon, glat, gz, _ = self._gw_ep
        self._cur_h = h
        los = self._los_mask_to_point(glon, glat, gz)
        dist = self._dist3(glon, glat, gz, h)
        ok = los & (C.fspl_db_arr(dist / 1000.0) <= GW_LMAX)
        self._gw_cache[h] = ok
        return ok

    def _dist3(self, plon, plat, pz, h):
        latm = np.radians((self.LAT + plat) / 2.0)
        dx = (plon - self.LON) * C.DEG2M * np.cos(latm)
        dy = (plat - self.LAT) * C.DEG2M
        dz = (self.ZG + h) - pz
        return np.sqrt(dx ** 2 + dy ** 2 + dz ** 2)

    def ra_reachable_mask(self, plon, plat, pz, h):
        """掩膜：中继在格点、离地 h 时与位于 (plon,plat,pz) 的运输机可用。"""
        self._cur_h = h
        los = self._los_mask_to_point(plon, plat, pz)
        dist = self._dist3(plon, plat, pz, h)
        return los & (C.fspl_db_arr(dist / 1000.0) <= RA_LMAX)

    def set_gateway(self, nodes):
        self._gw_ep = C.gateway_endpoint(nodes)

    def cell_index(self, lon_q, lat_q):
        j = int(round((lon_q - self.lon_g[0]) / (self.lon_g[1] - self.lon_g[0])))
        i = int(round((lat_q - self.lat_g[0]) / (self.lat_g[1] - self.lat_g[0])))
        return i, j

    def hover_alt(self, i, j, h):
        return float(self.ZG[i, j] + h)


# ---------------------------------------------------------------------------
# 中继候选点筛选：对每个 (格点, h) 求"可与 G01 通信"
# ---------------------------------------------------------------------------
def build_relay_candidates(grid, heights=(150.0, 200.0, 250.0, 300.0)):
    cand = []
    for h in heights:
        ok = grid.gw_reachable(h)
        idx = np.argwhere(ok)
        for i, j in idx:
            cand.append(dict(i=int(i), j=int(j), h=float(h),
                             lon=float(grid.LON[i, j]), lat=float(grid.LAT[i, j]),
                             zgnd=float(grid.ZG[i, j]),
                             alt=float(grid.ZG[i, j] + h)))
    return cand


# ---------------------------------------------------------------------------
# 候选悬停点的可达覆盖查询（局部窗口加速）
# ---------------------------------------------------------------------------
class CoverIndex:
    """给定候选悬停点集合，查询"哪些悬停点可在某时刻服务运输机"。

    判定条件 = 「与运输机无地形遮挡」**且**「三维距离 ≤ RA_RANGE_M」，
    与 ``core.link_available(T↔RA)`` 逐点一致（`tests/verify_q3_comm.py` 复核）。

    加速手段：只对以运输机为中心、半径 ``half_m`` 的窗口内格点做射线遮挡
    计算，但射线采样仍在**整幅 DEM** 上取值，故与全网格计算等价。
    结果按 (经度, 纬度, 海拔) 记忆化。
    """

    def __init__(self, grid, points, ngrid=400, margin_m=2000.0):
        """points: 可迭代的 (h, i, j) 候选悬停点（须已满足可与 G01 通信）。"""
        self.grid = grid
        self.pts = [(float(h), int(i), int(j)) for h, i, j in points]
        self.ngrid = int(ngrid)
        self.heights = sorted({p[0] for p in self.pts})
        # 窗口按**粗网格**（LON/LAT）的格距计，不是 DEM 的格距
        lat_cell = abs(grid.lat_g[1] - grid.lat_g[0]) * C.DEG2M
        lon_cell = abs(grid.lon_g[1] - grid.lon_g[0]) * C.DEG2M * math.cos(
            math.radians(max(abs(grid.lat_g[0]), abs(grid.lat_g[-1]))))
        half_m = RA_RANGE_M + margin_m
        self.half_r = int(math.ceil(half_m / lat_cell))
        self.half_c = int(math.ceil(half_m / lon_cell))
        self.layermap = {}
        for h in self.heights:
            m = np.full(grid.shape, -1, dtype=np.int64)
            for k, (hh, i, j) in enumerate(self.pts):
                if hh == h:
                    m[i, j] = k
            self.layermap[h] = m
        self._cache = {}

    def cover(self, plon, plat, pz):
        """返回 bool 数组（长度 = 候选点数）：候选点 k 可在该位置服务运输机。"""
        key = (round(plon, 7), round(plat, 7), round(pz, 2))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        g = self.grid
        nrow, ncol = g.shape
        # 粗网格下标空间（LON/LAT 的定义域）
        ci = int(round((plon - g.lon_g[0]) / (g.lon_g[1] - g.lon_g[0])))
        ri = int(round((g.lat_g[0] - plat) / (g.lat_g[0] - g.lat_g[1])))
        r0 = max(0, ri - self.half_r); r1 = min(nrow, ri + self.half_r + 1)
        c0 = max(0, ci - self.half_c); c1 = min(ncol, ci + self.half_c + 1)
        LON = g.LON[r0:r1, c0:c1]; LAT = g.LAT[r0:r1, c0:c1]; ZG = g.ZG[r0:r1, c0:c1]
        latm = np.radians((LAT + plat) / 2.0)
        dx = (plon - LON) * C.DEG2M * np.cos(latm)
        dy = (plat - LAT) * C.DEG2M
        ts = np.linspace(0.0, 1.0, self.ngrid)      # 与 _los_mask_to_point 同一采样集
        out = np.zeros(len(self.pts), dtype=bool)
        for h in self.heights:
            sub = self.layermap[h][r0:r1, c0:c1]
            if not (sub >= 0).any():
                continue
            top = ZG + h
            blocked = np.zeros(ZG.shape, dtype=bool)
            for t in ts:
                qlon = LON + (plon - LON) * t
                qlat = LAT + (plat - LAT) * t
                hline = top + (pz - top) * t
                ii = np.clip(((g.lat[0] - qlat) / (g.lat[0] - g.lat[1])).round().astype(int),
                             0, g.lat.size - 1)
                jj = np.clip(((qlon - g.lon[0]) / (g.lon[1] - g.lon[0])).round().astype(int),
                             0, g.lon.size - 1)
                blocked |= (hline < g.z[ii, jj])
            d3 = np.sqrt(dx ** 2 + dy ** 2 + (top - pz) ** 2)
            ok = (~blocked) & (C.fspl_db_arr(d3 / 1000.0) <= RA_LMAX)
            sel = ok & (sub >= 0)
            out[sub[sel]] = True
        self._cache[key] = out
        return out

    def brute(self, plon, plat, pz):
        """全网格实现（仅供自检比对，不用于求解）：每个高度层一次全网格掩膜。"""
        out = np.zeros(len(self.pts), dtype=bool)
        for h in self.heights:
            los = self.grid.ra_reachable_mask(plon, plat, pz, h)
            d3 = self.grid._dist3(plon, plat, pz, h)
            m = self.layermap[h]
            ok = (m >= 0) & los & (C.fspl_db_arr(d3 / 1000.0) <= RA_LMAX)
            out[m[ok]] = True
        return out


# ---------------------------------------------------------------------------
# 两点经纬度线性插值（航段采样用）
# ---------------------------------------------------------------------------
def lerp_ll(nodes, a, b, f):
    A = nodes['O01'] if a == 'O' else nodes['S%03d' % a]
    B = nodes['O01'] if b == 'O' else nodes['S%03d' % b]
    return A['lon'] + (B['lon'] - A['lon']) * f, A['lat'] + (B['lat'] - A['lat']) * f


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    nodes = C.load_nodes(); z, lon, lat = load_dem()
    print("门限半径（由 core 链路预算反推，非手抄）:")
    for tag, role_a, role_b in (('RB<->G01 回传', 'RB', 'G01'), ('T <->RA  接入', 'T', 'RA'),
                               ('T <->G01 直连', 'T', 'G01')):
        Lmax = C.link_budget_limit(role_a, role_b)
        print("  %s  Lmax=%6.1f dB  r=%8.3f km  (遮挡后 r=%7.3f km)"
              % (tag, Lmax, C.free_space_range_km(Lmax),
                 C.free_space_range_km(Lmax, extra_loss=C.L_OBS)))
    print("  掩膜采用: GW_RANGE_M=%.1f m, RA_RANGE_M=%.1f m" % (GW_RANGE_M, RA_RANGE_M))
    # 与唯一口径 spot check：零距离必可用
    ep_g = C.gateway_endpoint(nodes)
    ep_o = (nodes['O01']['lon'], nodes['O01']['lat'], nodes['O01']['elev'] + 150.0, 'RB')
    print("  spot check link_available(同点 RB,G01) =", C.link_available(z, lon, lat, ep_g, ep_o)['avail'])
    for step in (0.003, 0.0015):
        g = RelayGrid(z, lon, lat, step_deg=step)
        g.set_gateway(nodes)
        print("=== 格点步长 %.4f° (≈%.0f m)  网格 %s ===" %
              (step, step * C.DEG2M, g.shape))
        for h in (150.0, 200.0, 250.0, 300.0):
            ok = g.gw_reachable(h)
            print("  离地 %3.0f m：可与 G01 通信的格点数 = %6d (%.2f%%)  地面高程范围 %.0f–%.0f"
                  % (h, ok.sum(), 100 * ok.mean(),
                     g.ZG[ok].min() if ok.any() else -1,
                     g.ZG[ok].max() if ok.any() else -1))
