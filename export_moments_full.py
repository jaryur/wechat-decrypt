"""
微信朋友圈完整导出（含本地缓存图片 + 视频）
图片按月份与帖子对应展示
"""
import sqlite3
import xml.etree.ElementTree as ET
import datetime
import re
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import argparse
from config import load_config as _load_config

_cfg = _load_config()
_export_base = os.path.expanduser(_cfg.get('moments_export_dir', '~/moments_export_full'))
_decrypted_dir = _cfg.get('decrypted_dir', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'decrypted'))

DB_PATH = os.path.join(_decrypted_dir, 'sns', 'sns.db')
IMAGES_DIR = os.path.join(_export_base, 'images')
VIDEOS_DIR = os.path.expanduser(_cfg.get('cache_dir', ''))
OUT_PATH = os.path.join(_export_base, 'moments_full.html')

# Relative path from HTML to images (HTML is in moments_export_full/)
IMG_REL = 'images'


def parse_moment(content):
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None

    obj = root.find('TimelineObject')
    extra = root.find('LocalExtraInfo')
    if obj is None:
        return None

    post_id = obj.findtext('id', '')
    username = obj.findtext('username', '')
    ts = obj.findtext('createTime', '0')
    dt = datetime.datetime.fromtimestamp(int(ts)) if ts and ts.isdigit() else None
    text = obj.findtext('contentDesc', '').strip()
    content_type = obj.findtext('.//ContentObject/type', '0')

    nickname = ''
    if extra is not None:
        nickname = extra.findtext('nickname', '')

    loc = obj.find('location')
    location = ''
    if loc is not None:
        location = loc.get('poiName', '') or ''

    media_items = []
    for media in obj.findall('.//mediaList/media'):
        mtype = media.findtext('type', '0')
        thumb_el = media.find('thumb')
        url_el = media.find('url')
        thumb_url = thumb_el.text if thumb_el is not None else ''
        full_url = url_el.text if url_el is not None else ''
        size_el = media.find('size')
        w = size_el.get('width', '') if size_el is not None else ''
        h = size_el.get('height', '') if size_el is not None else ''
        media_items.append({
            'type': mtype,
            'thumb': thumb_url,
            'url': full_url,
            'width': w,
            'height': h,
        })

    link_title = obj.findtext('.//appInfo/title', '') or obj.findtext('.//title', '')
    link_desc = obj.findtext('.//appInfo/desc', '') or obj.findtext('.//desc', '')
    link_url = obj.findtext('.//appInfo/url', '') or obj.findtext('.//url', '')

    likes = []
    for u in obj.findall('.//likeUserList/likeUser'):
        n = u.findtext('nickName', '')
        if n:
            likes.append(n)

    comments = []
    for c in obj.findall('.//commentList/comment'):
        cfrom = c.findtext('nickName', '') or c.findtext('username', '')
        ctxt = c.findtext('content', '')
        reply_to = c.findtext('replyCommentNickName', '')
        if ctxt:
            comments.append({'from': cfrom, 'text': ctxt, 'reply_to': reply_to})

    return {
        'id': post_id,
        'username': username,
        'nickname': nickname or username,
        'time': dt,
        'text': text,
        'type': content_type,
        'media': media_items,
        'location': location,
        'likes': likes,
        'comments': comments,
        'link_title': link_title,
        'link_desc': link_desc,
        'link_url': link_url,
    }


def load_cached_images():
    """Load all cached images, indexed by YYYY-MM month key."""
    by_month = defaultdict(list)
    if not os.path.exists(IMAGES_DIR):
        return by_month
    for fname in sorted(os.listdir(IMAGES_DIR)):
        ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
        if ext in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
            # Try to find month from cache directory structure
            by_month['unknown'].append(fname)
    return by_month


def load_cached_images_by_month():
    """Load images indexed by YYYY-MM by scanning the original cache dirs."""
    by_month = defaultdict(list)
    cache_base = VIDEOS_DIR

    # Map: cache filename (no ext) -> month
    fname_to_month = {}
    for month_dir in sorted(os.listdir(cache_base)):
        if not re.match(r'\d{4}-\d{2}', month_dir):
            continue
        sns_img = os.path.join(cache_base, month_dir, 'Sns', 'Img')
        if not os.path.isdir(sns_img):
            continue
        for subdir in os.listdir(sns_img):
            subpath = os.path.join(sns_img, subdir)
            if os.path.isdir(subpath):
                for f in os.listdir(subpath):
                    if '.' not in f:
                        fname_to_month[f] = month_dir

    # Now match against our decrypted images
    if not os.path.exists(IMAGES_DIR):
        return by_month

    for fname in sorted(os.listdir(IMAGES_DIR)):
        if '.' not in fname:
            continue
        base = fname.rsplit('.', 1)[0]
        ext = fname.rsplit('.', 1)[1].lower()
        if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
            continue
        month = fname_to_month.get(base, 'unknown')
        by_month[month].append(fname)

    return by_month


