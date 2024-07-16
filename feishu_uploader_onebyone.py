import base64, configparser, time, uuid, zlib
from concurrent.futures import as_completed, ThreadPoolExecutor
import asyncio
import requests
from tqdm import tqdm
import aiohttp
from loguru import logger

import os,json
from dp_helper import DPHelper
# 读取配置文件
config = configparser.ConfigParser(interpolation=None)
config.read('config.ini', encoding='utf-8')
# 获取cookie
minutes_cookie = config.get('Cookies', 'minutes_cookie')
minutes_cookie_path = config.get('CookiesPath', 'minutes_cookie_path')

# 获取文件路径
file_path = config.get('上传设置', '要上传的文件所在路径（目前仅支持单个文件）')
# 获取代理设置
use_proxy = config.get('代理设置', '是否使用代理（是/否）')
proxy_address = config.get('代理设置', '代理地址')
if use_proxy == '是':
    proxies = {
        'http': proxy_address,
        'https': proxy_address,
    }
else:
    proxies = None
homeurl='https://meetings.feishu.cn/minutes/home'
semaphore = asyncio.Semaphore(5)  # Allow up to 50 concurrent tasks

class FeishuUploader:
    def __init__(self, cookie=None,folder=None):
        self.folder=folder
        self.file_path = file_path
        self.block_size = 2**20*4
        self.json_cookie_path=None
        self.cookie=cookie
        self.upload_token = None
        self.headers=None
        self.vhid = None
        self.upload_id = None
        self.object_token = None
        self.file_header=None
        self.csrf_token=None
    def auto_cookie(self):
        if not self.cookie:
            print('there is no cookie provided')

            if os.path.exists(self.json_cookie_path):
                print(f'try to load from disk:{self.json_cookie_path}')

                with open(self.json_cookie_path, 'r', encoding='utf-8') as file:
                    self.cookie=json.load(file)
                print(f'load done from disk:{self.json_cookie_path}')

            else:
                print('please scan qrcode to get cookie')

                self.browser=DPHelper(browser_path=None,HEADLESS=False)

                self.cookie=self.browser.getCookie(homeurl)
        print('check cookie is ok')
        if not self.cookie:
            print('get cookie failed')
            return            
        print('check csrf is ok')
        
        self.csrf_token=self.cookie[self.cookie.find('bv_csrf_token=') + len('bv_csrf_token='):self.cookie.find(';', self.cookie.find('bv_csrf_token='))] if isinstance(self.cookie,str) else self.cookie.get('bv_csrf_token')
        if not self.csrf_token:
            print('get csrf token failed')
            return             
        print('check header is ok')

        self.headers = {
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
                'cookie': self.cookie if isinstance(self.cookie,str) else self.cookie_dict2_str(self.cookie),
                'bv-csrf-token': self.csrf_token,
                'referer': 'https://minutes.feishu.cn/minutes/home'
            }
        print('check csrf length is ok')
                
        if len(self.headers.get('bv-csrf-token')) != 36:
            raise Exception("cookie中不包含bv_csrf_token，请确保从请求`list?size=20&`中获取！")
        print('===end to autocookie======')
        
    def cookie_dict2_str(self,jsondata):
        cookstr = ""
        print(f'convert cookie dict to str:\n{jsondata}')
        for k, v in jsondata.items():
            cookstr += k + "=" + v + "; "
        return cookstr

    def get_quota(self):
        if not os.path.exists(self.file_path) or os.path.getsize(self.file_path)==0:
            print('this video not found or broken')
            return
        try:
            with open(self.file_path, 'rb') as f:
                self.file_size = f.seek(0, 2)
                f.seek(0)
                self.file_header = base64.b64encode(f.read(512)).decode()             
        except Exception as e :
            print(f'video file: {self.file_path} cannot load：{e}')
        file_info = f'{uuid.uuid1()}_{self.file_size}'
        quota_url = f'https://meetings.feishu.cn/minutes/api/quota?file_info[]={file_info}&language=zh_cn'
        quota_res = requests.get(quota_url, headers=self.headers, proxies=proxies).json()
        if quota_res['data']['has_quota'] == False:
            raise Exception("飞书妙记空间已满，请清理后重试！")
        else:
            self.upload_token = quota_res['data']['upload_token'][file_info]
            return True
    # 分片上传文件（预上传）
    # doc: https://open.feishu.cn/document/server-docs/docs/drive-v1/upload/multipart-upload-file-/upload_prepare
    def prepare_upload(self):
        print('=====start to detect cookie======')
        self.auto_cookie()
        print('=====start to detect quota==========')
        if not self.get_quota():
            print('please manually delete some files to release space')
            return
   
        file_name = self.file_path.split("\\")[-1]
        # 如果文件名中包含后缀，需要去掉后缀
        if '.' in file_name:
            file_name = file_name[:file_name.rfind('.')]
        print(f'format video filename:{file_name}')
        prepare_url = f'https://meetings.feishu.cn/minutes/api/upload/prepare'
        json = {
            'name': file_name,
            'file_size': self.file_size,
            'file_header': self.file_header,
            'drive_upload': True,
            'upload_token': self.upload_token,
        }
        prepare_res = requests.post(prepare_url, headers=self.headers, proxies=proxies, json=json).json()
        self.vhid = prepare_res['data']['vhid']
        self.upload_id = prepare_res['data']['upload_id']
        self.object_token = prepare_res['data']['object_token']
    async def upload_one_block(self,upload_url,data,block_index):
        retries = 3
        for attempt in range(1, retries + 1):
            try:
                proxy_url=proxies

                proxy=proxy_url if proxy_url and 'http' in proxy_url else None
                async with aiohttp.ClientSession(connector=None) as session:    

                    async with session.post(upload_url,proxy=proxy,headers=self.headers,data=data) as response:
                        if response.status == 200:
                            if response.text:
                                logger.info(f"Task {self.file_path} completed on attempt {attempt}. Data: {data}")
                                return True
                        else:
                            print(f"Task {block_index} failed on attempt {attempt}.{proxy} Status code: {response.status}")
            except aiohttp.ClientConnectionError:
                if attempt < retries:
                    print(f"Task {block_index} failed on attempt {attempt}.{proxy} Retrying...")
                else:
                    print(f"Task {block_index} failed on all {retries} attempts. Skipping.")

            except Exception:
                if attempt < retries:
                    print(f"Task {block_index} failed on attempt {attempt}. Retrying...")
                else:
                    print(f"Task {block_index} failed on all {retries} attempts. Skipping.")


    # 分片上传文件（上传分片）
    # doc: https://open.feishu.cn/document/server-docs/docs/drive-v1/upload/multipart-upload-file-/upload_part
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

                        task = asyncio.create_task(self.upload_one_block(upload_url=upload_url,data=block_data,block_index=i
                                                                         ))
                        tasks.append(task)

                    results=await asyncio.gather(*tasks)

                    for r in results:
                        if r:
                            progress_bar.update(1)
    
    # 分片上传文件（完成上传）
    # doc: https://open.feishu.cn/document/server-docs/docs/drive-v1/upload/multipart-upload-file-/upload_finish
    def complete_upload(self):
        complete_url1 = f'https://internal-api-space.feishu.cn/space/api/box/upload/finish/'
        json = {
            'upload_id': self.upload_id,
            'num_blocks': (self.file_size + self.block_size - 1) // self.block_size,
            'vhid': self.vhid,
            'risk_detection_extra' : '{\"source_terminal\":1,\"file_operate_usage\":3,\"locale\":\"zh_cn\"}'
        }
        resp = requests.post(complete_url1, headers=self.headers, proxies=proxies, json=json).json()
        print(resp)

        complete_url2 = f'https://meetings.feishu.cn/minutes/api/upload/finish'
        json = {
            'auto_transcribe': True,
            'language': 'mixed',
            'num_blocks': (self.file_size + self.block_size - 1) // self.block_size,
            'upload_id': self.upload_id,
            'vhid': self.vhid,
            'upload_token': self.upload_token,
            'object_token': self.object_token,
        }
        resp = requests.post(complete_url2, headers=self.headers, proxies=proxies, json=json).json()
        print(resp)

        # 上传完成后检查是否转写完成
        start_time = time.time()
        while True:
            time.sleep(3)
            object_status_url = f'https://meetings.feishu.cn/minutes/api/batch-status?object_token[]={self.object_token}&language=zh_cn'
            object_status = requests.get(object_status_url, headers=self.headers, proxies=proxies).json()
            transcript_progress = object_status['data']['status'][0]['transcript_progress']
            spend_time = time.time() - start_time
            if object_status['data']['status'][0]['object_status'] == 2 or transcript_progress['current'] == '':
                print(f"\n转写完成！用时{spend_time}\nhttp://meetings.feishu.cn/minutes/{object_status['data']['status'][0]['object_token']}")
                break
            print(f"转写中...已用时{spend_time}\r", end='')
    async def do_one(self):


        retries = 3
        for i in range(retries):
            try:

                print(f'start to preparing video:{self.file_path}')

                self.prepare_upload()
                print(f'start to uploading video:{self.file_path}')
            
                self.upload_blocks()
                print(f'start to completing video:{self.file_path}')

                self.complete_upload()
                break  # 如果成功，则退出循环
            except Exception as e:
                print(f"Connection error on attempt {i+1}: {e}")
                if i < retries - 1:
                    await asyncio.sleep(2**i)  # 指数退避策略
                else:
                    print("Max retries reached. Exiting.")
                    return



    async def upload(self):
        print(f'current video folder:{self.folder}')
        if not self.folder:
            print('please choose a valid video folder,at least 1 video file')
            return
        print('start to detect video files')
        tasks=[]
        self.filetype='.mp4'
        for root, dirs, files in os.walk(self.folder):
            video_files = [file for file in files if file.endswith(self.filetype)]

            if video_files:
                for file in video_files:


                    self.file_path = os.path.join(root, file)
                    self.json_cookie_path='./cookie.json'
                    print(f'start to processing video:{self.file_path}')

                    task = asyncio.create_task(self.do_one())
                    tasks.append(task)

                await asyncio.gather(*tasks)

            else:
                print(f'there is no video under folder:{self.folder}')
# if __name__ == '__main__':

#     uploader = FeishuUploader(file_path, minutes_cookie)
#     uploader.upload()
