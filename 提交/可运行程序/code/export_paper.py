# -*- coding: utf-8 -*-
"""B11 论文导出：Markdown -> HTML -> DOCX（textutil）/ PDF（Word 或 qlmanage）。

无外部依赖（不依赖 pandoc / LaTeX / reportlab）：
  - 自带 Markdown 子集解析器（标题/表格/列表/代码/引用/粗斜体/行内代码）
  - 自带 LaTeX -> Unicode 数学转换（本论文的数学均为 Unicode 可表达的行内式）
  - 图片以 data URI 内嵌，保证 DOCX 可脱离相对路径
"""
from __future__ import annotations
import os, re, sys, base64, html as _html, subprocess, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER = os.path.join(ROOT, 'paper', 'main.md')
OUTDIR = os.path.join(ROOT, 'paper', 'export')
os.makedirs(OUTDIR, exist_ok=True)


# ---------------------------------------------------------------------------
# LaTeX -> Unicode（覆盖本论文用到的全部构造）
# ---------------------------------------------------------------------------
_SUP = str.maketrans('0123456789+-=()n', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ')
_SUB = str.maketrans('0123456789+-=()aeioxhklmnpst',
                     '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₒₓₕₖₗₘₙₚₛₜ')

# 命令 -> Unicode 映射（键为裸命令名，不含反斜杠）
CMD = {}
for _c, _r in [('leftrightarrow', '↔'), ('rightarrow', '→'), ('Rightarrow', '⇒'),
               ('mapsto', '↦'), ('uparrow', '↑'), ('downarrow', '↓'), ('to', '→'),
               ('subseteq', '⊆'), ('cup', '∪'), ('bigcup', '⋃'), ('cap', '∩'),
               ('varnothing', '∅'), ('emptyset', '∅'), ('notin', '∉'), ('in', '∈'),
               ('cdot', '·'), ('times', '×'), ('div', '÷'),
               ('leq', '≤'), ('geq', '≥'), ('le', '≤'), ('ge', '≥'), ('ne', '≠'),
               ('neq', '≠'), ('approx', '≈'), ('equiv', '≡'),
               ('sum', 'Σ'), ('prod', 'Π'), ('int', '∫'), ('sqrt', '√'),
               ('infty', '∞'), ('partial', '∂'),
               ('max', 'max'), ('min', 'min'), ('log', 'log'), ('exp', 'exp'),
               ('cos', 'cos'), ('sin', 'sin'),
               ('Lambda', 'Λ'), ('Delta', 'Δ'), ('Omega', 'Ω'), ('Gamma', 'Γ'),
               ('Theta', 'Θ'), ('Pi', 'Π'), ('Sigma', 'Σ'),
               ('lambda', 'λ'), ('delta', 'δ'), ('rho', 'ρ'), ('eta', 'η'),
               ('tau', 'τ'), ('phi', 'φ'), ('varphi', 'φ'), ('sigma', 'σ'),
               ('theta', 'θ'), ('omega', 'ω'), ('mu', 'μ'), ('alpha', 'α'),
               ('beta', 'β'), ('gamma', 'γ'), ('pi', 'π'), ('epsilon', 'ε'),
               ('varepsilon', 'ε'), ('psi', 'ψ'), ('zeta', 'ζ')]:
    CMD[_c] = _r

# 纯丢弃命令
DROP = {'left', 'right', 'mathnormal', 'displaystyle', 'limits', 'nolimits',
        'quad', 'qquad', 'Big', 'big', 'Bigg', 'bigg', 'Bigl', 'Bigr', 'bigl',
        'bigr', 'bar'}
# 字符型转义
CHAR_ESC = {',': ' ', ';': ' ', '!': '', ' ': ' ', ':': ' ', '>': ' ',
            '{': None, '}': None, '\\': '  ;  '}   # None 表示保留花括号字符
# 需要读取 {} 参数并保留内容的命令
KEEP_ARG = {'text', 'mathrm', 'mathbf', 'mathit', 'underbrace', 'overline',
            'sqrt', 'boxed', 'operatorname'}
# 需要读取 {} 参数并生成 (a)/(b) 的命令
FRAC_ARG = {'frac', 'dfrac', 'tfrac', 'cfrac'}


def latex_to_unicode(tex: str) -> str:
    """LaTeX -> Unicode（分词器实现，不依赖正则替换顺序）。

    单遍扫描：
      1. 反斜杠 -> 读命令名/转义字符
      2. 花括号 -> 结构括号（记录并在末尾清理）
      3. ^ / _ -> 上/下标（支持 {..} 与单字符两种形式）
    """
    out = []
    i = 0
    n = len(tex)
    while i < n:
        c = tex[i]
        if c == '\\':
            j = i + 1
            if j < n and not tex[j].isalpha():
                ch = tex[j]
                r = CHAR_ESC.get(ch, ch)
                if r is not None:
                    out.append(r)
                i = j + 1
                continue
            k = j
            while k < n and tex[k].isalpha():
                k += 1
            name = tex[j:k]
            i = k
            if name in FRAC_ARG:
                num, i = read_group(tex, i)
                den, i = read_group(tex, i)
                out.append('(%s)/(%s)' % (latex_to_unicode(num),
                                          latex_to_unicode(den)))
                continue
            if name in KEEP_ARG:
                arg, i = read_group(tex, i)
                out.append(latex_to_unicode(arg))
                continue
            if name in ('begin', 'end'):
                # \begin{cases} / \end{cases} 等：读环境名后丢弃，用 CASE[ ... ] 保留语义
                env, i = read_group(tex, i)
                if name == 'begin' and env.strip() == 'cases':
                    out.append('{')
                elif name == 'end' and env.strip() == 'cases':
                    out.append('}')
                continue
            if name in DROP:
                continue
            out.append(CMD.get(name, name))
            continue
        if c == '^' or c == '_':
            i += 1
            arg, i = read_group(tex, i, single_ok=True)
            sub = latex_to_unicode(arg)
            out.append(sub.translate(_SUP if c == '^' else _SUB))
            continue
        if c in '{}':
            i += 1
            continue
        out.append(c)
        i += 1
    return re.sub(r'[ \t]+', ' ', ''.join(out)).strip()


def read_group(s: str, i: int, single_ok: bool = False):
    """从 s[i] 读一个参数：{...}（配平）或单字符。返回 (内容, 下一位置)。"""
    n = len(s)
    while i < n and s[i] == ' ':
        i += 1
    if i < n and s[i] == '{':
        depth = 0
        j = i
        while j < n:
            if s[j] == '{':
                depth += 1
            elif s[j] == '}':
                depth -= 1
                if depth == 0:
                    return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], n
    if single_ok and i < n:
        # 参数也可能是命令（如 ^\uparrow、_\eta），须整段取命令
        if s[i] == chr(92):
            j = i + 1
            if j < n and not s[j].isalpha():
                return s[i:j + 1], j + 1
            k = j
            while k < n and s[k].isalpha():
                k += 1
            return s[i:k], k
        return s[i], i + 1
    if i < n:
        return s[i], i + 1
    return '', i


