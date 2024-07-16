import platform
import time
from typing import Optional

from DrissionPage import ChromiumOptions, ChromiumPage
import logging,os
import json


class DPHelper:

    def __init__(
        self,
        browser_path,
        HEADLESS: Optional[bool] = True,
        NO_GUI: Optional[bool] = True,
        proxy_server: Optional[str] = None,
        user_agent: Optional[str] = None,
        json_out_filepath: Optional[str]='./cookie.json',
        text_out_filepath: Optional[str]='./cookie.txt',

        
    ):

        if not browser_path:
            os_name = platform.system().lower()
            if "windows" in os_name:
                browser_path = "C:\Program Files\Google\Chrome\Application\chrome.exe"
                browser_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
                browser_path=r"C:\Users\Administrator\AppData\Local\ms-playwright\chromium-1124\chrome-win\chrome.exe"

            elif "darwin" in os_name:
                browser_path = (
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                )
            else:
                # linux like
                browser_path = "/usr/bin/google-chrome"

        options = ChromiumOptions()
        options.set_paths(browser_path=browser_path)
        options.auto_port()

        # https://stackoverflow.com/questions/68289474/selenium-headless-how-to-bypass-cloudflare-detection-using-selenium
        if HEADLESS:
            options.headless(True)
        else:
            options.headless(False)
    
        if user_agent:
            options.set_user_agent(user_agent)

        arguments = []
        if proxy_server:
            arguments.append(f"--proxy-server={proxy_server}")

        # Some arguments to make the browser better for automation and less detectable.
        if NO_GUI:
            logging.info("[CloudflareBypass.__init__] set --no-sandbox")
            arguments.append("--no-sandbox")

        for argument in arguments:
            options.set_argument(argument)
        self.jsonoutpath=json_out_filepath
        self.textoutpath=text_out_filepath

        self.driver = ChromiumPage(addr_or_opts=options)
        # print(self.driver.user_agent)
    def getCookie(self,url,json_cookie_path=None):
        print('start to get cookie,you can login in')
        tab=self.driver.get(url) 
        # time.sleep(60)
        attempts = 0
        max_attempts=6
        check_interval=10
        while True:
            # 检查退出循环的条件
            if max_attempts is not None and attempts >= max_attempts:
                print("Maximum attempts reached. Exiting the loop.")
                break

            # 执行你的条件检查
            print('url',self.driver.url)
            islogin=self.driver.cookies(as_dict=True).get('bv_csrf_token')
                
            if islogin:

                print("you have login in .")
                break  # 条件满足，退出循环

            # 如果条件不满足，等待一段时间后再次检查
            print(f"{attempts} failed. Retrying in {check_interval} seconds...")
            time.sleep(check_interval)
            attempts += 1
        cook= self.driver.cookies(as_dict=True)

        print(f'start save cookie to {self.jsonoutpath}')


        if cook:
            if not json_cookie_path:
                json_cookie_path='cookie.json'
            self.saveCookiejson(outfilepath=json_cookie_path,cookie=cook)
            print(f'save cookie to {json_cookie_path}')
            self.driver.close()
        else:
            print('though loginin, but cookie is not there')
        return cook
    def cookie_dict2_str(self, jsondata):
        cookstr = ""
        print(f'convert cookie dict to str:\n{jsondata}')
        for k, v in jsondata.items():
            cookstr += k + "=" + v + "; "
        return cookstr    
    def saveCookiejson(self,outfilepath="./cookie.json",cookie=None):
        if cookie:
            print(f'duump file to json:{outfilepath}')
            with open(outfilepath, 'w', encoding='utf-8') as file:
    # 将data序列化成JSON格式并写入文件
                json.dump(cookie, file, ensure_ascii=False, indent=4) 
            print('dump done')     
        else:
            print('cookie to be save is None')  # print(cook)
    def saveCookie(self,outfilepath="./cookie.txt",cookie=None):
        if cookie is None:
            cook = self.driver.cookies(as_dict=False)
        # print(cook)
        cookstr = ""
        for c in cook:
            for index, (k, v) in enumerate(c.items()):
                cookstr += k + "=" + v + "; "
        # print(cookstr)
        if cookstr is not None:

            with open (outfilepath,'w') as f:
                f.write(cookstr)
            # cookfile.add_data(cookstr)

    def loadCookie(self,cookiepath):
        if os.path.exists(cookiepath):

            with open(cookiepath) as f:
                cookstr = f.read()
            self.driver.set.cookies(cookstr)

    def is_justAmoment(self):
        block=self.driver.ele('text:just a moment')
        if block:
            return True
        else:
            return False
    def bypass(self, url: str):
        self.driver.get(url)

        check_count = 1
        while not self.is_justAmoment():
            self.try_to_click_challenge()

            if check_count >= 6:
                if not self.is_passed():
                    raise Exception("Meet challenge restart")

            logging.info(
                f"Handle category - meet challenge. Wait 20s to check it again. Count: {check_count}"
            )
            check_count += 1

            time.sleep(20)

        return self.driver.cookies(all_info=True)

    def try_to_click_challenge(self):
        try:
            if self.driver.wait.ele_displayed("xpath://div/iframe", timeout=1.5):
                time.sleep(1.5)
                self.driver("xpath://div/iframe").ele(
                    "Verify you are human", timeout=2.5
                ).click()
        except Exception as e:
            # 2025-05-26
            # 有时会出现错误，重试能解决一部分问题
            # 1. DrissionPage.errors.ContextLostError: 页面被刷新，请操作前尝试等待页面刷新或加载完成。
            # 2. DrissionPage.errors.ElementNotFoundError:
            #    没有找到元素。
            #    method: ele()
            #    args: {'locator': 'Verify you are human', 'index': 1}
            logging.info(
                f"[CloudflareBypass.try_to_click_challenge] fail to click the challenge. message: {str(e)}"
            )
            self.driver.refresh()

    def is_passed(self):
        print(self.driver.cookies())
        for cookie in self.driver.cookies():
            if cookie.get("name") == "cf_clearance":
                return True
        return False

    def close(self):
        try:
            self.driver.close()
        except Exception as e:
            print(e)
