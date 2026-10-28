"""HTML 报告渲染：单文件自包含（内联 CSS/JS + base64 截图）。

为什么不用 Jinja2
-----------------
本项目依赖表刻意保持精简（只有 Appium）。报告是纯静态拼接，用 f-string 足够，
引入模板引擎反而增加部署成本。截图转 base64 内嵌，报告可以单独发给别人。

视觉风格
--------
工业蓝图（Industrial Blueprint）：工程蓝 + 蓝图网格背景 + 等宽字体标注技术字段。
"""

from __future__ import annotations

import html
import json
import os
import time
from pathlib import Path

from core.report.models import (
    ERROR,
    FAILED,
    PASSED,
    SKIPPED,
    CaseRecord,
    RunRecord,
    human_duration,
    image_to_data_uri,
)

# ---------------------------------------------------------------------- #
# 样式
# ---------------------------------------------------------------------- #
_CSS = """
:root{
  --bg:#0a1120; --bg2:#0e1729; --panel:#111c31; --panel2:#152340;
  --line:#1e3252; --line2:#27405f;
  --ink:#e6eefc; --ink2:#9fb2ce; --ink3:#6b81a3;
  --blue:#3d8bfd; --blue2:#5fa8ff; --cyan:#22d3ee;
  --ok:#22c55e; --okbg:rgba(34,197,94,.12);
  --bad:#f2506a; --badbg:rgba(242,80,106,.12);
  --skip:#e0a828; --skipbg:rgba(224,168,40,.12);
  --mono:"Cascadia Mono","JetBrains Mono",Consolas,"Courier New",monospace;
  --sans:"Space Grotesk","Inter","Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
}
*{box-sizing:border-box}
body{
  margin:0; padding:0 0 64px; background:var(--bg); color:var(--ink);
  font-family:var(--sans); font-size:14px; line-height:1.6;
  background-image:
    linear-gradient(rgba(61,139,253,.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(61,139,253,.055) 1px, transparent 1px);
  background-size:32px 32px;
}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px}

/* ---------- 头部 ---------- */
header{
  border-bottom:1px solid var(--line); background:linear-gradient(180deg,#0c1526 0%,rgba(10,17,32,.6) 100%);
  padding:32px 0 26px; margin-bottom:28px; position:relative; overflow:hidden;
}
header:before{
  content:""; position:absolute; inset:0;
  background:radial-gradient(700px 180px at 12% 0%, rgba(61,139,253,.16), transparent 70%);
  pointer-events:none;
}
.brand{display:flex;align-items:center;gap:12px;font-size:12px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--blue2);font-family:var(--mono);margin-bottom:10px}
.brand .dot{width:7px;height:7px;background:var(--cyan);border-radius:50%;
  box-shadow:0 0 12px var(--cyan)}
h1{margin:0 0 8px;font-size:27px;font-weight:600;letter-spacing:-.01em}
.meta{color:var(--ink3);font-family:var(--mono);font-size:12.5px;display:flex;flex-wrap:wrap;gap:18px}
.verdict{
  display:inline-flex;align-items:center;gap:8px;margin-top:16px;padding:7px 16px;
  border-radius:6px;font-family:var(--mono);font-size:12.5px;font-weight:600;letter-spacing:.06em;
}
.verdict.ok{background:var(--okbg);color:var(--ok);border:1px solid rgba(34,197,94,.35)}
.verdict.bad{background:var(--badbg);color:var(--bad);border:1px solid rgba(242,80,106,.35)}
.verdict.none{background:var(--skipbg);color:var(--skip);border:1px solid rgba(224,168,40,.35)}

/* ---------- 统计卡 ---------- */
.stats{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:28px}
.stat{
  background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;
  position:relative;overflow:hidden;
}
.stat:after{content:"";position:absolute;left:0;top:0;bottom:0;width:2px;background:var(--line2)}
.stat.ok:after{background:var(--ok)} .stat.bad:after{background:var(--bad)}
.stat.skip:after{background:var(--skip)} .stat.blue:after{background:var(--blue)}
.stat .k{font-size:11px;letter-spacing:.12em;color:var(--ink3);text-transform:uppercase;
  font-family:var(--mono)}
.stat .v{font-size:26px;font-weight:600;font-family:var(--mono);margin-top:2px;line-height:1.2}
.stat.ok .v{color:var(--ok)} .stat.bad .v{color:var(--bad)} .stat.skip .v{color:var(--skip)}
.stat.blue .v{color:var(--blue2)}

/* ---------- 通用区块 ---------- */
section{margin-bottom:30px}
h2{font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink2);
  font-family:var(--mono);font-weight:600;margin:0 0 14px;
  display:flex;align-items:center;gap:10px}
h2:after{content:"";flex:1;height:1px;background:var(--line)}

/* ---------- 环境表 ---------- */
.env{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.env div{background:var(--panel);padding:11px 15px;display:flex;justify-content:space-between;
  gap:14px;align-items:baseline}
.env .k{color:var(--ink3);font-size:12px;white-space:nowrap}
.env .v{font-family:var(--mono);font-size:12.5px;color:var(--ink);text-align:right;
  word-break:break-all}

/* ---------- 用例 ---------- */
.toolbar{display:flex;gap:9px;margin-bottom:14px;flex-wrap:wrap}
button{
  background:var(--panel);color:var(--ink2);border:1px solid var(--line2);border-radius:6px;
  padding:6px 14px;font-size:12px;font-family:var(--mono);cursor:pointer;
  transition:.15s;letter-spacing:.04em;
}
button:hover{background:var(--panel2);color:var(--ink);border-color:var(--blue)}

.case{
  background:var(--panel);border:1px solid var(--line);border-radius:8px;
  margin-bottom:10px;overflow:hidden;transition:border-color .15s;
}
.case.failed{border-color:rgba(242,80,106,.4)}
.case.skipped{opacity:.72}
.case>summary{
  padding:14px 18px;cursor:pointer;display:flex;align-items:center;gap:13px;
  list-style:none;user-select:none;
}
.case>summary::-webkit-details-marker{display:none}
.case>summary:hover{background:var(--panel2)}
.badge{
  font-family:var(--mono);font-size:10.5px;font-weight:700;letter-spacing:.1em;
  padding:3px 9px;border-radius:4px;min-width:52px;text-align:center;flex-shrink:0;
}
.badge.passed{background:var(--okbg);color:var(--ok)}
.badge.failed,.badge.error{background:var(--badbg);color:var(--bad)}
.badge.skipped{background:var(--skipbg);color:var(--skip)}
.case .title{flex:1;font-weight:500;font-size:14.5px}
.case .dur{font-family:var(--mono);font-size:12px;color:var(--ink3);flex-shrink:0}

.body{padding:4px 18px 18px;border-top:1px solid var(--line)}
.sub{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink3);
  font-family:var(--mono);margin:16px 0 9px}

/* ---------- 步骤时间线 ---------- */
.steps{position:relative;padding-left:6px}
.step{display:flex;align-items:flex-start;gap:11px;padding:6px 0;font-size:13px}
.step .mk{width:8px;height:8px;border-radius:50%;margin-top:6px;flex-shrink:0;background:var(--ok)}
.step.failed .mk{background:var(--bad);box-shadow:0 0 9px var(--bad)}
.step .nm{flex:1;color:var(--ink)}
.step.failed .nm{color:var(--bad)}
.step .du{font-family:var(--mono);font-size:11.5px;color:var(--ink3);flex-shrink:0}
.step .err{color:var(--bad);font-family:var(--mono);font-size:11.5px;
  margin-left:19px;padding:6px 10px;background:var(--badbg);border-radius:4px;
  border-left:2px solid var(--bad);white-space:pre-wrap;word-break:break-word}

/* ---------- 错误块 ---------- */
.errbox{background:var(--badbg);border:1px solid rgba(242,80,106,.3);border-left:3px solid var(--bad);
  border-radius:6px;padding:13px 16px;margin-top:8px}
.errbox .etype{font-family:var(--mono);font-size:12.5px;color:var(--bad);font-weight:600}
.errbox .emsg{font-family:var(--mono);font-size:12.5px;color:var(--ink);margin-top:6px;
  white-space:pre-wrap;word-break:break-word}
pre.tb{background:#070d18;border:1px solid var(--line);border-radius:6px;padding:13px 15px;
  overflow-x:auto;font-family:var(--mono);font-size:11.5px;line-height:1.65;color:var(--ink2);
  margin:8px 0 0;max-height:340px;overflow-y:auto}

/* ---------- 上下文 ---------- */
.ctx{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.ctx span{background:var(--panel2);border:1px solid var(--line2);border-radius:4px;
  padding:4px 10px;font-family:var(--mono);font-size:11.5px;color:var(--ink2)}
.ctx b{color:var(--blue2);font-weight:500}

/* ---------- 截图 ---------- */
.shots{display:flex;gap:12px;flex-wrap:wrap;margin-top:10px}
.shot{border:1px solid var(--line2);border-radius:6px;overflow:hidden;cursor:zoom-in;
  transition:.18s;background:#000;line-height:0}
.shot:hover{border-color:var(--blue);transform:translateY(-2px)}
.shot img{width:168px;display:block;opacity:.94}
.shot:hover img{opacity:1}

/* ---------- 灯箱 ---------- */
#lb{position:fixed;inset:0;background:rgba(4,8,16,.94);display:none;align-items:center;
  justify-content:center;z-index:99;cursor:zoom-out;padding:32px}
#lb.on{display:flex}
#lb img{max-width:96vw;max-height:92vh;border:1px solid var(--line2);border-radius:5px;
  box-shadow:0 24px 80px rgba(0,0,0,.8)}

/* ---------- 空状态 ---------- */
.empty{text-align:center;padding:44px;color:var(--ink3);font-family:var(--mono);font-size:13px;
  border:1px dashed var(--line2);border-radius:8px}

footer{margin-top:44px;padding-top:20px;border-top:1px solid var(--line);
  color:var(--ink3);font-family:var(--mono);font-size:11.5px;display:flex;
  justify-content:space-between;flex-wrap:wrap;gap:10px}

@media (max-width:900px){
  .stats{grid-template-columns:repeat(3,1fr)}
  .env{grid-template-columns:1fr}
}
@media (max-width:560px){
  .stats{grid-template-columns:repeat(2,1fr)}
  h1{font-size:21px}
}
@media print{
  body{background:#fff;color:#111}
  .case{break-inside:avoid}
}
"""

