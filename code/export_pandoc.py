# -*- coding: utf-8 -*-
"""论文导出主链路：Markdown -> PDF（xelatex 直出，不经过 HTML）/ DOCX / LaTeX

与 code/export_paper.py 的分工（历史遗留）：
  - export_paper.py：自实现的 Markdown->HTML 转换器（已弃用，仅保留 latex_to_unicode 供 DOCX）
  - 本文件（export_pandoc.py）：使用 pandoc，直接由 main.md 编译 PDF（xelatex），
    并产出 Word 原生公式 DOCX 与 LaTeX 源。

PDF 由 pandoc 直接读 main.md 经 xelatex 编译，**全程不经过 HTML**，因此天然无
HTML 标签泄漏；Unicode 符号（✅❌≤≥⇒↔→ρλ 等）由 paper/pandoc_header.tex 的
回退映射保证字形完整。

pandoc 由 pip 包 pypandoc_binary 提供；xelatex 来自本机 TeX Live。
"""
from __future__ import annotations
import os, sys, shutil, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER = 'main.md'
TITLE = '山区洪涝灾害下无人机运输与通信协同优化'
PAPERDIR = os.path.join(ROOT, 'paper')           # pandoc 必须在此目录下运行（图片相对路径 figs/）
EXPORT = os.path.join(PAPERDIR, 'export')
OUTDIR = os.path.join(EXPORT, 'pandoc')
HEADER = os.path.join(PAPERDIR, 'pandoc_header.tex')
PDF_OUT = os.path.join(EXPORT, '论文.pdf')
os.makedirs(OUTDIR, exist_ok=True)

# LaTeX 的 CJK 主字体按平台选择：Windows 用宋体（SimSun），macOS 用宋体-简（Songti SC）
CJK_FONT = 'SimSun' if sys.platform == 'win32' else 'Songti SC'


def find_tool(name):
    """优先用 pypandoc 定位 pandoc；xelatex 用 shutil.which。返回可执行路径或 None。"""
    if name == 'pandoc':
        try:
            import pypandoc
            p = pypandoc.get_pandoc_path()
            if p and not os.path.exists(p) and os.path.exists(p + '.exe'):
                p += '.exe'
            if p and os.path.exists(p):
                return p
        except Exception:
            pass
    return shutil.which(name)


def run(pandoc, args, desc):
    r = subprocess.run([pandoc] + args, capture_output=True, text=True, cwd=PAPERDIR)
    if r.returncode != 0:
        print("  [FAIL] %s\n%s" % (desc, (r.stderr or r.stdout).strip()[-600:]))
        return False
    # xelatex 缺字是「编译成功但 PDF 有空洞」的隐蔽失败，需单独告警
    missing = [l for l in (r.stderr + r.stdout).splitlines()
               if 'Missing character' in l]
    print("  [ OK ] %s%s" % (desc, '（警告：%d 处缺字）' % len(missing) if missing else ''))
    for l in missing[:5]:
        print("         " + l.strip())
    return True


def main():
    pandoc = find_tool('pandoc')
    xelatex = find_tool('xelatex')
    print("=" * 84)
    print("pandoc 导出（PDF 直出 xelatex + DOCX + LaTeX）")
    print("=" * 84)
    print("  pandoc : %s" % pandoc)
    print("  xelatex: %s" % xelatex)
    if not pandoc or not os.path.exists(pandoc):
        print("  pandoc 不可用")
        return 1
    if not xelatex:
        print("  xelatex 不可用（需 TeX Live）")
        return 1

    ok = []
    base = [
        os.path.join(PAPERDIR, PAPER),
        '--from', 'markdown+tex_math_dollars+pipe_tables',
        '--resource-path', PAPERDIR,
        '--metadata', 'title=' + TITLE,
    ]

    # ---- 1) PDF：pandoc -> xelatex 直出（主产物，不经过 HTML）----
    ok.append(run(pandoc, base + [
        '-o', PDF_OUT,
        '--pdf-engine', xelatex,
        '-H', HEADER,
        '-V', 'CJKmainfont=' + CJK_FONT,
        '-V', 'mainfont=Times New Roman',
        '-V', 'monofont=Consolas',
        '-V', 'geometry:a4paper,margin=2.2cm',
        '-V', 'documentclass=extarticle',
        '-V', 'fontsize=10.5pt',
        '--pdf-engine-opt=-interaction=nonstopmode',
    ], 'PDF（xelatex 直出，A4）'))

    # ---- 2) DOCX（公式转 Word 原生 OMML）----
    ok.append(run(pandoc, base + [
        '-o', os.path.join(OUTDIR, '论文_pandoc.docx'),
        '--toc', '--toc-depth=3',
    ], 'DOCX（公式转 Word 原生公式）'))

    # ---- 3) LaTeX（供有 TeX 环境时编译为 PDF）----
    ok.append(run(pandoc, base + [
        '-o', os.path.join(OUTDIR, '论文.tex'),
        '--standalone',
        '-V', 'documentclass=extarticle',
        '-V', 'geometry:a4paper,margin=2.2cm',
        '-V', 'CJKmainfont=' + CJK_FONT,
        '-V', 'fontsize=10.5pt',
    ], 'LaTeX 源（xelatex 可编译）'))

    # ---- 4) 校验产出 ----
    print()
    print("=" * 84)
    print("输出校验")
    print("=" * 84)
    if os.path.exists(PDF_OUT):
        print("  PDF : %.1f KB -> %s" % (os.path.getsize(PDF_OUT) / 1024, PDF_OUT))
    else:
        print("  PDF : 未生成")
    import zipfile
    d = os.path.join(OUTDIR, '论文_pandoc.docx')
    t = os.path.join(OUTDIR, '论文.tex')
    if os.path.exists(d):
        z = zipfile.ZipFile(d)
        media = [n for n in z.namelist() if 'media' in n]
        xml = z.read('word/document.xml').decode('utf-8', 'ignore')
        print("  DOCX: %.1f KB | 内嵌图片=%d | Word 原生公式(oMath)=%d"
              % (os.path.getsize(d) / 1024, len(media), xml.count('<m:oMath')))
    if os.path.exists(t):
        tex = open(t, encoding='utf-8').read()
        print("  LaTeX: %.1f KB | 公式环境=%d | 图片引用=%d"
              % (os.path.getsize(t) / 1024, tex.count('\\begin{equation}') + tex.count('\\['),
                 tex.count('\\includegraphics')))
    print()
    print("  -> %s" % OUTDIR)
    return 0 if all(ok) else 1


if __name__ == '__main__':
    sys.exit(main())
