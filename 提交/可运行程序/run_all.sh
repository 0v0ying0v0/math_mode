#!/usr/bin/env bash
# 一键复现：四问求解 → 图表 → 论文导出 → 全部验证
set -e
set -o pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python"
export MPLCONFIGDIR="${TMPDIR:-/tmp}/mpl"
mkdir -p "$MPLCONFIGDIR"

echo "=========== 1/4 四问求解 ==========="
$PY -B code/q1_grouping.py       | tail -2
$PY -B code/q2_schedule.py       | tail -2
$PY -B code/q3_joint.py          | tail -2
$PY -B code/q4_partition.py      | tail -2

echo "=========== 2/4 分析与图表 ==========="
$PY -B code/gis_analysis.py               | tail -2
$PY -B code/q2_merge_accounting.py        | tail -2
$PY -B code/render.py                     | tail -2

echo "=========== 3/4 论文导出 ==========="
$PY -B code/export_paper.py               | tail -2   # 自实现 HTML（无依赖后备）
$PY -B code/export_docx.py                | tail -2   # DOCX（内嵌图片）
$PY -B code/export_pandoc.py              | tail -4   # pandoc：HTML(MathJax)/DOCX(原生公式)/LaTeX

echo "=========== 4/4 验证套件 ==========="
fail=0
run() { printf "%-40s" "$1"; shift; out=$("$@" 2>&1) && echo "✅ $(echo "$out" | tail -1)" || { echo "❌"; fail=1; }; }

printf "%-40s" "治理硬检查 G-01..G-06:"
bash tests/governance_check.sh >/dev/null 2>&1 && echo "✅ 全部通过" || { echo "❌"; fail=1; }
run "问题验证 V1-V5:"        $PY -B tests/run_verification.py
run "独立校验 A-F + R1-R5:"  $PY -B code/verify.py
run "Q3 通信零中断:"         $PY -B tests/verify_q3_comm.py
run "模板对齐 T-5.1:"        $PY -B tests/check_template.py
run "全局验证 B8:"           $PY -B tests/run_global_verify.py
run "账本结算 B9:"           $PY -B tests/run_settlement.py
run "论文-结果交叉对表 C6:"  $PY -B tests/check_consistency.py
run "导出结构验证:"          $PY -B tests/check_export.py

echo
if [ $fail -eq 0 ]; then echo "全部通过。"; else echo "存在失败项（应全绿，请检查上方输出）。"; fi
echo "PDF：浏览器打开 paper/export/main.html → ⌘P → 存储为 PDF"
