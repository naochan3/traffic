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
from urllib.parse import urlparse
from urllib.parse import urljoin
import re

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
    
    return response.text

@app.route('/create', methods=['POST'])
def create():
    original_url = request.form.get('original_url')
    pixel_id = request.form.get('pixel_id')
    
    if not original_url or not pixel_id:
        flash('URLとPixel IDを入力してください。', 'error')
        return redirect(url_for('index'))
    
    try:
        # オリジナルURLからドメイン部分を抽出
        parsed_url = urlparse(original_url)
        base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        # オリジナルURLからHTMLを取得（キャッシュを使用）
        try:
            html_content = fetch_html_content(original_url)
        except requests.exceptions.RequestException as e:
            flash(f'URLアクセスエラー: {str(e)}', 'error')
            return redirect(url_for('index'))
        
        # スクリプト隔離用のiframeを使うかどうかの判定
        use_iframe = 'true' == request.form.get('use_iframe', 'false')
        
        if use_iframe:
            # iframeを使ってTikTok Pixelを隔離する方法 - CORSエラー修正対応
            pixel_script = f'''
<!-- TikTok Pixel (iframe隔離版 - セキュリティ改善) -->
<script>
// ページの読み込みが完了した後にスクリプトを実行
window.addEventListener('DOMContentLoaded', function() {{
    // TikTok Pixelを含む非表示div要素を作成
    var pixelContainer = document.createElement('div');
    pixelContainer.id = 'tt-pixel-container';
    pixelContainer.style.cssText = 'position:absolute;width:1px;height:1px;overflow:hidden;visibility:hidden;display:none;';
    
    // ピクセルコードを実行するスクリプト要素
    var pixelScript = document.createElement('script');
    pixelScript.type = 'text/javascript';
    pixelScript.innerHTML = `
        try {{
            !function (w, d, t) {{
                w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"],ttq.setAndDefer=function(t,e){{t[e]=function(){{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}};}};for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);ttq.instance=function(t){{for(var e=ttq._i[t]||[],n=0;n<ttq.methods.length;n++)ttq.setAndDefer(e,ttq.methods[n]);return e}},ttq.load=function(e,n){{var i="https://analytics.tiktok.com/i18n/pixel/events.js";ttq._i=ttq._i||{{}},ttq._i[e]=[],ttq._i[e]._u=i,ttq._t=ttq._t||{{}},ttq._t[e]=+new Date,ttq._o=ttq._o||{{}},ttq._o[e]=n||{{}};var o=document.createElement("script");o.type="text/javascript",o.async=!0,o.src=i+"?sdkid="+e+"&lib="+t;var a=document.getElementsByTagName("script")[0];a.parentNode.insertBefore(o,a)}};

                ttq.load('{pixel_id}');
                ttq.page();
            }}(window, document, 'ttq');
        }} catch(err) {{
            console.error('TikTok Pixel実行エラー:', err);
        }}
    `;
    
    // コンテナにスクリプトを追加
    pixelContainer.appendChild(pixelScript);
    
    // bodyの最後に追加
    if (document.body) {{
        document.body.appendChild(pixelContainer);
    }} else {{
        // bodyがまだない場合は遅延実行
        window.addEventListener('load', function() {{
            document.body.appendChild(pixelContainer);
        }});
    }}
}});
</script>
'''
        else:
            # 通常の遅延読み込み方式 (CSS分離対策付き)
            pixel_script = f'''
<!-- TikTok Pixel (遅延読み込み版) -->
<style>
/* TikTok Pixelのコンテナを非表示にする */
#tt-pixel-container {{
    position: absolute !important;
    width: 1px !important;
    height: 1px !important;
    overflow: hidden !important;
    clip: rect(0,0,0,0) !important;
    white-space: nowrap !important;
    border: 0 !important;
    padding: 0 !important;
    margin: -1px !important;
    visibility: hidden !important;
    display: none !important;
}}
</style>
<script>
// ページの読み込みが完了した後にスクリプトを実行
window.addEventListener('DOMContentLoaded', function() {{
    // TikTok Pixelを含む非表示div要素を作成
    var pixelContainer = document.createElement('div');
    pixelContainer.id = 'tt-pixel-container';
    
    // ピクセルコードを実行するスクリプト要素
    var pixelScript = document.createElement('script');
    pixelScript.type = 'text/javascript';
    pixelScript.innerHTML = `
        try {{
            !function (w, d, t) {{
                w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"],ttq.setAndDefer=function(t,e){{t[e]=function(){{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}};}};for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);ttq.instance=function(t){{for(var e=ttq._i[t]||[],n=0;n<ttq.methods.length;n++)ttq.setAndDefer(e,ttq.methods[n]);return e}},ttq.load=function(e,n){{var i="https://analytics.tiktok.com/i18n/pixel/events.js";ttq._i=ttq._i||{{}},ttq._i[e]=[],ttq._i[e]._u=i,ttq._t=ttq._t||{{}},ttq._t[e]=+new Date,ttq._o=ttq._o||{{}},ttq._o[e]=n||{{}};var o=document.createElement("script");o.type="text/javascript",o.async=!0,o.src=i+"?sdkid="+e+"&lib="+t;var a=document.getElementsByTagName("script")[0];a.parentNode.insertBefore(o,a)}};

                ttq.load('{pixel_id}');
                ttq.page();
            }}(window, document, 'ttq');
        }} catch(err) {{
            console.error('TikTok Pixel実行エラー:', err);
        }}
    `;
    
    // コンテナにスクリプトを追加
    pixelContainer.appendChild(pixelScript);
    
    // 安全にDOMに追加
    if (document.body) {{
        document.body.appendChild(pixelContainer);
    }} else {{
        window.addEventListener('load', function() {{
            document.body.appendChild(pixelContainer);
        }});
    }}
}});
</script>
'''
        
        # 相対パスをコメントに記録
        base_href = f'<!-- 元のURL: {original_url} -->'
    
        # メタデータを埋め込むためのスクリプト（埋め込みURLの元情報をデータとして保持）
        metadata_script = f'''
<!-- メタデータ情報（TikTok Pixelで参照） -->
<script type="application/json" id="afitori-metadata">
{{
  "originalUrl": "{original_url}",
  "pixelId": "{pixel_id}",
  "generatedAt": "{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
  "protocol": "{urlparse(original_url).scheme}",
  "domain": "{urlparse(original_url).netloc}"
}}
</script>
'''
        
        # <head>タグを探してその終了直前にメタデータとピクセルスクリプトを挿入
        head_end_pos = html_content.find("</head>")
        if head_end_pos > 0:
            new_html = html_content[:head_end_pos] + base_href + metadata_script + pixel_script + html_content[head_end_pos:]
        else:
            # headタグが見つからない場合の処理
            head_start_pos = html_content.find("<head")
            if head_start_pos > 0:
                head_start_end = html_content.find(">", head_start_pos)
                if head_start_end > 0:
                    insert_pos = head_start_end + 1
                    # 相対パスをコメントに記録
                    base_href = f'<!-- 元のURL: {original_url} -->'
                    new_html = html_content[:insert_pos] + base_href + metadata_script + pixel_script + html_content[insert_pos:]
                else:
                    # HTMLタグを探してその直後に挿入
                    html_pos = html_content.find("<html")
                    if html_pos > 0:
                        html_end = html_content.find(">", html_pos)
                        if html_end > 0:
                            insert_pos = html_end + 1
                            # 相対パスをコメントに記録
                            new_html = html_content[:insert_pos] + "<head>" + f'<!-- 元のURL: {original_url} -->' + metadata_script + pixel_script + "</head>" + html_content[insert_pos:]
                        else:
                            # HTMLタグもない場合は先頭に挿入
                            new_html = "<!DOCTYPE html><html><head>" + f'<!-- 元のURL: {original_url} -->' + metadata_script + pixel_script + "</head>" + html_content
                    else:
                        # HTMLタグもない場合は先頭に挿入
                        new_html = "<!DOCTYPE html><html><head>" + f'<!-- 元のURL: {original_url} -->' + metadata_script + pixel_script + "</head>" + html_content
            else:
                # headタグがない場合は先頭に挿入
                new_html = "<!DOCTYPE html><html><head>" + f'<!-- 元のURL: {original_url} -->' + metadata_script + pixel_script + "</head>" + html_content
        
        # 修正したHTMLを保存する前に相対パスを絶対パスに変換
        new_html = fix_relative_paths(new_html, base_domain, original_url)
        
        # 一意のファイル名を生成
        file_id = str(uuid.uuid4())
        file_name = f"{file_id}.html"
        file_path = os.path.join(URLS_DIR, file_name)
        
        # HTMLエンコーディングを確認し、必要に応じて修正
        if '<?xml' not in new_html and '<!DOCTYPE' not in new_html and '<html' in new_html:
            # XMLやDOCTYPE宣言がない場合は追加
            if '<head' in new_html:
                head_pos = new_html.find('<head')
                head_end = new_html.find('>', head_pos) + 1
                
                # CSSリセットとメタタグを追加
                reset_css = '''<meta charset="UTF-8">
<!-- TikTok Pixel用スタイル隔離 -->
<style id="tiktok-pixel-reset">
/* TikTok Pixelが元のページスタイルに影響しないようにするリセット */
#tiktok-pixel-container, iframe[title="TikTok Pixel"] {
    display: none !important;
    width: 0 !important;
    height: 0 !important;
    position: absolute !important;
    left: -9999px !important;
    top: -9999px !important;
    opacity: 0 !important;
    visibility: hidden !important;
    overflow: hidden !important;
    pointer-events: none !important;
}

/* 保護スタイル - オリジナルコンテンツのスタイルが維持されるように特定のTikTokスタイルだけを上書き */
/* ttqが挿入する可能性のあるスタイルを選択的に無効化 */
html body div.ttq-loading, 
html body div.ttq-confirm,
html body div.ttq-pixel-base,
html body iframe.ttq-transport-frame {
    display: none !important;
    width: 0 !important;
    height: 0 !important;
    position: absolute !important;
    left: -9999px !important;
    top: -9999px !important;
    opacity: 0 !important;
    visibility: hidden !important;
    overflow: hidden !important;
    pointer-events: none !important;
}
</style>'''
                
                # metaタグとCSSリセットがなければ追加
                if '<meta charset=' not in new_html[:head_end+100]:
                    new_html = new_html[:head_end] + reset_css + new_html[head_end:]
                elif 'tiktok-pixel-reset' not in new_html:
                    charset_pos = new_html.find('<meta charset=')
                    if charset_pos > 0:
                        charset_end = new_html.find('>', charset_pos) + 1
                        new_html = new_html[:charset_end] + reset_css[20:] + new_html[charset_end:]
                    else:
                        new_html = new_html[:head_end] + reset_css + new_html[head_end:]
            else:
                # headタグがない場合
                html_pos = new_html.find('<html')
                html_end = new_html.find('>', html_pos) + 1
                new_html = new_html[:html_end] + f'<head>{reset_css}</head>' + new_html[html_end:]
        elif '<!DOCTYPE' not in new_html and '<html' not in new_html:
            # HTMLフォーマットでない場合は基本的なHTML構造で囲む
            new_html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<!-- 元のURL: {original_url} -->
{metadata_script}
<!-- TikTok Pixel用スタイル隔離 -->
<style id="tiktok-pixel-reset">
/* TikTok Pixelが元のページスタイルに影響しないようにするリセット */
#tiktok-pixel-container, iframe[title="TikTok Pixel"] {
    display: none !important;
    width: 0 !important;
    height: 0 !important;
    position: absolute !important;
    left: -9999px !important;
    top: -9999px !important;
    opacity: 0 !important;
    visibility: hidden !important;
    overflow: hidden !important;
    pointer-events: none !important;
}

