from flask import Flask, render_template, request, redirect, url_for, flash, make_response
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

# アップロードフォルダをapp.configに設定
app.config['UPLOAD_FOLDER'] = URLS_DIR

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
            with open(URL_LIST_FILE, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False)
        
        # まずUTF-8で試す
        try:
            with open(URL_LIST_FILE, 'r', encoding='utf-8') as f:
                url_list = json.load(f)
                
            # 文字化け検出
            has_mojibake = False
            for url in url_list:
                for key, value in url.items():
                    if isinstance(value, str) and ('繝' in value or '縺' in value):
                        has_mojibake = True
                        break
                if has_mojibake:
                    break
                    
            # 文字化けが検出された場合、別のエンコーディングを試す
            if has_mojibake:
                app.logger.info("URL一覧に文字化けを検出しました。別のエンコーディングを試みます。")
                encodings = ['shift_jis', 'euc-jp', 'cp932', 'iso-2022-jp']
                
                for encoding in encodings:
                    try:
                        with open(URL_LIST_FILE, 'rb') as f:
                            content = f.read()
                        decoded_content = content.decode(encoding, errors='ignore')
                        
                        # JSONとして解析できるか試す
                        fixed_url_list = json.loads(decoded_content)
                        
                        # 文字化けチェック
                        fixed_has_mojibake = False
                        for url in fixed_url_list:
                            for key, value in url.items():
                                if isinstance(value, str) and ('繝' in value or '縺' in value):
                                    fixed_has_mojibake = True
                                    break
                            if fixed_has_mojibake:
                                break
                                
                        if not fixed_has_mojibake:
                            # 文字化けが解消された場合は修正したリストを使用
                            app.logger.info(f"URL一覧の文字化けを{encoding}で修正しました")
                            url_list = fixed_url_list
                            
                            # 修正したリストを保存
                            with open(URL_LIST_FILE, 'w', encoding='utf-8') as f:
                                json.dump(url_list, f, ensure_ascii=False)
                            break
                    except Exception as e:
                        app.logger.error(f"エンコーディング{encoding}での読み込み失敗: {str(e)}")
            
            return url_list
            
        except json.JSONDecodeError:
            app.logger.error("JSONデコードエラー、ファイルを再初期化します")
            with open(URL_LIST_FILE, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False)
            return []
            
    except Exception as e:
        app.logger.error(f"URLリストの読み込みエラー: {str(e)}")
        return []

