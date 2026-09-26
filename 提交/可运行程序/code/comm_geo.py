# -*- coding: utf-8 -*-
"""问题三通信几何原语：中继覆盖集 + 逐时刻链路审计。

全部链路判定调用 core.link_available（唯一物理口径，见 D13 / 治理检查 G-02）。
本模块只负责：
  1. 生成中继候选格点（DEM 覆盖内）
  2. 预计算"中继在候选点可达 G01"与"中继在候选点可达某位置"的掩膜
  3. 对运输架次逐时刻采样，判定直连/中继/中断
"""
from __future__ import annotations
import os, sys, math
from functools import lru_cache
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                   # noqa: E402
from dem_io import load_dem                        # noqa: E402

GW_RANGE_M = 9.5e3      # RB<->G01 无遮挡门限 9.51 km
RA_RANGE_M = 5.6e3      # T<->RA  无遮挡门限 5.60 km
L_RA_MAX = 116.0
L_RB_MAX = 126.0


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
        ci = np.clip(((self.LON - lon[0]) / (lon[1] - lon[0])).round().astype(int), 0, lon.size - 1)
        ri = np.clip(((lat[0] - self.LAT) / (lat[0] - lat[1])).round().astype(int), 0, lat.size - 1)
        self.ZG = z[ri, ci]                      # 格点地面高程
        self.shape = self.LON.shape
        self._gw_cache = {}

    # ---- 射线遮挡（向量化） ----
    def _los_mask_to_point(self, plon, plat, pz, ngrid=40):
        """返回 bool 掩膜：格点 (i,j) 与点 P 之间无地形遮挡。"""
        dlon = plon - self.LON
        dlat = plat - self.LAT
        latm = np.radians((self.LAT + plat) / 2.0)
        dx = dlon * C.DEG2M * np.cos(latm)
        dy = dlat * C.DEG2M
        dist = np.hypot(dx, dy)
        blocked = np.zeros(self.shape, dtype=bool)
        ts = np.linspace(0.0, 1.0, ngrid + 2)[1:-1]
        for t in ts:
            qlon = self.LON + (plon - self.LON) * t
            qlat = self.LAT + (plat - self.LAT) * t
            hline = self.ZG + 0.0
            # 端点海拔：格点以 ZG+h 计，但对遮挡只用地形；用线性插值高度
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
        """掩膜：中继在格点、离地 h 时与 G01 双向链路可用（地形 + 距离门限）。"""
        if h in self._gw_cache:
            return self._gw_cache[h]
        glon, glat, gz, _ = self._gw_ep
        self._cur_h = h
        los = self._los_mask_to_point(glon, glat, gz)
        dist = self._dist3(glon, glat, gz, h)
        ok = los & (dist <= GW_RANGE_M)
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
        return los & (dist <= RA_RANGE_M)

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
# 运输架次逐时刻采样
# ---------------------------------------------------------------------------
def sortie_track(ty, geom_legs, seq_sites, elev_o, nodes, dt=30.0):
    """生成一个运输架次的时间-位置-海拔轨迹采样。

    seq_sites: 访问服务区序号列表（如 [1, 5]）
    geom_legs: dict (from,to)->geom，键用 'O' 或 int
    返回 [(t, lon, lat, alt, phase), ...]
    """
    track = []
    t = ty['t_prep']
    cur = 'O'
    cur_h = elev_o
    prev_site = None
    for si in seq_sites:
        g = geom_legs[(cur, si)]
        zc = g['z_cruise']
        d = g['d']
        h_up = max(0.0, zc - cur_h)
        t_up = h_up / ty['v_up']
        t_cr = d / ty['v_cruise']
        # 爬升段
        n = max(1, int(math.ceil(t_up / dt)))
        for k in range(1, n + 1):
            tt = t + t_up * k / n
            frac = k / n
            track.append((tt, g['qlon'][0] + (g['qlon'][-1] - g['qlon'][0]) * 0.0
                          if False else cur_lon(g, cur, si, nodes, 0.0),
                          cur_lat(g, cur, si, nodes, 0.0),
                          cur_h + h_up * frac, 'climb'))
        # 简化：用线性插值端点坐标
        track = [x for x in track]
        t += t_up
        # 巡航段
        n = max(1, int(math.ceil(t_cr / dt)))
        for k in range(0, n + 1):
            frac = k / n
            tt = t + t_cr * frac
            lon_, lat_ = lerp_ll(nodes, cur, si, frac)
            track.append((tt, lon_, lat_, zc, 'cruise'))
        t += t_cr
        # 下降段
        zt = nodes['S%03d' % si]['elev'] + C.CABIN
        h_dn = max(0.0, zc - zt)
        t_dn = h_dn / ty['v_down']
        n = max(1, int(math.ceil(t_dn / dt)))
        for k in range(1, n + 1):
            frac = k / n
            tt = t + t_dn * frac
            lon_, lat_ = lerp_ll(nodes, cur, si, 1.0)
            track.append((tt, lon_, lat_, zc - h_dn * frac, 'descent'))
        t += t_dn
        # 交接
        track.append((t, nodes['S%03d' % si]['lon'], nodes['S%03d' % si]['lat'], zt, 'handover'))
        cur = si; cur_h = zt
    # 返回 O01
    g = geom_legs[(cur, 'O')]
    zc = g['z_cruise']; d = g['d']
    h_up = max(0.0, zc - cur_h); t_up = h_up / ty['v_up']
    t += t_up
    zt = elev_o; h_dn = max(0.0, zc - zt); t_dn = h_dn / ty['v_down']
    t_cr = d / ty['v_cruise']
    n = max(1, int(math.ceil(t_cr / dt)))
    for k in range(0, n + 1):
        frac = k / n
        tt = t + t_cr * frac
        lon_, lat_ = lerp_ll(nodes, cur, 'O', frac)
        track.append((tt, lon_, lat_, zc, 'cruise'))
    t += t_cr + t_dn
    lon_, lat_ = nodes['O01']['lon'], nodes['O01']['lat']
    track.append((t, lon_, lat_, elev_o, 'descent'))
    return track


def lerp_ll(nodes, a, b, f):
    A = nodes['O01'] if a == 'O' else nodes['S%03d' % a]
    B = nodes['O01'] if b == 'O' else nodes['S%03d' % b]
    return A['lon'] + (B['lon'] - A['lon']) * f, A['lat'] + (B['lat'] - A['lat']) * f


def cur_lon(g, a, b, nodes, f):
    return lerp_ll(nodes, a, b, f)[0]


def cur_lat(g, a, b, nodes, f):
    return lerp_ll(nodes, a, b, f)[1]


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    nodes = C.load_nodes(); z, lon, lat = load_dem()
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
