"""Feux de la Vigie : VERT normal, ORANGE (montants ÷2, rien d'irréversible), ROUGE (pause)."""

VERT, ORANGE, ROUGE = "VERT", "ORANGE", "ROUGE"
SEVERITY = {VERT: 0, ORANGE: 1, ROUGE: 2}
# Seuls ces émetteurs peuvent changer le feu ou relancer après un ROUGE
LIGHT_AUTHORITIES = {"vigie", "human", "inject"}


def worst(*lights: str) -> str:
    return max(lights, key=lambda light: SEVERITY[light])