# URLリストを保存する関数
def save_url_list(url_list):
    try:
        # 文字化けが疑われるフィールドを修正
        for url in url_list:
            for key, value in url.items():
                if isinstance(value, str) and ('繝' in value or '縺' in value):
                    # 文字化けしている可能性があるため修正を試みる
                    try:
                        # UTF-8バイト列として扱い、別のエンコーディングで再解釈
                        raw_bytes = value.encode('utf-8', errors='ignore')
                        for encoding in ['shift_jis', 'euc-jp', 'cp932', 'iso-2022-jp']:
                            try:
                                # 一度encodeしてからdecodeしてみる
                                test_decode = raw_bytes.decode(encoding, errors='ignore')
                                if '繝' not in test_decode and '縺' not in test_decode:
                                    url[key] = test_decode
                                    app.logger.info(f"文字化け修正: {key}フィールドを{encoding}で再解釈しました")
                                    break
                            except Exception:
                                continue
                    except Exception as e:
                        app.logger.error(f"文字化け修正エラー (フィールド: {key}): {str(e)}")
        
        with open(URL_LIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(url_list, f, ensure_ascii=False)
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

@app.route('/create', methods=['POST'])
def create():
    original_url = request.form.get('original_url')
    pixel_code = request.form.get('pixel_code', '')
    
    if not original_url:
        flash('URLを入力してください。', 'error')
        return redirect(url_for('index'))
    
    if not pixel_code:
        flash('Pixelコードを入力してください。', 'error')
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
        
        # 文字化け検出と修正
        if '繝' in html_content or '縺' in html_content:
            app.logger.info(f"取得したHTMLコンテンツに文字化けを検出: {original_url}")
            
            # 一般的な文字化けパターンの修正を試みる
            common_mojibake = {
                '繧ｫ繧ｿ繧ｫ繝': 'カタカナ',
                '鬮倥＆': '高さ',
                '讌ｽ螢ｫ': '楽天',
                '髮ｻ蟄': '電子',
                '繝｡繝ｼ繧ｫ繝ｼ': 'メーカー',
                '繝悶Λ繝ｳ繝': 'ブランド',
                '繧ｷ繝ｧ繝': 'ショッ',
                '繧｢繧､繝': 'アイテ',
                '繧｢繝': 'アプ',
                '繝昴う繝ｳ繝': 'ポイント'
            }
            
            for mojibake, correct in common_mojibake.items():
                if mojibake in html_content:
                    html_content = html_content.replace(mojibake, correct)
            
            # 正規表現を使ったより複雑な置換パターン
            # 「繝」の後に「ｼ」が続くパターンはよく「ー」に変換される
            html_content = re.sub(r'繝ｼ', 'ー', html_content)
            
            # 楽天特有の文字化けパターン
            html_content = re.sub(r'Rakuten\s+[Ff]ashion', '楽天ファッション', html_content)
        
        # 提供されたPixelコードを直接使用する
        # スクリプトタグを削除して純粋なJSコードのみ抽出
        pixel_code = pixel_code.strip()
        if pixel_code.startswith('<script'):
            start_idx = pixel_code.find('>') + 1
            end_idx = pixel_code.rfind('</script>')
            if start_idx > 0 and end_idx > start_idx:
                pixel_code = pixel_code[start_idx:end_idx].strip()
        
        # 通常の非表示要素方式（直接コードを使用）
        pixel_script = """
<!-- TikTok Pixel（非表示要素方式） -->
<script type="text/javascript">
// ピクセルコードは独立したスコープで実行し、グローバル変数の競合を防ぐ
(function() {
    // ページが完全に読み込まれてから実行することで、DOM操作の競合を防ぐ
    function loadTikTokPixel() {
        try {
            // CSS競合を防ぐために完全に独立したコンテナを作成
    var pixelContainer = document.createElement('div');
            pixelContainer.id = 'tiktok-pixel-container';
            pixelContainer.setAttribute('aria-hidden', 'true');
            pixelContainer.style.cssText = 'position:absolute!important;width:0!important;height:0!important;overflow:hidden!important;opacity:0!important;pointer-events:none!important;display:none!important;left:-9999px!important;top:-9999px!important;';
            
            // shadowDOM使用 - 完全にメインページから隔離
            var shadow = null;
            try {
                // Mode: closed でCSSの競合を完全に防ぐ
                shadow = pixelContainer.attachShadow({mode: 'closed'});
            } catch(e) {
                console.error('ShadowDOM非対応: フォールバック使用', e);
            }
            
            // スクリプト要素（ピクセル用）
    var pixelScript = document.createElement('script');
    pixelScript.type = 'text/javascript';
            pixelScript.async = true;
            
            // ピクセルコードを独立スコープに閉じ込める
            pixelScript.textContent = `
                // ピクセルコードを実行する前にグローバル変数と名前空間を保護
                (function(w, d) {
                    try {
                        // オリジナルのJavaScriptオブジェクトを保存
                        var _origDefineProperty = Object.defineProperty;
                        var _origGetComputedStyle = window.getComputedStyle;
                        
                        // CSSとDOMへの干渉を防ぐためのラッパー関数
                        window.getComputedStyle = function(el) {
                            try {
                                return _origGetComputedStyle.apply(this, arguments);
                            } catch(e) {
                                // エラー時にはダミーのCSSオブジェクトを返す
                                return {getPropertyValue: function() { return ''; }};
                            }
                        };
                        
                        // ピクセルコード実行
                        """ + pixel_code.replace("'", "\\'") + """
                        
                        // 元のグローバル関数を復元
                        window.getComputedStyle = _origGetComputedStyle;
                    } catch(e) {
                        console.error('[TikTok]: ピクセル実行エラー', e);
                    }
                })(window, document);
            `;
            
            // shadowDOMが利用可能ならそこに追加、そうでなければ直接コンテナに追加
            if (shadow) {
                // shadowDOM内のスタイル定義 - 外部に影響しない
                var shadowStyle = document.createElement('style');
                shadowStyle.textContent = 'div { all: initial !important; }';
                shadow.appendChild(shadowStyle);
                
                var innerContainer = document.createElement('div');
                innerContainer.style.cssText = 'all: initial !important; visibility: hidden !important; position: absolute !important; width: 0 !important; height: 0 !important;';
                innerContainer.appendChild(pixelScript);
                shadow.appendChild(innerContainer);
            } else {
    pixelContainer.appendChild(pixelScript);
            }
            
            // コンテナをbodyではなくheadに追加することでCSSの衝突を減らす
            if (document.head) {
                document.head.appendChild(pixelContainer);
            } else {
                window.addEventListener('DOMContentLoaded', function() {
                    if (document.head) document.head.appendChild(pixelContainer);
                    else if (document.body) document.body.appendChild(pixelContainer);
                });
            }
            
            // 5秒後にピクセルコンテナを削除してメモリリークを防止
            setTimeout(function() {
                try {
                    if (document.contains(pixelContainer)) {
                        pixelContainer.parentNode.removeChild(pixelContainer);
                    }
                } catch(e) {
                    console.error('[TikTok]: クリーンアップエラー', e);
                }
            }, 5000);
        } catch(err) {
            // エラーをサイレントに処理（ユーザーに表示されないよう）
            console.error('[TikTok]: Pixel設定エラー', err);
        }
    }
    
    // ページの読み込みステータスに応じた実行タイミング制御
    if (document.readyState === 'complete') {
        // すでにページが読み込み完了している場合
        setTimeout(loadTikTokPixel, 2000);
    } else {
        // ページ読み込み完了後に実行
        window.addEventListener('load', function() {
            setTimeout(loadTikTokPixel, 2000);
        });
    }
})();
</script>
"""
        
        # メタデータを埋め込むためのスクリプト（埋め込みURLの元情報をデータとして保持）
        metadata_script = """
<!-- メタデータ情報（TikTok Pixelで参照） -->
<script type="application/json" id="afitori-metadata">
{{
  "originalUrl": "{0}",
  "generatedAt": "{1}",
  "protocol": "{2}",
  "domain": "{3}"
}}
</script>
""".format(original_url, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), urlparse(original_url).scheme, urlparse(original_url).netloc)
        
        # 元のURLをコメントとして記録
        base_href = f'<!-- 元のURL: {original_url} -->'
        
        # CSSリセットを追加（TikTokスクリプトがサイトのCSSに影響しないようにする）
        css_reset = '''
<!-- TikTok Pixel用スタイル分離 -->
<style id="tiktok-pixel-isolation">
/* TikTok Pixel用の隔離スタイル - メインページのスタイルに影響しないよう隔離 */
#tiktok-pixel-container,
iframe[title="TikTok Pixel"],
.ttq-loading, .ttq-confirm, .ttq-pixel-base, .ttq-transport-frame {
    all: initial !important;
    position: absolute !important;
    width: 1px !important;
    height: 1px !important;
    padding: 0 !important;
    margin: -1px !important;
    overflow: hidden !important;
    clip: rect(0, 0, 0, 0) !important;
    white-space: nowrap !important;
    border: 0 !important;
    display: none !important;
    opacity: 0 !important;
    visibility: hidden !important;
    pointer-events: none !important;
    z-index: -9999 !important;
}

/* 元のページのアニメーションとCSSを保護するためのリセット */
#css-debug-toolbar * {
    all: initial !important;
    box-sizing: border-box !important;
    font-family: sans-serif !important;
}

/* グローバルなCSSリセット防止 - 外部スクリプトがページ全体のスタイルをリセットすることを防ぐ */
html, body, div:not(#tiktok-pixel-container):not(#css-debug-toolbar), span, applet, object, iframe:not([title="TikTok Pixel"]),
h1, h2, h3, h4, h5, h6, p, blockquote, pre, a, abbr, acronym, address, big, cite, code,
del, dfn, em, img, ins, kbd, q, s, samp, small, strike, strong, sub, sup, tt, var,
b, u, i, center, dl, dt, dd, ol, ul, li, fieldset, form, label, legend,
table, caption, tbody, tfoot, thead, tr, th, td, article, aside, canvas, details,
embed, figure, figcaption, footer, header, hgroup, menu, nav, output, ruby,
section, summary, time, mark, audio, video {
    animation-play-state: running !important;
    transition: all 0.3s ease !important;
}

/* アニメーション保護 */
@keyframes preserve-animations {
    from { opacity: 1; }
    to { opacity: 1; }
}
</style>
'''
        
        # <head>タグを探してその終了直前にメタデータとピクセルスクリプトを挿入
        head_end_pos = html_content.find("</head>")
        if head_end_pos > 0:
            new_html = html_content[:head_end_pos] + base_href + metadata_script + css_reset + pixel_script + html_content[head_end_pos:]
        else:
            # headタグが見つからない場合の処理
            head_start_pos = html_content.find("<head")
            if head_start_pos > 0:
                head_start_end = html_content.find(">", head_start_pos)
                if head_start_end > 0:
                    insert_pos = head_start_end + 1
                    # CSSリセットを追加
                    new_html = html_content[:insert_pos] + "<head>" + f'<!-- 元のURL: {original_url} -->' + metadata_script + css_reset + pixel_script + "</head>" + html_content[insert_pos:]
                else:
                    # HTMLタグを探してその直後に挿入
                    html_pos = html_content.find("<html")
                    if html_pos > 0:
                        html_end = html_content.find(">", html_pos)
                        if html_end > 0:
                            insert_pos = html_end + 1
                            # CSSリセットを追加
                            new_html = html_content[:insert_pos] + "<head>" + f'<!-- 元のURL: {original_url} -->' + metadata_script + css_reset + pixel_script + "</head>" + html_content[insert_pos:]
                        else:
                            # HTMLタグもない場合は先頭に挿入
                            # CSSリセットを追加
                            new_html = "<!DOCTYPE html><html><head>" + f'<!-- 元のURL: {original_url} -->' + metadata_script + css_reset + pixel_script + "</head>" + html_content
                    else:
                        # HTMLタグもない場合は先頭に挿入
                        # CSSリセットを追加
                        new_html = "<!DOCTYPE html><html><head>" + f'<!-- 元のURL: {original_url} -->' + metadata_script + css_reset + pixel_script + "</head>" + html_content
            else:
                # headタグがない場合は先頭に挿入
                # CSSリセットを追加
                new_html = "<!DOCTYPE html><html><head>" + f'<!-- 元のURL: {original_url} -->' + metadata_script + css_reset + pixel_script + "</head>" + html_content
        
        # 修正したHTMLを保存する前に相対パスを絶対パスに変換
        new_html = fix_relative_paths(new_html, base_domain, original_url)
        
        # 一意のファイル名を生成
        file_id = str(uuid.uuid4())
        file_name = f"{file_id}.html"
        file_path = os.path.join(URLS_DIR, file_name)
        
        # メタタグでUTF-8設定されていることを確認（文字化け予防）
        if '<meta charset="UTF-8">' not in new_html:
            meta_pos = new_html.find('<head>') + 6 if '<head>' in new_html else 0
            if meta_pos > 0:
                new_html = new_html[:meta_pos] + '\n<meta charset="UTF-8">\n' + new_html[meta_pos:]
            else:
                # headがない場合は先頭に追加
                new_html = '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="UTF-8">\n</head>\n<body>\n' + new_html + '\n</body>\n</html>'
        
        # UTF-8で明示的に保存
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
        
        # pixelコードの有無を確認して、適切な情報を保存
        url_entry = {
            'id': file_id,
            'original_url': original_url,
            'new_url': new_url,
            'full_url': full_url,
            'pixel_id': 'カスタムコード',
            'custom_code': True,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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

@app.route('/view/<file_id>')
def view(file_id):
    try:
        # 設定情報を取得
        config = get_config()
        
        # クリック計測用に現在時刻をユニークIDで生成する
        # これにより、新しいセッションでのキャッシュ問題を回避する
        current_time = int(time.time())
        unique_id = f"{file_id}_{current_time}"
        
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
        
        # ファイルパスを取得
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id + '.html')
        
        if not os.path.exists(file_path):
            app.logger.error(f"File not found: {file_path}")
            return render_template('error.html', error="ファイルが見つかりません"), 404
        
        # バイナリモードでファイルを読み込む
        with open(file_path, 'rb') as f:
            raw_content = f.read()
        
        # まずUTF-8で試す
        try:
            html_content = raw_content.decode('utf-8')
        except UnicodeDecodeError:
            # UTF-8での読み込みに失敗した場合、他のエンコーディングを試す
            html_content = None
            encodings = ['shift_jis', 'euc-jp', 'cp932', 'iso-2022-jp']
            
            for encoding in encodings:
                try:
                    html_content = raw_content.decode(encoding)
                    app.logger.info(f"エンコーディング自動検出: {encoding}で正常にデコードしました")
                    break
                except UnicodeDecodeError:
                    continue
            
            # どのエンコーディングでも読み込めなかった場合は、エラー回避のためUTF-8でデコード
            if html_content is None:
                html_content = raw_content.decode('utf-8', errors='replace')
                app.logger.warning("すべてのエンコーディングでデコードに失敗しました。UTF-8で強制的にデコードします。")
        
        # 文字化け検出と修正
        if '繝' in html_content or '縺' in html_content:
            app.logger.info(f"文字化けを検出: {file_id}")
            
            # 文字化けが疑われる文字の置換マッピング
            mojibake_patterns = [
                ('繝', ''), ('縺', ''), ('菴', ''), ('・', '')
            ]
            
            # 可能性のあるエンコーディングを試す
            encodings = ['shift_jis', 'euc-jp', 'cp932', 'iso-2022-jp']
            for encoding in encodings:
                try:
                    # 一度encodeしてからdecodeしてみる
                    test_content = html_content.encode('utf-8', errors='ignore').decode(encoding, errors='ignore')
                    # 文字化けの特徴がなければ正しいエンコーディングと判断
                    if '繝' not in test_content and '縺' not in test_content:
                        html_content = test_content
                        app.logger.info(f"文字化けを{encoding}で修正しました")
                        break
                except Exception:
                    continue
            
            # バイナリレベルでの再変換を試みる
            if '繝' in html_content or '縺' in html_content:
                for encoding in encodings:
                    try:
                        # バイナリデータからの直接変換
                        decoded = raw_content.decode(encoding, errors='ignore')
                        if '繝' not in decoded and '縺' not in decoded:
                            html_content = decoded
                            app.logger.info(f"バイナリから{encoding}で再デコードして文字化けを修正しました")
                            break
                    except Exception:
                        continue
            
            # 一般的な文字化けパターンの修正も試みる
            if '繝' in html_content or '縺' in html_content:
                app.logger.info("パターン置換による文字化け修正を試みます")
                fixed_content = html_content
                # SHIFT-JISの不正なUTF-8エンコーディングが原因の文字化け対応
                common_mojibake = {
                    # 日本語の一般的な文字化けパターン
                    '繧ｫ繧ｿ繧ｫ繝': 'カタカナ',
                    '鬮倥＆': '高さ',
                    '讌ｽ螢ｫ': '楽天',
                    '髮ｻ蟄': '電子',
                    '繝｡繝ｼ繧ｫ繝ｼ': 'メーカー'
                }
                
                for mojibake, correct in common_mojibake.items():
                    if mojibake in fixed_content:
                        fixed_content = fixed_content.replace(mojibake, correct)
                
                # パターン修正で文字化けが減った場合は採用
                if fixed_content.count('繝') < html_content.count('繝'):
                    html_content = fixed_content
                    app.logger.info("パターン置換で文字化けを部分的に修正しました")
        
        # オリジナルURLを取得
        original_url = target_url.get('original_url', '')
        if original_url:
            parsed_url = urlparse(original_url)
            base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
            
            # 相対パスを絶対パスに変換
            html_content = fix_relative_paths(html_content, base_domain, original_url)
        
        # メタタグでのエンコーディング指定を確認
        if '<meta charset=' not in html_content.lower():
            # charset指定がない場合は追加
            head_end_pos = html_content.find('</head>')
            if head_end_pos > 0:
                html_content = html_content[:head_end_pos] + '<meta charset="UTF-8">\n' + html_content[head_end_pos:]
            else:
                # headタグが見つからない場合は先頭に追加
                html_content = '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="UTF-8">\n</head>\n<body>\n' + html_content + '\n</body>\n</html>'
        else:
            # 既存のcharset指定をUTF-8に統一
            charset_pattern = re.compile(r'<meta[^>]*charset=[\'"](.*?)[\'"][^>]*>', re.IGNORECASE)
            html_content = charset_pattern.sub('<meta charset="UTF-8">', html_content)
        
        # CSSデバッグ用のスクリプトを追加
        css_debug_script = """
<script>
// CSS診断ツール - デバッグモード
(function() {
    function addCssDebugger() {
        try {
            // CSS問題診断用のツールバーを作成
            var debugBar = document.createElement('div');
            debugBar.id = 'css-debug-toolbar';
            debugBar.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:rgba(0,0,0,0.8);color:#fff;z-index:999999;padding:8px;font-family:sans-serif;font-size:12px;text-align:left;box-shadow:0 -2px 10px rgba(0,0,0,0.3);';
            
            // ボタンと情報表示エリアを追加
            debugBar.innerHTML = `
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <button id="debug-toggle-animations" style="margin-right:10px;padding:3px 8px;background:#f44336;border:none;color:#fff;border-radius:3px;cursor:pointer;">アニメーション停止</button>
                        <button id="debug-highlight-css" style="margin-right:10px;padding:3px 8px;background:#2196F3;border:none;color:#fff;border-radius:3px;cursor:pointer;">CSSハイライト</button>
                        <button id="debug-show-styles" style="padding:3px 8px;background:#4CAF50;border:none;color:#fff;border-radius:3px;cursor:pointer;">スタイル検査</button>
                    </div>
                    <div id="debug-info" style="color:#fff;font-family:monospace;">デバッグモード</div>
                    <button id="debug-close" style="background:none;border:none;color:#fff;font-size:16px;cursor:pointer;">×</button>
                </div>
                <div id="debug-output" style="margin-top:8px;max-height:200px;overflow:auto;display:none;background:rgba(0,0,0,0.5);padding:5px;border-radius:3px;"></div>
            `;
            
            // ボディの最後に追加
            document.body.appendChild(debugBar);
            
            // ボタンのイベントリスナーを設定
            document.getElementById('debug-toggle-animations').addEventListener('click', function() {
                var btn = this;
                var allAnimations = [];
                
                // すべてのスタイルシートのアニメーションルールを取得
                for (var i = 0; i < document.styleSheets.length; i++) {
                    try {
                        var sheet = document.styleSheets[i];
                        var rules = sheet.cssRules || sheet.rules;
                        if (!rules) continue;
                        
                        for (var j = 0; j < rules.length; j++) {
                            var rule = rules[j];
                            // CSSアニメーションを検出
                            if (rule.type === CSSRule.KEYFRAMES_RULE || 
                                rule.cssText && rule.cssText.indexOf('@keyframes') >= 0) {
                                allAnimations.push({
                                    name: rule.name,
                                    cssText: rule.cssText
                                });
                            }
                        }
                    } catch(e) {
                        console.error('CSSルール読み取りエラー:', e);
                    }
                }
                
                // アニメーションの停止と再開を切り替え
                var style = document.createElement('style');
                if (btn.textContent === 'アニメーション停止') {
                    style.textContent = '*, *::before, *::after { animation-play-state: paused !important; transition: none !important; }';
                    document.head.appendChild(style);
                    style.id = 'debug-animation-pause';
                    btn.textContent = 'アニメーション再開';
                    btn.style.background = '#4CAF50';
                    
                    // アニメーションリストを表示
                    var output = document.getElementById('debug-output');
                    output.style.display = 'block';
                    output.innerHTML = '<h4>検出されたアニメーション:</h4><ul style="margin:0;padding-left:20px;">' + 
                        allAnimations.map(function(anim) {
                            return '<li style="margin-bottom:5px;">' + anim.name + '</li>';
                        }).join('') + '</ul>';
                    
                    document.getElementById('debug-info').textContent = 'アニメーション一時停止中 (' + allAnimations.length + '個検出)';
                } else {
                    var pauseStyle = document.getElementById('debug-animation-pause');
                    if (pauseStyle) pauseStyle.remove();
                    btn.textContent = 'アニメーション停止';
                    btn.style.background = '#f44336';
                    document.getElementById('debug-info').textContent = 'アニメーション実行中';
                    document.getElementById('debug-output').style.display = 'none';
                }
            });
            
            document.getElementById('debug-highlight-css').addEventListener('click', function() {
                var btn = this;
                var highlightStyle = document.getElementById('debug-highlight-style');
                
                if (!highlightStyle) {
                    // 各要素に枠線を付けて表示
                    highlightStyle = document.createElement('style');
                    highlightStyle.id = 'debug-highlight-style';
                    highlightStyle.textContent = `
                        * { outline: 1px solid rgba(255,0,0,0.2) !important; }
                        *:hover { outline: 2px solid rgba(255,0,0,0.8) !important; background: rgba(255,0,0,0.1) !important; }
                    `;
                    document.head.appendChild(highlightStyle);
                    btn.textContent = 'ハイライト解除';
                    btn.style.background = '#ff9800';
                    document.getElementById('debug-info').textContent = 'CSS要素ハイライト表示中';
                } else {
                    highlightStyle.remove();
                    btn.textContent = 'CSSハイライト';
                    btn.style.background = '#2196F3';
                    document.getElementById('debug-info').textContent = 'デバッグモード';
                }
            });
            
            document.getElementById('debug-show-styles').addEventListener('click', function() {
                var output = document.getElementById('debug-output');
                output.style.display = output.style.display === 'none' ? 'block' : 'none';
                
                if (output.style.display === 'block') {
                    // 外部スタイルシートの情報を収集
                    var styleInfo = [];
                    for (var i = 0; i < document.styleSheets.length; i++) {
                        try {
                            var sheet = document.styleSheets[i];
                            styleInfo.push({
                                href: sheet.href || 'インライン',
                                rules: sheet.cssRules ? sheet.cssRules.length : 0,
                                disabled: sheet.disabled
                            });
                        } catch(e) {
                            styleInfo.push({
                                href: sheet.href || 'インラインCORS制限',
                                error: e.message,
                                disabled: sheet.disabled
                            });
                        }
                    }
                    
                    output.innerHTML = '<h4>CSS情報:</h4><ul style="margin:0;padding-left:20px;">' + 
                        styleInfo.map(function(info) {
                            var status = info.disabled ? '無効' : '有効';
                            var error = info.error ? ' - エラー: ' + info.error : '';
                            var rules = info.rules !== undefined ? ' (' + info.rules + 'ルール)' : '';
                            return '<li style="margin-bottom:5px;">' + info.href + rules + ' - ' + status + error + '</li>';
                        }).join('') + '</ul>';
                }
            });
            
            // 閉じるボタン
            document.getElementById('debug-close').addEventListener('click', function() {
                var debugBar = document.getElementById('css-debug-toolbar');
                if (debugBar) debugBar.remove();
                
                // 追加したスタイルも削除
                var pauseStyle = document.getElementById('debug-animation-pause');
                if (pauseStyle) pauseStyle.remove();
                
                var highlightStyle = document.getElementById('debug-highlight-style');
                if (highlightStyle) highlightStyle.remove();
            });
            
            console.log('CSS診断ツール初期化完了');
        } catch(err) {
            console.error('CSS診断ツール初期化エラー:', err);
        }
    }

    // DOMの準備ができたらデバッガーを追加
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        setTimeout(addCssDebugger, 1000);
    } else {
        document.addEventListener('DOMContentLoaded', function() {
            setTimeout(addCssDebugger, 1000);
        });
    }
})();
</script>
        """
        
        # TikTokピクセル用のスクリプト
        tiktok_pixel_script = f"""
<script>
(function() {{
    // TikTokピクセルコードの実行を行う関数
    function injectTikTokPixel() {{
        try {{
            // インラインでスクリプトタグを作成して挿入
            var script = document.createElement('script');
            script.innerHTML = `
                !function (w, d, t) {{
                    w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"],ttq.setAndDefer=function(t,e){{t[e]=function(){{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}};}};for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);ttq.instance=function(t){{for(var e=ttq._i[t]||[],n=0;n<ttq.methods.length;n++)ttq.setAndDefer(e,ttq.methods[n]);return e}};ttq.load=function(e,n){{var i="https://analytics.tiktok.com/i18n/pixel/events.js";ttq._i=ttq._i||{{}},ttq._i[e]=[],ttq._i[e]._u=i,ttq._t=ttq._t||{{}},ttq._t[e]=+new Date,ttq._o=ttq._o||{{}},ttq._o[e]=n||{{}};var o=document.createElement("script");o.type="text/javascript",o.async=!0,o.src=i+"?sdkid="+e+"&lib="+t;var a=document.getElementsByTagName("script")[0];a.parentNode.insertBefore(o,a)}};
                    ttq.load('CM0EQKBC77U7DDDCEF4G');
                    ttq.page();
                }}(window, document, 'ttq');
            `;
            document.head.appendChild(script);
            console.log('[TikTok]: ピクセル設定完了');
        }} catch(err) {{
            // エラーを非表示にして、メインページに影響を与えないようにする
            console.error('[TikTok]: Pixel設定エラー', err);
        }}
    }}

    // ページの読み込みステータスに応じた実行タイミング制御
    if (document.readyState === 'complete') {{
        // すでにページが読み込み完了している場合
        setTimeout(injectTikTokPixel, 2000);
    }} else {{
        // ページ読み込み完了後に実行
        window.addEventListener('load', function() {{
            setTimeout(injectTikTokPixel, 2000);
        }});
    }}
}})();
</script>
        """
        
        # YouTubeの場合は表示をそのまま返す
        if target_url.get('youtube'):
            return html_content
        
        # TikTokピクセルコードをheadタグに追加
        head_end_pos = html_content.find('</head>')
        if head_end_pos != -1:
            html_content = html_content[:head_end_pos] + tiktok_pixel_script + css_debug_script + html_content[head_end_pos:]
        else:
            # headタグがない場合はbodyタグの直後に挿入
            body_start_pos = html_content.find('<body')
            if body_start_pos != -1:
                body_end_bracket = html_content.find('>', body_start_pos)
                if body_end_bracket != -1:
                    html_content = html_content[:body_end_bracket + 1] + tiktok_pixel_script + css_debug_script + html_content[body_end_bracket + 1:]
            else:
                # どちらもない場合はHTMLの先頭に追加
                html_content = tiktok_pixel_script + css_debug_script + html_content
        
        # クリック数を更新
        update_click_count(file_id)
        
        # レスポンスを返す
        response = make_response(html_content)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        return response
    except Exception as e:
        app.logger.error(f"ファイル読み込みエラー: {str(e)}")
        return render_template('error.html', error="ファイルの読み込み中にエラーが発生しました"), 500

@app.route('/delete/<file_id>', methods=['POST'])
def delete(file_id):
    file_path = os.path.join(URLS_DIR, f"{file_id}.html")
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # キャッシュから削除
        # キャッシュキーを直接指定して削除
        cache.delete(f'view/{file_id}')
        
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

# クリック数を更新する関数
def update_click_count(file_id):
    """URLのクリック数を更新する"""
    try:
        url_list = get_url_list()
        for url in url_list:
            if url.get('id') == file_id:
                # クリック数がない場合は初期化
                if 'clicks' not in url:
                    url['clicks'] = 0
                # クリック数を増加
                url['clicks'] += 1
                # 最終アクセス日時を更新
                url['last_accessed'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                save_url_list(url_list)
                break
    except Exception as e:
        app.logger.error(f"クリック数更新エラー: {str(e)}")

# 設定を取得する関数
def get_config():
    """アプリケーション設定を取得する"""
    # 将来的に設定ファイルから読み込むなどの対応が可能
    return {
        'site_name': 'URL変換サービス',
        'default_ttl': 86400 * 30,  # 30日
        'max_urls': 100
    }

if __name__ == '__main__':
    app.run(debug=True) 