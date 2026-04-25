#!/usr/bin/env python3
"""
测试单张朋友圈图片解密

Usage:
    # 传入完整路径
    python test_sns_decrypt.py <sns_image_path>

    # 或只传入文件名 (自动在 cache_dir 下搜索)
    python test_sns_decrypt.py 887111ed7967f445e9cb62427dfd7d

    # 或不传参数 (自动搜索并测试第一个 V2 文件)
    python test_sns_decrypt.py
"""
import os
import sys
import json
import glob
import struct
from Crypto.Cipher import AES
from Crypto.Util import Padding

V2_MAGIC_FULL = b'\x07\x08V2\x08\x07'


def detect_format(header_bytes):
    if header_bytes[:3] == bytes([0xFF, 0xD8, 0xFF]):
        return 'jpg'
    if header_bytes[:4] == bytes([0x89, 0x50, 0x4E, 0x47]):
        return 'png'
    if header_bytes[:3] == b'GIF':
        return 'gif'
    if header_bytes[:4] == b'RIFF':
        return 'webp'
    if header_bytes[:4] == b'wxgf':
        return 'hevc'
    return 'bin'


def decrypt(data, aes_key, xor_key):
    if len(data) < 15 or data[:6] != V2_MAGIC_FULL:
        return None, None

    aes_size, xor_size = struct.unpack_from('<LL', data, 6)
    aligned_aes_size = aes_size
    aligned_aes_size -= ~(~aligned_aes_size % 16)

    offset = 15
    aes_data = data[offset:offset + aligned_aes_size]

    cipher = AES.new(aes_key[:16], AES.MODE_ECB)
    dec_aes = Padding.unpad(cipher.decrypt(aes_data), AES.block_size)
    offset += aligned_aes_size

    raw_end = len(data) - xor_size
    raw_data = data[offset:raw_end] if offset < raw_end else b''

    xor_data = data[raw_end:]
    dec_xor = bytes(b ^ xor_key for b in xor_data)

    decrypted = dec_aes + raw_data + dec_xor
    fmt = detect_format(decrypted[:16])
    return decrypted, fmt


def find_file_in_cache(cache_dir, filename):
    """在 cache_dir 下搜索指定文件名的 V2 文件"""
    pattern = os.path.join(cache_dir, '*', 'Sns', 'Img', '*', filename)
    matches = glob.glob(pattern)
    for p in matches:
        if os.path.isfile(p):
            return p
    # 也尝试不加子目录限制的全局搜索
    for root, dirs, files in os.walk(cache_dir):
        if filename in files:
            return os.path.join(root, filename)
    return None


def find_first_v2_file(cache_dir):
    """找到第一个 V2 格式的朋友圈图片"""
    pattern = os.path.join(cache_dir, '*', 'Sns', 'Img', '*', '*')
    for p in glob.glob(pattern):
        if os.path.isfile(p) and '.' not in os.path.basename(p):
            try:
                with open(p, 'rb') as f:
                    if f.read(6) == V2_MAGIC_FULL:
                        return p
            except:
                pass
    return None


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.json')

    if not os.path.exists(config_path):
        print(f"[!] 找不到配置文件: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    cache_dir = config.get('cache_dir', '')
    if not cache_dir or not os.path.exists(cache_dir):
        # 尝试从 db_dir 推导
        db_dir = config.get('db_dir', '')
        if db_dir:
            cache_dir = os.path.join(os.path.dirname(db_dir), 'cache')

    # 解析命令行参数
    if len(sys.argv) >= 2:
        arg = sys.argv[1]
        if os.path.exists(arg):
            path = arg
        else:
            # 尝试在 cache_dir 下搜索
            path = find_file_in_cache(cache_dir, os.path.basename(arg))
            if not path:
                print(f"[!] 找不到文件: {arg}")
                print(f"    已搜索目录: {cache_dir}")
                sys.exit(1)
            print(f"[自动定位] 找到文件: {path}")
    else:
        # 不传入参数，自动找第一个 V2 文件
        path = find_first_v2_file(cache_dir)
        if not path:
            print(f"[!] 在 {cache_dir} 下找不到 V2 格式朋友圈图片")
            sys.exit(1)
        print(f"[自动定位] 找到第一个 V2 文件: {path}")

    # Load keys
    aes_key = config.get('image_aes_key', '').encode('ascii')[:16]
    xor_key = config.get('image_xor_key')

    print(f"\nAES key: {config.get('image_aes_key')} (len={len(aes_key)})")
    print(f"XOR key: 0x{xor_key:02x}" if xor_key is not None else "XOR key: None")

    with open(path, 'rb') as f:
        data = f.read()

    print(f"File: {path}")
    print(f"Size: {len(data)}")
    print(f"Header: {data[:15].hex()}")

    # Try configured xor_key
    if xor_key is not None:
        decrypted, fmt = decrypt(data, aes_key, xor_key)
        if decrypted and fmt != 'bin':
            out_path = path + f".test.{fmt}"
            with open(out_path, 'wb') as f:
                f.write(decrypted)
            print(f"\n[OK] Decrypted with XOR=0x{xor_key:02x} -> {fmt}")
            print(f"Saved: {out_path}")
            return

    # Try common xor keys
    for test_xor in [0x88, 0xc7, 0x00]:
        decrypted, fmt = decrypt(data, aes_key, test_xor)
        if decrypted and fmt != 'bin':
            out_path = path + f".test.{fmt}"
            with open(out_path, 'wb') as f:
                f.write(decrypted)
            print(f"\n[OK] Decrypted with XOR=0x{test_xor:02x} -> {fmt}")
            print(f"Saved: {out_path}")
            return

    print("\n[FAILED] Could not decrypt with any XOR key")


if __name__ == '__main__':
    main()
