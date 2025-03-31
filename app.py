import os
import json
import uuid
import time
import logging
import requests
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse
import re
import sys
import asyncio

# Vercel Blobモジュールを先にインポート
try:
    import vercel_blob
    from vercel_blob import put, get, list, del_
except ImportError as e:
    # インポートエラーをログ出力せずに変数に保存
    vercel_blob_import_error = str(e)

from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, make_response, session, abort

# 環境変数を確認
APP_ENV = os.environ.get('APP_ENV', 'development')
DEBUG = os.environ.get('DEBUG', 'True').lower() == 'true'
PORT = int(os.environ.get('PORT', 8080))

# アプリケーションディレクトリ
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# アプリケーション初期化
app = Flask(__name__, 
            static_folder=os.path.join(BASE_DIR, 'static'),
            template_folder=os.path.join(BASE_DIR, 'templates'))
app.secret_key = os.environ.get('SECRET_KEY', 'dev_secret_key')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB制限

# ログ設定
logging.basicConfig(level=logging.INFO)
if not DEBUG:
    # 本番環境ではより詳細なログを記録
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    # ファイルハンドラを追加（本番環境の場合）
    if not os.path.exists('logs'):
        os.makedirs('logs')
    file_handler = logging.FileHandler('logs/app.log')
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)

# Vercel環境の検出を改善 (VERCEL=1またはVERCEL_ENV=production)
IS_VERCEL = os.environ.get('VERCEL') == '1' or os.environ.get('VERCEL_ENV') in ['production', 'preview', 'development']
if IS_VERCEL:
    UPLOAD_FOLDER = '/tmp/urls'
    URL_LIST_FILE = '/tmp/url_list.json'
    CONFIG_FILE = '/tmp/config.json'
    app.logger.info(f"Vercel環境を検出しました。一時ディレクトリを使用: {UPLOAD_FOLDER}")
else:
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'urls')
    URL_LIST_FILE = os.path.join(BASE_DIR, 'url_list.json')
    CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
    app.logger.info(f"ローカル環境です。基本ディレクトリを使用: {UPLOAD_FOLDER}")

# 設定をアプリケーションに適用
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# URLsディレクトリを作成（存在しない場合）
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# URLリストファイルが存在しない場合に初期化ファイルを作成
if not os.path.exists(URL_LIST_FILE):
    try:
        # /tmpディレクトリが存在することを確認
        os.makedirs(os.path.dirname(URL_LIST_FILE), exist_ok=True)
        # 空のURLリストファイルを作成
        with open(URL_LIST_FILE, 'w') as f:
            json.dump([], f)
        app.logger.info(f"新しいURLリストファイルを作成しました: {URL_LIST_FILE}")
    except Exception as e:
        app.logger.error(f"URLリストファイルの初期化に失敗: {str(e)}")