/* 保護スタイル - オリジナルコンテンツのスタイルが維持されるように特定のTikTokスタイルだけを上書き */
/* ttqが挿入する可能性のあるスタイルを選択的に無効化 */
html body div.ttq-loading, 
html body div.ttq-confirm,
html body div.ttq-pixel-base,
html body iframe.ttq-transport-frame {
    display: none !important;
    width: 0 !important;
    height: 0 !important;
    position: absolute !important;
    left: -9999px !important;
    top: -9999px !important;
    opacity: 0 !important;
    visibility: hidden !important;
    overflow: hidden !important;
    pointer-events: none !important;
}
</style>
<title>Redirected Content</title>
</head>
<body>
{new_html}
</body>
</html>'''
        
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
        
        # Content-Typeヘッダーでエンコーディングを明示的に指定
        return content, 200, {'Content-Type': 'text/html; charset=utf-8'}
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

# 相対パスを絶対パスに変換する関数
def fix_relative_paths(html_content, base_domain, original_url):
    """HTMLコンテンツ内の相対パスを絶対パスに変換する"""
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
                url_pattern = re.compile(r'url\([\'"]?([^\'")]+)[\'"]?\)')
                
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
            url_pattern = re.compile(r'url\([\'"]?([^\'")]+)[\'"]?\)')
            
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
            (r'url\([\'"]?(?!(?:http|https|data|#))([^\)]+?)[\'"]?\)', r'url(' + original_url + r'\1)'),
            (r'url\([\'"]?//([^\)]+?)[\'"]?\)', r'url(https://\1)')
        ]
        
        for pattern, replacement in patterns:
            html_content = re.sub(pattern, replacement, html_content)
            
        return html_content

if __name__ == '__main__':
    app.run(debug=True) 