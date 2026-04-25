#!/usr/bin/env python3
"""
批量解密朋友圈 (Moments) V2 格式图片缓存

扫描 cache/YYYY-MM/Sns/Img/ 下的无扩展名文件，用 config.json 中的 AES/XOR key 解密

Usage:
    python decrypt_sns_images.py
"""
import os
import sys
import glob
import json
import struct
from Crypto.Cipher import AES
from Crypto.Util import Padding

# V2 格式签名
V2_MAGIC_FULL = b'\x07\x08V2\x08\x07'
V1_MAGIC_FULL = b'\x07\x08V1\x08\x07'


def detect_image_format(header_bytes):
    """根据解密后的文件头检测图片格式"""
    if header_bytes[:3] == bytes([0xFF, 0xD8, 0xFF]):
        return 'jpg'
    if header_bytes[:4] == bytes([0x89, 0x50, 0x4E, 0x47]):
        return 'png'
    if header_bytes[:3] == b'GIF':
        return 'gif'
    if header_bytes[:2] == b'BM':
        return 'bmp'
    if header_bytes[:4] == b'RIFF' and len(header_bytes) >= 12 and header_bytes[8:12] == b'WEBP':
        return 'webp'
    if header_bytes[:4] == bytes([0x49, 0x49, 0x2A, 0x00]):
        return 'tif'
    if header_bytes[:4] == b'wxgf':
        return 'hevc'
    return 'bin'


def v2_decrypt(data, aes_key, xor_key):
    """解密 V2 格式数据，返回 (decrypted_bytes, format) 或 (None, None)"""
    if len(data) < 15:
        return None, None

    sig = data[:6]
    if sig not in (V2_MAGIC_FULL, V1_MAGIC_FULL):
        return None, None

    aes_size, xor_size = struct.unpack_from('<LL', data, 6)

    # AES 对齐: 向上取整到 16 的倍数 (PKCS7 填充)
    aligned_aes_size = aes_size
    aligned_aes_size -= ~(~aligned_aes_size % 16)

    offset = 15
    if offset + aligned_aes_size > len(data):
        return None, None

    # AES-ECB 解密
    aes_data = data[offset:offset + aligned_aes_size]
    try:
        cipher = AES.new(aes_key[:16], AES.MODE_ECB)
        dec_aes = Padding.unpad(cipher.decrypt(aes_data), AES.block_size)
    except (ValueError, KeyError):
        return None, None
    offset += aligned_aes_size

    # Raw 部分 (不加密)
    raw_end = len(data) - xor_size
    raw_data = data[offset:raw_end] if offset < raw_end else b''
    offset = raw_end

    # XOR 部分
    xor_data = data[offset:]
    dec_xor = bytes(b ^ xor_key for b in xor_data)

    decrypted = dec_aes + raw_data + dec_xor
    fmt = detect_image_format(decrypted[:16])
    return decrypted, fmt


