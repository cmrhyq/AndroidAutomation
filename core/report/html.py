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

/* ---------- 分组 ---------- */
.group{background:var(--bg2);border:1px solid var(--line);border-radius:9px;margin-bottom:14px;overflow:hidden}
.group>summary{padding:12px 16px;cursor:pointer;display:flex;align-items:center;gap:14px;
  list-style:none;user-select:none}
.group>summary::-webkit-details-marker{display:none}
.group>summary:hover{background:var(--panel2)}
.group .gname{font-family:var(--mono);font-size:13px;font-weight:600;color:var(--blue2);min-width:130px}
.group .gbar{flex:1;max-width:320px;height:5px;background:var(--line2);border-radius:3px;overflow:hidden}
.group .gbar i{display:block;height:100%;background:var(--ok)}
.group.hasfail .gbar i{background:var(--bad)}
.group .gstat{font-family:var(--mono);font-size:11.5px;color:var(--ink3);margin-left:auto}
.gbody{padding:12px 14px 4px;border-top:1px solid var(--line)}

/* ---------- 失败概览 ---------- */
.fails{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}
.fail{background:var(--badbg);border:1px solid rgba(242,80,106,.3);border-left:3px solid var(--bad);
  border-radius:7px;padding:12px 15px}
.fail .ftype{font-family:var(--mono);font-size:13px;color:var(--bad);font-weight:600;
  display:flex;justify-content:space-between;gap:10px;align-items:baseline}
.fail .fcount{font-size:11px;color:var(--ink2);background:rgba(242,80,106,.18);
  border-radius:10px;padding:1px 8px;flex-shrink:0}
.fail .fcases{margin-top:7px;font-size:12px;color:var(--ink2);line-height:1.7}

/* ---------- 表格 ---------- */
.tblwrap{background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{text-align:left;font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink3);font-weight:600;padding:9px 14px;
  border-bottom:1px solid var(--line)}
.tbl td{padding:9px 14px;border-bottom:1px solid var(--line);color:var(--ink)}
.tbl tr:last-child td{border-bottom:none}
.tbl .num{font-family:var(--mono);font-size:12.5px;color:var(--ink2);text-align:right;white-space:nowrap}
.tbl .st{font-family:var(--mono);font-size:11px;letter-spacing:.06em}
.tbl .st.passed{color:var(--ok)} .tbl .st.failed,.tbl .st.error{color:var(--bad)}
.tbl .st.skipped{color:var(--skip)}

/* ---------- 步骤截图 ---------- */
.stepshot{margin:2px 0 8px 19px}
.stepshot img{max-width:230px;border:1px solid var(--line2);border-radius:5px;
  cursor:zoom-in;opacity:.95;display:block}
.stepshot img:hover{opacity:1;border-color:var(--blue)}

/* ---------- 用例说明 ---------- */
.desc{color:var(--ink2);font-size:12.5px;line-height:1.75;white-space:pre-wrap;
  background:var(--panel2);border-left:2px solid var(--line2);border-radius:0 5px 5px 0;
  padding:9px 13px;margin-top:10px}

/* ---------- 索引页 ---------- */
.runrow{display:flex;align-items:center;gap:14px;background:var(--panel);border:1px solid var(--line);
  border-radius:8px;padding:12px 16px;margin-bottom:8px;text-decoration:none;color:inherit;
  transition:.15s}
