import base64, configparser, time, uuid, zlib
import asyncio
import aiohttp
from tqdm import tqdm
from loguru import logger

import os, json
from dp_helper import DPHelper

import json
semaphore = asyncio.Semaphore(100)  # Allow up to 50 concurrent tasks

# 假设JSON配置文件名为 'config.json'
json_file_path = 'config.json'

# 读取JSON文件并解析为字典
with open(json_file_path, 'r', encoding='utf-8') as file:
    config_data = json.load(file)

# 从字典中获取cookie
minutes_cookie = config_data.get('Cookies', {}).get('minutes_cookie')
# 假设JSON中没有单独的cookie路径配置项，如果需要，可以添加类似下面的代码
# minutes_cookie_path = config_data.get('CookiesPath', {}).get('minutes_cookie_path')

# 获取文件路径
file_path = config_data.get('上传设置', {}).get('要上传的文件所在路径（目前仅支持单个文件）')

# 获取代理设置
use_proxy = config_data.get('代理设置', {}).get('是否使用代理（是/否）')
proxy_address = config_data.get('代理设置', {}).get('代理地址')

# 根据use_proxy决定代理是否被使用
if use_proxy == '是':
    if proxy_address:
        proxies = {'http': proxy_address, 'https': proxy_address}
    else:
        proxies=None
else:
    proxies = None

# 打印一些配置项来验证
print(f"Minutes Cookie: {minutes_cookie}")
print(f"File Path: {file_path}")
print(f"Proxies: {proxies}")

homeurl = 'https://meetings.feishu.cn/minutes/home'
semaphore = asyncio.Semaphore(20)  # Allow up to 5 concurrent tasks