# Vercel Blob/KVストレージのサポート
try:
    app.logger.info("Vercel Blobモジュールのインポートを試みます")
    app.logger.info(f"Pythonバージョン: {sys.version}")
    app.logger.info(f"sys.path: {sys.path}")
    
    # 事前にインポートされているかチェック
    if 'vercel_blob' not in sys.modules:
        app.logger.error("vercel_blob モジュールがインポートされていません")
        if 'vercel_blob_import_error' in globals():
            app.logger.error(f"vercel_blob インポートエラー: {vercel_blob_import_error}")
        raise ImportError("vercel_blob モジュールが見つかりません")
    
    app.logger.info(f"vercel_blob バージョン: {vercel_blob.__version__ if hasattr(vercel_blob, '__version__') else '不明'}")
    
    # 環境変数の確認
    app.logger.info(f"VERCEL環境変数: {os.environ.get('VERCEL')}")
    blob_token = os.environ.get('BLOB_READ_WRITE_TOKEN')
    
    app.logger.info(f"BLOB_READ_WRITE_TOKEN設定: {'有効 (長さ:' + str(len(blob_token)) + ')' if blob_token else '未設定'}")

    # トークンの有効性をさらに詳細にチェック
    if blob_token:
        if len(blob_token) < 20 or not blob_token.startswith('vercel_blob_rw_'):
            app.logger.warning(f"BLOB_READ_WRITE_TOKENの形式が不正と思われます: {blob_token[:15]}...")
        else:
            app.logger.info("BLOB_READ_WRITE_TOKENの形式は正常と思われます")
    
    # KV Storeへの接続 - 使用しない
    kv = None
    
    # Blob機能のラッパー関数
    def blob_put(key, data):
        """Blobストレージにデータを格納する"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            coroutine = put(key, data, {"access": "public"})
            url = loop.run_until_complete(coroutine)
            loop.close()
            return url
        except Exception as e:
            app.logger.error(f"Blobストレージエラー (put): {str(e)}")
            return None
            
    def blob_get(url):
        """Blobストレージからデータを取得する"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            coroutine = get(url)
            result = loop.run_until_complete(coroutine)
            loop.close()
            return result
        except Exception as e:
            app.logger.error(f"Blobストレージエラー (get): {str(e)}")
            return None
            
    def blob_delete(url):
        """Blobストレージからデータを削除する"""
        try:
            if url:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                coroutine = del_(url)
                result = loop.run_until_complete(coroutine)
                loop.close()
                return True
            return False
        except Exception as e:
            app.logger.error(f"Blobストレージエラー (delete): {str(e)}")
            return False
    
    # KV関数（互換性のためにダミー関数を提供）
    async def kv_get(key):
        """ファイルシステムからデータを取得する代替実装"""
        try:
            file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"{key}.json")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    return json.load(f)
            return None
        except Exception as e:
            app.logger.error(f"ファイル読み込みエラー: {str(e)}")
            return None
                
    async def kv_set(key, value):
        """ファイルシステムにデータを保存する代替実装"""
        try:
            file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"{key}.json")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            with open(file_path, 'w') as f:
                json.dump(value, f)
            return True
        except Exception as e:
            app.logger.error(f"ファイル保存エラー: {str(e)}")
            return False
                
    async def kv_delete(key):
        """ファイルシステムからデータを削除する代替実装"""
        try:
            file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"{key}.json")
            if os.path.exists(file_path):
                os.remove(file_path)
                return True
            return False
        except Exception as e:
            app.logger.error(f"ファイル削除エラー: {str(e)}")
            return False
        
    # 非同期関数を実行するヘルパー
    def run_async(coroutine):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # coroutineがNoneの場合やコルーチンでない場合の処理を追加
        if coroutine is None:
            return None
        
        if not asyncio.iscoroutine(coroutine):
            app.logger.warning(f"coroutineではないオブジェクトが渡されました: {type(coroutine)}")
            return coroutine
        
        try:
            return loop.run_until_complete(coroutine)
        except Exception as e:
            app.logger.error(f"coroutine実行中のエラー: {str(e)}")
            return None
        
    app.logger.info("Vercel Blobストレージが利用可能です")
except ImportError as e:
    # KV/Blob機能が利用できない場合は、ダミー関数を提供
    app.logger.error(f"Vercel Blobストレージのインポートに失敗: {str(e)}")
    app.logger.warning("互換性のためのダミー関数を提供します")
    kv = None
    
    async def blob_put(key, data):
        return None
        
    async def blob_get(url):
        return None
        
    async def blob_delete(url):
        return False
        
    async def kv_get(key):
        return None
        
    async def kv_set(key, value):
        return False
        
    async def kv_delete(key):
        return False
        
    def run_async(coroutine):
        return None
except Exception as e:
    app.logger.error(f"Vercel Blobストレージ初期化エラー: {str(e)}")
    kv = None
    
    async def blob_put(key, data):
        return None
        
    async def blob_get(url):
        return None
        
    async def blob_delete(url):
        return False
        
    async def kv_get(key):
        return None
        
    async def kv_set(key, value):
        return False
        
    async def kv_delete(key):
        return False
        
    def run_async(coroutine):
        return None

