from flask import Flask, render_template, request, redirect, url_for, flash, make_response, jsonify
import os
import requests
from bs4 import BeautifulSoup
import uuid
import json
from datetime import datetime
import time
from functools import lru_cache
from flask_caching import Cache
from urllib.parse import urlparse
from urllib.parse import urljoin
import re
from dotenv import load_dotenv
import base64
import hashlib

# 環境変数をロード
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key_for_testing')

# キャッシュ設定
cache_config = {
    "DEBUG": os.environ.get('FLASK_DEBUG', 'false').lower() == 'true',
    "CACHE_TYPE": "SimpleCache",  # Vercelの環境に適したシンプルなインメモリキャッシュ
    "CACHE_DEFAULT_TIMEOUT": 300  # 5分間のデフォルトタイムアウト
}
cache = Cache(app, config=cache_config)

# 一時的な互換性のためにURLSディレクトリを維持
# 将来的には完全に削除し、Vercel KV/Blobに移行
if os.environ.get('VERCEL_ENV') == 'production':
    URLS_DIR = '/tmp/urls'
else:
    URLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'urls')

# アップロードフォルダをapp.configに設定
app.config['UPLOAD_FOLDER'] = URLS_DIR

if not os.path.exists(URLS_DIR):
    os.makedirs(URLS_DIR)

# URLリストのJSONファイル
URL_LIST_FILE = os.path.join(URLS_DIR, 'url_list.json')
if not os.path.exists(URL_LIST_FILE):
    with open(URL_LIST_FILE, 'w') as f:
        json.dump([], f)

# Vercel KV接続情報
KV_REST_API_URL = os.environ.get('KV_REST_API_URL')
KV_REST_API_TOKEN = os.environ.get('KV_REST_API_TOKEN')

# Vercel Blob接続情報
BLOB_READ_WRITE_TOKEN = os.environ.get('BLOB_READ_WRITE_TOKEN')

