#!/usr/bin/env python3
"""git diff から、人間がレビューするための解説つきHTML（1枚・外部依存なし）を生成する。

    python3 gen_review.py <notes.py> <base>...<head> <out.html>

設計:
  - 差分は git diff をパースして機械生成する（AIが差分を書き写さない = トークン節約）
  - 解説は notes.py の NOTES だけに書く。この機構ファイルは案件ごとに触らない
  - 出力は自己完結HTML 1枚。file:// で開け、Drive等にそのまま置ける
  - 生成時に解説とコードの整合を検証し、齟齬を警告する（後からHTMLは直せない前提）
"""
import html
import importlib.util
import json
import re
import subprocess
import sys

# =============================================================================
# 構造図（インラインSVG・外部依存なし）
# =============================================================================
DIAGRAM_CSS = """
.diag{width:100%;height:auto;max-width:1000px;margin:6px 0 4px;display:block}
.diag .bx{fill:var(--soft);stroke:var(--line);stroke-width:1.4}
.diag .bx.chg{fill:var(--core-bg);stroke:var(--core);stroke-width:1.8}
.diag .bx.prob{fill:var(--del-bg);stroke:#cf222e;stroke-width:1.6}
.diag .bx.cf{fill:var(--note-bg);stroke:var(--note);stroke-width:1.6}
.diag text{fill:var(--fg);font-size:13px;font-family:inherit}
.diag .mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.diag .b{font-weight:600}
.diag .sm{font-size:11.5px;fill:var(--muted)}
.diag .xs{font-size:10.5px;fill:var(--muted)}
.diag .mid{text-anchor:middle}
.diag .end{text-anchor:end}
.diag .lane{font-size:11px;fill:var(--muted);font-weight:600;letter-spacing:.04em}
.diag .acc{fill:var(--core);font-weight:600}
.diag .warnfg{fill:var(--warn)}
.diag .probfg{fill:#cf222e}
.diag .ln{fill:none;stroke:var(--muted);stroke-width:1.4}
.diag .chgln{stroke:var(--core);stroke-width:2}
.diag .mk{fill:var(--muted)}
.diag .mk2{fill:var(--core)}
.diag .tag2{font-size:11px;fill:var(--core);font-weight:600}
.diag .tag2i{font-size:10.5px;fill:var(--core);font-weight:600}
.ba{display:grid;grid-template-columns:1fr;gap:10px;margin:14px 0 4px}
@media(min-width:760px){.ba{grid-template-columns:1fr 1fr}}
.ba>div{border:1px solid var(--line);border-left-width:4px;border-radius:6px;padding:12px 14px;background:var(--soft)}
.ba .before{border-left-color:#cf222e}
.ba .after{border-left-color:var(--core)}
.ba h4{margin:0 0 8px;font-size:12.5px;letter-spacing:.03em}
.ba .u{font:11.5px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all;margin-bottom:6px}
.ba .r{font-size:12px;color:var(--muted);line-height:1.7}
"""


# =============================================================================
# git diff のパース（言語非依存）
# =============================================================================
def parse_diff(text):
    files = []
    cur = None
    hunk = None
    old_no = new_no = 0

    for line in text.split("\n"):
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            cur = {"path": m.group(2) if m else line, "status": "modified",
                   "additions": 0, "deletions": 0, "hunks": []}
            files.append(cur)
            hunk = None
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur["status"] = "added"
        elif line.startswith("deleted file mode"):
            cur["status"] = "deleted"
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$", line)
            if not m:
                continue
            old_no, new_no = int(m.group(1)), int(m.group(2))
            hunk = {"header": line[:line.rindex("@@") + 2], "context": m.group(3).strip(), "lines": []}
            cur["hunks"].append(hunk)
        elif hunk is not None:
            if line.startswith("+"):
                hunk["lines"].append(("add", None, new_no, line[1:]))
                new_no += 1
                cur["additions"] += 1
            elif line.startswith("-"):
                hunk["lines"].append(("del", old_no, None, line[1:]))
                old_no += 1
                cur["deletions"] += 1
            elif line.startswith("\\"):
                hunk["lines"].append(("meta", None, None, line[1:].strip()))
            else:
                hunk["lines"].append(("ctx", old_no, new_no, line[1:] if line else ""))
                old_no += 1
                new_no += 1
    return files


def group_of(path, groups):
    """groups は (id, ラベル, 説明, 既定で開くか, 判定関数) のタプル列。

    id を2箇所に書くと片方の書き換え忘れでファイルが無言で消えるため、
    判定関数もグループ定義に持たせて一元化している。
    どれにも当たらないファイルは最後のグループへ落とす（取りこぼし防止）。
    """
    for g in groups:
        if len(g) > 4 and g[4](path):
            return g[0]
    return groups[-1][0] if groups else "other"


