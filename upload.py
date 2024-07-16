from feishu_uploader import FeishuUploader
import asyncio
async def main():
    videopath='./videos'
    fup =FeishuUploader(folder=videopath)
    fup.upload()
async def main2():
    videopath=r'C:\Users\Administrator\Downloads\archive6-ahrefs\result\AhrefsCom'
    videopath=r'C:\Users\Administrator\Downloads\archive5\result\AhrefsCom'

    fup1 =FeishuUploader(folder=videopath,json_cookie_path='./cookie.json')
    await fup1.upload()
asyncio.run(main2())