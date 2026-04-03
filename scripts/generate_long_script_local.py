"""
長編動画 脚本生成スクリプト（ローカルLLM版）
Ollama を使い、セクションごとに分割生成することで 1万字規模の原稿を作る。

【チェックポイント保存】
  各セクション生成後に data/cache/longform/<page_id>.json へ逐次保存。
  途中クラッシュ・Ollama タイムアウトが発生しても、再実行時に
  生成済みセクションをスキップして再開できる。
  Notion 保存成功後にキャッシュファイルを自動削除。

通常版との違い:
  - backend を強制的に ollama に設定
  - 8セクションそれぞれを個別 LLM 呼び出しで生成（各 1200〜1500 字）
  - 合計目標: 10,000 字以上

使用方法:
  python scripts/generate_long_script_local.py           # 未生成の偉人を最大5件処理
  python scripts/generate_long_script_local.py --limit 3
  python scripts/generate_long_script_local.py --all     # 全偉人を再生成
  python scripts/generate_long_script_local.py --figure 平賀源内  # 特定偉人のみ
  python scripts/generate_long_script_local.py --clear-cache     # キャッシュを全削除して終了
"""

import argparse
import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
from notion_client import NotionFigureClient
from llm_client import LLMClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SEP = "=============================="
CACHE_DIR = ROOT / "data" / "cache" / "longform"
PARALLEL_SECTIONS = 4          # 並列生成数（Ollama の OLLAMA_NUM_PARALLEL と合わせる）
_cache_lock = threading.Lock() # キャッシュファイルへのスレッドセーフな書き込み用

# セクション定義: (見出しキー, ターゲット文字数, 生成指示)
SECTIONS = [
    (
        "【Hook】掴み",
        400,
        "冒頭の15秒で視聴者を釘付けにする「引き」を作ってください。\n"
        "【鉄則】謎を残して終わること。答えを言わないこと。\n"
        "・この人物の人生で最も「意外・衝撃・逆説的」な一側面をひとつだけ取り上げる\n"
        "・有名な業績・弟子の名前・具体的な数字・結末は一切書かない（それが後のセクションへの引きになる）\n"
        "・「なぜ？」「どうして？」と視聴者が続きを見たくなる問いで締めること\n"
        "・文字数は400字以内に収めること（長すぎるとネタバレになる）",
    ),
    (
        "【時代背景】",
        1200,
        "この人物が生きた時代の社会状況・政治・経済・文化の空気感を具体的に描写してください。"
        "当時の庶民の暮らし、権力構造、時代を動かしていた力学を分かりやすく伝えること。"
        "年号や具体的な事件・出来事を交えて立体的に描くこと。",
    ),
    (
        "【生い立ち】",
        1300,
        "幼少期・家庭環境・生まれた境遇を詳しく描いてください。"
        "どんな家族に囲まれ、どんな体験が後の人生を形作ったか。"
        "原体験となったエピソードを具体的に描写し、"
        "後のセクションへの伏線を張ること。",
    ),
    (
        "【転機】",
        1300,
        "人生を180度変えた出来事・出会い・決断を中心に描いてください。"
        "その前後で何がどう変わったかを対比させて伝えること。"
        "その決断がいかに勇気ある・無謀・奇跡的だったかを強調し、"
        "ドラマチックに語ること。",
    ),
    (
        "【最大業績】",
        1500,
        "歴史に名を刻んだ最大の功績・発見・作品・活動を詳しく描いてください。"
        "それがいかに当時の常識を覆すものだったか、"
        "どのくらいの規模・影響があったかを具体的な数字や比較で示すこと。"
        "後世への影響も含めて語ること。",
    ),
    (
        "【苦難と逆境】",
        1200,
        "このセクションでしか語られない「固有の苦難」だけを描いてください。\n"
        "【禁止】生い立ち・転機・最大業績のセクションで既出のエピソードを繰り返さないこと。\n"
        "以下のような、他セクションでは掘り下げられない苦難に焦点を当てること：\n"
        "・家族・弟子・同僚との人間関係の軋轢や葛藤\n"
        "・経済的苦境・資金難・生活の苦しさ\n"
        "・健康問題・病気・肉体的限界との戦い\n"
        "・社会や権力からの具体的な妨害・迫害・冷遇の場面\n"
        "・内面の迷い・挫折・自己否定の瞬間\n"
        "それをどう乗り越えたかのプロセスを感情豊かに描くこと。",
    ),
    (
        "【晩年と遺産】",
        1200,
        "晩年の様子（何をし、どんな境遇だったか）と、"
        "この人物が後世に残したもの（制度・作品・思想・影響）を描いてください。"
        "死後どのように再評価されたか、現代にどう受け継がれているかも含めること。",
    ),
    (
        "【現代へのメッセージ】",
        1000,
        "この人物の生き方から、今を生きる私たちへの教訓・メッセージを語ってください。\n"
        "【禁止】「学びを止めないこと」「他人を思いやること」のような汎用的な教訓は書かない。\n"
        "この人物だけに当てはまる、固有の生き方・選択・信念から導かれるメッセージにすること。\n"
        "現代の具体的なシーン・職業・課題に結びつけ、"
        "視聴者が「明日から何かを変えてみよう」と思えるような締め方にすること。",
    ),
]