# =============================================================================
# 検証 — 解説文を差分・リポジトリと照合する
#
# 使い切りの成果物なので、生成後にHTMLを手で直す運用は存在しない。
# 誤りは生成時点で捕まえる必要がある。
# =============================================================================
# 一般語・PHP組み込みなど、リポジトリに無くても正常なもの
IDENT_ALLOW = {
    "date", "now", "empty", "isset", "count", "implode", "explode", "sprintf",
    "file_get_contents", "file_put_contents", "unlink", "mkdir", "dirname",
    "str_replace", "substr", "strpos", "array_merge", "json_encode", "preg_match",
    "config", "view", "response", "abort", "app", "base_path", "storage_path",
    "public_path", "trigger_error", "grep", "curl",
}


# 「その識別子が存在しないこと」自体を述べている文脈。V3 の誤検出を防ぐ。
NEGATION_RE = re.compile(
    "|".join([
        "使わない", "使っていない", "使わず", "呼ばない", "呼ばず",
        "未定義", "存在しない", "無い", "ない\b", "なし",
        "できない", "検出できない", "消える", "失われ",
        "削除", "やめ", "代わりに", "ではなく", "以前は", "元は",
    ])
)


def _idents(text):
    """解説文から検証対象の識別子を抽出する。"""
    out = set()
    # Class::member
    out |= set(re.findall(r"\b([A-Z][A-Za-z0-9_]*::[A-Za-z_][A-Za-z0-9_]*)", text))
    # method() / function()
    out |= set(re.findall(r"\b([a-z_][a-zA-Z0-9_]{2,})\(\)", text))
    # CONSTANT_NAME
    out |= set(re.findall(r"\b([A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+)\b", text))
    # $variable
    out |= set(re.findall(r"(\$[a-z_][a-zA-Z0-9_]*)", text))
    return out


def _numbers(text):
    """解説文から「N行」「Nファイル」などの数量表記を抽出する。"""
    return set(re.findall(r"(\d+)\s*(?:行|ファイル|%|カラム|箇所|経路|件|種|個)", text))


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _read_code(path):
    """コメントを除いたファイル内容。

    解説に「touch() は使わない」と書くとコメントにも touch() が残るため、
    コメントを含めて検索すると「コードに存在する」と誤判定してしまう。
    言語非依存にするため // # * -- の行頭/行末コメントだけを簡易に落とす。
    """
    out = []
    for line in _read(path).split("\n"):
        s = line.strip()
        if s.startswith(("//", "#", "*", "/*", "*/", "--", "<!--")):
            continue
        s = re.sub(r"\s+//.*$", "", line)
        s = re.sub(r"\s+#(?!\[).*$", "", s)  # #[Attribute] は残す
        out.append(s)
    return "\n".join(out)


def verify(files, notes, stats):
    """警告のリストを返す。空なら整合が取れている。"""
    warns = []
    changed = {f["path"] for f in files}

    added = {}
    for f in files:
        added[f["path"]] = "\n".join(
            t for h in f["hunks"] for k, _, _, t in h["lines"] if k == "add"
        )

    # ---- V4: パスの実在 -----------------------------------------------------
    for path in notes["file_notes"]:
        if path not in changed:
            warns.append(("V4", f"file_notes のキーが差分に存在しない（解説が外れている）: {path}"))
    for f in files:
        if f["path"] not in notes["file_notes"]:
            warns.append(("V4", f"解説が無いファイル: {f['path']}"))

    # ---- V3: file_notes の識別子が、そのファイルの実体にあるか ---------------
    for path, note in notes["file_notes"].items():
        if path not in changed:
            continue
        note = note if isinstance(note, str) else " ".join(str(v) for v in note.values())
        body = _read_code(path)
        if not body:
            continue
        for ident in _idents(note):
            name = ident.split("::")[-1].rstrip("()").lstrip("$")
            if not name or name in IDENT_ALLOW or len(name) < 3:
                continue
            if re.search(r"\b" + re.escape(name) + r"\b", body):
                continue
            # 「使わない」「未定義」など、無いこと自体が主張の場合は誤りではない
            pos = note.find(ident)
            around = note[max(0, pos - 40):pos + len(ident) + 60] if pos >= 0 else note
            if NEGATION_RE.search(around):
                continue
            warns.append((
                "V3",
                f"{path} の解説に「{ident}」があるが、そのファイルの現在の内容に無い"
                f"（途中状態の記述、または誤った識別子の疑い）",
            ))

    # ---- V2: summary / highlights の識別子がリポジトリにあるか ---------------
    wide = []
    wide.append(("summary", " ".join(notes["summary"])))
    for h in notes["highlights"]:
        wide.append((f"highlights「{h['title']}」", h["title"] + " " + h["body"]))
    for path, why in notes.get("reading_order", []):
        wide.append((f"reading_order「{path}」", why))
    for i, step in enumerate(notes.get("verify_steps", []), 1):
        wide.append((f"verify_steps[{i}]", step))

    repo_cache = {}

    def in_repo(name):
        if name in repo_cache:
            return repo_cache[name]
        r = subprocess.run(
            ["git", "grep", "-l", "--fixed-strings", "-e", name, "--", "app", "tests", "resources", "config"],
            capture_output=True, text=True,
        )
        repo_cache[name] = bool(r.stdout.strip())
        return repo_cache[name]

    for where, text in wide:
        for ident in _idents(text):
            name = ident.split("::")[-1].rstrip("()").lstrip("$")
            if not name or name in IDENT_ALLOW or len(name) < 4:
                continue
            if not in_repo(name):
                warns.append(("V2", f"{where} の「{ident}」がリポジトリに見つからない"))

    # ---- V1: 数量表記の照合 -------------------------------------------------
    measured = {str(v) for v in stats.values()}
    written = set()
    for where, text in wide:
        for num in _numbers(text):
            written.add((where, num))
    for path, note in notes["file_notes"].items():
        note = note if isinstance(note, str) else " ".join(str(v) for v in note.values())
        for num in _numbers(note):
            written.add((f"file_notes「{path}」", num))

    verified = set(notes.get("verified_numbers", {}))
    unmatched = sorted({
        (w, n) for w, n in written
        if n not in measured and n not in verified and int(n) > 3
    })
    if unmatched:
        warns.append((
            "V1",
            "実測値にも verified_numbers にも無い数量表記。値を直すか、根拠を "
            "NOTES['verified_numbers'] に登録すること: "
            + " / ".join(f"{n}（{w}）" for w, n in unmatched),
        ))

    return warns


