// シンプルなリダイレクト関数
exports.handler = async function(event, context) {
  return {
    statusCode: 200,
    headers: {
      "Content-Type": "text/html"
    },
    body: `
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TikTok Pixel自動埋め込みツール</title>
    <style>
        body {
            font-family: 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            background: white;
            padding: 20px;
            border-radius: 5px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
        }
        h1 {
            color: #2c3e50;
            text-align: center;
            margin-bottom: 30px;
        }
        .card {
            background: white;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .card-header {
            background: #f8f9fa;
            padding: 10px;
            margin: -20px -20px 20px;
            border-bottom: 1px solid #ddd;
            border-radius: 4px 4px 0 0;
        }
        .btn {
            display: inline-block;
            background: #3498db;
            color: white;
            padding: 8px 16px;
            border-radius: 4px;
            text-decoration: none;
            border: none;
            cursor: pointer;
        }
        .btn:hover {
            background: #2980b9;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
        }
        input[type="text"],
        input[type="url"] {
            width: 100%;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 4px;
            box-sizing: border-box;
        }
        .alert {
            padding: 15px;
            margin-bottom: 20px;
            border: 1px solid transparent;
            border-radius: 4px;
        }
        .alert-info {
            background-color: #d9edf7;
            border-color: #bce8f1;
            color: #31708f;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>TikTok Pixel自動埋め込みツール</h1>
        
        <div class="alert alert-info">
            <p>このNetlifyサイトでは、サーバーレス関数の設定上の問題により、現在はデモ表示のみとなっています。</p>
            <p>完全な機能を利用するには、GitHub上のコードをローカルで実行するか、Vercelへのデプロイをお試しください。</p>
        </div>
        
        <div class="row">
            <div class="card">
                <div class="card-header">
                    <h2 style="margin: 0; font-size: 1.2em;">新しいURLを作成</h2>
                </div>
                <div class="card-body">
                    <form>
                        <div class="form-group">
                            <label for="original_url">オリジナルURL</label>
                            <input type="url" id="original_url" name="original_url" placeholder="https://example.com" required>
                            <div class="form-text">TikTok Pixelを埋め込みたいURLを入力してください</div>
                        </div>
                        <div class="form-group">
                            <label for="pixel_id">TikTok Pixel ID</label>
                            <input type="text" id="pixel_id" name="pixel_id" placeholder="ABCDEFGHIJK123456789" required>
                            <div class="form-text">TikTok広告管理画面で確認できるPixel IDを入力してください</div>
                        </div>
                        <button type="button" class="btn" onclick="alert('デモ表示のため、実際の処理は行われません')">URLを生成</button>
                    </form>
                </div>
            </div>
            
            <div class="card">
                <div class="card-header">
                    <h2 style="margin: 0; font-size: 1.2em;">生成されたURL一覧</h2>
                </div>
                <div class="card-body">
                    <p style="text-align: center;">デモ表示のため、URLは表示されていません。</p>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
    `
  };
}; 