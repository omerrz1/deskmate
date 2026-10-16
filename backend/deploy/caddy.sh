#!/usr/bin/env bash
# Writes /etc/caddy/Caddyfile for the API and reloads Caddy. Run by setup.sh, and weekly by cron to keep CloudFront's addresses current.
set -euo pipefail
API_HOST="${API_HOST:?set API_HOST, e.g. api.example.com}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# CloudFront's own addresses: requests that arrive through Amplify come from these, and only these may tell us who the visitor is
RANGES="$(curl -fsS --max-time 20 https://ip-ranges.amazonaws.com/ip-ranges.json | python3 -c '
import json, sys
d = json.load(sys.stdin)
out = {p["ip_prefix"] for p in d["prefixes"] if p["service"] in ("CLOUDFRONT", "CLOUDFRONT_ORIGIN_FACING")}
out |= {p["ipv6_prefix"] for p in d["ipv6_prefixes"] if p["service"] in ("CLOUDFRONT", "CLOUDFRONT_ORIGIN_FACING")}
print(" ".join(sorted(out)))' 2>/dev/null || true)"
[ -n "$RANGES" ] || { RANGES="private_ranges"; echo "Could not read CloudFront's addresses; trusting only private networks for now."; }

sed "s|__API_HOST__|$API_HOST|; s|__TRUSTED__|$RANGES|" "$HERE/Caddyfile.template" > /etc/caddy/Caddyfile.new
caddy validate --config /etc/caddy/Caddyfile.new --adapter caddyfile >/dev/null
mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile
systemctl enable caddy >/dev/null 2>&1 || true
systemctl reload caddy 2>/dev/null || systemctl restart caddy
echo "Caddy is serving https://$API_HOST ($(echo "$RANGES" | wc -w | tr -d ' ') trusted proxy ranges)"