# KVヘルパー関数
def kv_get(key):
    """KVストアから値を取得"""
    if not KV_REST_API_URL or not KV_REST_API_TOKEN:
        app.logger.warning("KV接続情報が設定されていません。ファイルベースのストレージにフォールバックします。")
        return None
    
    try:
        url = f"{KV_REST_API_URL}/get/{key}"
        headers = {
            "Authorization": f"Bearer {KV_REST_API_TOKEN}"
        }
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            result = response.json()
            return result.get('result')
        elif response.status_code == 404:
            app.logger.info(f"KVキー '{key}' が見つかりません")
            return None
        else:
            app.logger.error(f"KV取得エラー: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        app.logger.error(f"KV取得例外: {str(e)}")
        return None

def kv_set(key, value, ex=None):
    """KVストアに値を設定"""
    if not KV_REST_API_URL or not KV_REST_API_TOKEN:
        app.logger.warning("KV接続情報が設定されていません。ファイルベースのストレージにフォールバックします。")
        return False
    
    try:
        url = f"{KV_REST_API_URL}/set/{key}"
        headers = {
            "Authorization": f"Bearer {KV_REST_API_TOKEN}",
            "Content-Type": "application/json"
        }
        data = {
            "value": value
        }
        
        if ex:
            data["ex"] = ex
        
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            return True
        else:
            app.logger.error(f"KV設定エラー: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        app.logger.error(f"KV設定例外: {str(e)}")
        return False

def kv_del(key):
    """KVストアから値を削除"""
    if not KV_REST_API_URL or not KV_REST_API_TOKEN:
        app.logger.warning("KV接続情報が設定されていません。ファイルベースのストレージにフォールバックします。")
        return False
    
    try:
        url = f"{KV_REST_API_URL}/del/{key}"
        headers = {
            "Authorization": f"Bearer {KV_REST_API_TOKEN}"
        }
        
        response = requests.delete(url, headers=headers)
        if response.status_code == 200:
            return True
        else:
            app.logger.error(f"KV削除エラー: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        app.logger.error(f"KV削除例外: {str(e)}")
        return False

def kv_keys(pattern="*"):
    """パターンに一致するすべてのキーを取得"""
    if not KV_REST_API_URL or not KV_REST_API_TOKEN:
        app.logger.warning("KV接続情報が設定されていません。ファイルベースのストレージにフォールバックします。")
        return []
    
    try:
        url = f"{KV_REST_API_URL}/keys/{pattern}"
        headers = {
            "Authorization": f"Bearer {KV_REST_API_TOKEN}"
        }
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            result = response.json()
            return result.get('result', [])
        else:
            app.logger.error(f"KVキー一覧取得エラー: {response.status_code} - {response.text}")
            return []
    except Exception as e:
        app.logger.error(f"KVキー一覧例外: {str(e)}")
        return []

# Blobヘルパー関数
def blob_put(file_name, content, options=None):
    """コンテンツをBlobストレージに保存"""
    if not BLOB_READ_WRITE_TOKEN:
        app.logger.warning("Blob接続情報が設定されていません。ファイルベースのストレージにフォールバックします。")
        return None
    
    try:
        url = "https://blob.vercel-storage.com/put"
        headers = {
            "Authorization": f"Bearer {BLOB_READ_WRITE_TOKEN}",
            "X-Blob-Store": "store",
            "X-Blob-Filename": file_name,
        }
        
        if isinstance(content, str):
            content = content.encode('utf-8')
        
        response = requests.post(url, headers=headers, data=content)
        if response.status_code == 200:
            result = response.json()
            return result.get('url')
        else:
            app.logger.error(f"Blob保存エラー: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        app.logger.error(f"Blob保存例外: {str(e)}")
        return None

def blob_get(url):
    """Blobストレージからコンテンツを取得"""
    if not url or not url.startswith('https://'):
        app.logger.error(f"無効なBlobURL: {url}")
        return None
    
    try:
        response = requests.get(url)
        if response.status_code == 200:
            return response.text
        else:
            app.logger.error(f"Blob取得エラー: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        app.logger.error(f"Blob取得例外: {str(e)}")
        return None

def blob_delete(url):
    """Blobストレージからコンテンツを削除"""
    if not BLOB_READ_WRITE_TOKEN or not url or not url.startswith('https://'):
        app.logger.error(f"無効なBlobURL: {url}")
        return False
    
    try:
        # URLからパス部分を抽出
        path = url.split('/')[-1]
        delete_url = f"https://blob.vercel-storage.com/delete/store/{path}"
        
        headers = {
            "Authorization": f"Bearer {BLOB_READ_WRITE_TOKEN}"
        }
        
        response = requests.delete(delete_url, headers=headers)
        if response.status_code == 200:
            return True
        else:
            app.logger.error(f"Blob削除エラー: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        app.logger.error(f"Blob削除例外: {str(e)}")
        return False

# 非同期関数をシンクロに変換するヘルパー関数
def run_async(func):
    """非同期関数実行ヘルパー（必要なくなったので直接結果を返す）"""
    return func

# URLリスト管理の新しい実装
def get_url_list():
    """保存されたURLリストを取得 (Vercel KV & 後方互換性)"""
    try:
        # まずVercel KVから取得を試みる
        url_list = run_async(kv_get('url_list'))
        if url_list:
            return url_list
        
        # KVに接続できない場合はファイルから読み込む
        with open(URL_LIST_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        app.logger.error(f"URLリスト取得エラー: {str(e)}")
        # エラーが発生した場合は空のリストを返す
        return []

def save_url_list(url_list):
    """URLリストを保存 (Vercel KV & 後方互換性)"""
    try:
        # まずVercel KVへの保存を試みる
        kv_success = run_async(kv_set('url_list', url_list))
        
        # どちらの場合も、後方互換性のためにファイルにも保存
        with open(URL_LIST_FILE, 'w') as f:
            json.dump(url_list, f)
        
        return True
    except Exception as e:
        app.logger.error(f"URLリスト保存エラー: {str(e)}")
        return False

# 設定管理
@lru_cache(maxsize=1)
def get_config():
    """アプリケーションの設定を取得"""
    try:
        config = run_async(kv_get('config'))
        if not config:
            # デフォルト設定
            config = {
                'max_urls': 100,
                'default_expire_days': 30,
                'debug_enabled': False,
                'pixel_id': 'CM0EQKBC77U7DDDCEF4G'  # デフォルトのTikTokピクセルID
            }
            # 設定を保存
            run_async(kv_set('config', config))
        return config
    except Exception as e:
        app.logger.error(f"設定取得エラー: {str(e)}")
        # デフォルト設定
        return {
            'max_urls': 100,
            'default_expire_days': 30,
            'debug_enabled': False,
            'pixel_id': 'CM0EQKBC77U7DDDCEF4G'  # デフォルトのTikTokピクセルID
        }

def save_config(config):
    """アプリケーションの設定を保存"""
    try:
        # キャッシュをクリア
        get_config.cache_clear()
        # KVに保存
        return run_async(kv_set('config', config))
    except Exception as e:
        app.logger.error(f"設定保存エラー: {str(e)}")
        return False

# クリック数の更新
def update_click_count(file_id):
    """URLのクリック数を更新"""
    try:
        # クリック数情報を取得
        click_stats = run_async(kv_get(f'clicks:{file_id}')) or {'total': 0, 'history': []}
        
        # 現在時刻
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 更新
        click_stats['total'] += 1
        click_stats['history'].append(now)
        
        # 履歴は最新100件のみ保持
        if len(click_stats['history']) > 100:
            click_stats['history'] = click_stats['history'][-100:]
        
        # 保存
        run_async(kv_set(f'clicks:{file_id}', click_stats))
        
        return True
    except Exception as e:
        app.logger.error(f"クリック数更新エラー: {str(e)}")
        return False

def get_click_stats(file_id):
    """URLのクリック統計情報を取得"""
    try:
        return run_async(kv_get(f'clicks:{file_id}')) or {'total': 0, 'history': []}
    except Exception as e:
        app.logger.error(f"クリック統計取得エラー: {str(e)}")
        return {'total': 0, 'history': []}

@app.route('/')
def index():
    url_list = get_url_list()
    return render_template('index.html', url_list=url_list)

# HTMLリクエストの結果をキャッシュする
@cache.memoize(timeout=3600)  # 1時間キャッシュ
def fetch_html_content(url):
    """URLからHTMLコンテンツを取得し、キャッシュする"""
    # リクエストヘッダーを追加
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8'
    }
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    
    # レスポンスのエンコーディングを検出・設定
    if response.encoding.lower() == 'iso-8859-1':
        # エンコーディングが未検出の場合はUTF-8を試みる
        response.encoding = 'utf-8'
    
    html_content = response.text
    
    # 文字化け検出と修正
    if '繝' in html_content or '縺' in html_content:
        app.logger.info(f"URLコンテンツに文字化けを検出: {url}")
        
        # オリジナルのバイナリコンテンツ
        raw_content = response.content
        
        # 可能性のあるエンコーディングを試す
        encodings = ['shift_jis', 'euc-jp', 'cp932', 'iso-2022-jp']
        for encoding in encodings:
            try:
                test_content = raw_content.decode(encoding, errors='ignore')
                if '繝' not in test_content and '縺' not in test_content:
                    app.logger.info(f"コンテンツの文字化けを{encoding}で修正しました")
                    html_content = test_content
                    break
            except Exception as e:
                app.logger.error(f"エンコーディング{encoding}での変換失敗: {str(e)}")
    
    return html_content

@app.route('/admin/create', methods=['POST'])
def create():
    try:
        # フォームデータを取得
        url = request.form.get('original_url')  # HTMLフォームのname属性と一致させる
        pixel_code = request.form.get('pixel_code', '')  # ユーザー入力のピクセルコード（任意）
        
        app.logger.info(f"URL作成リクエスト: {url}")
        
        if not url:
            flash('URLを入力してください。', 'error')
            return redirect(url_for('index'))
            
        # URLの形式を検証
        if not url.startswith(('http://', 'https://')):
            url = f"https://{url}"
            
        # ドメインを取得
        parsed_url = urlparse(url)
        base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        # YouTube URLの特別処理
        is_youtube = 'youtube.com' in parsed_url.netloc or 'youtu.be' in parsed_url.netloc
        
        # 設定情報を取得
        config = get_config()
        
        # リクエストタイムアウト: 10秒
        timeout = 10
        
        # HTMLコンテンツを取得
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
            }
            
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()  # エラーチェック
            
            # エンコーディングの自動検出
            if response.encoding == 'ISO-8859-1':
                # 日本語サイトの場合、エンコーディングを推測
                encodings = ['utf-8', 'shift_jis', 'euc-jp', 'cp932', 'iso-2022-jp']
                html_content = None
                
                for encoding in encodings:
                    try:
                        response.encoding = encoding
                        decoded_text = response.text
                        if not ('繝' in decoded_text or '縺' in decoded_text):
                            html_content = decoded_text
                            app.logger.info(f"エンコーディング検出: {encoding}")
                            break
                    except UnicodeDecodeError:
                        continue
                
                # どのエンコーディングでも失敗した場合はデフォルトに戻す
                if html_content is None:
                    response.encoding = 'utf-8'
                    html_content = response.text
            else:
                html_content = response.text
                
        except requests.exceptions.RequestException as e:
            app.logger.error(f"リクエストエラー: {str(e)}")
            flash(f'URLからのコンテンツ取得に失敗しました: {str(e)}', 'error')
            return redirect(url_for('index'))
        
        # ベースURLの取得
        base_url = url
        
        # TikTokピクセルコードの生成
        # デフォルトのピクセルIDを使用（または設定からカスタムIDを取得）
        tiktok_pixel_id = config.get('pixel_id', 'CM0EQKBC77U7DDDCEF4G')
        
        # ユーザーがカスタムコードを入力した場合はそれを優先
        if pixel_code and len(pixel_code.strip()) > 0:
            # XSS対策：スクリプトタグだけを抽出して許可
            if '<script' in pixel_code and '</script>' in pixel_code:
                script_pattern = re.compile(r'<script[^>]*>(.*?)</script>', re.DOTALL)
                script_matches = script_pattern.findall(pixel_code)
                if script_matches:
                    script_content = script_matches[0]
                    # スクリプト内容はエスケープせずにそのまま使用
                    pixel_script = f"<script>\n{script_content}\n</script>"
                else:
                    # スクリプトタグはあるが内容がない場合
                    pixel_script = generate_tiktok_pixel_script(tiktok_pixel_id)
            else:
                # スクリプトタグがない場合はIDとして扱う
                cleaned_id = re.sub(r'[^A-Z0-9]', '', pixel_code.upper())
                if cleaned_id:
                    tiktok_pixel_id = cleaned_id
                pixel_script = generate_tiktok_pixel_script(tiktok_pixel_id)
        else:
            # デフォルトピクセルスクリプトを生成
            pixel_script = generate_tiktok_pixel_script(tiktok_pixel_id)
        
        # CSSリセットとメタデータタグ
        css_reset = """
<style>
/* 基本的なブラウザスタイルリセット */
html, body, div, span, applet, object, iframe,
h1, h2, h3, h4, h5, h6, p, blockquote, pre,
a, abbr, acronym, address, big, cite, code,
del, dfn, em, img, ins, kbd, q, s, samp,
small, strike, strong, sub, sup, tt, var,
b, u, i, center,
dl, dt, dd, ol, ul, li,
fieldset, form, label, legend,
table, caption, tbody, tfoot, thead, tr, th, td,
article, aside, canvas, details, embed,
figure, figcaption, footer, header, hgroup,
menu, nav, output, ruby, section, summary,
time, mark, audio, video {
    margin: 0;
    padding: 0;
    border: 0;
    font-size: 100%;
    font: inherit;
    vertical-align: baseline;
}
/* HTML5 display-role reset for older browsers */
article, aside, details, figcaption, figure,
footer, header, hgroup, menu, nav, section {
    display: block;
}
body {
    line-height: 1;
}
ol, ul {
    list-style: none;
}
blockquote, q {
    quotes: none;
}
blockquote:before, blockquote:after,
q:before, q:after {
    content: '';
    content: none;
}
table {
    border-collapse: collapse;
    border-spacing: 0;
}
</style>
"""
        
        # メタデータスクリプト (OGP情報を追加)
        metadata_script = f"""
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="ie=edge">
<meta property="og:title" content="Shared Link">
<meta property="og:description" content="Click to view content">
<meta property="og:image" content="https://example.com/og-image.jpg">
<meta property="og:url" content="{url}">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
"""
        
        # HTML加工処理を改善
        # YouTube：iframeを直接埋め込む
        if is_youtube:
            video_id = None
            if 'youtube.com/watch' in url:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                video_id = query.get('v', [None])[0]
            elif 'youtu.be' in url:
                video_id = urllib.parse.urlparse(url).path.lstrip('/')
            
            if video_id:
                new_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>YouTube Video</title>
    {pixel_script}
    <style>
        body {{ margin: 0; padding: 0; overflow: hidden; }}
        .video-container {{ position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden; }}
        .video-container iframe {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; }}
    </style>
</head>
<body>
    <div class="video-container">
        <iframe width="100%" height="100%" src="https://www.youtube.com/embed/{video_id}" 
                frameborder="0" allowfullscreen></iframe>
    </div>
</body>
</html>"""
            else:
                # ビデオIDが見つからない場合
                new_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>YouTube Video</title>
    {pixel_script}
</head>
<body>
    <h1>YouTube URL</h1>
    <p><a href="{url}" target="_blank">元のYouTube動画を開く</a></p>
</body>
</html>"""
        else:
            # 通常のウェブページの場合
            # BeautifulSoupを使用して安全にHTMLを解析
            try:
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # 元のheadタグのコンテンツを保持
                head_content = ''
                if soup.head:
                    for tag in soup.head.children:
                        if tag.name != 'meta' and tag.name != 'title':
                            head_content += str(tag)
                
                # HTML内の相対URLを絶対URLに変換
                for tag in soup.find_all(['img', 'script', 'link', 'a']):
                    if tag.name == 'img' and tag.get('src'):
                        if not tag['src'].startswith(('http://', 'https://', 'data:', '//')):
                            tag['src'] = urljoin(base_url, tag['src'])
                    elif tag.name == 'script' and tag.get('src'):
                        if not tag['src'].startswith(('http://', 'https://', 'data:', '//')):
                            tag['src'] = urljoin(base_url, tag['src'])
                    elif tag.name == 'link' and tag.get('href'):
                        if not tag['href'].startswith(('http://', 'https://', 'data:', '//')):
                            tag['href'] = urljoin(base_url, tag['href'])
                    elif tag.name == 'a' and tag.get('href'):
                        if not tag['href'].startswith(('http://', 'https://', 'data:', '//', '#', 'javascript:', 'mailto:')):
                            tag['href'] = urljoin(base_url, tag['href'])
                
                # CSSのurl()を修正
                for style_tag in soup.find_all('style'):
                    if style_tag.string:
                        style_content = style_tag.string
                        # url(...) パターンを検索して絶対パスに変換
                        style_tag.string = re.sub(
                            r'url\([\'"]?([^\'" \)]+)[\'"]?\)',
                            lambda m: f'url({urljoin(base_url, m.group(1))})' if not re.match(r'^(https?:|data:|\/\/)', m.group(1)) else m.group(0),
                            style_content
                        )
                
                # インラインスタイルのurl()も修正
                for tag in soup.find_all(attrs={"style": True}):
                    style_content = tag['style']
                    tag['style'] = re.sub(
                        r'url\([\'"]?([^\'" \)]+)[\'"]?\)',
                        lambda m: f'url({urljoin(base_url, m.group(1))})' if not re.match(r'^(https?:|data:|\/\/)', m.group(1)) else m.group(0),
                        style_content
                    )
                
                # HTML文書を再構築
                body_content = str(soup.body) if soup.body else ''
                if not body_content:
                    body_content = f"<body>{html_content}</body>"
                
                # 新しいHTMLを構築
                new_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{soup.title.string if soup.title else 'Web Page'}</title>
    {metadata_script}
    {css_reset}
    {pixel_script}
    {head_content}
</head>
{body_content}
</html>"""
                
            except Exception as e:
                app.logger.error(f"HTML解析エラー: {str(e)}")
                # 解析に失敗した場合は、単純に挿入
                new_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Web Page</title>
    {metadata_script}
    {css_reset}
    {pixel_script}
</head>
<body>
    {html_content}
</body>
</html>"""
        
        # 一意のファイル名を生成
        file_id = str(uuid.uuid4())
        file_name = f"{file_id}.html"
        
        # Vercel Blobにコンテンツを保存
        blob_url = run_async(blob_put(file_name, new_html))
        
        if not blob_url:
            # Blobストレージが利用できない場合はファイルに保存（後方互換性）
            file_path = os.path.join(URLS_DIR, file_name)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_html)
            app.logger.warning("Blobストレージが使用できないため、ファイルに保存しました")
        
        # URLリストに追加
        url_list = get_url_list()
        
        # 本番環境のURLを取得
        if os.environ.get('VERCEL_URL'):
            base_url = f"https://{os.environ.get('VERCEL_URL')}"
        else:
            base_url = request.host_url.rstrip('/')
            
        new_url = f"/view/{file_id}"
        full_url = f"{base_url}{new_url}"
        
        # 古いエントリの削除（100件を超える場合）
        max_urls = config.get('max_urls', 100)
        if len(url_list) >= max_urls:
            # 作成日時でソートして古いものから削除
            url_list.sort(key=lambda x: x.get('created_at', ''))
            oldest_entry = url_list.pop(0)
            
            # Blobストレージから古いコンテンツを削除
            if oldest_entry.get('blob_url'):
                run_async(blob_delete(oldest_entry['blob_url']))
            
            # 後方互換性のため、ファイルも削除
            oldest_file = os.path.join(URLS_DIR, f"{oldest_entry['id']}.html")
            if os.path.exists(oldest_file):
                try:
                    os.remove(oldest_file)
                except Exception as e:
                    app.logger.error(f"古いファイルの削除エラー: {str(e)}")
        
        # URLエントリの作成
        url_entry = {
            'id': file_id,
            'original_url': url,
            'new_url': new_url,
            'full_url': full_url,
            'pixel_id': tiktok_pixel_id,
            'custom_code': pixel_code and len(pixel_code.strip()) > 0,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'blob_url': blob_url,  # Blobストレージのリンク
            'youtube': is_youtube
        }
        
        url_list.append(url_entry)
        
        if save_url_list(url_list):
            flash('新しいURLが正常に作成されました！', 'success')
        else:
            flash('URLリストの保存中にエラーが発生しました。', 'error')
            
        return redirect(url_for('index'))
    
    except Exception as e:
        app.logger.error(f"URL作成エラー: {str(e)}")
        flash(f'エラー: {str(e)}', 'error')
        return redirect(url_for('index'))

# TikTokピクセルスクリプト生成関数
def generate_tiktok_pixel_script(pixel_id):
    """標準的なTikTokピクセル初期化スクリプトを生成"""
    if not pixel_id:
        pixel_id = 'CM0EQKBC77U7DDDCEF4G'  # デフォルトID
    
    return f"""
<script>
!function (w, d, t) {{
    w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"],ttq.setAndDefer=function(t,e){{t[e]=function(){{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}};}};for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);ttq.instance=function(t){{for(var e=ttq._i[t]||[],n=0;n<ttq.methods.length;n++)ttq.setAndDefer(e,ttq.methods[n]);return e}};ttq.load=function(e,n){{var i="https://analytics.tiktok.com/i18n/pixel/events.js";ttq._i=ttq._i||{{}},ttq._i[e]=[],ttq._i[e]._u=i,ttq._t=ttq._t||{{}},ttq._t[e]=+new Date,ttq._o=ttq._o||{{}},ttq._o[e]=n||{{}};var o=document.createElement("script");o.type="text/javascript",o.async=!0,o.src=i+"?sdkid="+e+"&lib="+t;var a=document.getElementsByTagName("script")[0];a.parentNode.insertBefore(o,a)}};
    ttq.load('{pixel_id}');
    ttq.page();
}}(window, document, 'ttq');
</script>
"""

@app.route('/view/<file_id>')
def view(file_id):
    try:
        # 設定情報を取得
        config = get_config()
        
        # URLステータスチェック (有効期限、クリック数制限など)
        url_list = get_url_list()
        target_url = None
        
        for url in url_list:
            if url.get('id') == file_id:
                target_url = url
                break
        
        if not target_url:
            # URLが見つからない場合
            app.logger.info(f"URL not found: {file_id}")
            return render_template('error.html', error="指定されたURLは存在しません"), 404
        
        # HTMLコンテンツを取得（Blob優先）
        html_content = None
        
        # 1. BlobストレージからHTMLコンテンツを取得
        if target_url.get('blob_url'):
            app.logger.info(f"Blobストレージからコンテンツを取得: {target_url['blob_url']}")
            html_content = run_async(blob_get(target_url['blob_url']))
        
        # 2. Blobが存在しない、または取得に失敗した場合はファイルから読み込み
        if not html_content:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id + '.html')
            if not os.path.exists(file_path):
                app.logger.error(f"HTML file not found: {file_path}")
                return render_template('error.html', error="ファイルが見つかりません"), 404
            
            # ファイルからHTMLコンテンツを読み込み
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
            except UnicodeDecodeError:
                # UTF-8で読めない場合はバイナリモードで読み込み、エンコーディングを推測
                with open(file_path, 'rb') as f:
                    raw_content = f.read()
                
                # エンコーディングを推測
                encodings = ['utf-8', 'shift_jis', 'euc-jp', 'cp932', 'iso-2022-jp']
                for encoding in encodings:
                    try:
                        html_content = raw_content.decode(encoding)
                        app.logger.info(f"エンコーディング自動検出: {encoding}")
                        break
                    except UnicodeDecodeError:
                        continue
                
                # どのエンコーディングでも読み込めなかった場合、置換モードでUTF-8を試す
                if not html_content:
                    html_content = raw_content.decode('utf-8', errors='replace')
                    app.logger.warning("エンコーディング推測失敗、UTF-8(置換モード)で読み込み")
        
        if not html_content:
            return render_template('error.html', error="コンテンツの読み込みに失敗しました"), 500
        
        # クリック数を更新
        update_click_count(file_id)
        
        # YouTubeコンテンツの場合は特別な処理をしない
        if target_url.get('youtube'):
            return html_content
        
        # HTML charset指定の確認
        if 'charset=' not in html_content:
            # charsetメタタグがない場合は追加
            head_end_pos = html_content.find('</head>')
            if head_end_pos > 0:
                html_content = html_content[:head_end_pos] + '\n<meta charset="UTF-8">\n' + html_content[head_end_pos:]
        
        # レスポンスを返す
        response = make_response(html_content)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        
        # キャッシュ制御ヘッダー
        # プライベートキャッシュは許可するが、共有キャッシュは不可（CDNなど）
        response.headers['Cache-Control'] = 'private, max-age=3600'
        
        return response
        
    except Exception as e:
        app.logger.error(f"ファイル表示エラー: {str(e)}", exc_info=True)
        return render_template('error.html', error="コンテンツの表示中にエラーが発生しました"), 500

@app.route('/delete/<file_id>', methods=['POST'])
def delete(file_id):
    try:
        # URLリストから該当エントリを取得
        url_list = get_url_list()
        target_url = None
        updated_list = []
        
        for url in url_list:
            if url.get('id') == file_id:
                target_url = url
            else:
                updated_list.append(url)
        
        if not target_url:
            flash('指定されたURLが見つかりませんでした。', 'error')
            return redirect(url_for('index'))
        
        # Blobストレージのファイルを削除
        if target_url.get('blob_url'):
            blob_deleted = run_async(blob_delete(target_url['blob_url']))
            if blob_deleted:
                app.logger.info(f"Blobから削除: {target_url['blob_url']}")
            else:
                app.logger.warning(f"Blob削除失敗: {target_url['blob_url']}")
        
        # 後方互換性: ファイルを削除
        file_path = os.path.join(URLS_DIR, f"{file_id}.html")
        if os.path.exists(file_path):
            os.remove(file_path)
            app.logger.info(f"ファイルから削除: {file_path}")
        
        # クリック数の削除
        run_async(kv_del(f'clicks:{file_id}'))
        
        # リストを更新
        if save_url_list(updated_list):
            flash('URLが正常に削除されました！', 'success')
        else:
            flash('URLリストの更新中にエラーが発生しました。', 'error')
        
        # キャッシュから削除
        cache.delete(f'view/{file_id}')
        
        return redirect(url_for('index'))
    except Exception as e:
        app.logger.error(f"URL削除エラー: {str(e)}", exc_info=True)
        flash(f'削除中にエラーが発生しました: {str(e)}', 'error')
        return redirect(url_for('index'))

# エラーハンドラー
@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', error="ページが見つかりません"), 404

@app.errorhandler(500)
def internal_server_error(e):
    app.logger.error(f"500エラー発生: {str(e)}", exc_info=True)
    return render_template('error.html', error="サーバーエラーが発生しました"), 500

# リソース使用状況をクリアするエンドポイント（メンテナンス用）
@app.route('/maintenance/clear-cache', methods=['POST'])
def clear_cache():
    try:
        cache.clear()
        return "キャッシュをクリアしました", 200
    except Exception as e:
        return f"エラー: {str(e)}", 500

# 相対パスを絶対パスに変換する関数
def fix_relative_paths(html_content, base_domain, original_url):
    """HTMLコンテンツ内の相対パスを絶対パスに変換する"""
    try:
        # 軽量なHTMLパース処理を試みる
        return fix_relative_paths_minimal(html_content, original_url)
    except Exception as e:
        # 軽量処理に失敗した場合はログを残す
        app.logger.error(f"最小限HTMLパースエラー: {str(e)}、従来の方法を試みます")
    try:
        # HTMLパースを試みる
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # src属性の修正
        for tag in soup.find_all(attrs={"src": True}):
            if tag['src'].startswith('//'):
                # プロトコル相対URLの場合
                tag['src'] = f"https:{tag['src']}"
            elif not tag['src'].startswith(('http://', 'https://', 'data:', '#')):
                # 相対パスの場合
                tag['src'] = urljoin(original_url, tag['src'])
        
        # href属性の修正
        for tag in soup.find_all(attrs={"href": True}):
            if tag['href'].startswith('//'):
                # プロトコル相対URLの場合
                tag['href'] = f"https:{tag['href']}"
            elif not tag['href'].startswith(('http://', 'https://', 'data:', '#', 'javascript:', 'mailto:')):
                # 相対パスの場合
                tag['href'] = urljoin(original_url, tag['href'])
        
        # srcset属性の修正
        for tag in soup.find_all(attrs={"srcset": True}):
            srcset = tag['srcset']
            # スペースで分割し、URLとサイズ指定部分に分ける
            parts = srcset.split(',')
            new_parts = []
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                # URLと記述子（1x, 2x など）を分離
                url_parts = part.split(' ')
                url = url_parts[0]
                if url.startswith('//'):
                    url = f"https:{url}"
                elif not url.startswith(('http://', 'https://', 'data:', '#')):
                    url = urljoin(original_url, url)
                url_parts[0] = url
                new_parts.append(' '.join(url_parts))
            tag['srcset'] = ', '.join(new_parts)
        
        # スタイルシート内のURLを修正
        for style_tag in soup.find_all('style'):
            if style_tag.string:
                # url() パターンを検索して絶対パスに変換
                style_content = style_tag.string
                # url(...) パターンを抽出
                url_pattern = re.compile(r'url\([\'"]?([^\'" \)]+)[\'"]?\)')
                
                def replace_url(match):
                    url = match.group(1)
                    if url.startswith('//'):
                        return f"url(https:{url})"
                    elif not url.startswith(('http://', 'https://', 'data:', '#')):
                        return f"url({urljoin(original_url, url)})"
                    else:
                        return match.group(0)
                
                style_tag.string = url_pattern.sub(replace_url, style_content)
                
        # インラインスタイルのURLを修正
        for tag in soup.find_all(attrs={"style": True}):
            style_content = tag['style']
            # url() パターンを抽出
            url_pattern = re.compile(r'url\([\'"]?([^\'" \)]+)[\'"]?\)')
            
            def replace_url(match):
                url = match.group(1)
                if url.startswith('//'):
                    return f"url(https:{url})"
                elif not url.startswith(('http://', 'https://', 'data:', '#')):
                    return f"url({urljoin(original_url, url)})"
                else:
                    return match.group(0)
            
            tag['style'] = url_pattern.sub(replace_url, style_content)
            
        return str(soup)
    except Exception as e:
        # HTMLパースに失敗した場合は、正規表現でのみ置換を試みる
        app.logger.error(f"HTMLパースエラー: {str(e)}、正規表現で置換を試みます")
        
        # 基本的な属性の置換パターン
        patterns = [
            (r'(src|href)=[\'"](?!(?:http|https|data|#|javascript|mailto))([^\'"]+)[\'"]', r'\1="' + original_url + r'\2"'),
            (r'(src|href)=[\'"]//([^\'"]+)[\'"]', r'\1="https://\2"'),
                (r'url\([\'"]?((?!(?:http|https|data|#))([^\)\'"\s]+))[\'"]?\)', r'url(' + original_url + r'\1)'),
            (r'url\([\'"]?//([^\)]+?)[\'"]?\)', r'url(https://\1)')
        ]
        
        for pattern, replacement in patterns:
            html_content = re.sub(pattern, replacement, html_content)
            
        return html_content

# 最小限のHTMLパース処理で相対パスを絶対パスに変換する新関数
def fix_relative_paths_minimal(html_content, base_url):
    """正規表現のみを使用して相対パスを絶対パスに変換し、元のHTMLの構造を可能な限り維持"""
    try:
        # より正確な正規表現パターン
        patterns = [
            # src, href属性のパターン
            (r'(src|href)=[\'"]((?!(?:http:|https:|data:|#|javascript:|mailto:))([^\'"]+))[\'"]', 
             lambda m: f'{m.group(1)}="{urljoin(base_url, m.group(2))}"'),
            
            # プロトコル相対URLのパターン
            (r'(src|href)=[\'"]//([^\'"]+)[\'"]', 
             r'\1="https://\2"'),
             
            # srcset属性のパターン
            (r'srcset=[\'"](.*?)[\'"]', 
             lambda m: 'srcset="' + process_srcset(m.group(1), base_url) + '"'),
             
            # CSSのurl()パターン 
            (r'url\([\'"]?((?!(?:http:|https:|data:|#))([^\)\'"\s]+))[\'"]?\)', 
             lambda m: f'url({urljoin(base_url, m.group(1))})')
        ]
        
        # 変換処理
        for pattern, replacement in patterns:
            html_content = re.sub(pattern, replacement, html_content)
        
        return html_content
    except Exception as e:
        app.logger.error(f"最小限の置換エラー: {str(e)}")
        # 例外発生時は元のHTMLをそのまま返す
        return html_content

# srcset属性内の複数URLを処理するヘルパー関数
def process_srcset(srcset_value, base_url):
    parts = srcset_value.split(',')
    new_parts = []
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        # URLと記述子（1x, 2x など）を分離
        components = part.split()
        if not components:
            continue
            
        url = components[0]
        
        # URLの処理
        if url.startswith('//'):
            url = f"https:{url}"
        elif not url.startswith(('http://', 'https://', 'data:', '#')):
            url = urljoin(base_url, url)
            
        # 新しいURLと記述子を結合
        new_part = url
        if len(components) > 1:
            new_part += ' ' + ' '.join(components[1:])
            
        new_parts.append(new_part)
        
    return ', '.join(new_parts)

# Vercel環境変数確認（デバッグ用）
@app.route('/check-env')
def check_env():
    try:
        env_info = {
            "KV_REST_API_URL": KV_REST_API_URL is not None,
            "KV_REST_API_TOKEN": KV_REST_API_TOKEN is not None,
            "BLOB_READ_WRITE_TOKEN": BLOB_READ_WRITE_TOKEN is not None,
            "VERCEL_ENV": os.environ.get('VERCEL_ENV'),
            "URLS_DIR": URLS_DIR,
            "URLS_DIR_EXISTS": os.path.exists(URLS_DIR),
            "URL_LIST_FILE_EXISTS": os.path.exists(URL_LIST_FILE)
        }
        return jsonify(env_info)
    except Exception as e:
        app.logger.error(f"環境変数確認エラー: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True) 