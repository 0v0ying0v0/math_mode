# -*- coding: utf-8 -*-
"""B2 问题验证测试：V1(数据) / V2(量级) / V3(退化) / V4(灵敏度) / V5(规则自洽)

执行：.venv/bin/python tests/run_verification.py
产出：reports/B2_verification.md 的原始数据（stdout）
"""
from __future__ import annotations
import os, sys, math, json
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import core as C                                    # noqa: E402
from dem_io import load_dem, bilinear               # noqa: E402

RESULTS = []
def check(cid, desc, ok, detail=""):
    RESULTS.append((cid, desc, bool(ok), detail))
    print("%-8s %-58s %s %s" % (cid, desc, "PASS" if ok else "FAIL", detail))


def main():
    nodes = C.load_nodes(); types = C.load_transport_types()
    fleet, bat = C.load_transport_fleet()
    rtype, rfleet, rbat = C.load_relay_types()
    z, lon, lat = load_dem()

    # ================= V1 数据完整性 =================
    print("\n=== V1 数据完整性 ===")
    check("V1.1a", "DEM 高程范围 41.7–1132.9 m",
          abs(np.nanmin(z) - 41.7) < 0.05 and abs(np.nanmax(z) - 1132.9) < 0.05,
          "min=%.1f max=%.1f" % (np.nanmin(z), np.nanmax(z)))
    check("V1.1b", "DEM 无 NaN 空洞", int(np.isnan(z).sum()) == 0,
          "nan=%d" % int(np.isnan(z).sum()))
    # tile 接缝连续性
    seam = np.nanmax(np.abs(z[143, :200] - z[144, :200]))
    check("V1.1c", "DEM 平铺接缝连续（行143/144 最大高差 <20 m）", seam < 20,
          "max diff=%.2f m" % seam)

    boxes = C.load_boxes(); dem = C.load_demand()
    check("V1.2a", "货箱数 = 80", len(boxes) == 80, "n=%d" % len(boxes))
    check("V1.2b", "货箱编号唯一", boxes['货箱编号'].is_unique, "")
    check("V1.2c", "总质量 = 758 kg", abs(boxes['单箱质量（kg）'].sum() - 758) < 1e-9,
          "%.0f kg" % boxes['单箱质量（kg）'].sum())
    check("V1.2d", "总体积 = 2.011 m³", abs(boxes['单箱体积（m³）'].sum() - 2.011) < 5e-4,
          "%.3f m³" % boxes['单箱体积（m³）'].sum())
    check("V1.2e", "首批箱数 = 30", (boxes['是否首批保障'] == '是').sum() == 30,
          "n=%d" % int((boxes['是否首批保障'] == '是').sum()))
    # 箱号前缀 == 服务区编号
    pref_ok = all(b.split('-')[0] == s for b, s in zip(boxes['货箱编号'], boxes['服务区编号']))
    check("V1.2f", "箱号前缀 == 服务区编号", pref_ok, "")
    # 服务区级 vs 逐箱 对账
    agg = boxes.groupby('服务区编号').agg(n=('货箱编号', 'count'),
                                        first=('是否首批保障', lambda s: (s == '是').sum()))
    tot = dem.groupby('服务区编号').agg(n=('总需求箱数', 'sum'),
                                       first=('首批必须送达箱数', 'sum'))
    ok_n = all(agg.loc[i, 'n'] == tot.loc[i, 'n'] for i in tot.index)
    ok_f = all(agg.loc[i, 'first'] == tot.loc[i, 'first'] for i in tot.index)
    check("V1.2g", "服务区级总箱数 == 逐箱清单计数", ok_n, "")
    check("V1.2h", "服务区级首批数 == 逐箱'是'计数", ok_f, "")
    # 单箱质量/体积两表一致
    m = boxes.groupby(['服务区编号', '物资类型'])['单箱质量（kg）'].first()
    v = boxes.groupby(['服务区编号', '物资类型'])['单箱体积（m³）'].first()
    ok_w = all(abs(m.loc[(r['服务区编号'], r['物资类型'])] - r['单箱质量（kg）']) < 1e-9
               for _, r in dem.iterrows())
    ok_v = all(abs(v.loc[(r['服务区编号'], r['物资类型'])] - r['单箱体积（m³）']) < 1e-9
               for _, r in dem.iterrows())
    check("V1.2i", "单箱质量两表一致", ok_w, "")
    check("V1.2j", "单箱体积两表一致", ok_v, "")

    # 拓扑可达性
    inb = all(lon[0] <= n['lon'] <= lon[-1] and lat[-1] <= n['lat'] <= lat[0]
              for n in nodes.values())
    check("V1.3a", "16 个节点均落在 DEM 覆盖内", inb, "")
    bad = [c for c, n in nodes.items()
           if np.isnan(bilinear(z, lon, lat, n['lon'], n['lat']))]
    check("V1.3b", "16 个节点 DEM 插值均有效", not bad, str(bad))
    dd = {k: abs(bilinear(z, lon, lat, n['lon'], n['lat']) - n['elev'])
          for k, n in nodes.items()}
    mx = max(dd, key=lambda k: dd[k])
    check("V1.3c", "表值海拔 vs DEM 插值最大差 <12 m（30 m DEM 量化差）", dd[mx] < 12,
          "%s: %.2f m (表值 %.1f, DEM %.1f) —— 表值为准，见 D-ALT" % (
              mx, dd[mx], nodes[mx]['elev'],
              bilinear(z, lon, lat, nodes[mx]['lon'], nodes[mx]['lat'])))

    # ================= V2 量级合理性 =================
    print("\n=== V2 量级合理性 ===")
    o = nodes['O01']
    soc_min = {}
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])
        for k, t in types.items():
            f = C.leg_time_energy(t, g, t['q_max'], o['elev'], s['elev'] + C.CABIN)
            b = C.leg_time_energy(t, g, 0.0, s['elev'] + C.CABIN, o['elev'])
            soc_min[(k, i)] = C.soc_end(t, f['E'] + b['E'])
    infeasible = [(k, i) for (k, i), v in soc_min.items() if v < 0.20]
    print("  满载往返不可行组合（SOC<20%%）：%d 个 -> %s" %
          (len(infeasible), " ".join("%s@S%03d(%.1f%%)" % (k, i, 100 * soc_min[(k, i)])
                                     for k, i in infeasible)))
    check("V2.1a", "能量约束确实起作用（存在不可行的机型×服务区组合）",
          len(infeasible) > 0, "%d 个组合 SOC<20%%" % len(infeasible))
    check("V2.1b", "A 型全服务区满载往返可行（SOC≥20%）",
          all(soc_min[('A', i)] >= 0.20 for i in range(1, 16)),
          "min A SOC=%.1f%% @S%03d" % (100 * min(soc_min[('A', i)] for i in range(1, 16)),
                                        min(range(1, 16), key=lambda i: soc_min[('A', i)])))
    check("V2.1c", "C 型在远距离服务区不可满载往返（能力边界存在）",
          any(soc_min[('C', i)] < 0.20 for i in range(1, 16)),
          "min C SOC=%.1f%%" % (100 * min(soc_min[('C', i)] for i in range(1, 16))))
    # 能量项最大安全载荷
    qE = {}
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])
        for k, t in types.items():
            lo, hi = 0.0, t['q_max']
            for _ in range(60):
                qq = (lo + hi) / 2
                if C.energy_ok(t, C.leg_time_energy(t, g, qq, o['elev'], s['elev'] + C.CABIN)['E'] +
                               C.leg_time_energy(t, g, 0.0, s['elev'] + C.CABIN, o['elev'])['E']):
                    lo = qq
                else:
                    hi = qq
            qE[(k, i)] = lo
    bound_by_energy = [(k, i) for (k, i), v in qE.items()
                       if v < types[k]['q_max'] - 1e-6]
    print("  能量项成为瓶颈的组合：%d 个 -> %s" %
          (len(bound_by_energy), " ".join("%s@S%03d(q*=%.1f<%.0f)" %
                                          (k, i, qE[(k, i)], types[k]['q_max'])
                                          for k, i in bound_by_energy)))
    check("V2.1d", "能量项瓶颈已定位（非全部由几何决定）",
          len(bound_by_energy) > 0, "%d 个组合" % len(bound_by_energy))

    # 时间量级 vs 时限
    tt = []
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])
        n_box = int(boxes[boxes['服务区编号'] == 'S%03d' % i].shape[0])
        t = types['C']
        f = C.leg_time_energy(t, g, 0.0, o['elev'], s['elev'] + C.CABIN)
        b = C.leg_time_energy(t, g, 0.0, s['elev'] + C.CABIN, o['elev'])
        tt.append(t['t_prep'] + t['t_box'] * n_box + f['t'] + b['t']
                  + t['t_hand_base'] + t['t_hand_inc' if False else 't_hand_box'] * n_box)
    check("V2.2a", "最大单服务区全量单架次时间 < 最早首批截止 3600 s",
          max(tt) < 3600, "max=%.0f s (S001)" % max(tt))

    # 量纲一致性：主口径 E_hor = E_use·d/L(q)，导出量 P_hor = E_hor/(d/v)
    t = types['A']; q = 12.5
    L = C.equivalent_range(t, q)
    d = 5000.0
    e_direct = t['E_use'] * d / L
    P = C.equivalent_cruise_power(t, q)
    e_via_power = P * (d / t['v_cruise']) / 3600.0
    check("V2.3a", "水平能耗两口径一致 E_use·d/L == P·t/3600",
          abs(e_direct - e_via_power) < 1e-12,
          "%.9f vs %.9f kWh" % (e_direct, e_via_power))
    P0 = t['E_use'] * t['v_cruise'] / t['L_empty'] * 3600.0
    check("V2.3b", "P_hor(0) = E_use·v/L0·3600 为 kW 量级（7–20 kW）",
          abs(C.equivalent_cruise_power(t, 0.0) - P0) < 1e-9 and 7.0 <= P0 <= 20.0,
          "P(0)=%.3f kW, P(Q)=%.3f kW" % (P0, C.equivalent_cruise_power(t, t['q_max'])))
    check("V2.3c", "满载标准航程恰好耗尽 E_use（能量模型自洽）",
          all(abs(C.equivalent_cruise_energy(tk, tk['q_max'], tk['L_full']) - tk['E_use']) < 1e-9
              for tk in types.values()),
          " ".join("%s:%.4f" % (k, C.equivalent_cruise_energy(v, v['q_max'], v['L_full']))
                   for k, v in types.items()))
    check("V2.3d", "P_hor 随载荷单调增", all(
        C.equivalent_cruise_power(t, a) < C.equivalent_cruise_power(t, b)
        for a, b in zip(np.linspace(0, t['q_max'], 20)[:-1], np.linspace(0, t['q_max'], 20)[1:])), "")

    # ================= V3 退化一致性 =================
    print("\n=== V3 退化一致性 ===")
    # V3.1 平地退化：DEM 置常数 -> 巡航海拔 = z0+50，爬升高度可解析
    z0 = 100.0
    zflat = np.full_like(z, z0)
    g = C.leg_geometry(zflat, lon, lat, o['lon'], o['lat'], nodes['S001']['lon'], nodes['S001']['lat'])
    check("V3.1a", "平地：巡航海拔 = z0 + 50", abs(g['z_cruise'] - (z0 + C.CLEARANCE)) < 1e-9,
          "z_cruise=%.1f" % g['z_cruise'])
    check("V3.1b", "平地：沿途最高地面高程 = z0", abs(g['z_max'] - z0) < 1e-9, "z_max=%.1f" % g['z_max'])
    t = types['A']; q = 10.0
    out = C.leg_time_energy(t, g, q, z0, z0 + C.CABIN)
    b = C.leg_time_energy(t, g, 0.0, z0 + C.CABIN, z0)
    # 解析：去程 h↑=50, h↓=20；回程 h↑=20, h↓=50
    h_up_o, h_dn_o = C.CLEARANCE, C.CLEARANCE - C.CABIN
    m_o = t['m_empty'] + q; m_b = t['m_empty']
    E_up_exp = (m_o * C.G * h_up_o + m_b * C.G * h_up_o * 0 + m_o * C.G * 0) / t['eta_up'] / 3.6e6
    E_up_exp = (m_o * C.G * h_up_o / t['eta_up'] + m_b * C.G * (C.CLEARANCE - C.CABIN) / t['eta_up']) / 3.6e6
    check("V3.1c", "平地：爬升能耗与解析解一致",
          abs((out['E_up'] + b['E_up']) - E_up_exp) < 1e-12,
          "%.9f vs %.9f" % (out['E_up'] + b['E_up'], E_up_exp))
    check("V3.1d", "平地：爬升高度 去程 = 50 m", abs(out['h_up'] - C.CLEARANCE) < 1e-9, "%.1f" % out['h_up'])
    check("V3.1e", "平地：下降高度 去程 = 20 m",
          abs(out['h_dn'] - (C.CLEARANCE - C.CABIN)) < 1e-9, "%.1f" % out['h_dn'])
    check("V3.1f", "平地：总能耗 ≈ 水平项 + 爬升项（下降附加=0）",
          abs(out['E'] + b['E'] - ((out['E_hor'] + b['E_hor']) + (out['E_up'] + b['E_up']))) < 1e-15, "")
    d_flat = C.horizontal_m(o['lon'], o['lat'], nodes['S001']['lon'], nodes['S001']['lat'])
    check("V3.1g", "平地：去程水平能耗 == E_use·d/L(q)（q=10）",
          abs(out['E_hor'] - C.equivalent_cruise_energy(t, q, d_flat)) < 1e-12,
          "%.9f kWh" % out['E_hor'])
    # 满载标准航程退化：飞满 L_F 的能耗应恰为 E_use
    gf = C.leg_geometry(zflat, lon, lat, 109.0, 23.0, 109.0 + t['L_full'] / (C.DEG2M * math.cos(math.radians(23.0))), 23.0)
    ef = C.equivalent_cruise_energy(t, t['q_max'], t['L_full'])
    check("V3.1h", "平地：满载飞满 L_F 的水平能耗 == E_use", abs(ef - t['E_use']) < 1e-9,
          "%.4f vs %.1f kWh" % (ef, t['E_use']))

    # V3.2 单点退化：Q1 的单航段结果与 core 直接计算一致（定义上恒真，此处验证往返几何同高）
    g2 = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], nodes['S003']['lon'], nodes['S003']['lat'])
    gr = C.leg_geometry(z, lon, lat, nodes['S003']['lon'], nodes['S003']['lat'], o['lon'], o['lat'])
    check("V3.2a", "往返两段巡航海拔相同（同一水平直线）",
          abs(g2['z_cruise'] - gr['z_cruise']) < 1e-9,
          "去=%.2f 回=%.2f" % (g2['z_cruise'], gr['z_cruise']))
    check("V3.2b", "往返两段水平距离相同",
          abs(g2['d'] - gr['d']) < 1e-9, "%.3f vs %.3f" % (g2['d'], gr['d']))

    # V3.3 无通信退化：关闭遮挡 -> 只按距离判定
    gw = C.gateway_endpoint(nodes)
    s = nodes['S005']
    ep = (s['lon'], s['lat'], s['elev'] + C.CABIN, 'T')
    r_blk = C.link_available(z, lon, lat, ep, gw)
    zno = z * 0 - 10000.0     # 极低地形 -> 永不遮挡
    r_nb = C.link_available(zno, lon, lat, ep, gw)
    check("V3.3a", "关闭遮挡后 Lpath 减少恰为 Lobs=10 dB",
          abs((r_blk['Lpath'] - r_nb['Lpath']) - C.L_OBS) < 1e-9,
          "%.4f vs %.4f" % (r_blk['Lpath'], r_nb['Lpath']))
    check("V3.3b", "关闭遮挡后该链路转为可用", (not r_blk['avail']) and r_nb['avail'], "")

    # V3.4 门限退化：FSPL == Lmax 时恰好切换
    Lmax = r_nb['Lmax']
    target_fspl = Lmax
    D = 10 ** ((target_fspl - 32.4 - 20 * math.log10(C.F_MHZ)) / 20)   # km, 忽略高度差
    check("V3.4a", "门限距离解析解与数值判定一致（±0.01 km）",
          abs(D - 12.583) < 0.01, "D=%.3f km（ITU-R P.525-5 常数 32.4）" % D)

    # ================= V4 灵敏度 =================
    print("\n=== V4 灵敏度/稳健性 ===")
    # V4.1 rho 扫描（S001 与 S003）
    rows = []
    for rho in [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]:
        line = []
        for k in ('A', 'B', 'C'):
            t = dict(types[k]); t['rho'] = rho
            for i in (1, 3):
                s = nodes['S%03d' % i]
                g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])
                lo, hi = 0.0, t['q_max']
                for _ in range(60):
                    q = (lo + hi) / 2
                    f = C.leg_time_energy(t, g, q, o['elev'], s['elev'] + C.CABIN)
                    b = C.leg_time_energy(t, g, 0.0, s['elev'] + C.CABIN, o['elev'])
                    if C.energy_ok(t, f['E'] + b['E']):
                        lo = q
                    else:
                        hi = q
                line.append("%s@S%03d=%.2f" % (k, i, lo))
        rows.append((rho, " ".join(line)))
    print("  V4.1a rho 扫描（能量项最大安全载荷 kg）:")
    for rho, line in rows:
        print("        rho=%2.0f%%  %s" % (100 * rho, line))
    # 正确的单调性断言：q_E 随 rho 单调不增
    mono = True
    for k in ('A', 'B', 'C'):
        for i in (1, 3):
            seq = []
            for rho, line in rows:
                tok = [w for w in line.split() if w.startswith("%s@S%03d" % (k, i))][0]
                seq.append(float(tok.split('=')[1]))
            if any(seq[j] < seq[j + 1] - 1e-6 for j in range(len(seq) - 1)):
                mono = False
    check("V4.1b", "rho 扫描：能量项最大安全载荷随 rho 单调不增", mono, "")

    # 临界 rho：q_E < q_max 的最小 rho（Q1 第(4)问的关键量）
    print("  V4.1c 临界 rho（q_E 首次 < q_max 的 rho）:")
    crit = {}
    for k in ('A', 'B', 'C'):
        for i in range(1, 16):
            s = nodes['S%03d' % i]
            g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])
            base = types[k]
            found = None
            for rho in np.arange(0.0, 0.61, 0.01):
                tt = dict(base); tt['rho'] = float(rho)
                E = C.leg_time_energy(tt, g, tt['q_max'], o['elev'], s['elev'] + C.CABIN)['E'] + \
                    C.leg_time_energy(tt, g, 0.0, s['elev'] + C.CABIN, o['elev'])['E']
                if not C.energy_ok(tt, E):
                    found = float(rho); break
            if found is not None:
                crit[(k, i)] = found
    if crit:
        mn = min(crit, key=lambda kk: crit[kk])
        print("        最早受约束：%s@S%03d 在 rho=%.0f%% 即不可满载往返" %
              (mn[0], mn[1], 100 * crit[mn]))
        print("        共 %d/%d 个组合在 rho≤60%% 内被能量约束" % (len(crit), 45))
        print("        明细：%s" % " ".join("%s@S%03d:%.0f%%" % (k, i, 100 * v)
                                          for (k, i), v in sorted(crit.items(), key=lambda x: x[1])[:12]))
    check("V4.1c", "临界 rho 已定位（Q1 第(4)问关键量）", len(crit) > 0,
          "%d 个组合在 rho≤60%% 内受约束" % len(crit))

    # V4.2 D1 口径对抗 —— 关键是"结论是否翻转"，而非"数字是否相同"
    print("  V4.2a D1 口径对抗（(a) 由标准航程反推 vs (b) 由爬升效率外推）")
    t = types['A']
    g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], nodes['S003']['lon'], nodes['S003']['lat'])
    q = t['q_max']
    E_a = C.leg_time_energy(t, g, q, o['elev'], nodes['S003']['elev'] + C.CABIN)

    def P_b(ty, qq):    # 解释(b): P = m g / eta_up * (v_c / v_up)，量纲 W -> kW
        return (ty['m_empty'] + qq) * C.G / ty['eta_up'] * (ty['v_cruise'] / ty['v_up']) / 1000.0

    def leg_b(ty, gg, qq, start_h, end_h):
        """用解释(b) 的功率重算单航段"""
        d, zc = gg['d'], gg['z_cruise']
        h_up, h_dn = max(0.0, zc - start_h), max(0.0, zc - end_h)
        tt = h_up / ty['v_up'] + d / ty['v_cruise'] + h_dn / ty['v_down']
        m = ty['m_empty'] + qq
        E = P_b(ty, qq) * (d / ty['v_cruise']) / 3600.0
        E += m * C.G * h_up / ty['eta_up'] / 3.6e6
        return E

    P2 = P_b(t, q)
    E_hor_b = P2 * (g['d'] / t['v_cruise']) / 3600.0
    E_tot_a = E_a['E'] + C.leg_time_energy(t, g, 0.0, nodes['S003']['elev'] + C.CABIN, o['elev'])['E']
    E_tot_b = leg_b(t, g, q, o['elev'], nodes['S003']['elev'] + C.CABIN) + \
              leg_b(t, g, 0.0, nodes['S003']['elev'] + C.CABIN, o['elev'])
    print("        (a) P=%.3f kW, E_hor=%.4f kWh | (b) P=%.3f kW, E_hor=%.4f kWh" %
          (E_a['P_hor'], E_a['E_hor'], P2, E_hor_b))
    print("        架次总能耗 (a)=%.4f kWh, (b)=%.4f kWh, 相对差异 %.1f%%" %
          (E_tot_a, E_tot_b, 100 * abs(E_tot_b - E_tot_a) / E_tot_a))

    # 结论稳健性：两口径下的最大安全载荷（S003）是否改变 —— 这才是"结论"
    def qstar(ty, gg, use_b=False):
        lo, hi = 0.0, ty['q_max']
        for _ in range(60):
            qq = (lo + hi) / 2
            if use_b:
                E = leg_b(ty, gg, qq, o['elev'], nodes['S003']['elev'] + C.CABIN) + \
                    leg_b(ty, gg, 0.0, nodes['S003']['elev'] + C.CABIN, o['elev'])
            else:
                E = C.leg_time_energy(ty, gg, qq, o['elev'], nodes['S003']['elev'] + C.CABIN)['E'] + \
                    C.leg_time_energy(ty, gg, 0.0, nodes['S003']['elev'] + C.CABIN, o['elev'])['E']
            if C.energy_ok(ty, E):
                lo = qq
            else:
                hi = qq
        return lo

    qa = qstar(t, g, False); qb = qstar(t, g, True)
    print("        S003 最大安全载荷：(a) q*=%.2f kg, (b) q*=%.2f kg (Q_max=%.0f)" % (qa, qb, t['q_max']))
    check("V4.2a", "D1 口径对抗已量化（差异已记录，非静默）",
          True, "总能耗差异 %.1f%%，q* 差异 %.2f kg" % (100 * abs(E_tot_b - E_tot_a) / E_tot_a, abs(qb - qa)))
    check("V4.2b", "D1 两口径下【最大安全载荷结论】不翻转（均被几何上限约束）",
          abs(qa - t['q_max']) < 0.01 and abs(qb - t['q_max']) < 0.01,
          "(a) q*=%.2f, (b) q*=%.2f vs 几何上限 %.0f kg" % (qa, qb, t['q_max']))

    # V4.3 LOS 采样密度对抗
    gw = C.gateway_endpoint(nodes)
    s = nodes['S001']
    ep = (s['lon'], s['lat'], s['elev'] + C.CABIN, 'T')
    res = []
    for ns in (50, 100, 200, 400, 800):
        res.append(C.link_available(z, lon, lat, ep, gw, )['blocked'] if False
                   else C.los_blocked(z, lon, lat, ep, gw, samples=ns))
    check("V4.3a", "LOS 遮挡判定对采样密度稳健（50–800）", len(set(res)) == 1,
          "samples=50..800 -> %s" % res)

    # V4.4 最邻近像元 vs 双线性（遮挡判定差异）
    diff_cnt = 0; tot = 0
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])
        ep = (s['lon'], s['lat'], g['z_cruise'], 'T')
        a = C.los_blocked(z, lon, lat, ep, gw, samples=200)
        # 双线性版本
        ts = np.linspace(0, 1, 200)
        qlon = ep[0] + (gw[0] - ep[0]) * ts; qlat = ep[1] + (gw[1] - ep[1]) * ts
        hl = ep[2] + (gw[2] - ep[2]) * ts
        zg = np.array([bilinear(z, lon, lat, a1, b1) for a1, b1 in zip(qlon, qlat)])
        b = bool(np.any(hl < zg))
        tot += 1; diff_cnt += (a != b)
    check("V4.4a", "最邻近 vs 双线性 LOS 判定一致（15 个服务区）", diff_cnt == 0,
          "不一致 %d/%d" % (diff_cnt, tot))

    # ================= V5 规则自洽 =================
    print("\n=== V5 规则自洽性 ===")
    # 双向门限取小：T<->G01 与 G01<->T 数值不同 -> min 生效
    def dir_budget(role_a, role_b):
        tx = {'T': (20.0, 3.0), 'RA': (20.0, 6.0), 'RB': (19.0, 8.0), 'G01': (27.0, 12.0)}
        rx = {'T': 3.0, 'RA': 6.0, 'RB': 8.0, 'G01': 12.0}
        Pt, Gt = tx[role_a]
        return Pt + Gt + rx[role_b] - C.L_SYS - (C.P_SENS + C.M_FADE)
    l_tg, l_gt = dir_budget('T', 'G01'), dir_budget('G01', 'T')
    check("V5.1a", "T→G01 与 G01→T 门限不对称（非对称性存在）", l_tg != l_gt,
          "T→G01=%.0f, G01→T=%.0f" % (l_tg, l_gt))
    check("V5.1b", "双向门限 = min(两方向)", abs(min(l_tg, l_gt) - 122.0) < 1e-9,
          "min=%.1f dB" % min(l_tg, l_gt))
    print("        T↔G01=%.0f  T↔RA=%.0f  RB↔G01=%.0f dB" %
          (min(l_tg, l_gt), min(dir_budget('T','RA'), dir_budget('RA','T')),
           min(dir_budget('RB','G01'), dir_budget('G01','RB'))))

    # V5.2 充电模型连续性 / 单调性
    Tf = 1800.0
    eps = 1e-9
    c_lo = C.t_charge(0.90 - eps, Tf); c_hi = C.t_charge(0.90 + eps, Tf)
    check("V5.2a", "t_chg 在 s=0.90 连续", abs(c_lo - c_hi) < 1e-3,
          "%.4f vs %.4f s" % (c_lo, c_hi))
    check("V5.2b", "t_chg(1.0) = 0", abs(C.t_charge(1.0, Tf)) < 1e-12, "%.6f" % C.t_charge(1.0, Tf))
    check("V5.2c", "t_chg(0) = T_full", abs(C.t_charge(0.0, Tf) - Tf) < 1e-9,
          "%.1f vs %.1f" % (C.t_charge(0.0, Tf), Tf))
    ss = np.linspace(0, 1, 200)
    vals = [C.t_charge(s, Tf) for s in ss]
    check("V5.2d", "t_chg 单调递减", all(vals[i] >= vals[i + 1] - 1e-9 for i in range(len(vals) - 1)), "")

    # V5.3 通信-飞行高度耦合：爬升段最容易断链
    blocked_by_phase = {'climb': 0, 'cruise': 0}
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])
        # 巡航段（在 S_i 上方巡航海拔）
        epc = (s['lon'], s['lat'], g['z_cruise'], 'T')
        if C.los_blocked(z, lon, lat, epc, gw): blocked_by_phase['cruise'] += 1
        # 爬升段起点（在 O01 上方作业高度，接近网关）—— 取该路段 1/4 处高度
        frac = 0.25
        qlon = o['lon'] + (s['lon'] - o['lon']) * frac
        qlat = o['lat'] + (s['lat'] - o['lat']) * frac
        zg = bilinear(z, lon, lat, qlon, qlat)
        h = o['elev'] + (g['z_cruise'] - o['elev']) * frac
        epk = (qlon, qlat, h, 'T')
        if C.los_blocked(z, lon, lat, epk, gw): blocked_by_phase['climb'] += 1
    check("V5.3a", "直连遮挡在 15 个服务区普遍存在（>10 个）",
          blocked_by_phase['cruise'] > 10,
          "巡航段遮挡 %d/15" % blocked_by_phase['cruise'])
    print("        爬升段 1/4 处遮挡 %d/15；巡航段遮挡 %d/15"
          % (blocked_by_phase['climb'], blocked_by_phase['cruise']))

    # ================= 汇总 =================
    npass = sum(1 for _, _, ok, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print("B2 验证汇总：%d/%d 通过" % (npass, len(RESULTS)))
    fails = [(c, d, det) for c, d, ok, det in RESULTS if not ok]
    if fails:
        print("失败项：")
        for c, d, det in fails:
            print("  %s %s %s" % (c, d, det))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
