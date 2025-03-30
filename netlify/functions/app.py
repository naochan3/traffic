from flask import Flask, render_template, request, redirect, url_for, flash
import os
import requests
from bs4 import BeautifulSoup
import uuid
import json
from datetime import datetime
from flask_serverless import FlaskServerless

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev_key_for_testing')

# URLsの保存先ディレクトリ
# Netlifyは読み書き可能なtmpディレクトリを提供
URLS_DIR = '/tmp/urls'
if not os.path.exists(URLS_DIR):
    os.makedirs(URLS_DIR)

# URLリストのJSONファイル
URL_LIST_FILE = os.path.join(URLS_DIR, 'url_list.json')
if not os.path.exists(URL_LIST_FILE):
    with open(URL_LIST_FILE, 'w') as f:
        json.dump([], f)

@app.route('/')
def index():
    # 保存されたURLリストを取得
    if not os.path.exists(URL_LIST_FILE):
        with open(URL_LIST_FILE, 'w') as f:
            json.dump([], f)
    
    with open(URL_LIST_FILE, 'r') as f:
        url_list = json.load(f)
    return render_template('index.html', url_list=url_list)

@app.route('/create', methods=['POST'])
def create():
    original_url = request.form.get('original_url')
    pixel_id = request.form.get('pixel_id')
    
    if not original_url or not pixel_id:
        flash('URLとPixel IDを入力してください。', 'error')
        return redirect(url_for('index'))
    
    try:
        # オリジナルURLからHTMLを取得
        response = requests.get(original_url)
        response.raise_for_status()
        
        # HTMLの解析
        soup = BeautifulSoup(response.text, 'html.parser')
        
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
        
        # headタグがない場合は作成
        if not soup.head:
            head = soup.new_tag('head')
            if soup.html:
                soup.html.insert(0, head)
            else:
                html = soup.new_tag('html')
                html.append(head)
                soup.append(html)
        
        # Pixelスクリプトを挿入
        new_script = BeautifulSoup(pixel_script, 'html.parser')
        soup.head.append(new_script)
        
        # 一意のファイル名を生成
        file_id = str(uuid.uuid4())
        file_name = f"{file_id}.html"
        file_path = os.path.join(URLS_DIR, file_name)
        
        # 修正したHTMLを保存
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(str(soup))
        
        # URLリストに追加
        if not os.path.exists(URL_LIST_FILE):
            with open(URL_LIST_FILE, 'w') as f:
                json.dump([], f)
                
        with open(URL_LIST_FILE, 'r') as f:
            url_list = json.load(f)
        
        # 本番環境のURLを取得
        if os.environ.get('URL'):
            base_url = os.environ.get('URL')
        else:
            base_url = request.host_url.rstrip('/')
            
        new_url = f"/view/{file_id}"
        full_url = f"{base_url}{new_url}"
        
        url_list.append({
            'id': file_id,
            'original_url': original_url,
            'pixel_id': pixel_id,
            'new_url': new_url,
            'full_url': full_url,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        
        with open(URL_LIST_FILE, 'w') as f:
            json.dump(url_list, f)
        
        flash('新しいURLが正常に作成されました！', 'success')
        return redirect(url_for('index'))
    
    except requests.exceptions.RequestException as e:
        flash(f'エラー: {str(e)}', 'error')
        return redirect(url_for('index'))
    except Exception as e:
        flash(f'エラー: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/view/<file_id>')
def view(file_id):
    file_path = os.path.join(URLS_DIR, f"{file_id}.html")
    if not os.path.exists(file_path):
        return "ファイルが見つかりません", 404
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    return content

@app.route('/delete/<file_id>', methods=['POST'])
def delete(file_id):
    file_path = os.path.join(URLS_DIR, f"{file_id}.html")
    if os.path.exists(file_path):
        os.remove(file_path)
    
    if os.path.exists(URL_LIST_FILE):
        with open(URL_LIST_FILE, 'r') as f:
            url_list = json.load(f)
        
        url_list = [url for url in url_list if url['id'] != file_id]
        
        with open(URL_LIST_FILE, 'w') as f:
            json.dump(url_list, f)
    
    flash('URLが正常に削除されました！', 'success')
    return redirect(url_for('index'))

# Netlify Functions用のハンドラー
handler = FlaskServerless(app) 