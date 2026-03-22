#!/usr/bin/env python3
"""
微信朋友圈缓存图片画廊生成器

从 cache_monitor.py 解密的图片目录生成按月份分组的 gallery.html。
所有图片为本地文件，离线永久可访问。

用法：
    python export_gallery.py
    python export_gallery.py --images ~/moments_export_full/images
    python export_gallery.py --output ~/Desktop/gallery.html
"""
import os
import sys
import json
import argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_config

_cfg = load_config()
_export_base = os.path.expanduser(_cfg.get('moments_export_dir', '~/moments_export_full'))

DEFAULT_IMAGES_DIR = os.path.join(_export_base, 'images')
DEFAULT_INDEX_FILE = os.path.join(_export_base, 'image_index.jsonl')
DEFAULT_OUTPUT = os.path.join(_export_base, 'gallery.html')

IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'gif'}


def load_index(index_file):
    """读取 image_index.jsonl，返回 {filename: month} 映射"""
    mapping = {}
    if not os.path.exists(index_file):
        return mapping
    with open(index_file, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                mapping[entry['file']] = entry.get('month', 'unknown')
            except Exception:
                pass
    return mapping


def collect_images(images_dir, index_file):
    """收集图片，按月份分组。优先使用 index 文件，fallback 到 unknown 分组"""
    fname_to_month = load_index(index_file)
    by_month = defaultdict(list)

    if not os.path.isdir(images_dir):
        print(f"[!] 图片目录不存在: {images_dir}")
        print("    请先运行 cache_monitor.py 解密图片")
        return by_month

    for fname in sorted(os.listdir(images_dir)):
        ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
        if ext not in IMAGE_EXTS:
            continue
        month = fname_to_month.get(fname, 'unknown')
        by_month[month].append(fname)

    return by_month


def generate_html(by_month, images_rel):
    total = sum(len(v) for v in by_month.values())
    months_sorted = sorted(k for k in by_month if k != 'unknown')
    if 'unknown' in by_month:
        months_sorted.append('unknown')

    nav_items = ''.join(
        f'<a href="#{m}">{m}</a>'
        for m in months_sorted if m != 'unknown'
    )

    sections = []
    for month in months_sorted:
        imgs = by_month[month]
        if month == 'unknown':
            label = f'未知月份 · {len(imgs)} 张'
        else:
            try:
                yr, mo = month.split('-')
                label = f'{yr}年{mo}月 · {len(imgs)} 张'
            except Exception:
                label = f'{month} · {len(imgs)} 张'

        img_tags = '\n'.join(
            f'<a href="{images_rel}/{f}" target="_blank">'
            f'<img src="{images_rel}/{f}" loading="lazy" alt="">'
            f'</a>'
            for f in imgs
        )
        sections.append(f'<section class="month" id="{month}">\n'
                        f'<h2>{label}</h2>\n'
                        f'<div class="grid">{img_tags}</div>\n'
                        f'</section>')

    content = '\n'.join(sections)

    return f'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>朋友圈缓存图片 ({total} 张)</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#111;color:#ddd;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh}}
header{{background:#1a1a1a;padding:16px 20px;border-bottom:1px solid #333;position:sticky;top:0;z-index:10}}
header h1{{font-size:18px;color:#fff;font-weight:600}}
header p{{font-size:13px;color:#777;margin-top:3px}}
.nav{{padding:8px 20px;background:#161616;border-bottom:1px solid #252525;overflow-x:auto;white-space:nowrap;font-size:12px;line-height:2}}
.nav a{{color:#4a9;text-decoration:none;margin-right:10px;padding:2px 6px;border-radius:3px}}
.nav a:hover{{background:#2a3a2a;color:#6cb}}
.content{{padding:20px;max-width:1600px;margin:0 auto}}
.month{{margin-bottom:36px}}
.month h2{{font-size:13px;color:#4a9;border-bottom:1px solid #252525;padding-bottom:7px;margin-bottom:10px;font-weight:500}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:3px}}
.grid a{{display:block;aspect-ratio:1;overflow:hidden;background:#1e1e1e;border-radius:2px}}
.grid img{{width:100%;height:100%;object-fit:cover;transition:opacity .15s}}
.grid a:hover img{{opacity:.8}}
#backtop{{position:fixed;bottom:20px;right:20px;background:#2a2a2a;border:1px solid #444;color:#ccc;border-radius:50%;width:38px;height:38px;font-size:16px;cursor:pointer;opacity:0;transition:opacity .3s;display:flex;align-items:center;justify-content:center}}
#backtop.show{{opacity:1}}
</style>
</head>
<body>
<header>
  <h1>朋友圈缓存图片</h1>
  <p>共 {total} 张 · {len(months_sorted)} 个月份</p>
</header>
<nav class="nav">{nav_items}</nav>
<main class="content">
{content}
</main>
<button id="backtop" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</button>
<script>
window.addEventListener('scroll',()=>{{
  document.getElementById('backtop').classList.toggle('show',window.scrollY>400);
}});
</script>
</body>
</html>'''


def main():
    parser = argparse.ArgumentParser(description='微信朋友圈缓存图片画廊生成器')
    parser.add_argument('--images', default=DEFAULT_IMAGES_DIR,
                        help=f'解密图片目录 (默认: {DEFAULT_IMAGES_DIR})')
    parser.add_argument('--index', default=DEFAULT_INDEX_FILE,
                        help=f'image_index.jsonl 路径 (默认: {DEFAULT_INDEX_FILE})')
    parser.add_argument('--output', default=DEFAULT_OUTPUT,
                        help=f'输出 HTML 路径 (默认: {DEFAULT_OUTPUT})')
    args = parser.parse_args()

    images_dir = os.path.expanduser(args.images)
    index_file = os.path.expanduser(args.index)
    output_path = os.path.expanduser(args.output)

    print(f"扫描图片目录: {images_dir}")
    by_month = collect_images(images_dir, index_file)

    total = sum(len(v) for v in by_month.values())
    if total == 0:
        print("[!] 没有找到图片，请先运行 cache_monitor.py 解密图片")
        sys.exit(1)

    print(f"找到 {total} 张图片，{len(by_month)} 个月份")

    # 计算 HTML 文件到 images 目录的相对路径
    output_dir = os.path.dirname(os.path.abspath(output_path))
    images_rel = os.path.relpath(os.path.abspath(images_dir), output_dir)

    html = generate_html(by_month, images_rel)

    os.makedirs(output_dir, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    size_kb = os.path.getsize(output_path) // 1024
    print(f"\n✅ 画廊已生成：{output_path}")
    print(f"   {total} 张图片 · {len(by_month)} 个月份 · {size_kb} KB")
    print(f"\n用浏览器打开：file://{os.path.abspath(output_path)}")


if __name__ == '__main__':
    main()
