"""レビューHTMLの解説データ。AI/人間が書くのはこのファイルだけ。

    python3 <skill>/assets/gen_review.py notes.py <base>...<head> out.html

書くときの規律（生成時の検証がこれを前提にしている）:
  - 数字は実測値を使う。導けない数字は verified_numbers に根拠ごと登録する
  - メソッド名は必ず foo() の形で書く（括弧なしだと検証が識別子を拾えない）
  - 差分に現れないコードの構造を断定しない（検証できず、誤りが残る）
  - 「差分を読めば分かること」は書かない。なぜそう書いたか、何を見落としやすいかを書く
"""

# =============================================================================
# グループ定義 — (id, ラベル, 説明, 既定で開くか, 判定関数)
#   上から順に評価し、最初に当たったグループへ。最後は必ず全捕捉にする。
#   言語別の例:
#     Go         : ("impl", "実装", "...", True,  lambda p: p.endswith(".go") and "_test" not in p)
#                  ("test", "テスト", "...", False, lambda p: p.endswith("_test.go"))
#     TypeScript : ("impl", "実装", "...", True,  lambda p: p.startswith("src/"))
#                  ("test", "テスト", "...", False, lambda p: ".test." in p or ".spec." in p)
#     Rails      : ("impl", "実装", "...", True,  lambda p: p.startswith("app/"))
#                  ("test", "テスト", "...", False, lambda p: p.startswith(("spec/", "test/")))
# =============================================================================
GROUPS = [
    ("core", "本題", "この変更の中心。ここだけで趣旨が分かる", True,
     lambda p: False),                                  # ← 対象を書く
    ("impl", "実装", "本題を成立させるための処理", True,
     lambda p: p.startswith("src/")),                   # ← 対象を書く
    ("test", "テスト", "担保内容は各行を参照", False,
     lambda p: "test" in p or "spec" in p),
    ("other", "その他", "設定・ドキュメント等", False,
     lambda p: True),                                   # ← 最後は全捕捉
]

NOTES = {
    "title": "<機能名> — コードレビュー用サマリ",
    "subtitle": "<チケットID> / PR #<番号>",
    "pr_url": "",          # 空なら GitHub リンクを出さない

    # 2〜3文。何を直したか、変更の本体はどこか
    "summary": [
        "",
    ],

    # レビュアーが10分で掴むべきこと。3〜6個。level は core / warn / note
    #   core = 本題、warn = 判断や注意が要る点、note = 経緯や補足
    # 「このPRで壊れうるもの」を1つ入れると、レビューが読み合わせから判断に変わる
    "highlights": [
        {"level": "warn", "title": "", "body": ""},
    ],

    # 修正前後を1行で対比できるなら書く（無ければキーごと省略可）
    "before_after": {
        "before": {"sample": "", "note": ""},
        "after": {"sample": "", "note": ""},
    },

    # リリース後に確認すること（無ければ省略可）
    "verify_steps": [],

    # 実測値から導けない数字は根拠ごとここに。未登録の数字は検証で警告される
    "verified_numbers": {
        # "56": "対象メソッドが保存点より前に代入するカラム数を実測",
    },

    "groups": GROUPS,

    # ファイルごとの解説。
    #   文字列        → 展開後にだけ出る
    #   {"guarantee"} → 折りたたんだままでも読める1行（テストの担保内容に使う）
    #   {"note"}      → 展開後の詳細
    "file_notes": {
        # "src/foo.ts": "なぜこう書いたか。既存構造との関係。見落としやすい点",
        # "src/foo.test.ts": {
        #     "guarantee": "担保: <この変更で何が守られるか>",
        #     "note": "補足（読み飛ばしてよい詳細）",
        # },
    },
}

# 変更の因果が図で伝わるときだけ書く。空なら図のセクションごと出ない。
# 座標ハードコードなので、要素を増やすと箱からテキストがはみ出しやすい。
# 雛形は references/diagram-patterns.md を見ること。
DIAGRAM_SVG = ""
