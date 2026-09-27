# -*- coding: utf-8 -*-
"""导出产物结构验证：HTML 打印就绪性 + DOCX 可解析性 + 图表引用完整性。

因本机 Word 自动化未授权、且无 pandoc/LaTeX，PDF 采用"浏览器打印"路径，
故此处验证**该路径所需的结构条件是否全部满足**，而非伪造 PDF。
"""
import os, re, sys, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXP = os.path.join(ROOT, 'paper', 'export')
HTML = os.path.join(EXP, 'main.html')
DOCX = os.path.join(EXP, '山区洪涝灾害下无人机运输与通信协同优化.docx')
FIGDIR = os.path.join(ROOT, 'paper', 'figs')
R = []
def ck(cid, desc, ok, detail=''):
    R.append((cid, desc, bool(ok), detail))
    print('%-8s %-52s %s %s' % (cid, 'OK' if ok else 'FAIL', desc, detail))

# ---------------------------------------------------------------------------
# pandoc 导出校验（T-pandoc）：公式原生渲染 + 图片内嵌 + 结构完整
# ---------------------------------------------------------------------------
def check_pandoc():
    import zipfile
    PD = os.path.join(EXP, 'pandoc')
    print('\n--- pandoc 导出链路 ---')
    if not os.path.isdir(PD):
        ck('P-0', 'pandoc 导出目录存在', False, '请先运行 code/export_pandoc.py')
        return
    ck('P-0', 'pandoc 导出目录存在', True, PD)
    figs = [f for f in os.listdir(FIGDIR) if f.endswith('.png')]

    # HTML
    h = os.path.join(PD, '论文_pandoc.html')
    if os.path.exists(h):
        html = open(h, encoding='utf-8').read()
        ck('P-1', 'HTML 含 MathJax（公式原生渲染）', 'MathJax' in html or 'mathjax' in html, '')
        n_img = html.count('data:image')
        ck('P-2', 'HTML 内嵌图片数 == 图总数', n_img == len(figs),
           '内嵌 %d / 图 %d' % (n_img, len(figs)))
        ck('P-3', 'HTML 表格数 >= 30', html.count('<table') >= 30, '%d 个' % html.count('<table'))
    else:
        ck('P-1', 'HTML 存在', False, '')

    # DOCX：公式必须是 Word 原生 OMML
    d = os.path.join(PD, '论文_pandoc.docx')
    if os.path.exists(d):
        z = zipfile.ZipFile(d)
        media = [n for n in z.namelist() if 'media' in n]
        xml = z.read('word/document.xml').decode('utf-8', 'ignore')
        n_omml = xml.count('<m:oMath')
        ck('P-4', 'DOCX 内嵌图片数 == 图总数', len(media) == len(figs),
           '内嵌 %d / 图 %d' % (len(media), len(figs)))
        ck('P-5', 'DOCX 含 Word 原生公式（OMML）>= 100 个', n_omml >= 100,
           '%d 个（非纯文本公式）' % n_omml)
    else:
        ck('P-4', 'DOCX 存在', False, '')

    # LaTeX
    t = os.path.join(PD, '论文.tex')
    if os.path.exists(t):
        tex = open(t, encoding='utf-8').read()
        ck('P-6', 'LaTeX 源存在且含图片引用', tex.count('\\includegraphics') >= len(figs),
           'includegraphics %d 处' % tex.count('\\includegraphics'))
        ck('P-7', 'LaTeX 源含 CJK 字体设置', 'CJK' in tex or 'ctex' in tex, '')
    else:
        ck('P-6', 'LaTeX 源存在', False, '')


