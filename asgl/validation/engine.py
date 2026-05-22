import math, ast
from typing import Any, Dict, List, Tuple
from asgl.utils.logging import setup_logging

log = setup_logging("asgl.validation")

class RuleViolation(dict): ...

def _is_missing(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip()=="")

def _safe_eval(expr: str, row: Dict[str, Any]) -> float:
    # very small safe expression evaluator
    node = ast.parse(expr, mode="eval")
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Num, ast.Name, ast.Load,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd, ast.Constant, ast.Mod)
    for n in ast.walk(node):
        if not isinstance(n, allowed):
            raise ValueError("Disallowed expression")
        if isinstance(n, ast.Name) and n.id not in row:
            row[n.id]=0
    return float(eval(compile(node, "<expr>", "eval"), {"__builtins__": {}}, row))

def validate_rows(schema_name: str, rows: List[Dict[str, Any]], rules: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    schema_rules = rules.get(schema_name, [])
    violations: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows):
        for rule in schema_rules:
            rtype = rule.get("type")
            level = rule.get("level","warn")
            rid = rule.get("id","rule")
            try:
                if rtype=="required_fields":
                    missing=[f for f in rule["fields"] if _is_missing(row.get(f))]
                    if missing:
                        violations.append({"row":idx,"rule":rid,"level":level,"detail":{"missing":missing}})
                elif rtype=="enum":
                    v=row.get(rule["field"])
                    if not _is_missing(v) and v not in rule["allowed"]:
                        violations.append({"row":idx,"rule":rid,"level":level,"detail":{"value":v,"allowed":rule["allowed"]}})
                elif rtype=="min_value":
                    for f in rule["fields"]:
                        v=row.get(f)
                        if v is None: 
                            continue
                        try:
                            if float(v) < float(rule["min"]):
                                violations.append({"row":idx,"rule":rid,"level":level,"detail":{"field":f,"value":v,"min":rule["min"]}})
                        except: 
                            pass
                elif rtype=="approx_equal":
                    a=row.get(rule["a"]); b=row.get(rule["b"])
                    if a is None or b is None: 
                        continue
                    if abs(float(a)-float(b))>float(rule.get("tolerance",0.0)):
                        violations.append({"row":idx,"rule":rid,"level":level,"detail":{"a":a,"b":b,"tol":rule.get("tolerance")}})
                elif rtype=="approx_relation":
                    lhs=row.get(rule["lhs"])
                    if lhs is None: 
                        continue
                    rhs=_safe_eval(rule["rhs"], dict(row))
                    if abs(float(lhs)-rhs)>float(rule.get("tolerance",0.0)):
                        violations.append({"row":idx,"rule":rid,"level":level,"detail":{"lhs":lhs,"rhs":rhs,"tol":rule.get("tolerance")}})
                elif rtype=="ohlc_consistency":
                    o=row.get(rule["open"]); h=row.get(rule["high"]); l=row.get(rule["low"]); c=row.get(rule["close"])
                    if None in (o,h,l,c): 
                        continue
                    o=float(o); h=float(h); l=float(l); c=float(c)
                    if h < max(o,c) or l > min(o,c) or h < l:
                        violations.append({"row":idx,"rule":rid,"level":level,"detail":{"open":o,"high":h,"low":l,"close":c}})
                elif rtype=="tb_balance":
                    net_field=rule.get("net_field","net")
                    total=0.0
                    for r in rows:
                        v=r.get(net_field)
                        if v is None: 
                            continue
                        try: total += float(v)
                        except: pass
                    if abs(total) > float(rule.get("tolerance",0.0)):
                        violations.append({"row":None,"rule":rid,"level":level,"detail":{"total_net":total,"tol":rule.get("tolerance")}})
                    break
                elif rtype=="xor_fields":
                    present=[f for f in rule["fields"] if not _is_missing(row.get(f))]
                    if len(present)!=1:
                        violations.append({"row":idx,"rule":rid,"level":level,"detail":{"present":present,"expected":1}})
            except Exception as e:
                violations.append({"row":idx,"rule":rid,"level":"warn","detail":{"error":str(e)}})
    errors=sum(1 for v in violations if v["level"]=="error")
    warns=sum(1 for v in violations if v["level"]!="error")
    summary={"schema":schema_name,"rows":len(rows),"errors":errors,"warnings":warns,"violations":violations[:500]}
    log.info("validation_complete", extra={"extra":{"schema":schema_name,"errors":errors,"warnings":warns}})
    return summary
