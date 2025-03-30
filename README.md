# TikTok Pixelスクリプト自動埋め込みシステム

指定されたURLにTikTok Pixelスクリプトを自動で埋め込み、埋め込んだHTMLファイルを新しいURLとして公開できるシステムです。

## 機能

1. フォームからURLとPixel IDを入力
2. 提供されたTikTok Pixelスクリプトを対象ページの`<head>`タグに自動挿入
3. 埋め込まれたHTMLを新しいURLとしてサーバーに保存
4. 生成されたリンクを一覧で管理

## システム要件

- Python 3.8以上

## ローカルでのセットアップ

1. システムをダウンロード
```
cd shiratori
```

2. 仮想環境を作成して有効化（推奨）
```
# Windowsの場合
python -m venv venv
venv\Scripts\activate

# Mac/Linuxの場合
python3 -m venv venv
source venv/bin/activate
```

3. 依存パッケージをインストール
```
pip install -r requirements.txt
```

4. アプリケーションを実行
```
python app.py
```

5. ブラウザで `http://127.0.0.1:5000` にアクセス

## Vercelへのデプロイ手順

Vercelを使用すると無料で簡単にウェブアプリケーションをデプロイできます。

1. Vercelアカウントを作成
   - [Vercel](https://vercel.com/)にアクセスし、GitHubなどのアカウントでサインアップ

2. GitHubにプロジェクトをプッシュ
   ```
   git init
   git add .
   git commit -m "初期コミット"
   git remote add origin [あなたのGitHubリポジトリURL]
   git push -u origin main
   ```

3. Vercelでプロジェクトをインポート
   - Vercelダッシュボードで「New Project」をクリック
   - GitHubリポジトリをインポート
   - プロジェクト設定を確認（自動的にPythonプロジェクトとして認識されるはず）
   - 「Deploy」をクリック

4. デプロイ後の注意点
   - Vercelのサーバーレス環境では、一時ファイルしか保存できないため、URL履歴は定期的にリセットされます
   - より永続的なストレージが必要な場合は、MongoDBなどの外部データベースサービスとの連携を検討してください

## 使い方

1. トップページのフォームにTikTok Pixelを埋め込みたいURLを入力
2. TikTok Pixel IDを入力（TikTok広告管理画面で確認可能）
3. 「URLを生成」ボタンをクリック
4. 生成されたURLが一覧に表示される
5. 「表示」ボタンで生成されたページを確認、「コピー」ボタンでURLをクリップボードにコピー

## 注意事項

- 一部のWebサイトでは、クロスオリジンポリシーやコンテンツセキュリティポリシーにより、スクリプトの埋め込みや取得が制限される場合があります。
- 本ツールは教育目的での使用を想定しています。実際の利用は各Webサイトの利用規約に従ってください。
- Vercelの無料プランには以下の制限があります：
  - 帯域幅: 100GB/月
  - サーバーレス関数の実行時間: 10秒/リクエスト
  - 一時ストレージのみ使用可能
  - 商用利用に制限がある場合があります 