def load_cached_videos():
    """Find cached MP4 videos in Video subdirs."""
    videos = []
    for month_dir in sorted(os.listdir(VIDEOS_DIR)):
        if not re.match(r'\d{4}-\d{2}', month_dir):
            continue
        video_dir = os.path.join(VIDEOS_DIR, month_dir, 'Sns', 'Video')
        if not os.path.isdir(video_dir):
            continue
        for subdir in os.listdir(video_dir):
            subpath = os.path.join(video_dir, subdir)
            if not os.path.isdir(subpath):
                continue
            for f in os.listdir(subpath):
                if f.endswith('.mp4'):
                    videos.append({
                        'month': month_dir,
                        'path': os.path.join(subpath, f),
                        'thumb': os.path.join(subpath, f.replace('.mp4', '.jpg')),
                        'name': f,
                    })
    return videos


def esc(s):
    return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def moment_html(m, idx):
    time_str = m['time'].strftime('%Y年%m月%d日 %H:%M') if m['time'] else '未知时间'
    date_str = m['time'].strftime('%Y-%m-%d') if m['time'] else ''

    avatar_char = (m['nickname'] or '?')[0]
    color_idx = sum(ord(c) for c in (m['username'] or '?')) % 8
    colors = ['#FF6B6B','#4ECDC4','#45B7D1','#96CEB4','#FFEAA7','#DDA0DD','#98D8C8','#F7DC6F']
    avatar_color = colors[color_idx]

    text_html = ''
    if m['text']:
        text_html = f'<div class="post-text">{esc(m["text"]).replace(chr(10), "<br>")}</div>'

    media_html = ''
    images = [mv for mv in m['media'] if mv['type'] in ('2', '1')]
    videos = [mv for mv in m['media'] if mv['type'] == '15']

    if images:
        n = len(images)
        grid_class = f'grid-{min(n, 3)}'
        img_tags = ''
        for mv in images:
            src = mv['thumb'] or mv['url']
            full = mv['url'] or mv['thumb']
            if src:
                img_tags += (
                    f'<a href="{esc(full)}" target="_blank">'
                    f'<img src="{esc(src)}" loading="lazy" '
                    f'onerror="this.style.display=\'none\';this.parentNode.classList.add(\'img-dead\')">'
                    f'</a>'
                )
            else:
                img_tags += f'<div class="img-placeholder">🖼</div>'
        media_html = f'<div class="media-grid {grid_class}">{img_tags}</div>'

    if videos:
        for mv in videos:
            src = mv['thumb'] or mv['url']
            full = mv['url'] or mv['thumb']
            if src:
                media_html += (
                    f'<div class="video-placeholder">'
                    f'<a href="{esc(full)}" target="_blank">'
                    f'<img src="{esc(src)}" loading="lazy" '
                    f'onerror="this.closest(\'.video-placeholder\').innerHTML=\'🎬 视频\'">'
                    f'<span class="play-icon">▶</span></a></div>'
                )

    link_html = ''
    if m['link_title']:
        href = esc(m['link_url'] or '#')
        link_html = f'''<a class="link-card" href="{href}" target="_blank">
            <div class="link-title">{esc(m["link_title"])}</div>
            <div class="link-desc">{esc(m["link_desc"] or "")}</div>
        </a>'''

    loc_html = f'<span class="location">📍 {esc(m["location"])}</span>' if m['location'] else ''

    likes_html = ''
    if m['likes']:
        likes_html = f'<div class="likes">❤ {esc("、".join(m["likes"]))}</div>'

    comments_html = ''
    if m['comments']:
        items = ''
        for c in m['comments']:
            fn = esc(c['from'])
            ct = esc(c['text']).replace('\n', '<br>')
            if c['reply_to']:
                rt = esc(c['reply_to'])
                items += f'<div class="comment"><span class="cfrom">{fn}</span> 回复 <span class="cfrom">{rt}</span>：{ct}</div>'
            else:
                items += f'<div class="comment"><span class="cfrom">{fn}</span>：{ct}</div>'
        comments_html = f'<div class="comments">{items}</div>'

    return f'''<article class="post" id="post-{idx}" data-date="{date_str}">
  <div class="post-header">
    <div class="avatar" style="background:{avatar_color}">{esc(avatar_char)}</div>
    <div class="post-meta">
      <span class="nickname">{esc(m["nickname"])}</span>
      <span class="post-time">{time_str}</span>
      {loc_html}
    </div>
  </div>
  <div class="post-body">
    {text_html}
    {media_html}
    {link_html}
  </div>
  {likes_html}
  {comments_html}
</article>'''


