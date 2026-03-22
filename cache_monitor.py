#!/usr/bin/env python3
"""
WeChat 朋友圈图片自动解密守护进程
监控缓存目录，有新文件就自动解密到 ~/moments_export_full/images/

修复：
- 用 os.replace() 原子写入，彻底消除 unlink+rename 的竞态条件
- 输出文件已存在时直接跳过，永不覆盖已有数据
- 用 image_index.jsonl 永久记录月份元数据，WeChat 清缓存后仍可用
"""
import os, sys, time, json
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from decode_image import v2_decrypt_file, xor_decrypt_file, detect_image_format
from config import load_config

_cfg = load_config()


def _load_aes_key():
    """从 config.json image_aes_key 字段或 image_aes_key.txt 文件加载 AES 密钥"""
    key_hex = _cfg.get('image_aes_key', '').strip()
    if key_hex:
        try:
            return bytes.fromhex(key_hex)
        except ValueError:
            pass
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'image_aes_key.txt')
    if os.path.exists(key_file):
        with open(key_file) as f:
            try:
                return bytes.fromhex(f.read().strip())
            except ValueError:
                pass
    return None


AES_KEY = _load_aes_key()
_export_base = os.path.expanduser(_cfg.get('moments_export_dir', '~/moments_export_full'))
CACHE_BASE = os.path.expanduser(_cfg.get('cache_dir', ''))
OUT_DIR = os.path.join(_export_base, 'images')
INDEX_FILE = os.path.join(_export_base, 'image_index.jsonl')
os.makedirs(OUT_DIR, exist_ok=True)

if not CACHE_BASE:
    print("[!] 请在 config.json 中配置 cache_dir（微信缓存目录路径）")
    sys.exit(1)
if AES_KEY is None:
    print("[!] 未找到图片 AES 密钥，V2 格式图片将无法解密")
    print("    请先运行 find_image_key.py 提取密钥，或在 config.json 中设置 image_aes_key")

processed = set()  # basenames already handled this session
stats = {'new': 0, 'skip': 0, 'fail': 0}


def append_index(fname, month):
    """追加一条索引记录（原子追加，不会损坏已有数据）"""
    entry = json.dumps({'file': fname, 'month': month}, ensure_ascii=False) + '\n'
    with open(INDEX_FILE, 'a', encoding='utf-8') as f:
        f.write(entry)


def load_indexed_files():
    """读取索引，返回已记录的文件名集合"""
    known = set()
    if not os.path.exists(INDEX_FILE):
        return known
    with open(INDEX_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    known.add(json.loads(line)['file'])
                except Exception:
                    pass
    return known


def output_exists(fname_base):
    """检查某文件名是否已有解密输出"""
    out_base = os.path.join(OUT_DIR, fname_base)
    for ext in ('jpg', 'jpeg', 'png', 'webp', 'gif', 'mp4', 'hevc'):
        if os.path.exists(f"{out_base}.{ext}"):
            return f"{fname_base}.{ext}"
    return None


def extract_month(fpath):
    for part in fpath.split('/'):
        if len(part) == 7 and part[4] == '-':
            return part
    return 'unknown'


def process_file(fpath):
    fname = os.path.basename(fpath)
    if '.' in fname:
        return  # 跳过已有扩展名的文件

    if fname in processed:
        return

    # 如果已有输出文件，直接跳过（永不覆盖）
    existing = output_exists(fname)
    if existing:
        processed.add(fname)
        stats['skip'] += 1
        return

    processed.add(fname)

    # 等文件写入完成
    time.sleep(0.3)
    if not os.path.exists(fpath):
        return

    try:
        fsize = os.path.getsize(fpath)
        if fsize < 100:
            return
        with open(fpath, 'rb') as f:
            head = f.read(6)
    except Exception:
        return

    out_base = os.path.join(OUT_DIR, fname)
    tmp_path = out_base + '.tmp'

    if head[:4] == b'\x07\x08V2' or head == b'\x07\x08V1\x08\x07':
        result, fmt = v2_decrypt_file(fpath, tmp_path, AES_KEY, xor_key=0xFF)
    else:
        result, fmt = xor_decrypt_file(fpath, tmp_path)

    if result and os.path.exists(result):
        final = f"{out_base}.{fmt}"
        try:
            # os.replace 在 POSIX 上是原子操作：
            # 即使进程被杀，要么旧文件还在，要么新文件已就位，不会两者都没有
            os.replace(result, final)
            stats['new'] += 1
            month = extract_month(fpath)
            append_index(f"{fname}.{fmt}", month)
            print(f"[+] {month} → {fname}.{fmt} ({fsize//1024}KB) [new={stats['new']}]", flush=True)
        except Exception as e:
            print(f"[!] rename failed for {fname}: {e}", flush=True)
            try:
                os.unlink(result)
            except Exception:
                pass
    else:
        stats['fail'] += 1
        # 清理失败的 tmp
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass


def scan_existing():
    """启动时扫描所有缓存文件，只处理尚未解密的"""
    count = 0
    for root, dirs, files in os.walk(CACHE_BASE):
        if 'Sns' not in root:
            continue
        for fname in files:
            if '.' in fname:
                continue
            fpath = os.path.join(root, fname)
            existing = output_exists(fname)
            if existing:
                processed.add(fname)
                stats['skip'] += 1
            else:
                process_file(fpath)
                count += 1
    print(f"[*] 初始扫描完成：处理 {count} 个新文件，跳过 {stats['skip']} 个已有文件", flush=True)


def watch_with_polling():
    """轮询监控新文件"""
    seen = {}
    for root, dirs, files in os.walk(CACHE_BASE):
        if 'Sns' not in root:
            continue
        for f in files:
            if '.' not in f:
                fpath = os.path.join(root, f)
                try:
                    seen[fpath] = os.path.getmtime(fpath)
                except Exception:
                    pass

    print(f"[*] 开始监控: {CACHE_BASE}", flush=True)
    print(f"[*] 当前监控文件数: {len(seen)}", flush=True)

    while True:
        time.sleep(2)
        for root, dirs, files in os.walk(CACHE_BASE):
            if 'Sns' not in root:
                continue
            for f in files:
                if '.' in f:
                    continue
                fpath = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(fpath)
                except Exception:
                    continue
                if fpath not in seen or seen[fpath] != mtime:
                    seen[fpath] = mtime
                    process_file(fpath)


if __name__ == '__main__':
    print("=" * 60, flush=True)
    print("WeChat 朋友圈图片守护进程 (v2 - 原子写入)", flush=True)
    print(f"输出目录: {OUT_DIR}", flush=True)
    print(f"索引文件: {INDEX_FILE}", flush=True)
    print("=" * 60, flush=True)

    # 从索引文件加载已知文件（跨会话持久）
    known_from_index = load_indexed_files()
    print(f"[*] 索引记录已知文件: {len(known_from_index)} 张", flush=True)

    scan_existing()
    watch_with_polling()