# ---------------------------------------------------------------------------
# Markdown -> HTML（子集：标题/表格/列表/代码/引用/分割线/段落）
# ---------------------------------------------------------------------------
def inline(md: str, img_base: str) -> str:
    # 保护行内代码
    codes = []
    def stash(m):
        codes.append(m.group(1))
        return '\x00%d\x00' % (len(codes) - 1)
    md = re.sub(r'`([^`]+)`', stash, md)

    # 数学：$$...$$ 与 $...$
    md = re.sub(r'\$\$(.+?)\$\$', lambda m: '<span class="math">%s</span>'
                % _html.escape(latex_to_unicode(m.group(1))), md, flags=re.S)
    md = re.sub(r'\$([^$]+)\$', lambda m: '<span class="math">%s</span>'
                % _html.escape(latex_to_unicode(m.group(1))), md)

    md = _html.escape(md, quote=False)
    md = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)',
                lambda m: '<img alt="%s" src="%s">' % (m.group(1),
                '../' + m.group(2) if m.group(2).startswith('figs/') else m.group(2)), md)
    md = re.sub(r'\[([^\]]+)\]\(([^)]+)\)',
                lambda m: '<a href="%s">%s</a>' % (m.group(2), m.group(1)), md)
    md = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', md)
    md = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', md)
    md = md.replace('~~', '')          # 删除线标记不渲染
    for i, c in enumerate(codes):
        md = md.replace('\x00%d\x00' % i,
                        '<code>%s</code>' % _html.escape(c))
    return md