# =============================================================================
# HTML 生成
# =============================================================================
CSS = """
:root{--bg:#fff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--soft:#f6f8fa;
--add-bg:#e6ffec;--add-num:#ccffd8;--del-bg:#ffebe9;--del-num:#ffd7d5;
--hunk:#ddf4ff;--hunk-fg:#0550ae;--accent:#0969da;
--core:#1a7f37;--core-bg:#dafbe1;--warn:#9a6700;--warn-bg:#fff8c5;--note:#0550ae;--note-bg:#ddf4ff}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;
--line:#3d444d;--soft:#151b23;--add-bg:#12261e;--add-num:#1b4721;--del-bg:#25171c;--del-num:#542527;
--hunk:#121d2f;--hunk-fg:#4493f8;--accent:#4493f8;
--core:#3fb950;--core-bg:#12261e;--warn:#d29922;--warn-bg:#272115;--note:#4493f8;--note-bg:#121d2f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.7 -apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:32px 20px 96px}
h1{font-size:23px;margin:0 0 6px;letter-spacing:.01em}
.sub{color:var(--muted);font-size:13px;margin-bottom:24px}
.sub a{color:var(--accent)}
.stat{display:flex;gap:20px;flex-wrap:wrap;padding:14px 18px;background:var(--soft);
border:1px solid var(--line);border-radius:8px;margin-bottom:28px;font-size:13px}
.stat b{font-size:17px;display:block;font-weight:600}
.stat .add{color:var(--core)}.stat .del{color:#cf222e}
h2{font-size:17px;margin:36px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
p{margin:0 0 12px}
.hl{border:1px solid var(--line);border-left-width:4px;border-radius:6px;padding:13px 16px;margin-bottom:11px;background:var(--soft)}
.hl.core{border-left-color:var(--core)}.hl.warn{border-left-color:var(--warn)}.hl.note{border-left-color:var(--note)}
.hl .t{font-weight:600;margin-bottom:5px;display:flex;align-items:center;gap:8px}
.hl .b{font-size:14px;color:var(--muted);line-height:1.75}
.tag{font-size:10.5px;padding:2px 7px;border-radius:10px;font-weight:600;letter-spacing:.03em;white-space:nowrap}
.tag.core{background:var(--core-bg);color:var(--core)}
.tag.warn{background:var(--warn-bg);color:var(--warn)}
.tag.note{background:var(--note-bg);color:var(--note)}
ol.read{padding-left:22px;font-size:14px}
ol.read li{margin-bottom:7px}
ol.read code{font-size:12.5px}
ol.read span{color:var(--muted);font-size:13px}
code{background:var(--soft);border:1px solid var(--line);border-radius:4px;padding:1px 5px;
font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
.gh{margin:26px 0 8px}
.gh .ghead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:4px}
.gh .gt{font-size:15px;font-weight:600}
.gh .gd{font-size:13px;color:var(--muted)}
details.file{border:1px solid var(--line);border-radius:8px;margin-bottom:10px;overflow:hidden;background:var(--bg)}
details.file>summary{cursor:pointer;padding:10px 14px;background:var(--soft);
display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13.5px;list-style:none}
details.file>summary::-webkit-details-marker{display:none}
details.file>summary::before{content:"▸";color:var(--muted);font-size:11px;flex:none}
details.file[open]>summary::before{content:"▾"}
.fp{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:600;word-break:break-all}
.fs{margin-left:auto;font:11.5px ui-monospace,Menlo,monospace;color:var(--muted);white-space:nowrap}
.fs .a{color:var(--core)}.fs .d{color:#cf222e}
.grt{flex-basis:100%;font-size:12.5px;color:var(--muted);margin-top:5px;padding-left:17px;line-height:1.6}
.badge{font-size:10.5px;padding:2px 6px;border-radius:4px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}
.fnote{padding:11px 15px;font-size:13.5px;color:var(--muted);background:var(--bg);
border-bottom:1px solid var(--line);line-height:1.75}
table.d{width:100%;border-collapse:collapse;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;
table-layout:fixed}
table.d td{padding:0 6px;vertical-align:top;white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere}
td.n{width:48px;min-width:48px;text-align:right;color:var(--muted);user-select:none;
border-right:1px solid var(--line);font-size:11px}
tr.add td{background:var(--add-bg)}tr.add td.n{background:var(--add-num)}
tr.del td{background:var(--del-bg)}tr.del td.n{background:var(--del-num)}
tr.hh td{background:var(--hunk);color:var(--hunk-fg);font-size:11.5px;padding:4px 6px}
tr.meta td{color:var(--muted);font-size:11px}
td.s{width:14px;min-width:14px;text-align:center;color:var(--muted);user-select:none}
.ctrl{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0 4px}
.ctrl button{font:13px inherit;padding:6px 13px;border:1px solid var(--line);border-radius:6px;
background:var(--soft);color:var(--fg);cursor:pointer}
.ctrl button:hover{border-color:var(--accent);color:var(--accent)}
.foot{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}
"""

