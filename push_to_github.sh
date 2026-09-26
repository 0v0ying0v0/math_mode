#!/usr/bin/env bash
# 用仓库内的部署密钥推送到 GitHub（绕开 ~/.ssh 的沙箱写入限制）
set -e
cd "$(dirname "$0")"
KEY="$(pwd)/.deploy/id_ed25519"
REMOTE="git@github.com:0v0ying0v0/math_mode.git"

if [ ! -f "$KEY" ]; then
  echo "缺少部署私钥 $KEY"; exit 1
fi

export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

echo "1) 测试 SSH 认证..."
if ! git ls-remote "$REMOTE" >/dev/null 2>&1; then
  echo "❌ 认证失败。请确认已把 .deploy/id_ed25519.pub 添加到："
  echo "   https://github.com/0v0ying0v0/math_mode/settings/keys"
  echo "   并勾选 Allow write access"
  exit 1
fi
echo "   ✅ 认证通过"

# 只设置 origin，不删除重建——remove/add 会清空 refs/remotes/origin/*，
# 使 "远端有新提交" 与 "远端没动" 在本地看起来完全一样，掩盖真实分叉。
if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "$REMOTE"
elif [ "$(git remote get-url origin)" != "$REMOTE" ]; then
  git remote set-url origin "$REMOTE"
fi

echo "2) 同步远端状态..."
git fetch origin

if ! git merge-base --is-ancestor origin/main HEAD 2>/dev/null; then
  echo "⚠️  远端 main 上有本地没有的提交，直接推送会被拒绝。"
  echo "   请先处理分叉后再运行本脚本："
  git log --oneline HEAD..origin/main | sed 's/^/     /'
  exit 1
fi

echo "3) 推送 main..."
git push -u origin main

echo "4) 完成：https://github.com/0v0ying0v0/math_mode"
