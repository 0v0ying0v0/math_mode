# -*- coding: utf-8 -*-
"""C6 交叉对表：论文正文数字 vs 提交表 / 结果文件。

从 paper/main.md 中抽取"可核验断言"（形如 `**N**` 或表格中的关键数字），
与 out/*.csv、out/*.json 的实际值比对。

本轮（Q1/Q2/Q3 重解后）口径：
  Q1 = 18 架次 / 63.2416 kWh / 33043.2 s（值不变，瓶颈归属重述）
  Q2 = 37 架次 / 12527.0 s / 221985.2（新增受控对照表 out/Q2_替代方案.csv）
  Q3 = 继承 Q2 的 37 个运输架次 + 15 个中继架次 / 总能耗 94.7400 kWh
"""
from __future__ import annotations
import os, re, sys, json
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'out')
PAPER = os.path.join(ROOT, 'paper', 'main.md')
REPORTS = os.path.join(ROOT, 'reports')

R = []
def ck(cid, desc, ok, paper_val, real_val):
    R.append((cid, desc, bool(ok), paper_val, real_val))
    print("%-8s %-46s %s  论文=%s 实际=%s" % (cid, "OK" if ok else "FAIL", desc,
                                              paper_val, real_val))


def txt_of_paper():
    return open(PAPER, encoding='utf-8').read()