_JS = """
function toggleAll(open){
  document.querySelectorAll('details.case').forEach(function(d){ d.open = open; });
}
function openFailed(){
  document.querySelectorAll('details.case').forEach(function(d){
    d.open = d.classList.contains('failed');
  });
}
document.addEventListener('click', function(e){
  var shot = e.target.closest('.shot');
  if (shot){
    var img = shot.querySelector('img');
    if (img){
      var lb = document.getElementById('lb');
      lb.querySelector('img').src = img.src;
      lb.classList.add('on');
    }
    return;
  }
  if (e.target.id === 'lb'){ e.target.classList.remove('on'); }
});
document.addEventListener('keydown', function(e){
  if (e.key === 'Escape'){
    var lb = document.getElementById('lb');
    if (lb) lb.classList.remove('on');
  }
});
function copyText(id, btn){
  var node = document.getElementById(id);
  if (!node) return;
  navigator.clipboard.writeText(node.textContent).then(function(){
    var old = btn.textContent;
    btn.textContent = '已复制';
    setTimeout(function(){ btn.textContent = old; }, 1200);
  });
}
"""


# ---------------------------------------------------------------------- #
# 片段渲染
# ---------------------------------------------------------------------- #
def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _status_label(status: str) -> str:
    return {PASSED: "PASS", FAILED: "FAIL", SKIPPED: "SKIP", ERROR: "ERROR"}.get(
        status, status.upper()
    )