JS = """
function setAll(open,sel){document.querySelectorAll(sel).forEach(function(d){d.open=open})}
document.getElementById('b-core').onclick=function(){
  setAll(true,'details.file.g-core, details.file.g-impl');
  setAll(false,'details.file.g-test, details.file.g-infra');
  window.scrollTo({top:document.getElementById('diffs').offsetTop-16,behavior:'smooth'});
};
document.getElementById('b-all').onclick=function(){setAll(true,'details.file')};
document.getElementById('b-none').onclick=function(){setAll(false,'details.file')};
"""


# =============================================================================
# コメント機能（外部依存ゼロ・自己完結） — token-thin-docgen から踏襲
#   - 本文をドラッグ選択 → 「＋コメント」→ 入力
#   - 保存先は localStorage（不可環境はメモリ）。どこにも送信しない
#   - 差分内で選択した場合はファイルパスと行番号を自動で記録する
#   - 「まとめてコピー」で Markdown 化
# =============================================================================
COMMENT_CSS = """
#cmt-add{position:absolute;z-index:50;display:none;padding:5px 11px;font:12px inherit;
border:1px solid var(--accent);border-radius:6px;background:var(--accent);color:#fff;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.2)}
#cmt-pop{position:absolute;z-index:51;display:none;width:min(340px,90vw);padding:12px;
background:var(--bg);border:1px solid var(--line);border-radius:8px;box-shadow:0 6px 24px rgba(0,0,0,.18)}
#cmt-pop .q{font-size:11.5px;color:var(--muted);border-left:3px solid var(--line);padding-left:8px;
margin-bottom:8px;max-height:60px;overflow:auto;white-space:pre-wrap;word-break:break-all}
#cmt-pop textarea{width:100%;min-height:74px;padding:7px;font:13px inherit;color:var(--fg);
background:var(--bg);border:1px solid var(--line);border-radius:5px;resize:vertical}
#cmt-pop .row{display:flex;gap:7px;justify-content:flex-end;margin-top:8px}
#cmt-pop button{font:12.5px inherit;padding:5px 12px;border-radius:5px;border:1px solid var(--line);
background:var(--soft);color:var(--fg);cursor:pointer}
#cmt-pop button.pri{background:var(--accent);border-color:var(--accent);color:#fff}
#cmt-pop .note{font-size:10.5px;color:var(--muted);margin:8px 0 0;line-height:1.6}
.cmt-hl{background:var(--warn-bg);border-bottom:2px solid var(--warn);cursor:pointer}
#cmt-panel{position:fixed;right:14px;bottom:14px;z-index:49;width:min(330px,92vw);
background:var(--bg);border:1px solid var(--line);border-radius:9px;box-shadow:0 4px 20px rgba(0,0,0,.16);
font-size:12.5px;overflow:hidden}
#cmt-panel .hd{display:flex;align-items:center;gap:8px;padding:9px 12px;background:var(--soft);
border-bottom:1px solid var(--line);cursor:pointer;user-select:none}
#cmt-panel .hd .ttl{font-weight:600}
#cmt-panel .hd .tg{margin-left:auto;color:var(--muted);font-size:11px}
#cmt-panel .bd{max-height:min(46vh,380px);overflow:auto;padding:8px 10px}
#cmt-panel.min .bd,#cmt-panel.min .ft{display:none}
#cmt-panel .ft{padding:8px 10px;border-top:1px solid var(--line)}
#cmt-panel .ft button{width:100%;font:12.5px inherit;padding:7px;border-radius:6px;
border:1px solid var(--line);background:var(--soft);color:var(--fg);cursor:pointer}
#cmt-panel .it{padding:7px 0;border-bottom:1px solid var(--line)}
#cmt-panel .it:last-child{border-bottom:0}
#cmt-panel .it .w{font-size:10.5px;color:var(--accent);font-family:ui-monospace,Menlo,monospace;word-break:break-all}
#cmt-panel .it .q{font-size:11px;color:var(--muted);margin:3px 0;border-left:2px solid var(--line);padding-left:6px}
#cmt-panel .it .c{white-space:pre-wrap;line-height:1.6}
#cmt-panel .it .x{float:right;color:var(--muted);cursor:pointer;font-size:14px;line-height:1;padding:0 3px}
#cmt-empty{color:var(--muted);text-align:center;padding:14px 0;line-height:1.7}
#cmt-md{position:fixed;inset:0;z-index:60;display:none;background:rgba(0,0,0,.45);padding:5vh 4vw}
#cmt-md .box{max-width:760px;margin:0 auto;background:var(--bg);border-radius:9px;padding:16px;
max-height:90vh;display:flex;flex-direction:column}
#cmt-md textarea{flex:1;min-height:300px;width:100%;font:12px ui-monospace,Menlo,monospace;
padding:10px;border:1px solid var(--line);border-radius:6px;background:var(--soft);color:var(--fg)}
#cmt-md .row{display:flex;gap:8px;justify-content:flex-end;margin-top:10px}
#cmt-md button{font:13px inherit;padding:7px 14px;border-radius:6px;border:1px solid var(--line);
background:var(--soft);color:var(--fg);cursor:pointer}
#cmt-md button.pri{background:var(--accent);border-color:var(--accent);color:#fff}
@media print{#cmt-add,#cmt-pop,#cmt-panel,#cmt-md{display:none!important}}
"""