# URLリスト管理
def get_url_list():
    """保存されたURLリストを取得 (Vercel KV & 後方互換性)"""
    try:
        # ローカル環境ではURLリストファイルから読み込む
        try:
            if os.path.exists(URL_LIST_FILE):
                try:
                    with open(URL_LIST_FILE, 'r') as f:
                        url_list = json.load(f)
                        app.logger.info(f"ファイルからURLリストを取得しました: {URL_LIST_FILE}")
                        return url_list
                except json.JSONDecodeError as json_err:
                    app.logger.error(f"URLリストJSONデコードエラー: {str(json_err)}")
                    # 破損したJSONファイルを再作成
                    with open(URL_LIST_FILE, 'w') as f:
                        json.dump([], f)
                    app.logger.info(f"破損したURLリストファイルを再作成しました: {URL_LIST_FILE}")
                    return []
            else:
                # ファイルが存在しない場合は新しい空のリストを返す
                app.logger.warning(f"URLリストファイルが存在しません: {URL_LIST_FILE}")
                # URLリストファイルを作成
                try:
                    # /tmpディレクトリが存在することを確認
                    os.makedirs(os.path.dirname(URL_LIST_FILE), exist_ok=True)
                    # 空のURLリストファイルを作成
                    with open(URL_LIST_FILE, 'w') as f:
                        json.dump([], f)
                    app.logger.info(f"新しいURLリストファイルを作成しました: {URL_LIST_FILE}")
                except Exception as e:
                    app.logger.warning(f"新しいURLリストファイル作成に失敗: {str(e)}")
                return []
        except Exception as e:
            app.logger.error(f"URLリストファイル読み込みエラー: {str(e)}")
            return []
    except Exception as e:
        app.logger.error(f"URLリスト取得エラー: {str(e)}")
        # エラーが発生した場合は空のリストを返す
        return []

def save_url_list(url_list):
    """URLリストを保存 (Vercel KV & 後方互換性)"""
    try:
        # URL内のblobオブジェクトがcoroutineでないことを確認
        for url in url_list:
            # coroutineオブジェクトを文字列に変換
            if 'blob_url' in url and asyncio.iscoroutine(url['blob_url']):
                app.logger.warning(f"coroutineオブジェクトを文字列に変換します: {url['blob_url']}")
                url['blob_url'] = str(url['blob_url'])
        
        # ファイルに保存（後方互換性）
        file_saved = False
        try:
            with open(URL_LIST_FILE, 'w') as f:
                json.dump(url_list, f)
            app.logger.info(f"URLリストをファイルに保存しました: {URL_LIST_FILE}")
            file_saved = True
        except Exception as e:
            # Vercel環境ではファイル書き込みエラーは許容
            if os.environ.get('VERCEL') == '1':
                app.logger.warning(f"Vercel環境でのURLリストファイル保存をスキップ: {str(e)}")
                # tmpディレクトリに保存を試みる
                try:
                    os.makedirs(os.path.dirname(URL_LIST_FILE), exist_ok=True)
                    with open(URL_LIST_FILE, 'w') as f:
                        json.dump(url_list, f)
                    app.logger.info(f"URLリストをtmpディレクトリに保存しました: {URL_LIST_FILE}")
                    file_saved = True
                except Exception as tmp_err:
                    app.logger.error(f"tmpディレクトリへのURLリストファイル保存エラー: {str(tmp_err)}")
            else:
                app.logger.error(f"URLリストファイル保存エラー: {str(e)}")
                file_saved = False
            
        return file_saved
    except Exception as e:
        app.logger.error(f"URLリスト保存エラー: {str(e)}")
        return False

