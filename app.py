from flask import Flask, render_template, request, redirect, url_for, flash
import os
import requests
from bs4 import BeautifulSoup
import uuid
import json
from datetime import datetime
import time
from functools import lru_cache
from flask_caching import Cache

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key_for_testing')

# キャッシュ設定
cache_config = {
    "DEBUG": os.environ.get('FLASK_DEBUG', 'false').lower() == 'true',
    "CACHE_TYPE": "SimpleCache",  # Vercelの環境に適したシンプルなインメモリキャッシュ
    "CACHE_DEFAULT_TIMEOUT": 300  # 5分間のデフォルトタイムアウト
}
cache = Cache(app, config=cache_config)

# URLsの保存先ディレクトリ
# Vercelは読み書き可能な/tmpディレクトリを提供
if os.environ.get('VERCEL_ENV') == 'production':
    URLS_DIR = '/tmp/urls'
else:
    URLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'urls')

if not os.path.exists(URLS_DIR):
    os.makedirs(URLS_DIR)

# URLリストのJSONファイル
URL_LIST_FILE = os.path.join(URLS_DIR, 'url_list.json')
if not os.path.exists(URL_LIST_FILE):
    with open(URL_LIST_FILE, 'w') as f:
        json.dump([], f)

# URLリストを取得する関数（キャッシュ対応）
@cache.cached(timeout=300, key_prefix='url_list')
def get_url_list():
    try:
        if not os.path.exists(URL_LIST_FILE):
            with open(URL_LIST_FILE, 'w') as f:
                json.dump([], f)
        
        with open(URL_LIST_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        app.logger.error(f"URLリストの読み込みエラー: {str(e)}")
        return []

# URLリストを保存する関数
def save_url_list(url_list):
    try:
        with open(URL_LIST_FILE, 'w') as f:
            json.dump(url_list, f)
        # キャッシュを無効化して次回の取得で最新データを読み込む
        cache.delete('url_list')
        return True
    except Exception as e:
        app.logger.error(f"URLリストの保存エラー: {str(e)}")
        return False

@app.route('/')
def index():
    url_list = get_url_list()
    return render_template('index.html', url_list=url_list)

# HTMLリクエストの結果をキャッシュする
@cache.memoize(timeout=3600)  # 1時間キャッシュ
def fetch_html_content(url):
    """URLからHTMLコンテンツを取得し、キャッシュする"""
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.text

@app.route('/create', methods=['POST'])
def create():
    original_url = request.form.get('original_url')
    pixel_id = request.form.get('pixel_id')
    
    if not original_url or not pixel_id:
        flash('URLとPixel IDを入力してください。', 'error')
        return redirect(url_for('index'))
    
    try:
        # オリジナルURLからHTMLを取得（キャッシュを使用）
        try:
            html_content = fetch_html_content(original_url)
        except requests.exceptions.RequestException as e:
            flash(f'URLアクセスエラー: {str(e)}', 'error')
            return redirect(url_for('index'))
        
        # TikTok Pixelスクリプトの作成
        pixel_script = f'''
<script>
!function (w, d, t) {{{{
  w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"],ttq.setAndDefer=function(t,e){{{{t[e]=function(){{{{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}}}}}}};for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);ttq.instance=function(t){{{{for(var e=ttq._i[t]||[],n=0;n<ttq.methods.length;n++)ttq.setAndDefer(e,ttq.methods[n]);return e}}}},ttq.load=function(e,n){{{{var i="https://analytics.tiktok.com/i18n/pixel/events.js";ttq._i=ttq._i||{{}},ttq._i[e]=[],ttq._i[e]._u=i,ttq._t=ttq._t||{{}},ttq._t[e]=+new Date,ttq._o=ttq._o||{{}},ttq._o[e]=n||{{}};var o=document.createElement("script");o.type="text/javascript",o.async=!0,o.src=i+"?sdkid="+e+"&lib="+t;var a=document.getElementsByTagName("script")[0];a.parentNode.insertBefore(o,a)}}}};

  ttq.load('{pixel_id}');
  ttq.page();
}}}}(window, document, 'ttq');
</script>
'''
        
        # 最小限の変更で済むようにheadタグを見つけて直接操作する
        try:
            # まず<head>タグの終了位置を探す
            head_end_index = html_content.find("</head>")
            
            if head_end_index > 0:
                # headタグが見つかった場合、そこにスクリプトを挿入
                new_html = html_content[:head_end_index] + pixel_script + html_content[head_end_index:]
            else:
                # headタグが見つからない場合、BeautifulSoupで解析
                soup = BeautifulSoup(html_content, 'html5lib')
                
                if not soup.head:
                    head = soup.new_tag('head')
                    meta_charset = soup.new_tag('meta')
                    meta_charset['charset'] = 'utf-8'
                    head.append(meta_charset)
                    
                    if soup.html:
                        soup.html.insert(0, head)
                    else:
                        html = soup.new_tag('html')
                        html.append(head)
                        soup.append(html)
                
                # Pixelスクリプトを挿入
                soup.head.append(BeautifulSoup(pixel_script, 'html.parser'))
                new_html = str(soup)
        except Exception as e:
            app.logger.error(f"HTML解析エラー: {str(e)}")
            # エラーが発生した場合、単純に文字列操作で挿入を試みる
            head_tag = html_content.find("<head>")
            if head_tag >= 0:
                head_end = html_content.find("</head>", head_tag)
                if head_end >= 0:
                    new_html = html_content[:head_end] + pixel_script + html_content[head_end:]
                else:
                    new_html = html_content.replace("<head>", "<head>" + pixel_script)
            else:
                # headタグがない場合は、bodyタグの直後に挿入
                body_tag = html_content.find("<body")
                if body_tag >= 0:
                    body_end = html_content.find(">", body_tag)
                    if body_end >= 0:
                        new_html = html_content[:body_end+1] + f"<head>{pixel_script}</head>" + html_content[body_end+1:]
                    else:
                        new_html = html_content
                else:
                    # bodyタグもない場合は、htmlタグの直後に挿入
                    html_tag = html_content.find("<html")
                    if html_tag >= 0:
                        html_end = html_content.find(">", html_tag)
                        if html_end >= 0:
                            new_html = html_content[:html_end+1] + f"<head>{pixel_script}</head>" + html_content[html_end+1:]
                        else:
                            new_html = html_content
                    else:
                        # htmlタグもない場合は、ファイルの先頭に挿入
                        new_html = f"<!DOCTYPE html><html><head>{pixel_script}</head>" + html_content
        
        # 一意のファイル名を生成
        file_id = str(uuid.uuid4())
        file_name = f"{file_id}.html"
        file_path = os.path.join(URLS_DIR, file_name)
        
        # 修正したHTMLを保存
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_html)
        
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
        if len(url_list) >= 100:
            # 作成日時でソートして古いものから削除
            url_list.sort(key=lambda x: x.get('created_at', ''))
            oldest_entry = url_list.pop(0)
            # 対応するファイルも削除
            oldest_file = os.path.join(URLS_DIR, f"{oldest_entry['id']}.html")
            if os.path.exists(oldest_file):
                try:
                    os.remove(oldest_file)
                except Exception as e:
                    app.logger.error(f"古いファイルの削除エラー: {str(e)}")
        
        url_list.append({
            'id': file_id,
            'original_url': original_url,
            'pixel_id': pixel_id,
            'new_url': new_url,
            'full_url': full_url,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        
        if save_url_list(url_list):
            flash('新しいURLが正常に作成されました！', 'success')
        else:
            flash('URLリストの保存中にエラーが発生しました。', 'error')
            
        return redirect(url_for('index'))
    
    except Exception as e:
        app.logger.error(f"URL作成エラー: {str(e)}")
        flash(f'エラー: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/view/<file_id>')
@cache.cached(timeout=3600, query_string=True)  # キャッシュを1時間に設定
def view(file_id):
    file_path = os.path.join(URLS_DIR, f"{file_id}.html")
    if not os.path.exists(file_path):
        return "ファイルが見つかりません", 404
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except Exception as e:
        app.logger.error(f"ファイル読み込みエラー: {str(e)}")
        return "ファイルの読み込み中にエラーが発生しました", 500

@app.route('/delete/<file_id>', methods=['POST'])
def delete(file_id):
    file_path = os.path.join(URLS_DIR, f"{file_id}.html")
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # キャッシュから削除
        cache.delete_memoized(view, file_id)
        
        url_list = get_url_list()
        url_list = [url for url in url_list if url['id'] != file_id]
        
        if save_url_list(url_list):
            flash('URLが正常に削除されました！', 'success')
        else:
            flash('URLリストの更新中にエラーが発生しました。', 'error')
        
        return redirect(url_for('index'))
    except Exception as e:
        app.logger.error(f"URL削除エラー: {str(e)}")
        flash(f'削除中にエラーが発生しました: {str(e)}', 'error')
        return redirect(url_for('index'))

# エラーハンドラー
@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', error="ページが見つかりません"), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('error.html', error="サーバーエラーが発生しました"), 500

# リソース使用状況をクリアするエンドポイント（メンテナンス用）
@app.route('/maintenance/clear-cache', methods=['POST'])
def clear_cache():
    try:
        cache.clear()
        return "キャッシュをクリアしました", 200
    except Exception as e:
        return f"エラー: {str(e)}", 500

if __name__ == '__main__':
    app.run(debug=True) 