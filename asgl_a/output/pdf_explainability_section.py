
"""
Injects explainability summary into ASGL-A PDF reports.
"""

def render_explainability_section(explanations):
    lines = ["Explainability Summary", "----------------------"]
    for item in explanations:
        lines.append(f"- {item['feature']}: {item['contribution'] * 100:.0f}% impact")
    return "\n".join(lines)