# 設定管理
def get_config():
    """アプリケーション設定を取得"""
    try:
        # 環境変数からの設定を優先
        config = {
            'site_name': os.environ.get('SITE_NAME', 'AfiTori'),
            'pixel_id': os.environ.get('PIXEL_ID', ''),
            'max_urls': int(os.environ.get('MAX_URLS', 100)),
            'admin_enabled': os.environ.get('ADMIN_ENABLED', 'True').lower() == 'true',
            'admin_password': os.environ.get('ADMIN_PASSWORD', 'admin'),
        }
        
        # 設定ファイルが存在する場合は読み込み
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                file_config = json.load(f)
                # 環境変数で明示的に設定されていない場合のみファイルの設定を使用
                for key, value in file_config.items():
                    if key not in os.environ:
                        config[key] = value
        
        return config
    except Exception as e:
        app.logger.error(f"設定取得エラー: {str(e)}")
        # デフォルト設定を返す
        return {
            'site_name': 'AfiTori',
            'pixel_id': '',
            'max_urls': 100,
            'admin_enabled': True,
            'admin_password': 'admin',
        }

def update_config(new_config):
    """アプリケーション設定を更新"""
    try:
        # 現在の設定を取得
        current_config = get_config()
        # 新しい設定で更新
        current_config.update(new_config)
        
        # 設定ファイルに保存
        with open(CONFIG_FILE, 'w') as f:
            json.dump(current_config, f)
            
        return True
    except Exception as e:
        app.logger.error(f"設定更新エラー: {str(e)}")
        return False

# TikTok Pixelスクリプト生成
def generate_tiktok_pixel_script(pixel_id_or_code):
    """TikTok Pixelスクリプトを生成する"""
    if pixel_id_or_code.startswith('<script'):
        # 既にスクリプトタグの場合はそのまま返す
        return pixel_id_or_code
    else:
        # IDの場合は標準的なスクリプトを生成
        pixel_id = pixel_id_or_code.strip()
        return f"""
<script>
!function (w, d, t) {{
  w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"],ttq.setAndDefer=function(t,e){{t[e]=function(){{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}}}; for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);ttq.instance=function(t){{for(var e=ttq._i[t]||[],n=0;n<ttq.methods.length;n++)ttq.setAndDefer(e,ttq.methods[n]);return e}},ttq.load=function(e,n){{var i="https://analytics.tiktok.com/i18n/pixel/events.js";ttq._i=ttq._i||{{}},ttq._i[e]=[],ttq._i[e]._u=i,ttq._t=ttq._t||{{}},ttq._t[e]=+new Date,ttq._o=ttq._o||{{}},ttq._o[e]=n||{{}};var o=document.createElement("script");o.type="text/javascript",o.async=!0,o.src=i+"?sdkid="+e+"&lib="+t;var a=document.getElementsByTagName("script")[0];a.parentNode.insertBefore(o,a)}};
  ttq.load('{pixel_id}');
  ttq.page();
}}(window, document, 'ttq');
</script>
"""

