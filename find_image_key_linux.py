"""
Linux 版：从微信进程内存提取朋友圈图片 V2 AES 密钥

原理：
  V2 图片用 AES-128-ECB 加密，header 后的第一个 16 字节 ECB 块
  解密后应为 JPEG/PNG/WEBP 等已知文件头。
  扫描内存所有16字节候选，逐块验证。

用法：sudo python find_image_key_linux.py
"""
import os, sys, struct
sys.path.insert(0, os.path.dirname(__file__))

from Crypto.Cipher import AES

# 已知图片文件头（解密后第一个16字节应以这些开头）
KNOWN_HEADERS = [
    bytes([0xFF, 0xD8, 0xFF]),          # JPEG
    bytes([0x89, 0x50, 0x4E, 0x47]),    # PNG
    bytes([0x47, 0x49, 0x46, 0x38]),    # GIF
    bytes([0x52, 0x49, 0x46, 0x46]),    # WEBP/RIFF
    bytes([0x42, 0x4D]),                # BMP
]

V2_MAGIC_FULL = b'\x07\x08V2\x08\x07'


def get_wechat_pid():
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            exe = os.readlink(f"/proc/{p}/exe")
            if "wechat" in exe.lower():
                return int(p)
        except (PermissionError, FileNotFoundError):
            continue
    raise RuntimeError("找不到微信进程")


def load_v2_first_block(dat_path):
    """从 V2 文件提取第一个 AES 密文块（16字节），返回 (first_block, xor_key_hint)"""
    with open(dat_path, 'rb') as f:
        data = f.read(15 + 16)  # header(15) + 第一个AES块(16)
    if len(data) < 15 + 16:
        return None, None
    sig = data[:6]
    if sig != V2_MAGIC_FULL:
        return None, None
    # offset 15 开始是 AES 密文
    first_block = data[15:31]
    # 从文件尾部拿 xor 部分的第一字节推断 xor_key（可选）
    return first_block, None


def check_key(aes_key_bytes, enc_block):
    """用候选 key 解密第一个 ECB 块，检查是否是合法图片头"""
    try:
        cipher = AES.new(aes_key_bytes, AES.MODE_ECB)
        dec = cipher.decrypt(enc_block)
        for hdr in KNOWN_HEADERS:
            if dec[:len(hdr)] == hdr:
                return True
    except Exception:
        pass
    return False


def read_maps(pid):
    regions = []
    with open(f"/proc/{pid}/maps") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2 or 'r' not in parts[1]:
                continue
            start, end = [int(x, 16) for x in parts[0].split('-')]
            size = end - start
            # 只扫堆和匿名 rw 区域，跳过文件映射和过大区域
            mapped_file = parts[5] if len(parts) > 5 else ''
            if mapped_file and not mapped_file.startswith('/dev') and mapped_file != '[heap]' and mapped_file != '[stack]' and not mapped_file.startswith('[anon'):
                pass  # 文件映射也要扫，密钥可能在 .so 数据段
            if size > 200 * 1024 * 1024 or size < 4096:
                continue
            regions.append((start, end))
    return regions


def collect_unique_test_blocks(n=8):
    """收集 n 个第一AES块各不同的SNS图片文件"""
    base = os.path.expanduser('~/Documents/xwechat_files')
    seen_blocks = set()
    test_cases = []  # [(filepath, first_enc_block), ...]

    for root, dirs, files in os.walk(base):
        if 'Sns' not in root or 'Img' not in root:
            continue
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                with open(fp, 'rb') as f:
                    data = f.read(15 + 16)
                if data[:6] != V2_MAGIC_FULL or len(data) < 31:
                    continue
                block = data[15:31]
                if block not in seen_blocks:
                    seen_blocks.add(block)
                    test_cases.append((fp, block))
            except Exception:
                continue
            if len(test_cases) >= n:
                break
        if len(test_cases) >= n:
            break

    return test_cases


def find_image_key():
    pid = get_wechat_pid()
    print(f"微信 PID: {pid}")

    test_cases = collect_unique_test_blocks(8)
    if len(test_cases) < 2:
        print("找不到足够的测试文件")
        return None

    print(f"使用 {len(test_cases)} 个不同内容的测试块：")
    for fp, blk in test_cases:
        print(f"  {os.path.basename(fp)}: {blk.hex()}")

    # 提取所有唯一密文块列表（用于验证）
    enc_blocks = [blk for _, blk in test_cases]
    required_hits = len(enc_blocks)  # 必须全部命中

    print(f"\n开始扫描内存（全部 {required_hits} 块都需命中才算真密钥）...")
    regions = read_maps(pid)
    print(f"共 {len(regions)} 个内存区域")

    try:
        mem = open(f"/proc/{pid}/mem", "rb")
    except PermissionError:
        print("权限不足，请用 sudo 运行")
        sys.exit(1)

    found_keys = []
    total_bytes = 0

    for start, end in regions:
        size = end - start
        try:
            mem.seek(start)
            data = mem.read(size)
        except (OSError, ValueError):
            continue

        total_bytes += len(data)

        # 每16字节对齐扫
        for i in range(0, len(data) - 16, 16):
            candidate = data[i:i+16]
            # 快速过滤低熵候选
            if len(set(candidate)) < 6:
                continue

            # 必须命中所有不同块
            if all(check_key(candidate, blk) for blk in enc_blocks):
                print(f"\n✓ 找到密钥: {candidate.hex()}")
                found_keys.append(candidate.hex())

        if total_bytes % (50 * 1024 * 1024) < size:
            print(f"  已扫描 {total_bytes // 1024 // 1024} MB...")

    mem.close()

    # 去重
    found_keys = list(dict.fromkeys(found_keys))

    if not found_keys:
        print("未找到匹配密钥")
        return None

    print(f"\n找到 {len(found_keys)} 个候选密钥")
    best_key = found_keys[0]

    key_file = os.path.expanduser('~/wechat-decrypt/image_aes_key.txt')
    with open(key_file, 'w') as f:
        for k in found_keys:
            f.write(k + '\n')
    print(f"已保存到: {key_file}")
    return best_key


if __name__ == '__main__':
    key = find_image_key()
    if key:
        print(f"\n图片AES密钥: {key}")
