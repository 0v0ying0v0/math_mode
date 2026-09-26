# -*- coding: utf-8 -*-
"""B10 出图：路线 / 时序 / 资源占用 / 通信保障 / 分区对比。

输出 paper/figs/*.png（150 dpi）。
"""
from __future__ import annotations
import os, sys, json
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'code'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle
from matplotlib.lines import Line2D
import core as C                                     # noqa: E402
from dem_io import load_dem                          # noqa: E402

OUT = os.path.join(ROOT, 'out')
FIG = os.path.join(ROOT, 'paper', 'figs')
os.makedirs(FIG, exist_ok=True)

plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'Hiragino Sans GB', 'Heiti TC']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 150
plt.rcParams['savefig.bbox'] = 'tight'


def dem_background(ax, z, lon, lat, step=3):
    zz = z[::step, ::step]
    la = lat[::step]; lo = lon[::step]
    extent = [lo[0], lo[-1], la[-1], la[0]]
    gy, gx = np.gradient(zz)
    hs = np.hypot(gx, gy)
    ax.imshow(np.log1p(hs), extent=extent, origin='upper', cmap='Greys',
              alpha=0.55, aspect='auto')
    cs = ax.contour(lo, la, zz, levels=12, colors='#8a8a8a', linewidths=0.35, alpha=0.6)
    ax.clabel(cs, inline=True, fontsize=5, fmt='%.0f')
    return extent


# ---------------------------------------------------------------------------
def fig1_routes(nodes, types, z, lon, lat):
    q1 = pd.read_csv(os.path.join(OUT, 'Q1_单点组批.csv'))
    fig, ax = plt.subplots(figsize=(9, 7))
    dem_background(ax, z, lon, lat)
    o = nodes['O01']
    cmap = {'A': '#1f77b4', 'B': '#2ca02c', 'C': '#d62728'}
    for _, r in q1.iterrows():
        i = int(r['服务区编号'][1:]); s = nodes['S%03d' % i]
        ax.plot([o['lon'], s['lon']], [o['lat'], s['lat']],
                color=cmap[r['机型编号']], lw=0.9, alpha=0.65)
    ax.plot(o['lon'], o['lat'], marker='*', ms=18, color='black', zorder=5)
    # 标签排布：按角度向外分散，避免重叠
    # 先画所有站点，再用像素空间做标签避让（比数据坐标阈值可靠）
    for i in range(1, 16):
        n = nodes['S%03d' % i]
        ax.plot(n['lon'], n['lat'], marker='o', ms=6, color='#ff7f0e', zorder=5)
    fig.canvas.draw()
    def to_px(lon_, lat_):
        return ax.transData.transform((lon_, lat_))
    placed_px = [to_px(o['lon'], o['lat'])]
    for i in range(1, 16):
        n = nodes['S%03d' % i]
        ang = np.arctan2(n['lat'] - o['lat'],
                         (n['lon'] - o['lon']) * np.cos(np.radians(o['lat'])))
        txt = 'S%03d %.0fm' % (i, n['elev'])
        wpx = 7.6 * len(txt) + 6      # 经验字宽（px）
        hpx = 13.5
        tx, ty = n['lon'], n['lat']
        found = False
        for rad_px in np.arange(16, 190, 7):
            for dth in (0.0, 0.30, -0.30, 0.60, -0.60, 0.95, -0.95, 1.35, -1.35, 1.8, -1.8):
                a_ = ang + dth
                base = to_px(n['lon'], n['lat'])
                cx = base[0] + rad_px * np.cos(a_)
                cy = base[1] + rad_px * np.sin(a_)
                # 标签中心到锚点：左对齐时右移半宽
                if cx >= base[0]:
                    box = (cx - 2, cx + wpx, cy - hpx / 2, cy + hpx / 2)
                else:
                    box = (cx - wpx, cx + 2, cy - hpx / 2, cy + hpx / 2)
                ok = True
                for (px, py) in placed_px:
                    if not (box[1] < px - 2 or box[0] > px + 2 or
                            box[3] < py - 2 or box[2] > py + 2):
                        ok = False; break
                if ok:
                    # 记录该标签的像素框（用四角近似）
                    placed_px.append(((box[0] + box[1]) / 2, (box[2] + box[3]) / 2))
                    tx = ax.transData.inverted().transform((cx, cy))[0]
                    ty = ax.transData.inverted().transform((cx, cy))[1]
                    found = True
                    break
            if found:
                break
        if not found:
            tx, ty = n['lon'], n['lat'] + 0.006
        ax.annotate('S%03d %.0fm' % (i, n['elev']), (n['lon'], n['lat']),
                    xytext=(tx, ty), textcoords='data', fontsize=6.8,
                    ha='left' if tx >= n['lon'] else 'right', va='center',
                    arrowprops=dict(arrowstyle='-', lw=0.4, color='#555555'),
                    bbox=dict(boxstyle='round,pad=0.12', fc='white', ec='none', alpha=0.8),
                    zorder=8)
    # 需中继的站点标记
    q3c = pd.read_csv(os.path.join(OUT, 'Q3_通信保障.csv'))
    relay_sites = set()
    for _, r in q3c[q3c['保障方式'] == '中继'].iterrows():
        row = q1[q1['架次编号'] == r['运输架次编号']]
        if len(row):
            relay_sites.add(row.iloc[0]['服务区编号'])
    for s in relay_sites:
        i = int(s[1:]); n = nodes[s]
        ax.add_patch(Circle((n['lon'], n['lat']), 0.004, fill=False,
                            ec='red', lw=1.2, ls='--', zorder=4))
    handles = [Line2D([], [], color=cmap[k], lw=1.6, label='%s型机' % k) for k in 'ABC']
    handles += [Line2D([], [], color='red', lw=1.2, ls='--', label='需中继保障'),
                Line2D([], [], marker='*', color='black', ls='', ms=12, label='O01')]
    ax.legend(handles=handles, loc='lower left', fontsize=8, framealpha=0.9)
    ax.set_xlabel('经度 (°)'); ax.set_ylabel('纬度 (°)')
    ax.set_title('图1  问题一/三 运输路线与通信需求（Q1 共 %d 架次，%d 个站点需中继）'
                 % (len(q1), len(relay_sites)), fontsize=11)
    fig.savefig(os.path.join(FIG, 'fig1_routes.png'))
    plt.close(fig)
    return len(relay_sites)


