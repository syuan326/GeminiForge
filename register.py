# -*- coding: utf-8 -*-
"""
GeminiForge (原 gtgm) - Gemini Business 账号注册机
专为GitHub Actions无头环境设计，使用Playwright替代DrissionPage

环境变量配置:
  - MAILFREE_URL: mailfree 临时邮箱服务地址（如 https://mail.example.com）
  - MAILFREE_TOKEN: mailfree 的 JWT_TOKEN 根管理员令牌
  - EMAIL_DOMAIN: 邮箱域名（可选，用于在 mailfree 多域名中选择）
  - MAILFREE_DOMAIN_INDEX: 使用 mailfree 第几个域名 (默认0)
  - WORKER_DOMAIN: 旧版邮箱 Worker 域名（兼容旧接口，可选）
  - ADMIN_PASSWORD: 旧版邮箱管理密码（兼容旧接口，可选）
  - SYNC_URL: 同步API地址
  - SYNC_KEY: 同步API密钥
  - REGISTER_COUNT: 注册数量 (默认1)
  - CONCURRENT: 并发数 (默认1)
  - PROXY: 代理地址 (可选，如 http://user:pass@host:port)
"""

import os
import sys
import json
import re
import time
import random
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 全局代理配置
PROXY = os.environ.get('PROXY', '')


@dataclass
class CredentialData:
    """凭证数据"""
    email: str = ""
    csesidx: str = ""
    config_id: str = ""
    c_ses: str = ""
    c_oses: str = ""
    
    def to_dict(self) -> dict:
        """导出为同步格式"""
        # GitHub Actions运行在UTC时区，转换为北京时间(+8)后再加12小时有效期
        expires_at = (datetime.now() + timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "id": self.email,
            "csesidx": self.csesidx,
            "config_id": self.config_id,
            "secure_c_ses": self.c_ses,
            "host_c_oses": self.c_oses,
            "expires_at": expires_at
        }
    
    def is_complete(self) -> bool:
        """检查数据是否完整"""
        return all([self.csesidx, self.config_id, self.c_ses, self.c_oses])