COMMENT_HTML = """
<button id="cmt-add">＋ コメント</button>
<div id="cmt-pop">
  <div class="q" id="cmt-q"></div>
  <textarea id="cmt-ta" placeholder="気づいた点を書く"></textarea>
  <div class="row"><button id="cmt-cancel">やめる</button><button class="pri" id="cmt-save">追加</button></div>
  <p class="note">💾 コメントはこのブラウザ内に保存されるだけで、どこにも送信されません。
  共有するには「まとめてコピー」でテキスト化してください。</p>
</div>
<div id="cmt-panel" class="min">
  <div class="hd" id="cmt-hd"><span class="ttl">💬 コメント <span id="cmt-n">0</span></span><span class="tg" id="cmt-tg">開く</span></div>
  <div class="bd" id="cmt-list"></div>
  <div class="ft"><button id="cmt-copy">まとめてコピー</button></div>
</div>
<div id="cmt-md"><div class="box">
  <textarea id="cmt-md-ta" readonly></textarea>
  <div class="row"><button id="cmt-md-close">閉じる</button><button class="pri" id="cmt-md-copy">コピー</button></div>
</div></div>
"""

COMMENT_JS = """
(function(){
  var KEY='reviewdiff::'+DOC_KEY, MEM='[]';
  var LS=(function(){try{localStorage.setItem('__t','1');localStorage.removeItem('__t');return 1}catch(e){return 0}})();
  function load(){try{return JSON.parse(LS?(localStorage.getItem(KEY)||'[]'):MEM)||[]}catch(e){return[]}}
  function save(){var j=JSON.stringify(C);if(LS){try{localStorage.setItem(KEY,j)}catch(e){}}else{MEM=j}}
  var C=load(), pending=null;
  var $=function(i){return document.getElementById(i)};
  var addBtn=$('cmt-add'), pop=$('cmt-pop'), panel=$('cmt-panel');

  function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}

  // 選択位置から「どこに対するコメントか」を推定する
  function whereOf(node){
    var el=node&&node.nodeType===3?node.parentElement:node;
    if(!el||!el.closest)return{label:'本文'};
    var d=el.closest('details.file');
    if(d){
      var p=d.querySelector('.fp')?d.querySelector('.fp').textContent:'';
      var tr=el.closest('tr'), ln='';
      if(tr){var ns=tr.querySelectorAll('td.n');if(ns.length>1)ln=ns[1].textContent.trim()||ns[0].textContent.trim()}
      return{label:p+(ln?':'+ln:'')};
    }
    var hl=el.closest('.hl');
    if(hl){var t=hl.querySelector('.t');return{label:'要点: '+(t?t.textContent.trim():'')}}
    var h=el.closest('.wrap')?prevH2(el):null;
    return{label:h||'本文'};
  }
  function prevH2(el){var n=el;while(n&&n!==document.body){var p=n.previousElementSibling;
    while(p){if(p.tagName==='H2')return p.textContent.trim();p=p.previousElementSibling}n=n.parentElement}return null}

  document.addEventListener('mouseup',function(e){
    var tg=e.target; if(!tg||!tg.closest)return;
    if(pop.style.display==='block'||tg.closest('#cmt-pop,#cmt-panel,#cmt-md'))return;
    var s=window.getSelection();
    if(!s||s.isCollapsed||!s.toString().trim()){addBtn.style.display='none';return}
    var r=s.getRangeAt(0), b=r.getBoundingClientRect();
    pending={quote:s.toString().trim().slice(0,300), where:whereOf(r.startContainer).label, range:r.cloneRange()};
    addBtn.style.display='block';
    addBtn.style.left=(window.scrollX+b.left)+'px';
    addBtn.style.top=(window.scrollY+b.bottom+7)+'px';
  });

  addBtn.onclick=function(){
    if(!pending)return;
    addBtn.style.display='none';
    $('cmt-q').textContent=pending.where+'\\n'+pending.quote;
    $('cmt-ta').value='';
    pop.style.display='block';
    pop.style.left=addBtn.style.left; pop.style.top=addBtn.style.top;
    $('cmt-ta').focus();
  };
  $('cmt-cancel').onclick=function(){pop.style.display='none';pending=null};
  $('cmt-save').onclick=function(){
    var v=$('cmt-ta').value.trim(); if(!v||!pending)return;
    C.push({id:Date.now()+'-'+Math.random().toString(36).slice(2,7),where:pending.where,quote:pending.quote,text:v});
    save(); hl(pending.range); render(); pop.style.display='none'; pending=null;
    window.getSelection().removeAllRanges();
  };
  function hl(r){try{var m=document.createElement('mark');m.className='cmt-hl';r.surroundContents(m)}catch(e){}}

  function render(){
    $('cmt-n').textContent=C.length;
    var L=$('cmt-list');
    if(!C.length){L.innerHTML='<div id="cmt-empty">本文をドラッグ選択して<br>コメントを追加できます</div>';return}
    L.innerHTML=C.map(function(c){return '<div class="it"><span class="x" data-x="'+c.id+'">×</span>'+
      '<div class="w">'+esc(c.where)+'</div><div class="q">'+esc(c.quote.slice(0,90))+'</div>'+
      '<div class="c">'+esc(c.text)+'</div></div>'}).join('');
  }
  $('cmt-list').onclick=function(e){var x=e.target.getAttribute('data-x');
    if(x){C=C.filter(function(c){return c.id!==x});save();render()}};
  $('cmt-hd').onclick=function(){panel.classList.toggle('min');
    $('cmt-tg').textContent=panel.classList.contains('min')?'開く':'閉じる'};

  function md(){
    var o=['# レビューコメント: '+document.title,'','件数: '+C.length,''];
    C.forEach(function(c,i){o.push('## '+(i+1)+'. '+c.where,'','> '+c.quote.replace(/\\n/g,'\\n> '),'',c.text,'')});
    return o.join('\\n');
  }
  $('cmt-copy').onclick=function(){
    if(!C.length){alert('コメントがありません');return}
    $('cmt-md-ta').value=md(); $('cmt-md').style.display='block'; $('cmt-md-ta').select();
  };
  $('cmt-md-close').onclick=function(){$('cmt-md').style.display='none'};
  $('cmt-md-copy').onclick=function(){
    var t=$('cmt-md-ta'); t.select();
    try{navigator.clipboard.writeText(t.value)}catch(e){try{document.execCommand('copy')}catch(e2){}}
    $('cmt-md-copy').textContent='コピーした'; setTimeout(function(){$('cmt-md-copy').textContent='コピー'},1400);
  };
  document.addEventListener('click',function(e){
    if(e.target&&e.target.closest&&!e.target.closest('#cmt-add,#cmt-pop'))addBtn.style.display='none';
  });
  render();
})();
"""


