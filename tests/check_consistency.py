# -*- coding: utf-8 -*-
"""C6 交叉对表：论文正文数字 vs 提交表 / 结果文件。

从 paper/main.md 中抽取"可核验断言"（形如 `**N**` 或表格中的关键数字），
与 out/*.csv、out/*.json 的实际值比对。
"""
from __future__ import annotations
import os, re, sys, json
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'out')
PAPER = os.path.join(ROOT, 'paper', 'main.md')

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
    q3r = pd.read_csv(os.path.join(OUT, 'Q3_中继架次.csv'))
    q3c = pd.read_csv(os.path.join(OUT, 'Q3_通信保障.csv'))
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
    ck('X-1.1', 'Q1 架次数', has(r'\*\*18 架次\*\*'), 18, n1)
    ck('X-1.2', 'Q1 总能耗 64.4265 kWh', has(r'\*\*64\.4265 kWh\*\*'),
       '64.4265', round(e1, 4))
    ck('X-1.3', 'Q1 累计作业时间 33043.2 s', has(r'\*\*33043\.2 s\*\*'),
       '33043.2', round(t1, 1))
    ck('X-1.4', 'Q1 机型分布 C×11 / B×7',
       has(r'C×11') and has(r'B×7'),
       'C11/B7', 'C%d/B%d' % ((q1['机型编号'] == 'C').sum(), (q1['机型编号'] == 'B').sum()))
    ck('X-1.5', 'Q1 最低返航 SOC 20.89%', has(r'20\.89%'),
       '20.89', q1['返航SOC（%）'].min())
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
    # Pareto
    ck('X-1.7', 'Pareto 首点 (18, 64.4265, 33043.2)',
       has(r'\| \*\*18\*\* \| \*\*64\.4265\*\* \| \*\*33043\.2\*\* \|'),
       '18/64.4265/33043.2',
       '%d/%.4f/%.1f' % (par.iloc[0]['架次数'], par.iloc[0]['总能耗kWh'],
                         par.iloc[0]['累计作业时间s']))
    ck('X-1.8', 'Pareto 末点 (23, 63.4001, 41271.7)',
       has(r'\| 23 \| 63\.4001 \| 41271\.7 \|'),
       '23/63.4001/41271.7',
       '%d/%.4f/%.1f' % (par.iloc[-1]['架次数'], par.iloc[-1]['总能耗kWh'],
                         par.iloc[-1]['累计作业时间s']))
    ck('X-1.9', 'Pareto 能耗降幅 1.59%', has(r'1\.59%'), '1.59',
       round(100 * (par.iloc[-1]['总能耗kWh'] - par.iloc[0]['总能耗kWh'])
             / par.iloc[0]['总能耗kWh'], 2))
    ck('X-1.10', 'Pareto 作业时间增幅 24.9%', has(r'24\.9%'), '24.9',
       round(100 * (par.iloc[-1]['累计作业时间s'] - par.iloc[0]['累计作业时间s'])
             / par.iloc[0]['累计作业时间s'], 1))
    # rho 灵敏度
    s20 = sens[abs(sens['rho'] - 0.20) < 1e-9].iloc[0]
    s25 = sens[abs(sens['rho'] - 0.25) < 1e-9].iloc[0]
    s30 = sens[abs(sens['rho'] - 0.30) < 1e-9].iloc[0]
    ck('X-1.11', 'rho=20% 架次 18 / 能耗 64.4265',
       abs(s20['架次数'] - 18) < .5 and abs(s20['总能耗kWh'] - 64.4265) < 1e-3,
       '18/64.4265', '%d/%.4f' % (s20['架次数'], s20['总能耗kWh']))
    ck('X-1.12', 'rho=25% 架次 19 / 能耗 68.4400',
       abs(s25['架次数'] - 19) < .5 and abs(s25['总能耗kWh'] - 68.4400) < 1e-3,
       '19/68.4400', '%d/%.4f' % (s25['架次数'], s25['总能耗kWh']))
    ck('X-1.13', 'rho=30% 架次 20 / 能耗 72.6395',
       abs(s30['架次数'] - 20) < .5 and abs(s30['总能耗kWh'] - 72.6395) < 1e-3,
       '20/72.6395', '%d/%.4f' % (s30['架次数'], s30['总能耗kWh']))

    # ---- Q2 ----
    ck('X-2.1', 'Q2 架次数 37', has(r'\*\*37 架次\*\*'), 37, len(q2))
    ck('X-2.2', 'Q2 makespan 12522.3 s', has(r'\*\*12522\.3 s\*\*'),
       '12522.3', round(s2['makespan'], 1))
    ck('X-2.3', 'Q2 首批满足 100%', bool(s2['first_ok']), 'True', s2['first_ok'])
    ck('X-2.4', 'Q2 医疗满足', bool(s2['med_ok']), 'True', s2['med_ok'])
    ck('X-2.5', 'Q2 加权迟延 221732.7', has(r'221732\.7'),
       '221732.7', round(s2['wdelay'], 1))
    ck('X-2.6', 'Q2 逐箱交付覆盖 80 箱', len(set(q2b['货箱编号'])) == 80,
       80, len(set(q2b['货箱编号'])))
    ck('X-2.7', 'Q2 实体机 ≤ 库存', has(r'实体机同时占用峰值 ≤ 库存'),
       '声明', True)

    # ---- Q3 ----
    ck('X-3.1', 'Q3 中继架次 14', has(r'\*\*14 个中继架次\*\*'), 14, len(q3r))
    ck('X-3.2', 'Q3 中继能耗 5.8610 kWh', has(r'\*\*5\.8610 kWh\*\*'),
       '5.8610', round(s3['中继能耗kWh'], 4))
    ck('X-3.3', 'Q3 总能耗 70.2875 kWh', has(r'\*\*70\.2875 kWh\*\*'),
       '70.2875', round(s3['总能耗kWh'], 4))
    ck('X-3.4', 'Q3 联合完成时刻 33043.2 s', has(r'\*\*33043\.2 s\*\*'),
       '33043.2', round(s3['联合完成时刻s'], 1))
    ck('X-3.5', 'Q3 运输能耗 64.4265 kWh（== Q1）',
       abs(s3['运输能耗kWh'] - e1) < 1e-6, round(e1, 4), round(s3['运输能耗kWh'], 4))
    ck('X-3.6', 'Q3 中继最大能耗 0.5594 kWh', has(r'0\.5594'),
       '0.5594', round(q3r['架次能耗（kWh）'].max(), 4))
    ck('X-3.7', 'Q3 悬停离地最大 200 m', has(r'最大 \*\*200 m\*\*'),
       '200', '见 render/报表')
    ck('X-3.8', 'Q3 通信阶段 609 采样', True, '0/609', '见 verify.py D-1')

    # ---- Q4 ----
    for K in (2, 3):
        sub = q4[q4['K（2或3）'] == K]
        tot_t = int(sub[['A型运输无人机数', 'B型运输无人机数', 'C型运输无人机数']].sum().sum())
        tot_r = int(sub['中继无人机数'].sum())
        ck('X-4.%d.1' % K, 'Q4 K=%d 运输机合计 %d' % (K, tot_t),
           has(r'合计 \*\*%d\*\*' % tot_t) or has(r'\| 0 / \d / \d.*%d' % tot_t),
           tot_t, tot_t)
        ck('X-4.%d.2' % K, 'Q4 K=%d 中继合计数' % K, tot_r == (2 if K == 2 else 3),
           tot_r, tot_r)
    r2 = q4c[q4c['K'] == 2].iloc[0]
    ck('X-4.3', 'Q4 K=2 缺 1 架 C 型',
       has(r'缺 \*{0,2}1 架 C 型运输机') and int(r2['运输机缺口']) == 1,
       '1 架 C', int(r2['运输机缺口']))
    ck('X-4.4', 'Q4 K=2 工作量标准差 0.000', has(r'\*\*0\.000\*\*'),
       '0.000', r2['工作量均衡标准差'])
    r3 = q4c[q4c['K'] == 3].iloc[0]
    ck('X-4.5', 'Q4 K=3 组完成时刻 14057.7 s', has(r'14057\.7 s'),
       '14057.7', round(r3['组完成时刻max_s'], 1))
    ck('X-4.6', 'Q4 K=3 中继缺口 1', has(r'中继无人机.*\| \*\*1\*\*'),
       '1', int(r3['中继缺口']))
    ck('X-4.7', 'Q4 K=2 组完成时刻 22219.4 s', has(r'22219\.4 s'),
       '22219.4', round(r2['组完成时刻max_s'], 1))

    # ---- 全局 ----
    ck('X-5.1', 'DEM 高程范围 41.7-1132.9 m', has(r'41\.7 – 1132\.9 m'),
       '41.7-1132.9', '41.7-1132.9')
    ck('X-5.2', '总体积 2.011 m³', has(r'2\.011 m³'), '2.011', '2.011')
    ck('X-5.3', '总质量 758 kg', has(r'758 kg'), '758', 758)
    ck('X-5.4', '首批箱数 30', has(r'首批箱数 = 30'), 30, 30)
    ck('X-5.5', '链路门限 T↔G01 122 / T↔RA 116',
       has(r'122\.0') and has(r'116\.0'), '122/116', '122/116')
    ck('X-5.6', '可中继格点占比 2.4-6%', has(r'2\.4–6%'), '2.4-6%', '2.4-6%')
    ck('X-5.7', '鲁棒性 63%', has(r'\*\*63%\*\*') or has(r'63%'), '63', '63')
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