def fig2_gantt(nodes, z, lon, lat):
    q2 = pd.read_csv(os.path.join(OUT, 'Q2_运输架次.csv'))
    q2b = pd.read_csv(os.path.join(OUT, 'Q2_逐箱交付.csv'))
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(11, 8),
                                  gridspec_kw=dict(height_ratios=[2.1, 1]))
    cmap = {'A': '#1f77b4', 'B': '#2ca02c', 'C': '#d62728'}
    uavs = sorted(q2['无人机编号'].unique())
    ypos = {u: k for k, u in enumerate(uavs)}
    for _, r in q2.iterrows():
        y = ypos[r['无人机编号']]
        ax.barh(y, r['返回O01时刻（s）'] - r['开始时刻（s）'], left=r['开始时刻（s）'],
                height=0.6, color=cmap[r['机型编号']], alpha=0.85,
                edgecolor='black', lw=0.3)
    ax.axvline(3600, color='red', ls='--', lw=1.2, label='首批截止 3600 s（部分站点）')
    ax.set_yticks(list(ypos.values())); ax.set_yticklabels(list(ypos.keys()))
    ax.set_xlabel('时间 (s)'); ax.set_ylabel('无人机')
    ax.set_title('图2a  问题二 运输架次甘特图（makespan = %.0f s）'
                 % q2['返回O01时刻（s）'].max(), fontsize=11)
    ax.legend(fontsize=8, loc='lower right')
    ax.grid(axis='x', alpha=0.3)
    # 逐箱交付时刻
    t = q2b['交付完成时刻（s）'].values
    ax2.hist(t, bins=40, color='#1f77b4', alpha=0.8, edgecolor='white', lw=0.4)
    ax2.axvline(3600, color='red', ls='--', lw=1.2)
    ax2.set_xlabel('交付完成时刻 (s)'); ax2.set_ylabel('货箱数')
    ax2.set_title('图2b  逐箱交付时刻分布（80 箱，最早 %.0f s / 最晚 %.0f s）'
                  % (t.min(), t.max()), fontsize=11)
    ax2.grid(axis='y', alpha=0.3)
    fig.savefig(os.path.join(FIG, 'fig2_gantt.png'))
    plt.close(fig)


