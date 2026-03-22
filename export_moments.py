"""
微信朋友圈导出为 HTML
从解密后的 sns.db 提取所有朋友圈记录
"""
import sqlite3
import xml.etree.ElementTree as ET
import datetime
import re
import json
import sys
import os

import argparse
from config import load_config as _load_config

_cfg = _load_config()
_decrypted_dir = _cfg.get('decrypted_dir', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'decrypted'))
DB_PATH = os.path.join(_decrypted_dir, 'sns', 'sns.db')
OUT_PATH = os.path.expanduser('~/moments_export.html')


def parse_moment(content):
    """解析朋友圈 XML，返回结构化数据"""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None

    obj = root.find('TimelineObject')
    extra = root.find('LocalExtraInfo')
    if obj is None:
        return None

    # 基本信息
    post_id = obj.findtext('id', '')
    username = obj.findtext('username', '')
    ts = obj.findtext('createTime', '0')
    dt = datetime.datetime.fromtimestamp(int(ts)) if ts.isdigit() else None
    text = obj.findtext('contentDesc', '').strip()
    content_type = obj.findtext('.//ContentObject/type', '0')  # 1=图文, 2=视频, 4=音乐, 6=链接, 9=纯文

    # 昵称（LocalExtraInfo 里有缓存）
    nickname = ''
    if extra is not None:
        nickname = extra.findtext('nickname', '')

    # 位置
    loc = obj.find('location')
    location = ''
    if loc is not None:
        location = loc.get('poiName', '') or ''

    # 图片/视频
    media_items = []
    for media in obj.findall('.//mediaList/media'):
        mtype = media.findtext('type', '0')
        thumb_el = media.find('thumb')
        url_el = media.find('url')
        thumb_url = thumb_el.text if thumb_el is not None else ''
        full_url = url_el.text if url_el is not None else ''
        width = media.findtext('size[@width]', '') or media.find('size').get('width', '') if media.find('size') is not None else ''
        height = media.findtext('size[@height]', '') or media.find('size').get('height', '') if media.find('size') is not None else ''
        media_items.append({
            'type': mtype,  # 2=图片, 15=视频
            'thumb': thumb_url,
            'url': full_url,
            'width': width,
            'height': height,
        })

    # 转发的链接/文章
    link_title = obj.findtext('.//appInfo/title', '') or obj.findtext('.//title', '')
    link_desc = obj.findtext('.//appInfo/desc', '') or obj.findtext('.//desc', '')
    link_url = obj.findtext('.//appInfo/url', '') or obj.findtext('.//url', '')

    # 点赞用户列表
    likes = []
    for u in obj.findall('.//likeUserList/likeUser'):
        n = u.findtext('nickName', '')
        if n:
            likes.append(n)

    # 评论列表
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


def media_grid_html(media_items):
    """生成图片网格 HTML"""
    imgs = [m for m in media_items if m['type'] in ('2', '2')]
    if not imgs:
        return ''
    n = len(imgs)
    if n == 1:
        cls = 'grid-1'
    elif n == 2:
        cls = 'grid-2'
    elif n == 3:
        cls = 'grid-3'
    elif n == 4:
        cls = 'grid-4'
    else:
        cls = 'grid-n'

    html = f'<div class="media-grid {cls}">'
    for m in imgs:
        src = m['thumb'] or m['url']
        full = m['url'] or m['thumb']
        if src:
            html += f'<a href="{full}" target="_blank"><img src="{src}" loading="lazy" onerror="this.style.display=\'none\'"></a>'
    html += '</div>'
    return html