.runrow:hover{border-color:var(--blue);background:var(--panel2)}
.runrow .when{font-family:var(--mono);font-size:12.5px;color:var(--ink2);min-width:150px}
.runrow .bar{flex:1;max-width:240px;height:6px;background:var(--line2);border-radius:3px;overflow:hidden}
.runrow .bar i{display:block;height:100%;background:var(--ok)}
.runrow.bad .bar i{background:var(--bad)}
.runrow .rate{font-family:var(--mono);font-size:12.5px;min-width:62px;text-align:right;color:var(--ink2)}
.runrow .cnt{font-family:var(--mono);font-size:11.5px;color:var(--ink3);min-width:120px;text-align:right}

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
  document.querySelectorAll('details.case, details.group').forEach(function(d){ d.open = open; });
}
function toggleGroups(open){
  document.querySelectorAll('details.group').forEach(function(d){ d.open = open; });
}
function openFailed(){
  document.querySelectorAll('details.group').forEach(function(g){
    g.open = g.classList.contains('hasfail');
  });
  document.querySelectorAll('details.case').forEach(function(d){
    d.open = d.classList.contains('failed') || d.classList.contains('error');
  });
}
document.addEventListener('click', function(e){
  var shot = e.target.closest('.shot') || (e.target.tagName === 'IMG' && e.target.closest('.stepshot') ? e.target : null);
  if (shot){
    var img = shot.tagName === 'IMG' ? shot : shot.querySelector('img');
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


def _render_steps(case: CaseRecord, budget: "_ImageBudget | None" = None) -> str:
    if not case.steps:
        return ""
    rows = []
    for step in case.steps:
        failed = " failed" if step.status == FAILED else ""
        indent = f' style="padding-left:{step.level * 18}px"' if step.level else ""
        rows.append(
            f'<div class="step{failed}"{indent}><div class="mk"></div>'
            f'<div class="nm">{_esc(step.display_name)}</div>'
            f'<div class="du">{human_duration(step.duration)}</div></div>'
        )
        if step.error:
            rows.append(f'<div class="err">{_esc(step.error)}</div>')
        # 步骤级截图：失败步骤自己那一屏，比用例末尾的截图更贴近现场
        if step.screenshot:
            uri = budget.take(step.screenshot) if budget is not None else image_to_data_uri(step.screenshot)
            if uri:
                rows.append(
                    f'<div class="stepshot"><img src="{uri}" alt="{_esc(step.screenshot.name)}" '
                    f'loading="lazy"></div>'
                )
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
    # 步骤里已经展示过的截图不再重复渲染（collector 会把它同时登记到 case.screenshots，
    # 不去重的话同一张图会出现两次，还白吃一份体积预算）
    shown_in_steps = {s.screenshot for s in case.steps if s.screenshot}
    shots = [p for p in case.screenshots if p not in shown_in_steps]
    if not shots:
        return ""
    imgs = []
    for path in shots:
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
    open_attr = " open" if case.is_failure else ""
    # 跳过的用例不显示耗时：它的 duration 其实是「等夹具建会话」的时间（设备离线时可达几十秒），
    # 标出来会让人误以为这条用例跑了很久
    dur_text = "—" if case.status == SKIPPED else human_duration(case.duration)
    markers = ""
    if case.markers:
        chips = "".join(f"<span>@{_esc(m)}</span>" for m in case.markers)
        markers = f'<div class="sub">用例标记</div><div class="ctx">{chips}</div>'
    description = f'<div class="desc">{_esc(case.description)}</div>' if case.description else ""
    return (
        f'<details class="case {_esc(case.status)}"{open_attr}>'
        f"<summary>"
        f'<span class="badge {_esc(case.status)}">{_status_label(case.status)}</span>'
        f'<span class="title">{_esc(case.name)}</span>'
        f'<span class="dur">{dur_text}</span>'
        f"</summary>"
        f'<div class="body">'
        f"{description}"
        f"{_render_context(case)}"
        f"{_render_error(case)}"
        f"{_render_steps(case, budget)}"
        f"{_render_shots(case, budget)}"
        f"{markers}"
        f'<div class="sub" style="margin-top:16px">pytest 节点</div>'
        f'<div class="ctx"><span><b>{_esc(case.nodeid)}</b></span></div>'
        f"</div></details>"
    )


def _render_groups(run: RunRecord, budget: _ImageBudget | None = None) -> str:
    """按测试文件分组渲染。失败的分组默认展开，全绿的分组默认折叠。"""
    groups = run.groups()
    if not groups:
        return '<div class="empty">本次运行没有收集到用例</div>'
    blocks = []
    for group in groups:
        cls = "group hasfail" if group.has_failure else "group"
        open_attr = " open" if group.has_failure else ""
        body = "".join(_render_case(c, budget) for c in group.sorted_cases())
        blocks.append(
            f'<details class="{cls}"{open_attr}>'
            f"<summary>"
            f'<span class="gname">{_esc(group.name)}</span>'
            f'<span class="gbar"><i style="width:{min(group.pass_rate, 100):.0f}%"></i></span>'
            f'<span class="gstat">{group.passed}/{group.total} 通过 · {group.failed} 失败 · '
            f"{group.skipped} 跳过 · {human_duration(group.duration)}</span>"
            f"</summary>"
            f'<div class="gbody">{body}</div>'
            f"</details>"
        )
    return "".join(blocks)


def _render_failure_panel(run: RunRecord) -> str:
    """失败原因聚合：同一异常类型合并，一眼看出是「选择器失效」还是「网络挂了」。"""
    stats = run.error_stats()
    if not stats:
        return ""
    cards = []
    for stat in stats:
        names = " · ".join(_esc(n) for n in stat.cases[:6])
        more = f" 等 {stat.count} 条" if stat.count > 6 else ""
        cards.append(
            f'<div class="fail"><div class="ftype">{_esc(stat.error_type)}'
            f'<span class="fcount">{stat.count} 条</span></div>'
            f'<div class="fcases">{names}{more}</div></div>'
        )
    return f'<section><h2>失败概览</h2><div class="fails">{"".join(cards)}</div></section>'


def _render_slowest(run: RunRecord, limit: int = 5) -> str:
    slowest = run.slowest(limit)
    if not slowest:
        return ""
    rows = "".join(
        f"<tr><td>{_esc(c.name)}</td>"
        f'<td class="num">{human_duration(c.duration)}</td>'
        f'<td class="st {_esc(c.status)}">{_status_label(c.status)}</td></tr>'
        for c in slowest
    )
    return (
        "<section><h2>最慢用例</h2><div class=\"tblwrap\"><table class=\"tbl\">"
        '<thead><tr><th>用例</th><th class="num">耗时</th><th>结果</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _render_options(run: RunRecord) -> str:
    """运行参数与 marker 统计：复现问题时要什么都在这一块。"""
    rows: list[tuple[str, str]] = []
    if run.pytest_version:
        rows.append(("pytest", run.pytest_version))
    if run.exit_status_text:
        rows.append(("退出码", f"{run.exit_status} · {run.exit_status_text}"))
    rows.extend(run.options.items())

    parts = []
    if rows:
        body = "".join(
            f'<div><span class="k">{_esc(k)}</span><span class="v">{_esc(v)}</span></div>'
            for k, v in rows
        )
        parts.append(f'<div class="env">{body}</div>')

    markers = run.marker_stats()
    if markers:
        mrows = "".join(
            f"<tr><td>@{_esc(name)}</td>"
            f'<td class="num">{total}</td>'
            f'<td class="num">{passed}</td></tr>'
            for name, total, passed in markers
        )
        parts.append(
            '<div class="tblwrap" style="margin-top:12px"><table class="tbl">'
            '<thead><tr><th>用例标记</th><th class="num">用例数</th>'
            f'<th class="num">通过</th></tr></thead><tbody>{mrows}</tbody></table></div>'
        )
    if not parts:
        return ""
    return f'<section><h2>运行参数</h2>{"".join(parts)}</section>'


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
      <span>RESULT · {_esc(run.exit_status_text or "未执行")}</span>
      {command}
    </div>
    <div class="verdict {verdict_cls}">{_esc(verdict_text)}</div>
  </div>
</header>

<div class="wrap">
  {_render_stats(run)}
  {_render_failure_panel(run)}
  {_render_env(run)}
  {_render_options(run)}

  <section>
    <h2>用例明细（按文件分组）</h2>
    <div class="toolbar">
      <button onclick="openFailed()">只看失败</button>
      <button onclick="toggleAll(true)">全部展开</button>
      <button onclick="toggleAll(false)">全部折叠</button>
      <button onclick="toggleGroups(false)">折叠分组</button>
    </div>
    {_render_groups(run, budget)}
  </section>

  {_render_slowest(run)}

  <footer>
    <span>Generated by core.report · 单文件自包含报告</span>
    <span>{_esc(run.exit_status_text or "未执行")} · {_esc(run.env.device_model or "no device")}</span>
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
    """导出 JSON，方便 CI 或二次分析消费（结构与 HTML 报告一一对应）。"""
    payload = {
        "schema": 2,
        "title": run.title,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration": round(run.duration, 3),
        "command": run.command,
        "exit_status": run.exit_status,
        "exit_status_text": run.exit_status_text,
        "pytest_version": run.pytest_version,
        "summary": {
            "total": run.total,
            "passed": run.passed,
            "failed": run.failed,
            "errors": run.errors,
            "skipped": run.skipped,
            "executed": run.executed,
            "pass_rate": round(run.pass_rate, 2),
            "avg_case_duration": round(run.avg_case_duration, 3),
        },
        "env": dict(run.env.as_rows()),
        "options": run.options,
        "groups": [
            {
                "name": g.name,
                "total": g.total,
                "passed": g.passed,
                "failed": g.failed,
                "skipped": g.skipped,
                "duration": round(g.duration, 3),
                "pass_rate": round(g.pass_rate, 2),
            }
            for g in run.groups()
        ],
        "failures": [
            {"error_type": e.error_type, "count": e.count, "cases": e.cases}
            for e in run.error_stats()
        ],
        "cases": [
            {
                "nodeid": c.nodeid,
                "name": c.name,
                "file": c.file,
                "group": c.group,
                "markers": c.markers,
                "description": c.description,
                "status": c.status,
                "started_at": c.started_at,
                "duration": round(c.duration, 3),
                "context": c.context,
                "error_type": c.error_type,
                "error_message": c.error_message,
                "failed_step": c.first_failed_step.display_name if c.first_failed_step else "",
                "steps": [
                    {
                        "name": s.display_name,
                        "level": s.level,
                        "status": s.status,
                        "duration": round(s.duration, 3),
                        "error": s.error,
                        "screenshot": str(s.screenshot) if s.screenshot else "",
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


# ---------------------------------------------------------------------- #
# 运行历史索引页
# ---------------------------------------------------------------------- #
def write_index(out_dir: Path, title: str = "运行历史") -> Path:
    """扫描目录内所有 ``report_*.json``，生成可点击跳转的 ``index.html``。

    为什么需要它：报告按时间戳命名，跑几十次后 artifacts/reports 里全是文件，
    根本分不清哪份是刚才那次、通过率是在变好还是变差。
    """
    entries: list[dict] = []
    for json_path in out_dir.glob("report_*.json"):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries.append(_index_entry(payload, json_path))
    entries.sort(key=lambda e: e["started_at"], reverse=True)

    path = out_dir / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_index(entries, title), encoding="utf-8")
    return path


def _index_entry(payload: dict, json_path: Path) -> dict:
    summary = payload.get("summary") or {}
    env = payload.get("env") or {}
    return {
        "html": json_path.with_suffix(".html").name,
        "started_at": float(payload.get("started_at") or 0.0),
        "title": payload.get("title") or "",
        "total": int(summary.get("total") or 0),
        "passed": int(summary.get("passed") or 0),
        "failed": int(summary.get("failed") or 0),
        "skipped": int(summary.get("skipped") or 0),
        "pass_rate": float(summary.get("pass_rate") or 0.0),
        "duration": float(payload.get("duration") or 0.0),
        "command": payload.get("command") or "",
        "device": env.get("设备型号") or env.get("设备序列号") or "",
    }


def render_index(entries: list[dict], title: str = "运行历史") -> str:
    """渲染历史索引页（同一套工业蓝图样式）。"""
    latest = entries[0] if entries else {}
    avg_rate = sum(e["pass_rate"] for e in entries) / len(entries) if entries else 0.0

    cards = [
        ("blue", "运行次数", str(len(entries))),
        ("blue", "最近通过率", f"{float(latest.get('pass_rate') or 0):.1f}%"),
        ("ok", "历史平均", f"{avg_rate:.1f}%"),
        ("bad", "最近失败", str(latest.get("failed", 0))),
    ]
    stats = "".join(
        f'<div class="stat {cls}"><div class="k">{_esc(k)}</div><div class="v">{_esc(v)}</div></div>'
        for cls, k, v in cards
    )

    rows = "".join(_render_index_row(e) for e in entries)
    if not rows:
        rows = '<div class="empty">还没有历史运行记录。跑一次 pytest 就会出现在这里。</div>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)} · AndroidAutomation</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <div class="wrap">
    <div class="brand"><span class="dot"></span>AndroidAutomation · Run History</div>
    <h1>{_esc(title)}</h1>
    <div class="meta"><span>RECORDS · {len(entries)}</span><span>最新在前</span></div>
  </div>
</header>
<div class="wrap">
  <div class="stats" style="grid-template-columns:repeat(4,1fr)">{stats}</div>
  <section>
    <h2>历史运行</h2>
    {rows}
  </section>
  <footer>
    <span>Generated by core.report · 每次 pytest 运行都会刷新</span>
    <span>{time.strftime("%Y-%m-%d %H:%M:%S")}</span>
  </footer>
</div>
</body>
</html>
"""


def _render_index_row(entry: dict) -> str:
    rate = min(max(entry.get("pass_rate", 0.0), 0.0), 100.0)
    cls = "runrow bad" if entry.get("failed") else "runrow"
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.get("started_at") or time.time()))
    failed = entry.get("failed", 0)
    skipped = entry.get("skipped", 0)
    # 分母用「执行过的用例」—— 与通过率口径保持一致，否则「13/23 通过 + 100%」看起来自相矛盾
    executed = entry.get("total", 0) - skipped
    summary = f"{entry.get('passed', 0)}/{executed} 通过"
    if failed:
        summary += f" · {failed} 失败"
    if skipped:
        summary += f" · {skipped} 跳过"
    return (
        f'<a class="{cls}" href="{_esc(entry.get("html", ""))}">'
        f'<span class="when">{when}</span>'
        f'<span class="bar"><i style="width:{rate:.0f}%"></i></span>'
        f'<span class="rate">{rate:.1f}%</span>'
        f'<span class="cnt">{_esc(summary)}</span>'
        f"</a>"
    )