def main():
    ck('E-1', 'HTML 存在', os.path.exists(HTML), '')
    ck('E-2', 'DOCX 存在', os.path.exists(DOCX), '')
    if not os.path.exists(HTML):
        return 1
    h = open(HTML, encoding='utf-8').read()
    # 打印就绪条件
    ck('E-3', '@page A4 页面规则', '@page' in h and 'A4' in h, '')
    ck('E-4', '分页避让规则（表格/图/标题）',
       'break-inside: avoid' in h and 'break-after: avoid' in h, '')
    ck('E-5', '表头跨页重复', 'table-header-group' in h, '')
    ck('E-6', '中文字体声明（宋体系）',
       any(f in h for f in ('Songti SC', 'SimSun', 'STSong')), '')
    ck('E-7', '公式样式存在（.math）', '.math' in h, '')
    ck('E-8', '打印色彩保真', 'print-color-adjust' in h, '')
    # 结构
    n_h2 = len(re.findall(r'<h2>', h))
    n_tbl = len(re.findall(r'<table>', h))
    n_p = len(re.findall(r'<p>', h))
    ck('E-9', '章节数 ≥ 8', n_h2 >= 8, '%d 个 h2' % n_h2)
    ck('E-10', '表格数 ≥ 20', n_tbl >= 20, '%d 个表格' % n_tbl)
    # 注意：本论文大量内容以列表项与表格承载，故以"总文本块数"为指标更合理
    n_li = len(re.findall(r'<li>', h))
    n_cell = len(re.findall(r'<td', h))
    blocks = n_p + n_li + n_cell
    ck('E-11', '总文本块数（段落+列表项+表格单元）≥ 300', blocks >= 300,
       '段落 %d + 列表 %d + 单元 %d = %d' % (n_p, n_li, n_cell, blocks))
    # 图表引用 —— 关键修复：不仅查 HTML 是否含 <img>，更要查**正文是否引用了图**
    figs = [f for f in sorted(os.listdir(FIGDIR)) if f.endswith('.png')]
    missing = [f for f in figs if ('../figs/%s' % f) not in h]
    ck('E-12', '全部图被 HTML 引用', not missing,
       '引用 %d/%d' % (len(figs) - len(missing), len(figs)))
    ck('E-13', '图表标题（figcaption）齐备',
       len(re.findall(r'<figcaption', h)) >= len(figs),
       '%d 个' % len(re.findall(r'<figcaption', h)))
    # E-16（新增，修复治理盲区）：正文 MD 中必须出现「图n」引用，且引用数 >= 正文所配图数
    md = open(os.path.join(ROOT, 'paper', 'main.md'), encoding='utf-8').read()
    # 正文 = 参考文献章之前
    body = md[:md.index('## 九、参考文献')] if '## 九、参考文献' in md else md
    n_inline = len(re.findall(r'!\[[^\]]*\]\(figs/', body))
    n_ref = len(set(re.findall(r'\*\*图\s*(\d+)', body)))
    ck('E-16', '正文含图片嵌入（![] 形式）≥ 5 张', n_inline >= 5,
       '正文嵌入 %d 张' % n_inline)
    ck('E-17', '正文含「图n」文字引用，编号连续 1..N', n_ref >= n_inline and
       set(range(1, n_inline + 1)) <= set(int(x) for x in re.findall(r'\*\*图\s*(\d+)', body)),
       '正文引用图号 %s' % sorted(int(x) for x in
                                  set(re.findall(r'\*\*图\s*(\d+)', body))))
    # E-18：DOCX 必须内嵌图片（此前 textutil 导出会丢失全部图片）
    if os.path.exists(DOCX):
        import zipfile
        z = zipfile.ZipFile(DOCX)
        media = [n for n in z.namelist() if 'media' in n and n.lower().endswith('.png')]
        ck('E-18', 'DOCX 内嵌图片数 == 全部图数', len(media) == len(figs),
           '内嵌 %d 张（图总数 %d）' % (len(media), len(figs)))
    # 数学渲染残留检查（不应出现裸 LaTeX 命令）
    bad = re.findall(r'<span class="math">[^<]*\\(?:[A-Za-z]{2,})', h)
    ck('E-14', '公式中无未转换的 LaTeX 命令残留', not bad,
       ('残留 %d 处: %s' % (len(bad), bad[:3])) if bad else '')
    # DOCX 可解析（跨平台：用 zipfile 读 document.xml，替代 macOS 专属的 textutil）
    if os.path.exists(DOCX):
        import zipfile as _zipfile
        _xml = _zipfile.ZipFile(DOCX).read('word/document.xml').decode('utf-8', 'ignore')
        txt = ''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', _xml))
        ck('E-19', 'DOCX 可解析（document.xml 文本量充足）', len(txt) > 10000,
           '%d 字符' % len(txt))
        for kw in ('摘要', '问题重述', '模型建立与求解', '适用条件', '参考文献'):
            ck('E-20.' + kw, 'DOCX 含章节「%s」' % kw, kw in txt, '')
    check_pandoc()
    npass = sum(1 for *_, ok, _ in R if ok)
    print('\n' + '=' * 80)
    print('导出验证：%d/%d 通过' % (npass, len(R)))
    for cid, d, ok, det in R:
        if not ok:
            print('  FAIL %s %s %s' % (cid, d, det))
    return 0 if npass == len(R) else 1

if __name__ == '__main__':
    sys.exit(main())

