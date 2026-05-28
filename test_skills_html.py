import re

with open('debug_html.txt', 'r', encoding='utf-8') as f:
    html = f.read()

buttons = re.findall(r'<button[^>]*flex w-full flex-col[^>]*>(.*?)</button>', html, re.DOTALL)
for btn in buttons:
    img = re.search(r'<img[^>]*src="([^"]+)"', btn)
    span = re.search(r'<span[^>]*text-gray[^>]*>(.*?)</span>', btn)
    print('IMG:', img.group(1)[:50] if img else None)
    print('SPAN:', span.group(1).strip() if span else None)