HEADER_PROMPT = """あなたは日本の歴史偉人を紹介するYouTubeチャンネルのプロ脚本家です。
以下の偉人情報を読み、長編YouTube動画のメタ情報を生成してください。

【偉人情報】
{research_data}

【出力形式（プレーンテキストで、余計な説明は不要）】
タイトル: [動画タイトル（30文字以内）]

説明文: [視聴者を引き込む動画説明文（200文字以内）]

タグ: [カンマ区切りタグ 例: 偉人,日本史,歴史,江戸時代]
"""

BLUEPRINT_PROMPT = """あなたは日本の歴史偉人を紹介するYouTubeチャンネルのプロ脚本家です。
以下の偉人情報を読み、長編YouTube動画の「エピソード割り振り設計図」を作成してください。

【偉人情報】
{research_data}

【目的】
この設計図は、8つのセクションを別々のライターが並行執筆する際の指示書です。
同じエピソード・場面・人名が複数セクションに重複しないよう、各セクションの担当を具体的に割り振ってください。

【出力形式（プレーンテキスト）】
各セクションについて、以下の2点を書いてください：
  ・扱う内容: このセクションが担当するエピソード・事実・テーマ（具体的に）
  ・冒頭の入り方: このセクションの最初の一文のアプローチ（他セクションと被らないよう）

---
Hook:
  扱う内容: （謎かけのみ。業績・数字・有名人名・結末は一切ここに書かない）
  冒頭の入り方:

時代背景:
  扱う内容:
  冒頭の入り方:

生い立ち:
  扱う内容:
  冒頭の入り方:

転機:
  扱う内容:
  冒頭の入り方:

最大業績:
  扱う内容:
  冒頭の入り方:

苦難と逆境:
  扱う内容: （最大業績で扱ったエピソードは除く。人間関係・経済・健康・迫害など固有の苦労）
  冒頭の入り方:

晩年と遺産:
  扱う内容:
  冒頭の入り方:

現代へのメッセージ:
  扱う内容: （この人物固有の教訓のみ。汎用的な「学びを止めないこと」等は不可）
  冒頭の入り方:
---
"""

SECTION_PROMPT = """あなたは日本の歴史偉人を紹介するYouTubeチャンネルのプロ脚本家です。
以下の偉人情報を基に、長編YouTube動画の「{section_title}」セクションのナレーション原稿を書いてください。

【偉人情報】
{research_data}

【動画全体の構成設計図（全セクション共通）】
{blueprint}

上記の設計図で「{section_title}」に割り振られた内容のみを書いてください。
他のセクションに割り振られたエピソード・場面・人名は一切書かないこと。
冒頭の入り方も設計図の指示に従うこと。

【このセクションの執筆指示】
{instruction}

【要件】
- 目標文字数: {target_chars}字程度（Hook のみ400字以内厳守。他セクションは目安として達成すること）
- 視聴者: 歴史に興味ある20〜50代の日本人
- トーン: 親しみやすく情熱的。ドラマチックな語り口
- 具体的な年号・数字・人名・エピソードを豊富に盛り込む
- 改行・段落を使い、聴いて心地よいリズムにする
- セクション見出し（【{section_label}】など）は書かないこと。本文のみ出力すること

本文:"""


