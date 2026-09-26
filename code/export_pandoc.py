# -*- coding: utf-8 -*-
"""pandoc 导出链路：Markdown -> HTML(MathJax) / DOCX(原生公式) / LaTeX

与 code/export_paper.py 的分工：
  - export_paper.py：自实现的 Markdown->HTML 转换器（无外部依赖，作为后备）
  - 本文件（export_pandoc.py）：使用 pandoc，产出**数学原生渲染**的版本

pandoc 由 pip 包 pypandoc_binary 提供（自包含二进制，版本见运行时输出）。
"""
from __future__ import annotations
import os, sys, shutil, subprocess, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER = 'main.md'
FIGDIR = os.path.join(ROOT, 'paper', 'figs')
EXPORT = os.path.join(ROOT, 'paper', 'export')
OUTDIR = os.path.join(EXPORT, 'pandoc')
os.makedirs(OUTDIR, exist_ok=True)

# pandoc 专属 CSS：A4 打印 + 中文字体 + 表格/图/公式样式
CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: "Songti SC","SimSun",serif; font-size: 10.5pt; line-height: 1.75;
       max-width: 46em; margin: 0 auto; padding: 8mm 0; color:#000; }
h1 { font-size: 18pt; text-align: center; margin: 0 0 14pt 0; }
h2 { font-size: 14pt; margin: 16pt 0 8pt 0; border-bottom: 1px solid #999;
     padding-bottom: 3pt; break-after: avoid; }
h3 { font-size: 12pt; margin: 13pt 0 6pt 0; break-after: avoid; }
h4 { font-size: 11pt; margin: 11pt 0 4pt 0; break-after: avoid; }
p { margin: 5pt 0; text-align: justify; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9pt; }
th, td { border: 1px solid #666; padding: 3pt 5pt; vertical-align: top; }
th { background: #eef2f7; font-weight: bold; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
code { font-family: Menlo,monospace; font-size: 9pt; background: #f4f4f4; }
pre { background: #f6f6f6; border: 1px solid #ddd; padding: 6pt; font-size: 8.5pt;
      white-space: pre-wrap; }
blockquote { border-left: 3px solid #999; margin: 8pt 0; padding: 4pt 10pt;
             background: #fafafa; }
img { max-width: 100%; height: auto; display: block; margin: 8pt auto;
      break-inside: avoid; }
figure { margin: 14pt 0; text-align: center; }
figcaption { font-size: 9pt; color: #333; margin-top: 4pt; }
hr { border: none; border-top: 1px solid #bbb; margin: 12pt 0; }
ol, ul { margin: 5pt 0 5pt 18pt; }
li { margin: 2pt 0; }
"""


def find_pandoc():
    try:
        import pypandoc
        return pypandoc.get_pandoc_path(), pypandoc.get_pandoc_version()
    except Exception:
        p = shutil.which('pandoc')
        return p, 'unknown'


# pandoc 必须在 paper/ 目录下运行：main.md 中的图片路径为 figs/xxx.png（相对 paper/）
PAPERDIR = os.path.join(ROOT, 'paper')


def run(pandoc, args, desc):
    r = subprocess.run([pandoc] + args, capture_output=True, text=True, cwd=PAPERDIR)
    if r.returncode != 0:
        print("  [FAIL] %s\n%s" % (desc, r.stderr.strip()[:400]))
        return False
    print("  [ OK ] %s" % desc)
    return True


def main():
    pandoc, ver = find_pandoc()
    print("=" * 84)
    print("pandoc 导出（pandoc %s）" % ver)
    print("=" * 84)
    print("  pandoc: %s" % pandoc)
    if not pandoc or not os.path.exists(pandoc):
        print("  pandoc 不可用")
        return 1

    css = os.path.join(PAPERDIR, 'paper_pandoc.css')
    with open(css, 'w', encoding='utf-8') as f:
        f.write(CSS)

    ok = []
    # ---- 1) HTML + MathJax（公式由浏览器原生渲染）----
    ok.append(run(pandoc, [
        os.path.join(PAPERDIR, 'main.md'), '-o', os.path.join(OUTDIR, '论文_pandoc.html'),
        '--standalone', '--mathjax',
        '--metadata', 'title=山区洪涝灾害下无人机运输与通信协同优化',
        '--css', 'paper_pandoc.css', '--embed-resources',
        '--resource-path', PAPERDIR,
    ], 'HTML + MathJax（公式原生渲染）'))

    # ---- 2) DOCX（公式转为 Word 原生 OMML）----
    ok.append(run(pandoc, [
        os.path.join(PAPERDIR, 'main.md'), '-o', os.path.join(OUTDIR, '论文_pandoc.docx'),
        '--from', 'markdown+tex_math_dollars+pipe_tables',
        '--resource-path', PAPERDIR,
        '--toc', '--toc-depth=3',
    ], 'DOCX（公式转 Word 原生公式）'))

    # ---- 3) LaTeX（供有 TeX 环境时编译为 PDF）----
    ok.append(run(pandoc, [
        os.path.join(PAPERDIR, 'main.md'), '-o', os.path.join(OUTDIR, '论文.tex'),
        '--standalone',
        '--variable', 'documentclass=article',
        '--variable', 'geometry:margin=2.2cm',
        '--variable', 'CJKmainfont=Songti SC',
        '-V', 'fontsize=10.5pt',
        '--resource-path', PAPERDIR,
    ], 'LaTeX 源（c tex 环境可直接 xelatex 编译）'))

    # ---- 4) 校验产出 ----
    print()
    print("=" * 84)
    print("输出校验")
    print("=" * 84)
    import zipfile
    h = os.path.join(OUTDIR, '论文_pandoc.html')
    d = os.path.join(OUTDIR, '论文_pandoc.docx')
    t = os.path.join(OUTDIR, '论文_пandoc.tex'.replace('п', 'p'))
    if os.path.exists(h):
        html = open(h, encoding='utf-8').read()
        print("  HTML: %.1f KB | MathJax=%s | 内嵌图片=%d | 表格=%d"
              % (os.path.getsize(h) / 1024, 'MathJax' in html,
                 html.count('data:image'), html.count('<table')))
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
