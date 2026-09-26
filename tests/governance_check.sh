#!/usr/bin/env bash
# 治理硬约束检查（对应 P-05 / D13、C7、C8）
# 用法: bash tests/governance_check.sh
# 退出码 0 = 全部通过；1 = 有违规
set -u
cd "$(dirname "$0")/.."
fail=0

say() { printf '%s\n' "$*"; }
ok()  { printf '  [OK]   %s\n' "$*"; }
bad() { printf '  [FAIL] %s\n' "$*"; fail=1; }

say "=== G-01  verify.py 不得 import 任何求解模块 (P-05 / D13) ==="
if [ -f code/verify.py ]; then
  if grep -nE '^[[:space:]]*(import|from)[[:space:]]+(q1_|q2_|q3_|q4_)' code/verify.py; then
    bad "verify.py 导入了求解模块，独立性被破坏"
  # P-07 修复：同时封堵 importlib 动态导入绕过
  elif grep -nE 'import_module[[:space:]]*\(|__import__[[:space:]]*\(' code/verify.py \
       | grep -qE 'q1_|q2_|q3_|q4_'; then
    bad "verify.py 通过 importlib/__import__ 动态导入求解模块（P-07）"
  else
    ok "verify.py 只依赖 core.py / 标准库（含 importlib 绕过检查，P-07 已封堵）"
  fi
else
  say "  [skip] code/verify.py 尚未创建（B7 待办）"
fi

say "=== G-02  求解模块不得重写物理公式 (PLAN §3.1 唯一口径) ==="
# 求解模块中出现 3.6e6 / 32.4 / 20*log10 / 0.65* 等物理常数即视为重写
# comm_geo.py 由 C4 修复纳入：门限半径必须由 core 的链路预算反推
for f in code/q1_grouping.py code/q2_schedule.py code/q3_joint.py code/q4_partition.py code/comm_geo.py; do
  [ -f "$f" ] || { say "  [skip] $f 尚未创建"; continue; }
  if grep -nE '3\.6e6|32\.4[^0-9]|20[[:space:]]*\*[[:space:]]*math\.log10|0\.65[[:space:]]*\*[[:space:]]*\(?0\.90' "$f"; then
    bad "$f 出现物理常数，应由 core.py 提供"
  else
    ok "$f 无物理常数硬编码"
  fi
done

say "=== G-03  唯一物理常数源 ==="
n=$(grep -rlE '^[[:space:]]*(G|CABIN|CLEARANCE|F_MHZ|L_OBS|P_SENS|M_FADE)[[:space:]]*=' code/ 2>/dev/null | grep -v '^code/core.py$' | wc -l | tr -d ' ')
if [ "$n" -eq 0 ]; then ok "物理常数仅定义于 code/core.py"; else bad "有 $n 个文件重复定义物理常数"; fi
# 派生物理量：门限半径 / 链路余量不得写成字面量（C4）
# 判定右值是否为**纯数字字面量**（允许 1.0e4 与行尾注释）；形如
#   GW_RANGE_M = 1000.0 * C.free_space_range_km(GW_LMAX)
# 是合法的派生表达式（单位换算 × core 的函数），不构成"手抄常量"，故不匹配。
LIT='^[[:space:]]*(GW_RANGE_M|RA_RANGE_M|L_RA_MAX|L_RB_MAX)[[:space:]]*=[[:space:]]*[0-9][0-9_.eE+-]*[[:space:]]*(#.*)?$'
m=$(grep -rnE "$LIT" code/ 2>/dev/null | wc -l | tr -d ' ')
if [ "$m" -eq 0 ]; then ok "通信门限半径/链路余量均由 core 派生，无手抄字面量"; else grep -rnE "$LIT" code/; bad "有 $m 处手抄的通信门限常量"; fi

say "=== G-04  λ 变更审计：effectiveness_rubric.md 中的 λ 必须与审计记录一致 ==="
# 从 rubric 的公式定义行取当前 λ
lam_rubric=$(grep -E 'C_复杂度' -A0 -B0 spec/effectiveness_rubric.md >/dev/null 2>&1; \
             grep -oE 'λ[[:space:]]*=[[:space:]]*0\.[0-9]+' spec/effectiveness_rubric.md | head -1 | grep -oE '0\.[0-9]+')
# 从账本的“变更”行取定稿 λ（只认 λ-AUDIT 块中的 "<旧> → <新>"）
lam_ledger=$(grep -E '^\| 变更 \|' spec/iteration_ledger.md | head -1 | grep -oE '→[[:space:]]*\*\*0\.[0-9]+\*\*' | grep -oE '0\.[0-9]+')
if [ -z "$lam_rubric" ]; then bad "在 effectiveness_rubric.md 找不到 λ 定义";
elif [ -z "$lam_ledger" ]; then bad "在 iteration_ledger.md 找不到 λ-AUDIT 的定稿记录（需 '<旧> → **<新>**' 格式）";
elif [ "$lam_rubric" = "$lam_ledger" ]; then ok "λ=$lam_rubric 一致（rubric 与 λ-AUDIT 定稿相符）";
else bad "λ 不一致：rubric=$lam_rubric, λ-AUDIT 定稿=$lam_ledger"; fi

say "=== G-05  账本每轮记录必须含 §1–§8 八组字段 (C7) ==="
rounds=$(grep -cE '^### IT-[0-9]+' spec/iteration_ledger.md)
fields=$(grep -cE '^#### [0-8]\.' spec/iteration_ledger.md)
if [ "$rounds" -eq 0 ]; then bad "账本无任何轮次记录";
else
  need=$((rounds * 8))
  if [ "$fields" -ge "$need" ]; then ok "$rounds 轮 × 8 组 = $fields 组字段齐备";
  else bad "字段不足：$rounds 轮需 $need 组，实际 $fields 组"; fi
fi

say "=== G-06  问题注册表计数自洽 ==="
raised=$(grep -cE '^\| P-[0-9]+ \| IT-' spec/problem_registry.md)
open_=$(grep -E '^\| P-[0-9]+ \| IT-' spec/problem_registry.md | grep -vc '已裁决\|已取代\|不可判定')
say "  N_raised=$raised  N_open=$open_"
if [ "$open_" -eq 0 ]; then ok "无未决问题"; else say "  [note] 有 $open_ 个未决问题（B10 前须清零）"; fi

say ""
if [ "$fail" -eq 0 ]; then say "治理检查：全部通过"; else say "治理检查：存在违规项"; fi
exit $fail