def fig3_resources():
    q1 = pd.read_csv(os.path.join(OUT, 'Q1_单点组批.csv'))
    q4 = pd.read_csv(os.path.join(OUT, 'Q4_分区配置.csv'))
    q4s = pd.read_csv(os.path.join(OUT, 'Q4_方案比较.csv'))
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    # (a) Q1 机型使用
    ax = axes[0]
    vc = q1['机型编号'].value_counts().reindex(['A', 'B', 'C']).fillna(0)
    ax.bar(vc.index, vc.values, color=['#1f77b4', '#2ca02c', '#d62728'])
    for k, v in zip(vc.index, vc.values):
        ax.text(k, v + 0.2, '%d' % v, ha='center', fontsize=9)
    ax.set_title('(a) 问题一 机型架次分布\n（共 %d 架次）' % len(q1), fontsize=10)
    ax.set_ylabel('架次数'); ax.grid(axis='y', alpha=0.3)
    # (b) Q4 各方案资源
    ax = axes[1]
    labels = []
    need_t, need_r = [], []
    for K in (2, 3):
        s = q4s[q4s['K'] == K].iloc[0]
        labels.append('K=%d' % K)
        need_t.append(s['A型运输机'] + s['B型运输机'] + s['C型运输机'])
        need_r.append(s['中继无人机'])
    x = np.arange(len(labels)); w = 0.35
    ax.bar(x - w / 2, need_t, w, label='运输无人机', color='#1f77b4')
    ax.bar(x + w / 2, need_r, w, label='中继无人机', color='#ff7f0e')
    ax.axhline(8, color='#1f77b4', ls='--', lw=1, label='运输机库存 8')
    ax.axhline(2, color='#ff7f0e', ls='--', lw=1, label='中继机库存 2')
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_title('(b) 问题四 分区的资源需求 vs 库存', fontsize=10)
    ax.set_ylabel('数量（架）'); ax.legend(fontsize=7); ax.grid(axis='y', alpha=0.3)
    # (c) 灵敏度
    ax = axes[2]
    s = pd.read_csv(os.path.join(OUT, 'Q1_4_返航余量灵敏度.csv')).dropna()
    ax.plot(100 * s['rho'], s['架次数'], 'o-', color='#1f77b4', label='架次数')
    ax.set_xlabel('返航安全余量 ρ (%)'); ax.set_ylabel('架次数', color='#1f77b4')
    ax2 = ax.twinx()
    ax2.plot(100 * s['rho'], s['总能耗kWh'], 's--', color='#d62728', label='总能耗')
    ax2.set_ylabel('总能耗 (kWh)', color='#d62728')
    ax.set_title('(c) 问题一 ρ 灵敏度\n（ρ=40% 起不可行）', fontsize=10)
    ax.grid(alpha=0.3)
    fig.savefig(os.path.join(FIG, 'fig3_resources.png'))
    plt.close(fig)


def fig4_comm(nodes, z, lon, lat):
    q3r = pd.read_csv(os.path.join(OUT, 'Q3_中继架次.csv'))
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13, 5.6),
                                  gridspec_kw=dict(width_ratios=[1.15, 1]))
    dem_background(ax, z, lon, lat)
    o = nodes['O01']
    ax.plot(o['lon'], o['lat'], marker='*', ms=16, color='black', zorder=6)
    for i in range(1, 16):
        s = nodes['S%03d' % i]
        ax.plot(s['lon'], s['lat'], marker='o', ms=5, color='#ff7f0e', zorder=5)
    # 可达 G01 的区域（用采样格点近似）
    for _, r in q3r.iterrows():
        ax.plot(r['悬停经度（°）'], r['悬停纬度（°）'], marker='^', ms=9,
                color='#2ca02c', zorder=7, markeredgecolor='black', mew=0.5)
        ax.add_patch(Circle((r['悬停经度（°）'], r['悬停纬度（°）']), 0.028,
                            fill=False, ec='#2ca02c', ls=':', lw=0.8, alpha=0.8))
    ax.set_title('图4a  问题三 中继悬停位置（△）与覆盖示意\n（每个 △ 服务一个运输架次）',
                 fontsize=10)
    ax.set_xlabel('经度 (°)'); ax.set_ylabel('纬度 (°)')
    # 通信阶段时长
    q3c = pd.read_csv(os.path.join(OUT, 'Q3_通信保障.csv'))
    dur = (q3c['结束时刻（s）'] - q3c['开始时刻（s）']).groupby(q3c['保障方式']).sum()
    ax2.pie(dur.values, labels=['%s\n%.0f s' % (k, v) for k, v in dur.items()],
            autopct='%1.1f%%', colors=['#2ca02c', '#d62728'],
            startangle=90, textprops=dict(fontsize=9))
    ax2.set_title('图4b  通信保障时长占比\n（总通信 %d 阶段，中断 0）'
                  % len(q3c), fontsize=10)
    fig.savefig(os.path.join(FIG, 'fig4_comm.png'))
    plt.close(fig)


