#!/bin/bash
# MCG Dashboard — start Flask + localtunnel
# Usage: ./start.sh
#
# Starts both the Flask API server and the localtunnel so the
# Webflow admin hub at masoncapitalgroup.com/admin/admin-hub can reach it.

set -e
cd "$(dirname "$0")"

echo "→ Stopping any existing processes on port 5050..."
lsof -ti :5050 | xargs kill -9 2>/dev/null || true
pkill -f "localtunnel\|lt --port" 2>/dev/null || true
sleep 1

echo "→ Starting Flask on port 5050..."
python3 app.py > /tmp/flask_mcg.log 2>&1 &
FLASK_PID=$!
sleep 3

# Verify Flask started
HTTP_LOCAL=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5050/login)
if [ "$HTTP_LOCAL" != "200" ]; then
  echo "✗ Flask failed to start. Tail the log:"
  echo "  tail -50 /tmp/flask_mcg.log"
  exit 1
fi
echo "  ✓ Flask running (PID $FLASK_PID)"

echo "→ Starting localtunnel at mcg-dashboard.loca.lt..."
npx localtunnel --port 5050 --subdomain mcg-dashboard > /tmp/tunnel_mcg.log 2>&1 &
TUNNEL_PID=$!
sleep 6

# Verify tunnel
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -H "bypass-tunnel-reminder: 1" https://mcg-dashboard.loca.lt/login 2>/dev/null)
if [ "$HTTP" = "200" ]; then
  echo "  ✓ Tunnel live at https://mcg-dashboard.loca.lt"
else
  echo "  ⚠ Tunnel returned HTTP $HTTP — may still be starting."
  echo "    Check: tail -5 /tmp/tunnel_mcg.log"
fi

echo ""
echo "─────────────────────────────────────────"
echo "  MCG Dashboard ready"
echo "  Local:   http://localhost:5050"
echo "  Public:  https://mcg-dashboard.loca.lt"
echo "  Webflow: https://www.masoncapitalgroup.com/admin/admin-hub"
echo ""
echo "  Login: admin / mcg2026"
echo "─────────────────────────────────────────"
echo ""
echo "  Logs:"
echo "    Flask:  tail -f /tmp/flask_mcg.log"
echo "    Tunnel: tail -f /tmp/tunnel_mcg.log"