class EmailManager:
    """邮箱管理器（支持 mailfree API，也兼容旧的 worker admin API）"""

    def __init__(self, worker_domain: str = '', email_domain: str = '', admin_password: str = '',
                 mailfree_url: str = '', mailfree_token: str = '', mailfree_domain_index: str = None):
        self.worker_domain = worker_domain or ''
        self.email_domain = email_domain or ''
        self.admin_password = admin_password or ''
        self.mailfree_url = (mailfree_url or '').strip().rstrip('/')
        if self.mailfree_url and '://' not in self.mailfree_url:
            self.mailfree_url = 'https://' + self.mailfree_url
        self.mailfree_token = mailfree_token or ''
        try:
            index_source = mailfree_domain_index
            if index_source is None or str(index_source).strip() == '':
                index_source = os.environ.get('MAILFREE_DOMAIN_INDEX', '0')
            self.domain_index = int(str(index_source).strip() or '0')
        except ValueError:
            self.domain_index = 0

        # 配置Session
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=5, pool_maxsize=5)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.session.headers.update({'Connection': 'keep-alive'})

    def _update_proxy(self):
        """动态更新代理设置（仅在PROXY_EMAIL=true时启用）"""
        # 邮件API默认不走代理，除非明确设置PROXY_EMAIL=true
        use_proxy_for_email = os.environ.get('PROXY_EMAIL', '').lower() == 'true'
        if not use_proxy_for_email:
            return  # 邮件API不使用代理

        proxy = os.environ.get('PROXY', '') or PROXY
        if proxy and not self.session.proxies:
            self.session.proxies = {'http': proxy, 'https': proxy}
            logger.info(f"EmailManager 使用代理: {proxy[:30]}...")

    def _mailfree_headers(self) -> dict:
        """构造 mailfree 的根管理员请求头"""
        headers = {"Content-Type": "application/json"}
        if self.mailfree_token:
            headers["Authorization"] = f"Bearer {self.mailfree_token}"
        return headers

    def _mailfree_domain_index(self) -> int:
        """根据 EMAIL_DOMAIN 自动选择 mailfree 域名索引"""
        if not self.email_domain:
            return self.domain_index
        try:
            res = self.session.get(
                f"{self.mailfree_url}/api/domains",
                headers=self._mailfree_headers(),
                timeout=30
            )
            if res.status_code == 200:
                domains = res.json()
                if isinstance(domains, list):
                    for idx, domain in enumerate(domains):
                        if str(domain).strip().lower() == self.email_domain.strip().lower():
                            return idx
                    logger.warning(f"mailfree 域名列表中没有 {self.email_domain}，使用 index={self.domain_index}")
        except Exception as e:
            logger.warning(f"获取 mailfree 域名列表失败: {e}")
        return self.domain_index

    def create_email(self, max_retries: int = 3) -> tuple:
        """创建邮箱，返回 (jwt_or_none, email)"""
        import string

        # 动态更新代理设置
        self._update_proxy()

        letters1 = ''.join(random.choices(string.ascii_lowercase, k=4))
        numbers = ''.join(random.choices(string.digits, k=2))
        letters2 = ''.join(random.choices(string.ascii_lowercase, k=3))
        username = letters1 + numbers + letters2

        # 优先使用 mailfree
        if self.mailfree_url:
            url = f"{self.mailfree_url}/api/create"
            headers = self._mailfree_headers()
            payload = {
                "local": username,
                "domainIndex": self._mailfree_domain_index(),
            }

            for attempt in range(max_retries):
                try:
                    res = self.session.post(url, json=payload, headers=headers, timeout=30)
                    if res.status_code == 200:
                        data = res.json()
                        email = data.get('email', '')
                        if not email:
                            raise Exception("mailfree 返回中没有 email 字段")
                        logger.info(f"邮箱创建成功 (mailfree): {email}")
                        return None, email
                    else:
                        logger.warning(f"mailfree 创建邮箱失败 HTTP {res.status_code}: {res.text[:200]}")
                except Exception as e:
                    wait_time = (2 ** attempt) + 1
                    logger.warning(f"创建邮箱失败 ({attempt + 1}/{max_retries}): {e}")
                    time.sleep(wait_time)
            return None, None

        # 兼容旧的 worker admin API
        url = f"https://{self.worker_domain}/admin/new_address"
        headers = {"Content-Type": "application/json", "x-admin-auth": self.admin_password}
        payload = {"enablePrefix": True, "name": username, "domain": self.email_domain}

        for attempt in range(max_retries):
            try:
                res = self.session.post(url, json=payload, headers=headers, timeout=30)
                if res.status_code == 200:
                    data = res.json()
                    email = data.get('address', f"{username}@{self.email_domain}")
                    logger.info(f"邮箱创建成功 (旧版): {email}")
                    return data.get('jwt', ''), email
            except Exception as e:
                wait_time = (2 ** attempt) + 1
                logger.warning(f"创建邮箱失败 ({attempt + 1}/{max_retries}): {e}")
                time.sleep(wait_time)

        return None, None

    def check_verification_code(self, email: str, max_retries: int = 20) -> Optional[str]:
        """检查验证码"""
        # 动态更新代理设置
        self._update_proxy()

        for i in range(max_retries):
            try:
                # 优先使用 mailfree
                if self.mailfree_url:
                    code = self._check_verification_code_mailfree(email)
                    if code:
                        return code
                else:
                    code = self._check_verification_code_legacy(email)
                    if code:
                        return code

                logger.info(f"等待验证码... ({i+1}/{max_retries})")
                time.sleep(3)
            except Exception as e:
                logger.warning(f"检查验证码错误: {e}")
                time.sleep(3)

        return None

    def _check_verification_code_mailfree(self, email: str) -> Optional[str]:
        """通过 mailfree /api/emails 获取验证码"""
        url = f"{self.mailfree_url}/api/emails"
        params = {"mailbox": email, "limit": 20}
        headers = self._mailfree_headers()

        res = self.session.get(url, params=params, headers=headers, timeout=30)
        if res.status_code != 200:
            logger.warning(f"mailfree 获取邮件失败 HTTP {res.status_code}: {res.text[:200]}")
            return None

        emails = res.json()
        if not isinstance(emails, list) or not emails:
            return None

        patterns = [
            r'verification-code[^>]*>([A-Z0-9]{6})<',
            r'>([A-Z0-9]{6})</span>',
            r'([A-Z0-9]{6})',
        ]

        for mail in emails:
            # mailfree 已自动提取 verification_code
            code = str(mail.get('verification_code') or '').strip()
            if code:
                code = code.upper()
                if len(code) == 6 and code.isalnum():
                    logger.info(f"获取到验证码 (mailfree): {code}")
                    return code

            # 如果没提取到，再尝试从主题/预览里解析
            text = f"{mail.get('subject', '')} {mail.get('preview', '')}"
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    code = match.group(1).upper()
                    if len(code) == 6 and code.isalnum():
                        logger.info(f"获取到验证码 (mailfree 解析): {code}")
                        return code

        return None

    def _check_verification_code_legacy(self, email: str) -> Optional[str]:
        """兼容旧版 worker admin 邮件接口"""
        url = f"https://{self.worker_domain}/admin/mails"
        headers = {"x-admin-auth": self.admin_password}
        params = {"limit": 5, "offset": 0, "address": email}

        res = self.session.get(url, params=params, headers=headers, timeout=30)
        if res.status_code != 200:
            return None

        data = res.json()
        if not data.get('results') or len(data['results']) == 0:
            return None

        raw_content = data['results'][0].get('raw', '')
        cleaned = raw_content.replace('=\r\n', '').replace('=\n', '').replace('=3D', '=')

        patterns = [
            r'verification-code[^>]*>([A-Z0-9]{6})<',
            r'>([A-Z0-9]{6})</span>',
            r'\b([A-Z0-9]{6})\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, cleaned, re.IGNORECASE)
            if match:
                code = match.group(1).upper()
                if len(code) == 6 and code.isalnum():
                    logger.info(f"获取到验证码 (旧版): {code}")
                    return code

        return None


class GeminiRegistrar:
    """使用Playwright的注册机"""
    
    def __init__(self, email_config: Dict):
        self.email_config = email_config
        self.credential = CredentialData()
        self.email_manager = EmailManager(
            email_config.get('worker_domain', ''),
            email_config.get('email_domain', ''),
            email_config.get('admin_password', ''),
            email_config.get('mailfree_url', ''),
            email_config.get('mailfree_token', ''),
            email_config.get('mailfree_domain_index', '0'),
        )
        self.browser = None
        self.page = None
    
    def _verify_proxy(self, proxy: str) -> None:
        """浏览器启动前先用 requests 快速验证代理能否访问 Google"""
        if not proxy:
            return
        test_url = 'https://business.gemini.google/'
        logger.info("正在测试代理连通性...")
        try:
            res = requests.get(
                test_url,
                proxies={'http': proxy, 'https': proxy},
                timeout=15,
                allow_redirects=False,
            )
            logger.info(f"代理连通性测试通过: HTTP {res.status_code}")
        except Exception as e:
            logger.error(f"代理连通性测试失败: {e}")
            raise Exception(f"代理无法访问 {test_url}: {e}")

    async def register(self) -> bool:
        """执行注册流程"""
        from playwright.async_api import async_playwright
        
        try:
            # 1. 创建邮箱
            logger.info("正在创建邮箱...")
            jwt, email = self.email_manager.create_email()
            if not email:
                raise Exception("创建邮箱失败")
            self.credential.email = email
            
            # 2. 启动浏览器
            logger.info("正在启动浏览器...")
            async with async_playwright() as p:
                # 配置浏览器代理
                launch_args = {'headless': True}
                
                # 从环境变量读取代理（VLESS启动后会设置）
                browser_proxy = os.environ.get('PROXY', '') or PROXY
                # 先验证代理连通性，避免 Playwright 空等 60 秒
                self._verify_proxy(browser_proxy)
                if browser_proxy:
                    # 解析代理地址
                    from urllib.parse import urlparse
                    proxy_parsed = urlparse(browser_proxy)
                    proxy_scheme = (proxy_parsed.scheme or 'http').lower()
                    proxy_host = proxy_parsed.hostname or ''
                    proxy_port = proxy_parsed.port or 80
                    if proxy_scheme.startswith('socks5'):
                        proxy_server = f"socks5://{proxy_host}:{proxy_port}"
                    elif proxy_scheme.startswith('socks4'):
                        proxy_server = f"socks4://{proxy_host}:{proxy_port}"
                    else:
                        proxy_server = f"http://{proxy_host}:{proxy_port}"
                    # Playwright 需要的格式
                    proxy_config = {'server': proxy_server}
                    if proxy_parsed.username:
                        proxy_config['username'] = proxy_parsed.username
                        proxy_config['password'] = proxy_parsed.password or ''
                    launch_args['proxy'] = proxy_config
                    logger.info(f"浏览器使用代理: {proxy_host}:{proxy_port} ({proxy_scheme})")
                
                self.browser = await p.chromium.launch(**launch_args)
                context = await self.browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080}
                )
                self.page = await context.new_page()
                
                # 3. 打开注册页面（避免 networkidle 一直等不到，改用 domcontentloaded + 重试）
                logger.info("正在打开注册页面...")
                try:
                    await self.page.goto('https://business.gemini.google', wait_until='domcontentloaded', timeout=60000)
                except Exception as goto_err:
                    logger.warning(f"首次打开页面失败，正在重试: {goto_err}")
                    await self.page.goto('https://business.gemini.google', wait_until='load', timeout=60000)

                # 4. 输入邮箱
                logger.info(f"正在输入邮箱: {email}")
                await self.page.wait_for_selector('#email-input', timeout=60000)
                await self.page.fill('#email-input', email)
                await asyncio.sleep(1)
                
                # 5. 点击继续
                await self.page.click('#log-in-button')
                await asyncio.sleep(2)
                
                # 6. 获取验证码
                logger.info("正在等待验证码...")
                code = self.email_manager.check_verification_code(email)
                if not code:
                    raise Exception("未收到验证码")
                
                # 7. 输入验证码
                logger.info(f"正在输入验证码: {code}")
                await self.page.wait_for_selector('input[name="pinInput"]', timeout=30000)
                await self.page.fill('input[name="pinInput"]', code)
                await asyncio.sleep(1)
                
                # 8. 点击验证
                await self.page.click('button[jsname="XooR8e"]')
                await asyncio.sleep(3)
                
                # 9. 输入姓名
                logger.info("正在输入姓名...")
                await self.page.wait_for_selector('input[formcontrolname="fullName"]', timeout=15000)
                fullname = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz', k=5))
                await self.page.fill('input[formcontrolname="fullName"]', fullname)
                await asyncio.sleep(1)
                
                # 10. 点击同意
                await self.page.click('button.agree-button')
                
                # 11. 等待跳转
                logger.info("正在等待页面跳转...")
                await self.page.wait_for_url(re.compile(r'business\.gemini\.google/home/cid/'), timeout=90000)
                await asyncio.sleep(3)
                
                # 12. 提取数据
                logger.info("正在提取凭证数据...")
                cookies = await context.cookies()
                for cookie in cookies:
                    if cookie['name'] == '__Host-C_OSES':
                        self.credential.c_oses = cookie['value']
                    elif cookie['name'] == '__Secure-C_SES':
                        self.credential.c_ses = cookie['value']
                
                current_url = self.page.url
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(current_url)
                self.credential.csesidx = parse_qs(parsed.query).get('csesidx', [''])[0]
                
                path_match = re.search(r'/cid/([a-f0-9-]+)', parsed.path)
                if path_match:
                    self.credential.config_id = path_match.group(1)
                
                if self.credential.is_complete():
                    logger.info(f"✅ 注册成功! 邮箱: {email}")
                    return True
                else:
                    raise Exception("未能获取完整凭证数据")
                    
        except Exception as e:
            logger.error(f"❌ 注册失败: {e}")
            return False


