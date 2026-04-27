# escape-loop

AIが自律的にSouls風脱出ゲームをプレイするツールです。

罠にはまって死に、記憶を引き継ぎ、また挑む——を繰り返しながらAIが脱出を目指します。
シナリオはLLMがその場で生成するため、毎回異なる舞台でプレイできます。

## デモ

```
  呪われた図書館

Run 1/5  Step 4/30  手持ち: 焼け残った革表紙の本
▶ examine(mirror)
runner: 「鏡に何かが映っているかもしれない——覗いてみるのだ。」

YOU  DIED
鏡を覗き込んだ瞬間、暗闇の手がわたしを引き込んだ。

Run 2/5  Step 1/30
記憶: !! 大きな鏡（mirror）を直接調べると死ぬ
▶ examine(burned_book)
runner: 「今度は鏡には近づかない。本に手がかりがあるはずだ。」
```

## 必要なもの

- Python 3.11 以上
- OpenAI互換のAPIサーバー（OpenAI / vLLM / Ollama / LM Studio など）

## セットアップ

```bash
git clone https://github.com/Komawaraz/escape-loop
cd escape-loop

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# .env を編集してAPIキーとモデル名を設定する
```

## 使い方

```bash
# AIがランダムテーマでシナリオを生成してプレイ
python runner.py

# テーマを指定して生成
python runner.py --theme "廃病院の霊安室"

# 既存シナリオを使用
python runner.py --scenario scenarios/cursed_library.json

# キャラクター設定ファイルを指定する
python runner.py --character characters/ruina.json

# 放送・配信向け（ステップ間の待機秒数を伸ばす）
python runner.py --step-delay 7

# 1ランあたりの最大ターン数を変更
python runner.py --max-steps 20
```

## 対応LLMバックエンド

`.env` に以下を設定してください。

| バックエンド | OPENAI_BASE_URL | MODEL_NAME |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| vLLM | `http://localhost:8000/v1` | 使用するモデルID |
| Ollama | `http://localhost:11434/v1` | `llama3.1` など |
| LM Studio | `http://localhost:1234/v1` | 使用するモデルID |

## 仕組み

| ファイル | 役割 |
|---|---|
| `engine.py` | 純粋なゲームロジック。アクション処理・罠判定・状態管理 |
| `memory.py` | ラン間引き継ぎ記憶。死亡原因（danger_flags）と主観的振り返り（diary） |
| `scenario_gen.py` | LLMでシナリオJSONを生成。BFS検証をパスするまで最大3回リトライ |
| `scenario_validator.py` | 構造チェック + BFSで「本当に解けるか」を確認 |
| `runner.py` | メインループ。LLMにアクションを問い合わせ、タイプライター表示で結果を出力 |

## キャラクターの設定

`characters/` フォルダにJSONを置いて `--character` で指定します。

```json
{
  "name": "キャラクター名",
  "persona": "あなたは○○。どんな存在か、どんな状況か。",
  "speech_style": "一人称・語尾・禁則など話し方の設定。",
  "thinking_style": "探索や推理に対する姿勢・こだわり。"
}
```

`characters/ruina.json`（同梱）を参考にしてください。

## シナリオの作り方

独自シナリオはJSONで記述できます。`scenarios/cursed_library.json` が完全な例です。

```json
{
  "title": "タイトル",
  "intro": "場面説明",
  "max_runs": 5,
  "items": { ... },
  "locks": {
    "door_lock": { "key_required": "最終鍵のアイテムID" }
  },
  "traps": [
    {
      "trap_id": "罠ID",
      "trigger": { "action": "examine", "args": ["アイテムID"] },
      "severity": "death",
      "death_message": "死亡描写",
      "memory_hint": "次のランで引き継ぐヒント（答えは含めない）"
    }
  ],
  "code_limits": {
    "錠前ID": { "max_attempts": 3, "exhaust_trap": "罠ID" }
  }
}
```

## ライセンス

MIT