def month_gallery_html(month_key, images):
    items = ''.join(
        f'<a href="{IMG_REL}/{f}" target="_blank"><img src="{IMG_REL}/{f}" loading="lazy"></a>'
        for f in images
    )
    yr, mo = month_key.split('-')
    title = f'📁 {yr}年{mo}月本地备份 · {len(images)} 张（点击展开）'
    return (f'<div class="month-gallery">'
            f'<div class="gallery-title" onclick="toggleGallery(this)">{title}</div>'
            f'<div class="gallery-grid">{items}</div>'
            f'</div>')


CSS = '''
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#1a1a1a;color:#e0e0e0}
header{background:#111;padding:14px 20px;position:sticky;top:0;z-index:100;border-bottom:1px solid #333}
header h1{font-size:18px;color:#fff}
header p{font-size:13px;color:#888;margin-top:4px}
.filterbar{background:#111;padding:8px 16px;border-bottom:1px solid #333;display:flex;gap:8px;flex-wrap:wrap}
.filterbar input{background:#2a2a2a;border:1px solid #444;color:#e0e0e0;padding:6px 12px;border-radius:20px;font-size:13px;width:220px}
.filterbar input::placeholder{color:#666}
.timeline{max-width:700px;margin:0 auto;padding:16px}
.date-divider{text-align:center;margin:24px 0 10px;position:relative}
.date-divider::before{content:'';position:absolute;left:0;right:0;top:50%;height:1px;background:#333}
.date-divider span{background:#1a1a1a;padding:0 12px;position:relative;font-size:12px;color:#666}
.month-gallery{background:#1e2a1e;border:1px solid #2a4a2a;border-radius:10px;margin:12px 0 20px;padding:12px}
.gallery-title{font-size:13px;color:#6a9;margin-bottom:8px;font-weight:600;cursor:pointer;user-select:none}
.gallery-title:hover{color:#8cb}
.gallery-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:3px}
.gallery-grid a{display:block;aspect-ratio:1;overflow:hidden;background:#333;border-radius:3px}
.gallery-grid img{width:100%;height:100%;object-fit:cover;transition:transform .2s}
.gallery-grid a:hover img{transform:scale(1.06)}
.video-gallery{background:#1e1e2a;border:1px solid #2a2a4a;border-radius:10px;margin:12px 0 20px;padding:12px}
.video-gallery-title{font-size:13px;color:#79f;margin-bottom:8px;font-weight:600}
.video-grid{display:flex;gap:8px;flex-wrap:wrap}
.video-item{position:relative;width:160px;border-radius:6px;overflow:hidden;background:#000}
.video-item video{width:100%;display:block}
.post{background:#242424;border-radius:12px;margin-bottom:10px;padding:14px;border:1px solid #333}
.post:hover{border-color:#555}
.post-header{display:flex;gap:10px;margin-bottom:10px}
.avatar{width:40px;height:40px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:bold;color:#fff;flex-shrink:0}
.post-meta{flex:1}
.nickname{display:block;font-size:15px;font-weight:600;color:#7eb8f7}
.post-time{display:block;font-size:12px;color:#666;margin-top:2px}
.location{font-size:11px;color:#888;display:block;margin-top:1px}
.post-body{margin-left:50px}
.post-text{font-size:15px;line-height:1.6;color:#ddd;margin-bottom:8px;word-break:break-word}
.media-hint{font-size:12px;color:#666;background:#2a2a2a;padding:4px 8px;border-radius:4px;display:inline-block;margin-bottom:6px}
.img-placeholder{aspect-ratio:1;background:#2a2a2a;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:24px;color:#555}
.img-dead{background:#1e1e1e;aspect-ratio:1;border-radius:4px;display:flex;align-items:center;justify-content:center}
.img-dead::after{content:"🖼";font-size:20px;opacity:0.3}
.media-grid{display:grid;gap:3px;margin-bottom:8px}
.media-grid.grid-1{grid-template-columns:1fr;max-width:320px}
.media-grid.grid-2{grid-template-columns:1fr 1fr;max-width:320px}
.media-grid.grid-3,.media-grid.grid-4{grid-template-columns:repeat(3,1fr);max-width:360px}
.media-grid a{display:block;aspect-ratio:1;overflow:hidden;background:#333;border-radius:4px}
.media-grid.grid-1 a{aspect-ratio:auto;max-height:400px}
.media-grid img{width:100%;height:100%;object-fit:cover;transition:transform .2s}
.media-grid.grid-1 img{object-fit:contain;background:#1a1a1a}
.media-grid a:hover img{transform:scale(1.03)}
.video-placeholder{position:relative;display:inline-block;max-width:200px;margin-bottom:8px;border-radius:6px;overflow:hidden;background:#000}
.video-placeholder img{width:100%;display:block;object-fit:cover}
.play-icon{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:32px;color:#fff;text-shadow:0 0 8px rgba(0,0,0,.8);pointer-events:none}
.month-gallery{background:#1a2a1a;border:1px solid #243024;border-radius:8px;margin:4px 0 16px;padding:8px}
.gallery-title{font-size:12px;color:#4a7;margin-bottom:6px;font-weight:500;cursor:pointer;user-select:none}
.gallery-title:hover{color:#6b9}
.gallery-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:2px;display:none}
.link-card{display:block;background:#1e1e1e;border:1px solid #444;border-radius:8px;padding:10px 12px;margin-bottom:8px;text-decoration:none;color:inherit}
.link-card:hover{border-color:#666}
.link-title{font-size:14px;color:#7eb8f7;font-weight:500;margin-bottom:3px}
.link-desc{font-size:12px;color:#888}
.likes{margin-left:50px;font-size:13px;color:#e87070;padding:6px 0;border-top:1px solid #333;margin-top:6px}
.comments{margin-left:50px;background:#1e1e1e;border-radius:6px;padding:8px;margin-top:4px}
.comment{font-size:13px;color:#ccc;padding:2px 0;line-height:1.5}
.cfrom{color:#7eb8f7;font-weight:500}
.hidden{display:none!important}
#backtop{position:fixed;bottom:20px;right:20px;background:#444;color:#fff;border:none;border-radius:50%;width:44px;height:44px;font-size:20px;cursor:pointer;opacity:0;transition:opacity .3s}
#backtop.show{opacity:1}
'''

