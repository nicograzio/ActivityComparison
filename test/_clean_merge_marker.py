p = "core/strava_analyzer.py"
lines = [l for l in open(p, encoding="utf-8").read().splitlines() if l.strip() != ">>>>>>>"]
open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("cleaned, now %d lines" % len(lines))