def _render_stats(run: RunRecord) -> str:
    cells = [
        ("blue", "总用例", str(run.total)),
        ("ok", "通过", str(run.passed)),
        ("bad", "失败", str(run.failed)),
        ("skip", "跳过", str(run.skipped)),
        ("blue", "通过率", f"{run.pass_rate:.1f}%"),
        ("blue", "总耗时", human_duration(run.duration)),
    ]
    items = "".join(
        f'<div class="stat {cls}"><div class="k">{_esc(k)}</div>'
        f'<div class="v">{_esc(v)}</div></div>'
        for cls, k, v in cells
    )
    return f'<div class="stats">{items}</div>'


def _render_env(run: RunRecord) -> str:
    rows = run.env.as_rows()
    if not rows:
        return ""
    body = "".join(
        f'<div><span class="k">{_esc(k)}</span><span class="v">{_esc(v)}</span></div>'
        for k, v in rows
    )
    return f'<section><h2>运行环境</h2><div class="env">{body}</div></section>'


def _render_context(case: CaseRecord) -> str:
    if not case.context:
        return ""
    chips = "".join(
        f"<span><b>{_esc(k)}</b> {_esc(v)}</span>" for k, v in case.context.items()
    )
    return f'<div class="sub">业务上下文</div><div class="ctx">{chips}</div>'


