"""JSON and self-contained HTML export for codebase graphs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Hashable

import networkx as nx


_PALETTE = [
    "#4e79a7",
    "#f28e2b",
    "#59a14f",
    "#e15759",
    "#76b7b2",
    "#edc948",
    "#b07aa1",
    "#ff9da7",
    "#9c755f",
    "#bab0ac",
]


def _membership(communities: dict[int, list[Hashable]]) -> dict[Hashable, int]:
    return {
        node_id: community_id
        for community_id, members in communities.items()
        for node_id in members
    }


def _payload(graph: nx.Graph, communities: dict[int, list[Hashable]]) -> dict[str, Any]:
    membership = _membership(communities)
    nodes = []
    for node_id, attrs in graph.nodes(data=True):
        item = {"id": node_id, **dict(attrs)}
        item["label"] = item.get("label", str(node_id))
        item["community"] = membership.get(node_id, -1)
        nodes.append(item)

    links = []
    if graph.is_multigraph():
        edge_iter = graph.edges(keys=True, data=True)
        for source, target, key, attrs in edge_iter:
            links.append({"source": source, "target": target, "key": key, **dict(attrs)})
    else:
        for source, target, attrs in graph.edges(data=True):
            links.append({"source": source, "target": target, **dict(attrs)})

    return {
        "directed": graph.is_directed(),
        "multigraph": graph.is_multigraph(),
        "graph": dict(graph.graph),
        "nodes": nodes,
        "links": links,
        "hyperedges": [],
    }


def to_json(
    graph: nx.Graph,
    communities: dict[int, list[Hashable]],
    output_path: str | Path,
    *,
    force: bool = False,
) -> str:
    path = Path(output_path)
    if path.exists() and not force:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_payload(graph, communities), indent=2, default=str),
        encoding="utf-8",
    )
    return str(path)


def to_html(
    graph: nx.Graph,
    communities: dict[int, list[Hashable]],
    output_path: str | Path,
    *,
    community_labels: dict[int, str] | None = None,
) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload(graph, communities)
    data_json = json.dumps(payload, default=str).replace("</", "<\\/")
    labels_json = json.dumps(community_labels or {}).replace("</", "<\\/")
    palette_json = json.dumps(_PALETTE)
    template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ChipVerify Codebase Graph</title>
<style>
*{box-sizing:border-box}html,body{height:100%;margin:0;background:#101319;color:#e7eaf0;font-family:Arial,sans-serif}
main{display:grid;grid-template-columns:minmax(0,1fr) 280px;height:100%}canvas{width:100%;height:100%;display:block}
aside{border-left:1px solid #303641;background:#181c23;padding:14px;overflow:auto}h1{font-size:16px;margin:0 0 12px}
input{width:100%;padding:8px;border:1px solid #424a57;background:#0f1217;color:#fff}#info{font-size:13px;line-height:1.5;margin-top:14px;overflow-wrap:anywhere}
.muted{color:#9aa3b2}.legend{display:flex;align-items:center;gap:8px;margin:6px 0;font-size:12px}.dot{width:10px;height:10px;border-radius:50%}
@media(max-width:700px){main{grid-template-columns:1fr;grid-template-rows:minmax(0,1fr) 190px}aside{border-left:0;border-top:1px solid #303641}}
</style>
</head>
<body><main><canvas id="graph"></canvas><aside><h1>Codebase Graph</h1><input id="search" placeholder="Find a node"><div id="info" class="muted">Select a node to inspect it.</div><div id="legend"></div></aside></main>
<script>
const DATA=__DATA__,LABELS=__LABELS__,COLORS=__COLORS__;
const canvas=document.getElementById('graph'),ctx=canvas.getContext('2d'),info=document.getElementById('info');
let selected=null,positions=new Map(),scale=1,ox=0,oy=0;
function resize(){const r=canvas.getBoundingClientRect();canvas.width=Math.max(1,r.width*devicePixelRatio);canvas.height=Math.max(1,r.height*devicePixelRatio);ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);layout(r.width,r.height);draw()}
function layout(w,h){positions.clear();const n=Math.max(DATA.nodes.length,1),radius=Math.max(80,Math.min(w,h)*0.38);DATA.nodes.forEach((node,i)=>{const a=(Math.PI*2*i/n)-Math.PI/2;positions.set(String(node.id),{x:w/2+Math.cos(a)*radius,y:h/2+Math.sin(a)*radius})})}
function color(node){const c=Number(node.community);return COLORS[((c%COLORS.length)+COLORS.length)%COLORS.length]}
function draw(){const r=canvas.getBoundingClientRect();ctx.clearRect(0,0,r.width,r.height);ctx.save();ctx.translate(ox,oy);ctx.scale(scale,scale);ctx.strokeStyle='#38404c';ctx.globalAlpha=.55;DATA.links.forEach(e=>{const a=positions.get(String(e.source)),b=positions.get(String(e.target));if(a&&b){ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke()}});ctx.globalAlpha=1;DATA.nodes.forEach(n=>{const p=positions.get(String(n.id));if(!p)return;ctx.beginPath();ctx.arc(p.x,p.y,selected===n?8:5,0,Math.PI*2);ctx.fillStyle=color(n);ctx.fill();if(selected===n){ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();ctx.fillStyle='#fff';ctx.font='12px Arial';ctx.fillText(String(n.label||n.id),p.x+11,p.y+4)}});ctx.restore()}
function choose(node){selected=node;if(!node){info.className='muted';info.textContent='No matching node.';draw();return}info.className='';const degree=DATA.links.filter(e=>String(e.source)===String(node.id)||String(e.target)===String(node.id)).length;info.innerHTML='<b>'+esc(node.label||node.id)+'</b><br><span class="muted">'+esc(node.file_type||'node')+' | '+degree+' links</span><br>'+esc(node.source_file||'');draw()}
function esc(v){const d=document.createElement('div');d.textContent=String(v);return d.innerHTML}
canvas.addEventListener('click',e=>{const r=canvas.getBoundingClientRect(),x=(e.clientX-r.left-ox)/scale,y=(e.clientY-r.top-oy)/scale;let best=null,dist=14;DATA.nodes.forEach(n=>{const p=positions.get(String(n.id));if(!p)return;const d=Math.hypot(p.x-x,p.y-y);if(d<dist){best=n;dist=d}});choose(best)});
canvas.addEventListener('wheel',e=>{e.preventDefault();scale=Math.max(.3,Math.min(4,scale*(e.deltaY<0?1.12:.89)));draw()},{passive:false});
document.getElementById('search').addEventListener('input',e=>{const q=e.target.value.trim().toLowerCase();if(!q){choose(null);return}choose(DATA.nodes.find(n=>String(n.label||n.id).toLowerCase().includes(q))||null)});
const counts={};DATA.nodes.forEach(n=>counts[n.community]=(counts[n.community]||0)+1);document.getElementById('legend').innerHTML='<p class="muted">'+DATA.nodes.length+' nodes | '+DATA.links.length+' edges</p>'+Object.keys(counts).sort((a,b)=>counts[b]-counts[a]).slice(0,20).map(id=>'<div class="legend"><span class="dot" style="background:'+COLORS[((Number(id)%COLORS.length)+COLORS.length)%COLORS.length]+'"></span>'+esc(LABELS[id]||('Community '+id))+' ('+counts[id]+')</div>').join('');
addEventListener('resize',resize);resize();
</script></body></html>"""
    html = (
        template.replace("__DATA__", data_json)
        .replace("__LABELS__", labels_json)
        .replace("__COLORS__", palette_json)
    )
    path.write_text(html, encoding="utf-8")
    return str(path)