# ─────────────────────────────────────────
# チェックポイント管理
# ─────────────────────────────────────────

def _cache_path(page_id: str) -> Path:
    return CACHE_DIR / f"{page_id}.json"


def load_cache(page_id: str) -> dict:
    """キャッシュファイルを読み込む。なければ空の状態を返す"""
    path = _cache_path(page_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            done = list(data.get("sections", {}).keys())
            logger.info(f"  キャッシュ読み込み: {path.name} (完了済み: {done})")
            return data
        except Exception as e:
            logger.warning(f"  キャッシュ読み込み失敗（無視して最初から）: {e}")
    return {"blueprint": None, "header": None, "sections": {}}


def save_cache(page_id: str, name_ja: str, cache: dict):
    """現在の状態をキャッシュファイルに書き出す"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache["name_ja"] = name_ja
    _cache_path(page_id).write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def delete_cache(page_id: str):
    """Notion 保存成功後にキャッシュファイルを削除する"""
    path = _cache_path(page_id)
    if path.exists():
        path.unlink()
        logger.info(f"  キャッシュ削除: {path.name}")


def list_cache_files() -> list[Path]:
    if not CACHE_DIR.exists():
        return []
    return sorted(CACHE_DIR.glob("*.json"))


# ─────────────────────────────────────────
# 生成ロジック
# ─────────────────────────────────────────

def _clean_response(text: str) -> str:
    """
    推論モデルの <think>...</think> ブロックや
    モデルが吐く特殊トークンを除去して純粋な本文だけを返す。
    """
    import re
    # <think>...</think> ブロックを除去（複数行・複数ブロック対応）
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # <|endoftext|> などの特殊トークンを除去
    text = re.sub(r'<\|[^>]+\|>', '', text)
    # 先頭・末尾の空白・改行を整理
    return text.strip()


def generate_header(client: LLMClient, research_data: str) -> str:
    """メタ情報（タイトル・説明文・タグ）を生成"""
    prompt = HEADER_PROMPT.format(research_data=research_data)
    resp = client.create(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
        temperature=0.7,
    )
    logger.info(f"  ヘッダー生成: {resp.output_tokens} tokens")
    return _clean_response(resp.text)


def generate_blueprint(client: LLMClient, research_data: str) -> str:
    """エピソード割り振り設計図を生成（全セクションの担当・冒頭アプローチを決定）"""
    prompt = BLUEPRINT_PROMPT.format(research_data=research_data)
    resp = client.create(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
        temperature=0.6,
    )
    logger.info(f"  設計図生成: {resp.output_tokens} tokens")
    return _clean_response(resp.text)


def generate_section(
    client: LLMClient,
    research_data: str,
    blueprint: str,
    section_title: str,
    section_label: str,
    instruction: str,
    target_chars: int,
) -> str:
    """1セクション分のナレーション本文を生成"""
    prompt = SECTION_PROMPT.format(
        section_title=section_title,
        section_label=section_label,
        research_data=research_data,
        blueprint=blueprint,
        instruction=instruction,
        target_chars=target_chars,
    )
    resp = client.create(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.85,
    )
    cleaned = _clean_response(resp.text)
    logger.info(f"  {section_title}: {len(cleaned)}字 / {resp.output_tokens} tokens")
    return cleaned


def generate_full_script(client: LLMClient, figure: dict) -> str:
    """
    設計図→ヘッダー→8セクション並列 の順で生成して1つの原稿テキストに組み立てる。

    生成順:
      Step 1: 設計図（エピソード割り振り・冒頭アプローチ） ← NEW
      Step 2: ヘッダー（タイトル・説明文・タグ）
      Step 3: 8セクションを PARALLEL_SECTIONS 並列で生成（設計図を全セクションに渡す）

    セクションごとにキャッシュへ保存（ロック付き）し、途中再開に対応。
    """
    research_data = figure.get("research_data", "")
    name_ja = figure.get("name_ja", "この人物")
    page_id = figure["page_id"]

    if not research_data:
        raise ValueError(f"research_data が空: {name_ja}")

    cache = load_cache(page_id)
    total_steps = 2 + len(SECTIONS)  # 設計図 + ヘッダー + 8セクション

    # ── Step 1: 設計図（直列）──
    if not cache.get("blueprint"):
        logger.info(f"  [1/{total_steps}] 設計図（エピソード割り振り）生成中...")
        cache["blueprint"] = generate_blueprint(client, research_data)
        with _cache_lock:
            save_cache(page_id, name_ja, cache)
    else:
        logger.info(f"  [1/{total_steps}] 設計図: キャッシュ済みのためスキップ")

    blueprint = cache["blueprint"]

    # ── Step 2: ヘッダー（直列）──
    if cache["header"] is None:
        logger.info(f"  [2/{total_steps}] ヘッダー（タイトル・説明文・タグ）生成中...")
        cache["header"] = generate_header(client, research_data)
        with _cache_lock:
            save_cache(page_id, name_ja, cache)
    else:
        logger.info(f"  [2/{total_steps}] ヘッダー: キャッシュ済みのためスキップ")

    # ── キャッシュ済みセクションをログ表示 ──
    for idx, (title, _, _) in enumerate(SECTIONS, 3):
        if title in cache["sections"]:
            chars = len(cache["sections"][title])
            logger.info(f"  [{idx}/{total_steps}] {title}: キャッシュ済みのためスキップ ({chars}字)")

    # ── Step 3: 未生成セクションを並列生成（設計図を渡す）──
    pending = [
        (idx, title, tc, inst)
        for idx, (title, tc, inst) in enumerate(SECTIONS, 3)
        if title not in cache["sections"]
    ]

    if pending:
        logger.info(
            f"  {len(pending)} セクションを {PARALLEL_SECTIONS} 並列で生成開始..."
        )

        def _gen(args):
            idx, title, target_chars, instruction = args
            label = title.strip("【】").split("】")[0]
            text = generate_section(
                client, research_data, blueprint, title, label, instruction, target_chars
            )
            return idx, title, text

        errors = []
        with ThreadPoolExecutor(max_workers=PARALLEL_SECTIONS) as executor:
            futures = {executor.submit(_gen, args): args for args in pending}
            for future in as_completed(futures):
                _, title_arg, _, _ = futures[future]
                try:
                    idx, title, text = future.result()
                    with _cache_lock:
                        cache["sections"][title] = text
                        save_cache(page_id, name_ja, cache)
                    logger.info(f"  [{idx}/{total_steps}] {title}: 完了 ({len(text)}字)")
                except Exception as e:
                    logger.error(f"  セクション生成失敗: {title_arg}: {e}")
                    errors.append((title_arg, e))

        if errors:
            titles = [t for t, _ in errors]
            raise RuntimeError(f"セクション生成失敗: {titles}")

    # ── 組み立て（SECTIONS の定義順） ──
    missing = [t for t, _, _ in SECTIONS if t not in cache["sections"]]
    if missing:
        raise ValueError(f"未生成セクションあり: {missing}")

    parts = [cache["header"], "", SEP]
    for title, _, _ in SECTIONS:
        parts += ["", title, "", cache["sections"][title], "", SEP]

    full_text = "\n".join(parts)
    narration_chars = sum(len(cache["sections"][t]) for t, _, _ in SECTIONS)
    logger.info(f"  → 本文合計: {narration_chars}字 / 全体: {len(full_text)}字")
    return full_text


# ─────────────────────────────────────────
# エントリーポイント
# ─────────────────────────────────────────

def run(limit: int = 5, force_all: bool = False, figure_name: str = ""):
    notion = NotionFigureClient()
    client = LLMClient(backend="ollama")
    logger.info(f"LLMバックエンド: {client.backend}, モデル: {client.model}")
    logger.info(f"キャッシュ保存先: {CACHE_DIR}")

    notion.ensure_longform_properties()

    # ── 起動時: 前回セッションの未完了キャッシュを復元 ──
    cached_files = list_cache_files()
    cached_ids = {p.stem for p in cached_files}
    resume_figures = []
    if cached_ids and not figure_name:
        logger.info(f"前回セッションの未完了キャッシュ {len(cached_ids)} 件を検出 → 優先的に再開します")
        # キャッシュ済みの page_id を Notion から直接取得（limit に関係なく）
        all_pages = notion.query_figures()
        all_figures = [notion._page_to_figure(p) for p in all_pages]
        for fig in all_figures:
            if fig["page_id"] in cached_ids and fig.get("research_data"):
                cache_data = json.loads(_cache_path(fig["page_id"]).read_text(encoding="utf-8"))
                done = list(cache_data.get("sections", {}).keys())
                logger.info(f"  再開: {fig['name_ja']} ({len(done)}/8 セクション完了済み)")
                resume_figures.append(fig)

    if figure_name:
        pages = notion.query_figures()
        all_figures = [notion._page_to_figure(p) for p in pages]
        figures = [f for f in all_figures if f.get("name_ja") == figure_name]
        if not figures:
            logger.error(f"偉人が見つかりません: {figure_name}")
            return
    elif force_all:
        pages = notion.query_figures()
        figures = [notion._page_to_figure(p) for p in pages]
        figures = [f for f in figures if f.get("research_data")]
        total = len(figures)
        # 生成済み（long_script_ja あり）はスキップ（再生成したい場合は --force を別途追加）
        figures = [f for f in figures if not f.get("long_script_ja")]
        skipped = total - len(figures)
        logger.info(f"全偉人対象（research_data あり）: {total} 件 / 生成済みスキップ: {skipped} 件 / 未生成: {len(figures)} 件")
    else:
        figures = notion.get_figures_without_long_scripts(limit=limit)
        figures = [f for f in figures if f.get("research_data")]
        logger.info(f"未生成対象: {len(figures)} 件")

    # キャッシュ再開分を先頭にマージ（重複除去）
    existing_ids = {f["page_id"] for f in figures}
    prepend = [f for f in resume_figures if f["page_id"] not in existing_ids]
    figures = prepend + figures
    if prepend:
        logger.info(f"  ※ キャッシュ再開分 {len(prepend)} 件を先頭に追加")

    if not figures:
        logger.info("生成対象の偉人がいません。完了。")
        return

    success = 0
    for i, figure in enumerate(figures, 1):
        name_ja = figure.get("name_ja", "不明")
        page_id = figure["page_id"]
        logger.info(f"[{i}/{len(figures)}] 脚本生成開始: {name_ja} (page_id={page_id})")

        try:
            script_text = generate_full_script(client, figure)
            notion.save_long_script_ja(page_id=page_id, text=script_text)
            delete_cache(page_id)
            success += 1
            logger.info(f"  → 保存完了: {name_ja}")
        except Exception as e:
            logger.error(f"  → エラー: {name_ja}: {e}", exc_info=True)
            logger.info(f"  ※ キャッシュは保持されています。再実行で再開できます。")

        if i < len(figures):
            logger.info("次の偉人まで5秒待機...")
            time.sleep(5)

    logger.info(f"=== 完了: {success}/{len(figures)} 件の脚本を生成・保存 ===")

    # 残りキャッシュを報告
    remaining = list_cache_files()
    if remaining:
        logger.info(f"未完了キャッシュ {len(remaining)} 件 → {CACHE_DIR}")
        for p in remaining:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                done_sections = list(data.get("sections", {}).keys())
                logger.info(f"  {data.get('name_ja', p.stem)}: {len(done_sections)}/8 セクション完了")
            except Exception:
                logger.info(f"  {p.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ローカルLLM（Ollama）によるセクション分割長編脚本生成")
    parser.add_argument("--limit", type=int, default=5, help="最大処理件数（デフォルト: 5）")
    parser.add_argument("--all", dest="force_all", action="store_true",
                        help="全偉人を強制再生成（デフォルト: 未生成のみ）")
    parser.add_argument("--figure", type=str, default="",
                        help="特定の偉人名を指定して処理（例: --figure 平賀源内）")
    parser.add_argument("--clear-cache", action="store_true",
                        help="キャッシュを全削除して終了（生成は行わない）")
    args = parser.parse_args()

    if args.clear_cache:
        files = list_cache_files()
        if not files:
            print("キャッシュファイルはありません。")
        else:
            for p in files:
                p.unlink()
                print(f"削除: {p.name}")
            print(f"{len(files)} 件のキャッシュを削除しました。")
    else:
        run(limit=args.limit, force_all=args.force_all, figure_name=args.figure)