def _render_steps(case: CaseRecord) -> str:
    if not case.steps:
        return ""
    rows = []
    for step in case.steps:
        failed = " failed" if step.status == FAILED else ""
        rows.append(
            f'<div class="step{failed}"><div class="mk"></div>'
            f'<div class="nm">{_esc(step.name)}</div>'
            f'<div class="du">{human_duration(step.duration)}</div></div>'
        )
        if step.error:
            rows.append(f'<div class="err">{_esc(step.error)}</div>')
    return f'<div class="sub">执行步骤（{len(case.steps)} 步）</div><div class="steps">{"".join(rows)}</div>'


def _render_error(case: CaseRecord) -> str:
    if case.status not in {FAILED, ERROR}:
        return ""
    parts = ['<div class="sub">失败详情</div>']
    if case.error_type or case.error_message:
        parts.append(
            f'<div class="errbox"><div class="etype">{_esc(case.error_type)}</div>'
            f'<div class="emsg">{_esc(case.error_message)}</div></div>'
        )
    if case.traceback_text:
        tb_id = f"tb-{abs(hash(case.nodeid)) % 10**8}"
        parts.append(
            f'<div style="margin-top:10px"><button onclick="copyText(\'{tb_id}\', this)">'
            f"复制 traceback</button></div>"
            f'<pre class="tb" id="{tb_id}">{_esc(case.traceback_text.strip())}</pre>'
        )
    return "".join(parts)


def _render_shots(case: CaseRecord, budget: "_ImageBudget | None" = None) -> str:
    if not case.screenshots:
        return ""
    imgs = []
    for path in case.screenshots:
        uri = budget.take(path) if budget is not None else image_to_data_uri(path)
        if uri:
            imgs.append(
                f'<div class="shot" title="{_esc(path.name)}"><img src="{uri}" '
                f'alt="{_esc(path.name)}" loading="lazy"></div>'
            )
    if not imgs:
        return ""
    return f'<div class="sub">现场截图（点击放大）</div><div class="shots">{"".join(imgs)}</div>'


class _ImageBudget:
    """报告内嵌图片的体积预算管理。

    一张 1080x2400 的真机 PNG 约 2MB，base64 后还会再涨 33%。如果一次回归失败
    了十几条用例，全内嵌能让报告冲到几十 MB，发都发不出去。

    策略：默认预算 6MB，**优先保证失败用例的截图内嵌**（按用例顺序消费预算）。
    超出预算的图片改用相对路径引用（报告与 artifacts 同目录时仍能显示），
    并在图注里标注「外链」，让报告既自包含又不失控。
    """

    def __init__(self, limit_bytes: int, report_path: Path | None = None) -> None:
        self.limit = limit_bytes
        self.used = 0
        self.report_path = report_path
        self.external = 0

    def take(self, path: Path | None) -> str:
        if path is None:
            return ""
        try:
            size = path.stat().st_size if path.exists() else 0
        except OSError:
            size = 0
        # base64 后的体积约为原始的 4/3
        estimated = int(size * 4 / 3)
        if self.used + estimated <= self.limit:
            uri = image_to_data_uri(path)
            if uri:
                self.used += estimated
                return uri
        self.external += 1
        return self._relative(path)

    def _relative(self, path: Path) -> str:
        """生成相对报告文件的路径；算不出来就返回空（只显示文件名）。"""
        if self.report_path is None:
            return ""
        try:
            return Path(os.path.relpath(path, self.report_path.parent)).as_posix()
        except (ValueError, OSError):
            return ""