def md_to_html(md: str) -> str:
    lines = md.split('\n')
    out = []
    i = 0
    in_code = False
    code_buf = []
    while i < len(lines):
        ln = lines[i]
        # 代码块
        if ln.strip().startswith('```'):
            if not in_code:
                in_code = True; code_buf = []
            else:
                in_code = False
                out.append('<pre><code>%s</code></pre>'
                           % _html.escape('\n'.join(code_buf)))
            i += 1
            continue
        if in_code:
            code_buf.append(ln); i += 1; continue
        # 表格
        if ln.strip().startswith('|') and i + 1 < len(lines) and \
           re.match(r'^\s*\|[\s:|-]+\|\s*$', lines[i + 1]):
            hdr = [c.strip() for c in ln.strip().strip('|').split('|')]
            aligns = [('right' if c.strip().endswith(':') and c.strip().startswith(':')
                       else 'center' if c.strip().startswith(':') and c.strip().endswith(':')
                       else 'right' if c.strip().endswith(':') else 'left')
                      for c in lines[i + 1].strip().strip('|').split('|')]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            t = ['<table><thead><tr>']
            for h, al in zip(hdr, aligns):
                t.append('<th style="text-align:%s">%s</th>' % (al, inline(h, '')))
            t.append('</tr></thead><tbody>')
            for r in rows:
                t.append('<tr>')
                for k, c in enumerate(r):
                    al = aligns[k] if k < len(aligns) else 'left'
                    t.append('<td style="text-align:%s">%s</td>' % (al, inline(c, '')))
                t.append('</tr>')
            t.append('</tbody></table>')
            out.append(''.join(t))
            continue
        # 标题
        m = re.match(r'^(#{1,6})\s+(.*)$', ln)
        if m:
            lvl = len(m.group(1))
            out.append('<h%d>%s</h%d>' % (lvl, inline(m.group(2), ''), lvl))
            i += 1
            continue
        # 分割线
        if re.match(r'^\s*(---|\*\*\*|___)\s*$', ln):
            out.append('<hr>'); i += 1; continue
        # 引用
        if ln.strip().startswith('>'):
            buf = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                buf.append(lines[i].strip().lstrip('>').strip()); i += 1
            out.append('<blockquote>%s</blockquote>'
                       % '<br>'.join(inline(b, '') for b in buf))
            continue
        # 列表
        if re.match(r'^\s*([-*+]|\d+\.)\s+', ln):
            ordered = bool(re.match(r'^\s*\d+\.\s+', ln))
            buf = []
            while i < len(lines) and re.match(r'^\s*([-*+]|\d+\.)\s+', lines[i]):
                item = re.sub(r'^\s*([-*+]|\d+\.)\s+', '', lines[i])
                buf.append('<li>%s</li>' % inline(item, ''))
                i += 1
            tag = 'ol' if ordered else 'ul'
            out.append('<%s>%s</%s>' % (tag, ''.join(buf), tag))
            continue
        # 空行
        if not ln.strip():
            i += 1; continue
        # 段落（合并连续行）
        buf = [ln]
        i += 1
        while i < len(lines) and lines[i].strip() and \
                not re.match(r'^(#{1,6}\s|\s*([-*+]|\d+\.)\s|\s*>|\s*\||```)', lines[i]) \
                and not re.match(r'^\s*(---|\*\*\*|___)\s*$', lines[i]):
            buf.append(lines[i]); i += 1
        out.append('<p>%s</p>' % inline(' '.join(buf), ''))
    return '\n'.join(out)


CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
@media print {
  body { margin: 0; }
  h1, h2, h3, h4 { break-after: avoid; page-break-after: avoid; }
  table, pre, blockquote, img { break-inside: avoid; page-break-inside: avoid; }
  table { page-break-inside: auto; }
  tr { break-inside: avoid; page-break-inside: avoid; }
  thead { display: table-header-group; }
  a { color: #000; text-decoration: none; }
  .pagebreak { break-before: page; page-break-before: always; }
}
body { font-family: "Songti SC","SimSun","STSong",serif; font-size: 10.5pt;
       line-height: 1.75; margin: 2.2cm 2.0cm; color:#000;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1 { font-size: 18pt; text-align:center; margin: 0 0 14pt 0; font-weight:bold;
     break-after: avoid; }
h2 { font-size: 14pt; margin: 16pt 0 8pt 0; border-bottom:1px solid #999;
     padding-bottom:3pt; break-after: avoid; }
h3 { font-size: 12pt; margin: 13pt 0 6pt 0; break-after: avoid; }
h4 { font-size: 11pt; margin: 11pt 0 4pt 0; break-after: avoid; }
p { margin: 5pt 0; text-align: justify; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9pt; }
th, td { border: 1px solid #666; padding: 3pt 5pt; vertical-align: top; }
th { background: #eef2f7; font-weight: bold; }
code { font-family: "Menlo","Consolas",monospace; font-size: 9pt;
       background: #f4f4f4; padding: 0 2pt; }
pre { background: #f6f6f6; border: 1px solid #ddd; padding: 6pt;
      font-size: 8.5pt; white-space: pre-wrap; }
blockquote { border-left: 3px solid #999; margin: 8pt 0; padding: 4pt 10pt;
             background: #fafafa; }
.math { font-family: "Cambria Math","Times New Roman",serif; font-style: italic; }
img { max-width: 100%; height: auto; display:block; margin: 8pt auto;
      break-inside: avoid; }
hr { border: none; border-top: 1px solid #bbb; margin: 12pt 0; }
ol, ul { margin: 5pt 0 5pt 18pt; }
li { margin: 2pt 0; }
"""

# 图片引用需要在导出时展开为绝对路径（README 里说明用浏览器打开）
FIG_TITLES = {
    'fig1_routes.png': '图1  问题一/三 运输路线与通信需求',
    'fig2_gantt.png': '图2  问题二 运输架次甘特图与逐箱交付时刻分布',
    'fig3_resources.png': '图3  问题一机型分布、问题四资源需求与 ρ 灵敏度',
    'fig4_comm.png': '图4  问题三 中继悬停位置与通信保障时长占比',
    'fig5_partition.png': '图5  问题四 任务分区方案对比',
}


def main():
    md = open(PAPER, encoding='utf-8').read()
    body = md_to_html(md)
    # 追加图表附录（正文中未逐张插图，故在文末集中展示）
    figs = []
    figdir = os.path.join(ROOT, 'paper', 'figs')
    if os.path.isdir(figdir):
        figs.append('<h2>附录 C：图表</h2>')
        for fn in sorted(os.listdir(figdir)):
            if not fn.endswith('.png'):
                continue
            cap = FIG_TITLES.get(fn, fn)
            figs.append('<figure style="margin:14pt 0;text-align:center">'
                        '<img src="../figs/%s" style="max-width:100%%">'
                        '<figcaption style="font-size:9pt;color:#333;margin-top:4pt">%s</figcaption>'
                        '</figure>' % (fn, cap))
    body = body + '\n'.join(figs)
    doc = ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
           '<title>山区洪涝灾害下无人机运输与通信协同优化</title>'
           '<style>%s</style></head><body>%s</body></html>' % (CSS, body))
    html_path = os.path.join(OUTDIR, 'main.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(doc)
    print("HTML ->", html_path, "%.1f KB" % (os.path.getsize(html_path) / 1024))

    # DOCX 由 code/export_docx.py 生成（textutil 不内嵌图片，会丢失全部插图，故不用）
    print("DOCX：请运行 code/export_docx.py（内嵌图片版）")

    # PDF via qlmanage（无 Chrome/LaTeX 时的回退；qlmanage 为 macOS 专属，非 macOS 跳过）
    if sys.platform == 'darwin':
        pdf = os.path.join(OUTDIR, '论文.pdf')
        r2 = subprocess.run(['qlmanage', '-t', '-s', '1600', '-o', OUTDIR, html_path],
                            capture_output=True, text=True)
        cand = os.path.join(OUTDIR, os.path.basename(html_path) + '.png')
        if os.path.exists(cand):
            print("预览图 ->", cand)
    else:
        print("PDF：非 macOS，跳过 qlmanage（PDF 走 pandoc/浏览器打印路径）")
    return html_path


if __name__ == '__main__':
    main()
