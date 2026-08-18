# 構造図の雛形

`notes.py` の `DIAGRAM_SVG` に入れる。**図が要らない案件では空文字にする**（セクションごと出ない）。

## 入れるかどうかの判断

入れる価値があるのは「**複数の変更がなぜ全部必要か**」を示すときだけ。変更が1箇所なら図は不要で、文章のほうが速い。

図は解説文全体の7割前後のトークンを食う。座標がハードコードで、AIは描画結果を見られないため、**要素を減らすほど破綻しにくい**。目安は箱10個・矢印8本まで。

## 使えるCSSクラス

機構側で定義済み。色はテーマ変数を参照するので、ライト/ダーク両方に自動追従する。

| クラス | 用途 |
|---|---|
| `.bx` | 箱（既定） |
| `.bx.chg` | 変更した箇所（緑） |
| `.bx.cf` | 既存インフラ・外部要因（青） |
| `.bx.prob` | 問題箇所（赤）※使う位置に注意（後述） |
| `.ln` | 線 |
| `.ln.chgln` | 変更に関わる線（緑） |
| `.mono` `.b` `.sm` `.xs` | 等幅 / 太字 / 小 / 極小 |
| `.mid` `.end` | text-anchor: middle / end |
| `.lane` | レーン見出し |
| `.acc` `.warnfg` `.probfg` | 強調色 / 警告色 / 問題色 |
| `.tag2` | ★マークつきの注記（緑） |

矢印は `marker-end="url(#ar)"`（灰）と `url(#ar2)`（緑）。

## 縦フロー型（最も使いやすい）

処理が段階を追って進み、途中で分岐・合流する変更に向く。

```
<svg viewBox="0 0 1000 700" role="img" aria-label="変更箇所の繋がり" class="diag">
  <defs>
    <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0,0 L10,5 L0,10 z" class="mk"/>
    </marker>
    <marker id="ar2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0,0 L10,5 L0,10 z" class="mk2"/>
    </marker>
  </defs>

  <text x="20" y="22" class="lane">① 入口</text>
  <rect x="20" y="34" width="300" height="58" rx="7" class="bx chg"/>
  <text x="34" y="56" class="mono b">ClassName</text>
  <text x="34" y="76" class="sm">何をするか</text>

  <path d="M170,92 L170,130" class="ln chgln" marker-end="url(#ar2)"/>
  <text x="182" y="116" class="tag2">★ 追加した処理</text>

  <text x="20" y="158" class="lane">② 次の段</text>
  <rect x="20" y="170" width="300" height="58" rx="7" class="bx"/>
  <text x="34" y="192" class="mono b">既存の仕組み</text>
</svg>
```

### 座標の決め方

- 箱の高さは 52〜98。テキスト2行なら 58、4行なら 92 を目安に
- 箱の中のテキストは上端 +22 から、行間 20（`.sm` なら 18、`.xs` なら 16）
- レーン見出しは箱の上端 −12
- 矢印は箱の下端から次の箱の上端まで
- **`viewBox` の高さは最下要素 +40**。足りないと凡例が切れる

### はみ出しを防ぐ

日本語は等幅でないため、**1行あたり全角20文字（`.sm` なら24、`.xs` なら28）を超えたら箱幅を広げるか改行する**。生成後に必ずブラウザで見る。

## 注意: 赤（`.bx.prob`）を置く位置

**問題の「原因」に置く。** 前提条件や、元からある制約に赤を置くと「そこを直すPRなのか」と誤読される。

たとえば「キーが固定なのでURLが変わらない」が元からの前提で、実際の原因が「毎回変わるクエリを付けていたこと」なら、赤は後者に置く。前者は `.warnfg` の文字色で「前提」と示すに留める。

## 注意: 矢印と実際の参照関係

**A → B の線を引く前に、A が実際に B を呼んでいるか確認する。** 図の説明文と線が矛盾していると、レビュアーが構造を誤解する。

```bash
grep -rn "TargetClass" path/to/source.php
```

複数の経路が同じ場所に集まるように見えて、実は片方が別ルートを通ることがある。その場合は線を分ける。

## 凡例

図の下に置く。使った色だけ載せる。

```
<rect x="20" y="640" width="14" height="14" rx="3" class="bx chg"/>
<text x="42" y="652" class="xs">今回変更した箇所</text>
<text x="180" y="652" class="xs tag2i">★ = 追加した処理</text>
<rect x="330" y="640" width="14" height="14" rx="3" class="bx cf"/>
<text x="352" y="652" class="xs">既存（変更なし）</text>
```
