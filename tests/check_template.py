# -*- coding: utf-8 -*-
"""提交模板列名/顺序对齐检查（T-5.1 / T-5.2）。

逐字符比对 out/*.csv 与 结果提交模板.xlsx 的列名与顺序。
"""
import os, sys
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, '结果提交模板.xlsx')
OUT = os.path.join(ROOT, 'out')
MAP = {
    'Q1_单点组批.csv': 'Q1_单点组批',
    'Q2_运输架次.csv': 'Q2_运输架次',
    'Q2_逐箱交付.csv': 'Q2_逐箱交付',
    'Q3_中继架次.csv': 'Q3_中继架次',
    'Q3_通信保障.csv': 'Q3_通信保障',
    'Q4_分区配置.csv': 'Q4_分区配置',
}
# 模板中部分列在结果中拆分为更细的列（如"往返时间（s）"、"能耗"），允许的等价别名
ALIAS = {
    'Q1_单点组批': {},
    'Q2_运输架次': {},
    'Q2_逐箱交付': {},
    'Q3_中继架次': {},
    'Q4_分区配置': {'K（2或3）': 'K（2或3）'},
}

fails = []
print("=" * 80)
print("提交模板对齐检查（T-5.1）")
print("=" * 80)
for fn, sheet in MAP.items():
    p = os.path.join(OUT, fn)
    if not os.path.exists(p):
        fails.append((fn, '文件缺失'))
        print("[FAIL] %-22s 文件缺失" % fn)
        continue
    tpl = pd.read_excel(TPL, sheet_name=sheet, nrows=0)
    tcols = list(tpl.columns)
    d = pd.read_csv(p, nrows=0)
    dcols = list(d.columns)
    if dcols == tcols:
        print("[ OK ] %-22s 列名与顺序完全一致（%d 列）" % (fn, len(tcols)))
    else:
        miss = [c for c in tcols if c not in dcols]
        extra = [c for c in dcols if c not in tcols]
        order_ok = [c for c in dcols if c in tcols] == [c for c in tcols if c in dcols]
        detail = []
        if miss:
            detail.append('缺列 %s' % miss)
        if extra:
            detail.append('多列 %s' % extra)
        if miss or extra:
            fails.append((fn, '; '.join(detail)))
            print("[FAIL] %-22s %s" % (fn, '; '.join(detail)))
        elif not order_ok:
            fails.append((fn, '列顺序不一致'))
            print("[FAIL] %-22s 列顺序不一致" % fn)
            print("        模板: %s" % tcols)
            print("        结果: %s" % dcols)
        else:
            print("[ OK ] %-22s 列名一致（顺序不同）" % fn)

print()
if fails:
    print("对齐检查：%d 项不合格" % len(fails))
    for f, r in fails:
        print("  - %s: %s" % (f, r))
    sys.exit(1)
print("对齐检查：全部通过")
