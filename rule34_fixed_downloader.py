import requests
import re
import os
import time
import json
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import signal
import sys

# 默认配置
DEFAULT_CONFIG = {
    "tags": "mightyniku video",
    "max_workers": 2
}

def get_default_download_dir():
    """根据默认配置的tags[0]创建下载目录"""
    tags = DEFAULT_CONFIG["tags"].split()
    if tags:
        first_tag = tags[0]
        download_dir = os.path.join("downloads", first_tag)
        os.makedirs(download_dir, exist_ok=True)
        return download_dir
    return "downloads"

CONFIG_FILE = "rule34_config.json"

def save_config(config):
    """保存配置到文件"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"✅ 配置已保存到 {CONFIG_FILE}")
    except Exception as e:
        print(f"❌ 保存配置失败: {e}")

def load_config():
    """从文件加载配置"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            print(f"✅ 配置已从 {CONFIG_FILE} 加载")
            return config
        else:
            print(f"⚠️ 配置文件 {CONFIG_FILE} 不存在，使用默认配置")
            return DEFAULT_CONFIG
    except Exception as e:
        print(f"❌ 加载配置失败: {e}，使用默认配置")
        return DEFAULT_CONFIG

class Rule34FixedDownloader:
    def __init__(self, max_workers=3):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        })
        
        self.lock = threading.Lock()
        self.downloaded_count = 0
        self.total_posts = 0
        self.downloaded_urls = set()  # 记录已下载的URL，避免重复
        self.max_workers = max_workers  # 并发线程数
        
        # 重复文件检测
        self.downloaded_files_config = "downloaded_files_config.json"
        self.downloaded_files = self.load_downloaded_files()
        
        # 帖子检测记录
        self.detected_posts_config = "detected_posts_config.json"
        self.detected_posts = self.load_detected_posts()
        
        # 程序控制标志
        self.should_stop = False
        self.active_downloads = set()  # 记录正在下载的任务
        
        # 缩略图URL正则表达式
        self.thumbnail_pattern = re.compile(
            r'https://wimg\.rule34\.xxx/thumbnails/(\d+)/thumbnail_([a-f0-9]+)\.jpg\?(\d+)',
            re.IGNORECASE
        )
        
        # 注册信号处理器
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """处理Ctrl+C信号，优雅退出"""
        with self.lock:
            print("\n🛑 检测到中断信号，正在优雅退出...")
            print(f"📊 当前正在下载 {len(self.active_downloads)} 个文件")
            print(f"📈 已处理帖子总数: {self.total_posts}")
            print(f"💾 已记录帖子数: {len(self.detected_posts)}")
            print(f"📥 已下载文件数: {self.downloaded_count}")
            self.should_stop = True
            
            if self.active_downloads:
                print("⏳ 正在中断当前下载...")
                # 等待所有活跃下载完成，但设置超时
                timeout = 10  # 最多等待10秒
                while self.active_downloads and timeout > 0:
                    time.sleep(1)
                    timeout -= 1
                    print(f"⏳ 还有 {len(self.active_downloads)} 个文件正在下载... (剩余 {timeout} 秒)")
                
                if self.active_downloads:
                    print(f"⚠️ 仍有 {len(self.active_downloads)} 个下载未完成，强制退出")
                else:
                    print("✅ 所有下载已完成")
            
            # 保存当前进度
            self.save_detected_posts()
            print("💾 已保存检测到的帖子记录")
            print("👋 程序已安全退出")
            sys.exit(0)
    
    def load_detected_posts(self):
        """加载已检测的帖子记录"""
        detected_posts = set()
        if os.path.exists(self.detected_posts_config):
            try:
                with open(self.detected_posts_config, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                    if 'posts' in config_data:
                        for post_info in config_data['posts']:
                            if 'post_id' in post_info:
                                detected_posts.add(post_info['post_id'])
                print(f"📋 已加载 {len(detected_posts)} 个已检测帖子记录")
            except Exception as e:
                print(f"⚠️ 读取已检测帖子记录失败: {e}")
        else:
            print("📋 未找到已检测帖子记录，将创建新记录")
        return detected_posts
    
    def save_detected_posts(self):
        """保存已检测的帖子记录到JSON配置文件"""
        try:
            # 生成配置文件
            config_data = {
                "scan_time": datetime.now().isoformat(),
                "total_posts": len(self.detected_posts),
                "posts": [{"post_id": post_id} for post_id in sorted(self.detected_posts)]
            }
            
            # 保存配置文件
            with open(self.detected_posts_config, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
            
            print(f"💾 已保存 {len(self.detected_posts)} 个帖子记录到 {self.detected_posts_config}")
        except Exception as e:
            print(f"❌ 保存帖子记录失败: {e}")
    
    def is_post_detected(self, post_id):
        """检查帖子是否已检测过"""
        return post_id in self.detected_posts
    
    def add_detected_post(self, post_id):
        """添加已检测的帖子到记录"""
        self.detected_posts.add(post_id)
    
    # 移除waifu2x处理方法 - 完全跳过增强版链接
    
    def load_downloaded_files(self):
        """加载已下载文件记录"""
        downloaded_files = set()
        if os.path.exists(self.downloaded_files_config):
            try:
                with open(self.downloaded_files_config, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                    if 'files' in config_data:
                        for file_info in config_data['files']:
                            if 'filename' in file_info:
                                downloaded_files.add(file_info['filename'])
                print(f"📋 已加载 {len(downloaded_files)} 个已下载文件记录")
            except Exception as e:
                print(f"⚠️ 读取已下载文件记录失败: {e}")
        else:
            print("📋 未找到已下载文件记录，将创建新记录")
        return downloaded_files
    
    def save_downloaded_files(self, download_dir="downloads"):
        """保存已下载文件记录到JSON配置文件"""
        try:
            # 扫描下载目录获取文件信息
            file_details = []
            total_size = 0
            
            for root, dirs, files in os.walk(download_dir):
                for file in files:
                    if file.lower().endswith(('.mp4', '.webm', '.avi', '.mov', '.mkv', '.flv', '.wmv')):
                        file_path = os.path.join(root, file)
                        try:
                            file_size = os.path.getsize(file_path)
                            file_mtime = os.path.getmtime(file_path)
                            current_dir = os.path.relpath(root, download_dir)
                            if current_dir == ".":
                                current_dir = "根目录"
                            
                            file_details.append({
                                "filename": file,
                                "filepath": file_path,
                                "size": file_size,
                                "size_mb": round(file_size / (1024 * 1024), 2),
                                "modified_time": datetime.fromtimestamp(file_mtime).isoformat(),
                                "directory": current_dir,
                                "extension": os.path.splitext(file)[1].lower()
                            })
                            total_size += file_size
                        except OSError:
                            continue
            
            # 按目录和文件名排序
            file_details.sort(key=lambda x: (x["directory"], x["filename"]))
            
            # 生成配置文件
            config_data = {
                "scan_time": datetime.now().isoformat(),
                "download_directory": download_dir,
                "total_files": len(file_details),
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "files": file_details
            }
            
            # 保存配置文件
            with open(self.downloaded_files_config, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
            
            print(f"💾 已保存 {len(file_details)} 个文件记录到 {self.downloaded_files_config}")
            print(f"📊 总大小: {config_data['total_size_mb']} MB")
        except Exception as e:
            print(f"❌ 保存文件记录失败: {e}")
    
    def generate_file_list_summary(self, download_dir="downloads"):
        """生成文件列表摘要信息"""
        try:
            if not os.path.exists(self.downloaded_files_config):
                print("⚠️ 文件列表配置文件不存在，请先运行扫描")
                return
            
            with open(self.downloaded_files_config, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            print(f"\n📋 文件列表摘要:")
            print(f"   📁 扫描目录: {config_data['download_directory']}")
            print(f"   📅 扫描时间: {config_data['scan_time']}")
            print(f"   📊 文件总数: {config_data['total_files']}")
            print(f"   💾 总大小: {config_data['total_size_mb']} MB")
            
            # 按目录统计
            dir_stats = {}
            for file_info in config_data['files']:
                dir_name = file_info['directory']
                if dir_name not in dir_stats:
                    dir_stats[dir_name] = {'count': 0, 'size': 0}
                dir_stats[dir_name]['count'] += 1
                dir_stats[dir_name]['size'] += file_info['size']
            
            print(f"\n📁 按目录统计:")
            for dir_name, stats in sorted(dir_stats.items()):
                size_mb = round(stats['size'] / (1024 * 1024), 2)
                print(f"   📁 {dir_name}: {stats['count']} 个文件 ({size_mb} MB)")
            
            # 按扩展名统计
            ext_stats = {}
            for file_info in config_data['files']:
                ext = file_info['extension']
                if ext not in ext_stats:
                    ext_stats[ext] = {'count': 0, 'size': 0}
                ext_stats[ext]['count'] += 1
                ext_stats[ext]['size'] += file_info['size']
            
            print(f"\n📄 按扩展名统计:")
            for ext, stats in sorted(ext_stats.items()):
                size_mb = round(stats['size'] / (1024 * 1024), 2)
                print(f"   📄 {ext}: {stats['count']} 个文件 ({size_mb} MB)")
                
        except Exception as e:
            print(f"❌ 生成文件列表摘要失败: {e}")
    
    def is_file_downloaded(self, filename):
        """检查文件是否已下载"""
        return filename in self.downloaded_files
    
    def check_file_exists_with_size(self, filename, download_dir="downloads"):
        """检查文件是否存在且大小大于0"""
        # 首先检查记录
        if filename in self.downloaded_files:
            return True, "记录中存在"
        
        # 检查实际文件
        for root, dirs, files in os.walk(download_dir):
            if filename in files:
                file_path = os.path.join(root, filename)
                try:
                    file_size = os.path.getsize(file_path)
                    if file_size > 0:
                        return True, f"文件存在 ({file_size} 字节)"
                    else:
                        return False, "文件大小为0"
                except OSError:
                    return False, "无法读取文件"
        
        return False, "文件不存在"
    
    def add_downloaded_file(self, filename):
        """添加已下载文件到记录"""
        self.downloaded_files.add(filename)
    
    def find_files_by_post_id(self, post_id, download_dir="downloads"):
        """根据post_id查找已存在的文件"""
        existing_files = []
        
        # 检查配置文件中的文件
        for filename in self.downloaded_files:
            if f"_{post_id}" in filename or f"_{post_id}_" in filename:
                existing_files.append(filename)
        
        # 检查文件系统中的文件
        if os.path.exists(download_dir):
            for root, dirs, files in os.walk(download_dir):
                for file in files:
                    if f"_{post_id}" in file or f"_{post_id}_" in file:
                        if file not in existing_files:
                            existing_files.append(file)
        
        return existing_files
    
    def sync_existing_files(self, download_dir="downloads"):
        """同步现有文件到记录中，递归遍历所有文件夹"""
        if not os.path.exists(download_dir):
            print(f"⚠️ 下载目录 {download_dir} 不存在，将创建该目录")
            os.makedirs(download_dir, exist_ok=True)
            return
        
        print(f"🔍 正在扫描 {download_dir} 目录...")
        existing_files = set()
        scanned_dirs = 0
        scanned_files = 0
        total_size = 0
        
        # 递归遍历所有子目录
        for root, dirs, files in os.walk(download_dir):
            scanned_dirs += 1
            current_dir = os.path.relpath(root, download_dir)
            if current_dir == ".":
                current_dir = "根目录"
            
            video_files_in_dir = []
            dir_size = 0
            for file in files:
                scanned_files += 1
                if file.lower().endswith(('.mp4', '.webm', '.avi', '.mov', '.mkv', '.flv', '.wmv')):
                    video_files_in_dir.append(file)
                    existing_files.add(file)
                    try:
                        file_size = os.path.getsize(os.path.join(root, file))
                        dir_size += file_size
                        total_size += file_size
                    except OSError:
                        continue
            
            if video_files_in_dir:
                size_mb = dir_size / (1024 * 1024)
                print(f"  📁 {current_dir}: 找到 {len(video_files_in_dir)} 个视频文件 ({size_mb:.1f} MB)")
        
        total_size_mb = total_size / (1024 * 1024)
        print(f"📊 扫描完成: {scanned_dirs} 个目录, {scanned_files} 个文件, {len(existing_files)} 个视频文件 (总计 {total_size_mb:.1f} MB)")
        
        # 检查记录中的文件是否仍然存在
        missing_files = []
        for recorded_file in list(self.downloaded_files):
            if recorded_file not in existing_files:
                missing_files.append(recorded_file)
        
        # 移除不存在的文件记录
        for missing_file in missing_files:
            self.downloaded_files.discard(missing_file)
        
        # 添加新发现的文件到记录中
        added_count = 0
        for filename in existing_files:
            if filename not in self.downloaded_files:
                self.downloaded_files.add(filename)
                added_count += 1
        
        if missing_files:
            print(f"🗑️ 移除了 {len(missing_files)} 个不存在的文件记录")
        if added_count > 0:
            print(f"➕ 添加了 {added_count} 个新文件到记录中")
        
        if missing_files or added_count > 0:
            self.save_downloaded_files(download_dir)
            print(f"💾 已更新文件记录，当前记录 {len(self.downloaded_files)} 个文件")
        else:
            print(f"✅ 文件记录已是最新状态，当前记录 {len(self.downloaded_files)} 个文件")
    
    def cleanup_zero_size_files(self, download_dir="downloads"):
        """清理0字节的文件"""
        print(f"🧹 正在检查 {download_dir} 中的0字节文件...")
        zero_size_files = []
        
        for root, dirs, files in os.walk(download_dir):
            for file in files:
                if file.lower().endswith(('.mp4', '.webm', '.avi', '.mov', '.mkv', '.flv', '.wmv')):
                    file_path = os.path.join(root, file)
                    try:
                        if os.path.getsize(file_path) == 0:
                            zero_size_files.append(file_path)
                    except OSError:
                        continue
        
        if zero_size_files:
            print(f"⚠️ 发现 {len(zero_size_files)} 个0字节文件:")
            for file_path in zero_size_files:
                print(f"  🗑️ {file_path}")
            
            # 询问是否删除
            response = input("是否删除这些0字节文件? (y/N): ").strip().lower()
            if response in ['y', 'yes']:
                deleted_count = 0
                for file_path in zero_size_files:
                    try:
                        os.remove(file_path)
                        filename = os.path.basename(file_path)
                        self.downloaded_files.discard(filename)  # 从记录中移除
                        deleted_count += 1
                        print(f"  ✅ 已删除: {file_path}")
                    except OSError as e:
                        print(f"  ❌ 删除失败: {file_path} - {e}")
                
                if deleted_count > 0:
                    self.save_downloaded_files(download_dir)
                    print(f"💾 已删除 {deleted_count} 个0字节文件并更新记录")
        else:
            print("✅ 没有发现0字节文件")
    
    def normalize_video_url(self, url):
        """标准化视频URL，统一域名但不解析waifu2x链接"""
        # 统一rule34域名，保留查询参数
        parsed = urlparse(url)
        
        # 统一所有rule34子域名为wimg.rule34.xxx
        if 'rule34.xxx' in parsed.netloc:
            netloc = 'wimg.rule34.xxx'
        else:
            netloc = parsed.netloc.replace('//', '/')
        
        # 重新构建URL，保留查询参数
        normalized = f"{parsed.scheme}://{netloc}{parsed.path}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        
        print(f"🔍 标准化URL: {url} -> {normalized}")
        return normalized
    
    # 移除get_real_video_url方法 - 不再处理waifu2x链接
    
    def is_valid_video_url(self, url):
        """检查URL是否为有效的视频链接"""
        # 完全拒绝waifu2x链接
        if 'waifu2x' in url.lower():
            return False
            
        # 排除无效的URL域名
        invalid_domains = [
            'waifu2x.booru.pics',
            'waifu2x.udp.jp', 
            'waifu2x.0t0.nu',
            'waifu2x.c64.org'
        ]
        
        for domain in invalid_domains:
            if domain in url:
                return False
        
        # 检查是否为视频文件
        video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.webm']
        return any(ext in url.lower() for ext in video_extensions)
    
    def generate_search_urls(self, tags):
        """根据标签生成搜索URL列表，不预先检测页面数量"""
        # 不再预先检测所有页面，而是返回一个生成器
        # 让下载过程动态检测页面
        print(f"🔍 准备开始逐页处理，每页42个帖子...")
        print(f"📋 页面数量将在处理过程中动态检测")
        return []  # 返回空列表，让下载过程自己处理
    
    def extract_post_ids_from_page(self, page_url, show_details=True):
        """从搜索结果页面提取所有帖子ID"""
        # 添加延迟避免请求过快
        time.sleep(0.5)
        
        # 重试机制处理429错误
        max_retries = 100
        for attempt in range(max_retries):
            try:
                response = self.session.get(page_url, timeout=30)
                response.raise_for_status()
                break  # 成功则跳出重试循环
                
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:  # Too Many Requests
                    if attempt < max_retries - 1:  # 不是最后一次尝试
                        wait_time = 3 + attempt  # 递增等待时间：3, 4, 5, 6, 7...
                        with self.lock:
                            print(f"⚠️ 页面 {page_url} 遇到429错误，等待 {wait_time} 秒后重试... (尝试 {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        with self.lock:
                            print(f"❌ 页面 {page_url} 重试 {max_retries} 次后仍然429错误，跳过")
                        return []
                else:
                    # 其他HTTP错误直接抛出
                    raise
            except Exception as e:
                # 非HTTP错误直接抛出
                raise
        
        try:
            
            # 使用正则表达式提取帖子ID
            post_ids = []
            
            # 方法1: 从缩略图URL提取ID
            thumbnail_matches = self.thumbnail_pattern.findall(response.text)
            for match in thumbnail_matches:
                folder_id, hash_id, query_id = match
                post_ids.append(query_id)
            
            # 方法2: 从帖子链接提取ID
            post_link_pattern = r'page=post&s=view&id=(\d+)'
            link_matches = re.findall(post_link_pattern, response.text)
            post_ids.extend(link_matches)
            
            # 去重
            unique_post_ids = list(set(post_ids))
            
            # 显示检测进度（如果启用）
            if show_details:
                with self.lock:
                    print(f"🔍 页面检测完成: 找到 {len(unique_post_ids)} 个帖子ID")
                    if unique_post_ids:
                        print(f"📋 帖子列表:")
                        for i, post_id in enumerate(unique_post_ids, 1):
                            print(f"  {i:2d}. 帖子ID: {post_id}")
            
            # 注意：这里不记录帖子，只有在成功下载后才记录
            # 记录逻辑在 process_single_post 方法中
            
            return unique_post_ids
            
        except Exception as e:
            with self.lock:
                print(f"❌ 页面分析失败: {e}")
            return []
    
    def extract_video_url_from_post(self, post_id):
        """从单个帖子提取视频下载链接"""
        # 检查是否应该停止
        if self.should_stop:
            return []
            
        post_url = f"https://rule34.xxx/index.php?page=post&s=view&id={post_id}"
        
        # 添加1秒延迟避免429错误
        time.sleep(1)
        
        # 重试机制处理429错误
        max_retries = 100
        for attempt in range(max_retries):
            try:
                response = self.session.get(post_url, timeout=30)
                response.raise_for_status()
                break  # 成功则跳出重试循环
                
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:  # Too Many Requests
                    if attempt < max_retries - 1:  # 不是最后一次尝试
                        wait_time = 3 + attempt  # 递增等待时间：3, 4, 5, 6, 7...
                        with self.lock:
                            print(f"⚠️ 帖子 {post_id} 遇到429错误，等待 {wait_time} 秒后重试... (尝试 {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        with self.lock:
                            print(f"❌ 帖子 {post_id} 重试 {max_retries} 次后仍然429错误，跳过")
                        return []
                else:
                    # 其他HTTP错误直接抛出
                    raise
            except Exception as e:
                # 非HTTP错误直接抛出
                raise
        
        try:
            
            # 使用BeautifulSoup解析HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            video_urls = []
            processed_urls = set()  # 用于去重，存储标准化后的URL
            direct_video_urls = []  # 存储直接视频链接
            
            # 使用CSS选择器查找Original image链接
            original_links = soup.select('#post-view > div.sidebar > div:nth-child(6) > ul > li:nth-child(2) > a')
            
            for link in original_links:
                href = link.get('href')
                if href:
                    # 处理相对URL
                    if href.startswith('//'):
                        href = 'https:' + href
                    elif href.startswith('/'):
                        href = 'https://rule34.xxx' + href
                    
                    # 标准化URL并去重
                    normalized_url = self.normalize_video_url(href)
                    
                    if normalized_url and normalized_url not in processed_urls:
                        if self.is_valid_video_url(href):
                            # 只处理非waifu2x链接
                            if 'waifu2x' not in href.lower():
                                direct_video_urls.append(href)
                                processed_urls.add(normalized_url)
                            else:
                                print(f"🚫 跳过waifu2x增强版链接")
                        else:
                            print(f"❌ URL无效")
                    else:
                        print(f"⚠️ 跳过重复URL")
            
            # 如果通过"Original image"没有找到视频，再用正则表达式查找
            if not direct_video_urls:
                # 只匹配非waifu2x的.mp4链接
                video_pattern = r'href="((?!.*waifu2x)[^"]*\.mp4[^"]*)"'
                regex_matches = re.findall(video_pattern, response.text, re.IGNORECASE)
                for match in regex_matches:
                    if match.startswith('//'):
                        match = 'https:' + match
                    elif match.startswith('/'):
                        match = 'https://rule34.xxx' + match
                    
                    # 标准化URL并去重
                    normalized_url = self.normalize_video_url(match)
                    
                    if normalized_url and normalized_url not in processed_urls:
                        if self.is_valid_video_url(match):
                            # 只处理非waifu2x链接
                            if 'waifu2x' not in match.lower():
                                direct_video_urls.append(match)
                                processed_urls.add(normalized_url)
                            else:
                                print(f"🚫 跳过waifu2x增强版链接")
                        else:
                            print(f"❌ URL无效")
                    else:
                        print(f"⚠️ 跳过重复URL")
            
            # 只使用直接视频链接，完全跳过waifu2x链接
            if direct_video_urls:
                # 如果找到多个链接，只取第一个（避免重复下载）
                if len(direct_video_urls) > 1:
                    print(f"⚠️ 发现多个视频链接，只下载第一个")
                    video_urls = [direct_video_urls[0]]
                else:
                    video_urls = direct_video_urls
            else:
                video_urls = []
            
            with self.lock:
                if video_urls:
                    print(f"✅ 帖子 {post_id} 找到 {len(video_urls)} 个有效视频")
                else:
                    print(f"⚠️ 帖子 {post_id} 没有找到有效视频")
            
            return video_urls
            
        except Exception as e:
            with self.lock:
                print(f"❌ 帖子分析失败: {e}")
            return []
    
    def generate_unique_filename(self, video_url, post_id, download_dir):
        """生成唯一的文件名"""
        parsed_url = urlparse(video_url)
        original_filename = os.path.basename(parsed_url.path)
        
        # 如果没有扩展名，添加.mp4
        if not original_filename.endswith(('.mp4', '.avi', '.mov', '.mkv', '.webm')):
            original_filename = f"video_{post_id}.mp4"
        
        # 提取文件名和扩展名
        name, ext = os.path.splitext(original_filename)
        
        # 生成唯一文件名 - 使用hash格式匹配配置文件中的命名规则
        # 从URL中提取hash部分（如果存在）或使用原始文件名
        if '_' in name and len(name.split('_')[0]) == 32:  # 32位hash格式
            hash_part = name.split('_')[0]
            filename = f"{hash_part}_{post_id}{ext}"
        else:
            # 如果没有hash格式，使用原始文件名
            filename = f"{name}_{post_id}{ext}"
        
        filepath = os.path.join(download_dir, filename)
        
        # 检查文件名是否在配置文件中已存在或文件系统中已存在
        counter = 1
        while self.is_file_downloaded(filename) or os.path.exists(filepath):
            if '_' in name and len(name.split('_')[0]) == 32:
                hash_part = name.split('_')[0]
                # 使用更明确的重复文件标识，避免与post_id混淆
                filename = f"{hash_part}_{post_id}_duplicate_{counter}{ext}"
            else:
                filename = f"{name}_{post_id}_duplicate_{counter}{ext}"
            filepath = os.path.join(download_dir, filename)
            counter += 1
        
        return filepath
    
    def download_video(self, video_url, post_id, download_dir="downloads"):
        """下载单个视频文件"""
        try:
            # 检查是否应该停止
            if self.should_stop:
                return None
                
            # 检查是否已经下载过这个URL
            with self.lock:
                if video_url in self.downloaded_urls:
                    print(f"⚠️ URL已下载过，跳过")
                    return None
                    
                print(f"📥 开始下载帖子 {post_id}")
                # 记录活跃下载任务
                self.active_downloads.add(f"{post_id}_{video_url}")
            
            # 创建下载目录
            os.makedirs(download_dir, exist_ok=True)
            
            # 先检查是否已经有相同post_id的文件存在
            existing_files = self.find_files_by_post_id(post_id, download_dir)
            if existing_files:
                print(f"⚠️ 帖子 {post_id} 的文件已存在: {existing_files[0]}，跳过下载")
                return None
            
            # 生成唯一文件名
            filepath = self.generate_unique_filename(video_url, post_id, download_dir)
            filename = os.path.basename(filepath)
            
            # 检查生成的文件名是否与已存在的文件重复
            if self.is_file_downloaded(filename):
                print(f"⚠️ 文件 {filename} 在配置文件中已存在，跳过下载")
                return None
            
            # 检查文件是否已下载过（包括文件大小检查）
            exists, reason = self.check_file_exists_with_size(filename, download_dir)
            if exists:
                print(f"⚠️ 文件 {filename} 已下载过 ({reason})，跳过")
                return None
            
            # 下载文件
            response = self.session.get(video_url, stream=True, timeout=60)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    # 检查是否应该停止
                    if self.should_stop:
                        print(f"\n🛑 检测到停止信号，中断下载: {filename}")
                        # 移除活跃下载任务
                        with self.lock:
                            self.active_downloads.discard(f"{post_id}_{video_url}")
                        return None
                    
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        
                        if total_size > 0:
                            progress = (downloaded_size / total_size) * 100
                            with self.lock:
                                print(f"\r📊 下载进度: {progress:.1f}% ({downloaded_size}/{total_size} 字节)", end='')
            
            with self.lock:
                print(f"\n✅ 下载完成: {filename}")
                self.downloaded_count += 1
                self.downloaded_urls.add(video_url)  # 记录URL避免重复
                self.add_downloaded_file(filename)  # 添加文件到记录
                # 移除活跃下载任务
                self.active_downloads.discard(f"{post_id}_{video_url}")
            
            return filepath
            
        except Exception as e:
            with self.lock:
                print(f"\n❌ 下载失败: {e}")
                # 移除活跃下载任务
                self.active_downloads.discard(f"{post_id}_{video_url}")
            return None
    
    def process_single_post(self, post_id, download_dir="downloads"):
        """处理单个帖子"""
        # 检查是否应该停止
        if self.should_stop:
            return []
            
        with self.lock:
            print(f"🔄 开始处理帖子 {post_id}...")
        
        video_urls = self.extract_video_url_from_post(post_id)
        downloaded_files = []
        processed_successfully = False
        
        for video_url in video_urls:
            filepath = self.download_video(video_url, post_id, download_dir)
            if filepath:
                downloaded_files.append(filepath)
                processed_successfully = True
            else:
                # 即使文件已存在，也算处理成功
                processed_successfully = True
            time.sleep(3)  # 每个视频间隔3秒
        
        # 无论是否下载新文件，都记录帖子为已处理
        if processed_successfully:
            self.add_detected_post(post_id)
            if downloaded_files:
                print(f"✅ 帖子 {post_id} 下载成功，已记录")
            else:
                print(f"✅ 帖子 {post_id} 文件已存在，已记录")
        else:
            print(f"⚠️ 帖子 {post_id} 无有效视频，未记录")
        
        return downloaded_files
    
    def download_videos_by_tags(self, tags, download_dir="downloads"):
        """根据标签下载视频，逐页处理：检测一页→下载一页→记录→下一页"""
        print("🚀 Rule34 修复版视频下载器")
        print("="*80)
        print(f"🏷️ 搜索标签: {tags}")
        print(f"📄 页数: 动态检测")
        print(f"🧵 并发线程数: {self.max_workers}")
        print(f"📦 处理模式: 逐页处理")
        print("="*80)
        
        # 初始化页面参数
        page_num = 1
        pid = 0
        
        all_downloaded_files = []
        total_processed_posts = 0
        
        # 逐页处理：检测一页，下载一页
        while True:
            if self.should_stop:
                print("\n🛑 检测到停止信号，停止处理...")
                break
            
            print(f"\n🔄 开始处理第 {page_num} 页")
            print("="*60)
            
            # 构建当前页URL
            page_url = f"https://rule34.xxx/index.php?page=post&s=list&tags={tags}&pid={pid}"
            print(f"🔗 URL: {page_url}")
            
            # 步骤1: 检测当前页的帖子ID
            print(f"🔍 步骤1: 检测第 {page_num} 页的帖子...")
            page_post_ids = self.extract_post_ids_from_page(page_url, show_details=True)
            
            if not page_post_ids:
                print(f"📄 第 {page_num} 页没有找到内容，停止搜索")
                break
            
            # 过滤掉已检测的帖子
            new_post_ids = [post_id for post_id in page_post_ids if post_id not in self.detected_posts]
            
            print(f"📋 第 {page_num} 页检测到 {len(page_post_ids)} 个帖子")
            print(f"🆕 其中 {len(new_post_ids)} 个新帖子需要处理")
            print(f"⏭️ 跳过已检测: {len(page_post_ids) - len(new_post_ids)} 个")
            
            if not new_post_ids:
                print(f"⏭️ 第 {page_num} 页无新帖子，跳过")
                # 继续下一页
                page_num += 1
                pid += 42
                time.sleep(5)  # 保持间隔
                continue
            
            # 步骤2: 下载当前页的所有帖子
            print(f"📥 步骤2: 下载第 {page_num} 页的帖子...")
            page_downloaded_files = []
            page_processed_posts = 0
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # 提交当前页的任务
                future_to_post = {
                    executor.submit(self.process_single_post, post_id, download_dir): post_id 
                    for post_id in new_post_ids
                }
                
                # 处理完成的任务
                try:
                    for future in as_completed(future_to_post, timeout=1):
                        # 检查是否应该停止
                        if self.should_stop:
                            print("\n🛑 检测到停止信号，取消剩余任务...")
                            # 取消所有未完成的任务
                            for f in future_to_post:
                                f.cancel()
                            break
                            
                        post_id = future_to_post[future]
                        try:
                            downloaded_files = future.result()
                            if downloaded_files:  # 只有成功下载新文件才计数
                                page_downloaded_files.extend(downloaded_files)
                            
                            # 无论是否下载新文件，都算处理了一个帖子
                            page_processed_posts += 1
                            
                            # 进度基于已处理的帖子数量
                            progress = (page_processed_posts / len(new_post_ids)) * 100
                            
                            with self.lock:
                                print(f"📈 页面进度: {progress:.1f}% ({page_processed_posts}/{len(new_post_ids)}) - 帖子 {post_id}")
                                
                        except Exception as e:
                            with self.lock:
                                print(f"❌ 处理帖子 {post_id} 时出错: {e}")
                except TimeoutError:
                    # 超时检查是否应该停止
                    if self.should_stop:
                        print("\n🛑 检测到停止信号，取消剩余任务...")
                        for f in future_to_post:
                            f.cancel()
                        break
            
            # 步骤3: 页面完成检查
            print(f"💾 步骤3: 检查第 {page_num} 页完成情况...")
            
            # 检查是否所有帖子都处理完成
            remaining_posts = [post_id for post_id in page_post_ids if post_id not in self.detected_posts]
            
            if remaining_posts:
                print(f"⚠️ 第 {page_num} 页还有 {len(remaining_posts)} 个帖子未完成:")
                for post_id in remaining_posts:
                    print(f"  - 帖子ID: {post_id}")
                print(f"💾 保存当前进度，下次运行将从第 {page_num} 页继续...")
                # 保存当前进度并停止
                self.save_detected_posts()
                break
            else:
                print(f"✅ 第 {page_num} 页所有帖子处理完成!")
                all_downloaded_files.extend(page_downloaded_files)
                total_processed_posts += page_processed_posts
            
            print(f"\n📊 第 {page_num} 页统计:")
            print(f"  总帖子数: {len(page_post_ids)}")
            print(f"  已处理: {len(page_post_ids) - len(remaining_posts)}")
            print(f"  剩余: {len(remaining_posts)}")
            print(f"  下载文件: {len(page_downloaded_files)}")
            print(f"  累计处理: {total_processed_posts}")
            print(f"  累计下载: {len(all_downloaded_files)}")
            
            # 保存当前进度
            self.save_detected_posts()
            
            # 准备下一页
            page_num += 1
            pid += 42
            
            # 页面间隔
            print(f"⏳ 等待5秒后处理下一页...")
            time.sleep(5)
        
        self.total_posts = total_processed_posts
        return all_downloaded_files
    
    def save_results(self, downloaded_files, tags, filename="download_results.json"):
        """保存下载结果"""
        data = {
            'download_time': time.strftime("%Y-%m-%d %H:%M:%S"),
            'tags': tags,
            'total_posts': self.total_posts,
            'downloaded_count': self.downloaded_count,
            'downloaded_files': downloaded_files
        }
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        print(f"💾 结果已保存到: {filename}")
    
    def print_duplicate_check_info(self):
        """打印重复文件检查信息"""
        print(f"\n📋 重复文件检查信息:")
        print(f"   📁 配置文件中记录的文件数: {len(self.downloaded_files)}")
        print(f"   🔍 已加载的重复文件记录: {len(self.downloaded_files)} 个")
        if self.downloaded_files:
            print(f"   📝 示例文件名: {list(self.downloaded_files)[:3]}...")
        print("="*50)

    def print_final_statistics(self, downloaded_files, tags):
        """打印最终统计信息"""
        print("\n" + "="*80)
        print("📊 最终统计")
        print("="*80)
        print(f"🏷️ 搜索标签: {tags}")
        print(f"📋 总帖子数: {self.total_posts}")
        print(f"📥 下载成功: {len(downloaded_files)}")
        print(f"❌ 下载失败: {self.total_posts - len(downloaded_files)}")
        print(f"📈 成功率: {(len(downloaded_files)/self.total_posts*100):.1f}%" if self.total_posts > 0 else "0%")
        print(f"🔄 重复文件检查: 已跳过 {len(self.downloaded_files)} 个已存在的文件")
        
        if downloaded_files:
            print(f"\n📁 下载的文件:")
            for i, filepath in enumerate(downloaded_files, 1):
                print(f"  {i:2d}. {filepath}")
        
        print("="*80)

def get_user_input():
    """获取用户输入 - 支持配置选择"""
    print("🚀 Rule34 修复版视频下载器")
    print("="*50)
    
    # 配置选择
    print("⚙️ 配置选择:")
    print("   1 - 使用默认配置")
    print("   0 - 手动输入配置")
    
    while True:
        choice = input("🔧 请选择 (1/0): ").strip()
        if choice in ['1', '0']:
            break
        print("❌ 请输入 1 或 0!")
    
    if choice == '1':
        # 使用默认配置
        config = load_config()
        tags_input = config['tags']
        max_workers = config['max_workers']
        
        print(f"✅ 使用配置:")
        print(f"   🏷️ 标签: {tags_input}")
        print(f"   🧵 线程数: {max_workers}")
        print(f"   📄 页数: 自动检测")
        
        # 处理标签格式
        tag_list = [tag.strip() for tag in tags_input.split() if tag.strip()]
        tags = '+'.join(tag_list)
        
        return tags, max_workers
    
    else:
        # 手动输入配置
        print("\n📝 请输入搜索标签:")
        print("   示例: test1 test2 或 2futas video")
        print("   多个标签用空格分隔")
        tags_input = input("🏷️ 标签: ").strip()
        
        if not tags_input:
            print("❌ 标签不能为空!")
            return None, None
        
        # 处理标签格式 - 用空格分隔
        tag_list = [tag.strip() for tag in tags_input.split() if tag.strip()]
        tags = '+'.join(tag_list)
        
        print(f"✅ 处理后的标签: {tags}")
        
        # 获取线程数输入
        print("\n🧵 请输入并发线程数:")
        print("   示例: 3 (推荐3-5个线程)")
        print("   注意: 线程数过多可能导致请求过快被限制")
        try:
            max_workers = int(input("🧵 线程数: ").strip())
            if max_workers <= 0:
                print("❌ 线程数必须大于0!")
                return None, None
            if max_workers > 10:
                print("⚠️ 警告: 线程数过多可能导致请求过快被限制!")
        except ValueError:
            print("❌ 请输入有效的数字!")
            return None, None
        
        print(f"✅ 将自动检测页面数量，使用 {max_workers} 个并发线程")
        
        # 询问是否保存为默认配置
        print("\n💾 是否保存当前配置为默认配置?")
        save_choice = input("💾 保存配置? (y/n): ").strip().lower()
        if save_choice in ['y', 'yes', '是']:
            config = {
                "tags": tags_input,
                "max_workers": max_workers
            }
            save_config(config)
        
        return tags, max_workers

def main():
    print("🚀 Rule34 修复版视频下载器")
    print("="*80)
    
    # 根据默认配置的tags[0]创建下载目录
    download_dir = get_default_download_dir()
    print(f"📁 使用下载目录: {download_dir}")
    
    # 创建下载器
    downloader = Rule34FixedDownloader(max_workers=1)  # 先创建默认下载器用于扫描
    
    # 自动扫描下载文件夹并生成文件列表
    print(f"📁 正在自动扫描 {download_dir} 文件夹...")
    downloader.sync_existing_files(download_dir)
    
    # 生成并保存文件列表 JSON
    print("💾 正在生成文件列表 JSON...")
    downloader.save_downloaded_files(download_dir)
    
    # 显示文件列表摘要
    downloader.generate_file_list_summary(download_dir)
    
    # 显示重复文件检查信息
    downloader.print_duplicate_check_info()
    
    # 清理0字节文件
    print("\n🧹 检查0字节文件...")
    downloader.cleanup_zero_size_files(download_dir)
    
    # 获取用户输入
    tags, max_workers = get_user_input()
    
    if tags is None or max_workers is None:
        print("❌ 输入无效，程序退出")
        return
    
    # 重新创建下载器（使用用户指定的线程数）
    downloader = Rule34FixedDownloader(max_workers=max_workers)
    
    # 重新加载文件记录（确保使用最新的扫描结果）
    downloader.downloaded_files = downloader.load_downloaded_files()
    
    # 开始下载
    downloaded_files = downloader.download_videos_by_tags(tags, download_dir)
    
    # 保存文件记录（更新后的）
    downloader.save_downloaded_files(download_dir)
    
    # 保存帖子检测记录
    downloader.save_detected_posts()
    
    # 保存结果
    downloader.save_results(downloaded_files, tags)
    
    # 打印统计
    downloader.print_final_statistics(downloaded_files, tags)
    
    if downloaded_files:
        print(f"\n🎉 下载完成! 共下载 {len(downloaded_files)} 个视频文件")
    else:
        print("❌ 没有成功下载任何文件")

if __name__ == "__main__":
    main()