class CredentialSyncer:
    """凭证同步器"""
    
    def __init__(self, base_url: str, admin_key: str):
        self.base_url = base_url.rstrip('/')
        self.admin_key = admin_key
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.session.headers.update({'Connection': 'keep-alive'})
    
    def _update_proxy(self):
        """动态更新代理设置"""
        proxy = os.environ.get('PROXY', '') or PROXY
        if proxy and not self.session.proxies:
            self.session.proxies = {'http': proxy, 'https': proxy}
            logger.info(f"CredentialSyncer 使用代理: {proxy[:30]}...")
    
    def _request(self, method: str, url: str, **kwargs):
        """带重试的请求"""
        self._update_proxy()  # 动态更新代理
        for attempt in range(3):
            try:
                return getattr(self.session, method)(url, timeout=30, **kwargs)
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep((2 ** attempt) + 1)
        return None
    
    def sync(self, new_accounts: List[Dict]) -> bool:
        """执行同步"""
        try:
            # 1. 登录
            logger.info("步骤 1/4: 登录...")
            res = self._request('post', f"{self.base_url}/login", 
                data={'admin_key': self.admin_key},
                headers={'Content-Type': 'application/x-www-form-urlencoded'})
            if res.status_code != 200:
                logger.error(f"登录失败: {res.status_code}")
                return False
            logger.info("✅ 登录成功")
            
            # 2. 获取现有凭证
            logger.info("步骤 2/4: 获取现有凭证...")
            res = self._request('get', f"{self.base_url}/admin/accounts-config")
            existing = res.json().get('accounts', []) if res.status_code == 200 else []
            logger.info(f"获取到 {len(existing)} 个现有账户")
            
            # 3. 合并凭证
            logger.info("步骤 3/4: 合并凭证...")
            accounts_dict = {a['id']: a for a in existing if a.get('id')}
            for account in new_accounts:
                if account.get('id'):
                    accounts_dict[account['id']] = account
            merged = list(accounts_dict.values())
            logger.info(f"合并后共 {len(merged)} 个账户")
            
            # 4. 上传
            logger.info("步骤 4/4: 上传凭证...")
            res = self._request('put', f"{self.base_url}/admin/accounts-config",
                json=merged, headers={'Content-Type': 'application/json'})
            if res.status_code == 200:
                logger.info(f"✅ 上传成功!")
                return True
            else:
                logger.error(f"上传失败: {res.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"同步失败: {e}")
            return False


async def register_worker(worker_id: int, email_config: Dict) -> Optional[Dict]:
    """注册工作函数"""
    logger.info(f"[Worker-{worker_id}] 开始注册...")
    registrar = GeminiRegistrar(email_config)
    
    if await registrar.register():
        cred = registrar.credential.to_dict()
        logger.info(f"[Worker-{worker_id}] ✅ 成功: {cred['id']}")
        return cred
    else:
        logger.info(f"[Worker-{worker_id}] ❌ 失败")
        return None


async def main():
    global PROXY  # 允许proxy_helper更新全局代理
    
    # 启动VLESS代理（如果配置了）
    proxy_process = None
    vless_config = os.environ.get('VLESS_CONFIG', '')
    if vless_config:
        try:
            from proxy_helper import setup_proxy
            logger.info("正在启动VLESS代理...")
            proxy_process = setup_proxy()
            if proxy_process:
                # 更新全局PROXY变量
                PROXY = os.environ.get('PROXY', '')
                logger.info(f"VLESS代理已启动: {PROXY}")
        except Exception as e:
            logger.warning(f"VLESS代理启动失败: {e}")
    
    # 从环境变量读取配置
    email_config = {
        'mailfree_url': os.environ.get('MAILFREE_URL', ''),
        'mailfree_token': os.environ.get('MAILFREE_TOKEN', ''),
        'email_domain': os.environ.get('EMAIL_DOMAIN', ''),
        'mailfree_domain_index': os.environ.get('MAILFREE_DOMAIN_INDEX', '0'),
        'worker_domain': os.environ.get('WORKER_DOMAIN', ''),
        'admin_password': os.environ.get('ADMIN_PASSWORD', ''),
    }
    
    sync_url = os.environ.get('SYNC_URL', '')
    sync_key = os.environ.get('SYNC_KEY', '')
    count = int(os.environ.get('REGISTER_COUNT', '1'))
    concurrent = int(os.environ.get('CONCURRENT', '1'))
    
    # 验证配置（mailfree 或旧版 worker 邮箱二选一）
    mailfree_ready = bool(email_config['mailfree_url'] and email_config['mailfree_token'])
    legacy_ready = bool(
        email_config['worker_domain'] and email_config['email_domain'] and email_config['admin_password']
    )
    if not mailfree_ready and not legacy_ready:
        logger.error("❌ 缺少邮箱配置环境变量! 需要 MAILFREE_URL+MAILFREE_TOKEN，或旧版 WORKER_DOMAIN+EMAIL_DOMAIN+ADMIN_PASSWORD")
        sys.exit(1)
    
    if not sync_url or not sync_key:
        logger.error("❌ 缺少同步API配置!")
        sys.exit(1)
    
    print(f"\n{'='*50}")
    print(f"  Gemini Business 注册机 (GitHub Actions)")
    print(f"  计划注册: {count} 个账号")
    print(f"  并发数: {concurrent}")
    print(f"{'='*50}\n")
    
    # 执行注册
    credentials = []
    
    if concurrent == 1:
        # 串行
        for i in range(count):
            cred = await register_worker(i + 1, email_config)
            if cred:
                credentials.append(cred)
            if i < count - 1:
                await asyncio.sleep(random.randint(3, 6))
    else:
        # 并发
        tasks = [register_worker(i + 1, email_config) for i in range(count)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        credentials = [r for r in results if isinstance(r, dict)]
    
    print(f"\n注册完成: 成功 {len(credentials)} 个\n")
    
    # 同步到远程
    if credentials:
        print("开始同步到远程API...\n")
        syncer = CredentialSyncer(sync_url, sync_key)
        if syncer.sync(credentials):
            print("\n✅ 全部完成!")
        else:
            print("\n❌ 同步失败!")
            sys.exit(1)
    else:
        print("没有成功注册的账号，跳过同步")


if __name__ == '__main__':
    asyncio.run(main())
