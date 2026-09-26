# -*- coding: utf-8 -*-
"""HTML -> readable text extractor. Writes UTF-8 output (never prints CJK to stdout)."""
import re, html, sys, pathlib

def extract(raw: str) -> str:
    raw = re.sub(r'(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>', ' ', raw)
    raw = re.sub(r'(?is)<!--.*?-->', ' ', raw)
    # block-level tags become newlines
    raw = re.sub(r'(?i)<(br|/p|/div|/h[1-6]|/li|/tr|/table|/section|/article)\s*/?>', '\n', raw)
    raw = re.sub(r'(?i)<(td|th)[^>]*>', ' | ', raw)
    txt = re.sub(r'(?s)<[^>]+>', ' ', raw)
    txt = html.unescape(txt)
    txt = txt.replace(' ', ' ').replace('　', ' ')
    lines = []
    for l in txt.split('\n'):
        l = re.sub(r'[ \t]+', ' ', l).strip()
        if l:
            lines.append(l)
    return '\n'.join(lines)

if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    raw = pathlib.Path(src).read_bytes().decode('utf-8', 'ignore')
    out = extract(raw)
    pathlib.Path(dst).write_text(out, encoding='utf-8')
    # ASCII-only summary
    print("src=%s chars=%d lines=%d" % (src, len(out), out.count('\n')))