def esc(s):
    return html.escape(s, quote=False)


def _before_after(n):
    """NOTES["before_after"] = {"before": {...}, "after": {...}} を2枚のカードにする。"""
    ba = n.get("before_after")
    if not ba:
        return ""
    out = ['<div class="ba">']
    for key, label in (("before", "修正前"), ("after", "修正後")):
        d = ba.get(key)
        if not d:
            continue
        out.append(
            f'<div class="{key}"><h4>{esc(label)}</h4>'
            f'<div class="u">{esc(d.get("sample", ""))}</div>'
            f'<div class="r">{esc(d.get("note", ""))}</div></div>'
        )
    out.append("</div>")
    return "".join(out)


def render_hunk_rows(h):
    out = [f'<tr class="hh"><td class="n"></td><td class="n"></td><td class="s"></td>'
           f'<td>{esc(h["header"])} {esc(h["context"])}</td></tr>']
    for kind, o, n, text in h["lines"]:
        if kind == "meta":
            out.append(f'<tr class="meta"><td class="n"></td><td class="n"></td><td class="s"></td>'
                       f'<td>{esc(text)}</td></tr>')
            continue
        sign = {"add": "+", "del": "-", "ctx": ""}[kind]
        cls = f' class="{kind}"' if kind in ("add", "del") else ""
        out.append(f'<tr{cls}><td class="n">{o or ""}</td><td class="n">{n or ""}</td>'
                   f'<td class="s">{sign}</td><td>{esc(text)}</td></tr>')
    return "".join(out)


MAX_LINES_PER_FILE = 400      # これを超えたら先頭だけ出す
HEAD_LINES = 200
SKIP_BODY_PAT = re.compile(
    r"(\.lock$|-lock\.(json|yaml)$|\.snap$|^dist/|^build/|^vendor/|^node_modules/|\.min\.(js|css)$)"
)