class FeishuUploader:
    def __init__(self, cookie=None, folder=None,json_cookie_path=None):
        self.folder = folder
        self.file_path = file_path
        self.block_size = 2**20*4
        self.json_cookie_path = json_cookie_path
        self.cookie = cookie
        self.upload_token = None
        self.headers = None
        self.vhid = None
        self.upload_id = None
        self.object_token = None
        self.file_header = None
        self.csrf_token = None
        self.session = None

    async def auto_cookie(self):
        if not self.cookie:
            print('there is no cookie provided')

            if os.path.exists(self.json_cookie_path):
                print(f'try to load from disk:{self.json_cookie_path}')

                with open(self.json_cookie_path, 'r', encoding='utf-8') as file:
                    self.cookie = json.load(file)
                print(f'load done from disk:{self.json_cookie_path}')

            else:
                print('please scan qrcode to get cookie')

                self.browser = DPHelper(browser_path=None, HEADLESS=False)

                self.cookie = self.browser.getCookie(homeurl)
        print('check cookie is ok')
        if not self.cookie:
            print('get cookie failed')
            return            
        print('check csrf is ok')
        
        self.csrf_token = self.cookie[self.cookie.find('bv_csrf_token=') + len('bv_csrf_token='):self.cookie.find(';', self.cookie.find('bv_csrf_token='))] if isinstance(self.cookie, str) else self.cookie.get('bv_csrf_token')
        if not self.csrf_token:
            print('get csrf token failed')
            return
        print('check header is ok')

        self.headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
            'cookie': self.cookie if isinstance(self.cookie, str) else self.cookie_dict2_str(self.cookie),
            'bv-csrf-token': self.csrf_token,
            'referer': 'https://minutes.feishu.cn/minutes/home'
        }
        print('check csrf length is ok')
                
        if len(self.headers.get('bv-csrf-token')) != 36:
            raise Exception("cookie中不包含bv_csrf_token，请确保从请求`list?size=20&`中获取！")
        print('===end to autocookie======')
        
    def cookie_dict2_str(self, jsondata):
        cookstr = ""
        print(f'convert cookie dict to str:\n{jsondata}')
        for k, v in jsondata.items():
            cookstr += k + "=" + v + "; "
        return cookstr

    async def get_quota(self):
        if not os.path.exists(self.file_path) or os.path.getsize(self.file_path) == 0:
            print('this video not found or broken')
            return
        try:
            with open(self.file_path, 'rb') as f:
                self.file_size = f.seek(0, 2)
                f.seek(0)
                self.file_header = base64.b64encode(f.read(512)).decode()             
        except Exception as e:
            print(f'video file: {self.file_path} cannot load：{e}')
        file_info = f'{uuid.uuid1()}_{self.file_size}'
        quota_url = f'https://meetings.feishu.cn/minutes/api/quota?file_info[]={file_info}&language=zh_cn'
        print(f'quota url:{quota_url}')
        try:
            async with self.session.get(quota_url, headers=self.headers, proxy=proxies) as response:
                quota_res = await response.json()
        except Exception as e:
            print(f'canot get quota:{e}')    
        if quota_res['data']['has_quota'] == False:
            raise Exception("飞书妙记空间已满，请清理后重试！")
        else:
            self.upload_token = quota_res['data']['upload_token'][file_info]
            return True

    async def prepare_upload(self):
        print('=====start to detect quota==========')
        if not await self.get_quota():
            print('please manually delete some files to release space')
            return
   
        file_name = self.file_path.split("\\")[-1]
        # 如果文件名中包含后缀，需要去掉后缀
        if '.' in file_name:
            file_name = file_name[:file_name.rfind('.')]
        print(f'format video filename:{file_name}')
        prepare_url = f'https://meetings.feishu.cn/minutes/api/upload/prepare'
        json_data = {
            'name': file_name,
            'file_size': self.file_size,
            'file_header': self.file_header,
            'drive_upload': True,
            'upload_token': self.upload_token,
        }
        async with self.session.post(prepare_url, headers=self.headers, proxy=proxies, json=json_data) as response:
            prepare_res = await response.json()
        self.vhid = prepare_res['data']['vhid']
        self.upload_id = prepare_res['data']['upload_id']
        self.object_token = prepare_res['data']['object_token']

    async def upload_one_block(self, upload_url, data, block_index):
        retries = 3
        for attempt in range(1, retries + 1):
            try:
                async with self.session.post(upload_url, proxy=proxies, headers=self.headers, data=data) as response:
                    if response.status == 200:
                        if await response.text():
                            logger.info(f"Task {self.file_path} completed on attempt {attempt}. Data: {block_index}")
                            return True
                    else:
                        print(f"Task {block_index} failed on attempt {attempt}.{proxies} Status code: {response.status}")
            except aiohttp.ClientConnectionError:
                if attempt < retries:
                    print(f"Task {block_index} failed on attempt {attempt}.{proxies} Retrying...")
                else:
                    print(f"Task {block_index} failed on all {retries} attempts. Skipping.")
            except Exception:
                if attempt < retries:
                    print(f"Task {block_index} failed on attempt {attempt}. Retrying...")
                else:
                    print(f"Task {block_index} failed on all {retries} attempts. Skipping.")

    async def upload_blocks(self):
        with open(self.file_path, 'rb') as f:
            f.seek(0)
            block_count = (self.file_size + self.block_size - 1) // self.block_size
            async with semaphore:
                tasks = []
                with tqdm(total=block_count, unit='block') as progress_bar:
                    for i in range(block_count):
                        block_data = f.read(self.block_size)
                        block_size = len(block_data)
                        checksum = zlib.adler32(block_data) & 0xffffffff
                        upload_url = f'https://internal-api-space.feishu.cn/space/api/box/stream/upload/block?upload_id={self.upload_id}&seq={i}&size={block_size}&checksum={checksum}'

                        task = asyncio.create_task(self.upload_one_block(upload_url=upload_url, data=block_data, block_index=i))
                        tasks.append(task)

                    results = await asyncio.gather(*tasks)

                    for r in results:
                        if r:
                            progress_bar.update(1)

    async def complete_upload(self):
        complete_url1 = f'https://internal-api-space.feishu.cn/space/api/box/upload/finish/'
        json_data = {
            'upload_id': self.upload_id,
            'num_blocks': (self.file_size + self.block_size - 1) // self.block_size,
            'vhid': self.vhid,
            'risk_detection_extra': '{\"source_terminal\":1,\"file_operate_usage\":3,\"locale\":\"zh_cn\"}'
        }
        async with self.session.post(complete_url1, headers=self.headers, proxy=proxies, json=json_data) as response:
            resp = await response.json()
        print(resp)

        complete_url2 = f'https://meetings.feishu.cn/minutes/api/upload/finish'
        json_data = {
            'auto_transcribe': True,
            'language': 'mixed',
            'num_blocks': (self.file_size + self.block_size - 1) // self.block_size,
            'upload_id': self.upload_id,
            'vhid': self.vhid,
            'upload_token': self.upload_token,
            'object_token': self.object_token,
        }
        async with self.session.post(complete_url2, headers=self.headers, proxy=proxies, json=json_data) as response:
            resp = await response.json()
        print(resp)

        # Check if transcription is complete after upload
        start_time = time.time()
        while True:
            await asyncio.sleep(3)
            object_status_url = f'https://meetings.feishu.cn/minutes/api/batch-status?object_token[]={self.object_token}&language=zh_cn'
            async with self.session.get(object_status_url, headers=self.headers, proxy=proxies) as response:
                object_status = await response.json()
            transcript_progress = object_status['data']['status'][0]['transcript_progress']
            spend_time = time.time() - start_time
            if object_status['data']['status'][0]['object_status'] == 2 or transcript_progress['current'] == '':
                print(f"\n转写完成！用时{spend_time}\nhttp://meetings.feishu.cn/minutes/{object_status['data']['status'][0]['object_token']}")
                break
            print(f"转写中...已用时{spend_time}\r", end='')

    async def do_one(self):
        retries = 3
        async with semaphore:
            for i in range(retries):
                try:


                    print(f'start to preparing video:{self.file_path}')
                    await self.prepare_upload()
                    print(f'start to uploading video:{self.file_path}')
                    await self.upload_blocks()
                    print(f'start to completing video:{self.file_path}')
                    await self.complete_upload()
                    break  # If successful, exit the loop
                except Exception as e:
                    print(f"Connection error on attempt {i+1}: {e}")
                    if i < retries - 1:
                        await asyncio.sleep(2**i)  # Exponential backoff strategy
                    else:
                        print("Max retries reached. Exiting.")
                        return

    async def upload(self):
        print(f'current video folder:{self.folder}')
        if not self.folder:
            print('please choose a valid video folder, at least 1 video file')
            return
        print('start to detect video files')
        tasks = []
        self.filetype = '.mp4'
        self.session=aiohttp.ClientSession()
        async with   self.session:
            for root, dirs, files in os.walk(self.folder):
                video_files = [file for file in files if file.endswith(self.filetype)]

                if video_files:
                    print('=====start to detect cookie======')

                    await self.auto_cookie()
                    
                    for file in video_files[:2]:
                        self.file_path = os.path.join(root, file)
                        self.json_cookie_path = './cookie.json'
                        print(f'start to processing video:{self.file_path}')

                        task = asyncio.create_task(self.do_one())
                        tasks.append(task)

                    await asyncio.gather(*tasks)
                else:
                    print(f'there is no video under folder:{self.folder}')

# Example usage:
# async def main():
#     uploader = FeishuUploader(cookie=minutes_cookie, folder='path/to/video/folder')
#     await uploader.upload()

# if __name__ == '__main__':
#     asyncio.run(main())