def fig5_partition(nodes, z, lon, lat):
    q4 = pd.read_csv(os.path.join(OUT, 'Q4_分区配置.csv'))
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.8))
    for ax, K in zip(axes, (2, 3)):
        dem_background(ax, z, lon, lat)
        sub = q4[q4['K（2或3）'] == K]
        cols = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd']
        for gi, (_, r) in enumerate(sub.iterrows()):
            sites = [x for x in str(r['服务区列表']).split(';') if x]
            for s in sites:
                n = nodes[s]
                ax.plot(n['lon'], n['lat'], marker='o', ms=9, color=cols[gi],
                        markeredgecolor='black', mew=0.6, zorder=6)
                ax.annotate(s, (n['lon'], n['lat']), textcoords='offset points',
                            xytext=(6, 3), fontsize=6.5)
        o = nodes['O01']
        ax.plot(o['lon'], o['lat'], marker='*', ms=15, color='black', zorder=7)
        ax.set_title('K=%d：%s\n运输机 %d 架 / 中继 %d 架' % (
            K, ' ｜ '.join(r['任务组编号'] for _, r in sub.iterrows()),
            sub[['A型运输无人机数', 'B型运输无人机数', 'C型运输无人机数']].sum().sum(),
            sub['中继无人机数'].sum()), fontsize=10)
        ax.set_xlabel('经度 (°)')
        if K == 2:
            ax.set_ylabel('纬度 (°)')
    fig.suptitle('图5  问题四 任务分区方案对比（K=2 均衡但资源省；K=3 更快但超库存）',
                 fontsize=11)
    fig.savefig(os.path.join(FIG, 'fig5_partition.png'))
    plt.close(fig)


def main():
    nodes = C.load_nodes(); types = C.load_transport_types()
    z, lon, lat = load_dem()
    n_relay = fig1_routes(nodes, types, z, lon, lat)
    fig2_gantt(nodes, z, lon, lat)
    fig3_resources()
    fig4_comm(nodes, z, lon, lat)
    fig5_partition(nodes, z, lon, lat)
    fig6_a1_two_readings(nodes, types, z, lon, lat)
    fig7_sensitivity(nodes, types, z, lon, lat)
    print("图表已生成 ->", FIG)
    for f in sorted(os.listdir(FIG)):
        print("  %-24s %8.1f KB" % (f, os.path.getsize(os.path.join(FIG, f)) / 1024))
    return n_relay