def render_file(f, note, pr_url, groups, notes=None):
    """note は文字列、または {"guarantee": 折りたたみ外に出す1行, "note": 展開後の詳細}。

    テストは展開せずに読み飛ばす前提なので、「何を担保しているか」だけは
    summary 行に出して閉じたままでも見えるようにする。
    """
    if isinstance(note, dict):
        guarantee = note.get("guarantee", "")
        body = note.get("note", "")
    else:
        guarantee, body = "", (note or "")

    st = {"added": "新規", "deleted": "削除", "modified": "変更"}[f["status"]]
    link = ""
    if pr_url:
        link = f' <a class="badge" href="{pr_url}/files" target="_blank" rel="noopener">GitHub</a>'
    grt_html = f'<span class="grt">{esc(guarantee)}</span>' if guarantee else ""
    note_html = f'<div class="fnote">{esc(body)}</div>' if body else ""
    total = sum(len(h["lines"]) for h in f["hunks"])
    if SKIP_BODY_PAT.search(f["path"]):
        rows = (f'<tr class="meta"><td class="n"></td><td class="n"></td><td class="s"></td>'
                f'<td>生成物・ロックファイルのため差分は省略（{total}行）</td></tr>')
    elif total > MAX_LINES_PER_FILE:
        shown, acc = 0, []
        for h in f["hunks"]:
            if shown >= HEAD_LINES:
                break
            acc.append(render_hunk_rows(h))
            shown += len(h["lines"])
        acc.append(
            f'<tr class="meta"><td class="n"></td><td class="n"></td><td class="s"></td>'
            f'<td>… 残り {total - shown} 行は省略（全体は git diff / GitHub で確認）</td></tr>'
        )
        rows = "".join(acc)
    else:
        rows = "".join(render_hunk_rows(h) for h in f["hunks"])
    return (
        f'<details class="file g-{group_of(f["path"], groups)}">'
        f'<summary><span class="fp">{esc(f["path"])}</span>'
        f'<span class="badge">{st}</span>{link}'
        f'<span class="fs"><span class="a">+{f["additions"]}</span> '
        f'<span class="d">-{f["deletions"]}</span></span>{grt_html}</summary>'
        f'{note_html}<table class="d"><tbody>{rows}</tbody></table></details>'
    )


GIT_BASE = ["git", "-c", "diff.noprefix=false", "-c", "core.pager=cat"]