# クリック数の更新
def update_click_count(file_id):
    """URLのクリック数を更新する"""
    try:
        url_list = get_url_list()
        for url in url_list:
            if url.get('id') == file_id:
                if 'clicks' not in url:
                    url['clicks'] = 0
                url['clicks'] += 1
                url['last_clicked'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                save_url_list(url_list)
                break
    except Exception as e:
        app.logger.error(f"クリック数更新エラー: {str(e)}")

# ルーティング
@app.route('/')
def index():
    url_list = get_url_list()
    # 作成日時の降順でソート
    url_list.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return render_template('index.html', url_list=url_list)

@app.route('/create', methods=['POST'])
def create():
    try:
        app.logger.info("URL作成リクエスト受信")
        url = request.form.get('url')
        pixel_code = request.form.get('pixel_code', '')
        app.logger.info(f"ピクセルコード受信: {pixel_code[:100]}..." if pixel_code else "ピクセルコードなし")
        
        if not url:
            return jsonify({'error': 'URLが必要です'}), 400
        
        # URL検証
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        # ピクセルコードからIDを抽出または既存のスクリプトをそのまま使用
        if pixel_code and '<script' in pixel_code:
            # 完全なスクリプトの場合
            pixel_script = pixel_code
            # IDを抽出（ベストエフォート）
            match = re.search(r'ttq\.load\([\'"]([A-Z0-9]+)[\'"]', pixel_code)
            tiktok_pixel_id = match.group(1) if match else "カスタムコード"
        elif pixel_code:
            # IDのみの場合
            tiktok_pixel_id = re.sub(r'[^A-Z0-9]', '', pixel_code.upper())
            pixel_script = generate_tiktok_pixel_script(tiktok_pixel_id)
        else:
            # デフォルト設定を使用
            config = get_config()
            tiktok_pixel_id = config.get('pixel_id', 'CM0EQKBC77U7DDDCEF4G')
            pixel_script = generate_tiktok_pixel_script(tiktok_pixel_id)
        
        # URLからHTMLコンテンツを取得
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            html_content = response.text
        except requests.exceptions.RequestException as e:
            app.logger.error(f"URLからのコンテンツ取得に失敗: {str(e)}")
            flash(f'URLからのコンテンツ取得に失敗しました: {str(e)}', 'error')
            return redirect(url_for('index'))
        
        # HTMLにピクセルコードを挿入
        head_closing_tag = '</head>'
        if head_closing_tag in html_content:
            # </head> の直前にピクセルコードを挿入
            head_end_index = html_content.find(head_closing_tag)
            new_html = html_content[:head_end_index] + pixel_script + html_content[head_end_index:]
        else:
            # <head>タグがない場合は先頭に挿入
            new_html = pixel_script + html_content
        
        # 一意のファイル名を生成
        file_id = str(uuid.uuid4())
        file_name = f"{file_id}.html"
        
        # Vercel Blobにコンテンツを保存
        blob_url = None
        try:
            app.logger.info(f"Blobストレージの保存を開始: ファイル名={file_name}, サイズ={len(new_html)} バイト")
            blob_token = os.environ.get('BLOB_READ_WRITE_TOKEN')
            if blob_token and len(blob_token) > 10:  # トークンが実際に存在し有効な長さがあることを確認
                # 同期関数として実装したblob_putを直接呼び出す
                blob_url = blob_put(file_name, new_html)
                app.logger.info(f"Blob保存結果: {blob_url if blob_url else 'None'}")
                
                # coroutineオブジェクトが返された場合の対応
                if asyncio.iscoroutine(blob_url):
                    app.logger.warning("blob_putがcoroutineを返しました。同期的に実行します。")
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        blob_url = loop.run_until_complete(blob_url)
                        loop.close()
                        app.logger.info(f"coroutineを実行した結果: {blob_url}")
                    except Exception as coroutine_err:
                        app.logger.error(f"coroutineの実行中にエラーが発生: {str(coroutine_err)}")
                        blob_url = None
            else:
                app.logger.error(f"BLOB_READ_WRITE_TOKENが設定されていないか不正な値です: {blob_token[:5] if blob_token else 'None'}")
        except Exception as e:
            app.logger.error(f"Blob保存処理中の例外: {str(e)}", exc_info=True)
            blob_url = None
        
        if not blob_url:
            # Vercel環境ではBlobストレージは必須だが、ローカル開発用に条件分岐
            if os.environ.get('VERCEL') == '1':
                # Vercel環境でのエラー詳細
                env_details = ""
                blob_token = os.environ.get('BLOB_READ_WRITE_TOKEN', '')
                
                if not blob_token:
                    env_details = "BLOB_READ_WRITE_TOKEN環境変数が設定されていません。"
                elif len(blob_token) < 20:
                    env_details = f"BLOB_READ_WRITE_TOKENの値が短すぎます(長さ:{len(blob_token)})。有効なトークンを設定してください。"
                elif not blob_token.startswith('vercel_blob_rw_'):
                    env_details = "BLOB_READ_WRITE_TOKENの形式が不正です。Vercelダッシュボードで新しいBlobストレージを作成してください。"
                else:
                    env_details = "BLOB_READ_WRITE_TOKENが設定されていますが、Blobストレージへの接続に失敗しました。トークンが有効か確認してください。"
                
                # エラー詳細をより詳しく表示
                error_msg = f"Vercel環境でBlobストレージが使用できません。{env_details} Vercelダッシュボードで環境変数を確認してください。"
                app.logger.error(error_msg)
                
                # デバッグ情報ページへのリンクを含めたエラーメッセージ
                flash(f'サーバー設定エラー: Blobストレージが利用できません。{env_details} Vercelダッシュボードの「Settings」→「Environment Variables」で環境変数を確認してください。', 'error')
                return redirect(url_for('index'))
            
            # ローカル環境の場合はファイルに保存
            file_path = os.path.join(UPLOAD_FOLDER, file_name)
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_html)
                app.logger.warning("Blobストレージが使用できないため、ファイルに保存しました")
            except Exception as e:
                app.logger.error(f"ファイル保存エラー: {str(e)}")
                flash(f'ファイル保存エラー: {str(e)}', 'error')
                return redirect(url_for('index'))
        
        # URLリストに追加
        url_list = get_url_list()
        
        # 本番環境のURLを取得
        if os.environ.get('VERCEL_URL'):
            base_url = f"https://{os.environ.get('VERCEL_URL')}"
        else:
            base_url = request.host_url.rstrip('/')
            
        new_url = f"/view/{file_id}"
        full_url = f"{base_url}{new_url}"
        
        # 古いエントリの削除（上限を超える場合）
        config = get_config()
        max_urls = config.get('max_urls', 100)
        if len(url_list) >= max_urls:
            # 作成日時でソートして古いものから削除
            url_list.sort(key=lambda x: x.get('created_at', ''))
            oldest_entry = url_list.pop(0)
            
            # Blobストレージから古いコンテンツを削除
            if oldest_entry.get('blob_url'):
                run_async(blob_delete(oldest_entry['blob_url']))
            
            # ファイルも削除（後方互換性のため）
            oldest_file = os.path.join(UPLOAD_FOLDER, f"{oldest_entry['id']}.html")
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
            'custom_code': pixel_code and '<script' in pixel_code,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'blob_url': blob_url
        }
        
        url_list.append(url_entry)
        
        if save_url_list(url_list):
            flash('新しいURLが正常に作成されました！', 'success')
        else:
            flash('URLリストの保存中にエラーが発生しました。', 'error')
            
        return redirect(url_for('index'))
    
    except Exception as e:
        app.logger.error(f"URL作成エラー: {str(e)}", exc_info=True)
        flash(f'エラー: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/view/<file_id>')