JS = '''
window.addEventListener('scroll',()=>{
  document.getElementById('backtop').classList.toggle('show',window.scrollY>400);
});
function doSearch(q){
  q=q.trim().toLowerCase();
  document.querySelectorAll('.post').forEach(el=>{
    if(!q){el.classList.remove('hidden');return;}
    el.classList.toggle('hidden',!el.textContent.toLowerCase().includes(q));
  });
  document.querySelectorAll('.date-divider').forEach(el=>{
    const sib=el.nextElementSibling;
    if(sib&&sib.classList.contains('hidden'))el.classList.add('hidden');
    else el.classList.remove('hidden');
  });
}
'''


def main():
    print("读取朋友圈数据库...")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT tid, user_name, content FROM SnsTimeLine ORDER BY tid ASC')
    rows = cur.fetchall()
    conn.close()
    print(f"  共 {len(rows)} 条记录，解析中...")

    moments = []
    for tid, user, content in rows:
        if not content:
            continue
        m = parse_moment(content)
        if m:
            moments.append(m)

    moments.sort(key=lambda x: x['time'] or datetime.datetime.min)
    print(f"  解析成功 {len(moments)} 条")

    # Build HTML
    print("生成 HTML...")
    html_parts = []
    last_date = None

    for idx, m in enumerate(moments):
        date_str = m['time'].strftime('%Y年%m月%d日') if m['time'] else '未知日期'

        if date_str != last_date:
            html_parts.append(f'<div class="date-divider"><span>{date_str}</span></div>')
            last_date = date_str

        html_parts.append(moment_html(m, idx))

    content_html = '\n'.join(html_parts)

    valid_times = [m['time'] for m in moments if m['time']]
    if valid_times:
        oldest = min(valid_times).strftime('%Y.%m.%d')
        newest = max(valid_times).strftime('%Y.%m.%d')
        date_range = f'{oldest} – {newest}'
    else:
        date_range = ''

    html = f'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>朋友圈归档 {date_range}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>📱 朋友圈归档</h1>
  <p>共 {len(moments)} 条 · {date_range}</p>
</header>
<div class="filterbar">
  <input type="text" id="search" placeholder="搜索文字内容..." oninput="doSearch(this.value)">
</div>
<div class="timeline" id="timeline">
{content_html}
</div>
<button id="backtop" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</button>
<script>{JS}</script>
</body>
</html>'''

    print(f"写入 {OUT_PATH}...")
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        f.write(html)

    size_mb = os.path.getsize(OUT_PATH) / 1024 / 1024
    print(f"\n✅ 导出完成：{OUT_PATH}")
    print(f"   {len(moments)} 条朋友圈")
    print(f"   时间跨度：{date_range}")
    print(f"   文件大小：{size_mb:.1f} MB")


if __name__ == '__main__':
    main()
