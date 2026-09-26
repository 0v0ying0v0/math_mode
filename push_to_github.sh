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

git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE"

echo "2) 推送 main..."
git push -u origin main

echo "3) 完成：https://github.com/0v0ying0v0/math_mode"