def view(file_id):
    try:
        # URLリストから対象URLを検索
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
            try:
                if os.environ.get('BLOB_READ_WRITE_TOKEN'):
                    # 同期関数として実装したblob_getを直接呼び出す
                    html_content = blob_get(target_url['blob_url'])
                else:
                    app.logger.error("BLOB_READ_WRITE_TOKENが設定されていません")
            except Exception as e:
                app.logger.error(f"Blobからのコンテンツ取得に失敗: {str(e)}")
        
        # 2. Blobが存在しない、または取得に失敗した場合はファイルから読み込み
        if not html_content:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id + '.html')
            app.logger.info(f"ファイルからコンテンツを取得: {file_path}")
            
            if not os.path.exists(file_path):
                app.logger.error(f"HTML file not found: {file_path}")
                return render_template('error.html', error="ファイルが見つかりません"), 404
            
            # ファイルからHTMLコンテンツを読み込み
            try:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        html_content = f.read()
                except UnicodeDecodeError:
                    # UTF-8で読めない場合はバイナリモードで読み込み
                    with open(file_path, 'rb') as f:
                        raw_content = f.read()
                    
                    # エンコーディングを推測
                    encodings = ['utf-8', 'shift_jis', 'euc-jp', 'cp932', 'iso-2022-jp']
                    for encoding in encodings:
                        try:
                            html_content = raw_content.decode(encoding)
                            app.logger.info(f"エンコーディング検出: {encoding}")
                            break
                        except UnicodeDecodeError:
                            continue
                    
                    # どのエンコーディングでも読み込めなかった場合
                    if not html_content:
                        html_content = raw_content.decode('utf-8', errors='replace')
                        app.logger.warning(f"不明なエンコーディング、置換モードで読み込み")
            except Exception as e:
                app.logger.error(f"ファイル読み込みエラー: {str(e)}")
                return render_template('error.html', error=f"コンテンツの読み込みに失敗しました: {str(e)}"), 500
        
        if not html_content:
            return render_template('error.html', error="コンテンツの読み込みに失敗しました"), 500
        
        # クリック数を更新
        try:
            update_click_count(file_id)
        except Exception as e:
            app.logger.error(f"クリック数更新エラー: {str(e)}")
            # 更新失敗は無視する
        
        # レスポンスを返す
        response = make_response(html_content)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        return response
        
    except Exception as e:
        app.logger.error(f"ファイル表示エラー: {str(e)}", exc_info=True)
        return render_template('error.html', error="コンテンツの表示中にエラーが発生しました"), 500

