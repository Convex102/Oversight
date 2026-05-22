from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Dict, Any, List, Optional, Set
import time, traceback

@dataclass
class NodeResult:
    name: str
    ok: bool
    out: Dict[str,Any]=field(default_factory=dict)
    metrics: Dict[str,Any]=field(default_factory=dict)
    error: Optional[str]=None
    retries: int=0

@dataclass
class Node:
    name: str
    fn: Callable[[Dict[str,Any]], NodeResult]
    deps: List[str]=field(default_factory=list)
    max_retries: int=0

class DAGPipeline:
    def __init__(self, nodes: List[Node]):
        self.nodes = {n.name: n for n in nodes}
        self.order = self._toposort()

    def _toposort(self)->List[str]:
        visited=set(); temp=set(); out=[]
        def dfs(n):
            if n in visited: return
            if n in temp: raise RuntimeError("DAG cycle detected")
            temp.add(n)
            for d in self.nodes[n].deps:
                if d not in self.nodes: raise RuntimeError(f"Missing dep {d} for {n}")
                dfs(d)
            temp.remove(n); visited.add(n); out.append(n)
        for n in self.nodes:
            dfs(n)
        return out

    def run(self, ctx: Dict[str,Any], progress_cb=None)->Dict[str,Any]:
        ctx=dict(ctx)
        results=[]
        total=len(self.order)
        for i,name in enumerate(self.order):
            node=self.nodes[name]
            if progress_cb: progress_cb(stage=name, progress=i/total, message="running")
            t0=time.time()
            attempt=0
            while True:
                attempt+=1
                try:
                    res=node.fn(ctx)
                except Exception as e:
                    res=NodeResult(name=name, ok=False, error=str(e), metrics={"trace": traceback.format_exc()})
                res.metrics["duration_ms"]=int((time.time()-t0)*1000)
                res.retries=attempt-1
                results.append(res.__dict__)
                if not res.ok and attempt<=node.max_retries:
                    continue
                if not res.ok:
                    ctx["pipeline_error"]=res.error or "node_failed"
                    if progress_cb: progress_cb(stage=name, progress=i/total, message="failed", level="error")
                    break
                ctx.update(res.out or {})
                if progress_cb: progress_cb(stage=name, progress=(i+1)/total, message="ok")
                break
            if ctx.get("pipeline_error"): break
        return {"ok": ctx.get("pipeline_error") is None, "ctx": ctx, "nodes": results}