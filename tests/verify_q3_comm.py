# -*- coding: utf-8 -*-
"""Q3 通信保障独立校验（不 import q3_joint 的调度逻辑，只重建轨迹 + 读结果文件）。"""
import os, sys
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code')); sys.path.insert(0, ROOT)
import core as C
from dem_io import load_dem
import q3_joint as Q          # 仅复用"轨迹采样"（几何），不参与判定

TOL = 1.0                      # ±1 s 容差（CSV 时刻保留 1 位小数）

def main():
    sorties, nodes, types = Q.q1_sorties()
    z, lon, lat = load_dem(); gw = C.gateway_endpoint(nodes)
    q3r = pd.read_csv(os.path.join(ROOT, 'out', 'Q3_中继架次.csv'))
    q3c = pd.read_csv(os.path.join(ROOT, 'out', 'Q3_通信保障.csv'))
    start = {sid: g['开始时刻（s）'].min() - types[[s for s in sorties if s['sid'] == sid][0]['type']]['t_prep']
             for sid, g in q3c.groupby('运输架次编号')}
    tot = bad = rel = 0; bl = []
    for s in sorties:
        off = start[s['sid']]
        for (t, lo, la, alt, ph) in Q.sample_sortie(s, nodes, types):
            T = t + off; tot += 1
            if C.link_available(z, lon, lat, (lo, la, alt, 'T'), gw)['avail']:
                continue
            ok = False
            for _, r in q3r.iterrows():
                if not (r['建链完成时刻（s）'] - TOL <= T <= r['服务结束时刻（s）'] + TOL):
                    continue
                a = C.link_available(z, lon, lat, (lo, la, alt, 'T'),
                                     (r['悬停经度（°）'], r['悬停纬度（°）'], r['悬停海拔（m）'], 'RA'))['avail']
                b = C.link_available(z, lon, lat,
                                     (r['悬停经度（°）'], r['悬停纬度（°）'], r['悬停海拔（m）'], 'RB'), gw)['avail']
                if a and b:
                    ok = True; break
            if ok:
                rel += 1
            else:
                bad += 1; bl.append((s['sid'], round(T), round(alt), ph))
    print("采样点 %d | 直连 %d | 中继 %d | 中断 %d" % (tot, tot - rel - bad, rel, bad))
    for x in bl[:10]:
        print("   ", x)
    return 1 if bad else 0

if __name__ == '__main__':
    sys.exit(main())