@app.route('/delete/<file_id>', methods=['POST'])
def delete(file_id):
    try:
        url_list = get_url_list()
        target_index = None
        target_url = None
        
        for i, url in enumerate(url_list):
            if url.get('id') == file_id:
                target_index = i
                target_url = url
                break
        
        if target_index is not None:
            # リストから削除
            url_list.pop(target_index)
            
            # Blobストレージから削除
            if target_url.get('blob_url'):
                try:
                    if os.environ.get('BLOB_READ_WRITE_TOKEN'):
                        # 同期関数として実装したblob_deleteを直接呼び出す
                        blob_deleted = blob_delete(target_url['blob_url'])
                        app.logger.info(f"Blobストレージからファイルを削除: {target_url['blob_url']}, 結果: {blob_deleted}")
                    else:
                        app.logger.error("BLOB_READ_WRITE_TOKENが設定されていません")
                except Exception as e:
                    app.logger.error(f"Blobストレージからの削除に失敗: {str(e)}")
            
            # ファイルも削除（後方互換性のため）
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id + '.html')
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    app.logger.info(f"ファイルシステムからファイルを削除: {file_path}")
                except Exception as e:
                    app.logger.error(f"ファイルシステムからの削除に失敗: {str(e)}")
                    # Vercel環境での削除エラーは無視
                    if os.environ.get('VERCEL') != '1':
                        flash(f'ファイル削除エラー: {str(e)}', 'warning')
            
            # URLリストを保存
            if save_url_list(url_list):
                flash('URLが正常に削除されました', 'success')
            else:
                flash('URLリストの保存中にエラーが発生しました', 'error')
        else:
            flash('指定されたURLが見つかりません', 'error')
            
        return redirect(url_for('index'))
        
    except Exception as e:
        app.logger.error(f"URL削除エラー: {str(e)}")
        flash(f'エラー: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    config = get_config()
    
    if not config.get('admin_enabled', True):
        abort(404)
    
    if request.method == 'POST':
        password = request.form.get('password')
        
        if password == config.get('admin_password', 'admin'):
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash('パスワードが正しくありません', 'error')
    
    return render_template('admin_login.html')

@app.route('/admin/dashboard', methods=['GET', 'POST'])
def admin_dashboard():
    config = get_config()
    
    if not config.get('admin_enabled', True) or not session.get('admin_logged_in'):
        abort(404)
    
    if request.method == 'POST':
        new_config = {
            'site_name': request.form.get('site_name', 'AfiTori'),
            'pixel_id': request.form.get('pixel_id', ''),
            'max_urls': int(request.form.get('max_urls', 100)),
            'admin_password': request.form.get('admin_password', config.get('admin_password', 'admin'))
        }
        
        if update_config(new_config):
            flash('設定が更新されました', 'success')
        else:
            flash('設定の更新中にエラーが発生しました', 'error')
            
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin_dashboard.html', config=config)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('ログアウトしました', 'success')
    return redirect(url_for('index'))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', error="ページが見つかりません"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', error="サーバーエラーが発生しました"), 500