def try_derive_xor_key(data):
    """尝试从文件末尾推导 XOR key (假设是 JPEG/PNG/WEBP 的已知结尾)"""
    if len(data) < 2:
        return None
    tail = data[-2:]

    # JPEG 结尾: FF D9
    k = tail[0] ^ 0xFF
    if tail[1] ^ k == 0xD9:
        return k

    # PNG 结尾: AE 42 60 82 (取最后 2 字节)
    k = tail[0] ^ 0xAE
    if tail[1] ^ k == 0x42:
        return k

    # WEBP 没有固定结尾，跳过
    return None


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.json')

    if not os.path.exists(config_path):
        print(f"[!] 找不到配置文件: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    db_dir = config.get('db_dir', '')
    if not db_dir or not os.path.exists(db_dir):
        print(f"[!] db_dir 不存在: {db_dir}")
        sys.exit(1)

    base_dir = os.path.dirname(db_dir)
    sns_img_dir = os.path.join(base_dir, 'cache')
    out_dir = config.get('decoded_image_dir', os.path.join(script_dir, 'decoded_images'))
    out_dir = os.path.join(out_dir, 'sns')
    os.makedirs(out_dir, exist_ok=True)

    # 获取 key
    aes_key_str = config.get('image_aes_key')
    xor_key = config.get('image_xor_key')

    if not aes_key_str:
        print("[!] config.json 中没有 image_aes_key")
        print("    请先运行 find_image_key_monitor.py 或 find_image_key.py 获取 key")
        sys.exit(1)

    aes_key = aes_key_str.encode('ascii')[:16]
    if len(aes_key) < 16:
        print(f"[!] AES key 长度不足 16 字节: {aes_key_str} (len={len(aes_key)})")
        sys.exit(1)

    print(f"AES key: {aes_key_str} (len={len(aes_key)})")
    if xor_key is not None:
        print(f"XOR key: 0x{xor_key:02x}")
    else:
        print("XOR key: 未设置，将尝试自动推导")

    # 扫描所有 Sns/Img 下的文件
    search_pattern = os.path.join(sns_img_dir, '*', 'Sns', 'Img', '*', '*')
    all_files = glob.glob(search_pattern)

    # 过滤出无扩展名且是文件的
    v2_files = []
    for path in all_files:
        if os.path.isfile(path) and '.' not in os.path.basename(path):
            try:
                with open(path, 'rb') as f:
                    head = f.read(6)
                if head == V2_MAGIC_FULL:
                    v2_files.append(path)
            except:
                pass

    print(f"\n找到 {len(v2_files)} 个 V2 格式朋友圈图片")

    if not v2_files:
        print("[!] 没有找到 V2 格式文件")
        print(f"    搜索路径: {search_pattern}")
        return

    success = 0
    failed = 0
    formats = {}
    xor_fallbacks = {}

    for idx, path in enumerate(v2_files, 1):
        try:
            with open(path, 'rb') as f:
                data = f.read()

            # 使用配置中的 xor_key，如果没有则尝试推导
            current_xor = xor_key
            if current_xor is None:
                current_xor = try_derive_xor_key(data)

            if current_xor is None:
                # 尝试常见值
                for test_xor in [0x88, 0xc7, 0x00]:
                    dec, fmt = v2_decrypt(data, aes_key, test_xor)
                    if dec and fmt != 'bin':
                        current_xor = test_xor
                        xor_fallbacks[test_xor] = xor_fallbacks.get(test_xor, 0) + 1
                        break
                else:
                    failed += 1
                    print(f"  [{idx}/{len(v2_files)}] FAILED: {os.path.basename(path)} (无法推导 XOR key)")
                    continue

            decrypted, fmt = v2_decrypt(data, aes_key, current_xor)
            if not decrypted or fmt == 'bin':
                failed += 1
                print(f"  [{idx}/{len(v2_files)}] FAILED: {os.path.basename(path)} (解密失败或未知格式)")
                continue

            # 保存文件
            fname = os.path.basename(path)
            out_path = os.path.join(out_dir, f"{fname}.{fmt}")
            with open(out_path, 'wb') as f:
                f.write(decrypted)

            success += 1
            formats[fmt] = formats.get(fmt, 0) + 1
            if idx <= 5 or idx % 50 == 0:
                print(f"  [{idx}/{len(v2_files)}] OK: {fname}.{fmt} ({len(decrypted):,}B)")

        except Exception as e:
            failed += 1
            print(f"  [{idx}/{len(v2_files)}] ERROR: {os.path.basename(path)}: {e}")

    print(f"\n{'='*60}")
    print(f"解密完成: {success} 成功, {failed} 失败")
    print(f"格式分布: {formats}")
    if xor_fallbacks:
        print(f"自动推导的 XOR key: { {f'0x{k:02x}': v for k, v in xor_fallbacks.items()} }")
    print(f"输出目录: {out_dir}")


if __name__ == '__main__':
    main()
