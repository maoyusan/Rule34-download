#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描downloads文件夹并更新已下载文件记录
"""

import os
import json
import hashlib
from pathlib import Path

def get_file_hash(file_path):
    """计算文件的MD5哈希值"""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        print(f"计算文件哈希失败 {file_path}: {e}")
        return None

def scan_downloads_folder():
    """扫描downloads文件夹并生成已下载文件记录"""
    downloads_dir = Path("downloads")
    downloaded_files = {}
    
    if not downloads_dir.exists():
        print("downloads文件夹不存在！")
        return {}
    
    print("开始扫描downloads文件夹...")
    
    # 遍历所有子文件夹
    for subfolder in downloads_dir.iterdir():
        if subfolder.is_dir():
            print(f"扫描文件夹: {subfolder.name}")
            
            # 遍历子文件夹中的所有文件
            for file_path in subfolder.rglob("*"):
                if file_path.is_file():
                    # 计算文件哈希
                    file_hash = get_file_hash(file_path)
                    if file_hash:
                        # 使用相对路径作为键
                        relative_path = str(file_path.relative_to(downloads_dir))
                        downloaded_files[relative_path] = {
                            "hash": file_hash,
                            "size": file_path.stat().st_size,
                            "modified": file_path.stat().st_mtime
                        }
                        print(f"  添加文件: {relative_path}")
    
    print(f"扫描完成，共找到 {len(downloaded_files)} 个文件")
    return downloaded_files

def update_downloaded_files_config():
    """更新已下载文件配置文件"""
    # 扫描downloads文件夹
    downloaded_files = scan_downloads_folder()
    
    # 保存到配置文件
    config_file = "downloaded_files_config.json"
    try:
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(downloaded_files, f, indent=2, ensure_ascii=False)
        print(f"已更新配置文件: {config_file}")
        print(f"共记录 {len(downloaded_files)} 个已下载文件")
    except Exception as e:
        print(f"保存配置文件失败: {e}")

def main():
    """主函数"""
    print("=" * 50)
    print("扫描downloads文件夹并更新已下载文件记录")
    print("=" * 50)
    
    update_downloaded_files_config()
    
    print("\n扫描完成！")

if __name__ == "__main__":
    main()
