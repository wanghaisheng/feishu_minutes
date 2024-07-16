from feishu_downloader import FeishuDownloader
import asyncio
async def main2():

    fup1 =FeishuDownloader(folder=videopath,json_cookie_path='./download-cookie.json')
    await fup1.upload()
asyncio.run(main2())