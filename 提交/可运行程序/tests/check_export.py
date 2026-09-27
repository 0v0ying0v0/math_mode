# -*- coding: utf-8 -*-
"""导出产物结构验证：PDF（xelatex 直出）正确性 + DOCX 可解析性 + 图表引用完整。

PDF 由 pandoc 直接读 main.md 经 xelatex 编译（不经过 HTML），故本套件核心断言之一
是「PDF 文本中零 HTML 标签泄漏」（曾因自实现 HTML 转换器的双重转义 bug 导致
`<span class="math">` 以字面文本渗入 PDF）。
"""
import os, re, sys, subprocess
# Windows 控制台默认 GBK 无法输出 ✅❌≤≥ 等符号，统一 UTF-8 输出
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXP = os.path.join(ROOT, 'paper', 'export')
PDF = os.path.join(EXP, '论文.pdf')
DOCX = os.path.join(EXP, '山区洪涝灾害下无人机运输与通信协同优化.docx')
PANDOC_DOCX = os.path.join(EXP, 'pandoc', '论文_pandoc.docx')
TEX = os.path.join(EXP, 'pandoc', '论文.tex')
HEADER = os.path.join(ROOT, 'paper', 'pandoc_header.tex')
FIGDIR = os.path.join(ROOT, 'paper', 'figs')
R = []


def ck(cid, desc, ok, detail=''):
    R.append((cid, desc, bool(ok), detail))
    print('%-8s %-52s %s %s' % (cid, 'OK' if ok else 'FAIL', desc, detail))


def tool(name):
    return subprocess.run(['bash', '-lc', 'command -v %s' % name],
                          capture_output=True, text=True).stdout.strip() or name


def pdf_text():
    """提取 PDF 全文（UTF-8，容错）。"""
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix='.txt')
    os.close(fd)
    subprocess.run(['pdftotext', '-enc', 'UTF-8', PDF, tmp],
                   capture_output=True)
    txt = open(tmp, encoding='utf-8', errors='replace').read()
    os.remove(tmp)
    return txt


def pdfinfo():
    out = subprocess.run(['pdfinfo', PDF], capture_output=True, text=True,
                         encoding='utf-8', errors='replace').stdout
    info = {}
    for l in out.splitlines():
        if ':' in l:
            k, v = l.split(':', 1)
            info[k.strip()] = v.strip()
    return info


