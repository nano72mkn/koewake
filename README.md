# こえわけ / Koewake

動画を渡すと、日本語の字幕ファイル（**SRT**）が出てくるツール。
出てきた SRT を **Filmora** に読み込ませれば、字幕を手打ちしなくて済みます。

- 対応OS: **Windows / macOS**
- 音声認識は **自分のPCの中だけ**で動きます（動画をどこかにアップロードしません／利用料もかかりません）
- 縦動画（ショート）と横動画のどちらもOK。折り返し幅を自動で変えます

> **Phase 1（今ここ）**: 動画 → SRT を1本つくる
> **Phase 2（次）**: 声で話者を判別して、話者ごとに別々のSRTを出す

---

## つかいかた

### 1. 最初の1回だけ：セットアップ

| OS | やること |
|---|---|
| Windows | `scripts\setup-windows.bat` をダブルクリック |
| macOS | `scripts/setup-macos.command` をダブルクリック |

必要なものを自動でダウンロードします。終わるまで待ってください（10分ほど）。

### 2. 毎回：動画をドロップする

| OS | やること |
|---|---|
| Windows | 動画ファイルを `scripts\koewake.bat` の上にドラッグ＆ドロップ |
| macOS | `scripts/koewake.command` を開いて、動画ファイルをウィンドウにドラッグ → Enter |

動画と同じフォルダに `動画名.srt` ができます。複数まとめてドロップしてもOKです。

処理中は、いま何をしていて何％進んだかが1行で出ます。

```
  ⠹ モデルをダウンロード中  ██████░░░░░░░░░░  36.1%  経過 3秒 / 残り 約6秒
  ⠸ 文字起こし中          █████████░░░░░░░  58.5%  経過 16秒 / 残り 約11秒  よし行くぞ…
```

> 動きが止まって見えても、左端のマークが回っていれば動いています。
> 文字起こしの％は、ひとかたまり認識するたびに進むので、少し飛び飛びになります。

> macOS の Finder はシェルスクリプトへのドロップを受け付けないので、
> `koewake.command` を開いてから Terminal のウィンドウに動画をドラッグする形になります。

> 初回だけ、文字起こしのモデル（約1.5GB）をダウンロードするので時間がかかります。2回目からは速いです。

### 3. Filmora に読み込む

Filmora のタイムラインに `.srt` をドラッグするか、
`ファイル` → `メディアをインポート` から SRT を選びます。

---

## 精度を上げるコツ

ゲーム名・キャラ名・配信者名など、**間違えられて困る単語**をテキストファイルに書いておくと拾いやすくなります。

`単語リスト.txt`（1行に1語、`#` から後ろはメモ）

```
こえわけ
モンスターハンター
ヴァルハザク   # 敵の名前
```

`scripts/` フォルダに `単語リスト.txt` という名前で置いておけば、自動で読み込みます。
`scripts/単語リスト.example.txt` をコピーして使ってください。

---

## コマンドで使う（開発者向け）

```bash
uv sync
uv run koewake 動画.mp4

# よく使うオプション
uv run koewake 動画.mp4 -o out/                 # 出力先を指定
uv run koewake *.mp4 --quality accurate         # 精度優先（遅い）
uv run koewake 動画.mp4 --quality fast          # 速度優先（下書き用）
uv run koewake 動画.mp4 --vocab 単語リスト.txt   # 固有名詞を渡す
uv run koewake 動画.mp4 --layout vertical       # ショート用に折り返しを狭く
uv run koewake 動画.mp4 --keep-punctuation      # 句読点を残す
uv run koewake フォルダ/ --overwrite            # フォルダごとまとめて
```

| オプション | 既定 | 意味 |
|---|---|---|
| `--quality` | `balanced` | `fast` = small / `balanced` = large-v3-turbo / `accurate` = large-v3 |
| `--device` | `auto` | NVIDIA GPU があれば自動で使う。`cpu` / `cuda` で固定も可 |
| `--layout` | `auto` | 動画の縦横から折り返し幅を判定（横 20字×2行 / 縦 13字×2行） |
| `--encoding` | `utf-8-sig` | Filmora(Windows) で文字化けしにくい BOM 付き UTF-8 |
| `--save-transcript` | off | 文字起こしの生データを JSON で残す（Phase 2 の下地） |
| `--no-progress` | off | 進捗のアニメーションを出さない（ログに残すとき用） |

---

## 設計メモ

技術選定の理由と Phase 2 の計画は [docs/design.md](docs/design.md) にあります。

## 開発

```bash
uv sync --group dev
uv run pytest
```

```
src/koewake/
  cli.py         コマンドライン入口
  audio.py       ffmpeg で音声を抜く / 縦横判定
  progress.py    進捗表示（別スレッドで回すアニメーション）
  engine.py      GPU/CPU とモデルの自動選択
  transcribe.py  音声 -> テキスト（faster-whisper。差し替え可能な層）
  subtitle.py    テキスト -> 読める字幕 -> SRT
```
