import configparser, locale, os, re, subprocess, time, asyncio
import aiohttp
from tqdm import tqdm

from loguru import logger

import os, json
from dp_helper import DPHelper


locale.setlocale(locale.LC_CTYPE, "chinese")

# Read configuration file
import json

# 假设JSON配置文件名为 'config.json'
json_file_path = 'config.json'

# 读取JSON文件并解析为字典
with open(json_file_path, 'r', encoding='utf-8') as file:
    config_data = json.load(file)

# 从字典中提取配置项
minutes_cookie = config_data['Cookies']['minutes_cookie']
manager_cookie = config_data['Cookies']['manager_cookie']
space_name = config_data['下载设置']['所在空间']
list_size = config_data['下载设置']['每次检查的妙记数量']
check_interval = config_data['下载设置']['检查妙记的时间间隔（单位s，太短容易报错）']
download_type = config_data['下载设置']['文件类型']
subtitle_only = config_data['下载设置']['是否只下载字幕文件（是/否）'] == '是'
usage_threshold = float(config_data['下载设置']['妙记额度删除阈值（GB，填写了manager_cookie才有效）'])
save_path = config_data['下载设置']['保存路径（不填则默认为当前路径/data）'] or './data'
subtitle_params = {
    'add_speaker': config_data['下载设置']['字幕参数']['字幕是否包含说话人（是/否）'] == '是',
    'add_timestamp': config_data['下载设置']['字幕参数']['字幕是否包含时间戳（是/否）'] == '是',
    'format': 3 if config_data['下载设置']['字幕参数']['字幕格式（srt/txt）'] == 'srt' else 2
}
use_proxy = config_data['代理设置']['是否使用代理（是/否）']
proxy_address = config_data['代理设置']['代理地址'] if use_proxy == '是' else None

# 将数值类型的配置项转换为适当的类型
space_name = int(space_name)
list_size = int(list_size)
check_interval = int(check_interval)
download_type = int(download_type)
usage_threshold = float(usage_threshold)

# 打印一些配置项来验证
print(f"Minutes Cookie: {minutes_cookie}")
print(f"Space Name: {space_name}")
print(f"Subtitle Only: {subtitle_only}")
print(f"Save Path: {save_path}")
print(f"Proxies: {proxy_address}")
homeurl = 'https://home.feishu.cn/admin/index'