def _render_case(case: CaseRecord, budget: _ImageBudget | None = None) -> str:
    open_attr = " open" if case.status in {FAILED, ERROR} else ""
    return (
        f'<details class="case {_esc(case.status)}"{open_attr}>'
        f"<summary>"
        f'<span class="badge {_esc(case.status)}">{_status_label(case.status)}</span>'
        f'<span class="title">{_esc(case.name)}</span>'
        f'<span class="dur">{human_duration(case.duration)}</span>'
        f"</summary>"
        f'<div class="body">'
        f"{_render_context(case)}"
        f"{_render_error(case)}"
        f"{_render_steps(case)}"
        f"{_render_shots(case, budget)}"
        f'<div class="sub" style="margin-top:16px">pytest 节点</div>'
        f'<div class="ctx"><span><b>{_esc(case.nodeid)}</b></span></div>'
        f"</div></details>"
    )


def _render_cases(run: RunRecord, budget: _ImageBudget | None = None) -> str:
    cases = run.sorted_cases()
    if not cases:
        return '<div class="empty">本次运行没有收集到用例</div>'
    return "".join(_render_case(c, budget) for c in cases)


# ---------------------------------------------------------------------- #
# 主入口
# ---------------------------------------------------------------------- #
def render_html(run: RunRecord, report_path: Path | None = None) -> str:
    """把运行记录渲染成完整 HTML 文本。

    Args:
        report_path: 报告的实际落盘路径。给了才能把超出体积预算的截图改写为相对路径。
    """
    budget = _ImageBudget(3 * 1024 * 1024, report_path)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run.started_at or time.time()))
    duration = human_duration(run.duration)

    if run.total == 0:
        verdict_cls, verdict_text = "none", "未执行任何用例"
    elif run.is_all_green:
        verdict_cls, verdict_text = "ok", f"全部通过 · {run.passed}/{run.total}"
    else:
        verdict_cls = "bad"
        verdict_text = f"{run.failed} 个失败 · 通过率 {run.pass_rate:.1f}%"

    command = f'<span>CMD · {_esc(run.command)}</span>' if run.command else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(run.title)} · {stamp}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <div class="wrap">
    <div class="brand"><span class="dot"></span>AndroidAutomation · Test Report</div>
    <h1>{_esc(run.title)}</h1>
    <div class="meta">
      <span>START · {stamp}</span>
      <span>DURATION · {duration}</span>
      <span>CASES · {run.total}</span>
      {command}
    </div>
    <div class="verdict {verdict_cls}">{_esc(verdict_text)}</div>
  </div>
</header>

<div class="wrap">
  {_render_stats(run)}
  {_render_env(run)}

  <section>
    <h2>用例明细</h2>
    <div class="toolbar">
      <button onclick="openFailed()">只看失败</button>
      <button onclick="toggleAll(true)">全部展开</button>
      <button onclick="toggleAll(false)">全部折叠</button>
    </div>
    {_render_cases(run, budget)}
  </section>

  <footer>
    <span>Generated by core.report · 单文件自包含报告</span>
    <span>{_esc(run.env.device_model or "no device")}</span>
  </footer>
</div>

<div id="lb"><img src="" alt=""></div>
<script>{_JS}</script>
</body>
</html>
"""


def write_html(run: RunRecord, path: Path) -> Path:
    """渲染并写盘，返回实际路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(run, path), encoding="utf-8")
    return path


def write_json(run: RunRecord, path: Path) -> Path:
    """导出 JSON，方便 CI 或二次分析消费。"""
    payload = {
        "title": run.title,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration": run.duration,
        "summary": {
            "total": run.total,
            "passed": run.passed,
            "failed": run.failed,
            "skipped": run.skipped,
            "pass_rate": round(run.pass_rate, 2),
        },
        "env": dict(run.env.as_rows()),
        "cases": [
            {
                "nodeid": c.nodeid,
                "name": c.name,
                "status": c.status,
                "duration": round(c.duration, 3),
                "context": c.context,
                "error_type": c.error_type,
                "error_message": c.error_message,
                "steps": [
                    {
                        "name": s.name.strip(),
                        "status": s.status,
                        "duration": round(s.duration, 3),
                        "error": s.error,
                    }
                    for s in c.steps
                ],
                "screenshots": [str(p) for p in c.screenshots],
            }
            for c in run.cases
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
