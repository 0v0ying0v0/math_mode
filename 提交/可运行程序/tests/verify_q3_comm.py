# -*- coding: utf-8 -*-
"""Q3 通信保障独立校验（不 import q3_joint 的调度逻辑，只重建轨迹 + 读结果文件）。

继承口径（spec/task_spec.md §3 T-3.1）：Q3 的运输层继承 **Q2 的 37 个并行架次**
（``out/Q2_运输架次.csv`` + ``out/Q2_逐箱交付.csv``），**不再**继承 Q1 的 18 个串行
架次；故本脚本按 Q2 结果重建 37 条轨迹。

判定口径：唯一物理判据是 ``core.link_available``（精确口径，与 comm_geo 掩膜逐位一致）。
对每个采样点：
  · 直连可达 ⇒ 对应「通信保障」行必须标注为直连；
  · 直连不可达 ⇒ 对应行必须标注中继，且**该行点名的中继架次**的 [建链完成, 服务结束]
    窗口须覆盖该时刻，其两条链路（运输机→RA、RB→网关）须精确可达。
不符者计入「中断/标注不符」并以非零码退出。
"""
import os, sys
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code')); sys.path.insert(0, ROOT)
import core as C
from dem_io import load_dem
import q3_joint as Q          # 仅复用"轨迹采样"（几何），不参与判定

TOL = 1.0                      # ±1 s 容差（CSV 时刻保留 1 位小数）


def q2_sorties():
    """读取 Q2 结果，重建 37 个并行架次（机型 + 访问服务区顺序 + 箱数）。

    返回 (sorties, nodes, types)；sorties 的键与调用方一致（sid/type），
    另带 sites/nbox 供 ``Q.sample_sortie_rel`` 重建轨迹。
    """
    nodes = C.load_nodes(); types = C.load_transport_types()
    q2 = pd.read_csv(os.path.join(ROOT, 'out', 'Q2_运输架次.csv'))
    q2b = pd.read_csv(os.path.join(ROOT, 'out', 'Q2_逐箱交付.csv'))
    nbox = q2b.groupby('架次编号').size().to_dict()
    out = []
    for _, r in q2.iterrows():
        out.append(dict(
            sid=r['架次编号'], type=r['机型编号'],
            sites=[int(x.strip()[1:]) for x in str(r['访问服务区顺序']).split(';')
                   if x.strip()],
            nbox=int(nbox[r['架次编号']])))
    return out, nodes, types


def main():
    sorties, nodes, types = q2_sorties()
    z, lon, lat = load_dem(); gw = C.gateway_endpoint(nodes)
    q3r = pd.read_csv(os.path.join(ROOT, 'out', 'Q3_中继架次.csv'))
    q3c = pd.read_csv(os.path.join(ROOT, 'out', 'Q3_通信保障.csv'))
    print("继承源：Q2 并行架次 %d 个（机型 %s）"
          % (len(sorties), '/'.join(sorted({s['type'] for s in sorties}))))
    # 架次起点 = 该架次首个通信阶段行的开始时刻 = Q3 联合排程的架次起点 T0
    # （q3_joint 写表时首行 a = T0[jid]，即把起飞前准备时间折进首行，
    #   故轨道样本的相对时刻 t 直接加 T0 即为绝对时刻）
    start = {sid: g['开始时刻（s）'].min()
             for sid, g in q3c.groupby('运输架次编号')}
    # 通信保障：按架次归并阶段行 (开始, 结束, 保障方式, 中继架次编号)
    rows_of = {sid: [(float(r['开始时刻（s）']), float(r['结束时刻（s）']),
                      str(r['保障方式']), str(r['中继架次编号']))
                     for _, r in g.iterrows()]
               for sid, g in q3c.groupby('运输架次编号')}

    def relay_ok(r, T, lo, la, alt):
        """该中继架次在其服务窗口内能否精确连通该样本点。"""
        if not (float(r['建链完成时刻（s）']) - TOL <= T
                <= float(r['服务结束时刻（s）']) + TOL):
            return False
        a = C.link_available(z, lon, lat, (lo, la, alt, 'T'),
                             (r['悬停经度（°）'], r['悬停纬度（°）'],
                              r['悬停海拔（m）'], 'RA'))['avail']
        b = C.link_available(z, lon, lat,
                             (r['悬停经度（°）'], r['悬停纬度（°）'],
                              r['悬停海拔（m）'], 'RB'), gw)['avail']
        return bool(a and b)

    tot = direct = rel = bad = 0; bl = []
    for s in sorties:
        if s['sid'] not in start:                      # Q3 通信保障表漏记该架次
            bad += 1; bl.append((s['sid'], '-', '-', '缺通信保障记录'))
            continue
        off = start[s['sid']]
        trk = Q.dedup_track(Q.sample_sortie_rel(z, lon, lat, nodes, types[s['type']],
                                                s['sites'], s['nbox']))
        for (t, lo, la, alt, ph) in trk:
            T = t + off; tot += 1
            rows = [r for r in rows_of.get(s['sid'], [])
                    if r[0] <= T <= r[1]]               # 覆盖该时刻的阶段行
            if not rows:
                bad += 1; bl.append((s['sid'], round(T), round(alt), ph + ':无阶段记录'))
                continue
            if C.link_available(z, lon, lat, (lo, la, alt, 'T'), gw)['avail']:
                direct += 1
                if not any(m == '直连' for _, _, m, _ in rows):
                    bad += 1
                    bl.append((s['sid'], round(T), round(alt), ph + ':直连可达却标注中继'))
                continue
            ok = False
            for (_, _, m, rid) in rows:
                if m != '中继' or not rid:
                    continue                            # 空中继号 = 未命中任何驻留
                for _, r in q3r[q3r['中继架次编号'] == rid].iterrows():
                    if relay_ok(r, T, lo, la, alt):
                        ok = True; break
                if ok:
                    break
            if ok:
                rel += 1
            else:
                bad += 1; bl.append((s['sid'], round(T), round(alt), ph + ':无中继覆盖'))
    print("采样点 %d | 直连 %d | 中继 %d | 中断 %d" % (tot, direct, rel, bad))
    for x in bl[:10]:
        print("   ", x)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