def build(rng, out_path, NOTES, DIAGRAM_SVG=""):
    if ".." not in rng:
        sys.exit(
            f"範囲の指定が不正です: {rng}\n"
            "  base...head の形で渡してください（例: main...HEAD, origin/main...HEAD）。\n"
            "  単一リビジョンだと git log が全履歴を返し、巨大なコミット一覧が出ます。"
        )

    # --no-color: color.ui=always の gitconfig で ANSI が混ざりパースが 0 件になるのを防ぐ
    # --no-ext-diff: diff.external 設定を無視する
    # errors="replace": 非UTF-8（Shift-JIS 等）でクラッシュしない
    diff_text = subprocess.run(
        GIT_BASE + ["diff", "--no-color", "--no-ext-diff", rng],
        capture_output=True, text=True, errors="replace", check=True,
    ).stdout
    files = parse_diff(diff_text)
    log_out = subprocess.run(
        GIT_BASE + ["log", "--oneline", "-n", "50", rng.replace("...", "..")],
        capture_output=True, text=True, errors="replace", check=True,
    ).stdout.strip()
    log = log_out.split("\n") if log_out else []

    total_a = sum(f["additions"] for f in files)
    total_d = sum(f["deletions"] for f in files)
    groups = NOTES["groups"]
    by_group = {}
    for f in files:
        by_group.setdefault(group_of(f["path"], groups), []).append(f)

    n = NOTES
    parts = [
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<title>{esc(n["title"])}</title><style>{CSS}{DIAGRAM_CSS}{COMMENT_CSS}</style></head><body><div class="wrap">',
        f'<h1>{esc(n["title"])}</h1>',
        f'<div class="sub">{esc(n["subtitle"])}',
    ]
    if n.get("pr_url"):
        parts.append(f' · <a href="{n["pr_url"]}" target="_blank" rel="noopener">PRを開く</a>')
    parts.append("</div>")

    # グループ定義から算出する。パスのハードコードをやめ、グループを変えれば統計も追従する
    group_add = {gid: sum(x["additions"] for x in gf) for gid, gf in by_group.items()}
    # 既定で開くグループ = 実装、閉じるグループのうち "test" を含む id = テスト、と見なす
    open_ids = {g[0] for g in groups if g[3]}
    test_ids = {g[0] for g in groups if not g[3] and "test" in g[0]}
    impl_add = sum(v for gid, v in group_add.items() if gid in open_ids)
    test_add = sum(v for gid, v in group_add.items() if gid in test_ids)
    parts.append(
        f'<div class="stat">'
        f'<div><b>{len(log)}</b>コミット</div>'
        f'<div><b>{len(files)}</b>ファイル</div>'
        f'<div><b class="add">+{total_a}</b>追加</div>'
        f'<div><b class="del">-{total_d}</b>削除</div>'
        f'<div><b>{impl_add}</b>実装の追加行</div>'
        f'<div><b>{test_add}</b>テストの追加行</div>'
        f'</div>'
    )

    parts.append("<h2>この変更は何か</h2>")
    for p in n["summary"]:
        parts.append(f"<p>{esc(p)}</p>")

    parts.append(
        _before_after(n)
    )

    parts.append("<h2>レビューの要点</h2>")
    for h in n["highlights"]:
        lv = h["level"]
        label = {"core": "本題", "warn": "注意", "note": "経緯"}[lv]
        parts.append(
            f'<div class="hl {lv}"><div class="t"><span class="tag {lv}">{label}</span>'
            f'{esc(h["title"])}</div><div class="b">{esc(h["body"])}</div></div>'
        )

    if n.get("verify_steps"):
        parts.append("<h2>リリース後に確認すること</h2><ol class='read'>")
        for step in n["verify_steps"]:
            parts.append(f"<li>{esc(step)}</li>")
        parts.append("</ol>")

    if DIAGRAM_SVG.strip():
        parts.append(
            "<h2>変更箇所の繋がり</h2>"
            "<p>3種類の変更（★）がなぜ全部必要なのかを示す。S3のキーが固定なので画像を差し替えてもURLが変わらない。"
            "だから <code>updated_at</code> を進め、Blade がそれを読んでURLに載せ、"
            "CloudFront がそのクエリをキャッシュキーとして扱う。この連鎖のどこか1つが欠けると効果が出ない。"
            "これから読む9ファイルの地図として使ってほしい。</p>"
            + DIAGRAM_SVG
        )

    parts.append(
        '<h2 id="diffs">差分</h2>'
        '<div class="ctrl">'
        '<button id="b-core">本題と実装だけ開く</button>'
        '<button id="b-all">すべて開く</button>'
        '<button id="b-none">すべて閉じる</button>'
        '</div>'
    )

    for gid, gtitle, gdesc, default_open, *_ in n["groups"]:
        gfiles = by_group.get(gid, [])
        if not gfiles:
            continue
        ga = sum(f["additions"] for f in gfiles)
        gd = sum(f["deletions"] for f in gfiles)
        parts.append(
            f'<div class="gh"><div class="ghead"><span class="gt">{esc(gtitle)}</span>'
            f'<span class="gd">{len(gfiles)}ファイル · '
            f'<span style="color:var(--core)">+{ga}</span> '
            f'<span style="color:#cf222e">-{gd}</span> · {esc(gdesc)}</span></div></div>'
        )
        for f in sorted(gfiles, key=lambda x: x["path"]):
            item = render_file(f, n["file_notes"].get(f["path"], ""), n.get("pr_url"), groups, n)
            if default_open:
                item = item.replace('<details class="file', '<details open class="file', 1)
            parts.append(item)

    parts.append("<h2>コミット</h2><ol class='read'>")
    for line in log:
        sha, _, msg = line.partition(" ")
        parts.append(f"<li><code>{esc(sha)}</code> {esc(msg)}</li>")
    parts.append("</ol>")

    parts.append(
        f'<div class="foot">git diff {esc(rng)} から自動生成 · '
        f'差分は機械生成、解説は手書き · 外部依存なしの単一HTML</div>'
    )
    doc_key = re.sub(r"[^A-Za-z0-9]+", "-", n["title"])[:60]
    parts.append("</div>" + COMMENT_HTML)
    parts.append(
        "<script>var DOC_KEY=" + json.dumps(doc_key) + ";</script>"
        f"<script>{JS}</script><script>{COMMENT_JS}</script></body></html>"
    )

    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write("".join(parts))

    print(f"生成: {out_path}")

    # ---- 実測値（解説に書いた数字はこれと突き合わせる）----------------------
    stats = {
        "commits": len(log),
        "files": len(files),
        "additions": total_a,
        "deletions": total_d,
        "impl_add": impl_add,
        "test_add": test_add,
        "test_ratio": round(test_add * 100 / total_a) if total_a else 0,
    }
    for gid, *_ in n["groups"]:
        gf = by_group.get(gid, [])
        stats[f"g_{gid}_files"] = len(gf)
        stats[f"g_{gid}_lines"] = sum(x["additions"] for x in gf)

    print("  実測値:")
    for k, v in stats.items():
        print(f"    {k:16} {v}")

    # ---- 検証 ---------------------------------------------------------------
    warns = verify(files, n, stats)
    if warns:
        print(f"\n  ⚠ 検証で {len(warns)} 件の指摘。HTMLは生成したが、解説を直して再生成すること:")
        for kind, msg in warns:
            print(f"    [{kind}] {msg}")
    else:
        print("\n  ✓ 検証に指摘なし")


def load_notes(path):
    """notes.py を読み込み、NOTES と（あれば）DIAGRAM_SVG を返す。"""
    spec = importlib.util.spec_from_file_location("review_notes", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.NOTES, getattr(mod, "DIAGRAM_SVG", "")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    notes, diagram = load_notes(sys.argv[1])
    build(sys.argv[2], sys.argv[3], notes, diagram)
