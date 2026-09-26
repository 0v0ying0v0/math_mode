# -*- coding: utf-8 -*-
"""问题三覆盖诊断：运输航段有多少比例可获得通信保障？

诊断内容：
  1. 直连可用比例（按阶段：爬升/巡航/下降/交接）
  2. 若不可直连，是否存在中继候选点同时可达 G01 与该运输位置
  3. 需要中继的时间窗分布（用于最小中继架次覆盖）
"""
from __future__ import annotations
import os, sys, math
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                          # noqa: E402
import comm_geo as G                                      # noqa: E402
from dem_io import load_dem, bilinear                     # noqa: E402

REP = os.path.join(ROOT, 'reports'); os.makedirs(REP, exist_ok=True)


def main():
    nodes = C.load_nodes(); types = C.load_transport_types()
    z, lon, lat = load_dem()
    o = nodes['O01']
    gw = C.gateway_endpoint(nodes)

    grid = G.RelayGrid(z, lon, lat, step_deg=0.0015)
    grid.set_gateway(nodes)
    HEIGHTS = (150.0, 200.0, 250.0, 300.0)
    gwm = {h: grid.gw_reachable(h) for h in HEIGHTS}

    # 运输位置可达掩膜缓存（按 1 km 网格粗化，便于快速查询）
    lines = ["# B5 问题三通信覆盖诊断\n"]
    lines.append("## 1. 直连可用性（逐阶段，按 30 s 采样）\n")

    geom = {('O', i): C.leg_geometry(z, lon, lat, o['lon'], o['lat'],
                                     nodes['S%03d' % i]['lon'], nodes['S%03d' % i]['lat'])
            for i in range(1, 16)}
    for i in range(1, 16):
        geom[(i, 'O')] = C.leg_geometry(z, lon, lat, nodes['S%03d' % i]['lon'],
                                        nodes['S%03d' % i]['lat'], o['lon'], o['lat'])

    # 逐服务区单点往返，按阶段统计直连可用率
    rows = []
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        g_out = geom[('O', i)]
        zc = g_out['z_cruise']
        samples = []
        # 去程爬升
        for f in np.linspace(0, 1, 12)[1:]:
            lon_, lat_ = G.lerp_ll(nodes, 'O', i, f * g_out['d'] / (g_out['d'] + 1e-9))
            lon_, lat_ = G.lerp_ll(nodes, 'O', i, f)
            h = o['elev'] + (zc - o['elev']) * f
            samples.append(('climb', lon_, lat_, h))
        for f in np.linspace(0, 1, 12):
            lon_, lat_ = G.lerp_ll(nodes, 'O', i, f)
            samples.append(('cruise', lon_, lat_, zc))
        for f in np.linspace(0, 1, 6)[1:]:
            lon_, lat_ = G.lerp_ll(nodes, 'O', i, f)
            h = zc + ((s['elev'] + 30) - zc) * f
            samples.append(('descent', lon_, lat_, h))
        samples.append(('handover', s['lon'], s['lat'], s['elev'] + 30))
        for ph in ('climb', 'cruise', 'descent', 'handover'):
            sub = [x for x in samples if x[0] == ph]
            nd = sum(1 for _, lo, la, h in sub
                     if not C.link_available(z, lon, lat, (lo, la, h, 'T'), gw)['avail'])
            rows.append(dict(Si='S%03d' % i, phase=ph, n=len(sub), no_direct=nd,
                             pct=round(100 * nd / len(sub), 1)))
    df = pd.DataFrame(rows)
    piv = df.pivot(index='Si', columns='phase', values='pct')
    lines.append("直连不可用比例（%）：\n")
    lines.append("| Si | climb | cruise | descent | handover |")
    lines.append("|---|---|---|---|---|")
    for i in range(1, 16):
        r = piv.loc['S%03d' % i]
        lines.append("| S%03d | %.1f | %.1f | %.1f | %.1f |" %
                     (i, r['climb'], r['cruise'], r['descent'], r['handover']))

    # ---- 中继可达性：对每个服务区作业点，有多少中继候选可同时覆盖 ----
    lines.append("\n## 2. 中继候选可达性（运输机在服务区作业高度，地面+30 m）\n")
    lines.append("| Si | 直连 | 可被中继覆盖的候选点数 | 最小可行离地高度 |")
    lines.append("|---|---|---|---|")
    cov_rows = []
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        pz = s['elev'] + C.CABIN
        direct = C.link_available(z, lon, lat, (s['lon'], s['lat'], pz, 'T'), gw)['avail']
        best = None
        for h in HEIGHTS:
            ra = grid.ra_reachable_mask(s['lon'], s['lat'], pz, h)
            both = ra & gwm[h]
            n = int(both.sum())
            if n > 0 and best is None:
                best = h
            if h == HEIGHTS[-1]:
                nmax = n
        lines.append("| S%03d | %s | %d | %s |" %
                     (i, '是' if direct else '否', nmax,
                      '—' if best is None else '%.0f m' % best))
        cov_rows.append(dict(Si='S%03d' % i, direct=direct, n_relay=nmax, h_min=best))
    cov = pd.DataFrame(cov_rows)

    # ---- 沿航段的覆盖：采样整条去程航段 ----
    lines.append("\n## 3. 沿航段的中继覆盖（去程，按 30 s 等效采样 20 点）\n")
    lines.append("| Si | 直连点数 | 中继可覆盖点数 | 两者皆不可 | 最低可行离地高度 |")
    lines.append("|---|---|---|---|---|")
    seg_rows = []
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        g = geom[('O', i)]
        zc = g['z_cruise']
        pts = []
        for f in np.linspace(0, 1, 20):
            lon_, lat_ = G.lerp_ll(nodes, 'O', i, f)
            h = o['elev'] + (zc - o['elev']) * min(1.0, f * 3) if f < 0.34 else zc
            pts.append((lon_, lat_, h))
        nd = nc = nn = 0
        hmin = None
        for (lo, la, h) in pts:
            if C.link_available(z, lon, lat, (lo, la, h, 'T'), gw)['avail']:
                nd += 1; continue
            found = None
            for hh in HEIGHTS:
                ra = grid.ra_reachable_mask(lo, la, h, hh)
                if (ra & gwm[hh]).any():
                    found = hh; break
            if found is None:
                nn += 1
            else:
                nc += 1
                hmin = found if hmin is None else min(hmin, found)
        lines.append("| S%03d | %d | %d | %d | %s |" %
                     (i, nd, nc, nn, '—' if hmin is None else '%.0f m' % hmin))
        seg_rows.append(dict(Si='S%03d' % i, direct=nd, relay=nc, none=nn, h_min=hmin))
    seg = pd.DataFrame(seg_rows)

    lines.append("\n## 4. 结论\n")
    tot_none = int(seg['none'].sum())
    lines.append("- 15 个服务区的去程航段共采样 %d 点：直连 %d，可中继 %d，**两者皆不可 %d**。"
                 % (15 * 20, int(seg['direct'].sum()), int(seg['relay'].sum()), tot_none))
    if tot_none == 0:
        lines.append("- **单中继架构在几何上可行**：所有运输位置均可由「可达 G01 的高地中继」覆盖。")
    else:
        lines.append("- **存在 %d 个采样点无任何通信手段** → 单中继架构不足，需引入"
                     "「逐段不同中继」或重新设计航段。" % tot_none)
    out = os.path.join(REP, 'B5_comm_diagnosis.md')
    with open(out, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    seg.to_csv(os.path.join(ROOT, 'out', 'Q3_航段覆盖诊断.csv'), index=False, encoding='utf-8-sig')


if __name__ == '__main__':
    main()