def main():
    figs = [f for f in sorted(os.listdir(FIGDIR)) if f.endswith('.png')]

    # ------------------------------------------------------------------
    # PDF（xelatex 直出）验证
    # ------------------------------------------------------------------
    ck('F-1', 'PDF 存在', os.path.exists(PDF), '')
    if not os.path.exists(PDF):
        ck('F-2', '（跳过后续 PDF 检查）', False, '请先运行 code/export_pandoc.py')
        return 1

    info = pdfinfo()
    ck('F-2', 'A4 页面', 'A4' in info.get('Page size', '') or '595' in info.get('Page size', ''),
       info.get('Page size', ''))
    pages = int(info.get('Pages', 0))
    ck('F-3', '页数 ≥ 20', pages >= 20, '%d 页' % pages)

    txt = pdf_text()
    ck('F-4', 'PDF 文本可提取（含摘要）', '摘要' in txt, '%d 字符' % len(txt))
    for kw in ('摘要', '问题重述', '模型建立与求解', '结论', '参考文献'):
        ck('F-5.' + kw, 'PDF 含章节「%s」' % kw, kw in txt, '')

    # 核心：零 HTML 标签泄漏（曾渗入 <span class="math">）
    tags = re.findall(r'<(span|div|table|br|p|img|td|tr|th|h[0-9]|ul|li|ol|code|pre|'
                      r'style|strong|em|a|blockquote|figure|figcaption)\b', txt)
    ent = txt.count('&lt;') + txt.count('&gt;') + txt.count('&amp;lt;')
    ck('F-6', 'PDF 零 HTML 标签泄漏', not tags and ent == 0,
       '标签 %d 处 / 转义实体 %d 处' % (len(tags), ent) if (tags or ent) else '')

    # Unicode 符号回退生效（缺字形会直接消失，故此处查存在性）
    sym = {ch: txt.count(ch) for ch in '✅❌≤≥⇒↔→①②③℃'}
    missing_sym = [ch for ch, n in sym.items() if n == 0]
    ck('F-7', '关键 Unicode 符号齐备（✅❌≤≥⇒↔→①②③℃）', not missing_sym,
       '缺失 %s' % missing_sym if missing_sym else '全部出现')

    # 图片嵌入（每张 PNG 拆成 image+smask 两类，取 image 型计数）
    img = subprocess.run(['pdfimages', '-list', PDF], capture_output=True, text=True,
                         encoding='utf-8', errors='replace').stdout
    n_img = sum(1 for l in img.splitlines()[2:] if l.split() and len(l.split()) > 2 and l.split()[2] == 'image')
    ck('F-8', 'PDF 内嵌图数 == 图总数', n_img == len(figs), '内嵌 %d / 图 %d' % (n_img, len(figs)))

    # ------------------------------------------------------------------
    # DOCX（自实现 export_docx.py：图片内嵌 + Unicode 数学）
    # ------------------------------------------------------------------
    ck('D-1', 'DOCX 存在（export_docx）', os.path.exists(DOCX), '')
    if os.path.exists(DOCX):
        import zipfile
        z = zipfile.ZipFile(DOCX)
        media = [n for n in z.namelist() if 'media' in n and n.lower().endswith('.png')]
        ck('D-2', 'DOCX 内嵌图片数 == 全部图数', len(media) == len(figs),
           '内嵌 %d 张（图总数 %d）' % (len(media), len(figs)))
        xml = z.read('word/document.xml').decode('utf-8', 'ignore')
        txtd = ''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', xml))
        ck('D-3', 'DOCX 可解析（文本量充足）', len(txtd) > 10000, '%d 字符' % len(txtd))
        for kw in ('摘要', '问题重述', '模型建立与求解', '参考文献'):
            ck('D-4.' + kw, 'DOCX 含章节「%s」' % kw, kw in txtd, '')

    # ------------------------------------------------------------------
    # pandoc DOCX（Word 原生公式）+ LaTeX
    # ------------------------------------------------------------------
    ck('P-1', 'pandoc DOCX 存在', os.path.exists(PANDOC_DOCX), '')
    if os.path.exists(PANDOC_DOCX):
        import zipfile
        z = zipfile.ZipFile(PANDOC_DOCX)
        media = [n for n in z.namelist() if 'media' in n]
        xml = z.read('word/document.xml').decode('utf-8', 'ignore')
        n_omml = xml.count('<m:oMath')
        ck('P-2', 'pandoc DOCX 含 Word 原生公式 >= 100', n_omml >= 100,
           '%d 个' % n_omml)
        ck('P-3', 'pandoc DOCX 内嵌图片 == 图总数', len(media) == len(figs),
           '内嵌 %d / 图 %d' % (len(media), len(figs)))

    ck('P-4', 'LaTeX 源存在', os.path.exists(TEX), '')
    if os.path.exists(TEX):
        tex = open(TEX, encoding='utf-8').read()
        ck('P-5', 'LaTeX 源含图片引用 >= 图总数', tex.count('\\includegraphics') >= len(figs),
           'includegraphics %d 处' % tex.count('\\includegraphics'))
        ck('P-6', 'LaTeX 源含 CJK 字体设置', 'CJK' in tex or 'ctex' in tex or 'SimSun' in tex, '')

    # ------------------------------------------------------------------
    # 回退头文件（Unicode 映射接线）
    # ------------------------------------------------------------------
    ck('H-1', 'pandoc_header.tex 存在', os.path.exists(HEADER), '')
    if os.path.exists(HEADER):
        h = open(HEADER, encoding='utf-8').read()
        ck('H-2', '回退头含 Segoe UI Symbol 字体', 'Segoe UI Symbol' in h, '')
        ck('H-3', '回退头含 newunicodechar 映射', 'newunicodechar' in h, '')

    npass = sum(1 for *_, ok, _ in R if ok)
    print('\n' + '=' * 80)
    print('导出验证：%d/%d 通过' % (npass, len(R)))
    for cid, d, ok, det in R:
        if not ok:
            print('  FAIL %s %s %s' % (cid, d, det))
    return 0 if npass == len(R) else 1


if __name__ == '__main__':
    sys.exit(main())