# 診断用のエンドポイントを追加
@app.route('/debug/env')
def debug_env():
    """環境変数とストレージ設定の診断情報を表示（本番環境では無効化すべき）"""
    if not DEBUG:
        return jsonify({"error": "デバッグモードでのみ利用可能です"}), 403
        
    # 環境診断情報
    env_info = {
        "python_version": sys.version,
        "environment": {
            "VERCEL": os.environ.get('VERCEL'),
            "VERCEL_URL": os.environ.get('VERCEL_URL'),
            "APP_ENV": APP_ENV,
            "DEBUG": DEBUG,
        },
        "storage_config": {
            "KV_REST_API_URL": bool(os.environ.get('KV_REST_API_URL')),
            "KV_REST_API_TOKEN": bool(os.environ.get('KV_REST_API_TOKEN')),
            "BLOB_READ_WRITE_TOKEN": bool(os.environ.get('BLOB_READ_WRITE_TOKEN')),
        },
        "file_paths": {
            "BASE_DIR": BASE_DIR,
            "UPLOAD_FOLDER": UPLOAD_FOLDER,
            "URL_LIST_FILE": URL_LIST_FILE,
            "upload_folder_exists": os.path.exists(UPLOAD_FOLDER),
            "url_list_file_exists": os.path.exists(URL_LIST_FILE),
        },
        "modules": {}
    }
    
    # インストール済みのモジュール確認
    try:
        import pkg_resources
        for package in ['vercel-kv', 'vercel-blob', 'flask']:
            try:
                version = pkg_resources.get_distribution(package).version
                env_info["modules"][package] = version
            except pkg_resources.DistributionNotFound:
                env_info["modules"][package] = "未インストール"
    except ImportError:
        env_info["modules"]["note"] = "pkg_resources が利用できないため、モジュール情報を取得できません"
    
    # 接続テスト
    env_info["connection_test"] = {}
    
    # KV接続テスト
    try:
        if os.environ.get('KV_REST_API_URL') and os.environ.get('KV_REST_API_TOKEN'):
            test_result = run_async(kv_set('test_key', 'test_value'))
            env_info["connection_test"]["kv_write"] = bool(test_result)
            
            test_result = run_async(kv_get('test_key'))
            env_info["connection_test"]["kv_read"] = test_result == 'test_value'
            
            run_async(kv_delete('test_key'))
        else:
            env_info["connection_test"]["kv"] = "設定なし"
    except Exception as e:
        env_info["connection_test"]["kv_error"] = str(e)
    
    # Blob接続テスト
    try:
        if os.environ.get('BLOB_READ_WRITE_TOKEN'):
            test_content = "Test content for Blob Storage"
            # 同期関数として実装したblob_putを直接呼び出す
            blob_url = blob_put('test.txt', test_content)
            env_info["connection_test"]["blob_write"] = bool(blob_url)
            
            if blob_url:
                # 同期関数として実装したblob_getを直接呼び出す
                content = blob_get(blob_url)
                env_info["connection_test"]["blob_read"] = content == test_content
                
                # 同期関数として実装したblob_deleteを直接呼び出す
                deleted = blob_delete(blob_url)
                env_info["connection_test"]["blob_delete"] = bool(deleted)
        else:
            env_info["connection_test"]["blob"] = "設定なし"
    except Exception as e:
        env_info["connection_test"]["blob_error"] = str(e)
    
    return jsonify(env_info)

# アプリケーション起動（開発環境のみ）
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG) 