class FeishuDownloader:
    def __init__(self, cookie=None,json_cookie_path=None):
        self.headers = None
        self.cookie = cookie
        self.csrf_token = None
        self.json_cookie_path = json_cookie_path

        self.meeting_time_dict = {}
        self.subtitle_type = 'srt' if subtitle_params['format'] == 3 else 'txt'

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

                self.cookie = self.browser.getCookie(homeurl,json_cookie_path=self.json_cookie_path)
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
        
    async def get_minutes(self):
        """
        批量获取妙记信息
        """
        get_rec_url = f'https://meetings.feishu.cn/minutes/api/space/list?&size={list_size}&space_name={space_name}'
        async with aiohttp.ClientSession() as session:
            async with session.get(url=get_rec_url, headers=self.headers, proxy=proxy_address) as resp:
                data = await resp.json()
                if 'list' not in data['data']:
                    raise Exception("minutes_cookie失效，请重新获取！")
                return list(reversed(data['data']['list']))

    async def check_minutes(self):
        """
        检查需要下载的妙记
        """
        downloaded_minutes = set()
        if os.path.exists('minutes.txt'):
            with open('minutes.txt', 'r') as f:
                downloaded_minutes = set(line.strip() for line in f)
        
        all_minutes = await self.get_minutes()

        need_download_minutes = [
            minutes for minutes in all_minutes
            if minutes['object_token'] not in downloaded_minutes and
            (download_type == 2 or minutes['object_type'] == download_type)
        ]

        if need_download_minutes:
            await self.download_minutes(need_download_minutes)
            with open('minutes.txt', 'a') as f:
                for minutes in need_download_minutes:
                    f.write(minutes['object_token']+'\n')
            print(f"成功下载了{len(need_download_minutes)}个妙记，等待{check_interval}s后再次检查...")

    async def download_minutes(self, minutes_list):
        """
        使用aria2批量下载妙记
        """
        async with aiohttp.ClientSession() as session:
            tasks = [self.get_minutes_url(session, minutes) for minutes in minutes_list]
            results = await asyncio.gather(*tasks)

        with open('links.temp', 'w', encoding='utf-8') as file:
            for video_url, file_name in results:
                video_name = file_name
                file.write(f'{video_url}\n out={save_path}/{file_name}/{video_name}.mp4\n')

        if not subtitle_only:
            headers_option = ' '.join(f'--header="{k}: {v}"' for k, v in self.headers.items())
            proxy_cmd = f'--all-proxy={proxy_address}' if proxy_address else ""
            cmd = f'aria2c -c --input-file=links.temp {headers_option} --continue=true --auto-file-renaming=true --console-log-level=warn {proxy_cmd} -s16 -x16 -k1M'
            subprocess.run(cmd, shell=True)

        os.remove('links.temp')

        for file_name, start_time in self.meeting_time_dict.items():
            os.utime(f'{save_path}/{file_name}', (start_time, start_time))
            if not subtitle_only:
                os.utime(f'{save_path}/{file_name}/{file_name}.mp4', (start_time, start_time))
            os.utime(f'{save_path}/{file_name}/{file_name}.{self.subtitle_type}', (start_time, start_time))
        self.meeting_time_dict = {}

    async def get_minutes_url(self, session, minutes):
        """
        获取妙记视频下载链接；写入字幕文件。
        """
        video_url_url = f'https://meetings.feishu.cn/minutes/api/status?object_token={minutes["object_token"]}&language=zh_cn&_t={int(time.time() * 1000)}'
        async with session.get(url=video_url_url, headers=self.headers, proxy=proxy_address) as resp:
            data = await resp.json()
            video_url = data['data']['video_info']['video_download_url']

        subtitle_url = f'https://meetings.feishu.cn/minutes/api/export'
        params = subtitle_params.copy()
        params['object_token'] = minutes['object_token']
        async with session.post(url=subtitle_url, params=params, headers=self.headers, proxy=proxy_address) as resp:
            subtitle_text = await resp.text()

        file_name = re.sub(r'[\/\\\:\*\?\"\<\>\|]', '_', minutes['topic'])
        
        if minutes['object_type'] == 0:
            start_time = time.strftime("%Y年%m月%d日%H时%M分", time.localtime(minutes['start_time'] / 1000))
            stop_time = time.strftime("%Y年%m月%d日%H时%M分", time.localtime(minutes['stop_time'] / 1000))
            file_name = start_time + "至" + stop_time + file_name
        else:
            create_time = time.strftime("%Y年%m月%d日%H时%M分", time.localtime(minutes['create_time'] / 1000))
            file_name = create_time + file_name
        
        subtitle_name = file_name
            
        os.makedirs(f'{save_path}/{file_name}', exist_ok=True)

        with open(f'{save_path}/{file_name}/{subtitle_name}.{self.subtitle_type}', 'w', encoding='utf-8') as f:
            f.write(subtitle_text)
        
        if minutes['object_type'] == 0:
            self.meeting_time_dict[file_name] = minutes['start_time']/1000

        return video_url, file_name

    async def delete_minutes(self, num):
        """
        删除指定数量的最早几个妙记
        """
        all_minutes = await self.get_minutes()

        async with aiohttp.ClientSession() as session:
            for index in tqdm(all_minutes[:num], desc='删除妙记'):
                try:
                    delete_url = f'https://meetings.feishu.cn/minutes/api/space/delete'
                    params = {'object_tokens': index['object_token'],
                            'is_destroyed': 'false',
                            'language': 'zh_cn'}
                    async with session.post(url=delete_url, params=params, headers=self.headers, proxy=proxy_address) as resp:
                        if resp.status != 200:
                            raise Exception(f"删除妙记 http://meetings.feishu.cn/minutes/{index['object_token']} 失败！{await resp.json()}")

                    params['is_destroyed'] = 'true'
                    async with session.post(url=delete_url, params=params, headers=self.headers, proxy=proxy_address) as resp:
                        if resp.status != 200:
                            raise Exception(f"删除妙记 http://meetings.feishu.cn/minutes/{index['object_token']} 失败！{await resp.json()}")
                except Exception as e:
                    print(f"{e} 可能是没有该妙记的权限。")
                    num += 1
                    continue

async def main():
    downloader = FeishuDownloader(json_cookie_path='./download-cookie.json')

    await downloader.auto_cookie()

    if not minutes_cookie:
        raise Exception("cookie不能为空！")
    
    elif not manager_cookie:
        while True:
            print(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()))
            if await downloader.check_minutes():
                await downloader.delete_minutes(1)
            await asyncio.sleep(check_interval)

    else:
        x_csrf_token = manager_cookie[manager_cookie.find(' csrf_token=') + len(' csrf_token='):manager_cookie.find(';', manager_cookie.find(' csrf_token='))]
        if len(x_csrf_token) != 36:
            raise Exception("manager_cookie中不包含csrf_token，请确保从请求`count?_t=`中获取！")

        usage_bytes_old = 0
        while True:
            print(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
            query_url = f'https://www.feishu.cn/suite/admin/api/gaea/usages'
            manager_headers = {'cookie': manager_cookie, 'X-Csrf-Token': x_csrf_token}
            async with aiohttp.ClientSession() as session:
                async with session.get(url=query_url, headers=manager_headers, proxy=proxy_address) as resp:
                    data = await resp.json()
                    usage_bytes = int(data['data']['items'][6]['usage'])
            print(f"已用空间：{usage_bytes / 2 ** 30:.2f}GB")
            if usage_bytes != usage_bytes_old:
                downloader = FeishuDownloader(minutes_cookie)
                await downloader.check_minutes()
                if usage_bytes > 2 ** 30 * usage_threshold:
                    await downloader.delete_minutes(2)
            else:
                print(f"等待{check_interval}s后再次检查...")
            usage_bytes_old = usage_bytes
            
            await asyncio.sleep(check_interval)

if __name__ == '__main__':
    asyncio.run(main())