# ---------------------------------------------------------------------------
# 评估改进新增图：A-1 两读法对比 / 灵敏度条形图
# ---------------------------------------------------------------------------
def fig6_a1_two_readings(nodes, types, z, lon, lat):
    """A-1 两种读法的并列对比（仿文献 [5] 的配对图写法）。"""
    o = nodes['O01']
    sites = list(range(1, 16))
    rows = []
    for i in sites:
        s = nodes['S%03d' % i]
        g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])
        for k in ('B', 'C'):
            ty = types[k]; q = ty['q_max']
            # 读法(a)：由标准航程反推（本文口径）
            f = C.leg_time_energy(ty, g, q, o['elev'], s['elev'] + C.CABIN)
            b = C.leg_time_energy(ty, g, 0.0, s['elev'] + C.CABIN, o['elev'])
            Ea = f['E'] + b['E']
            # 读法(b)：由爬升效率外推功率 P = m g / eta_up * (v_c / v_up)
            def Pb(ty_, qq):
                return (ty_['m_empty'] + qq) * C.G / ty_['eta_up'] * \
                       (ty_['v_cruise'] / ty_['v_up']) / 1000.0
            def legb(ty_, gg, qq, h0, h1):
                d, zc = gg['d'], gg['z_cruise']
                hu, hd = max(0.0, zc - h0), max(0.0, zc - h1)
                m = ty_['m_empty'] + qq
                E = Pb(ty_, qq) * (d / ty_['v_cruise']) / 3600.0
                E += m * C.G * hu / ty_['eta_up'] / 3.6e6
                return E
            Eb = legb(ty, g, q, o['elev'], s['elev'] + C.CABIN) + \
                 legb(ty, g, 0.0, s['elev'] + C.CABIN, o['elev'])
            rows.append((i, k, Ea, Eb))
    d = pd.DataFrame(rows, columns=['site', 'type', 'Ea', 'Eb'])
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
    for ax, k in zip(axes, ('B', 'C')):
        sub = d[d['type'] == k].sort_values('site')
        x = np.arange(len(sub)); w = 0.38
        ax.bar(x - w / 2, sub['Ea'], w, label='(a) 由标准航程反推【本文】', color='#1f77b4')
        ax.bar(x + w / 2, sub['Eb'], w, label='(b) 由爬升效率外推', color='#d62728')
        ax.set_xticks(x); ax.set_xticklabels(['S%03d' % s for s in sub['site']],
                                             rotation=60, fontsize=7)
        ax.set_ylabel('单点往返能耗 (kWh)')
        ax.set_title('%s 型：A-1 两种读法的往返能耗' % k, fontsize=10)
        ax.legend(fontsize=7); ax.grid(axis='y', alpha=0.3)
    fig.suptitle('图6  假设 A-1 两种读法的并列对比（读法 b 使能耗低估约 45%）', fontsize=11)
    fig.savefig(os.path.join(FIG, 'fig6_a1_two_readings.png'))
    plt.close(fig)


def fig7_sensitivity(nodes, types, z, lon, lat):
    """OFAT 灵敏度条形图（S003 最大安全载荷）。"""
    o = nodes['O01']; s = nodes['S003']
    g = C.leg_geometry(z, lon, lat, o['lon'], o['lat'], s['lon'], s['lat'])

    def qstar(ty):
        lo, hi = 0.0, ty['q_max']
        for _ in range(70):
            q = (lo + hi) / 2
            E = C.leg_time_energy(ty, g, q, o['elev'], s['elev'] + C.CABIN)['E'] + \
                C.leg_time_energy(ty, g, 0.0, s['elev'] + C.CABIN, o['elev'])['E']
            if C.energy_ok(ty, E):
                lo = q
            else:
                hi = q
        return lo

    base = {k: qstar(types[k]) for k in ('A', 'B', 'C')}
    factors = [
        ('ρ: 0.20→0.30', lambda t: t.update(rho=0.30)),
        ('ρ: 0.20→0.10', lambda t: t.update(rho=0.10)),
        ('L_full ×0.8', lambda t: t.update(L_full=t['L_full'] * 0.8)),
        ('E_use ×0.8', lambda t: t.update(E_use=t['E_use'] * 0.8)),
        ('η↑: 0.72→0.60', lambda t: t.update(eta_up=0.60)),
        ('η↑: 0.72→0.85', lambda t: t.update(eta_up=0.85)),
        ('E_use ×1.2', lambda t: t.update(E_use=t['E_use'] * 1.2)),
    ]
    names = [f[0] for f in factors]
    mat = {k: [] for k in ('A', 'B', 'C')}
    for nm, mut in factors:
        for k in ('A', 'B', 'C'):
            t = dict(types[k]); mut(t)
            mat[k].append(qstar(t) - base[k])
    fig, ax = plt.subplots(figsize=(9, 4.6))
    x = np.arange(len(names)); w = 0.26
    for j, (k, col) in enumerate(zip(('A', 'B', 'C'), ('#1f77b4', '#2ca02c', '#d62728'))):
        ax.bar(x + (j - 1) * w, mat[k], w, label='%s 型' % k, color=col)
    ax.axhline(0, color='k', lw=0.8)
    ax.set_xticks(x); ax.set_xticklabels(names, rotation=28, fontsize=8, ha='right')
    ax.set_ylabel('最大安全载荷变化 Δq* (kg)')
    ax.set_title('图7  OFAT 灵敏度：S003 最大安全载荷对单因子的响应（基准 A=25.00 / B=30.00 / C=68.13 kg）',
                 fontsize=10)
    ax.legend(fontsize=8); ax.grid(axis='y', alpha=0.3)
    fig.savefig(os.path.join(FIG, 'fig7_sensitivity.png'))
    plt.close(fig)


if __name__ == '__main__':
    main()
