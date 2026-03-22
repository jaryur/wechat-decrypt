"""
Linux 版：从微信进程内存提取所有数据库 raw key
使用 /proc/[pid]/mem 替代 Windows ReadProcessMemory

用法：sudo python find_all_keys_linux.py
"""
import os, sys, re, json, struct, hashlib, hmac as hmac_mod
from Crypto.Cipher import AES
from config import load_config

_cfg = load_config()
DB_DIR   = _cfg["db_dir"]
OUT_FILE = _cfg["keys_file"]

KEY_PATTERN = re.compile(rb"x'([0-9a-fA-F]{96})'")  # 64hex key + 32hex salt = 96 hex chars

def get_wechat_pid() -> int:
    pid = _cfg.get("wechat_pid")
    if pid:
        try:
            os.kill(int(pid), 0)
            return int(pid)
        except ProcessLookupError:
            pass
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            exe = os.readlink(f"/proc/{p}/exe")
            if "wechat" in exe.lower():
                return int(p)
        except (PermissionError, FileNotFoundError):
            continue
    raise RuntimeError("找不到微信进程，请确认微信正在运行")

def read_maps(pid: int):
    """读取 /proc/[pid]/maps，返回可读内存区间列表"""
    regions = []
    with open(f"/proc/{pid}/maps") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            perms = parts[1]
            if "r" not in perms:
                continue
            addr_range = parts[0].split("-")
            start = int(addr_range[0], 16)
            end   = int(addr_range[1], 16)
            size  = end - start
            # 跳过过大区域（> 500MB）和太小区域
            if size > 500 * 1024 * 1024 or size < 4096:
                continue
            regions.append((start, end))
    return regions

def scan_memory(pid: int) -> list[bytes]:
    """扫描进程内存，找出所有 key 候选"""
    candidates = []
    mem_path = f"/proc/{pid}/mem"
    regions = read_maps(pid)
    print(f"扫描 {len(regions)} 个内存区域...")
    try:
        mem = open(mem_path, "rb")
    except PermissionError:
        print("权限不足，请用 sudo 运行")
        sys.exit(1)

    for start, end in regions:
        size = end - start
        try:
            mem.seek(start)
            data = mem.read(size)
        except (OSError, ValueError):
            continue
        for m in KEY_PATTERN.finditer(data):
            candidates.append(bytes.fromhex(m.group(1).decode()))
    mem.close()
    return candidates

def get_db_salt(db_path: str) -> bytes:
    with open(db_path, "rb") as f:
        return f.read(16)

PAGE_SZ    = 4096
RESERVE_SZ = 80   # IV(16) + HMAC(64)
IV_SZ      = 16
HMAC_SZ    = 64

PAGE_SZ    = 4096
RESERVE_SZ = 80   # IV(16) + HMAC(64)
IV_SZ      = 16
HMAC_SZ    = 64

def verify_key(enc_key: bytes, salt: bytes, db_path: str) -> bool:
    """SQLCipher 4 HMAC 验证：HMAC-SHA512 覆盖 page[16:4032] + pgno"""
    try:
        with open(db_path, "rb") as f:
            page = f.read(PAGE_SZ)
        if len(page) < PAGE_SZ:
            return False
        mac_salt = bytes(b ^ 0x3a for b in salt)
        mac_key  = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=32)
        # HMAC 在 page 末尾 64 字节
        hmac_val = page[PAGE_SZ - HMAC_SZ:]
        # HMAC 覆盖范围：salt之后到HMAC之前（不含16字节salt）+ 页码 LE
        body     = page[len(salt) : PAGE_SZ - HMAC_SZ]
        expected = hmac_mod.new(mac_key, body + struct.pack("<I", 1), hashlib.sha512).digest()
        return hmac_mod.compare_digest(hmac_val, expected)
    except Exception:
        return False

def find_all_keys():
    pid = get_wechat_pid()
    print(f"微信 PID: {pid}")

    # 收集所有 DB 文件
    db_files = []
    for root, _, files in os.walk(DB_DIR):
        for fn in files:
            if fn.endswith(".db"):
                db_files.append(os.path.join(root, fn))
    print(f"找到 {len(db_files)} 个数据库文件")

    candidates = scan_memory(pid)
    print(f"找到 {len(candidates)} 个密钥候选")

    results = {}
    for db_path in db_files:
        try:
            salt = get_db_salt(db_path)
        except Exception:
            continue
        for cand in candidates:
            enc_key = cand[:32]
            cand_salt = cand[32:]
            if cand_salt != salt:
                continue
            if verify_key(enc_key, salt, db_path):
                rel = os.path.relpath(db_path, DB_DIR)
                rel_win = rel.replace("/", "\\")
                sz = os.path.getsize(db_path)
                results[rel_win] = {
                    "enc_key": enc_key.hex(),
                    "salt": salt.hex(),
                    "size_mb": round(sz / 1024 / 1024, 1),
                }
                print(f"  ✓ {rel}")
                break

    print(f"\n共匹配 {len(results)}/{len(db_files)} 个数据库")
    with open(OUT_FILE, "w") as f:
        json.dump(results, f, indent=2)
    print(f"密钥已保存到 {OUT_FILE}")
    return results

if __name__ == "__main__":
    find_all_keys()