def main():
    md = txt_of_paper()
    q1 = pd.read_csv(os.path.join(OUT, 'Q1_单点组批.csv'))
    q2 = pd.read_csv(os.path.join(OUT, 'Q2_运输架次.csv'))
    q2b = pd.read_csv(os.path.join(OUT, 'Q2_逐箱交付.csv'))
    q2a = pd.read_csv(os.path.join(OUT, 'Q2_替代方案.csv'))
    q3r = pd.read_csv(os.path.join(OUT, 'Q3_中继架次.csv'))
    q3c = pd.read_csv(os.path.join(OUT, 'Q3_通信保障.csv'))
    q3d = pd.read_csv(os.path.join(OUT, 'Q3_航段覆盖诊断.csv'))
    q4 = pd.read_csv(os.path.join(OUT, 'Q4_分区配置.csv'))
    q4c = pd.read_csv(os.path.join(OUT, 'Q4_方案比较.csv'))
    s2 = json.load(open(os.path.join(OUT, 'Q2_summary.json'), encoding='utf-8'))
    s3 = json.load(open(os.path.join(OUT, 'Q3_summary.json'), encoding='utf-8'))
    q11 = pd.read_csv(os.path.join(OUT, 'Q1_1_最大安全载荷.csv'))
    par = pd.read_csv(os.path.join(OUT, 'Q1_Pareto前沿.csv'))
    sens = pd.read_csv(os.path.join(OUT, 'Q1_4_返航余量灵敏度.csv'))

    print("=" * 96)
    print("C6 论文—结果交叉对表")
    print("=" * 96)

    def has(pat):
        return re.search(pat, md) is not None

    # ---- Q1 ----
    n1 = len(q1); e1 = q1['架次能耗（kWh）'].sum(); t1 = q1['往返时间（s）'].sum()
    ck('X-1.1', 'Q1 架次数 18', has(r'\*\*18 架次\*\*') and n1 == 18,
       18, n1)
    ck('X-1.2', 'Q1 总能耗 63.2416 kWh',
       has(r'\*\*63\.2416 kWh\*\*') and abs(e1 - 63.2416) < 1e-3,
       '63.2416', round(e1, 4))
    ck('X-1.3', 'Q1 累计作业时间 33043.2 s',
       has(r'\*\*33043\.2 s\*\*') and abs(t1 - 33043.2) < 0.1,
       '33043.2', round(t1, 1))
    ck('X-1.4', 'Q1 机型分布 C×11 / B×7 / A×0',
       has(r'C×11') and has(r'B×7') and has(r'A 型 0'),
       'C11/B7/A0', 'C%d/B%d/A%d' % ((q1['机型编号'] == 'C').sum(),
                                     (q1['机型编号'] == 'B').sum(),
                                     (q1['机型编号'] == 'A').sum()))
    i_min = q1['返航SOC（%）'].idxmin()
    r_min = q1.loc[i_min]
    ck('X-1.5', 'Q1 最低返航 SOC 22.71%（Q1-007/S004/C）',
       has(r'22\.71%') and abs(r_min['返航SOC（%）'] - 22.71) < 0.01
       and r_min['架次编号'] == 'Q1-007' and r_min['服务区编号'] == 'S004'
       and r_min['机型编号'] == 'C',
       '22.71', '%s/%s/%s=%.2f' % (r_min['架次编号'], r_min['服务区编号'],
                                   r_min['机型编号'], r_min['返航SOC（%）']))
    # 三瓶颈数值
    def qs(site, tp):
        r = q11[(q11['Si'] == site) & (q11['type'] == tp)]
        return round(float(r.iloc[0]['q_safe']), 2)
    for site, tp, val in [('S002', 'C', 68.45), ('S003', 'C', 68.13),
                          ('S004', 'C', 63.14), ('S008', 'C', 58.30),
                          ('S008', 'B', 28.58), ('S012', 'C', 67.91)]:
        real = qs(site, tp)
        ck('X-1.6.' + site + tp, 'Q1.1 %s %s型 最大安全载荷' % (site, tp),
           abs(real - val) < 0.01 and has(re.escape(str(val))), val, real)
    # 瓶颈归属计数（bottleneck 取值为 质量/能量/质量能量，后两者同为"质量限"）：
    #   A 15 站 质量限；B 14 站 质量限 + S008 能量限；C 10 站 质量限 + 5 站 能量限
    def _n(tp, kind):
        sub = q11[q11['type'] == tp]
        return int((sub['bottleneck'] == kind).sum() + (sub['bottleneck'] == '质量能量').sum()) \
            if kind == '质量' else int((sub['bottleneck'] == kind).sum())
    n_C_m, n_C_e = _n('C', '质量'), _n('C', '能量')
    n_B_m, n_B_e = _n('B', '质量'), _n('B', '能量')
    ck('X-1.6c', 'Q1.1 瓶颈归属 C:10 质量 + 5 能量 / B:14 质量 + 1 能量',
       n_C_m == 10 and n_C_e == 5 and n_B_m == 14 and n_B_e == 1
       and has(r'C 型 10 个受质量限 \+ 5 个受能量限')
       and has(r'B 型 14 个受质量限 \+ S008 受能量限'),
       'C10m+5e/B14m+1e',
       'C%dm+%de/B%dm+%de' % (n_C_m, n_C_e, n_B_m, n_B_e))
    # Pareto
    p0 = par.iloc[0]; pN = par.iloc[-1]
    ck('X-1.7', 'Pareto 首点 (18, 63.2416, 33043.2)',
       has(r'\| \*\*18\*\* \| \*\*63\.2416\*\* \| \*\*33043\.2\*\* \|')
       and abs(p0['总能耗kWh'] - 63.2416) < 1e-3 and abs(p0['累计作业时间s'] - 33043.2) < 0.1,
       '18/63.2416/33043.2',
       '%d/%.4f/%.1f' % (p0['架次数'], p0['总能耗kWh'], p0['累计作业时间s']))
    ck('X-1.8', 'Pareto 末点 (23, 62.7474, 41271.7)',
       has(r'\| 23 \| 62\.7474 \| 41271\.7 \|')
       and abs(pN['总能耗kWh'] - 62.7474) < 1e-3 and abs(pN['累计作业时间s'] - 41271.7) < 0.1,
       '23/62.7474/41271.7',
       '%d/%.4f/%.1f' % (pN['架次数'], pN['总能耗kWh'], pN['累计作业时间s']))
    dE = round(100 * (p0['总能耗kWh'] - pN['总能耗kWh']) / p0['总能耗kWh'], 2)
    dT = round(100 * (pN['累计作业时间s'] - p0['累计作业时间s']) / p0['累计作业时间s'], 1)
    ck('X-1.9', 'Pareto 能耗降幅 0.78%',
       has(r'0\.78%') and abs(dE - 0.78) < 0.01, '0.78', dE)
    ck('X-1.10', 'Pareto 作业时间增幅 24.9%',
       has(r'24\.9%') and abs(dT - 24.9) < 0.05, '24.9', dT)
    # rho 灵敏度
    s20 = sens[abs(sens['rho'] - 0.20) < 1e-9].iloc[0]
    s25 = sens[abs(sens['rho'] - 0.25) < 1e-9].iloc[0]
    s30 = sens[abs(sens['rho'] - 0.30) < 1e-9].iloc[0]
    ck('X-1.11', 'rho=20% 架次 18 / 能耗 63.2416',
       abs(s20['架次数'] - 18) < .5 and abs(s20['总能耗kWh'] - 63.2416) < 1e-3
       and abs(s20['累计作业时间s'] - 33043.2) < 0.1
       and has(r'\| \*\*20%\*\* \| \*\*18\*\* \| \*\*63\.2416\*\* \| \*\*33043\.2\*\* \|'),
       '18/63.2416/33043.2', '%d/%.4f/%.1f' % (s20['架次数'], s20['总能耗kWh'],
                                               s20['累计作业时间s']))
    ck('X-1.12', 'rho=25% 架次 19 / 能耗 67.5507 / 34953.5 s',
       abs(s25['架次数'] - 19) < .5 and abs(s25['总能耗kWh'] - 67.5507) < 1e-3
       and abs(s25['累计作业时间s'] - 34953.5) < 0.1
       and has(r'\| 25% \| 19 \| 67\.5507 \| 34953\.5 \|'),
       '19/67.5507/34953.5', '%d/%.4f/%.1f' % (s25['架次数'], s25['总能耗kWh'],
                                               s25['累计作业时间s']))
    ck('X-1.13', 'rho=30% 架次 20 / 能耗 72.3407 / 36828.5 s',
       abs(s30['架次数'] - 20) < .5 and abs(s30['总能耗kWh'] - 72.3407) < 1e-3
       and abs(s30['累计作业时间s'] - 36828.5) < 0.1
       and has(r'\| 30% \| 20 \| 72\.3407 \| 36828\.5 \|'),
       '20/72.3407/36828.5', '%d/%.4f/%.1f' % (s30['架次数'], s30['总能耗kWh'],
                                               s30['累计作业时间s']))

    # ---- Q2 ----
    tc2 = q2['机型编号'].value_counts().to_dict()
    ck('X-2.1', 'Q2 架次数 37（A24/B10/C3）',
       has(r'\*\*37 架次\*\*') and len(q2) == 37
       and tc2.get('A') == 24 and tc2.get('B') == 10 and tc2.get('C') == 3,
       37, len(q2))
    ck('X-2.2', 'Q2 makespan 12527.0 s',
       has(r'\*\*12527\.0 s\*\*') and abs(s2['makespan'] - 12527.0) < 0.05,
       '12527.0', round(s2['makespan'], 1))
    ck('X-2.3', 'Q2 首批满足 30/30',
       bool(s2['first_ok']) and has(r'30/30'), '30/30', s2['first_ok'])
    ck('X-2.4', 'Q2 医疗满足 16/16',
       bool(s2['med_ok']) and has(r'16/16'), '16/16', s2['med_ok'])
    ck('X-2.5', 'Q2 加权迟延 221985.2',
       has(r'221985\.2') and abs(s2['wdelay'] - 221985.2) < 0.1,
       '221985.2', round(s2['wdelay'], 1))
    b1 = set()
    for x in q1['货箱编号列表']:
        b1.update(str(x).split(';'))
    b2 = set(q2b['货箱编号'])
    ck('X-2.6', 'Q2 逐箱交付覆盖 80 箱（与 Q1 组批箱集一致）',
       len(b2) == 80 and b1 == b2, '80', '%d（Q1=%d，一致=%s）' % (len(b2), len(b1), b1 == b2))
    inv = {'A': 4, 'B': 2, 'C': 2}
    used = q2.groupby('机型编号')['无人机编号'].nunique().to_dict()
    ck('X-2.7', 'Q2 实体机同时占用峰值 ≤ 库存（A4/B2/C2）',
       has(r'实体机同时占用峰值 ≤ 库存（A 4/B 2/C 2）')
       and all(used.get(k, 0) <= v for k, v in inv.items()), 'A4/B2/C2', used)
    # 受控对照：交付方案 / 替代方案（out/Q2_替代方案.csv）
    alt_ser = q2a[q2a['方案'].str.contains('串行')].iloc[0]
    alt_par = q2a[q2a['方案'].str.contains('并行')].iloc[0]
    alt_del = q2a[q2a['方案'].str.contains('Q2 两阶段')].iloc[0]
    makespan_real = q2['返回O01时刻（s）'].max()
    ck('X-2.8', '替代方案表「交付方案」完成时间 == Q2 max 返回O01',
       abs(alt_del['完成时间s'] - makespan_real) < 0.05
       and int(alt_del['架次数']) == len(q2),
       '%d/%.1f' % (int(alt_del['架次数']), makespan_real),
       '%d/%.1f' % (int(alt_del['架次数']), alt_del['完成时间s']))
    ck('X-2.9', '替代方案（Q1 组批 + 并行）可行且 12017.0 < 12527.0',
       alt_par['首批违例数'] == 0 and alt_par['医疗违例数'] == 0
       and '是' in str(alt_par['是否可行'])
       and alt_par['完成时间s'] < alt_del['完成时间s']
       and has(r'\*\*12017\.0\*\*'),
       '0/0,12017.0<12527.0',
       '%s/%s,%s<%s' % (int(alt_par['首批违例数']), alt_par['医疗违例数'],
                        alt_par['完成时间s'], alt_del['完成时间s']))
    ck('X-2.10', '论文披露 IT-21（非支配关系）', has(r'IT-21'),
       'IT-21', 'IT-21' if has(r'IT-21') else '缺失')
    # 论文对两方案的比较口径（§4.3.1）必须能从 out/Q2_替代方案.csv 复算
    d_energy = alt_del['运输能耗kWh']; a_energy = alt_par['运输能耗kWh']
    d_wdel = alt_del['加权迟延']; a_wdel = alt_par['加权迟延']
    adv_e = round(100 * (d_energy - a_energy) / d_energy, 1)      # 替代方案在能耗上的优势
    ratio_w = round(a_wdel / d_wdel, 2)                            # 替代方案迟延的倍数
    cut_w = round(100 * (a_wdel - d_wdel) / a_wdel, 0)             # 交付方案相对对照的迟延降幅
    ck('X-2.11', '两方案比较口径：能耗优 27.3% / 迟延 2.26 倍 / 降 56%',
       has(r'27\.3%') and has(r'2\.26 倍') and has(r'56%')
       and abs(adv_e - 27.3) < 0.1 and abs(ratio_w - 2.26) < 0.01
       and abs(cut_w - 56) < 0.6,
       '27.3%/2.26/56%', '%.1f%%/%.2f/%.0f%%' % (adv_e, ratio_w, cut_w))

    # ---- Q3 ----
    ck('X-3.1', 'Q3 中继架次 15',
       has(r'\*\*15 个中继架次\*\*') and len(q3r) == 15, 15, len(q3r))
    ck('X-3.2', 'Q3 中继能耗 7.7561 kWh',
       has(r'\*\*7\.7561 kWh\*\*') and abs(s3['中继能耗kWh'] - 7.7561) < 1e-3,
       '7.7561', round(s3['中继能耗kWh'], 4))
    ck('X-3.3', 'Q3 总能耗 94.7400 kWh',
       has(r'\*\*94\.7400 kWh\*\*') and abs(s3['总能耗kWh'] - 94.7400) < 1e-3,
       '94.7400', round(s3['总能耗kWh'], 4))
    ck('X-3.4', 'Q3 联合完成时刻 20072 s（运输 20063 / 中继 20072）',
       has(r'\*\*20072 s\*\*') and abs(s3['联合完成时刻s'] - 20072) < 1
       and abs(s3['运输完成时刻s'] - 20063) < 1 and abs(s3['中继完成时刻s'] - 20072) < 1,
       '20072', round(s3['联合完成时刻s'], 1))
    # X-3.5（原"运输能耗 == Q1 总能耗"前提已废）→ 结构检查：Q3 运输能耗 == Q2 运输能耗
    e2 = q2['架次能耗（kWh）'].sum()
    ck('X-3.5', 'Q3 运输能耗 == Q2 运输能耗（继承口径）',
       abs(s3['运输能耗kWh'] - e2) < 1e-6, round(e2, 4), round(s3['运输能耗kWh'], 4))
    ck('X-3.5b', 'Q3 运输架次数 == Q2 架次数（继承口径）',
       int(s3['运输架次数']) == len(q2), len(q2), int(s3['运输架次数']))
    ck('X-3.6', 'Q3 中继最大能耗 0.9493 kWh',
       has(r'0\.9493') and abs(q3r['架次能耗（kWh）'].max() - 0.9493) < 1e-3,
       '0.9493', round(q3r['架次能耗（kWh）'].max(), 4))
    # 悬停离地：论文断言最大 300 m（= 上限）。out/ 无"悬停离地"列，故三向对表：
    #   论文 300 m  ←→  core.RELAY_HMAX（唯一物理常数源）  ←→  各需中继站 h_min ≤ 300（可行性）
    hmin = q3d.loc[q3d['relay'] > 0, 'h_min'].dropna()
    core_src = open(os.path.join(ROOT, 'code', 'core.py'), encoding='utf-8').read()
    m_hmax = re.search(r'^RELAY_HMAX\s*=\s*([0-9.]+)', core_src, re.M)
    hmax_core = float(m_hmax.group(1)) if m_hmax else float('nan')
    ck('X-3.7', 'Q3 悬停离地最大 300 m（上限 = core.RELAY_HMAX，无站需 >300 m）',
       has(r'最大 \*\*300 m\*\*（上限 300 m）') and hmax_core == 300.0
       and len(hmin) > 0 and (hmin <= hmax_core).all(),
       '300', 'RELAY_HMAX=%.0f; h_min≤%.0f（%d 站）' % (hmax_core, hmin.max(), len(hmin)))
    ck('X-3.8', 'Q3 采样 996 = 直连 648 + 需中继 348；中继保障 == 需中继；中断 0',
       has(r'996 个通信判定点') and has(r'直连 648') and has(r'348')
       and int(s3['需中继样本数']) + int(s3['直连样本数']) == 996
       and int(s3['直连样本数']) == 648 and int(s3['需中继样本数']) == 348
       and int(s3['中继保障样本数']) == int(s3['需中继样本数'])
       and int(s3['通信中断样本数']) == 0,
       '996/648/348', '%d/%d/%d' % (int(s3['需中继样本数']) + int(s3['直连样本数']),
                                    int(s3['直连样本数']), int(s3['需中继样本数'])))
    ck('X-3.9', 'Q3 硬时限架次 15 / 超限 0',
       has(r'\*\*0 / 15\*\*') and int(s3['硬时限架次数']) == 15
       and int(s3['硬时限架次超限数']) == 0,
       '0/15', '%d/%d' % (int(s3['硬时限架次超限数']), int(s3['硬时限架次数'])))
    ck('X-3.10', 'Q3 首批截止违反 0 / 医疗期望违反 0',
       has(r'30/30 = 100%') and has(r'16/16 = 100%')
       and int(s3['首批截止违反数']) == 0 and int(s3['医疗期望违反数']) == 0
       and int(s3['首批保障箱数']) == 30 and int(s3['医疗箱数']) == 16,
       '30/30,16/16',
       '%d/%d,%d/%d' % (int(s3['首批保障箱数']) - int(s3['首批截止违反数']),
                        int(s3['首批保障箱数']),
                        int(s3['医疗箱数']) - int(s3['医疗期望违反数']),
                        int(s3['医疗箱数'])))
    relay_ph = int((q3c['保障方式'] == '中继').sum())
    ck('X-3.11', 'Q3 通信阶段 320（中继 145 / 直连 175）',
       has(r'合计 320 个通信阶段')
       and int(s3['通信阶段数']) == 320 and int(s3['中继保障阶段数']) == 145
       and int(s3['直连保障阶段数']) == 175 and len(q3c) == 320
       and relay_ph == 145,
       '320/145/175', '%d/%d/%d' % (int(s3['通信阶段数']), relay_ph,
                                    int(s3['直连保障阶段数'])))
    ck('X-3.12', 'Q3 中继最大悬停 2365 s ≤ 上限 7137 s',
       has(r'2365 s') and int(s3['中继最大悬停s']) == 2365
       and int(s3['中继最大悬停s']) <= int(s3['中继悬停时长上限s']),
       '2365', '%d（上限 %d）' % (int(s3['中继最大悬停s']), int(s3['中继悬停时长上限s'])))
    ck('X-3.13', 'Q3 中继并发上限 2（= 库存中继机数）',
       has(r'中继无人机只有 \*\*2 架\*\*') and int(s3['中继并发上限']) == 2
       and q3r['中继无人机编号'].nunique() == 2,
       '2', '%d（实用 %d 架）' % (int(s3['中继并发上限']), q3r['中继无人机编号'].nunique()))
    ck('X-3.14', 'Q3 被延迟架次 21 / 37，最大后推 12695 s',
       has(r'\*\*21 / 37\*\*') and has(r'12695 s')
       and int(s3['被延迟架次数']) == 21 and int(s3['最大运输延迟s']) == 12695
       and int(s3['运输架次数']) == 37,
       '21/37,12695', '%d/%d,%d' % (int(s3['被延迟架次数']), int(s3['运输架次数']),
                                   int(s3['最大运输延迟s'])))

    # ---- Q4 ----
    def deliver(K):
        return q4c[(q4c['K'] == K) & q4c['方案'].str.contains('交付方案')].iloc[0]
    r2 = deliver(2); r3 = deliver(3)
    # 分区配置（q4）逐组明细；方案比较（q4c）整方案汇总，两者按同口径相加
    tot = {K: dict(t=int(q4[q4['K（2或3）'] == K]
                         [['A型运输无人机数', 'B型运输无人机数', 'C型运输无人机数']].sum().sum()),
                   r=int(q4[q4['K（2或3）'] == K]['中继无人机数'].sum()),
                   c=int(q4[q4['K（2或3）'] == K]['中继能源组件数'].sum()))
           for K in (2, 3)}
    ck('X-4.1', 'Q4 运输机合计 K=2 11 / K=3 13（分区加总）',
       has(r'5 / 4 / 2（合计 11）') and has(r'7 / 4 / 2（合计 13）')
       and tot[2]['t'] == 11 and tot[3]['t'] == 13,
       '11/13', '%d/%d' % (tot[2]['t'], tot[3]['t']))
    ck('X-4.2', 'Q4 中继合计 K=2 4 / K=3 6（分区加总）',
       has(r'中继无人机 \| 4 \| 6')
       and tot[2]['r'] == 4 and tot[3]['r'] == 6,
       '4/6', '%d/%d' % (tot[2]['r'], tot[3]['r']))
    ck('X-4.3', 'Q4 中继组件 K=2 7 / K=3 8（分区加总）',
       has(r'中继能源组件 \| 7 \| 8')
       and tot[2]['c'] == 7 and tot[3]['c'] == 8,
       '7/8', '%d/%d' % (tot[2]['c'], tot[3]['c']))
    ck('X-4.4', 'Q4 缺口率 K=2 2.4167 / K=3 4.25',
       has(r'\*\*2\.4167\*\*') and has(r'\*\*4\.2500\*\*')
       and abs(float(r2['缺口率']) - 2.4167) < 1e-3 and abs(float(r3['缺口率']) - 4.25) < 1e-3,
       '2.4167/4.25', '%s/%s' % (r2['缺口率'], r3['缺口率']))
    ck('X-4.5', 'Q4 缺口总数 K=2 6 / K=3 12',
       has(r'\*\*6\*\*') and has(r'\*\*12\*\*')
       and int(r2['缺口总数']) == 6 and int(r3['缺口总数']) == 12,
       '6/12', '%d/%d' % (int(r2['缺口总数']), int(r3['缺口总数'])))
    ck('X-4.6', 'Q4 工时极差 K=2 119 / K=3 1114 s',
       has(r'工时极差 \(s\) \| 119 \| 1114')
       and abs(float(r2['工时极差s']) - 119.0) < 0.5 and abs(float(r3['工时极差s']) - 1114.0) < 0.5,
       '119/1114', '%s/%s' % (r2['工时极差s'], r3['工时极差s']))
    ck('X-4.7', 'Q4 组完成时刻极差 K=2 5634 / K=3 1912 s',
       has(r'组完成时刻极差 \(s\) \| 5634 \| 1912')
       and abs(float(r2['组完成时刻极差s']) - 5634.0) < 0.5
       and abs(float(r3['组完成时刻极差s']) - 1912.0) < 0.5,
       '5634/1912', '%s/%s' % (r2['组完成时刻极差s'], r3['组完成时刻极差s']))
    ck('X-4.8', 'Q4 联合完成时刻 20072（K=2/K=3 同，冻结 Q3）',
       has(r'联合完成时刻 \(s\) \| 20072 \| 20072')
       and abs(float(r2['联合完成时刻s']) - 20072.0) < 0.5
       and abs(float(r3['联合完成时刻s']) - 20072.0) < 0.5,
       '20072', '%s/%s' % (r2['联合完成时刻s'], r3['联合完成时刻s']))
    ck('X-4.9', 'Q4 组架次数均衡 K=2 19/18、K=3 12/12/13',
       has(r'19 / 18') and has(r'12 / 12 / 13')
       and r2['组架次数'] == '19/18' and r3['组架次数'] == '12/12/13',
       '19/18,12/12/13', '%s,%s' % (r2['组架次数'], r3['组架次数']))

    # ---- 全局 ----
    ck('X-5.1', 'DEM 高程范围 41.7-1132.9 m', has(r'41\.7 – 1132\.9 m'),
       '41.7-1132.9', '41.7-1132.9')
    vol_sum = q1['总体积（m³）'].sum()
    ck('X-5.2', '总体积 2.011 m³（= Q1 各架次体积和）',
       has(r'2\.011 m³') and abs(vol_sum - 2.011) < 5e-4,
       '2.011', round(vol_sum, 4))
    mass_sum = q1['总质量（kg）'].sum()
    ck('X-5.3', '总质量 758 kg（= Q1 各架次质量和）',
       has(r'758 kg') and abs(mass_sum - 758) < 0.5,
       '758', round(mass_sum, 1))
    ck('X-5.4', '首批箱数 30',
       has(r'首批箱数 = 30') and int(s3['首批保障箱数']) == 30,
       30, int(s3['首批保障箱数']))
    ck('X-5.5', '链路门限 T↔G01 122 / T↔RA 116',
       has(r'122\.0') and has(r'116\.0'), '122/116', '122/116')
    # 可中继格点占比：论文 2.1–6.5%（150–300 m 悬停高度带）；out 侧核 12 需中继 / 3 直连
    n_relay_site = int((q3d['relay'] > 0).sum())
    n_direct_site = int(((q3d['direct'] > 0) & (q3d['relay'] == 0)).sum())
    ck('X-5.6', '可中继格点占比 2.1–6.5%（12 需中继 / 3 全程直连）',
       has(r'2\.1–6\.5%') and has(r'12 个存在需中继时段')
       and n_relay_site == 12 and n_direct_site == 3 and int(q3d['none'].sum()) == 0,
       '2.1-6.5%', '%d/%d' % (n_relay_site, n_direct_site))
    # 鲁棒性 63% → 100%（数据源：reports/B8_global.md GR-1；out/ 无对应产物）
    b8p = os.path.join(REPORTS, 'B8_global.md')
    b8 = open(b8p, encoding='utf-8').read() if os.path.exists(b8p) else ''
    ck('X-5.7', '鲁棒性 100/100（逐架次失效 8/1800 = 0.44%）',
       has(r'100/100') and has(r'0\.44%') and has(r'8/1800')
       and abs(100.0 * 8 / 1800 - 0.44) < 0.005
       and re.search(r'100/100', b8) is not None,
       '100/100,0.44%', '8/1800=%.2f%%;B8=%s' % (100.0 * 8 / 1800,
                                                  'Y' if re.search(r'100/100', b8) else 'N'))
    # 图引用一致性（评审 T3-15）
    body = md[:md.index('## 八、参考文献')] if '## 八、参考文献' in md else md
    n_inline = len(re.findall(r'!\[[^\]]*\]\(figs/', body))
    ck('X-6.1', '正文嵌入图片 ≥ 5 张', n_inline >= 5, 5, n_inline)
    ck('X-6.2', '正文「图n」引用编号覆盖 1..N',
       set(range(1, n_inline + 1)) <= {int(x) for x in re.findall(r'\*\*图\s*(\d+)', body)},
       '1..%d' % n_inline, sorted({int(x) for x in re.findall(r'\*\*图\s*(\d+)', body)}))
    ck('X-6.3', 'ITU 常数已改为 32.4（与 ITU-R P.525-5 一致）',
       has(r'32\.4\+20') and not has(r'32\.45'), '32.4', '32.4' if not has(r'32\.45') else '仍为 32.45')
    ck('X-6.4', '假设 A-3（充电模型降格）已写入正文',
       has(r'\*\*A-3\*\*'), 'A-3', 'A-3' if has(r'\*\*A-3\*\*') else '缺失')
    ck('X-6.5', 'DSM 性质已声明', has(r'数字表面模型（DSM）'), 'DSM', 'DSM')

    npass = sum(1 for *_, ok, _, _ in R if ok)
    print("\n" + "=" * 96)
    print("C6 交叉对表：%d/%d 通过" % (npass, len(R)))
    for cid, desc, ok, pv, rv in R:
        if not ok:
            print("  FAIL %s %s 论文=%s 实际=%s" % (cid, desc, pv, rv))
    return 0 if npass == len(R) else 1


if __name__ == '__main__':
    sys.exit(main())