def moment_html(m, idx):
    """生成单条朋友圈卡片 HTML"""
    time_str = m['time'].strftime('%Y年%m月%d日 %H:%M') if m['time'] else '未知时间'
    date_str = m['time'].strftime('%Y-%m-%d') if m['time'] else ''

    # 头像：用昵称首字作为 fallback
    avatar_char = (m['nickname'] or '?')[0]
    color_idx = sum(ord(c) for c in m['username']) % 8
    colors = ['#FF6B6B','#4ECDC4','#45B7D1','#96CEB4','#FFEAA7','#DDA0DD','#98D8C8','#F7DC6F']
    avatar_color = colors[color_idx]

    # 文字内容
    text_html = ''
    if m['text']:
        text_escaped = m['text'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')
        text_html = f'<div class="post-text">{text_escaped}</div>'

    # 媒体
    media_html = media_grid_html(m['media'])

    # 视频标记
    videos = [mv for mv in m['media'] if mv['type'] == '15']
    if videos:
        src = videos[0]['thumb']
        media_html += f'<div class="video-placeholder"><img src="{src}" loading="lazy"><span class="play-icon">▶</span></div>'

    # 链接卡片
    link_html = ''
    if m['link_title']:
        title_esc = m['link_title'].replace('&', '&amp;').replace('<', '&lt;')
        desc_esc = (m['link_desc'] or '').replace('&', '&amp;').replace('<', '&lt;')
        href = m['link_url'] or '#'
        link_html = f'''<a class="link-card" href="{href}" target="_blank">
            <div class="link-title">{title_esc}</div>
            <div class="link-desc">{desc_esc}</div>
        </a>'''

    # 位置
    loc_html = f'<span class="location">📍 {m["location"]}</span>' if m['location'] else ''

    # 点赞
    likes_html = ''
    if m['likes']:
        names = '、'.join(m['likes'])
        likes_html = f'<div class="likes">❤ {names}</div>'

    # 评论
    comments_html = ''
    if m['comments']:
        items = ''
        for c in m['comments']:
            fn = c['from'].replace('&', '&amp;').replace('<', '&lt;')
            ct = c['text'].replace('&', '&amp;').replace('<', '&lt;').replace('\n', '<br>')
            if c['reply_to']:
                rt = c['reply_to'].replace('&', '&amp;').replace('<', '&lt;')
                items += f'<div class="comment"><span class="comment-from">{fn}</span> 回复 <span class="comment-from">{rt}</span>：{ct}</div>'
            else:
                items += f'<div class="comment"><span class="comment-from">{fn}</span>：{ct}</div>'
        comments_html = f'<div class="comments">{items}</div>'

    return f'''<article class="post" id="post-{idx}" data-date="{date_str}">
    <div class="post-header">
        <div class="avatar" style="background:{avatar_color}">{avatar_char}</div>
        <div class="post-meta">
            <span class="nickname">{m["nickname"]}</span>
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


HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>微信朋友圈导出</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #1a1a1a;
    color: #e0e0e0;
    min-height: 100vh;
}}
header {{
    background: #111;
    padding: 16px 20px;
    position: sticky;
    top: 0;
    z-index: 100;
    border-bottom: 1px solid #333;
    display: flex;
    align-items: center;
    gap: 16px;
}}
header h1 {{ font-size: 18px; color: #fff; }}
header .stats {{ font-size: 13px; color: #888; }}
.filter-bar {{
    background: #111;
    padding: 10px 20px;
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    border-bottom: 1px solid #333;
}}
.filter-bar input {{
    background: #2a2a2a;
    border: 1px solid #444;
    color: #e0e0e0;
    padding: 6px 12px;
    border-radius: 20px;
    font-size: 13px;
    width: 220px;
}}
.filter-bar input::placeholder {{ color: #666; }}
.timeline {{
    max-width: 680px;
    margin: 0 auto;
    padding: 16px;
}}
.date-divider {{
    text-align: center;
    margin: 24px 0 12px;
    position: relative;
}}
.date-divider::before {{
    content: '';
    position: absolute;
    left: 0; right: 0; top: 50%;
    height: 1px;
    background: #333;
}}
.date-divider span {{
    background: #1a1a1a;
    padding: 0 12px;
    position: relative;
    font-size: 12px;
    color: #666;
}}
.post {{
    background: #242424;
    border-radius: 12px;
    margin-bottom: 12px;
    padding: 14px;
    border: 1px solid #333;
    transition: border-color 0.2s;
}}
.post:hover {{ border-color: #555; }}
.post-header {{
    display: flex;
    gap: 10px;
    align-items: flex-start;
    margin-bottom: 10px;
}}
.avatar {{
    width: 40px;
    height: 40px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    font-weight: bold;
    color: #fff;
    flex-shrink: 0;
}}
.post-meta {{ flex: 1; }}
.nickname {{
    display: block;
    font-size: 15px;
    font-weight: 600;
    color: #7eb8f7;
}}
.post-time {{
    display: block;
    font-size: 12px;
    color: #666;
    margin-top: 2px;
}}
.location {{
    font-size: 11px;
    color: #888;
    margin-top: 2px;
    display: block;
}}
.post-body {{ margin-left: 50px; }}
.post-text {{
    font-size: 15px;
    line-height: 1.6;
    color: #ddd;
    margin-bottom: 10px;
    word-break: break-word;
}}
/* 图片网格 */
.media-grid {{
    display: grid;
    gap: 3px;
    margin-bottom: 8px;
    border-radius: 8px;
    overflow: hidden;
}}
.grid-1 {{ grid-template-columns: 1fr; max-width: 300px; }}
.grid-2 {{ grid-template-columns: 1fr 1fr; }}
.grid-3 {{ grid-template-columns: 1fr 1fr 1fr; }}
.grid-4 {{ grid-template-columns: 1fr 1fr; }}
.grid-n {{ grid-template-columns: repeat(3, 1fr); }}
.media-grid a {{
    display: block;
    overflow: hidden;
    aspect-ratio: 1;
    background: #333;
}}
.media-grid img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    transition: transform 0.2s;
}}
.media-grid a:hover img {{ transform: scale(1.03); }}
.grid-1 a {{ aspect-ratio: auto; max-height: 400px; }}
.grid-1 img {{ height: auto; max-height: 400px; object-fit: contain; background: #1a1a1a; }}
/* 视频 */
.video-placeholder {{
    position: relative;
    max-width: 300px;
    border-radius: 8px;
    overflow: hidden;
    background: #000;
    margin-bottom: 8px;
}}
.video-placeholder img {{ width: 100%; }}
.play-icon {{
    position: absolute;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    font-size: 40px;
    color: rgba(255,255,255,0.85);
    text-shadow: 0 2px 8px rgba(0,0,0,0.5);
    pointer-events: none;
}}
/* 链接卡片 */
.link-card {{
    display: block;
    background: #1e1e1e;
    border: 1px solid #444;
    border-radius: 8px;
    padding: 10px 12px;
    margin-bottom: 8px;
    text-decoration: none;
    color: inherit;
}}
.link-card:hover {{ border-color: #666; }}
.link-title {{ font-size: 14px; color: #7eb8f7; font-weight: 500; margin-bottom: 4px; }}
.link-desc {{ font-size: 12px; color: #888; }}
/* 点赞 */
.likes {{
    margin-left: 50px;
    font-size: 13px;
    color: #e87070;
    padding: 6px 0;
    border-top: 1px solid #333;
    margin-top: 6px;
}}
/* 评论 */
.comments {{
    margin-left: 50px;
    background: #1e1e1e;
    border-radius: 6px;
    padding: 8px;
    margin-top: 4px;
}}
.comment {{
    font-size: 13px;
    color: #ccc;
    padding: 3px 0;
    line-height: 1.5;
}}
.comment-from {{ color: #7eb8f7; font-weight: 500; }}
/* 搜索高亮 */
.highlight {{ background: #f0c040; color: #000; border-radius: 2px; }}
.hidden {{ display: none !important; }}
/* 回到顶部 */
#backtop {{
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: #444;
    color: #fff;
    border: none;
    border-radius: 50%;
    width: 44px;
    height: 44px;
    font-size: 20px;
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.3s;
}}
#backtop.show {{ opacity: 1; }}
</style>
</head>
<body>
<header>
    <h1>📱 朋友圈归档</h1>
    <span class="stats">共 {total} 条 · {date_range}</span>
</header>
<div class="filter-bar">
    <input type="text" id="search" placeholder="搜索文字内容..." oninput="doSearch(this.value)">
</div>
<div class="timeline" id="timeline">
{content}
</div>
<button id="backtop" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</button>
<script>
window.addEventListener('scroll', () => {{
    document.getElementById('backtop').classList.toggle('show', window.scrollY > 400);
}});
function doSearch(q) {{
    q = q.trim().toLowerCase();
    document.querySelectorAll('.post').forEach(el => {{
        if (!q) {{
            el.classList.remove('hidden');
            return;
        }}
        const txt = el.textContent.toLowerCase();
        el.classList.toggle('hidden', !txt.includes(q));
    }});
    document.querySelectorAll('.date-divider').forEach(el => {{
        const next = el.nextElementSibling;
        el.classList.toggle('hidden', !next || next.classList.contains('hidden'));
    }});
}};
</script>
</body>
</html>'''


def main():
    print("读取数据库...")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT tid, user_name, content FROM SnsTimeLine ORDER BY tid ASC')
    rows = cur.fetchall()
    conn.close()
    print(f"共 {len(rows)} 条记录，开始解析...")

    moments = []
    for tid, user, content in rows:
        if not content:
            continue
        m = parse_moment(content)
        if m:
            moments.append(m)

    moments.sort(key=lambda x: x['time'] or datetime.datetime.min)
    print(f"解析成功 {len(moments)} 条")

    # 生成 HTML 内容
    html_parts = []
    last_date = None
    for idx, m in enumerate(moments):
        date_str = m['time'].strftime('%Y年%m月%d日') if m['time'] else '未知日期'
        if date_str != last_date:
            html_parts.append(f'<div class="date-divider"><span>{date_str}</span></div>')
            last_date = date_str
        html_parts.append(moment_html(m, idx))

    content_html = '\n'.join(html_parts)

    # 日期范围
    valid_times = [m['time'] for m in moments if m['time']]
    if valid_times:
        oldest = min(valid_times).strftime('%Y.%m.%d')
        newest = max(valid_times).strftime('%Y.%m.%d')
        date_range = f'{oldest} – {newest}'
    else:
        date_range = ''

    html = HTML_TEMPLATE.format(
        total=len(moments),
        date_range=date_range,
        content=content_html,
    )

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\n✅ 导出完成：{OUT_PATH}")
    print(f"   共 {len(moments)} 条朋友圈")
    if valid_times:
        print(f"   时间跨度：{date_range}")


if __name__ == '__main__':
    main()
